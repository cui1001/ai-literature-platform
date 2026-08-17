"""应用入口：创建 FastAPI 应用，注册所有路由。"""
from fastapi import FastAPI
from app.routers import chat, rag

app = FastAPI()

# 注册路由（把各模块的 router 挂到 app 上）
app.include_router(chat.router)
app.include_router(rag.router)