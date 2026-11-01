"""确定性哈希与内容指纹。

整个系统的 ID 都从内容派生，保证「同一输入 → 同一 ID → 同一产物」，
这是复现性承诺的基础（见 docs/ARCHITECTURE.md §5）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

_HEX_RE = re.compile(r"[^0-9a-f]")


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1_text(text: str) -> str:
    return sha1_bytes(text.encode("utf-8", errors="replace"))


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def short_id(seed: str, n: int = 16) -> str:
    """取内容哈希前 n 位；seed 为空时返回全零，用于稳定占位。"""
    if not seed:
        return "0" * n
    return sha1_text(seed)[:n]


def paper_id_from_bytes(pdf_bytes: bytes) -> str:
    """文献主键：PDF 字节内容的 sha1 前 16 位。"""
    return sha1_bytes(pdf_bytes)[:16]


def chunk_id(paper_id: str, page: int, order_idx: int, text: str) -> str:
    return short_id(f"{paper_id}|{page}|{order_idx}|{text.strip()}")


def stable_json(value: Any, *, sort_keys: bool = True) -> str:
    """稳定序列化：用于比较产物、计算产物包哈希。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
        default=str,
    )


def content_digest(value: Any) -> str:
    return sha1_text(stable_json(value))


def artifact_tree_digest(files: Iterable[tuple[str, bytes]]) -> str:
    """对 (相对路径, 内容) 序列计算整体哈希，用于确定性断言。"""
    digest = hashlib.sha1()
    for name, payload in sorted(files, key=lambda item: item[0]):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha1(payload).digest())
    return digest.hexdigest()


def simhash64(text: str, *, shingle: int = 2, limit: int = 4000) -> int:
    """64 位 SimHash：接近线性时间，用于近似去重（无需外部依赖）。"""
    tokens = _shingles(text.lower(), shingle)[:limit]
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        h = int(sha1_text(token)[:16], 16)
        for bit in range(64):
            vector[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit in range(64):
        if vector[bit] > 0:
            out |= 1 << bit
    return out


def _shingles(text: str, size: int) -> list[str]:
    compact = re.sub(r"\s+", " ", text)
    if len(compact) <= size:
        return [compact] if compact else []
    return [compact[i : i + size] for i in range(0, len(compact) - size + 1, max(1, size // 2))]


def hamming64(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def simhash_similarity(a: int, b: int) -> float:
    """0~1，1 表示完全一致。"""
    return 1.0 - hamming64(a, b) / 64.0
