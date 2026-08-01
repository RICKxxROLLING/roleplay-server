from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import settings_store
from ..config import settings
from ..db import get_db
from ..llm import get_client
from ..llm.factory import reset_client, reset_embedder

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    """Reports whether the local inference backend is actually reachable."""
    try:
        models = await get_client().list_models()

        def installed(name: str) -> bool:
            return any(m == name or m.split(":")[0] == name for m in models)

        return {
            "ok": True,
            "backend": settings.backend,
            "model": settings.model,
            "models": models,
            "model_installed": installed(settings.model),
            "retrieval_enabled": settings.retrieval_enabled,
            "embedding_model": settings.embedding_model,
            # Only meaningful when embeddings share the chat host; if they're on
            # a separate server this list doesn't describe it.
            "embedding_model_installed": (
                installed(settings.embedding_model)
                if not settings.embedding_base_url.strip()
                else None
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "backend": settings.backend,
            "base_url": settings.llm_base_url,
            "error": f"{type(exc).__name__}: {exc}",
        }


@router.get("/models")
async def list_models() -> dict:
    client = get_client()
    try:
        models = await client.list_models()
        error = None
    except Exception as exc:
        models, error = [], f"{type(exc).__name__}: {exc}"
    return {
        "models": models,
        "active": settings.model,
        "supports_pull": client.supports_pull,
        "error": error,
    }


class PullIn(BaseModel):
    name: str


@router.post("/models/pull")
async def pull_model(body: PullIn) -> StreamingResponse:
    """Download a model, streaming progress so the UI can show a bar."""
    client = get_client()
    if not client.supports_pull:
        raise HTTPException(
            400,
            "This backend cannot download models. Point it at weights already on disk.",
        )
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Model name required")

    async def gen() -> AsyncIterator[str]:
        try:
            async for evt in client.pull_model(name):
                yield f"data: {json.dumps({'type': 'progress', **evt})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'name': name})}\n\n"
        except Exception as exc:
            payload = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class SettingsPatch(BaseModel):
    model: str | None = None
    backend: str | None = None
    llm_base_url: str | None = None

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    max_new_tokens: int | None = None
    context_tokens: int | None = None

    summary_enabled: bool | None = None
    summary_trigger_tokens: int | None = None
    keep_recent_messages: int | None = None
    summary_max_tokens: int | None = None
    summary_temperature: float | None = None

    retrieval_enabled: bool | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    retrieval_top_k: int | None = None
    retrieval_min_score: float | None = None
    retrieval_budget_tokens: int | None = None
    retrieval_query_messages: int | None = None


def _settings_payload() -> dict:
    data = settings_store.current()
    data["persisted"] = True
    return data


@router.get("/settings")
def get_settings() -> dict:
    return _settings_payload()


@router.patch("/settings")
async def patch_settings(
    body: SettingsPatch, db: DbSession = Depends(get_db)
) -> dict:
    """Saved to the database, so changes survive a restart."""
    changed = settings_store.save(db, body.model_dump(exclude_none=True))

    # The client captures backend/url/model at construction, so it must be
    # rebuilt when any of those change or a model switch would be a no-op.
    if changed & settings_store.CLIENT_KEYS:
        await reset_client()
    if changed & settings_store.EMBEDDER_KEYS:
        await reset_embedder()

    return {**_settings_payload(), "changed": sorted(changed)}
