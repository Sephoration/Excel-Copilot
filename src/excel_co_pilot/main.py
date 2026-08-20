"""应用入口：FastAPI REST API + Gradio 界面。

- `main()`：启动 Gradio 界面（默认 http://localhost:7861）。
- `create_app()`：返回 FastAPI 应用（健康检查 / 上传 / 对话 / 知识库），可独立用 uvicorn 启动。

注意：本模块不使用 `from __future__ import annotations`——Python 3.14 + FastAPI 下字符串注解会让
`UploadFile = File(...)` 变成未解析的 ForwardRef（PydanticUserError: class-not-fully-defined）。
"""

import os
from pathlib import Path

from fastapi import UploadFile
from loguru import logger

from .config import settings
from .ui.gradio_app import APP_CSS, APP_THEME, build_ui
from .utils.exceptions import FileValidationError
from .utils.logger import setup_logger


async def _stream_to_disk(file: UploadFile, dest: Path) -> None:
    """流式写盘，边写边校验大小上限；超限立即中止并清理，避免大文件占满磁盘。"""
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    written = 0
    with dest.open("wb") as fh:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                dest.unlink(missing_ok=True)
                raise FileValidationError(
                    f"文件超过上限 {settings.max_file_size_mb}MB，已中止上传"
                )
            fh.write(chunk)


def create_app():
    """构建 FastAPI 应用（REST 接口，供独立部署）。"""
    from fastapi import FastAPI, File
    from pydantic import BaseModel

    app = FastAPI(title="Excel Co-Pilot API", version="0.1.0")

    @app.on_event("startup")
    def _startup_warm_up() -> None:
        """启动时后台预热知识库索引，保证首次问答即就绪。"""
        import threading

        from .kb.services import get_services

        threading.Thread(target=get_services().warm_up, daemon=True).start()

    @app.get("/health")
    def health() -> dict:
        from .services.llm_gateway import LLMGateway

        return {"status": "ok", "llm_ready": LLMGateway.available()}

    @app.get("/kb/health")
    def kb_health() -> dict:
        from .kb.services import get_services

        svc = get_services()
        return {
            "status": "ok",
            "llm_ready": svc.llm_ready,
            "stats": svc.db.stats(),
        }

    @app.post("/kb/upload")
    async def kb_upload(file: UploadFile = File(...), library: str = "default") -> dict:
        from .kb.services import get_services

        svc = get_services()
        if not svc.db.library_exists(library):
            svc.db.create_library(library)
        svc.cfg.raw_dir.mkdir(parents=True, exist_ok=True)
        # 只取文件名部分，防止 ../../ 路径穿越写出目录
        safe_name = Path(file.filename or "upload.bin").name
        dest = svc.cfg.raw_dir / safe_name
        try:
            await _stream_to_disk(file, dest)
        except FileValidationError as exc:
            return {"error": str(exc)}
        result = svc.ingest.process_file(dest, source_name=safe_name, library=library)
        svc.refresh_indexes()  # 导入后立即刷新检索索引，保持可问答状态
        return result

    class KbAskRequest(BaseModel):
        question: str
        filters: dict | None = None

    @app.post("/kb/ask")
    def kb_ask(req: KbAskRequest) -> dict:
        from .kb.services import get_services

        svc = get_services()
        if not svc.llm_ready:
            return {"error": "未配置 DEEPSEEK_API_KEY"}
        result = svc.qa.answer(req.question, filters=req.filters)
        return {
            "answer": result.answer,
            "refused": result.refused,
            "refuse_reason": result.refuse_reason,
            "citations": result.citations,
            "rewritten": result.rewritten,
            "latency_ms": result.latency_ms,
        }

    return app


def main() -> None:
    """默认入口：启动 Gradio 界面（http://localhost:7861，可用 --port / GRADIO_PORT 覆盖）。"""
    import argparse

    parser = argparse.ArgumentParser(description="Excel Co-Pilot 界面")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("GRADIO_PORT", "7861"))
    )
    args = parser.parse_args()

    setup_logger()
    logger.info("启动 Excel Co-Pilot 界面")
    demo = build_ui()
    # 并发上限：导入索引是分钟级长任务，不能阻塞问答等其他事件
    demo.queue(default_concurrency_limit=8)
    # 禁用缓存：改版后浏览器必须拿到最新界面，避免旧布局残留
    from starlette.middleware.base import BaseHTTPMiddleware

    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response

    demo.app.add_middleware(NoCacheMiddleware)
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        show_error=True,
        css=APP_CSS,
        theme=APP_THEME,
    )


if __name__ == "__main__":
    main()
