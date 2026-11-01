"""大模型网关：在线（OpenAI 兼容协议）与离线（确定性引擎）双通道。

调用方只使用 ``llm.run(task, payload)``，任务定义集中在 ``TASK_SPECS``，
因此新增能力只需增加一个任务，而不是在每个模块里拼 prompt。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..core.config import Settings, get_settings, has_llm_credentials
from ..core.errors import ProviderUnavailable
from ..core.logging import get_logger
from .base import LLMResult, ProviderStatus
from .offline import run_offline

log = get_logger("provider.llm")

#: 任务 → (system prompt, 期望输出说明)
TASK_SPECS: dict[str, dict[str, str]] = {
    "answer_question": {
        "system": (
            "你是严谨的学术助理。只能依据用户提供的证据块回答，禁止引入证据之外的事实。"
            "每个结论句末尾用 [编号] 标注证据来源。证据不足时明确回答「未找到直接证据」。"
            "保留原文中的数字、符号、专有名词，不得改写数值。输出中文（专有名词保留英文）。"
        ),
        "output": "自然语言答案，逐句带 [证据编号]",
    },
    "compare_papers": {
        "system": (
            "你是学术对比分析助手。给定维度与各论文证据，输出 JSON："
            '{"dimensions":[{"dimension":"...","rows":[{"paper_id":"...","value":"...","evidence":[1,2]}],'
            '"conclusion":"..."}]}。缺失证据时 value 写 "未提及"，禁止猜测。'
        ),
        "output": "JSON",
    },
    "summarize": {
        "system": "把给定段落压缩为 1~3 句学术摘要，只保留原文事实，不添加新信息。输出中文。",
        "output": "纯文本",
    },
    "translate": {
        "system": (
            "将英文学术文本翻译为通顺中文。公式、代码、变量名、引用标记（如 [11]）保持原样；"
            "术语使用学界通行译法。只输出译文。"
        ),
        "output": "纯文本",
    },
    "academic_translate": {
        "system": (
            "将中文学术文本翻译为符合国际期刊规范的英文：被动语态适度、术语准确、"
            "避免中式表达。只输出英文译文。"
        ),
        "output": "纯文本",
    },
    "outline": {
        "system": (
            "你是论文写作顾问。基于给定 idea 与本地文献证据，生成写作框架 JSON："
            '{"title":"...","abstract":"...","introduction":["..."],"related_work":["..."],'
            '"method_sketch":["..."],"experiments":["..."],"conclusion":["..."],'
            '"citations":{"1":"paper_id"}}。每条论断必须能对应到提供的文献，禁止虚构引用。'
        ),
        "output": "JSON",
    },
    "caption": {
        "system": (
            "根据图表的真实统计量撰写 1~2 句英文学术 Figure Caption，"
            "包含变量含义、趋势与关键数值；不得编造未提供的数值。"
        ),
        "output": "纯文本",
    },
    "survey_section": {
        "system": (
            "基于给定论文群（含标题、摘要、年份、引用关系）撰写一段领域综述，"
            "按时间线梳理脉络，每个论断后用 [paper_id] 标注。输出中文。"
        ),
        "output": "纯文本（markdown）",
    },
    "verify_claim": {
        "system": (
            "判断 claim 能否由证据支持。输出 JSON："
            '{"verdict":"supported|contradicted|unsupported","evidence":[1,2],"reason":"..."}。'
            "数字、专名必须逐字一致才算支持。"
        ),
        "output": "JSON",
    },
    "translation_glossary": {
        "system": "抽取文本中的学术术语并给出中文译名，输出 JSON：{\"terms\":[{\"en\":\"...\",\"zh\":\"...\"}]}。",
        "output": "JSON",
    },
}


class OnlineLLM:
    """OpenAI 兼容协议（DeepSeek / OpenAI / 自建网关）。"""

    name = "online"
    degraded = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        self._client = None

    # ---------------- 底层 ----------------
    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.settings.llm_api_key:
            raise ProviderUnavailable("未配置 DEEPSEEK_API_KEY")
        try:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                timeout=self.settings.llm_timeout,
                max_retries=0,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"openai 客户端初始化失败: {exc}") from exc
        return self._client

    def _chat(self, system: str, user: str, *, expect_json: bool) -> str:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": 1600,
        }
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    # ---------------- 对外 ----------------
    def run(self, task: str, payload: dict[str, Any], *, expect_json: bool = False) -> LLMResult:
        spec = TASK_SPECS.get(task)
        started = time.perf_counter()
        if not spec:
            raise ProviderUnavailable(f"未定义任务: {task}")
        user = json.dumps(payload, ensure_ascii=False, indent=1)
        try:
            text = self._chat(spec["system"], user, expect_json=expect_json)
            data: dict[str, Any] = {}
            if expect_json:
                data = _parse_json(text)
            return LLMResult(
                text=text,
                data=data,
                provider=self.name,
                model=self.model,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - 网络类异常统一降级
            log.warning("online llm failed | %s: %s", type(exc).__name__, exc)
            raise ProviderUnavailable(f"在线模型调用失败: {exc}") from exc

    def status(self) -> ProviderStatus:
        reachable = self._probe()
        return ProviderStatus(
            name="llm",
            kind="text-generation",
            active="online" if reachable else "offline",
            available=reachable,
            detail=f"model={self.model} base={self.settings.llm_base_url}",
        )

    def _probe(self) -> bool:
        if not has_llm_credentials(self.settings):
            return False
        url = f"{self.settings.llm_base_url}/models"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.settings.llm_api_key}"})
        try:
            with urllib.request.urlopen(request, timeout=min(4.0, self.settings.llm_probe_seconds)) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text
        text = text.removeprefix("json").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"value": value}
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except ValueError:
                return {}
        return {}


_CACHE: dict[tuple, Any] = {}


def get_llm(settings: Settings | None = None) -> Any:
    """按配置返回 LLM 实例；在线不可用时透明降级（auto 模式）。"""
    s = settings or get_settings()
    key = (str(s.data_dir), s.llm_mode, s.llm_api_key[-4:], s.llm_model)
    if key in _CACHE:
        return _CACHE[key]

    from .offline import OfflineLLM

    instance: Any
    if s.llm_mode == "offline" or not has_llm_credentials(s):
        instance = OfflineLLM(s, reason="offline-mode" if s.llm_mode == "offline" else "no-credentials")
    elif s.llm_mode == "online":
        instance = OnlineLLM(s)
    else:  # auto
        online = OnlineLLM(s)
        instance = online if online._probe() else OfflineLLM(s, reason="unreachable")
    _CACHE[key] = instance
    return instance


def reset_llm_cache() -> None:
    _CACHE.clear()


def llm_status(settings: Settings | None = None) -> ProviderStatus:
    s = settings or get_settings()
    return get_llm(s).status()


def run_task(task: str, payload: dict[str, Any], *, expect_json: bool = False) -> LLMResult:
    """带降级的任务执行：在线失败自动回落离线引擎，并标记 degraded。"""
    s = get_settings()
    llm = get_llm(s)
    started = time.perf_counter()
    try:
        return llm.run(task, payload, expect_json=expect_json)
    except ProviderUnavailable as exc:
        result = run_offline(task, payload, expect_json=expect_json)
        result.degraded = True
        result.notes = (result.notes + " | " if result.notes else "") + f"在线失败降级: {exc}"
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
