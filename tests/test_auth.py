"""The gate. Most of these are negative tests on purpose -- the value of this
feature is entirely in what it refuses."""
from __future__ import annotations

from app.db import SessionLocal

PW = "correct-horse-battery"


def set_first_password(client, password=PW):
    r = client.post("/api/auth/password", json={"password": password})
    assert r.status_code == 200, r.text
    return r


def test_open_until_a_password_is_set(client):
    """An upgrade must not lock anyone out of their own chats."""
    assert client.get("/api/auth/status").json()["enabled"] is False
    assert client.get("/api/sessions").status_code == 200


def test_chats_are_refused_once_a_password_is_set(client, imported):
    set_first_password(client)
    client.cookies.clear()

    for path in ["/api/sessions", "/api/characters", "/api/settings", "/api/health"]:
        assert client.get(path).status_code == 401, f"{path} answered without a session"


def test_signing_in_restores_access(client):
    set_first_password(client)
    client.cookies.clear()
    assert client.get("/api/sessions").status_code == 401

    assert client.post("/api/auth/login", json={"password": PW}).status_code == 200
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_the_wrong_password_is_refused(client):
    set_first_password(client)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/sessions").status_code == 401


def test_logout_revokes_the_session(client):
    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})
    assert client.get("/api/sessions").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/sessions").status_code == 401


def test_a_forged_cookie_is_refused(client):
    set_first_password(client)
    client.cookies.clear()
    client.cookies.set("rp_session", "not-a-real-token")
    assert client.get("/api/sessions").status_code == 401


def test_the_login_page_can_still_load(client):
    """Static assets stay public or there is nothing to sign in *with*."""
    set_first_password(client)
    client.cookies.clear()
    assert client.get("/api/auth/status").status_code == 200


def test_changing_the_password_needs_the_old_one(client):
    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})

    bad = client.post(
        "/api/auth/password",
        json={"password": "another-long-one", "current_password": "wrong"},
    )
    assert bad.status_code == 403
    # the original still works
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"password": PW}).status_code == 200


def test_setting_a_password_cannot_be_hijacked_without_a_session(client):
    """Otherwise anyone reaching the port could take ownership by resetting it."""
    set_first_password(client)
    client.cookies.clear()
    r = client.post(
        "/api/auth/password",
        json={"password": "attacker-chosen", "current_password": PW},
    )
    assert r.status_code == 401


def test_changing_the_password_logs_every_device_out(client):
    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})

    r = client.post(
        "/api/auth/password",
        json={"password": "a-brand-new-secret", "current_password": PW},
    )
    assert r.status_code == 200
    # the session that made the change is gone too
    assert client.get("/api/sessions").status_code == 401


def test_the_password_is_never_returned_by_the_settings_api(client):
    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})

    body = client.get("/api/settings").text
    assert PW not in body
    assert "auth_password_hash" not in body


def test_the_password_is_stored_hashed(client):
    from app import auth

    set_first_password(client)
    db = SessionLocal()
    stored = auth.get_password_hash(db)
    db.close()
    assert PW not in stored
    assert auth.verify_password(PW, stored)
    assert not auth.verify_password("wrong", stored)


def test_a_short_password_is_rejected(client):
    assert client.post("/api/auth/password", json={"password": "short"}).status_code == 400


def test_clearing_the_password_reopens_the_server(client):
    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})

    r = client.post("/api/auth/password", json={"password": "", "current_password": PW})
    assert r.status_code == 200 and r.json()["enabled"] is False
    client.cookies.clear()
    assert client.get("/api/sessions").status_code == 200


def test_repeated_failures_lock_the_gate(client):
    """A shared password is a small keyspace; an unthrottled endpoint is an oracle."""
    import app.routes.auth as auth_routes

    auth_routes._reset_failures()
    set_first_password(client)
    client.cookies.clear()
    try:
        codes = [
            client.post("/api/auth/login", json={"password": "wrong"}).status_code
            for _ in range(9)
        ]
        assert 429 in codes, f"never locked out: {codes}"
        # and the lockout applies to the correct password too, not just wrong ones
        assert client.post("/api/auth/login", json={"password": PW}).status_code == 429
    finally:
        auth_routes._reset_failures()


def test_expired_sessions_are_refused(client):
    import datetime as dt

    from app.db.models import AuthToken

    set_first_password(client)
    client.cookies.clear()
    client.post("/api/auth/login", json={"password": PW})
    assert client.get("/api/sessions").status_code == 200

    db = SessionLocal()
    for row in db.query(AuthToken).all():
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    db.commit()
    db.close()

    assert client.get("/api/sessions").status_code == 401
