"""Load curated ATS board configs for the DRAM ecosystem."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dram_signals import config


@dataclass(frozen=True)
class Board:
    """One public ATS job board to scrape."""

    provider: str  # "greenhouse" | "lever" | "workday"
    token: str
    company_name: str
    notes: str = ""
    cxs_jobs_url: Optional[str] = None  # Workday CXS jobs endpoint


def default_boards_path() -> Path:
    override = os.environ.get("DRAM_SIGNALS_BOARDS_FILE")
    if override:
        return Path(override)
    return config.ROOT_DIR / "data" / "job_boards.json"


def load_boards(path: Path | None = None) -> list[Board]:
    boards_path = path or default_boards_path()
    raw: dict[str, Any] = json.loads(boards_path.read_text(encoding="utf-8"))
    boards: list[Board] = []
    for provider in ("greenhouse", "lever", "workday"):
        for entry in raw.get(provider, []):
            boards.append(
                Board(
                    provider=provider,
                    token=entry["token"],
                    company_name=entry["company_name"],
                    notes=entry.get("notes", ""),
                    cxs_jobs_url=entry.get("cxs_jobs_url"),
                )
            )
    return boards


def boards_for(provider: str, path: Path | None = None) -> list[Board]:
    return [b for b in load_boards(path) if b.provider == provider]
