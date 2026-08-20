# Excel Co-Pilot — Code Style

This guide documents the conventions actually used in this codebase so new code conforms immediately. It is **observational** — it describes what exists, not aspirational rules.

## Naming Conventions

| Element | Convention | Example |
| :--- | :--- | :--- |
| Files / modules | `snake_case.py` | `table_manager.py`, `qa_engine.py` |
| Packages | single lowercase word | `core/`, `kb/`, `ui/`, `utils/` |
| Classes | `CapWords` (PascalCase) | `TableManager`, `QAEngine`, `Services` |
| Dataclasses | `CapWords` | `Chunk`, `Hit`, `AnswerResult`, `TableInfo` |
| Exceptions | `CapWords` + `Error` | `FileValidationError`, `LoadError` |
| Protocols | `CapWords` | `EmbeddingProvider`, `Reranker`, `LLM` |
| Functions | `snake_case` | `detect_encoding`, `build_chat_openai` |
| Private helpers | `_snake_case` | `_safe_value`, `_build_context`, `_active_df` |
| Variables / attrs | `snake_case` (private `_`-prefixed) | `self._frames`, `f_col`, `f_op` |
| Constants | `UPPER_SNAKE_CASE` | `ST_INDEXED`, `KB_SCHEMA_VERSION`, `SYSTEM_PROMPT` |
| Private constants | `_UPPER_SNAKE_CASE` | `_STOP`, `_ENCODING_CANDIDATES`, `_DEFAULT_MAX_RETRIES` |

**Notes:**
- Distribution name is kebab-case (`excel-co-pilot`), package name is snake_case (`excel_co_pilot`), console script is kebab-case (`excel-co-pilot`).
- Private helpers are consistently prefixed with a single underscore `_`.
- Constants that are internal implementation details get a `_` prefix; those that are part of the module's contract don't (e.g. `MAX_REFERENCE_CHARS` public vs `_STOP` private).

## File Organization

- **src layout**: all code lives under `src/excel_co_pilot/`, organized into layers: `core/` (business), `kb/` (RAG), `services/` (LLM gateway), `ui/` (Gradio), `utils/` (cross-cutting).
- **No package-level re-exports** in `core/` — import submodules directly: `from .xxx import Y`. (`core/__init__.py` explicitly documents this.)
- **`__all__`** is used in utility modules to define the public API (e.g. `utils/logger.py`, `utils/exceptions.py`, `core/llm_factory.py`, `core/kb_context.py`).
- **Section separators** within classes use `# ---------- 名称 ----------` comment banners (e.g. `kb/db.py`, `kb/qa_engine.py`, `kb/services.py`).
- **Lazy imports inside functions** for heavy/optional deps (e.g. FastAPI inside `create_app()`, `pdfplumber` inside `_load_pdf()`, `rank_bm25` inside `_rebuild_unlocked()`).

## Import Style

Imports follow a strict order: **stdlib → third-party → local (relative)**, each group separated by a blank line. `from __future__ import annotations` is always first.

```python
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from ..config import settings
from .llm_factory import build_chat_openai
from .table_manager import TableManager
```

**Key patterns:**
- **Relative imports** throughout (`from .config import settings`, `from ..config import settings`). No absolute `excel_co_pilot.` imports inside the package.
- **Module aliasing** for name collisions: `from . import db as db_mod`, `from . import providers as pb`.
- `from __future__ import annotations` is used in **most** modules — but **not** in `main.py` (intentional: string annotations break FastAPI's `UploadFile = File(...)`).

## Code Patterns

- **Dataclasses** for data-only records (`@dataclass class Chunk:`, `@dataclass class AnswerResult:`).
- **`@tool` decorator** (LangChain) for analysis tools in `core/tools.py` — each tool is a standalone function returning JSON-serializable dicts.
- **Registry pattern** for pluggable providers (`kb/providers/base.py`): `register_*` decorators + `build_*` factories.
- **Lazy property-based initialization** with caching in `Services` (`kb/services.py`) — each component is a `@property` that builds once and caches.
- **ContextVar for session isolation** — `TableManager` is bound via `set_current_manager`/`get_current_manager` so tools read the current session's tables.
- **Module-level singletons**: `settings = Settings()` (`config.py`), `kb_config = KbConfig()` (`kb/config.py`), `get_services()` (`kb/services.py`).
- **In-place config mutation** so all modules see changes without restart (`_apply_to_settings`).

## Error Handling

- Custom exceptions subclass `Exception` and end with `Error` (`FileValidationError`, `LoadError`).
- `ValueError` / `FileNotFoundError` are raised directly for invalid input (e.g. `table_manager.py`).
- **Fault isolation**: single-item failures are caught and skipped so the batch continues (e.g. ingest). This is why `BLE001` (blind exception) and `S110` (try/except/pass) are ignored in `pyproject.toml` — they're intentional "best-effort" tolerance.
- `Raises:` sections in docstrings (Google style) document expected exceptions for complex functions.

## Logging

- **loguru** via `utils/logger.py` — `setup_logger()` configures stderr (INFO) + daily-rotating file (DEBUG, 7-day retention).
- Import the shared logger: `from loguru import logger` (in `main.py`) or `from ..utils.logger import logger`.
- The KB layer uses a structlog-style adapter (`kb/logging.py`) that forwards to loguru: `log = get_logger("services")`, then `log.info("kb_warm_up_done", chunks=...)`.
- Log messages use **snake_case keys** for structured fields.
- Comments and log messages are primarily in **Chinese**.

## Testing

- **pytest**, configured in `pyproject.toml` (`testpaths = ["tests"]`, `addopts = "-q"`).
- Test files mirror the module under test with a `test_` prefix: `tests/unit/test_loader.py`, `tests/integration/test_kb_flow.py`.
- Test functions named `test_<behavior>` (e.g. `test_load_gbk_csv`, `test_load_missing_file`).
- Tests import via the **package path** (`from excel_co_pilot.core.data_loader import DataLoader, LoadError`), not relative imports.
- Shared fixtures live in `tests/conftest.py` (e.g. `sample_csv` using `tmp_path`).
- Organized into `tests/unit/` and `tests/integration/`.

## Docstrings & Comments

- **Module docstrings** are always the first statement, in **Chinese**, describing the module's purpose. Ported modules note provenance ("移植自 yannick_vault ...").
- **Function docstrings**: one-line Chinese summaries, with optional `Args:` / `Returns:` / `Raises:` sections (Google style) for complex functions.
- **Class docstrings**: one-line Chinese descriptions.
- **Inline comments** explain *why* (rationale), not *what*, and are written in Chinese — often documenting non-obvious ordering constraints or failure modes (e.g. `db.py` busy_timeout/WAL ordering).

## Do's and Don'ts

**Do:**
- Use `snake_case` for files, functions, variables; `CapWords` for classes; `UPPER_SNAKE` for constants.
- Prefix private helpers/attrs with `_`.
- Put `from __future__ import annotations` first (except in `main.py`).
- Use relative imports inside the package.
- Write docstrings and comments in Chinese.
- Use `__all__` in utility modules.
- Use `# ---------- 名称 ----------` banners to separate sections in large classes.
- Lazy-import heavy deps inside functions.

**Don't:**
- Don't add `from __future__ import annotations` to `main.py` (breaks FastAPI `UploadFile`).
- Don't re-export submodules from package `__init__.py` in `core/`.
- Don't use absolute `excel_co_pilot.` imports inside the package — use relative imports.
- Don't use `try/except/pass` for non-best-effort logic (it's only tolerated for fault isolation).
- Don't write English docstrings in new code — the codebase is consistently Chinese (except the older `data_loader.py` / `join_service.py`).

## Known Inconsistencies (be aware)

1. `from __future__ import annotations` is used everywhere **except** `main.py` (intentional, documented).
2. Constant visibility is slightly inconsistent: `MAX_REFERENCE_CHARS` (public) vs `_STOP` (private) — both `UPPER_SNAKE`.
3. `core/data_loader.py` and `core/join_service.py` use English docstrings/comments while the rest of the codebase uses Chinese (older files).
4. `core/data_loader.py` uses `class LoadError(Exception): pass` with a bare `pass` body, unlike the more descriptive exception classes elsewhere.
