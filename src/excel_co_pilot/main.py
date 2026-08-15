"""应用入口：FastAPI REST API + Gradio 界面。

- `main()`：启动 Gradio 界面（默认 http://localhost:7860）。
- `create_app()`：返回 FastAPI 应用（健康检查 / 上传 / 对话 / 知识库），可独立用 uvicorn 启动。

注意：本模块不使用 `from __future__ import annotations`——Python 3.14 + FastAPI 下字符串注解会让
`UploadFile = File(...)` 变成未解析的 ForwardRef（PydanticUserError: class-not-fully-defined）。
"""

import os
import shutil

from loguru import logger

from .config import settings
from .core.session import SessionManager
from .ui.gradio_app import build_ui
from .utils.logger import setup_logger

_sessions = SessionManager()


def create_app():
    """构建 FastAPI 应用（REST 接口，供独立部署）。"""
    from fastapi import FastAPI, File, UploadFile
    from pydantic import BaseModel

    app = FastAPI(title="Excel Co-Pilot API", version="0.1.0")

    class ChatRequest(BaseModel):
        session_id: str
        message: str

    class ChatResponse(BaseModel):
        reply: str
        plot_html: str | None = None

    @app.get("/health")
    def health() -> dict:
        from .services.llm_gateway import LLMGateway

        return {"status": "ok", "llm_ready": LLMGateway.available()}

    @app.post("/upload")
    async def upload(session_id: str, file: UploadFile = File(...)) -> dict:
        session = _sessions.get(session_id)
        upload_dir = settings.storage_dir / "uploads" / session_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / (file.filename or "upload.bin")
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        try:
            alias = session.load(dest)
        except Exception as exc:
            return {"error": str(exc)}
        return {"alias": alias, "file": file.filename}

    @app.post("/chat")
    def chat(req: ChatRequest) -> ChatResponse:
        session = _sessions.get(req.session_id)
        result = session.chat(req.message)
        return ChatResponse(reply=result["reply"], plot_html=result.get("plot_html"))

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
    async def kb_upload(file: UploadFile = File(...)) -> dict:
        from .kb.services import get_services

        svc = get_services()
        svc.cfg.raw_dir.mkdir(parents=True, exist_ok=True)
        dest = svc.cfg.raw_dir / (file.filename or "upload.bin")
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        result = svc.ingest.process_file(dest, source_name=file.filename)
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
    """默认入口：启动 Gradio 界面（http://localhost:7861，可用 GRADIO_PORT 覆盖）。"""
    setup_logger()
    logger.info("启动 Excel Co-Pilot 界面")
    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("GRADIO_PORT", "7861")),
        show_error=True,
    )


if __name__ == "__main__":
    main()
