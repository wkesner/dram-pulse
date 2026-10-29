#!/usr/bin/env python3
"""Simulate three DRAM electronic-component brokers competing for virtual P&L.

Co-located with ``scripts/simulate_votes.py``. Brokers share a seed DRAM
catalog and a partially overlapping customer universe; each tries to buy under
market and sell at or above market.

Usage::

    python3 -m scripts.simulate_brokers
    python3 -m scripts.simulate_brokers --days 60 --seed 7 --live
    python3 -m scripts.simulate_brokers --no-live --json
    python3 -m dram_signals.broker_sim --days 30 --seed 42

GUI::

    uvicorn app.server:app --reload --port 8000
    # http://127.0.0.1:8000/brokers.html
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dram_signals.broker_sim.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
