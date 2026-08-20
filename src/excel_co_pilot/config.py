from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 环境变量名 -> 设置字段名
_ENV_FIELDS = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "LLM_BASE_URL": "llm_base_url",
    "LLM_MODEL": "llm_model",
    "LLM_TIMEOUT": "llm_timeout",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 60.0

    max_file_size_mb: int = 200

    storage_dir: Path = PROJECT_ROOT / "storage"

    # ---- 表格分析（结构化问数 / 多表联表）配置 ----
    excel_preview_rows: int = 5
    excel_default_result_limit: int = 20
    excel_max_result_limit: int = 1000
    # 工具调用循环最大轮数
    agent_max_iterations: int = 8

    # ---- 知识库（KB / RAG）配置 ----
    # KB 问答 LLM：api = OpenAI 兼容（DeepSeek 等，需 API Key）；ollama = 完全本地
    kb_llm_provider: str = "api"
    # Embedding（siliconflow / openai / local）
    kb_embedding_provider: str = "siliconflow"
    kb_embedding_model: str = ""  # 空则按 provider 取默认模型
    kb_embedding_dim: int = 1024
    # Rerank（none / jina / cohere / siliconflow / local）
    kb_rerank_provider: str = "none"
    # 各 provider 的 API Key（.env 配置）
    siliconflow_api_key: str = ""
    openai_api_key: str = ""
    jina_api_key: str = ""
    cohere_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # 检索
    kb_dense_top_k: int = 30
    kb_sparse_top_k: int = 30
    kb_rrf_k: int = 60
    kb_rrf_top_k: int = 20
    kb_final_top_k: int = 5
    kb_use_rerank: bool = True
    kb_query_rewrite_threshold: float = 0.62
    kb_query_rewrite_variants: int = 3
    # 拒答
    kb_refusal_absolute_threshold: float = 0.62
    kb_refusal_margin: float = 0.1
    # 切分
    kb_chunk_size: int = 800
    kb_chunk_overlap: int = 100
    kb_min_chunk_size: int = 60
    kb_max_parent_tokens: int = 2000
    # 摄入
    kb_ingest_workers: int = 8
    kb_ocr_enabled: bool = False
    # 会话
    kb_history_turns: int = 8
    kb_max_context_chars: int = 60000

    @property
    def llm_ready(self) -> bool:
        return bool(self.deepseek_api_key)


settings = Settings()


def _apply_to_settings(updates: dict[str, str]) -> None:
    """把更新同步到内存中的 settings 单例（无需重启即对全进程生效）。

    settings 被各模块以 `from .config import settings` 引用（同一对象），
    就地更新后 LLM 网关、知识库 providers 与健康检查都能立即感知。
    """
    for env, field in _ENV_FIELDS.items():
        if env in updates:
            raw = updates[env]
            setattr(settings, field, float(raw) if field == "llm_timeout" else raw)


def save_env(updates: dict[str, str], path: Path | None = None) -> None:
    """把配置写入 .env，并同步更新内存中的 settings 单例。"""
    target = path or PROJECT_ROOT / ".env"
    if not target.exists():
        target.write_text("# Excel Co-Pilot 配置（由界面写入）\n", encoding="utf-8")
    lines = target.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}")
    target.write_text("\n".join(out) + "\n", encoding="utf-8")

    _apply_to_settings(updates)


def apply_llm_config(
    api_key: str, base_url: str, model: str, persist: bool = False
) -> None:
    """让 LLM 配置立即生效（内存），persist=True 时额外写入 .env。

    表格分析 Session 与知识库 / REST API 读的都是同一份 settings：
    即使不勾选「保存到 .env」，内存同步后 KB 问答与 /kb/ask 也能直接用上新 key。
    """
    updates = {
        "DEEPSEEK_API_KEY": (api_key or "").strip(),
        "LLM_BASE_URL": (base_url or "").strip(),
        "LLM_MODEL": (model or "").strip(),
    }
    _apply_to_settings(updates)
    if persist:
        save_env({k: v for k, v in updates.items() if v})
