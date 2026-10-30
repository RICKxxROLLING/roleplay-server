"""Public-access hardening: per-client throttling, two-factor, secure cookies.

Mostly negative tests, like test_auth.py -- the value is in what is refused.
"""
from __future__ import annotations

import base64
import time

from app import auth
from app.db import SessionLocal

PW = "correct-horse-battery"


def password(client):
    assert client.post("/api/auth/password", json={"password": PW}).status_code == 200
    client.cookies.clear()


def login(client, pw=PW, code="", headers=None):
    return client.post("/api/auth/login", json={"password": pw, "code": code}, headers=headers or {})


def now_code(secret, offset=0):
    return auth.totp_code(secret, int(time.time() // 30) + offset)


def turn_on_totp(client):
    """Password set, signed in, two-factor on. Returns (secret, recovery codes)."""
    password(client)
    assert login(client).status_code == 200
    r = client.post("/api/auth/totp/setup", json={"current_password": PW})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["uri"].startswith("otpauth://totp/Roleplay:")
    r = client.post("/api/auth/totp/enable", json={"code": now_code(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


# --- throttling -----------------------------------------------------------------

CF = lambda ip: {"cf-connecting-ip": ip}  # noqa: E731


def test_one_client_failing_does_not_lock_out_another(client):
    password(client)
    for _ in range(5):
        login(client, "wrong", headers=CF("203.0.113.9"))
    assert login(client, headers=CF("203.0.113.9")).status_code == 429
    # The owner, elsewhere, still gets in.
    assert login(client, headers=CF("198.51.100.4")).status_code == 200


def test_lockouts_grow_for_a_repeat_offender():
    from app.routes import auth as r

    class Req:
        headers = {"cf-connecting-ip": "203.0.113.9"}
        client = None

    for _ in range(5):
        r._record_failure(Req)
    first = r._locked(Req)
    r._clients["203.0.113.9"]["until"] = 0  # the first lock runs out
    for _ in range(5):
        r._record_failure(Req)
    assert 50 < first <= 61 and r._locked(Req) > 100


def test_many_remote_addresses_hit_the_remote_ceiling(client):
    password(client)
    for i in range(30):
        login(client, "wrong", headers=CF(f"203.0.113.{i}"))
    assert login(client, headers=CF("198.51.100.4")).status_code == 429


def test_the_remote_ceiling_never_locks_out_the_lan():
    from app.routes import auth as r

    class Remote:
        headers = {"cf-connecting-ip": "203.0.113.1"}
        client = None

    class Lan:
        headers = {}

        class client:
            host = "192.168.1.20"

    r._remote_locked_until = time.monotonic() + 900
    assert r._locked(Remote) > 0 and r._locked(Lan) == 0


def test_signing_in_clears_that_clients_record(client):
    password(client)
    for _ in range(4):
        login(client, "wrong", headers=CF("203.0.113.9"))
    assert login(client, headers=CF("203.0.113.9")).status_code == 200
    for _ in range(4):
        login(client, "wrong", headers=CF("203.0.113.9"))
    assert login(client, headers=CF("203.0.113.9")).status_code == 200  # not locked yet


# --- two-factor -------------------------------------------------------------------

def test_totp_matches_rfc6238():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"
    assert auth.totp_code(secret, 1111111109 // 30) == "081804"


def test_with_two_factor_the_password_alone_is_refused(client):
    secret, _ = turn_on_totp(client)
    client.cookies.clear()
    assert client.get("/api/auth/status").json()["totp"] is True
    assert login(client).status_code == 401
    assert login(client, code="000000").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    assert login(client, code=now_code(secret, 1)).status_code == 200
    assert client.get("/api/sessions").status_code == 200


def test_wrong_password_with_right_code_says_nothing_specific(client):
    secret, _ = turn_on_totp(client)
    client.cookies.clear()
    r = login(client, "wrong-password", code=now_code(secret))
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect password or code."


def test_a_code_cannot_be_replayed(client):
    secret, _ = turn_on_totp(client)
    client.cookies.clear()
    code = now_code(secret, 1)
    assert login(client, code=code).status_code == 200
    client.cookies.clear()
    assert login(client, code=code).status_code == 401


def test_a_recovery_code_works_once(client):
    _, recovery = turn_on_totp(client)
    assert len(recovery) == 8
    client.cookies.clear()
    assert login(client, code=recovery[0].upper()).status_code == 200
    assert client.get("/api/auth/status").json()["recovery_left"] == 7
    client.cookies.clear()
    assert login(client, code=recovery[0]).status_code == 401


def test_an_unconfirmed_setup_is_not_enforced(client):
    password(client)
    login(client)
    client.post("/api/auth/totp/setup", json={"current_password": PW})
    client.cookies.clear()
    assert login(client).status_code == 200  # never confirmed, so not required


def test_setup_needs_the_password(client):
    password(client)
    login(client)
    assert client.post("/api/auth/totp/setup", json={"current_password": "nope"}).status_code == 403


def test_two_factor_routes_need_a_session(client):
    password(client)
    assert client.post("/api/auth/totp/setup", json={"current_password": PW}).status_code == 401


def test_turning_it_on_signs_out_other_devices(client):
    password(client)
    other = login(client).cookies.get(auth.COOKIE)
    client.cookies.clear()
    login(client)
    secret = client.post("/api/auth/totp/setup", json={"current_password": PW}).json()["secret"]
    client.post("/api/auth/totp/enable", json={"code": now_code(secret)})
    assert client.get("/api/sessions").status_code == 200  # this one stays
    with SessionLocal() as db:
        assert not auth.valid_token(db, other)


def test_turning_it_off_needs_password_and_code(client):
    secret, _ = turn_on_totp(client)
    bad = client.post("/api/auth/totp/disable", json={"current_password": PW, "code": "123456"})
    assert bad.status_code == 403
    ok = client.post("/api/auth/totp/disable",
                     json={"current_password": PW, "code": now_code(secret, 1)})
    assert ok.status_code == 200
    client.cookies.clear()
    assert login(client).status_code == 200


def test_clearing_the_password_clears_two_factor(client):
    turn_on_totp(client)
    client.post("/api/auth/password", json={"password": "", "current_password": PW})
    password(client)
    assert login(client).status_code == 200


# --- cookies ----------------------------------------------------------------------

def test_cookie_is_secure_behind_https_proxy(client):
    password(client)
    r = login(client, headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()
    client.cookies.clear()
    r = login(client, headers={"cf-visitor": '{"scheme":"https"}'})
    assert "secure" in r.headers["set-cookie"].lower()


def test_cookie_is_not_secure_on_plain_lan_http(client):
    password(client)
    r = login(client)
    assert "secure" not in r.headers["set-cookie"].lower()
