#!/usr/bin/env python3
"""Step 0: read-only catalog probes. Print results; store nothing.

Usage:
  COLLECTOR_CONTACT=you@example.com python scripts/probe_catalogs.py
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

CONTACT = os.environ.get("COLLECTOR_CONTACT", "research@example.com")
UA = f"DRAMSignalPipeline-Probe/0.1 (+contact:{CONTACT}; lifecycle research; read-only)"
TIMEOUT = 15
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*"})

PART_PATH_RE = re.compile(r"^/(dram|mcp)/[^/]+/[^/]+/[a-z0-9-]+/$")
FAMILY_PATH_RE = re.compile(r"^/(dram|mcp)/[^/]+/[^/]+/$")
META_PRODUCT_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](meta-product[^"\']+|og:url)["\'][^>]+content=["\']([^"\']*)["\']',
    re.I,
)
# Also handle content-before-name attribute order
META_PRODUCT_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:name|property)=["\'](meta-product[^"\']+|og:url)["\']',
    re.I,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def fetch(url: str) -> requests.Response:
    return SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_robots(url: str) -> None:
    section(f"robots.txt — {url}")
    try:
        resp = fetch(url)
        print(f"status: {resp.status_code}")
        print(f"final_url: {resp.url}")
        print(f"content_type: {resp.headers.get('content-type')}")
        print(f"bytes: {len(resp.content)}")
        print("--- body ---")
        print(resp.text)
        print("--- end ---")
    except requests.RequestException as exc:
        print(f"ERROR: {exc}")


def extract_meta(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, content in META_PRODUCT_RE.findall(html):
        found[name] = content
    for content, name in META_PRODUCT_RE2.findall(html):
        found.setdefault(name, content)
    return found


def extract_title(html: str) -> str:
    match = TITLE_RE.search(html)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def sitemap_locs(url: str) -> list[str]:
    resp = fetch(url)
    resp.raise_for_status()
    # Strip default namespace so findall works simply
    text = re.sub(r'\sxmlns="[^"]+"', "", resp.text, count=1)
    root = ET.fromstring(text)
    locs: list[str] = []
    if root.tag.endswith("sitemapindex"):
        for sm in root.findall("sitemap"):
            loc = sm.findtext("loc")
            if loc:
                locs.extend(sitemap_locs(loc))
    else:
        for url_el in root.findall("url"):
            loc = url_el.findtext("loc")
            if loc:
                locs.append(loc)
    return locs


def pick_part_urls(locs: list[str]) -> dict[str, str]:
    """Pick one GDDR7, one LPDDR5X, one RDIMM part-like URL from sitemap locs."""
    picks: dict[str, str] = {}
    for loc in locs:
        path = urlparse(loc).path.lower()
        if not PART_PATH_RE.match(path):
            continue
        if "gddr7" in path and "gddr7" not in picks:
            picks["gddr7"] = loc
        elif "lpddr5x" in path and "lpddr5x" not in picks:
            picks["lpddr5x"] = loc
        elif "/rdimm/" in path and "rdimm" not in picks:
            picks["rdimm"] = loc
        if len(picks) == 3:
            break
    return picks


def probe_samsung_part(url: str, label: str) -> None:
    section(f"Samsung part URL ({label}) — {url}")
    try:
        resp = fetch(url)
    except requests.RequestException as exc:
        print(f"ERROR: {exc}")
        return
    html = resp.text
    title = extract_title(html)
    meta = extract_meta(html)
    slug = urlparse(url).path.rstrip("/").split("/")[-1].lower()
    body_lower = html.lower()
    slug_in_body = slug in body_lower
    print(f"status: {resp.status_code}")
    print(f"final_url: {resp.url}")
    print(f"title: {title}")
    print(f"og:url: {meta.get('og:url', '(missing)')}")
    product_metas = {k: v for k, v in meta.items() if k.startswith("meta-product")}
    if product_metas:
        print("meta-product* tags:")
        for k in sorted(product_metas):
            print(f"  {k}: {product_metas[k]}")
    else:
        print("meta-product* tags: (none)")
    # Also print any meta that looks product-related via name= without meta- prefix
    extra = re.findall(
        r'<meta[^>]+name=["\']([^"\']*product[^"\']*)["\'][^>]+content=["\']([^"\']*)["\']',
        html,
        re.I,
    )
    extra2 = re.findall(
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']([^"\']*product[^"\']*)["\']',
        html,
        re.I,
    )
    if extra or extra2:
        print("other *product* meta names:")
        for name, content in extra:
            print(f"  {name}: {content}")
        for content, name in extra2:
            print(f"  {name}: {content}")
    print(f"part_slug: {slug}")
    print(f"slug_in_body_text: {slug_in_body}")


def probe_samsung_sitemap_counts() -> list[str]:
    section("Samsung sitemap part-like vs family URL counts")
    locs: list[str] = []
    for sm_url in (
        "https://semiconductor.samsung.com/sitemap.xml",
        "https://semiconductor.samsung.com/top_sitemap.xml",
    ):
        try:
            found = sitemap_locs(sm_url)
            print(f"{sm_url}: {len(found)} URLs")
            locs.extend(found)
        except Exception as exc:
            print(f"{sm_url}: ERROR {exc}")
    # Dedupe
    locs = sorted(set(locs))
    part_n = 0
    family_n = 0
    other_dram_n = 0
    for loc in locs:
        path = urlparse(loc).path
        if PART_PATH_RE.match(path):
            part_n += 1
        elif FAMILY_PATH_RE.match(path):
            family_n += 1
        elif path.startswith("/dram/") or path.startswith("/mcp/"):
            other_dram_n += 1
    print(f"unique_urls_total: {len(locs)}")
    print(f"part_like ^/(dram|mcp)/[^/]+/[^/]+/[a-z0-9-]+/$ : {part_n}")
    print(f"family_like ^/(dram|mcp)/[^/]+/[^/]+/$ : {family_n}")
    print(f"other /dram|/mcp paths: {other_dram_n}")
    return locs


def probe_skhynix_home() -> None:
    url = "https://product.skhynix.com/home.go"
    section(f"SK hynix product home — {url}")
    try:
        resp = fetch(url)
        print(f"status: {resp.status_code}")
        print(f"final_url: {resp.url}")
        print(f"content_type: {resp.headers.get('content-type')}")
        print(f"bytes: {len(resp.content)}")
        print("--- first 500 characters ---")
        print(resp.text[:500])
        print("--- end ---")
    except requests.RequestException as exc:
        print(f"ERROR: {exc}")


def main() -> int:
    print(f"User-Agent: {UA}")
    print(f"timeout_s: {TIMEOUT}")
    print("no retries; print-only; no storage")

    print_robots("https://product.skhynix.com/robots.txt")
    print_robots("https://semiconductor.samsung.com/robots.txt")

    locs = probe_samsung_sitemap_counts()
    picks = pick_part_urls(locs)
    print()
    print("picked part URLs for detail probe:")
    for label, url in picks.items():
        print(f"  {label}: {url}")
    if len(picks) < 3:
        print("WARNING: fewer than 3 part URL categories found in sitemap")

    for label, url in picks.items():
        probe_samsung_part(url, label)

    probe_skhynix_home()
    print_robots("https://www.dramexchange.com/robots.txt")

    section("Step 0 complete — awaiting review before any collector work")
    return 0


if __name__ == "__main__":
    sys.exit(main())
