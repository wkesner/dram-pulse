"""Shared text helpers for collectors."""

from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    """Strip HTML to plain text. Greenhouse content is often double-entity-encoded."""
    if not html:
        return ""
    # Unescape repeatedly until stable (handles &amp;lt;div&amp;gt; style payloads).
    previous = None
    text = html
    for _ in range(3):
        if text == previous:
            break
        previous = text
        text = unescape(text)

    try:
        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
    except Exception:
        text = _TAG_RE.sub(" ", text)

    return _WS_RE.sub(" ", text).strip()
