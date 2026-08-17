from __future__ import annotations

import ast
import builtins
import threading
from typing import Any


class SafeExecutionError(Exception):
    pass


class UnsafeCodeError(SafeExecutionError):
    pass


DEFAULT_ALLOWED_NAMES = {"pd", "np", "df", "result"} | {f"df_{i}" for i in range(1, 21)}

SAFE_BUILTINS = {
    "abs",
    "all",
    "any",
    "bin",
    "bool",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "hex",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
}

DANGEROUS_NAMES = {
    "exec",
    "eval",
    "compile",
    "open",
    "input",
    "getattr",
    "setattr",
    "delattr",
    "vars",
    "globals",
    "locals",
    "__import__",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "type",
    "object",
    "dir",
    "hasattr",
    "memoryview",
}

# pandas / numpy 的文件读写方法：注入的 pd/np/df 也能通过这些方法读写任意本地
# 文件，构成数据外带通道（如 pd.read_csv('C:/secret') 读取、df.to_csv 写盘）。
# 分析任务只应操作内存中的 DataFrame，故一律拦截。
FILE_IO_METHODS = {
    # pandas 读取器
    "read_csv",
    "read_table",
    "read_fwf",
    "read_excel",
    "read_json",
    "read_html",
    "read_xml",
    "read_parquet",
    "read_feather",
    "read_pickle",
    "read_sas",
    "read_spss",
    "read_stata",
    "read_orc",
    "read_clipboard",
    "read_sql",
    "read_sql_table",
    "read_sql_query",
    "read_gbq",
    # pandas 写入器
    "to_csv",
    "to_excel",
    "to_json",
    "to_pickle",
    "to_parquet",
    "to_sql",
    "to_feather",
    "to_hdf",
    "to_stata",
    "to_orc",
    "to_xml",
    "to_gbq",
    "to_clipboard",
    # pandas 内部通道：绕过 read_*/to_* 拦截后仍能直接拿文件句柄或内容
    # （如 pd.io.common.open('C:/secret').read()、get_filepath_or_buffer(...).read()）
    "open",
    "get_filepath_or_buffer",
    "get_handle",
    "file_exists",
    "json_reader",
    "clipboard_get",
    "clipboard_set",
    # numpy 文件 I/O
    "load",
    "save",
    "savez",
    "savez_compressed",
    "loadtxt",
    "savetxt",
    "genfromtxt",
    "fromfile",
    "tofile",
    "memmap",
    # numpy 内部通道：np.lib.format / np.lib.npyio 直读直写
    "read_array",
    "write_array",
    "open_memmap",
    "recfromcsv",
    "recfromtxt",
    "dump",
}


class SafeExecutor:
    """AST-whitelist sandbox with thread-based timeout.

    Only pre-injected names (pd / np / df_*) and a curated builtin subset
    are callable. No imports, no dunder access, no `os`/`sys` style escapes.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def _validate(self, code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise UnsafeCodeError(f"代码语法错误: {exc}") from exc

        allowed = set(DEFAULT_ALLOWED_NAMES)
        for node in ast.walk(tree):
            if isinstance(node, ast.Lambda):
                for arg in node.args.args:
                    allowed.add(arg.arg)
            elif isinstance(
                node,
                (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
            ):
                for gen in node.generators:
                    for sub in ast.walk(gen.target):
                        if isinstance(sub, ast.Name):
                            allowed.add(sub.id)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise UnsafeCodeError("禁止使用 import")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise UnsafeCodeError(f"禁止访问私有属性: {node.attr}")
            if isinstance(node, ast.Attribute) and node.attr in FILE_IO_METHODS:
                raise UnsafeCodeError(f"禁止文件读写方法: {node.attr}")
            if isinstance(node, ast.Name):
                # 赋值目标（Store/Del）允许任意本地变量名，例如 LLM 生成的中间变量
                if not isinstance(node.ctx, ast.Load):
                    continue
                if node.id in DANGEROUS_NAMES:
                    raise UnsafeCodeError(f"禁止使用危险名称: {node.id}")
                if node.id not in allowed:
                    raise UnsafeCodeError(
                        f"名称 '{node.id}' 不在白名单中，只能使用 pd / np / df / df_1... 及内置函数"
                    )

    def execute(
        self,
        code: str,
        namespace: dict,
        timeout: float | None = None,
    ) -> Any:
        self._validate(code)
        limit = self.timeout if timeout is None else timeout

        safe_ns = {
            "__builtins__": {name: getattr(builtins, name) for name in SAFE_BUILTINS},
            **namespace,
        }
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                compiled = compile(code, "<safe_exec>", "exec")
                exec(compiled, safe_ns)
                box["ok"] = safe_ns.get("result")
            except BaseException as exc:
                box["err"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(limit)
        if thread.is_alive():
            raise SafeExecutionError(f"执行超时（>{limit}s）")
        if "err" in box:
            raise box["err"]
        return box.get("ok")
