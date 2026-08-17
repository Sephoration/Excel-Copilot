"""启动前环境就绪检测：Python 版本、核心依赖、LLM 服务与存储目录。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以正确显示中文/emoji
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REQUIRED = [
    "pandas",
    "numpy",
    "gradio",
    "fastapi",
    "openai",
    "pdfplumber",
    "loguru",
    "pydantic_settings",
    "chromadb",
    "jieba",
]


def main() -> int:
    print("== 环境检测 ==")
    print(f"Python: {sys.version.split()[0]}")

    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        print(f"❌ 缺少依赖: {', '.join(missing)}")
        print("   请运行: uv sync")
        return 1
    print("✅ 核心依赖齐全")

    from excel_co_pilot.config import settings
    from excel_co_pilot.services.llm_gateway import LLMGateway

    if LLMGateway.available():
        print(f"✅ LLM 已就绪: {settings.llm_model} @ {settings.llm_base_url}")
    else:
        print("⚠️ 未配置 LLM（可上传/浏览数据，对话需 .env 中的 DEEPSEEK_API_KEY）")

    storage = Path(settings.storage_dir)
    print(f"存储目录: {storage}")
    storage.mkdir(parents=True, exist_ok=True)
    for sub in ("exports", "logs"):
        (storage / sub).mkdir(parents=True, exist_ok=True)
    print("✅ 存储目录就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
