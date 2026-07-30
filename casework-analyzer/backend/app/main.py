"""FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analyze, export, history, meta, scoring, upload
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("casework_analyzer")

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(scoring.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ok"}
