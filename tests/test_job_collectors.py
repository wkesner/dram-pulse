"""Unit tests for job board helpers and collectors (no network)."""

from __future__ import annotations

from dram_signals.boards import Board, load_boards
from dram_signals.collectors.greenhouse import GreenhouseCollector
from dram_signals.collectors.lever import LeverCollector
from dram_signals.job_titles import matching_title_keyword
from dram_signals.textutil import html_to_text


def test_title_keywords() -> None:
    assert matching_title_keyword("Senior Buyer, Memory") == "buyer"
    assert matching_title_keyword("Director of Procurement") == "procurement"
    assert matching_title_keyword("Commodity Manager - DRAM") == "commodity manager"
    assert matching_title_keyword("Global Supply Chain Manager") == "supply chain"
    assert matching_title_keyword("Software Engineer") is None


def test_load_boards_includes_dram_makers() -> None:
    boards = load_boards()
    greenhouse = {b.token for b in boards if b.provider == "greenhouse"}
    lever = {b.token for b in boards if b.provider == "lever"}
    workday = {b.token: b for b in boards if b.provider == "workday"}
    icims = {b.token: b for b in boards if b.provider == "icims"}
    assert "skhynixamerica" in greenhouse
    assert "samsungsemiconductor" in greenhouse
    assert "tenstorrent" in greenhouse
    assert "waymo" in greenhouse
    assert "spacex" in greenhouse
    assert "shieldai" in lever
    assert "zoox" in lever
    assert "micron" in workday
    assert "jabil" in workday
    assert "flextronics" in workday
    assert workday["micron"].cxs_jobs_url is not None
    assert "rambus" in icims
    assert "hamiltoncompany" in icims
    assert "strattec" in icims
    assert icims["rambus"].host == "careers-rambus.icims.com"


def test_greenhouse_job_to_record_filters_and_decodes() -> None:
    board = Board("greenhouse", "tenstorrent", "Tenstorrent")
    collector = GreenhouseCollector(boards=[board])
    job = {
        "id": 123,
        "title": "Global Supply Chain Manager",
        "company_name": "Tenstorrent",
        "absolute_url": "https://example.com/jobs/123",
        "updated_at": "2026-09-24T13:30:07-04:00",
        "location": {"name": "Santa Clara"},
        "content": "&lt;p&gt;Own DRAM allocation and OSAT partners.&lt;/p&gt;",
    }
    record = collector._job_to_record(board, job)
    assert record is not None
    assert record.source == "greenhouse"
    assert record.keyword == "supply chain"
    assert record.company_name == "Tenstorrent"
    assert "DRAM allocation" in record.raw_text
    assert "<" not in record.raw_text
    assert record.external_id == "tenstorrent:123"

    skip = collector._job_to_record(
        board,
        {**job, "title": "Staff Software Engineer"},
    )
    assert skip is None


def test_lever_posting_to_record() -> None:
    board = Board("lever", "shieldai", "Shield AI")
    collector = LeverCollector(boards=[board])
    posting = {
        "id": "abc-123",
        "text": "Staff Engineer, Supply Chain (R6215)",
        "hostedUrl": "https://jobs.lever.co/shieldai/abc-123",
        "createdAt": 1_700_000_000_000,
        "categories": {"location": "Dallas, TX"},
        "descriptionPlain": "Work with commodity managers on memory sourcing.",
    }
    record = collector._posting_to_record(board, posting)
    assert record is not None
    assert record.source == "lever"
    assert record.keyword == "supply chain"
    assert record.company_name == "Shield AI"
    assert "memory sourcing" in record.raw_text
    assert record.timestamp.startswith("2023-")


def test_html_to_text_double_encoded() -> None:
    text = html_to_text("&lt;div&gt;Hello &amp;amp; welcome&lt;/div&gt;")
    assert "Hello" in text
    assert "welcome" in text
    assert "<" not in text


def test_workday_url_helpers() -> None:
    from dram_signals.collectors.workday import WorkdayCollector

    jobs = "https://micron.wd1.myworkdayjobs.com/wday/cxs/micron/External/jobs"
    path = "/job/Boise/Buyer_JR1"
    assert WorkdayCollector._detail_api_url(jobs, path).endswith(
        "/wday/cxs/micron/External/job/Boise/Buyer_JR1"
    )
    public = WorkdayCollector._public_job_url(jobs, path)
    assert public.startswith("https://micron.wd1.myworkdayjobs.com/External/")


def test_icims_parse_job_posting_and_location() -> None:
    from dram_signals.collectors.icims import IcimsCollector

    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@type": "JobPosting",
      "title": "Senior Buyer",
      "description": "<p>Buy DRAM and NAND.</p>",
      "datePosted": "2026-10-01T12:00:00.000Z",
      "url": "https://careers-example.icims.com/jobs/1/senior-buyer/job",
      "hiringOrganization": {"name": "Example Corp"},
      "jobLocation": {
        "address": {
          "addressLocality": "Austin",
          "addressRegion": "TX",
          "addressCountry": "US",
          "postalCode": "UNAVAILABLE"
        }
      }
    }
    </script>
    </head></html>
    """
    posting = IcimsCollector._parse_job_posting(html)
    assert posting is not None
    assert posting["title"] == "Senior Buyer"
    assert IcimsCollector._format_location(posting["jobLocation"]) == "Austin, TX, US"
    assert "UNAVAILABLE" not in IcimsCollector._format_location(posting["jobLocation"])


