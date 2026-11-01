"""向量化 Provider：确定性哈希向量（默认）与在线 bge-m3（可选）。

设计目标：
- 默认通道不依赖网络与第三方模型，任何机器上得到完全相同的向量；
- 在线通道仅在显式配置凭证时启用，失败自动回落默认通道；
- 向量以 float32 小端 BLOB 存储，便于 SQLite 直接持久化与跨进程复现。
"""

from __future__ import annotations

import math
import struct
import threading
import zlib
from typing import Any, Iterable, Sequence

from ..core.config import Settings, get_settings
from ..core.errors import UpstreamError
from ..core.logging import get_logger
from ..core.textutil import tokens
from .base import EmbeddingEncoder, ProviderStatus

log = get_logger(__name__)

#: 特征权重：整词 > 词内字符三元组（缓解未登录词/词形变化/CJK 分词粒度问题）
WORD_WEIGHT = 1.0
TRIGRAM_WEIGHT = 0.35


def _crc(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _features(text: str) -> dict[str, float]:
    feats: dict[str, float] = {}
    for token in tokens(text):
        if not token:
            continue
        feats[token] = feats.get(token, 0.0) + WORD_WEIGHT
        if len(token) >= 3 and token.isascii():
            padded = f"#{token}#"
            for i in range(len(padded) - 2):
                gram = padded[i : i + 3]
                feats[gram] = feats.get(gram, 0.0) + TRIGRAM_WEIGHT
        elif len(token) >= 2 and not token.isascii():
            # 中文二元组已由 tokens() 提供，这里补单字降权特征
            feats[token] = feats.get(token, 0.0) + 0.2
    return feats


class HashingEmbedder:
    """带符号哈希 + L2 归一化的确定性稀疏向量展开。"""

    name = "hashing"
    model = "hashing-v1"
    kind = "embedding"

    def __init__(self, dim: int = 1024):
        if dim < 8:
            raise ValueError("dim too small")
        self.dim = int(dim)

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feature, weight in _features(text or "").items():
                h = _crc(feature)
                idx = h % self.dim
                sign = 1.0 if (h >> 31) & 1 else -1.0
                # 依特征长度做次线性缩放，避免长文本高频词主导
                vec[idx] += sign * weight * (1.0 + math.log1p(len(feature)) * 0.1)
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([round(v / norm, 6) for v in vec])
        return out

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name="embedding",
            kind="embedding",
            active="hashing",
            available=True,
            detail=f"确定性哈希向量（dim={self.dim}，无需网络与模型下载）",
        )


class OnlineEmbedder:
    """SiliconFlow / OpenAI 兼容的 /embeddings 接口（默认 bge-m3）。"""

    name = "online"
    kind = "embedding"

    def __init__(self, settings: Settings, batch_size: int = 16):
        self.settings = settings
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self.batch_size = max(1, batch_size)
        self._client: Any = None

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # 延迟导入：离线场景不要求安装
        except Exception as exc:  # pragma: no cover - 依赖缺失
            raise UpstreamError("openai SDK 不可用，无法使用在线向量模型") from exc
        self._client = OpenAI(
            api_key=self.settings.embed_api_key,
            base_url=self.settings.embed_base_url,
            timeout=min(30.0, float(self.settings.llm_timeout)),
            max_retries=0,
        )
        return self._client

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._client_or_create()
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + self.batch_size]]
            try:
                response = client.embeddings.create(model=self.model, input=batch)
            except Exception as exc:
                raise UpstreamError(f"在线向量接口失败：{exc}") from exc
            vectors = sorted(response.data, key=lambda d: d.index)
            for item in vectors:
                vec = list(item.embedding)
                if len(vec) != self.dim:
                    # 维度不匹配时做确定性投影，保持下游契约稳定
                    vec = self._project(vec, self.dim)
                norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                out.append([round(v / norm, 6) for v in vec])
        return out

    @staticmethod
    def _project(vec: Sequence[float], dim: int) -> list[float]:
        acc = [0.0] * dim
        for i, value in enumerate(vec):
            acc[i % dim] += value
        return acc

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name="embedding",
            kind="embedding",
            active="online",
            available=True,
            detail=f"{self.model} @ {self.settings.embed_base_url}",
        )


def to_blob(vector: Sequence[float]) -> bytes:
    """float32 小端字节串。"""
    return struct.pack(f"<{len(vector)}f", *[float(v) for v in vector])


def from_blob(blob: bytes, dim: int | None = None) -> list[float]:
    count = len(blob) // 4
    if dim is not None and count != dim:
        raise ValueError(f"vector dim mismatch: {count} != {dim}")
    return list(struct.unpack(f"<{count}f", blob))


def matmul_scores(query: Sequence[float], matrix: Iterable[Sequence[float]]) -> list[float]:
    """行向量点积（向量均已归一化，等价于余弦相似度）。"""
    return [sum(q * v for q, v in zip(query, row)) for row in matrix]


class _Cache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.key: tuple[Any, ...] | None = None
        self.value: Any = None


_CACHE = _Cache()


def _cache_key(settings: Settings) -> tuple[Any, ...]:
    return (
        str(settings.data_dir),
        settings.embed_mode,
        settings.embed_model,
        settings.embed_dim,
        (settings.embed_api_key or "")[-4:],
        settings.embed_base_url,
    )


def get_embedder(settings: Settings | None = None) -> Any:
    """按配置返回向量化 Provider；auto 模式优先在线，探测失败回落哈希。"""
    settings = settings or get_settings()
    key = _cache_key(settings)
    with _CACHE.lock:
        if _CACHE.key == key and _CACHE.value is not None:
            return _CACHE.value
    embedder: Any
    if settings.embed_mode in {"offline", "hashing"} or not settings.has_embed_credentials():
        embedder = HashingEmbedder(settings.embed_dim)
    elif settings.embed_mode == "online":
        embedder = OnlineEmbedder(settings)
    else:  # auto
        online = OnlineEmbedder(settings)
        try:
            online.encode(["warmup"])
            embedder = online
        except Exception as exc:
            log.warning("在线向量模型不可用，回落到确定性哈希向量：%s", exc)
            embedder = HashingEmbedder(settings.embed_dim)
    with _CACHE.lock:
        _CACHE.key = key
        _CACHE.value = embedder
    return embedder


def reset_embedder_cache() -> None:
    with _CACHE.lock:
        _CACHE.key = None
        _CACHE.value = None


def embed_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    provider = get_embedder(settings)
    status = provider.status()
    return {
        "mode": settings.embed_mode,
        "active": status.active,
        "model": getattr(provider, "model", settings.embed_model),
        "dim": getattr(provider, "dim", settings.embed_dim),
        "available": status.available,
        "detail": status.detail,
    }


# 便于类型检查与文档展示
__all__ = [
    "HashingEmbedder",
    "OnlineEmbedder",
    "EmbeddingEncoder",
    "from_blob",
    "get_embedder",
    "matmul_scores",
    "reset_embedder_cache",
    "embed_status",
    "to_blob",
]
