from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class APIError(HTTPException):
    """Expected, client-safe error. `message` must never contain secrets or internal detail."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "details": details or {}},
            headers=headers,
        )


def unauthorized() -> APIError:
    return APIError(
        status.HTTP_401_UNAUTHORIZED, "authentication_required", "Authentication is required."
    )


def too_many_attempts(retry_after_seconds: int) -> APIError:
    # Identical wording whether or not the account exists (no enumeration).
    return APIError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too_many_attempts",
        "Too many attempts. Try again later.",
        headers={"Retry-After": str(retry_after_seconds)},
    )
