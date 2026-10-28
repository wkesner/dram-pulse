"""Load gitignored `.env` into os.environ (without printing secrets)."""

from __future__ import annotations

import os
from pathlib import Path

from src.fetch_edgar import REPO_ROOT


def load_dotenv(path: Path | None = None) -> Path | None:
    """Parse KEY=VALUE lines into os.environ if the key is not already set."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return env_path


def require_dart_api_key() -> str:
    load_dotenv()
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "DART_API_KEY is required. Put the 40-character OpenDART key in "
            ".env (see .env.example). Never commit .env."
        )
    if not (35 <= len(key) <= 45):
        raise SystemExit(
            f"DART_API_KEY length looks wrong ({len(key)} chars; expected ~40)."
        )
    return key
