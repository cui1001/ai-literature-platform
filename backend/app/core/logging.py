"""结构化日志：控制台 + 可选文件，带 request_id 与模块标签。"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

_CONFIGURED = False
_LOGGER_NAME = "agentlit"


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.tag = getattr(record, "tag", "-")
        record.rid = getattr(record, "request_id", "-")[:8]
        return super().format(record)


def setup_logging(level: str | None = None, *, log_file: str | None = None) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return logger
    logger.setLevel((level or os.environ.get("LOG_LEVEL", "INFO")).upper())
    logger.propagate = False
    fmt = _Formatter("%(asctime)s %(levelname)-7s [%(rid)s] %(name)s:%(tag)s %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    _CONFIGURED = True
    return logger


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


@contextmanager
def log_span(logger: logging.Logger, message: str, **fields) -> Iterator[dict]:
    """记录耗时与异常；生成器 yield 的字典会并入日志。"""
    extra = {"tag": fields.pop("tag", "-"), **fields}
    start = time.perf_counter()
    try:
        yield extra
    except Exception as exc:  # noqa: BLE001 - 记录后原样抛出
        extra["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("%s | %s", message, extra)
        raise
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        extra["ms"] = round(elapsed, 1)
        logger.info("%s | %s", message, extra)


def new_request_id() -> str:
    return uuid.uuid4().hex
