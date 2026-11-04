from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..chat import voices

router = APIRouter(prefix="/voices", tags=["voices"])


@router.get("")
def list_voices() -> dict:
    return {
        "available": voices.is_installed(),
        "voices": [{"id": v.id, "label": v.label, "gender": v.gender, "accent": v.accent,
                    "downloaded": voices.downloaded(v.id)} for v in voices.VOICES.values()],
    }


@router.get("/{voice_id}/preview")
async def preview(voice_id: str) -> FileResponse:
    """A sample line. The first time, this is what downloads the voice."""
    if voice_id not in voices.VOICES:
        raise HTTPException(404, "No such voice")
    try:
        path = await voices.render([(voice_id, voices.SAMPLE)])
    except voices.VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return FileResponse(path, media_type="audio/wav")
