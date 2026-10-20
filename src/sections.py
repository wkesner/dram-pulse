"""Split 10-K / 10-Q HTML (or plain text) into Item 1 / 1A / 7 sections."""

from __future__ import annotations

import re
from dataclasses import dataclass

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Canonical section keys used in signals.csv / on disk.
SECTION_BUSINESS = "business"
SECTION_RISK_FACTORS = "risk_factors"
SECTION_MDNA = "mdna"
SECTION_OTHER = "other"

# Match common EDGAR heading styles: "Item 1.", "ITEM 1A —", HTML-stripped noise.
_ITEM_HEADING = re.compile(
    r"(?im)"
    r"(?:^|\n)\s*"
    r"(?:ITEM|Item)\s+"
    r"(?P<label>1A|1B|1C|7A|7|1|2|3|4|5|6|8|9A|9B|9C|9|10|11|12|13|14|15|16)"
    r"\s*[.\-:—–]?\s*"
    r"(?P<title>[^\n]{0,120})"
)


@dataclass(frozen=True)
class SectionHit:
    label: str
    title: str
    start: int  # offset into plain text where the heading begins


def html_to_text(html: str) -> str:
    """Strip HTML to readable plain text for section splitting and caching."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines; keep single newlines for heading detection.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _find_item_headings(text: str) -> list[SectionHit]:
    hits: list[SectionHit] = []
    for match in _ITEM_HEADING.finditer(text):
        label = match.group("label").upper()
        title = (match.group("title") or "").strip()
        # Prefer the start of "Item …" itself, not the preceding newline.
        item_at = match.start()
        local = text[item_at : match.end()]
        rel = re.search(r"(?i)(?:ITEM|Item)\s+", local)
        start = item_at + (rel.start() if rel else 0)
        hits.append(SectionHit(label=label, title=title, start=start))
    return hits


def _pick_body_heading(
    hits: list[SectionHit],
    label: str,
    title_hint: re.Pattern[str] | None = None,
) -> SectionHit | None:
    """
    EDGAR filings usually list Items in a table of contents first, then again
    in the body. Prefer the last heading for a label whose title matches the
    hint (when given); otherwise the last occurrence.
    """
    candidates = [h for h in hits if h.label == label]
    if not candidates:
        return None
    if title_hint is not None:
        titled = [h for h in candidates if title_hint.search(h.title)]
        if titled:
            return titled[-1]
    return candidates[-1]


def _next_section_start(
    hits: list[SectionHit],
    after: SectionHit,
    stop_labels: tuple[str, ...],
) -> int | None:
    """Return the start offset of the next stop-label heading after `after`."""
    later = [h for h in hits if h.start > after.start and h.label in stop_labels]
    if not later:
        return None
    return later[0].start


def split_10k_sections(text: str) -> dict[str, str]:
    """
    Split plain filing text into business / risk_factors / mdna / other.

    `other` is the full plain text (per SPEC: keep the full text with section
    set to `other`). Named sections are the body slices for Items 1, 1A, and 7.
    """
    hits = _find_item_headings(text)

    item1 = _pick_body_heading(
        hits, "1", re.compile(r"business", re.I)
    ) or _pick_body_heading(hits, "1")
    item1a = _pick_body_heading(
        hits, "1A", re.compile(r"risk", re.I)
    ) or _pick_body_heading(hits, "1A")
    item7 = _pick_body_heading(
        hits,
        "7",
        re.compile(r"management|discussion|md&a|analysis", re.I),
    ) or _pick_body_heading(hits, "7")

    sections: dict[str, str] = {SECTION_OTHER: text}

    if item1 is not None:
        end = (
            _next_section_start(hits, item1, ("1A", "1B", "1C", "2"))
            if item1a is None
            else item1a.start
        )
        # If Item 1A is the chosen end but appears before Item 1 (TOC quirk),
        # fall back to the next stop label after Item 1.
        if end is not None and end <= item1.start:
            end = _next_section_start(hits, item1, ("1A", "1B", "1C", "2"))
        sections[SECTION_BUSINESS] = text[item1.start : end].strip() + "\n"

    if item1a is not None:
        end = _next_section_start(hits, item1a, ("1B", "1C", "2", "3"))
        sections[SECTION_RISK_FACTORS] = text[item1a.start : end].strip() + "\n"

    if item7 is not None:
        end = _next_section_start(hits, item7, ("7A", "8", "9"))
        sections[SECTION_MDNA] = text[item7.start : end].strip() + "\n"

    return sections


def split_filing(html_or_text: str) -> dict[str, str]:
    """Accept HTML or plain text; return section map."""
    stripped = html_or_text.lstrip()
    if stripped.lower().startswith("<!") or stripped.lower().startswith("<html") or "<html" in stripped[:500].lower():
        text = html_to_text(html_or_text)
    elif "<" in stripped[:200] and ">" in stripped[:500]:
        text = html_to_text(html_or_text)
    else:
        text = html_or_text if html_or_text.endswith("\n") else html_or_text + "\n"
    return split_10k_sections(text)
