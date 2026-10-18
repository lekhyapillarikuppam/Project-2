"""FastAPI entry point: routes, error handlers, and the job worker / cleanup threads (SPEC §5)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.ratelimit import UploadRateLimiter
from app.api.routes import FullBookBuilder, router
from app.config import Settings, get_settings
from app.dialogue.llm import client_from_settings
from app.jobs import CleanupThread, EngineFactory, JobRegistry, JobWorker, wipe_jobs_folder
from app.jobs.worker import LLMFactory
from app.tts.engines import build_engine

logger = logging.getLogger("audiobook")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    jobs_dir = settings.storage_path / "jobs"
    wipe_jobs_folder(jobs_dir)  # jobs are not restored after a restart (D18)
    app.state.registry.clear()
    worker = JobWorker(app.state.registry, settings, app.state.engine_factory, app.state.llm_factory)
    cleanup = CleanupThread(app.state.registry, settings)
    app.state.worker = worker
    worker.start()
    cleanup.start()
    logger.info("Storage at %s; TTS fallback: %s", settings.storage_path, settings.tts_fallback)
    try:
        yield
    finally:
        cleanup.stop()
        worker.stop()


def create_app(
    settings: Settings | None = None,
    engine_factory: EngineFactory | None = None,
    llm_factory: LLMFactory | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, a fake engine factory and a fake LLM factory."""
    settings = settings or get_settings()
    app = FastAPI(title="Audiobook Generator API", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine_factory = engine_factory or (lambda s: build_engine(s))
    app.state.llm_factory = llm_factory or client_from_settings
    app.state.registry = JobRegistry(settings.max_queued_jobs)
    app.state.full_books = FullBookBuilder()
    app.state.upload_limiter = UploadRateLimiter(settings.uploads_per_ip_per_hour)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges", "Content-Length", "Retry-After"],
    )
    install_error_handlers(app)
    app.include_router(router)
    return app


_settings = get_settings()
logging.basicConfig(level=_settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app(_settings)
