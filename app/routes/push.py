"""Notification devices, and the pending queue a woken device reads (app/push.py)."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import push
from ..db import get_db
from ..db.models import PushSubscription

router = APIRouter(prefix="/push", tags=["push"])

#: The browser vendors' relays. A subscription is a URL the server will POST
#: to, so anything else is refused -- it would make the server a request
#: forwarder to whatever address a subscription named.
RELAY_HOSTS = (
    "fcm.googleapis.com",          # Chrome, Edge, Android
    "push.services.mozilla.com",   # Firefox
    "push.apple.com",              # Safari, iOS home-screen apps
    "notify.windows.com",          # legacy Edge / Windows
)


def _relay_ok(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in RELAY_HOSTS)


class SubscribeIn(BaseModel):
    endpoint: str
    #: What the device is, for the list in Settings ("iPhone", "Firefox on Linux").
    label: str = ""


class EndpointIn(BaseModel):
    endpoint: str


@router.get("/key")
def key(db: DbSession = Depends(get_db)) -> dict:
    return {"key": push.application_server_key(db)}


@router.post("/subscribe")
def subscribe(body: SubscribeIn, db: DbSession = Depends(get_db)) -> dict:
    if not _relay_ok(body.endpoint):
        raise HTTPException(400, "That isn't a browser push service this server will send to.")
    sub = db.get(PushSubscription, body.endpoint)
    if sub is None:
        db.add(PushSubscription(endpoint=body.endpoint, label=body.label[:200]))
    else:
        sub.label = body.label[:200] or sub.label
    db.commit()
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(body: EndpointIn, db: DbSession = Depends(get_db)) -> dict:
    sub = db.get(PushSubscription, body.endpoint)
    if sub is not None:
        db.delete(sub)
        db.commit()
    return {"ok": True}


@router.get("/devices")
def devices(db: DbSession = Depends(get_db)) -> list[dict]:
    # The endpoint is a capability URL; the list shows labels and dates only.
    return [{"label": s.label or "Unnamed device", "since": s.created_at.isoformat(),
             "relay": urlsplit(s.endpoint).hostname}
            for s in db.query(PushSubscription).order_by(PushSubscription.created_at)]


@router.post("/test")
async def test(db: DbSession = Depends(get_db)) -> dict:
    # Nothing to wake means nothing should wait to be shown later.
    if not db.query(PushSubscription).count():
        return {"sent": 0}
    push.notify(db, "Roleplay", "Notifications are working on this device.")
    # Per device, with the relay's own words when it refuses: "Apple: 403
    # BadJwtToken" is a diagnosis, "nothing arrived" is not.
    report = await push.ping_report(db)
    return {"sent": sum(r["ok"] for r in report), "devices": report}


@router.post("/pending")
def pending(db: DbSession = Depends(get_db)) -> dict:
    """What a device woken by an empty push shows. POST because reading spends them."""
    return {"notifications": [
        {"id": n.id, "title": n.title, "body": n.body, "session_id": n.session_id}
        for n in push.take_pending(db)
    ]}
