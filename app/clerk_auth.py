"""Clerk session JWT verification for multi-org mode."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import jwt
from jwt import PyJWKClient

from src.envutil import load_dotenv


@dataclass(frozen=True)
class ClerkSession:
    user_id: str
    org_id: str
    org_role: str
    org_slug: str
    display_name: str
    raw: dict[str, Any]


class ClerkConfigError(RuntimeError):
    pass


def auth_mode() -> str:
    load_dotenv()
    mode = (os.environ.get("AUTH_MODE") or "single").strip().lower()
    return mode if mode in {"single", "clerk"} else "single"


def clerk_publishable_key() -> str:
    load_dotenv()
    return (os.environ.get("CLERK_PUBLISHABLE_KEY") or "").strip()


def clerk_secret_key() -> str:
    load_dotenv()
    return (os.environ.get("CLERK_SECRET_KEY") or "").strip()


def clerk_issuer() -> str:
    """
    JWT issuer, e.g. https://verb-noun-00.clerk.accounts.dev
    Prefer CLERK_ISSUER; else derive from CLERK_JWT_ISSUER; else empty.
    """
    load_dotenv()
    return (
        (os.environ.get("CLERK_ISSUER") or os.environ.get("CLERK_JWT_ISSUER") or "")
        .strip()
        .rstrip("/")
    )


def clerk_configured() -> bool:
    return bool(clerk_publishable_key() and clerk_secret_key() and clerk_issuer())


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(issuer: str) -> PyJWKClient:
    if issuer not in _jwks_clients:
        _jwks_clients[issuer] = PyJWKClient(f"{issuer}/.well-known/jwks.json")
    return _jwks_clients[issuer]


def verify_clerk_session_token(token: str) -> ClerkSession:
    """Validate a Clerk session JWT and require an active organization."""
    issuer = clerk_issuer()
    if not issuer:
        raise ClerkConfigError(
            "CLERK_ISSUER is required (Frontend API URL from Clerk Dashboard)"
        )
    if not token:
        raise PermissionError("missing bearer token")

    try:
        key = _jwks_clients.get(issuer) or _jwks_client(issuer)
        signing_key = key.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp", "iss", "sub"]},
        )
    except Exception as exc:  # noqa: BLE001
        raise PermissionError(f"invalid session token: {exc}") from exc

    org_id = str(claims.get("org_id") or claims.get("o", {}).get("id") or "").strip()
    if not org_id:
        # Newer Clerk tokens nest org under "o"
        o = claims.get("o") or {}
        if isinstance(o, dict):
            org_id = str(o.get("id") or "").strip()
    if not org_id:
        raise PermissionError(
            "no active organization — create or select an org in Clerk"
        )

    org_role = str(
        claims.get("org_role")
        or (claims.get("o") or {}).get("rol")
        or (claims.get("o") or {}).get("role")
        or ""
    )
    org_slug = str(claims.get("org_slug") or (claims.get("o") or {}).get("slg") or "")
    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise PermissionError("token missing sub")

    name = (
        str(claims.get("name") or "").strip()
        or str(claims.get("email") or "").strip()
        or user_id
    )
    return ClerkSession(
        user_id=user_id,
        org_id=org_id,
        org_role=org_role,
        org_slug=org_slug,
        display_name=name,
        raw=claims,
    )


SessionResolver = Callable[[str | None], ClerkSession]


def make_test_session(
    *,
    user_id: str,
    org_id: str,
    display_name: str = "",
    org_role: str = "admin",
) -> ClerkSession:
    return ClerkSession(
        user_id=user_id,
        org_id=org_id,
        org_role=org_role,
        org_slug="",
        display_name=display_name or user_id,
        raw={"sub": user_id, "org_id": org_id, "iat": int(time.time())},
    )
