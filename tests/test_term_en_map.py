"""English highlight equivalents for foreign matched_terms."""

from __future__ import annotations

from src.term_en_map import (
    english_equivalents_for_term,
    matched_terms_en,
    parse_matched_terms,
)


def test_parse_matched_terms_umc():
    raw = "product:約當晶圓; topic:產能利用率; dir:產能滿載"
    assert parse_matched_terms(raw) == ["約當晶圓", "產能利用率", "產能滿載"]


def test_umc_chinese_terms_map_to_en_bank():
    excerpt = (
        "The overall capacity utilization rate is 92%, with several "
        "12-inch crystal mills full"
    )
    ens = matched_terms_en(
        "product:約當晶圓; topic:產能利用率; dir:產能滿載",
        excerpt_en=excerpt,
    )
    # Only phrases that occur in excerpt_en (so Show English can paint marks).
    assert ens
    assert all(t.lower() in excerpt.lower() for t in ens)
    assert "capacity utilization rate" in ens or "capacity utilization" in ens
    assert "capacity" in ens
    # Argos / gloss hits for 約當晶圓 / 產能滿載
    assert "crystal" in ens or "crystals" in ens
    assert "full" in ens


def test_cninfo_shortage_maps():
    ens = english_equivalents_for_term("供不应求")
    assert any(t in ens for t in ("shortage", "supply shortage", "undersupply"))


def test_latin_product_codes_pass_through():
    assert english_equivalents_for_term("DRAM") == ["DRAM"]
    assert english_equivalents_for_term("LPDDR4X") == ["LPDDR4X"]
    ens = matched_terms_en("product:DRAM; topic:供不应求; dir:供不应求")
    assert "DRAM" in ens
    assert "shortage" in ens or "supply shortage" in ens
