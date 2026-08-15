"""智能切分：Markdown/Word 标题层级 → 段落 → 语义窗口。

产出父子块：
- 叶子块（leaf）：检索用，粒度小；
- 父块（parent）：所属章节，喂给 LLM，兼顾上下文完整。

移植自 yannick_vault core/splitter.py，仅调整导入路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .db import Chunk
from .loader import ContentBlock
from .logging import get_logger

log = get_logger("splitter")


def estimate_tokens(text: str) -> int:
    """中文按字、英文按词粗略估计 token 数。"""
    cjk = sum(
        1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff"
    )
    words = len(re.findall(r"[A-Za-z0-9_]+", text))
    return cjk + words


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


@dataclass
class _Section:
    title: str = ""
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


def _build_sections(blocks: list[ContentBlock]) -> list[_Section]:
    """按标题层级组织为章节树（扁平化为有序 section 列表）。"""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (level, heading_text)

    def current_title() -> str:
        return " / ".join(t for _, t in stack)

    for b in blocks:
        if b.kind == "heading" and b.heading_level > 0:
            while stack and stack[-1][0] >= b.heading_level:
                stack.pop()
            stack.append((b.heading_level, b.text))
            continue
        if not sections:
            sections.append(_Section(title=current_title()))
        if b.text.strip():
            sections[-1].paragraphs.extend(_split_paragraphs(b.text))
    if not sections:
        sections = [_Section(title="")]
    return sections


def split_blocks(
    blocks: list[ContentBlock],
    chunk_size: int = 800,
    overlap: int = 100,
    min_chunk_size: int = 60,
    max_parent_tokens: int = 2000,
) -> list[Chunk]:
    """返回 Chunk 列表。顺序：每个章节先父块、后叶子；父子用临时负 id 关联。"""
    chunk_size = max(chunk_size, 40)
    overlap = min(overlap, chunk_size // 2)
    sections = _build_sections(blocks)

    out: list[Chunk] = []
    id_counter = [0]  # 负 id 计数器（闭包可变）

    def new_id() -> int:
        id_counter[0] -= 1
        return id_counter[0]

    idx = 0
    for sec in sections:
        parent_chunks, parent_offsets = _split_parent(sec, max_parent_tokens, new_id)
        leaf_chunks, leaf_offsets = _split_leaves(
            sec, chunk_size, overlap, min_chunk_size
        )

        out.extend(parent_chunks)
        for leaf, offset in zip(leaf_chunks, leaf_offsets):
            parent_i = _locate_parent(offset, parent_offsets)
            parent = parent_chunks[parent_i] if parent_chunks else None
            out.append(
                Chunk(
                    id=new_id(),
                    doc_id=0,
                    parent_id=parent.id if parent else None,
                    index=idx,
                    text=leaf,
                    token_len=estimate_tokens(leaf),
                )
            )
            idx += 1
        for p in parent_chunks:
            p.index = idx
            idx += 1
    return out


def _prefix_title(title: str, limit: int = 80) -> str:
    return title[:limit].strip() if title else ""


def _split_parent(
    sec: _Section, max_tokens: int, new_id
) -> tuple[list[Chunk], list[int]]:
    """父块：章节按段落切成 ≤max_tokens 的段，每段带标题前缀；超长段先硬切。"""
    chunks: list[Chunk] = []
    offsets: list[int] = []
    title = _prefix_title(sec.title)
    buf: list[str] = []
    cur_tokens = 0
    offset = 0

    def flush():
        nonlocal cur_tokens, offset, buf
        if not buf:
            return
        head = f"【{title}】\n" if title else ""
        text = head + "\n\n".join(buf)
        chunks.append(
            Chunk(
                id=new_id(),
                doc_id=0,
                parent_id=None,
                index=0,
                text=text,
                token_len=estimate_tokens(text),
            )
        )
        offsets.append(offset)
        cur_tokens = 0
        buf = []

    for para in sec.paragraphs:
        parts = _hard_split_text(para, max_tokens)
        for piece in parts:
            piece_tokens = estimate_tokens(piece)
            if cur_tokens + piece_tokens > max_tokens and buf:
                flush()
            buf.append(piece)
            cur_tokens += piece_tokens + 2
            offset += len(piece) + 2
    flush()
    if not chunks:
        text = title or "(空章节)"
        chunks.append(
            Chunk(
                id=new_id(),
                doc_id=0,
                parent_id=None,
                index=0,
                text=text,
                token_len=estimate_tokens(text),
            )
        )
        offsets.append(0)
    return chunks, offsets


def _split_leaves(
    sec: _Section, chunk_size: int, overlap: int, min_chunk_size: int
) -> tuple[list[str], list[int]]:
    """叶子块：按 token 预算累积段落，带 overlap，过短合并。"""
    title = _prefix_title(sec.title)
    head = f"【{title}】\n" if title else ""
    leaves: list[str] = []
    offsets: list[int] = []

    cur_buf: list[str] = []
    cur_tokens = 0
    offset = 0

    def flush(force: bool = False):
        nonlocal cur_buf, cur_tokens
        text = head + "\n\n".join(cur_buf)
        tokens = estimate_tokens(text)
        if not force and tokens < min_chunk_size and leaves:
            leaves[-1] = leaves[-1] + "\n\n" + "\n\n".join(cur_buf)
            return
        leaves.append(text)
        offsets.append(max(offset - len(text), 0))
        keep = _tail_tokens(cur_buf, overlap)
        cur_buf = keep
        cur_tokens = estimate_tokens(head + "\n\n".join(keep))

    def push(piece: str, piece_tokens: int):
        nonlocal cur_tokens, offset
        if cur_tokens + piece_tokens > chunk_size and cur_buf:
            flush()
        cur_buf.append(piece)
        cur_tokens += piece_tokens + 2
        offset += len(piece) + 2

    for para in sec.paragraphs:
        parts = _hard_split_text(para, chunk_size)
        for piece in parts:
            push(piece, estimate_tokens(piece))
    if cur_buf:
        flush(force=True)
    if not leaves:
        leaves.append(head or "(空章节)")
        offsets.append(0)
    return leaves, offsets


def _hard_split_text(text: str, max_tokens: int) -> list[str]:
    """超长段落按行硬切分，保证单片不超过 max_tokens；超长单行按字符再切。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    parts: list[str] = []
    for ln in text.split("\n"):
        if estimate_tokens(ln) > max_tokens:
            step = max(max_tokens, 200)
            for i in range(0, len(ln), step):
                parts.append(ln[i : i + step])
            continue
        if parts and estimate_tokens(parts[-1] + "\n" + ln) <= max_tokens:
            parts[-1] = f"{parts[-1]}\n{ln}"
        else:
            parts.append(ln)
    return [p for p in parts if p.strip()] or [text]


def _tail_tokens(buf: list[str], overlap_tokens: int) -> list[str]:
    """取末尾近似 overlap 的段落。"""
    if overlap_tokens <= 0 or not buf:
        return []
    acc, keep = 0, []
    for p in reversed(buf):
        acc += estimate_tokens(p)
        keep.append(p)
        if acc >= overlap_tokens:
            break
    return list(reversed(keep))


def _locate_parent(offset: int, parent_offsets: list[int]) -> int:
    """按偏移量找叶子所属父块下标。"""
    if not parent_offsets:
        return 0
    for i in range(len(parent_offsets) - 1):
        if parent_offsets[i] <= offset < parent_offsets[i + 1]:
            return i
    return len(parent_offsets) - 1
