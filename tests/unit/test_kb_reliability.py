from __future__ import annotations

import unittest
from types import SimpleNamespace

from excel_co_pilot.kb.db import Chunk, FileRecord, ST_INDEXED
from excel_co_pilot.kb.logging import _trace_var
from excel_co_pilot.kb.qa_engine import QAEngine, _merge_hits
from excel_co_pilot.kb.retriever import Hit, Retriever


def hit(chunk_id: int, score: float = 0.9) -> Hit:
    chunk = Chunk(chunk_id, chunk_id, None, 0, "参考内容" * 100, page=1)
    return Hit(chunk_id, chunk_id, f"{chunk_id}.txt", 1, 0, score, chunk, None, score)


class FakeDB:
    def __init__(self):
        self.files = {
            1: FileRecord(1, "/a", "a.txt", status=ST_INDEXED, library="one"),
            2: FileRecord(2, "/b", "b.txt", status=ST_INDEXED, library="two"),
        }
        self.chunks = {i: hit(i).leaf for i in self.files}

    def get_file(self, doc_id):
        return self.files.get(doc_id)

    def get_chunk(self, chunk_id):
        return self.chunks.get(chunk_id)

    def get_parent_chunk(self, chunk_id):
        return None

    def library_file_ids(self, name):
        return [f.id for f in self.files.values() if f.library == name]

    def list_files(self, status=None, limit=1000, offset=0):
        return list(self.files.values())[offset:offset + limit]


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.r = object.__new__(Retriever)
        self.r.cfg = SimpleNamespace(retrieval={"use_rerank": False})
        self.r.db = FakeDB()
        self.r.indexer = SimpleNamespace(query=lambda *a, **k: [
            {"chunk_id": 2, "score": 0.9}, {"chunk_id": 1, "score": 0.8}
        ])
        self.r.embedder = SimpleNamespace(embed=lambda _q: [[1.0]])
        self.r.sparse = SimpleNamespace(search=lambda *a, **k: [(2, 10.0), (1, 9.0)])
        self.r.reranker = None

    def test_exact_filename_applies_to_both_search_paths(self):
        hits = self.r.hybrid_search("query", filters={"file_name": "a.txt"})
        self.assertEqual([h.doc_id for h in hits], [1])

    def test_filter_intersection_and_empty_selection(self):
        self.assertEqual(self.r.hybrid_search("query", filters={"doc_ids": [2], "file_names": ["a.txt"]}), [])
        self.assertEqual(self.r.hybrid_search("query", filters={"file_names": []}), [])
        self.assertEqual(self.r.hybrid_search("query", filters={"library": "one", "doc_ids": [2]}), [])
        with self.assertRaises(ValueError):
            self.r.hybrid_search("query", filters={"file_names": "a.txt"})

    def test_sparse_filter_applies_before_top_k(self):
        self.r.cfg.retrieval["sparse_top_k"] = 1
        self.r.indexer = SimpleNamespace(query=lambda *a, **k: [])
        hits = self.r.hybrid_search("query", filters={"doc_ids": [1]})
        self.assertEqual([h.doc_id for h in hits], [1])

    def test_unindexed_document_does_not_leak_from_stale_vector(self):
        self.r.db.files[2].status = "failed"
        self.assertEqual([h.doc_id for h in self.r.hybrid_search("q")], [1])


class QATests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(chat={"max_context_chars": 80}, retrieval={}, refusal={})
        self.engine = QAEngine(self.cfg, None, None, None)

    def test_context_respects_budget_and_citations_are_in_sync(self):
        text, citations = self.engine._build_context([hit(1), hit(2)])
        self.assertLessEqual(len(text), 80)
        self.assertEqual(len(citations), 1)
        self.assertTrue(citations[0]["text"] in text)
        self.cfg.chat["max_context_chars"] = 2
        self.assertEqual(self.engine._build_context([hit(1)]), ("", []))

    def test_merge_keeps_first_rank_without_mutating_inputs(self):
        one, two, improved = hit(1, 0.3), hit(2, 0.5), hit(1, 0.9)
        result = _merge_hits([one, two, improved])
        self.assertEqual([h.chunk_id for h in result], [1, 2])
        self.assertEqual(result[0].cosine, 0.9)
        self.assertEqual(one.cosine, 0.3)

    def test_rewrite_after_no_hits(self):
        calls = []
        def search(query, filters=None):
            calls.append(query)
            return [hit(1)] if query == "改写" else []
        self.engine.retriever = SimpleNamespace(hybrid_search=search)
        self.engine.llm = SimpleNamespace(complete=lambda *a, **k: "改写")
        result, variants = self.engine._retrieve_with_reflection("原文", None)
        self.assertEqual(calls, ["原文", "改写"])
        self.assertEqual(len(result), 1)
        self.assertEqual(variants, ["改写"])

    def test_exception_clears_trace_context(self):
        def fail(*args, **kwargs):
            raise RuntimeError("embedding failed")
        self.engine.retriever = SimpleNamespace(hybrid_search=fail)
        with self.assertRaises(RuntimeError):
            self.engine.answer("问题")
        self.assertIsNone(_trace_var.get())


if __name__ == "__main__":
    unittest.main()
