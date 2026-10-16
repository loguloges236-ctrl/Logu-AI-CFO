"""Database-backed failure throttling.

Replaces the earlier process-local limiter and the per-account hard lock:
- Counters live in PostgreSQL, so they are shared across API replicas and survive restarts.
- Memory is bounded: rows older than the retention period are purged on every write.
- Scopes are HMAC digests of (client IP, email) and (email) and (IP). No raw IP or email is stored here.
- There is NO account-wide permanent lock. An attacker who floods failures can slow the owner's
  sign-in for one window, but cannot disable the account; the window always expires.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import too_many_attempts
from app.models import AuthFailure
from app.security.digester import get_digester

RETENTION = timedelta(days=1)


@dataclass(frozen=True)
class Scope:
    key: str
    limit: int


def login_scopes(ip: str | None, email: str) -> list[Scope]:
    settings = get_settings()
    digester = get_digester()
    scopes = [
        Scope(digester.digest("login:ip-email", f"{ip or 'unknown'}|{email}"), settings.login_ip_email_limit),
        Scope(digester.digest("login:email", email), settings.login_email_limit),
    ]
    if ip:
        scopes.append(Scope(digester.digest("login:ip", ip), settings.login_ip_limit))
    return scopes


def reset_request_scopes(ip: str | None, email: str) -> list[Scope]:
    settings = get_settings()
    digester = get_digester()
    scopes = [
        Scope(digester.digest("reset-request:ip-email", f"{ip or 'unknown'}|{email}"), settings.login_ip_email_limit),
        Scope(digester.digest("reset-request:email", email), settings.login_email_limit),
    ]
    if ip:
        scopes.append(Scope(digester.digest("reset-request:ip", ip), settings.login_ip_limit))
    return scopes


def reset_confirm_scopes(ip: str | None) -> list[Scope]:
    settings = get_settings()
    digester = get_digester()
    return [Scope(digester.digest("reset-confirm:ip", ip or "unknown"), settings.login_ip_limit)]


def password_change_scopes(owner_id: str) -> list[Scope]:
    settings = get_settings()
    digester = get_digester()
    return [Scope(digester.digest("password-change:owner", owner_id), settings.login_email_limit)]


def ensure_allowed(db: Session, scopes: Sequence[Scope]) -> None:
    settings = get_settings()
    window = timedelta(minutes=settings.throttle_window_minutes)
    window_start = utcnow() - window
    for scope in scopes:
        count = db.scalar(
            select(func.count())
            .select_from(AuthFailure)
            .where(AuthFailure.scope_key == scope.key, AuthFailure.created_at > window_start)
        )
        if count is not None and count >= scope.limit:
            raise too_many_attempts(int(window.total_seconds()))


def record_failure(db: Session, scopes: Sequence[Scope]) -> None:
    now = utcnow()
    for scope in scopes:
        db.add(AuthFailure(scope_key=scope.key, created_at=now))
    db.execute(delete(AuthFailure).where(AuthFailure.created_at < now - RETENTION))
