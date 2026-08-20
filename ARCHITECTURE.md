# Excel Co-Pilot — Architecture

## Overview

Excel Co-Pilot (`excel-co-pilot`) is a **local-first AI data analysis workbench** (Python 3.14). It combines a LangGraph table-analysis agent, a Gradio web UI, and a RAG knowledge base. Users analyze in-memory tables with Chinese natural language and turn documents into a searchable knowledge base — **data never leaves the machine** (except when calling cloud models, which is opt-in).

## Tech Stack

| Area | Technology |
| :--- | :--- |
| Language | Python `>=3.14` (pinned via `.python-version`) |
| Package manager / build | `uv` + `uv_build` backend (src layout) |
| Web UI | Gradio (`>=6.24`) |
| REST API | FastAPI + uvicorn |
| Table analysis agent | LangChain 1.x `create_agent` (LangGraph) + LangChain tools |
| Data | pandas, numpy, openpyxl, pyarrow, pdfplumber, PyMuPDF, python-docx, python-pptx |
| RAG vector store | ChromaDB (derived index) |
| RAG sparse search | rank-bm25 + jieba (Chinese tokenization) |
| Config | pydantic-settings (`.env`) |
| Logging | loguru (daily rotation, 7-day retention) |
| Testing | pytest, ruff (lint) |

## Directory Structure

```
C:\Gitcode\Excel_Copilot\
├── pyproject.toml          # uv build config, deps, pytest/ruff config, console script
├── uv.lock                 # locked dependency graph
├── package.json            # npm wrapper: "dev" → python -m excel_co_pilot.main
├── README.md               # feature docs, usage, REST API table, env var reference
├── .env / .env.example     # runtime config (secrets gitignored; example = template)
├── .python-version         # "3.14"
│
├── src\excel_co_pilot\     # ← the application package (src layout)
│   ├── main.py             # ENTRY POINT: main() → Gradio UI; create_app() → FastAPI REST
│   ├── config.py           # pydantic-settings singleton `settings` + runtime apply/save_env
│   │
│   ├── core\               # LAYER: table-analysis business logic (LangGraph agent)
│   │   ├── agent.py        #   LangChain create_agent tool-calling loop (8-iter cap)
│   │   ├── session.py      #   facade: LLM gateway + multi-table mgmt + memory
│   │   ├── table_manager.py#   register/switch/join multiple table files
│   │   ├── tools.py        #   Excel analysis @tool set (largest core file, ~28 KB)
│   │   ├── data_loader.py  #   file → DataFrame loading
│   │   ├── join_service.py #   LLM-driven table join config suggestion
│   │   ├── kb_context.py   #   injects RAG hits into prompt context
│   │   └── llm_factory.py  #   builds ChatOpenAI from global settings
│   │
│   ├── kb\                 # LAYER: RAG knowledge base (ported from yannick_vault)
│   │   ├── config.py       #   adapts pydantic Settings → KB Config
│   │   ├── db.py           #   SQLite access layer (SSOT)
│   │   ├── loader.py       #   registry-driven parsing of 7 doc formats → ContentBlock
│   │   ├── splitter.py     #   parent-child chunking
│   │   ├── ingest.py       #   batch/incremental ingest, MD5 dedup, resume, fault isolation
│   │   ├── indexer.py      #   ChromaDB index mgmt (rebuildable derived index)
│   │   ├── retriever.py    #   hybrid retrieval: vector + BM25 → RRF fuse → rerank
│   │   ├── qa_engine.py    #   retrieve → rewrite → refusal check → streamed answer
│   │   ├── services.py     #   component assembly (lazy init + caching)
│   │   ├── logging.py      #   structlog-style calls → loguru adapter
│   │   └── providers\      #   pluggable provider layer (registry pattern)
│   │       ├── base.py     #     Protocol interfaces + registries + builders
│   │       ├── embedding.py#     siliconflow / openai / local
│   │       ├── llm.py      #     OpenAI-compatible API / local Ollama
│   │       └── rerank.py   #     jina / cohere / siliconflow / local / none
│   │
│   ├── services\           # LAYER: thin service layer
│   │   └── llm_gateway.py  #   LLM gateway (availability check, chat)
│   │
│   ├── ui\                 # LAYER: Gradio frontend
│   │   ├── gradio_app.py   #   main UI: mode select + Tabs (build_ui entry)
│   │   ├── analysis_tab.py #   "单次问答" tab: data workbench + agent chat
│   │   ├── kb_tab.py       #   "知识库问答" tab: env nav, DB details, cited Q&A
│   │   └── progress.py     #   HTML-drawn AI answer progress bar
│   │
│   └── utils\              # LAYER: cross-cutting helpers
│       ├── encoding.py     #   text encoding detection (GBK fallback)
│       ├── exceptions.py   #   custom exception hierarchy
│       ├── logger.py       #   loguru setup, daily rotation, 7-day retention
│       ├── retry.py        #   exponential-backoff retry decorator
│       └── validators.py   #   file-size validation
│
├── tests\
│   ├── conftest.py         # shared fixture: sample_csv
│   ├── unit\               # 7 files: config, join_service, loader, session, table_manager, tools
│   └── integration\        # test_kb_flow.py (KB end-to-end flow)
│
├── scripts\
│   ├── check_environment.py    # env sanity check
│   └── generate_sample_data.py # sample data generator
│
└── storage\                # RUNTIME DATA (gitignored)
    ├── kb\                 #   active KB: raw\, kb.db (SQLite SSOT), chroma_db\ (vector index)
    ├── exports\            #   generated reports
    └── logs\               #   app_YYYY-MM-DD.log (loguru daily files)
```

## Core Components

### Entry Points
- **`main.py:main()`** — default entry; launches the Gradio UI at `http://localhost:7861` (`--host`/`--port` CLI args, `GRADIO_PORT` env override). Wires up logging, no-cache middleware, and `demo.launch()`.
- **`main.py:create_app()`** — returns a FastAPI app (REST API: `/health`, `/kb/health`, `/kb/upload`, `/kb/ask`) for standalone deployment via uvicorn.
- Console script: `excel-co-pilot = "excel_co_pilot.main:main"` (run via `uv run excel-co-pilot`).

### Layered Architecture (bottom-up dependency direction)

| Layer | Package | Responsibility |
| :--- | :--- | :--- |
| Entry | `main.py` | Gradio UI launch + FastAPI REST factory |
| UI | `ui/` | Gradio tabs; imports `core/`, `kb/`, `services/` |
| Business | `core/` | Table analysis agent, session, tools, tables |
| RAG | `kb/` | Ingest → split → index → retrieve → QA pipeline |
| Service | `services/` | LLM gateway |
| Config | `config.py` | Global `settings` singleton (pydantic-settings) |
| Utils | `utils/` | Logging, exceptions, retry, encoding, validation |

### Key Abstractions
- **`Session`** (`core/session.py`) — facade tying the LLM gateway, multi-table management, and context memory into one UI/API-friendly entry. Two modes: *simple Q&A* (files → text → LLM) and *table analysis* (tables → LangGraph agent).
- **`TableManager`** (`core/table_manager.py`) — holds multiple DataFrames, supports switching and joining. Exposed to tools via a `ContextVar` (`set_current_manager`/`get_current_manager`) for per-session isolation.
- **`Services`** (`kb/services.py`) — process-level singleton (`get_services()`) that lazily builds and caches all KB components (db, indexer, embedder, reranker, llm, retriever, qa, ingest).
- **Provider registry** (`kb/providers/base.py`) — `Protocol` interfaces + `register_*` decorators + `build_*` factories for pluggable embedding / LLM / rerank providers.
- **SSOT + derived index split** — `kb/db.py` (SQLite = source of truth) vs `kb/indexer.py` (ChromaDB = rebuildable derived index).

## Data Flow

### Table Analysis Path (单次问答)
```
ui/analysis_tab.py → core/session.py (Session.analyze_chat)
  → core/agent.py (analyze_stream, LangGraph create_agent)
    → core/tools.py (@tool set) → core/table_manager.py / core/data_loader.py
  → core/kb_context.py (optional RAG context injection)
  → event stream (thinking / tool_call / tool_result / token / done / error) → UI
```

### Knowledge Base Path (知识库问答)
```
ui/kb_tab.py → kb/services.py (get_services)
  Ingest:  kb/ingest.py → kb/loader.py + kb/splitter.py → kb/db.py + kb/indexer.py
  Q&A:     kb/retriever.py (vector + BM25 → RRF → rerank) → kb/qa_engine.py → kb/providers/*
```

### REST API Path
```
uvicorn → main.create_app()
  /kb/upload → _stream_to_disk → kb/services.ingest.process_file → refresh_indexes
  /kb/ask    → kb/services.qa.answer → AnswerResult (answer, citations, refused, ...)
```

## External Integrations

| Integration | Purpose | Config |
| :--- | :--- | :--- |
| DeepSeek / OpenAI-compatible API | LLM for chat & Q&A | `DEEPSEEK_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` |
| SiliconFlow | Embedding (BAAI/bge-m3) | `SILICONFLOW_API_KEY` |
| OpenAI | Embedding (text-embedding-3-small) | `OPENAI_API_KEY` |
| Ollama | Fully-local LLM | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| Jina / Cohere / SiliconFlow / local | Reranking | `JINA_API_KEY`, `COHERE_API_KEY` |
| ChromaDB | Vector index (local) | `storage/kb/chroma_db` |
| SQLite | KB metadata SSOT | `storage/kb/kb.db` |

## Configuration

- **Single config source**: `config.py` creates a module-level `settings` (pydantic-settings, reads `.env`). `_apply_to_settings()` mutates it in place so all modules (`from .config import settings`) see changes without restart. `save_env()` persists to `.env`.
- **KB config adapter**: `kb/config.py` wraps `settings` into a `KbConfig` with yannick-style dict access (`cfg.retrieval.get("dense_top_k")`), keeping the ported KB module nearly unchanged.
- **Runtime LLM config**: `apply_llm_config()` / `Session.configure_llm()` let the UI update API key/base URL/model live (in-memory), optionally persisting to `.env`.
- Key env vars: `DEEPSEEK_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT`, `GRADIO_PORT`, `MAX_FILE_SIZE_MB`, `KB_EMBEDDING_PROVIDER`, `KB_LLM_PROVIDER`, `KB_RERANK_PROVIDER`, `SILICONFLOW_API_KEY`, `OPENAI_API_KEY`, `JINA_API_KEY`, `COHERE_API_KEY`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. (Full list in `README.md` and `.env.example`.)

## Build & Deploy

```bash
# Install (requires uv + Python >= 3.14)
uv sync

# Launch the Gradio UI
uv run excel-co-pilot            # http://localhost:7861
# or
npm run dev                      # alternative launcher

# Launch the REST API
uv run uvicorn excel_co_pilot.main:create_app --factory --port 8000

# Run tests (use python -m to avoid the Windows uv shim issue)
uv run python -m pytest

# Lint
uv run ruff check .
```

There is **no CI, Docker, or Makefile** in this repo. Deployment is local-first (Gradio UI and/or FastAPI served via uvicorn).
