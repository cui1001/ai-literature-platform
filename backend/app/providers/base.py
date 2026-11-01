"""Provider 基础类型：统一结果结构与会话协议。

四个能力网关（LLM / Embedding / OCR / arXiv）共享同一套「在线优先、离线兜底、
状态可观测」的模式（见 docs/ARCHITECTURE.md §6）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class LLMResult:
    """任务级生成结果。"""

    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    provider: str = "offline"
    model: str = "deterministic"
    latency_ms: float = 0.0
    degraded: bool = False
    notes: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "data": self.data,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "degraded": self.degraded,
            "notes": self.notes,
        }


@dataclass
class ProviderStatus:
    name: str
    kind: str
    active: str
    available: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "active": self.active,
            "available": self.available,
            "detail": self.detail,
        }


@runtime_checkable
class TextGenerator(Protocol):
    name: str
    model: str

    def run(self, task: str, payload: dict[str, Any], *, expect_json: bool = False) -> LLMResult: ...

    def status(self) -> ProviderStatus: ...


class EmbeddingEncoder(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> Any: ...

    def status(self) -> ProviderStatus: ...
