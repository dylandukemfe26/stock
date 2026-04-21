"""FastAPI app entry.

Run:
    uvicorn backend.main:app --reload

Health check:  GET /health
API docs:      GET /docs
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router

app = FastAPI(title="Day Trading Analysis System", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# Serve the minimal frontend if present.
_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(_frontend_dir)),
        name="static",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(_frontend_dir / "index.html"))
