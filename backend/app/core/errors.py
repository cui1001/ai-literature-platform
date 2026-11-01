"""统一异常体系。

API 层据此把内部错误翻译为稳定 JSON：``{"error": {"code", "message", "detail", "trace_id"}}``。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期业务异常的基类。"""

    code = "internal_error"
    status = 500
    message = "服务内部错误"

    def __init__(self, message: str = "", *, detail: Any = None, status: int | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.detail = detail
        self.status_code = status or self.status

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class ValidationError(AppError):
    code = "invalid_request"
    status = 400
    message = "请求参数不合法"


class NotFoundError(AppError):
    code = "not_found"
    status = 404
    message = "资源不存在"


class ConflictError(AppError):
    code = "conflict"
    status = 409
    message = "资源已存在或状态冲突"


class ParseError(AppError):
    code = "parse_failed"
    status = 422
    message = "文档解析失败"


class UnsupportedFileError(AppError):
    code = "unsupported_file"
    status = 415
    message = "不支持的文件类型"


class UpstreamError(AppError):
    code = "upstream_error"
    status = 502
    message = "上游服务不可用"


class ProviderUnavailable(AppError):
    """外部能力（LLM/向量/arXiv）不可用；调用方应据此降级。"""

    code = "provider_unavailable"
    status = 503
    message = "外部能力不可用"


class BudgetExceeded(AppError):
    code = "budget_exceeded"
    status = 429
    message = "资源预算超限"
