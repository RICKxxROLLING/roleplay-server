from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import settings_store
from .config import settings
from .db import SessionLocal, init_db
from .llm import get_client
from . import auth
from .routes import auth as auth_routes
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


API_PREFIX = "/api"

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

# --- Authentication -------------------------------------------------------
# A middleware rather than a per-route dependency: the guarantee wanted here is
# "nothing under /api answers without a session", and that is only true if it
# cannot be forgotten on a new route. Static files stay public because the
# login screen has to load from somewhere; they contain no chat data.
_OPEN_PATHS = (f"{API_PREFIX}/auth/status", f"{API_PREFIX}/auth/login",
               f"{API_PREFIX}/auth/logout", f"{API_PREFIX}/auth/password")


@app.middleware("http")
async def require_session(request: Request, call_next):
    path = request.url.path
    if not path.startswith(API_PREFIX) or path in _OPEN_PATHS:
        return await call_next(request)

    db = SessionLocal()
    try:
        if not auth.is_enabled(db):
            return await call_next(request)
        if auth.valid_token(db, request.cookies.get(auth.COOKIE)):
            return await call_next(request)
    finally:
        db.close()

    return JSONResponse({"detail": "Sign in to continue."}, status_code=401)


# --- API lives under /api so it can never collide with a static asset path ---
API = "/api"
app.include_router(auth_routes.router, prefix=API)
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
