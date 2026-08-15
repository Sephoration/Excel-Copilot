"""KB 集成测试：摄入 → 混合检索 → RAG 问答 全链路（离线，用假 Provider）。

覆盖：
- 摄入（md 解析、切分、索引、写回 SQLite + Chroma）
- 混合检索（向量 + BM25 + RRF）
- 问答（引用、拒答判定）
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from excel_co_pilot.kb import providers as pb
from excel_co_pilot.kb.config import KbConfig
from excel_co_pilot.kb.services import Services


class FakeEmbed:
    """字符 bigram 词袋向量（维度 128）：共享子串越多，余弦越高。

    对 bge-m3 / 向量模型 这类 token 在查询与文档中都出现时给出合理相似度，
    与真实 embedding 行为一致（相关问题相似度高、无关问题相似度低）。
    """

    name = "fake"
    dim = 128
    normalize = True

    def __init__(self, **kwargs) -> None:
        pass

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        v = [0.0] * self.dim
        for i in range(len(t) - 1):
            g = t[i : i + 2]
            if not g.strip():
                continue
            idx = hashlib.md5(g.encode("utf-8")).digest()[0] % self.dim
            v[idx] += 1.0
        n = (sum(x * x for x in v)) ** 0.5 or 1.0
        return [x / n for x in v]

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class FakeLLM:
    name = "fake"
    supports_tools = False

    def __init__(self, **kwargs) -> None:
        pass

    def complete(self, prompt, history=None, stream=False, **kwargs):
        return "根据资料，BAAI/bge-m3 是智源发布的多语言向量模型。"

    def chat(self, messages, tools=None, stream=False, **kwargs):
        return {"role": "assistant", "content": "ok", "tool_calls": None}


@pytest.fixture(scope="module")
def services(tmp_path_factory) -> Services:
    """独立临时存储的 Services 实例（假 provider 注册后装配）。"""
    pb._EMBEDDING_REGISTRY["fake"] = FakeEmbed
    pb._LLM_REGISTRY["fake"] = FakeLLM
    from excel_co_pilot.kb.providers.rerank import NoopReranker

    pb._RERANK_REGISTRY["fake_none"] = NoopReranker

    cfg = KbConfig()
    # 重定向存储到临时目录
    root = tmp_path_factory.mktemp("kb")
    cfg._paths.update(
        {
            "storage_dir": str(root),
            "raw_dir": str(root / "raw"),
            "chroma_dir": str(root / "chroma_db"),
            "db_path": str(root / "kb.db"),
            "logs_dir": str(root / "logs"),
            "notes_dir": str(root / "notes"),
            "userdict": str(root / "userdict.txt"),
        }
    )
    cfg.embedding["provider"] = "fake"
    cfg.llm["provider"] = "fake"
    cfg.rerank["provider"] = "fake_none"
    # 离线假向量相似度量纲与真实 embedding 不同，调低阈值以适配测试
    cfg.refusal["absolute_threshold"] = 0.15
    cfg.refusal["margin"] = 0.02
    cfg.retrieval["query_rewrite_threshold"] = 0.10
    svc = Services(cfg)
    yield svc
    svc.close()


def _write_doc(svc: Services, name: str, text: str) -> pathlib.Path:
    raw = svc.cfg.raw_dir
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / name
    p.write_text(text, encoding="utf-8")
    return p


DOC_TEXT = """# 模型介绍

BAAI/bge-m3 是智源研究院发布的多语言向量模型。

## 特点

支持中文、英文等多语言混合检索，输出 1024 维向量。

## 用途

常用于 RAG 知识库的语义检索阶段。

# 使用方法

可以通过硅基流动 API 或本地 sentence-transformers 调用。
"""


def test_ingest_indexes_file(services: Services):
    p = _write_doc(services, "model.md", DOC_TEXT)
    res = services.ingest.process_file(p)
    assert res["status"] == "indexed", res
    assert res["chunks"] > 0
    # SQLite 落库
    files = services.db.list_files()
    assert any(f.name == "model.md" for f in files)
    assert services.db.count_indexed_chunks() > 0
    # Chroma 落库
    assert services.indexer.count() > 0


def test_ingest_dedup(services: Services):
    text = "# 独立内容\n\n这是用于去重测试的独有文本，不与其它用例共享。\n" * 3
    p = _write_doc(services, "dedup_a.md", text)
    r1 = services.ingest.process_file(p)
    assert r1["status"] == "indexed", r1
    # 同路径同 hash 二次摄入应跳过
    r2 = services.ingest.process_file(p)
    assert r2.get("skipped") is True, r2
    # 内容级去重：同 hash 不同文件名 → duplicate
    p2 = _write_doc(services, "dedup_b.md", text)
    r3 = services.ingest.process_file(p2)
    assert r3["status"] == "duplicate", r3


def test_hybrid_search_returns_hits(services: Services):
    hits = services.retriever.hybrid_search("bge-m3 向量模型是什么")
    assert len(hits) > 0
    top = hits[0]
    assert top.file_name.endswith(".md")  # 命中知识库文档
    assert top.leaf is not None
    # 命中叶子或父块均可；若命中叶子则父块应能展开
    if top.parent is None:
        assert top.leaf.parent_id is None  # 命中的是父块本身
    else:
        assert top.parent.text  # 父子块展开有父上下文
    assert "bge-m3" in top.leaf.text.lower()


def test_qa_answer_with_citations(services: Services):
    r = services.qa.answer("bge-m3 是什么模型？")
    assert r.answer
    assert not r.refused
    assert len(r.citations) > 0
    assert r.citations[0]["file_name"] == "model.md"
    assert "bge-m3" in r.answer.lower() or "向量" in r.answer


def test_qa_refuses_out_of_scope(services: Services):
    # 与知识库完全无关的问题：检索置信度过低 → 应拒答（不臆造）
    r = services.qa.answer("今天上海天气怎么样？")
    assert r.refused, f"应拒答，实际 answer={r.answer!r} hits={len(r.hits)}"
