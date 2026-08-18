"""通用工具：错误处理装饰器。"""
import functools
import logging

from app.exceptions import ServiceError

logger = logging.getLogger(__name__)


def handle_errors(service_name: str):
    """装饰器：捕获异常，记录日志，抛出业务异常。

    用法：在需要错误处理的函数上标注 @handle_errors("服务名")，
    替代重复的 try/except 样板代码。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ServiceError:
                # 已是业务异常，直接向上抛，不重复包装
                raise
            except Exception as e:
                logger.error("%s 调用失败: %s", service_name, e)
                raise ServiceError(f"{service_name}调用失败，请稍后重试") from e
        return wrapper
    return decorator
