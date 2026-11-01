"""写作模块的 LLM 运行时：在线优先、离线兜底。

语义与 :func:`app.providers.llm.run_task` 完全一致，区别只在于允许显式传入
``Settings``（测试 / 多库演示时不必依赖进程级单例）。
"""

from __future__ import annotations

from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import ProviderUnavailable
from ..core.logging import get_logger
from ..providers.base import LLMResult
from ..providers.llm import get_llm
from ..providers.offline import run_offline

log = get_logger(__name__)


def run_task_with(
    task: str,
    payload: dict[str, Any],
    settings: Settings | None = None,
    *,
    expect_json: bool = False,
) -> LLMResult:
    """执行一个生成任务；在线失败时自动回落离线引擎并标记 ``degraded``。"""
    resolved = settings or get_settings()
    llm = get_llm(resolved)
    try:
        return llm.run(task, payload, expect_json=expect_json)
    except ProviderUnavailable as exc:
        log.warning("在线 %s 不可用，降级离线引擎: %s", task, exc)
        result = run_offline(task, payload, expect_json=expect_json)
        result.degraded = True
        result.notes = (result.notes + " | " if result.notes else "") + f"在线失败降级: {exc}"
        return result
