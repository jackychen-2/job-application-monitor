"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from job_monitor.api.applications import router as applications_router
from job_monitor.api.emails import router as emails_router
from job_monitor.api.scan import router as scan_router
from job_monitor.api.stats import router as stats_router
from job_monitor.eval.api import router as eval_router
from job_monitor.config import AppConfig, get_config
from job_monitor.database import init_db
from job_monitor.logging_config import setup_logging

logger = structlog.get_logger(__name__)

# Module-level config cache
_config: AppConfig | None = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = get_config()
    return _config


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    config = _get_config()
    setup_logging(level=config.log_level, log_file=config.log_file)
    init_db(config)
    logger.info(
        "server_starting",
        host=config.host,
        port=config.port,
        llm_enabled=config.llm_enabled,
        llm_provider=config.llm_provider if config.llm_enabled else "disabled",
    )
    yield
    logger.info("server_shutting_down")


def create_app() -> FastAPI:
    """Application factory — create and configure the FastAPI app."""
    config = _get_config()

    app = FastAPI(
        title="Job Application Monitor",
        description="Track job applications from email with LLM-powered extraction",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(applications_router)
    app.include_router(emails_router)
    app.include_router(scan_router)
    app.include_router(stats_router)
    app.include_router(eval_router)

    @app.get("/api/health", tags=["health"])
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # Serve built React frontend (if available)
    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        # Serve static assets (JS, CSS, images)
        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

        # Serve index.html for all non-API routes (SPA routing)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            file_path = frontend_dist / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(frontend_dist / "index.html"))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = _get_config()
    uvicorn.run(
        "job_monitor.main:app",
        host=config.host,
        port=config.port,
        reload=True,
    )
