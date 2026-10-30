"""Push: VAPID signing, subscriptions, and the empty-ping delivery path.

No test may reach a real relay -- `push._http` is replaced for every test here.
"""
from __future__ import annotations

import base64
import json

import pytest

from app import push

FCM = "https://fcm.googleapis.com/fcm/send/abc123"


class FakeRelay:
    def __init__(self, status=201, text=""):
        self.status = status
        self.text = text
        self.calls: list[dict] = []

    def __call__(self):
        relay = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, content=None):
                relay.calls.append({"url": url, "headers": headers, "content": content})

                class Res:
                    status_code = relay.status
                    text = relay.text
                return Res()

        return Client()


@pytest.fixture(autouse=True)
def relay(monkeypatch):
    fake = FakeRelay()
    monkeypatch.setattr(push, "_http", fake)
    return fake


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- signing ---------------------------------------------------------------------

def test_ecdsa_matches_rfc6979_p256_sha256_vector():
    x = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    assert push.public_key(x) == (
        0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6,
        0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299,
    )
    r, s = push.sign(x, b"sample")
    assert r == 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
    assert s == 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
    assert push.verify(push.public_key(x), b"sample", r, s)
    assert not push.verify(push.public_key(x), b"sampl3", r, s)


def test_vapid_header_is_a_valid_es256_jwt(client):
    from app.db import SessionLocal

    with SessionLocal() as db:
        header = push.vapid_header(db, FCM, now=1_700_000_000)
        pub = push.public_key(push.vapid_private(db))
    token = header.split("t=")[1].split(",")[0]
    k = header.split("k=")[1]
    head, body, sig = token.split(".")
    assert json.loads(_unb64(head)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(_unb64(body))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] == 1_700_000_000 + 43200
    raw = _unb64(sig)
    assert push.verify(pub, f"{head}.{body}".encode(), int.from_bytes(raw[:32], "big"),
                       int.from_bytes(raw[32:], "big"))
    assert _unb64(k) == push.raw_public(pub)


def test_the_key_is_kept_across_calls(client):
    a = client.get("/api/push/key").json()["key"]
    b = client.get("/api/push/key").json()["key"]
    assert a == b and len(_unb64(a)) == 65 and _unb64(a)[0] == 4


# --- subscriptions -------------------------------------------------------------------

def test_only_browser_relays_are_accepted(client):
    for bad in ("http://fcm.googleapis.com/x", "https://evil.example/x",
                "https://fcm.googleapis.com.evil.example/x", "https://10.2.1.75/x", "ftp://push.apple.com/x"):
        assert client.post("/api/push/subscribe", json={"endpoint": bad}).status_code == 400, bad
    for good in (FCM, "https://updates.push.services.mozilla.com/wpush/v2/x",
                 "https://web.push.apple.com/QGx"):
        assert client.post("/api/push/subscribe", json={"endpoint": good}).status_code == 200, good


def test_devices_list_hides_the_endpoint(client):
    client.post("/api/push/subscribe", json={"endpoint": FCM, "label": "Chrome on Android"})
    devices = client.get("/api/push/devices").json()
    assert devices[0]["label"] == "Chrome on Android" and devices[0]["relay"] == "fcm.googleapis.com"
    assert "abc123" not in json.dumps(devices)


def test_push_routes_are_behind_the_password(client):
    client.post("/api/auth/password", json={"password": "correct-horse-battery"})
    client.cookies.clear()
    for method, path in (("get", "/api/push/key"), ("post", "/api/push/pending"),
                         ("get", "/api/push/devices"), ("post", "/api/push/test")):
        assert getattr(client, method)(path).status_code == 401, path


# --- delivery ----------------------------------------------------------------------

def test_a_ping_is_empty_and_signed(client, relay):
    client.post("/api/push/subscribe", json={"endpoint": FCM})
    assert client.post("/api/push/test").json()["sent"] == 1
    call = relay.calls[0]
    assert call["url"] == FCM and call["content"] == b""
    assert call["headers"]["Authorization"].startswith("vapid t=")
    assert call["headers"]["Content-Length"] == "0"
    # Nothing about the notification travels: no title or body anywhere.
    assert "working" not in json.dumps(call["headers"])


def test_the_device_fetches_the_text_once(client):
    client.post("/api/push/subscribe", json={"endpoint": FCM})
    client.post("/api/push/test")
    got = client.post("/api/push/pending").json()["notifications"]
    assert len(got) == 1 and got[0]["body"].startswith("Notifications are working")
    assert client.post("/api/push/pending").json()["notifications"] == []


def test_notify_carries_the_chat_to_open(client, session):
    from app.db import SessionLocal

    with SessionLocal() as db:
        push.notify(db, "Dina", "You up?", session_id=session)
    got = client.post("/api/push/pending").json()["notifications"]
    assert got[0]["session_id"] == session and got[0]["title"] == "Dina"


def test_a_gone_subscription_is_forgotten(client, relay):
    client.post("/api/push/subscribe", json={"endpoint": FCM})
    relay.status = 410
    assert client.post("/api/push/test").json()["sent"] == 0
    assert client.get("/api/push/devices").json() == []


def test_a_relay_failure_never_raises(client, monkeypatch):
    client.post("/api/push/subscribe", json={"endpoint": FCM})

    def broken():
        raise OSError("relay unreachable")

    monkeypatch.setattr(push, "_http", broken)
    r = client.post("/api/push/test").json()
    assert r["sent"] == 0 and "relay unreachable" in r["devices"][0]["reason"]


# --- serving ---------------------------------------------------------------------

def test_manifest_has_its_media_type():
    import mimetypes

    import app.main  # noqa: F401 -- registers the type

    assert mimetypes.guess_type("manifest.webmanifest")[0] == "application/manifest+json"


def test_deleting_a_chat_drops_its_waiting_notifications(client, session):
    from app.db import SessionLocal

    with SessionLocal() as db:
        push.notify(db, "Dina", "You up?", session_id=session)
    client.delete(f"/api/sessions/{session}")
    assert client.post("/api/push/pending").json()["notifications"] == []


def test_a_test_with_no_devices_leaves_nothing_waiting(client):
    assert client.post("/api/push/test").json() == {"sent": 0}
    assert client.post("/api/push/pending").json()["notifications"] == []


def test_stale_notifications_are_dropped_not_shown(client):
    import datetime as dt

    from app.db import SessionLocal

    with SessionLocal() as db:
        old = push.notify(db, "Dina", "From two days ago")
        old.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
        db.commit()
        push.notify(db, "Dina", "Just now")
    got = client.post("/api/push/pending").json()["notifications"]
    assert [n["body"] for n in got] == ["Just now"]
    assert client.post("/api/push/pending").json()["notifications"] == []


def test_a_wake_shows_at_most_five_newest(client):
    from app.db import SessionLocal

    with SessionLocal() as db:
        for i in range(8):
            push.notify(db, "Dina", f"message {i}")
    got = client.post("/api/push/pending").json()["notifications"]
    assert [n["body"] for n in got] == [f"message {i}" for i in range(3, 8)]


def test_a_refusal_reports_the_relays_reason(client, relay):
    client.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/QGx", "label": "iPhone"})
    relay.status, relay.text = 403, '{"reason":"BadJwtToken"}'
    r = client.post("/api/push/test").json()
    assert r["sent"] == 0
    assert r["devices"] == [{"device": "iPhone", "relay": "web.push.apple.com", "ok": False,
                             "reason": '403 {"reason":"BadJwtToken"}'}]
    assert client.get("/api/push/devices").json()  # a refusal is not a removal


def test_the_contact_is_one_apple_accepts(client):
    """Apple answered 403 BadJwtToken to `mailto:owner@roleplay.invalid`, live;
    example.com and https contacts passed. Reserved non-existent TLDs must not return."""
    from app.config import settings
    from app.db import SessionLocal

    with SessionLocal() as db:
        token = push.vapid_header(db, "https://web.push.apple.com/x").split("t=")[1].split(",")[0]
    sub = json.loads(_unb64(token.split(".")[1]))["sub"]
    assert sub == settings.push_contact
    assert sub.startswith(("mailto:", "https://"))
    assert not sub.rstrip("/").endswith((".invalid", ".test", ".localhost", ".local"))
