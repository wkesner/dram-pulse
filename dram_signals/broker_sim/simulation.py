"""Day-step broker market simulation with inventory and P&L tracking.

Each simulation day:

1. Latent **memory score** updates (shortage / balanced / glut lean).
2. Shared market mid for every SKU drifts (noise + mean reversion + score bias).
3. Each broker sees buy offers under market and sell demand at/above market.
4. Baseline brokers trade on strategy alone; scored twins also nudge thresholds
   from a **lagged** memory-score reading (faster info than having none).
5. Cash / inventory / realized P&L update (FIFO lots on sells).

Invariants enforced in code (and covered by tests):

* Buys only execute when ``offer_price < market_price``.
* Sells only execute when ``bid_price >= market_price``.
* Inventory and cash never go negative from a fill.
* Customer membership is fixed for the run (seeded overlap); scored twins
  inherit their parent's book for a fair comparison.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dram_signals.broker_sim.catalog import Product, load_products_from_seed, product_index
from dram_signals.broker_sim.customers import CustomerBook, build_customer_universe
from dram_signals.broker_sim.live_prices import (
    DEFAULT_CACHE_PATH,
    PriceBundle,
    resolve_market_prices,
)
from dram_signals.broker_sim.memory_score import (
    ScoreReading,
    drift_bias_from_score,
    generate_score_path,
    nudge_from_score,
    observe_score,
    regime_label,
    true_score_on,
)
from dram_signals.broker_sim.strategies import (
    BASELINE_SPECS,
    DEFAULT_SCORED_LAG_DAYS,
    BrokerSpec,
    all_broker_specs,
)


@dataclass
class Lot:
    qty: int
    unit_cost: float


@dataclass
class BrokerState:
    spec: BrokerSpec
    cash: float
    inventory: dict[str, list[Lot]] = field(default_factory=dict)
    units_bought: int = 0
    units_sold: int = 0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    realized_pnl: float = 0.0
    buy_fills: int = 0
    sell_fills: int = 0
    buy_rejects: int = 0
    sell_rejects: int = 0
    last_observed_score: Optional[float] = None

    def units_on_hand(self, sku: Optional[str] = None) -> int:
        if sku is not None:
            return sum(lot.qty for lot in self.inventory.get(sku, []))
        return sum(lot.qty for lots in self.inventory.values() for lot in lots)

    def inventory_cost(self) -> float:
        return sum(
            lot.qty * lot.unit_cost
            for lots in self.inventory.values()
            for lot in lots
        )

    def equity(self, market: dict[str, float]) -> float:
        """Cash + mark-to-market inventory at current market mids."""
        mtm = 0.0
        for sku, lots in self.inventory.items():
            px = market.get(sku, 0.0)
            mtm += sum(lot.qty for lot in lots) * px
        return self.cash + mtm


@dataclass(frozen=True)
class BuyOffer:
    sku: str
    qty: int
    offer_price: float
    market_price: float


@dataclass(frozen=True)
class SellBid:
    sku: str
    qty: int
    bid_price: float
    market_price: float
    customer: str


@dataclass
class SimulationResult:
    seed: int
    days: int
    starting_cash: float
    products: list[Product]
    book: CustomerBook
    brokers: dict[str, BrokerState]
    final_market: dict[str, float]
    day_equity: list[dict[str, float]] = field(default_factory=list)
    history: list[dict[str, object]] = field(default_factory=list)
    price_bundle: Optional[PriceBundle] = None
    score_path: list[ScoreReading] = field(default_factory=list)
    include_scored: bool = True
    score_lag_days: int = DEFAULT_SCORED_LAG_DAYS

    def summary(self) -> dict[str, object]:
        baseline_ids = [
            bid
            for bid, state in self.brokers.items()
            if state.spec.cohort == "baseline"
        ]
        overlap = self.book.overlap_stats(baseline_ids or None)
        broker_rows = []
        for bid, state in self.brokers.items():
            broker_rows.append(
                {
                    "broker_id": bid,
                    "name": state.spec.name,
                    "personality": state.spec.personality,
                    "cohort": state.spec.cohort,
                    "parent_id": state.spec.parent_id,
                    "has_memory_score": state.spec.has_memory_score,
                    "score_lag_days": state.spec.score_lag_days
                    if state.spec.has_memory_score
                    else None,
                    "cash": round(state.cash, 2),
                    "inventory_units": state.units_on_hand(),
                    "inventory_cost": round(state.inventory_cost(), 2),
                    "equity": round(state.equity(self.final_market), 2),
                    "realized_pnl": round(state.realized_pnl, 2),
                    "pnl_vs_start": round(
                        state.equity(self.final_market) - self.starting_cash, 2
                    ),
                    "units_bought": state.units_bought,
                    "units_sold": state.units_sold,
                    "buy_fills": state.buy_fills,
                    "sell_fills": state.sell_fills,
                    "customers": len(self.book.customers_for(bid)),
                }
            )
        broker_rows.sort(key=lambda r: r["equity"], reverse=True)
        prices = self.price_bundle.as_dict() if self.price_bundle else None
        comparisons = []
        by_id = {r["broker_id"]: r for r in broker_rows}
        for row in broker_rows:
            parent = row.get("parent_id")
            if not parent or parent not in by_id:
                continue
            base = by_id[parent]
            comparisons.append(
                {
                    "baseline_id": parent,
                    "scored_id": row["broker_id"],
                    "baseline_name": base["name"],
                    "scored_name": row["name"],
                    "equity_delta": round(row["equity"] - base["equity"], 2),
                    "pnl_delta": round(row["pnl_vs_start"] - base["pnl_vs_start"], 2),
                    "scored_wins": row["equity"] > base["equity"],
                }
            )
        return {
            "seed": self.seed,
            "days": self.days,
            "starting_cash": self.starting_cash,
            "skus": len(self.products),
            "overlap": overlap,
            "brokers": broker_rows,
            "winner": broker_rows[0]["name"] if broker_rows else None,
            "prices": prices,
            "history": self.history,
            "day_equity": self.day_equity,
            "include_scored": self.include_scored,
            "score_lag_days": self.score_lag_days,
            "score_model": {
                "description": (
                    "Simple 0–100 memory timing score; scored twins observe with "
                    f"lag={self.score_lag_days} day(s); baselines unscored."
                ),
                "shortage_threshold": 60,
                "glut_threshold": 40,
            },
            "pair_comparisons": comparisons,
            "score_path": [
                {
                    "day": r.day,
                    "true_score": r.true_score,
                    "regime": regime_label(r.true_score),
                    "event": r.event,
                }
                for r in self.score_path
            ],
        }


def _drift_markets(
    market: dict[str, float],
    bases: dict[str, float],
    rng: random.Random,
    *,
    daily_vol: float = 0.012,
    reversion: float = 0.08,
    score_bias: float = 0.0,
) -> None:
    for sku, px in list(market.items()):
        shock = rng.gauss(0.0, daily_vol)
        pull = reversion * (bases[sku] - px) / bases[sku]
        market[sku] = max(0.05, round(px * (1.0 + shock + pull + score_bias), 4))


def _gen_buy_offers(
    *,
    products: list[Product],
    market: dict[str, float],
    rng: random.Random,
    n_offers: int,
) -> list[BuyOffer]:
    offers: list[BuyOffer] = []
    if not products:
        return offers
    for _ in range(n_offers):
        product = rng.choice(products)
        mid = market[product.sku]
        discount = rng.uniform(0.02, 0.18)
        price = round(mid * (1.0 - discount), 4)
        assert price < mid
        qty = rng.choice([100, 250, 500, 1000, 2000, 5000])
        offers.append(
            BuyOffer(
                sku=product.sku,
                qty=qty,
                offer_price=price,
                market_price=mid,
            )
        )
    return offers


def _gen_sell_bids(
    *,
    products: list[Product],
    market: dict[str, float],
    customers: frozenset[str],
    rng: random.Random,
    n_bids: int,
) -> list[SellBid]:
    bids: list[SellBid] = []
    if not products or not customers:
        return bids
    cust_list = sorted(customers)
    for _ in range(n_bids):
        product = rng.choice(products)
        mid = market[product.sku]
        premium = rng.uniform(0.0, 0.10)
        price = round(mid * (1.0 + premium), 4)
        assert price >= mid
        qty = rng.choice([50, 100, 250, 500, 1000, 2000])
        bids.append(
            SellBid(
                sku=product.sku,
                qty=qty,
                bid_price=price,
                market_price=mid,
                customer=rng.choice(cust_list),
            )
        )
    return bids


def _should_buy(
    spec: BrokerSpec,
    offer: BuyOffer,
    product: Product,
    state: BrokerState,
    *,
    min_buy_discount: float,
    inventory_target: int,
) -> bool:
    if offer.offer_price >= offer.market_price:
        return False
    discount = 1.0 - (offer.offer_price / offer.market_price)
    if discount < min_buy_discount:
        return False
    if state.units_on_hand() >= inventory_target:
        return False
    if spec.prefer_active and product.lifecycle_status.lower() not in {
        "active",
        "nrnd",
    }:
        if discount < min_buy_discount + 0.08:
            return False
    max_spend = state.cash * spec.max_cash_fraction_per_buy
    if offer.offer_price * offer.qty > max_spend:
        return False
    if offer.offer_price * offer.qty > state.cash:
        return False
    return True


def _fill_buy(state: BrokerState, offer: BuyOffer) -> bool:
    """Execute a buy under market. Returns True if filled."""
    if offer.offer_price >= offer.market_price:
        state.buy_rejects += 1
        return False
    cost = offer.offer_price * offer.qty
    if cost > state.cash or offer.qty <= 0:
        state.buy_rejects += 1
        return False
    state.cash -= cost
    state.inventory.setdefault(offer.sku, []).append(
        Lot(qty=offer.qty, unit_cost=offer.offer_price)
    )
    state.units_bought += offer.qty
    state.buy_notional += cost
    state.buy_fills += 1
    return True


def _should_sell(
    bid: SellBid,
    state: BrokerState,
    *,
    min_sell_premium: float,
) -> int:
    """Return qty to sell (0 if skip)."""
    if bid.bid_price < bid.market_price:
        return 0
    premium = (bid.bid_price / bid.market_price) - 1.0
    if premium < min_sell_premium:
        return 0
    available = state.units_on_hand(bid.sku)
    if available <= 0:
        return 0
    return min(available, bid.qty)


def _fill_sell(state: BrokerState, bid: SellBid, qty: int) -> bool:
    """Execute a sell at/above market using FIFO lots. Returns True if filled."""
    if qty <= 0 or bid.bid_price < bid.market_price:
        state.sell_rejects += 1
        return False
    lots = state.inventory.get(bid.sku) or []
    remaining = qty
    cost_basis = 0.0
    while remaining > 0 and lots:
        lot = lots[0]
        take = min(lot.qty, remaining)
        cost_basis += take * lot.unit_cost
        lot.qty -= take
        remaining -= take
        if lot.qty == 0:
            lots.pop(0)
    filled = qty - remaining
    if filled <= 0:
        state.sell_rejects += 1
        return False
    proceeds = filled * bid.bid_price
    state.cash += proceeds
    state.units_sold += filled
    state.sell_notional += proceeds
    state.realized_pnl += proceeds - cost_basis
    state.sell_fills += 1
    if not lots:
        state.inventory.pop(bid.sku, None)
    return True


def _build_books_with_twin_copy(
    specs: tuple[BrokerSpec, ...],
    rng: random.Random,
    companies_path: Optional[Path],
) -> CustomerBook:
    """Overlap books for baselines; scored twins inherit their parent's customers."""
    baselines = [s for s in specs if s.cohort == "baseline"]
    base_ids = [s.broker_id for s in baselines]
    affinities = {s.broker_id: s.customer_affinity for s in baselines}
    book = build_customer_universe(
        base_ids,
        rng=rng,
        companies_path=companies_path,
        affinities=affinities,
    )
    membership = dict(book.membership)
    for spec in specs:
        if spec.parent_id:
            membership[spec.broker_id] = membership.get(spec.parent_id, frozenset())
    return CustomerBook(customers=book.customers, membership=membership)


def run_simulation(
    *,
    days: int = 30,
    seed: int = 42,
    starting_cash: float = 250_000.0,
    seed_catalog: Optional[Path] = None,
    companies_path: Optional[Path] = None,
    offers_per_day: int = 6,
    bids_per_day: int = 8,
    broker_specs: Optional[tuple[BrokerSpec, ...]] = None,
    include_scored: bool = True,
    score_lag_days: int = DEFAULT_SCORED_LAG_DAYS,
    prefer_live_prices: bool = True,
    price_bundle: Optional[PriceBundle] = None,
    price_cache_path: Optional[Path] = None,
) -> SimulationResult:
    """Run N simulation days for baseline brokers and optional scored twins.

    Starting cash defaults to **$250,000** per broker (confirmed).
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    specs = broker_specs or all_broker_specs(
        include_scored=include_scored,
        score_lag_days=score_lag_days,
    )
    rng = random.Random(seed)
    products = load_products_from_seed(seed_catalog)
    by_sku = product_index(products)

    book = _build_books_with_twin_copy(specs, rng, companies_path)

    if price_bundle is None:
        price_bundle = resolve_market_prices(
            products,
            prefer_live=prefer_live_prices,
            cache_path=price_cache_path or DEFAULT_CACHE_PATH,
        )

    market = dict(price_bundle.prices)
    for p in products:
        market.setdefault(p.sku, p.base_market_price)
    bases = dict(market)
    brokers = {
        s.broker_id: BrokerState(spec=s, cash=float(starting_cash))
        for s in specs
    }
    score_path = generate_score_path(days, rng)
    day_equity: list[dict[str, float]] = []
    history: list[dict[str, object]] = []

    for day_idx in range(days):
        day_num = day_idx + 1
        true_score = true_score_on(score_path, day_num)
        bias = drift_bias_from_score(true_score)
        _drift_markets(market, bases, rng, score_bias=bias)

        for spec in specs:
            state = brokers[spec.broker_id]
            observed = observe_score(
                score_path,
                day=day_num,
                has_memory_score=spec.has_memory_score,
                score_lag_days=spec.score_lag_days,
            )
            state.last_observed_score = observed
            nudge = nudge_from_score(
                min_buy_discount=spec.min_buy_discount,
                min_sell_premium=spec.min_sell_premium,
                observed=observed,
            )
            inv_target = int(round(spec.inventory_target_units * nudge.inventory_headroom))

            offers = _gen_buy_offers(
                products=products,
                market=market,
                rng=rng,
                n_offers=offers_per_day,
            )
            for offer in offers:
                product = by_sku[offer.sku]
                if _should_buy(
                    spec,
                    offer,
                    product,
                    state,
                    min_buy_discount=nudge.min_buy_discount,
                    inventory_target=inv_target,
                ):
                    _fill_buy(state, offer)
                else:
                    state.buy_rejects += 1

            bids = _gen_sell_bids(
                products=products,
                market=market,
                customers=book.customers_for(spec.broker_id),
                rng=rng,
                n_bids=bids_per_day,
            )
            for bid in bids:
                qty = _should_sell(
                    bid,
                    state,
                    min_sell_premium=nudge.min_sell_premium,
                )
                if qty:
                    _fill_sell(state, bid, qty)
                else:
                    state.sell_rejects += 1

        equity_row: dict[str, float] = {"day": float(day_num)}
        hist_brokers: dict[str, dict[str, object]] = {}
        for bid, st in brokers.items():
            eq = round(st.equity(market), 2)
            equity_row[bid] = eq
            hist_brokers[bid] = {
                "equity": eq,
                "cash": round(st.cash, 2),
                "inventory_units": float(st.units_on_hand()),
                "inventory_cost": round(st.inventory_cost(), 2),
                "realized_pnl": round(st.realized_pnl, 2),
                "pnl_vs_start": round(eq - starting_cash, 2),
                "cohort": st.spec.cohort,
                "parent_id": st.spec.parent_id,
                "observed_score": st.last_observed_score,
            }
        day_equity.append(equity_row)
        history.append(
            {
                "day": day_num,
                "true_memory_score": true_score,
                "regime": regime_label(true_score),
                "brokers": hist_brokers,
            }
        )

    return SimulationResult(
        seed=seed,
        days=days,
        starting_cash=starting_cash,
        products=products,
        book=book,
        brokers=brokers,
        final_market=dict(market),
        day_equity=day_equity,
        history=history,
        price_bundle=price_bundle,
        score_path=score_path,
        include_scored=any(s.has_memory_score for s in specs),
        score_lag_days=score_lag_days,
    )


def format_summary(result: SimulationResult) -> str:
    """Human-readable multi-line summary for CLI output."""
    s = result.summary()
    overlap = s["overlap"]
    prices = s.get("prices") or {}
    lines = [
        f"Broker market simulation — {s['days']} days, seed={s['seed']}, "
        f"{s['skus']} SKUs, start cash=${s['starting_cash']:,.0f} each",
        f"Prices: {prices.get('source', 'unknown')}"
        + (f" — {prices.get('detail')}" if prices.get("detail") else ""),
    ]
    if prices.get("attribution"):
        lines.append(f"  attribution: {prices['attribution']}")
    if prices.get("license_note"):
        lines.append(f"  license: {prices['license_note']}")
    if prices.get("matched_skus") is not None:
        lines.append(
            f"  matched SKUs: {prices.get('matched_skus')}/"
            f"{prices.get('matched_skus', 0) + prices.get('unmatched_skus', 0)}"
        )
    if s.get("include_scored"):
        lines.append(
            f"Memory score: scored twins lag={s.get('score_lag_days')} day(s); "
            "baselines unscored"
        )
    lines.extend(
        [
            "",
            "Customer overlap (baseline books; twins inherit parent):",
            f"  universe={overlap['customers']}  exclusive={overlap['exclusive']}  "
            f"on_2={overlap['on_two_brokers']}  on_3={overlap['on_three_brokers']}  "
            f"overlap_rate={overlap['overlap_rate']:.1%}",
        ]
    )
    pairwise = overlap.get("pairwise_shared") or {}
    if pairwise:
        bits = ", ".join(f"{k}={v}" for k, v in sorted(pairwise.items()))
        lines.append(f"  pairwise shared: {bits}")
    lines.append("")
    lines.append(
        f"{'Broker':<36} {'Cohort':<9} {'Equity':>12} {'P&L':>10} "
        f"{'Cash':>12} {'Inv':>6} {'Cust':>5}"
    )
    lines.append("-" * 104)
    for row in s["brokers"]:
        lines.append(
            f"{row['name']:<36} {row['cohort']:<9} ${row['equity']:>10,.2f} "
            f"${row['pnl_vs_start']:>8,.2f} ${row['cash']:>10,.2f} "
            f"{row['inventory_units']:>6} {row['customers']:>5}"
        )
    comparisons = s.get("pair_comparisons") or []
    if comparisons:
        lines.append("")
        lines.append("Baseline vs scored twin (equity Δ = scored − baseline):")
        for c in comparisons:
            flag = "wins" if c["scored_wins"] else "trails"
            lines.append(
                f"  {c['scored_name']}: Δ equity ${c['equity_delta']:>8,.2f} "
                f"(Δ P&L ${c['pnl_delta']:>8,.2f}) — scored {flag}"
            )
    lines.append("")
    lines.append(f"Leader (mark-to-market equity): {s['winner']}")
    return "\n".join(lines)


# Re-export for tests that imported BROKER_SPECS from simulation historically.
BROKER_SPECS = BASELINE_SPECS
