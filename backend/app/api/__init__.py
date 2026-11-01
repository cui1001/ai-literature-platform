"""HTTP 层：所有路由在此汇总。"""

from fastapi import APIRouter

from . import papers, qa, reading, system

api_router = APIRouter()
api_router.include_router(papers.router)
api_router.include_router(reading.router)
api_router.include_router(qa.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
