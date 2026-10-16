"""FastAPI entry point. Phase 0: health and config endpoints only."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("audiobook")

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_path.joinpath("jobs").mkdir(parents=True, exist_ok=True)
    logger.info("Storage at %s", settings.storage_path)
    yield


app = FastAPI(title="Audiobook Generator API", version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": settings.version}


@app.get("/api/config")
def public_config() -> dict:
    return {
        "max_file_mb": settings.max_file_mb,
        "max_pdf_pages": settings.max_pdf_pages,
        "max_words": settings.max_words,
        "accepted_types": [".pdf", ".docx"],
        "retention_hours": settings.retention_hours,
    }
