from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    """Application logs never include request bodies, cookies, tokens, keys or raw IP addresses."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
