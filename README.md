### 📊 Excel Co-Pilot

本地优先、AI 驱动的 **数据与知识问答工作站**：一边用中文自然语言操作内存中的表格（清洗 / 计算 / 多表关联 / 交互式图表），一边把 PDF / Word / Markdown / Excel 文档变成可检索的知识库，基于 RAG 用中文提问并带回溯引用。

[![Python](https://img.shields.io/badge/Python-3.14-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-package%20manager-8B89CC.svg)](https://docs.astral.sh/uv/)
[![Tests](https://img.shields.io/badge/tests-46%20passed-brightgreen.svg)](#-测试)

> [!note] ==数据不出域==：所有处理都在本地内存完成，不主动上传云端（调用云端模型时自行评估）。

---

### ✨ 特性

**📊 表格数据分析（Tab 1）**

| 能力 | 说明 |
| :--- | :--- |
| 多格式接入 | CSV / Excel / JSON / Parquet / PDF 表格（自动识别编码，GBK 不乱码） |
| 自动 EDA | 行 / 列数、空值率、唯一值、类型推断、离群值（Z-score > 3）预检 |
| 自然语言转代码 | LangGraph 状态机 Planner → Executor → Reflector → Responder，出错自动反思重试 |
| 多表关联 + 回滚 | 支持多步操作，结果写回数据池；最多 20 步快照，可撤销 / 重置 |
| 交互式图表 | Plotly 自动推荐图表类型，可缩放、悬停看值 |
| 报告导出 | 一键导出 HTML + PDF（中文支持） |
| 安全沙箱 | AST 白名单 + 文件读写拦截 + 线程超时，杜绝 import / 文件外带 / 危险调用 |

**📚 知识库问答（Tab 2，RAG）**

| 能力 | 说明 |
| :--- | :--- |
| 多格式摄入 | PDF / Word / Markdown / TXT / PPT / Excel，MD5 去重 + 增量 + 断点续传 |
| 智能切分 | 标题层级 → 段落 → 语义窗口，**父子块**（小块检索、大块喂 LLM） |
| 混合检索 | 向量（语义）+ BM25（关键词）→ RRF 融合 → 可插拔重排（Jina / Cohere / 硅基流动 / 本地） |
| RAG 问答 | 引用溯源、双保险拒答、查询改写反思、流式输出 |
| 可插拔 Provider | Embedding / Rerank / LLM 全部可切换（API 或本地 Ollama，完全离线） |

---

### 🚀 快速开始

#### 1. 安装依赖

需要 [uv](https://docs.astral.sh/uv/) 与 Python `>= 3.14`：

```bash
uv sync
```

#### 2. 配置 LLM（DeepSeek）

两种方式任选其一：

- **界面配置（推荐）**：启动后在页面顶部「⚙️ 模型配置」填写 API Key / Base URL / 模型，点「应用并连接」立即生效；勾选"保存到 .env"则重启后仍有效。
- **文件配置**：

```bash
cp .env.example .env
```

在 `.env` 填入 `DEEPSEEK_API_KEY`。

> [!note] 不配置也能启动，可上传 / 浏览数据，只是对话不可用。支持任意 OpenAI 兼容协议，改 `LLM_BASE_URL` / `LLM_MODEL` 即可对接其他模型。

#### 3. 启动界面

```bash
uv run excel-co-pilot
```

打开 http://localhost:7861 ，上传数据后用中文对话分析。端口可用环境变量 `GRADIO_PORT` 覆盖（如 `GRADIO_PORT=8080 uv run excel-co-pilot`）。

---

### 🖥️ 使用指南

**表格数据分析（切到「📊 表格数据分析」Tab）**

1. **上传数据**：左侧选择 CSV / Excel / JSON / Parquet / PDF（可多选），点「加载到会话」
2. **浏览数据**：下拉切换数据表，右侧查看预览
3. **对话分析**：输入自然语言指令，例如
   - 合并两表，按城市统计金额，画柱状图
   - 新增一列"利润"，等于销售额减成本
   - 把上一张图改成红色
4. **导出报告**：一键下载 HTML + PDF
5. **回滚**：撤销上一步 / 重置会话

**知识库问答（切到「📚 知识库问答」Tab）**

1. 上传 PDF / Word / Markdown / TXT / PPT / Excel 文档（可多选），点「导入并建立索引」
2. 提问，例如：文档里关于 XX 是怎么说的？回答会标注引用来源（《文件名》页码 + 相似度）
3. 检索不到相关内容时会明确拒答，不会编造

> [!note] 知识库需要 Embedding 能力：默认走硅基流动（`SILICONFLOW_API_KEY`），也可配 `OPENAI_API_KEY` 或本地模型（`KB_EMBEDDING_PROVIDER=local`，需装 sentence-transformers）。只有 LLM 没有 Embedding Key 时，导入会提示 embedding 失败。

---

### 🌐 REST API（可选）

```bash
uv run uvicorn excel_co_pilot.main:create_app --factory --port 8000
```

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/health` | 健康检查（含 LLM 就绪状态） |
| POST | `/upload?session_id=...` | 上传表格文件（multipart） |
| POST | `/chat` | 表格对话，JSON `{"session_id": "...", "message": "..."}` |
| GET | `/kb/health` | 知识库健康检查（含 LLM 与索引统计） |
| POST | `/kb/upload` | 上传文档并建索引（multipart） |
| POST | `/kb/ask` | 知识库问答，JSON `{"question": "...", "filters": {...}}` |

---

### 🗂️ 项目结构

```
src/excel_co_pilot/
├── main.py            # 入口：FastAPI（create_app）+ Gradio（main）
├── config.py          # Pydantic 配置（读取 .env，含 KB 配置）
├── core/              # 数据加载 / 探查 / 数据池 / 沙箱 / 可视化 / 会话
├── agent/             # LangGraph 状态机（state / prompts / nodes / graph）
├── kb/                # 知识库 RAG
│   ├── loader.py      # 多格式文档解析（PDF/MD/TXT/DOCX/PPTX/XLSX）
│   ├── splitter.py    # 父子块切分（标题层级 → 语义窗口）
│   ├── db.py          # SQLite 单一事实源（SSOT）
│   ├── indexer.py     # ChromaDB 向量索引（可重建）
│   ├── ingest.py      # 摄入管道（MD5 去重 / 增量 / 断点续传）
│   ├── retriever.py   # 混合检索（向量 + BM25 → RRF → 重排）
│   ├── qa_engine.py   # RAG 问答（改写反思 / 拒答 / 引用溯源 / 流式）
│   ├── providers/     # 可插拔 Embedding / Rerank / LLM
│   └── services.py    # 组件装配（进程级单例）
├── services/          # LLM 网关 / PDF 导出
├── ui/                # Gradio 界面（数据分析 Tab + 知识库 Tab）
└── utils/             # 日志 / 异常 / 校验
```

---

### ✅ 测试

```bash
uv run python -m pytest
```

> [!note] 本机直接 `uv run pytest` 会触发 uv 的 Windows shim 报错，请用 `uv run python -m pytest`。

---

### 🔒 安全说明

- ==默认只在本地内存处理数据==，不主动上传云端（使用云端模型时自行评估）
- AI 生成代码在受限沙箱执行：
  - 仅注入 `pd` / `np` / `df*`，禁止 `import`、私有属性、危险内置函数
  - 拦截 pandas / numpy 全部文件读写方法（`read_*` / `to_*` / `np.load` 等），杜绝文件外带
  - 5 秒线程超时
- 上传文件默认上限 200MB（`MAX_FILE_SIZE_MB` 可调）

---

### ⚙️ 配置项

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DEEPSEEK_API_KEY` | 空 | LLM API Key（对话 / 问答必需） |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_TIMEOUT` | `60` | LLM 请求超时（秒） |
| `GRADIO_PORT` | `7861` | 界面端口 |
| `MAX_FILE_SIZE_MB` | `200` | 上传文件上限 |
| `EXECUTOR_TIMEOUT` | `5` | 沙箱执行超时（秒） |
| `MAX_REFLECT_RETRIES` | `3` | 出错自动反思重试次数 |
| `KB_EMBEDDING_PROVIDER` | `siliconflow` | 知识库向量模型：`siliconflow` / `openai` / `local` |
| `SILICONFLOW_API_KEY` | 空 | 硅基流动（BAAI/bge-m3 中文首选） |
| `OPENAI_API_KEY` | 空 | OpenAI text-embedding-3-small |
| `KB_RERANK_PROVIDER` | `none` | 重排：`none` / `jina` / `cohere` / `siliconflow` / `local` |
| `JINA_API_KEY` / `COHERE_API_KEY` | 空 | 重排 API Key |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:7b` | 完全离线模式（LLM） |

> [!note] 更多高级参数（切分 / 检索 / 拒答阈值 / 联网兜底等）见 `src/excel_co_pilot/config.py`。

---

### 🧰 技术栈

Python 3.14 · LangGraph · Pandas · Plotly · Gradio · FastAPI · pdfplumber · reportlab · Loguru · Pydantic Settings · ChromaDB · rank-bm25 · jieba · PyMuPDF
