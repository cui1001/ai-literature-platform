"""应用入口：创建 FastAPI 应用，注册所有路由和异常处理器。"""
from fastapi import FastAPI

from app.exceptions import ServiceError
from app.handlers import global_error_handler, service_error_handler
from app.logging_config import setup_logging
from app.routers import chat, rag

# 配置日志（集中管理，见 logging_config.py）
setup_logging()

app = FastAPI()

# 注册异常处理器
app.add_exception_handler(ServiceError, service_error_handler)
app.add_exception_handler(Exception, global_error_handler)

# 注册路由
app.include_router(chat.router)
app.include_router(rag.router)
