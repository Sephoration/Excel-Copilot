### 📊 Excel Co-Pilot

构建了一套**本地优先**的 AI 数据工作站：用中文自然语言操作内存表格进行分析，同时将文档资料转化为可检索的知识库，**数据全程不出域**，解决传统 BI 门槛高与敏感数据外泄的痛点。

[![Python](https://img.shields.io/badge/Python-3.14-blue.svg)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-package%20manager-8B89CC.svg)](https://docs.astral.sh/uv/)
[![Tests](https://img.shields.io/badge/tests-44%20passed-brightgreen.svg)](#-测试)

> [!note] ==数据不出域==：所有处理都在本地内存完成，不主动上传云端（调用云端模型时自行评估）。

---

### ✨ 核心能力（四大支柱）

**1. LangGraph 架构**

> 构建工作流——节点化编排、条件路由、工具调用，执行过程可追踪可回溯。工具按需调用，结果自动回流参与下一轮推理；默认 8 轮上限、recursion_limit 24 步硬限制防死循环。

**2. RAG 混合检索**

> 向量 + BM25 双路召回，经 RRF 融合后接入可插拔重排（Jina / Cohere / 硅基流动 / 本地）。父子块切分——小块检索、大块喂 LLM；召回 20 路、融合取 Top-15、精排 5 条，层层收窄。

**3. 问答管线**

> 检索 → 查询改写反思 → 拒答判定 → 流式生成四阶段闭环，回答带页码引用溯源。置信度过低或候选差距过小双保险拒答——宁可不说也不臆造；全链路本地沙盒执行、数据不出域。

**4. 文档摄入管道**

> 注册表驱动解析 7 种格式（PDF / Word / PPT / Markdown / TXT / Excel / CSV），扫描件可选 OCR。MD5 去重 + 增量摄入 + 断点续传，单条失败容错隔离。

> [!note] 另有「📊 单次问答」Tab：把 Excel / CSV / JSON / Parquet / PDF 等文件直接发进上下文，用中文自然语言分析表格数据（自动识别编码，GBK 不乱码）。

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

**单次问答（切到「📊 单次问答」Tab）**

1. **发送文件**：左侧选择 CSV / Excel / PDF / Word / MD / TXT / PPT（可多选），点「发送给 AI」
2. **简单问答**：在下方输入中文问题，例如"这些文件里，3 月份的总销售额是多少？"
3. **清空**：清空上下文，重新发送文件

**知识库问答（切到「📚 知识库问答」Tab）**

1. **新建 / 选择库**：左侧环境（知识库）下拉，可新建多库；导入文档前先切到目标库
2. **导入文档**：上传 PDF / Word / Markdown / TXT / PPT / Excel（可多选），点「导入并建立索引」
3. **提问**：例如"文档里关于 XX 是怎么说的？"回答会标注引用来源（《文件名》页码 + 相似度）
4. **压缩数据库**：当 `storage/kb/chroma_db` 体积偏大时，点「🧹 压缩数据库」重建向量索引、回收空间
5. 检索不到相关内容时会明确拒答，不会编造

> [!note] 知识库需要 Embedding 能力：默认走硅基流动（`SILICONFLOW_API_KEY`），也可配 `OPENAI_API_KEY` 或本地模型（`KB_EMBEDDING_PROVIDER=local`，需装 sentence-transformers）。只有 LLM 没有 Embedding Key 时，导入会提示 embedding 失败。

> [!note] 完全离线：设置 `KB_LLM_PROVIDER=ollama` + `KB_EMBEDDING_PROVIDER=local` 后，知识库的检索与问答全部在本机完成（需先启动 Ollama，`OLLAMA_MODEL` 默认 qwen2.5:7b）。本地嵌入用 bge-small 时，系统会自动用适配的低阈值（真实命中余弦约 0.45），不会被「置信度过低」误拒答。

---

### 🌐 REST API（可选）

```bash
uv run uvicorn excel_co_pilot.main:create_app --factory --port 8000
```

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/health` | 健康检查（含 LLM 就绪状态） |
| GET | `/kb/health` | 知识库健康检查（含 LLM 与索引统计） |
| POST | `/kb/upload` | 上传文档并建索引（multipart） |
| POST | `/kb/ask` | 知识库问答，JSON `{"question": "...", "filters": {...}}` |

---

### ✅ 测试

```bash
uv run python -m pytest
```

> [!note] 本机直接 `uv run pytest` 会触发 uv 的 Windows shim 报错，请用 `uv run python -m pytest`。

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
| `KB_EMBEDDING_PROVIDER` | `siliconflow` | 知识库向量模型：`siliconflow` / `openai` / `local` |
| `KB_LLM_PROVIDER` | `api` | 知识库问答 LLM：`api`（OpenAI 兼容）/ `ollama`（本地） |
| `SILICONFLOW_API_KEY` | 空 | 硅基流动（BAAI/bge-m3 中文首选） |
| `OPENAI_API_KEY` | 空 | OpenAI text-embedding-3-small |
| `KB_RERANK_PROVIDER` | `none` | 重排：`none` / `jina` / `cohere` / `siliconflow` / `local` |
| `JINA_API_KEY` / `COHERE_API_KEY` | 空 | 重排 API Key |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5:7b` | 完全离线模式（LLM） |

> [!note] 更多高级参数（切分 / 检索 / 拒答阈值等）见 `src/excel_co_pilot/config.py`。

---

### 🧰 技术栈

Python 3.14 · LangGraph · Pandas · Gradio · FastAPI · pdfplumber · Loguru · Pydantic Settings · ChromaDB · rank-bm25 · jieba · PyMuPDF