"""科研助手 Agent —— FastAPI 应用入口。

启动：``python -m app.main`` 或 ``uvicorn app.main:app --port 8000``。
前端为原生 ES Module SPA，构建产物无需打包，由本服务直接静态托管。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .core.config import Settings, get_settings
from .core.errors import AppError
from .core.logging import get_logger, setup_logging
from .db.database import close_all, get_connection

log = get_logger("main")


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        s.ensure_dirs()
        get_connection(s)
        log.info("agentlit %s ready | data=%s", s.version, s.data_dir)
        try:
            yield
        finally:
            close_all()

    app = FastAPI(
        title="科研助手 Agent",
        description="全栈式科研助手：文献解析、抗幻觉问答、版式阅读、arXiv 追踪、引用图谱与写作 Copilot",
        version=s.version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": str(exc)}})

    app.include_router(api_router)

    if s.static_dir.exists():
        app.mount("/", StaticFiles(directory=str(s.static_dir), html=True), name="ui")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
