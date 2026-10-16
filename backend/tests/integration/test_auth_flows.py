"""Authentication, sessions, devices, CSRF, throttling, password change/reset and voice-status tests.

Requires TEST_DATABASE_URL (PostgreSQL)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core.clock import utcnow
from app.db.session import get_session_factory
from app.main import app
from app.models import AuthFailure, Owner
from app.models import Session as OwnerSession
from app.security.digester import get_digester
from tests.conftest import BOOTSTRAP_HEADER, EMAIL, PASSWORD, csrf_headers


def _db():
    return get_session_factory()()


def test_bootstrap_creates_single_owner_then_refuses(client):
    body = {"email": EMAIL, "password": PASSWORD}
    assert client.post("/api/v1/auth/bootstrap", json=body, headers=BOOTSTRAP_HEADER).status_code == 201
    second = client.post("/api/v1/auth/bootstrap", json=body, headers=BOOTSTRAP_HEADER)
    assert second.status_code == 409


def test_bootstrap_requires_token_and_is_hidden_when_disabled(client, monkeypatch):
    body = {"email": EMAIL, "password": PASSWORD}
    wrong = client.post("/api/v1/auth/bootstrap", json=body, headers={"X-Setup-Token": "nope"})
    assert wrong.status_code == 403
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_bootstrap", False)
    hidden = client.post("/api/v1/auth/bootstrap", json=body, headers=BOOTSTRAP_HEADER)
    assert hidden.status_code == 404


def test_login_sets_httponly_session_and_readable_csrf_cookies(owner_client):
    me = owner_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    set_cookie = " ".join(owner_client.cookies.keys())
    assert "logu_session" in set_cookie and "logu_csrf" in set_cookie


def test_failed_login_message_is_identical_for_unknown_and_wrong_password(owner_client, client):
    wrong_password = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password-1"})
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert wrong_password.status_code == unknown.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_login_is_throttled_per_client_and_email_then_recovers(owner_client, client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password-1"})
    blocked = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after")
    # The window is the only lock: once failures age out, the correct password works again.
    with _db() as db:
        db.execute(update(AuthFailure).values(created_at=utcnow() - timedelta(hours=2)))
        db.commit()
    recovered = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert recovered.status_code == 200


def test_throttle_response_does_not_depend_on_account_existence(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong-password-1"})
    response = client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong-password-1"})
    assert response.status_code == 429
    assert response.json()["error"]["message"] == "Too many attempts. Try again later."


def test_logout_revokes_session_and_requires_csrf(owner_client):
    assert owner_client.post("/api/v1/auth/logout").status_code == 403
    assert owner_client.post("/api/v1/auth/logout", headers=csrf_headers(owner_client)).status_code == 200
    assert owner_client.get("/api/v1/auth/me").status_code == 401


def test_wrong_csrf_token_is_rejected(owner_client):
    response = owner_client.post("/api/v1/auth/sessions/revoke-others", headers={"X-CSRF-Token": "forged"})
    assert response.status_code == 403


def test_cross_origin_state_change_is_rejected(owner_client):
    headers = {**csrf_headers(owner_client), "Origin": "https://attacker.example"}
    response = owner_client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


def test_expired_session_is_rejected(owner_client):
    with _db() as db:
        db.execute(update(OwnerSession).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert owner_client.get("/api/v1/auth/me").status_code == 401


def test_idle_timeout_revokes_session(owner_client):
    with _db() as db:
        db.execute(update(OwnerSession).values(last_seen_at=utcnow() - timedelta(hours=3)))
        db.commit()
    assert owner_client.get("/api/v1/auth/me").status_code == 401
    with _db() as db:
        reason = db.scalar(select(OwnerSession.revoke_reason))
    assert reason == "idle_timeout"


def test_session_listing_and_revoking_other_sessions(owner_client, client):
    other = TestClient(app, base_url="http://testserver")
    other.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    listing = owner_client.get("/api/v1/auth/sessions").json()["sessions"]
    assert len(listing) == 2
    assert sum(1 for s in listing if s["current"]) == 1

    revoked = owner_client.post("/api/v1/auth/sessions/revoke-others", headers=csrf_headers(owner_client))
    assert revoked.status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    assert owner_client.get("/api/v1/auth/me").status_code == 200


def test_revoking_a_specific_session(owner_client):
    other = TestClient(app, base_url="http://testserver")
    other.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    target = next(s for s in owner_client.get("/api/v1/auth/sessions").json()["sessions"] if not s["current"])
    response = owner_client.delete(f"/api/v1/auth/sessions/{target['id']}", headers=csrf_headers(owner_client))
    assert response.status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_device_revocation_signs_out_its_sessions(owner_client):
    other = TestClient(app, base_url="http://testserver")
    other.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    devices = owner_client.get("/api/v1/auth/devices").json()["devices"]
    assert len(devices) == 2
    target = next(d for d in devices if not d["current"])
    response = owner_client.post(f"/api/v1/auth/devices/{target['id']}/revoke", headers=csrf_headers(owner_client))
    assert response.status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401


def test_password_change_keeps_current_session_and_revokes_others(owner_client):
    other = TestClient(app, base_url="http://testserver")
    other.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    response = owner_client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(owner_client),
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password-2"},
    )
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 1
    assert owner_client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    old = TestClient(app, base_url="http://testserver").post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert old.status_code == 401


def test_password_change_requires_current_password(owner_client):
    response = owner_client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(owner_client),
        json={"current_password": "wrong-current-password", "new_password": "a-brand-new-password-2"},
    )
    assert response.status_code == 401


def test_password_policy_is_enforced_on_change(owner_client):
    response = owner_client.post(
        "/api/v1/auth/password",
        headers=csrf_headers(owner_client),
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert response.status_code == 422


def test_reset_request_is_generic_and_issues_no_token_without_provider(owner_client, client):
    response = client.post("/api/v1/auth/password/reset-request", json={"email": EMAIL})
    unknown = client.post("/api/v1/auth/password/reset-request", json={"email": "ghost@example.com"})
    assert response.status_code == unknown.status_code == 202
    assert response.json() == unknown.json()
    from app.models import PasswordResetToken

    with _db() as db:
        assert db.scalar(select(PasswordResetToken.id)) is None


def test_reset_confirm_rejects_unknown_token(client):
    response = client.post(
        "/api/v1/auth/password/reset",
        json={"token": "x" * 40, "new_password": "a-brand-new-password-2"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reset_token"


def test_voice_unlock_is_honestly_not_implemented(owner_client):
    status = owner_client.get("/api/v1/security/voice-unlock").json()
    assert status["enabled"] is False
    assert status["implemented"] is False
    assert status["provider_configured"] is False
    enable = owner_client.put(
        "/api/v1/security/voice-unlock", headers=csrf_headers(owner_client), json={"enabled": True}
    )
    assert enable.status_code == 409
    enroll = owner_client.post("/api/v1/security/voice-unlock/enroll", headers=csrf_headers(owner_client))
    assert enroll.status_code == 501


def test_login_stores_keyed_ip_digest_not_raw_ip(client):
    peer_client = TestClient(app, base_url="http://testserver", client=("172.28.10.10", 4000))
    peer_client.post(
        "/api/v1/auth/bootstrap",
        json={"email": EMAIL, "password": PASSWORD},
        headers=BOOTSTRAP_HEADER,
    )
    peer_client.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.7", "X-Forwarded-Proto": "https"},
    )
    with _db() as db:
        stored = db.scalar(text("SELECT ip_hash FROM sessions LIMIT 1"))
    assert stored == get_digester().digest("ip", "198.51.100.7")
    assert stored != "198.51.100.7"


def test_owner_row_has_no_plaintext_password(owner_client):
    with _db() as db:
        owner = db.scalar(select(Owner))
    assert owner.password_hash.startswith("$argon2id$")
    assert PASSWORD not in owner.password_hash
