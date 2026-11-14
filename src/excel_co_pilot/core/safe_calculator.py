"""有限的数学表达式求值，不执行 Python 代码。"""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

_MAX_EXPRESSION_LENGTH = 1024
_MAX_NODES = 100
_MAX_DEPTH = 32
_MAX_MAGNITUDE = 1e100
_MAX_EXPONENT = 100
_MAX_ARGUMENTS = 20

_BINARY_OPERATORS: dict[type[ast.operator], Callable] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_MATH_FUNCTIONS: dict[str, Callable] = {
    name: getattr(math, name)
    for name in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
                 "log", "log10", "log2", "exp", "floor", "ceil", "fabs", "degrees", "radians")
}
_MATH_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _number(value: object) -> int | float:
    if type(value) not in (int, float):
        raise ValueError("只允许数值")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("结果必须是有限数值")
    if abs(value) > _MAX_MAGNITUDE:
        raise ValueError("数值超出允许范围")
    return value


def _power(base: int | float, exponent: int | float) -> int | float:
    if abs(exponent) > _MAX_EXPONENT:
        raise ValueError("指数超出允许范围")
    return _number(base ** exponent)


def evaluate_expression(expression: str) -> int | float:
    """解析有限数量的算术节点和白名单函数，拒绝属性访问及任意调用。"""
    if not isinstance(expression, str) or len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError("表达式太长或类型不正确")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise ValueError("表达式过于复杂")

    def visit(node: ast.AST, depth: int = 0) -> int | float:
        if depth > _MAX_DEPTH:
            raise ValueError("表达式嵌套过深")
        def child(subnode: ast.AST) -> int | float:
            return visit(subnode, depth + 1)
        if isinstance(node, ast.Constant):
            return _number(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = child(node.operand)
            return _number(value if isinstance(node.op, ast.UAdd) else -value)
        if isinstance(node, ast.BinOp):
            left, right = child(node.left), child(node.right)
            if isinstance(node.op, ast.Pow):
                return _power(left, right)
            operation = _BINARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ValueError("不支持的运算符")
            return _number(operation(left, right))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "math" and node.attr in _MATH_CONSTANTS:
                return _MATH_CONSTANTS[node.attr]
            raise ValueError("不支持的属性")
        if isinstance(node, ast.Call) and not node.keywords:
            if len(node.args) > _MAX_ARGUMENTS:
                raise ValueError("参数过多")
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "math":
                if node.func.attr == "pow" and len(node.args) == 2:
                    return _power(child(node.args[0]), child(node.args[1]))
                function = _MATH_FUNCTIONS.get(node.func.attr)
                if function is None:
                    raise ValueError("不支持的函数")
                return _number(function(*(child(arg) for arg in node.args)))
            if name in {"sum", "min", "max"} and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
                items = node.args[0].elts
                if len(items) > _MAX_ARGUMENTS:
                    raise ValueError("参数过多")
                args = [child(item) for item in items]
            else:
                args = [child(arg) for arg in node.args]
            if name == "pow" and len(args) == 2:
                return _power(*args)
            if name == "abs" and len(args) == 1:
                return _number(abs(args[0]))
            if name == "round" and len(args) in (1, 2):
                if len(args) == 2 and (type(args[1]) is not int or abs(args[1]) > _MAX_EXPONENT):
                    raise ValueError("小数位数不合法")
                return _number(round(*args))
            if name == "sum":
                return _number(sum(args))
            if name == "min":
                return _number(min(args))
            if name == "max":
                return _number(max(args))
            raise ValueError("不支持的函数或参数")
        raise ValueError("不支持的表达式")

    return visit(tree.body)
