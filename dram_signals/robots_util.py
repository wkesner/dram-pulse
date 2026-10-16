"""Thin robots.txt helper — check a path before fetching a new host path."""

from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from dram_signals import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _parser_for(host_origin: str, user_agent: str) -> RobotFileParser:
    rp = RobotFileParser()
    robots_url = f"{host_origin.rstrip('/')}/robots.txt"
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001 — treat unreadable robots as allow-unknown
        logger.warning("robots.txt unreadable for %s: %s", robots_url, exc)
    return rp


def allowed(url: str, *, user_agent: str | None = None) -> bool:
    """Return True if robots.txt allows fetching `url` for our UA.

    Missing or unreadable robots.txt → True (robots convention: no rules = allow).
    Callers must still honour site Terms of Use separately.
    """
    ua = user_agent or config.USER_AGENT
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _parser_for(origin, ua)
    try:
        return bool(rp.can_fetch(ua, url))
    except Exception:  # noqa: BLE001
        return True


def clear_cache() -> None:
    _parser_for.cache_clear()
