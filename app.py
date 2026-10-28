"""Memory Signals feed — Milestone 4 Streamlit UI.

Run:
  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from src.feed_data import (
    append_vote,
    family_table,
    filter_signals,
    highlight_excerpt,
    load_sightings,
    load_signals,
    load_votes,
    lookback_label,
    order_feed,
    sightings_for_signal,
    vote_counts_for_signal,
)

st.set_page_config(page_title="Memory Signals", layout="wide")

ALL = "(all)"


def _unique(values: list[str]) -> list[str]:
    return sorted({v for v in values if v})


def _ensure_state() -> None:
    st.session_state.setdefault("feed_index", 0)
    st.session_state.setdefault("filter_family", ALL)
    st.session_state.setdefault("active_tab", "Feed")


def main() -> None:
    _ensure_state()
    signals = load_signals()
    sightings = load_sightings()
    votes = load_votes()
    window = lookback_label(signals)

    st.title("Memory Signals")
    st.caption(f"Qualitative memory supply signals · {window}")

    # --- Sidebar ---
    with st.sidebar:
        st.header("User")
        user = st.text_input(
            "Your name",
            value=st.session_state.get("user_name", ""),
            help="Required before voting. Typed name only — no accounts.",
        ).strip()
        st.session_state["user_name"] = user
        if not user:
            st.warning("Enter your name to vote.")

        st.header("Filters")
        categories = [ALL] + _unique([r.get("product_category", "") for r in signals])
        families = [ALL] + _unique([r.get("product_family", "") for r in signals])
        directions = [ALL] + _unique([r.get("supply_direction", "") for r in signals])
        companies = [ALL] + _unique([r.get("company", "") for r in signals])
        roles = [ALL] + _unique([r.get("role", "") for r in signals])
        sources = [ALL] + _unique([r.get("source", "") for r in signals])

        # Family filter can be set from Family view clicks.
        if st.session_state["filter_family"] not in families:
            st.session_state["filter_family"] = ALL
        fam_index = families.index(st.session_state["filter_family"])

        category = st.selectbox("Product category", categories, key="filter_category")
        family = st.selectbox(
            "Product family",
            families,
            index=fam_index,
            key="filter_family_box",
        )
        # Keep session filter in sync with the selectbox.
        st.session_state["filter_family"] = family

        direction = st.selectbox("Supply direction", directions, key="filter_direction")
        company = st.selectbox("Company", companies, key="filter_company")
        role = st.selectbox("Role", roles, key="filter_role")
        source = st.selectbox("Source", sources, key="filter_source")

        if st.button("Clear family filter"):
            st.session_state["filter_family"] = ALL
            st.session_state["feed_index"] = 0
            st.rerun()

    filtered = filter_signals(
        signals,
        product_category=category,
        product_family=st.session_state["filter_family"],
        supply_direction=direction,
        company=company,
        role=role,
        source=source,
    )
    ordered = order_feed(filtered, user, votes)

    feed_tab, family_tab = st.tabs(["Feed", "Family view"])

    # --- Feed ---
    with feed_tab:
        if not ordered:
            st.info("No signals match the current filters.")
        else:
            if st.session_state["feed_index"] >= len(ordered):
                st.session_state["feed_index"] = 0
            idx = st.session_state["feed_index"]
            signal = ordered[idx]
            _render_card(signal, votes, user, idx, len(ordered), sightings)

    # --- Family view ---
    with family_tab:
        st.subheader("Family view")
        st.caption(
            f"Plain counts by product family · {window}. "
            "Click a family to filter the Feed tab."
        )
        # Family table uses the same filters except family itself (show all families
        # within the other active filters).
        family_source = filter_signals(
            signals,
            product_category=category,
            product_family=None,
            supply_direction=direction,
            company=company,
            role=role,
            source=source,
        )
        table = family_table(family_source)
        if not table:
            st.info("No signals to summarise.")
        else:
            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "product_family": "Family",
                    "product_category": "Category",
                    "tightening": "Tightening",
                    "easing": "Easing",
                    "stable": "Stable",
                    "unclear": "Unclear",
                    "total": "Total",
                    "supplier_signals": "Supplier",
                    "buyer_signals": "Buyer",
                    "latest_filing_date": "Latest filing",
                },
            )
            st.write("Filter feed to a family:")
            cols = st.columns(min(4, max(1, len(table))))
            for i, row in enumerate(table):
                col = cols[i % len(cols)]
                label = f"{row['product_family']} ({row['total']})"
                if col.button(label, key=f"fam_btn_{row['product_family']}"):
                    st.session_state["filter_family"] = row["product_family"]
                    # Reset the selectbox widget so it picks up the new family.
                    st.session_state.pop("filter_family_box", None)
                    st.session_state["feed_index"] = 0
                    st.rerun()


def _render_card(
    signal: dict[str, str],
    votes: list[dict[str, str]],
    user: str,
    idx: int,
    total: int,
    sightings: list[dict[str, str]],
) -> None:
    sid = signal["signal_id"]
    up, down = vote_counts_for_signal(sid, votes)
    related = sightings_for_signal(sid, sightings)
    times_seen = int(signal.get("times_seen") or len(related) or 1)
    last_seen = signal.get("last_seen") or signal.get("filing_date") or ""

    st.markdown(f"**Card {idx + 1} of {total}**")
    st.markdown(
        f"### {signal.get('company', '')} "
        f"<span style='font-size:0.7em;font-weight:500'>({signal.get('role', '')})</span>",
        unsafe_allow_html=True,
    )
    meta_bits = [
        f"Form **{signal.get('form', '')}**",
        f"First seen **{signal.get('first_seen') or signal.get('filing_date', '')}**",
        f"Family **{signal.get('product_family', '')}**",
        f"Direction **{signal.get('supply_direction', '')}**",
        f"Sentiment **{signal.get('sentiment', '')}**",
    ]
    if (signal.get("carried_from_prior_year") or "").lower() == "true":
        meta_bits.append("carried from prior year")
    st.markdown(" · ".join(meta_bits))

    html = highlight_excerpt(signal.get("excerpt") or "", signal.get("matched_terms") or "")
    st.markdown(
        f"<div style='padding:1rem;border-left:4px solid #444;background:#f7f7f7;"
        f"color:#111;line-height:1.45'>{html}</div>",
        unsafe_allow_html=True,
    )
    matched = signal.get("matched_terms") or ""
    if matched:
        st.caption(f"Matched: {matched}")

    with st.expander(f"Seen in {times_seen} filings, latest {last_seen}", expanded=False):
        if not related:
            source_url = signal.get("source_url") or ""
            if source_url:
                st.markdown(
                    f"- {signal.get('form', '')} · {signal.get('filing_date', '')} · "
                    f"[{signal.get('filing_id', 'filing')}]({source_url})"
                )
            else:
                st.caption("No sighting rows loaded.")
        else:
            for s in related:
                label = (
                    f"{s.get('form', '')} · {s.get('filing_date', '')} · "
                    f"{s.get('section', '')} · match={s.get('match', '')}"
                )
                url = s.get("source_url") or ""
                if url:
                    st.markdown(f"- [{label}]({url})")
                else:
                    st.markdown(f"- {label}")

    source_url = signal.get("source_url") or ""
    if source_url:
        st.markdown(f"[Open source filing]({source_url})")

    st.markdown(f"**Votes:** 👍 {up} · 👎 {down}")

    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 2])
    can_vote = bool(user)

    def _cast(vote: int) -> None:
        if not user:
            st.warning("Enter your name in the sidebar before voting.")
            return
        append_vote(sid, user, vote)
        st.session_state["feed_index"] = min(idx + 1, max(total - 1, 0))
        st.rerun()

    if c1.button("👍", disabled=not can_vote, use_container_width=True):
        _cast(1)
    if c2.button("👎", disabled=not can_vote, use_container_width=True):
        _cast(-1)
    if c3.button("Skip", use_container_width=True):
        st.session_state["feed_index"] = min(idx + 1, max(total - 1, 0))
        st.rerun()
    if c4.button("← Prev", use_container_width=True):
        st.session_state["feed_index"] = max(idx - 1, 0)
        st.rerun()


if __name__ == "__main__":
    main()
