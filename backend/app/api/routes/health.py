from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db.session import get_session_factory

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness only. Does not touch the database and does not expose version or configuration."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response) -> dict[str, str]:
    try:
        with get_session_factory()() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ready"}
