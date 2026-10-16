"""Unit tests for the pure part-number decoder (Step 2)."""

from __future__ import annotations

from dram_signals.partnums import decode, extract_candidate_mpns, normalize_pn


def test_samsung_confirmed_prefixes() -> None:
    k4v = decode("K4VAF325ZC-SC28")
    assert k4v is not None
    assert k4v["vendor"] == "samsung"
    assert k4v["family"] == "GDDR7"
    assert k4v["confidence"] == "high"
    assert k4v["rule_source"] == "samsung_sitemap_confirmed"

    k3k = decode("K3KL2L20DM-JGCT")
    assert k3k is not None
    assert k3k["family"] == "LPDDR5X"

    k4u = decode("K4UBE3D4AA-MGCL")
    assert k4u is not None
    assert k4u["family"] == "LPDDR4X"

    m321 = decode("M321R4GA0EB0-CWM")
    assert m321 is not None
    assert m321["kind"] == "module"
    assert m321["module_type"] == "RDIMM"
    assert m321["family"] == "DDR5"


def test_sk_hynix_marked_unverified() -> None:
    h5 = decode("H5AN8G6NCJR-VKC")
    assert h5 is not None
    assert h5["vendor"] == "sk_hynix"
    assert h5["family"] == "DDR4"
    assert h5["rule_source"] == "general_knowledge_unverified"


def test_micron_ddr4() -> None:
    mt = decode("MT40A1G8SA-062E:B")
    assert mt is not None
    assert mt["vendor"] == "micron"
    assert mt["family"] == "DDR4"
    assert mt["die_rev"] == "B"


def test_unknown_returns_none() -> None:
    assert decode("XYZ123") is None
    assert decode("") is None


def test_extract_from_text() -> None:
    text = "We purchased K4A8G165WC-BCTD and also H5AN8G6NCJR-VKC last quarter."
    found = extract_candidate_mpns(text)
    assert "K4A8G165WC-BCTD" in found
    assert "H5AN8G6NCJR-VKC" in found


def test_normalize() -> None:
    assert normalize_pn(" k4a8g165wc-bctd ") == "K4A8G165WC-BCTD"
