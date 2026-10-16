from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from app.core.config import get_settings
from app.security.keys import KeyedDigester

logger = logging.getLogger(__name__)


@lru_cache
def get_digester() -> KeyedDigester:
    """Return the process-wide keyed digester.

    Production and staging refuse to start without SECURITY_HMAC_KEY (see settings validation).
    In development/test an ephemeral key is generated: digests then change on restart, which only
    resets short-lived rate-limit state and invalidates dev sessions.
    """
    settings = get_settings()
    if settings.security_hmac_key:
        return KeyedDigester(settings.security_hmac_key.encode("utf-8"))
    logger.warning("SECURITY_HMAC_KEY not set; using an ephemeral development key.")
    return KeyedDigester(secrets.token_bytes(32))
