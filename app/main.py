from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings_store
from .config import settings
from .db import SessionLocal, init_db
from .llm import get_client
from .routes import characters, personas, sessions, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Apply UI-edited settings over the env defaults before anything reads them.
    db = SessionLocal()
    try:
        settings_store.load_into_settings(db)
    finally:
        db.close()
    yield
    await get_client().close()


app = FastAPI(
    title="Local Roleplay Server",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Same-origin in the container, so CORS is off by default. Set RP_CORS_ORIGINS
# only if you serve the UI from somewhere else.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --- API lives under /api so it can never collide with a static asset path ---
API = "/api"
app.include_router(system.router, prefix=API)
app.include_router(characters.router, prefix=API)
app.include_router(personas.router, prefix=API)
app.include_router(sessions.router, prefix=API)


# --- Static UI (built by the Docker web stage into /app/static) ---
_static = settings.static_dir
_has_ui = os.path.isdir(_static) and os.path.isfile(os.path.join(_static, "index.html"))

if _has_ui:
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_static, "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(os.path.join(_static, "index.html"))

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        """Serve real files when they exist, otherwise hand back index.html.

        Registered last, so every API route above wins the match first.
        """
        candidate = os.path.normpath(os.path.join(_static, path))
        # Guard against ../ escaping the static root.
        if candidate.startswith(_static) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_static, "index.html"))

else:

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "service": "local-roleplay-server",
            "ui": "not built - run the Docker image, or `npm run build` in web/",
            "docs": "/api/docs",
        }
