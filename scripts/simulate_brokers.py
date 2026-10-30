#!/usr/bin/env python3
"""Simulate DRAM electronic-component brokers competing for virtual P&L.

Co-located with ``scripts/simulate_votes.py``. Baseline Apex / Harbor / Summit
plus optional scored twins (same personality, faster memory-score access).

Usage::

    python3 -m scripts.simulate_brokers
    python3 -m scripts.simulate_brokers --days 60 --seed 7 --live
    python3 -m scripts.simulate_brokers --baseline-only --no-live
    python3 -m scripts.simulate_brokers --score-lag 1 --json

GUI (port 8010 — avoids clash with the feed on 8000)::

    uvicorn app.server:app --host 0.0.0.0 --port 8010
    # http://127.0.0.1:8010/brokers.html
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
