"""全局异常处理器：把未捕获的异常转成规范 JSON 返回。"""
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from app.exceptions import ServiceError

logger = logging.getLogger(__name__)

async def service_error_handler(request: Request, exc: ServiceError):
    """处理业务异常：返回 502 + 友好错误信息。"""
    logger.error("请求 %s %s 失败: %s", request.method, request.url.path, exc.message)
    return JSONResponse(
        status_code=502,
        content={"error": exc.message},
    )

async def global_error_handler(request: Request, exc: Exception):
    """兜底处理：任何未预期的异常。"""
    logger.exception("请求 %s %s 发生未预期错误", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误，请稍后重试"},
    )