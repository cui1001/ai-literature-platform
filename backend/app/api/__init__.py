"""HTTP 层：所有路由在此汇总。

各业务路由采用「延迟导入」方式引用服务实现：即使某个可选模块
（discovery / graph / writing / dataviz）尚未安装或初始化失败，
其余接口仍能正常提供，便于分阶段集成与演示降级。
"""

from fastapi import APIRouter

from . import dataviz, discovery, graph, papers, qa, reading, system, writing

api_router = APIRouter()
api_router.include_router(papers.router)
api_router.include_router(reading.router)
api_router.include_router(qa.router)
api_router.include_router(discovery.router)
api_router.include_router(graph.router)
api_router.include_router(writing.router)
api_router.include_router(dataviz.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
