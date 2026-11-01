"""全局配置：环境变量 → 不可变 Settings 对象。

设计要点
--------
1. 只在 ``get_settings()`` 中读取环境变量，业务代码不得直接 ``os.environ``；
2. 支持通过 ``get_settings(data_dir=...)`` 为测试/评测指定隔离数据目录；
3. 未安装 python-dotenv 时静默跳过，保证最小依赖下可运行。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> <repo>
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"

_TRUTHY = {"1", "true", "yes", "on"}


def _load_dotenv() -> None:
    env_file = BACKEND_ROOT / ".env"
    if not env_file.exists():
        return
    try:  # 优先用 python-dotenv，缺失时退化为极简解析
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_file, override=False)
    except Exception:  # pragma: no cover - 可选依赖
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key, default)
    return value.strip() if isinstance(value, str) else value


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key, "1" if default else "0").lower()
    return raw in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """运行期配置快照。"""

    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: BACKEND_ROOT / "data")
    static_dir: Path = field(default_factory=lambda: REPO_ROOT / "frontend")
    repo_root: Path = field(default_factory=lambda: REPO_ROOT)

    # 大模型通道
    llm_mode: str = "auto"  # auto | online | offline
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 60.0
    llm_probe_seconds: float = 8.0

    # 向量通道
    embed_mode: str = "auto"
    embed_api_key: str = ""
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024

    # arXiv
    arxiv_mode: str = "auto"
    arxiv_max_results: int = 20
    tracker_interval_minutes: int = 360

    # 解析
    page_render_dpi: int = 150
    ocr_text_threshold: int = 40
    max_upload_mb: int = 130

    # 检索
    top_k: int = 6
    rrf_k: int = 60

    # 确定性
    layout_seed: int = 20260101

    version: str = "2.0.0"

    # ---------------- 派生路径 ----------------
    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def pages_dir(self) -> Path:
        return self.data_dir / "pages"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def figures_dir(self) -> Path:
        return self.data_dir / "figures"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.papers_dir,
            self.pages_dir,
            self.parsed_dir,
            self.figures_dir,
            self.artifacts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def has_llm_credentials(self) -> bool:
        return bool(self.llm_api_key)

    def has_embed_credentials(self) -> bool:
        return bool(self.embed_api_key)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        _load_dotenv()
        default_data = BACKEND_ROOT / "data"
        data_dir = Path(_env("DATA_DIR") or default_data)
        if not data_dir.is_absolute():
            data_dir = (BACKEND_ROOT / data_dir).resolve()
        settings = cls(
            env=_env("APP_ENV", "dev"),
            host=_env("APP_HOST", "127.0.0.1"),
            port=_env_int("APP_PORT", 8000),
            data_dir=data_dir,
            llm_mode=_env("LLM_MODE", "auto").lower(),
            llm_api_key=_env("DEEPSEEK_API_KEY"),
            llm_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            llm_model=_env("LLM_MODEL", "deepseek-chat"),
            llm_timeout=_env_float("LLM_TIMEOUT", 60.0),
            embed_mode=_env("EMBED_MODE", "auto").lower(),
            embed_api_key=_env("SILICONFLOW_API_KEY"),
            embed_base_url=_env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/"),
            embed_model=_env("EMBED_MODEL", "BAAI/bge-m3"),
            embed_dim=_env_int("EMBED_DIM", 1024),
            arxiv_mode=_env("ARXIV_MODE", "auto").lower(),
            arxiv_max_results=_env_int("ARXIV_MAX_RESULTS", 20),
            tracker_interval_minutes=_env_int("TRACKER_INTERVAL_MINUTES", 360),
            page_render_dpi=_env_int("PAGE_RENDER_DPI", 150),
            ocr_text_threshold=_env_int("OCR_TEXT_THRESHOLD", 40),
            top_k=_env_int("TOP_K", 6),
            rrf_k=_env_int("RRF_K", 60),
            layout_seed=_env_int("LAYOUT_SEED", 20260101),
        )
        if overrides:
            settings = cls(**{**settings.__dict__, **{k: _coerce_overrides(k, v) for k, v in overrides.items()}})
        settings.ensure_dirs()
        return settings


_INT_OVERRIDES = {
    "port",
    "embed_dim",
    "arxiv_max_results",
    "tracker_interval_minutes",
    "page_render_dpi",
    "ocr_text_threshold",
    "top_k",
    "rrf_k",
    "layout_seed",
}
_FLOAT_OVERRIDES = {"llm_timeout", "llm_probe_seconds"}
_MODE_OVERRIDES = {"llm_mode", "embed_mode", "arxiv_mode"}


def _coerce_overrides(name: str, value: Any) -> Any:
    """把环境变量/测试传入的字符串转换成字段声明的类型（from_env 覆盖入口）。"""
    if name == "data_dir":
        path = Path(str(value))
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()
    if value is None:
        return value
    if name in _INT_OVERRIDES:
        return int(value)
    if name in _FLOAT_OVERRIDES:
        return float(value)
    if name in _MODE_OVERRIDES:
        return str(value).lower()
    if name in {"max_upload_mb"}:
        return float(value)
    return value


#: 进程级配置覆盖（测试/多库演示用），由 ``reset_settings`` 写入
_OVERRIDES: dict[str, object] = {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if _OVERRIDES:
        return Settings.from_env(**_OVERRIDES)  # type: ignore[arg-type]
    return Settings.from_env()


def reset_settings(**overrides) -> Settings:
    """切换全局配置（测试/多库演示），保证 repo 与调用方指向同一个数据库。"""
    global _OVERRIDES
    _OVERRIDES = dict(overrides)
    get_settings.cache_clear()
    return get_settings()


def has_llm_credentials(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.llm_api_key)


def has_embed_credentials(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.embed_api_key)
