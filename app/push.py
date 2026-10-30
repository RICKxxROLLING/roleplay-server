"""Push notifications: an empty wake-up through the phone's push relay.

The one runtime call this project makes off the box, and only when the owner
turns notifications on for a device. A sleeping phone can only be woken through
its vendor's relay (Apple, Google, Mozilla), so the push carries NOTHING -- no
name, no text, not even encrypted. The service worker (web/public/sw.js) wakes
and fetches what happened from this server, over the owner's own signed-in
connection. The relay learns that a ping happened and when; nothing more.

An empty push also needs no payload encryption (RFC 8291), which is what keeps
this free of a crypto dependency. What the relays do require is a VAPID
signature (RFC 8292): an ES256 JWT. ES256 is ECDSA over P-256, implemented
below in plain integer arithmetic with RFC 6979 deterministic nonces, and
pinned to that RFC's published test vectors. Speed is irrelevant at a few
pushes a day; correctness is what the vectors are for.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from sqlalchemy.orm import Session as DbSession

from .db.models import AppSetting, Notification, PushSubscription

# --- P-256 (secp256r1) ---------------------------------------------------------

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k: int, point=G):
    result, addend = None, point
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def _int(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _b32(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _rfc6979_k(x: int, h1: bytes) -> int:
    """Deterministic nonce (RFC 6979 3.2, SHA-256): no randomness to get wrong,
    and the published vectors pin the whole signer."""
    hb = _b32(_int(h1) % N)
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + _b32(x) + hb, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + _b32(x) + hb, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        cand = _int(v)
        if 1 <= cand < N:
            return cand
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign(private: int, message: bytes) -> tuple[int, int]:
    h = hashlib.sha256(message).digest()
    z = _int(h)
    while True:
        k = _rfc6979_k(private, h)
        r = _mul(k)[0] % N
        s = pow(k, -1, N) * (z + r * private) % N
        if r and s:
            return r, s


def verify(public: tuple[int, int], message: bytes, r: int, s: int) -> bool:
    if not (1 <= r < N and 1 <= s < N):
        return False
    z = _int(hashlib.sha256(message).digest())
    w = pow(s, -1, N)
    pt = _add(_mul(z * w % N), _mul(r * w % N, public))
    return pt is not None and pt[0] % N == r


def public_key(private: int) -> tuple[int, int]:
    return _mul(private)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def raw_public(pub: tuple[int, int]) -> bytes:
    """Uncompressed point: what `applicationServerKey` and the `k=` header take."""
    return b"\x04" + _b32(pub[0]) + _b32(pub[1])


# --- VAPID keys and JWT ----------------------------------------------------------

VAPID_KEY = "push_vapid_private"
#: Shown to the relays as who to contact about abuse; never used to send mail.
VAPID_SUBJECT = "mailto:owner@roleplay.invalid"


def vapid_private(db: DbSession) -> int:
    """Generated on first use and kept, so subscriptions survive restarts. Not in
    PERSISTABLE, so never returned by the settings API."""
    row = db.get(AppSetting, VAPID_KEY)
    if row is None:
        row = AppSetting(key=VAPID_KEY, value=format(secrets.randbelow(N - 1) + 1, "x"))
        db.add(row)
        db.commit()
    return int(row.value, 16)


def application_server_key(db: DbSession) -> str:
    return _b64url(raw_public(public_key(vapid_private(db))))


def vapid_header(db: DbSession, endpoint: str, now: float | None = None) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}",
              "exp": int((now or time.time()) + 12 * 3600), "sub": VAPID_SUBJECT}
    head = _b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    priv = vapid_private(db)
    r, s = sign(priv, f"{head}.{body}".encode())
    token = f"{head}.{body}.{_b64url(_b32(r) + _b32(s))}"
    return f"vapid t={token}, k={_b64url(raw_public(public_key(priv)))}"


# --- delivery ---------------------------------------------------------------------


def _http():
    """A fresh client per wake-up -- a few a day, so pooling buys nothing.
    A function so tests can replace it; nothing here may reach a real relay."""
    import httpx

    return httpx.AsyncClient(timeout=10)


def notify(db: DbSession, title: str, body: str, session_id: int | None = None) -> Notification:
    """Queue a notification for the devices to fetch. Call `ping_all` to wake them."""
    n = Notification(title=title[:120], body=body[:300], session_id=session_id)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


#: Older than this, a notification is dropped rather than shown. One a device
#: never fetched (Access session expired, phone off) would otherwise pop up days
#: late on the next wake.
PENDING_MAX_AGE_HOURS = 24
#: At most this many per wake; the newest win.
PENDING_MAX = 5


def take_pending(db: DbSession) -> list[Notification]:
    """What a woken device shows; each notification is handed out once."""
    import datetime as dt

    rows = db.query(Notification).filter(Notification.delivered.is_(False)).order_by(Notification.id).all()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=PENDING_MAX_AGE_HOURS)
    fresh = []
    for n in rows:
        n.delivered = True
        made = n.created_at if n.created_at.tzinfo else n.created_at.replace(tzinfo=dt.timezone.utc)
        if made >= cutoff:
            fresh.append(n)
    db.commit()
    return fresh[-PENDING_MAX:]


async def ping(db: DbSession, sub: PushSubscription) -> bool:
    """One empty push. Returns False -- and forgets the device -- when the relay
    says the subscription is gone (404/410: uninstalled, or permission revoked)."""
    headers = {"TTL": "86400", "Urgency": "normal", "Content-Length": "0",
               "Authorization": vapid_header(db, sub.endpoint)}
    async with _http() as c:
        res = await c.post(sub.endpoint, headers=headers, content=b"")
    if res.status_code in (404, 410):
        db.delete(sub)
        db.commit()
        return False
    return res.status_code < 400


async def ping_all(db: DbSession) -> int:
    """Wake every subscribed device. Best-effort: a relay outage never raises."""
    sent = 0
    for sub in db.query(PushSubscription).all():
        try:
            sent += await ping(db, sub)
        except Exception:
            pass
    return sent
