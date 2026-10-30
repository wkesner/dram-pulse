"""CNINFO / EDINET / Companies House fetch + Chinese extract wiring."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.extract import (
    REPO_ROOT,
    ExtractContext,
    _load_term_banks,
    _text_is_cjk,
    extract_from_section,
    split_sentences,
)
from src.fetch_cninfo import (
    _ms_to_iso,
    list_announcements,
    resolve_org_id,
)
from src.fetch_edgar import Company, load_companies
from src.fetch_edinet import _sec_code, require_edinet_api_key
from src.fetch_companies_house import require_companies_house_api_key
from src.fetch_mops import roc_to_iso, _material_body, _norm_row


def test_companies_yaml_has_asia_uk_sources() -> None:
    cos = load_companies(REPO_ROOT / "config" / "companies.yaml")
    by_source: dict[str, list[Company]] = {}
    for c in cos:
        by_source.setdefault(c.source.upper(), []).append(c)
    assert any(c.ticker == "688981" for c in by_source["CNINFO"])
    assert any(c.ticker == "8035" for c in by_source["EDINET"])
    assert any(c.company_number == "01106260" for c in by_source["CH"])
    assert any(c.exchange == "szse" for c in by_source["CNINFO"])
    assert any(c.ticker == "2330" for c in by_source["MOPS"])
    assert any(c.ticker == "2454" for c in by_source["MOPS"])


def test_mops_roc_to_iso() -> None:
    assert roc_to_iso("1151028") == "2026-10-28"
    assert roc_to_iso("11509") == "2026-09-01"


def test_mops_portal_url_not_openapi_json() -> None:
    from src.fetch_mops import filing_open_url, is_twse_openapi_url, mops_portal_url

    api = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
    assert is_twse_openapi_url(api)
    portal = mops_portal_url(
        ticker="2303", filing_date="2026-10-28", feed="t187ap04_L"
    )
    assert "mopsov.twse.com.tw" in portal
    assert "co_id=2303" in portal
    assert "openapi.twse.com.tw" not in portal
    # Open filing rewrite for UMC
    fixed = filing_open_url(
        api, ticker="2303", filing_date="2026-10-28", source="MOPS"
    )
    assert fixed == portal
    assert not is_twse_openapi_url(fixed)


def test_mops_material_body_includes_detail() -> None:
    row = _norm_row(
        {
            "主旨 ": "公告取得機器設備",
            "符合條款": "第20款",
            "事實發生日": "1151027",
            "說明": "晶圓代工產能擴充，購置設備。",
        }
    )
    body = _material_body(row)
    assert "主旨：公告取得機器設備" in body
    assert "晶圓代工產能擴充" in body


def test_edinet_sec_code_padding() -> None:
    c = Company(name="TEL", role="supplier", source="EDINET", ticker="8035")
    assert _sec_code(c) == "80350"


def test_cninfo_ms_to_iso() -> None:
    # 2024-08-30 00:00 CST ≈ 1724947200000 in cninfo samples
    assert _ms_to_iso(1724947200000).startswith("2024-08")


def test_resolve_org_id_uses_cache(tmp_path: Path, monkeypatch) -> None:
    settings = {
        "paths": {"raw_dir": str(tmp_path)},
        "cninfo": {
            "top_search_url": "https://example.test/topSearch",
        },
    }
    # Point REPO_ROOT-relative raw via monkeypatch of helper? resolve uses
    # settings paths under REPO_ROOT — write into real _cninfo under tmp by
    # patching _raw_root.
    cache = tmp_path / "_cninfo"
    cache.mkdir(parents=True)
    (cache / "org_ids.json").write_text(
        json.dumps({"688981": "gshk0000981"}) + "\n", encoding="utf-8"
    )
    sess = MagicMock()
    lim = MagicMock()
    with patch("src.fetch_cninfo._raw_root", return_value=tmp_path):
        org = resolve_org_id("688981", settings, sess, lim)
    assert org == "gshk0000981"
    sess.post.assert_not_called()


def test_list_announcements_paginates(monkeypatch) -> None:
    settings = {
        "cninfo": {
            "query_url": "https://example.test/query",
            "default_column": "sse",
            "categories": ["category_ndbg_szsh"],
        }
    }
    page1 = {
        "announcements": [
            {"announcementId": "1", "announcementTitle": "年报", "announcementTime": 1}
        ],
        "hasMore": False,
        "totalpages": 1,
    }
    sess = MagicMock()
    sess.post.return_value = MagicMock(
        status_code=200, json=lambda: page1, raise_for_status=lambda: None
    )
    lim = MagicMock()
    rows = list_announcements(
        "688981",
        "gshk0000981",
        date(2024, 1, 1),
        date(2024, 12, 31),
        settings,
        sess,
        lim,
    )
    assert len(rows) == 1
    assert rows[0]["announcementId"] == "1"


def test_key_stubs_exit_without_env(monkeypatch) -> None:
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    # Ensure .env load does not revive keys: point load_dotenv at missing file.
    with patch("src.fetch_edinet.load_dotenv", return_value=None), patch(
        "src.envutil.load_dotenv", return_value=None
    ):
        try:
            require_edinet_api_key()
            raised = False
        except SystemExit as exc:
            raised = True
            assert "EDINET_API_KEY" in str(exc)
        assert raised
    with patch("src.fetch_companies_house.load_dotenv", return_value=None), patch(
        "src.envutil.load_dotenv", return_value=None
    ):
        try:
            require_companies_house_api_key()
            raised = False
        except SystemExit as exc:
            raised = True
            assert "COMPANIES_HOUSE_API_KEY" in str(exc)
        assert raised


def test_chinese_terms_and_extract() -> None:
    banks = _load_term_banks(
        REPO_ROOT / "config" / "product_taxonomy.yaml",
        REPO_ROOT / "config" / "supply_terms.yaml",
    )
    product_terms = {p.term for p in banks["products"]}
    assert "闪存" in product_terms or "存储器" in product_terms
    # Commodity banks for CNINFO electronic / foundry language.
    for term in (
        "微控制器",
        "图形处理器",
        "中央处理器",
        "电源管理芯片",
        "固态硬盘",
        "多层陶瓷电容",
        "高速连接器",
        "闪存芯片",
        "晶圆厂",
    ):
        assert term in product_terms, term
    blob = " ".join(term for _label, term, _pat in banks["tightening"])
    assert "供应紧张" in blob or "供应短缺" in blob
    assert "紧缺" in blob or "缺货" in blob
    assert "产能紧张" in blob or "量价齐升" in blob

    text = (
        "报告期内公司存储器与微控制器产品需求旺盛，闪存芯片供应紧张导致价格上涨。"
        "晶圆代工产能利用率维持高位，GPU与电源管理芯片交期延长。"
    )
    assert _text_is_cjk(text)
    assert len(split_sentences(text)) >= 1

    raw_dir = REPO_ROOT / "data" / "raw" / "_test_cninfo"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / "other_full.txt"
    raw.write_text(text, encoding="utf-8")
    company = Company(
        name="GigaDevice",
        role="supplier",
        source="CNINFO",
        ticker="603986",
        exchange="sse",
    )
    meta = {
        "source": "CNINFO",
        "source_url": "https://static.cninfo.com.cn/example.pdf",
        "form": "半年度报告",
        "filing_date": "2024-08-30",
        "accession_number": "testzh001",
        "language": "zh",
        "lookback_months": 12,
        "window_start": "2025-01-01",
        "window_end": "2025-12-31",
        "company": "GigaDevice",
        "ticker": "603986",
        "role": "supplier",
    }
    from datetime import datetime, timezone

    ctx = ExtractContext(
        company=company,
        meta=meta,
        products=banks["products"],
        topic_compiled=banks["topic_compiled"],
        tightening=banks["tightening"],
        easing=banks["easing"],
        stable=banks["stable"],
        positive=banks["positive"],
        negative=banks["negative"],
        prior_texts=[],
        extracted_at=datetime.now(timezone.utc).isoformat(),
    )
    rows = extract_from_section(ctx, "other", raw, text)
    assert rows, "expected at least one Chinese supply signal"
    assert all(r.get("language") == "zh" for r in rows)
    assert all(r.get("source") == "CNINFO" for r in rows)
