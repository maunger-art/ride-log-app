from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from api import app as api_app

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

app = FastAPI(title="Ride Log")

app.mount("/api", api_app)


@app.get("/{full_path:path}")
def serve_react_app(full_path: str) -> FileResponse:
    if not FRONTEND_DIST.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")

    requested = (FRONTEND_DIST / full_path).resolve()
    if requested.is_file() and FRONTEND_DIST in requested.parents:
        return FileResponse(requested)

    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)

    raise HTTPException(status_code=404, detail="Frontend entrypoint not found")
