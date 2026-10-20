"""Unit tests for 10-K section splitting."""

from __future__ import annotations

from src.sections import (
    SECTION_BUSINESS,
    SECTION_MDNA,
    SECTION_OTHER,
    SECTION_RISK_FACTORS,
    split_10k_sections,
)

SAMPLE = """
UNITED STATES SECURITIES AND EXCHANGE COMMISSION

TABLE OF CONTENTS
Item 1. Business ............................................. 1
Item 1A. Risk Factors ........................................ 5
Item 7. Management's Discussion and Analysis ................. 20

PART I

Item 1. Business
We make DRAM and NAND memory products for data center customers.

Item 1A. Risk Factors
In addition to the factors discussed elsewhere, supply may be constrained.

Volatility in average selling prices may adversely affect our business.

Item 1B. Unresolved Staff Comments
None.

Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
AI-driven demand growth is outpacing industry supply this year.

Item 7A. Quantitative and Qualitative Disclosures About Market Risk
Interest rate risk is limited.

Item 8. Financial Statements and Supplementary Data
See consolidated statements.
"""


def test_split_10k_sections_extracts_items():
    sections = split_10k_sections(SAMPLE)
    assert SECTION_OTHER in sections
    assert "We make DRAM" in sections[SECTION_BUSINESS]
    assert "Risk Factors" in sections[SECTION_RISK_FACTORS]
    assert "supply may be constrained" in sections[SECTION_RISK_FACTORS]
    assert "Item 1B" not in sections[SECTION_RISK_FACTORS]
    assert "outpacing industry supply" in sections[SECTION_MDNA]
    assert "Interest rate risk" not in sections[SECTION_MDNA]
    # Full text preserved under other
    assert "UNITED STATES SECURITIES" in sections[SECTION_OTHER]
    assert sections[SECTION_OTHER].strip() == SAMPLE.strip()
