"""Clerk session JWT verification for multi-org mode.

Follows Clerk's manual JWT / Express-backend patterns:
https://clerk.com/docs/backend-requests/manual-jwt
https://clerk.com/docs/expressjs/getting-started/quickstart
"""

from __future__ import annotations

import base64
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


def clerk_jwt_key() -> str:
    """Optional PEM/JWKS public key for networkless verify (CLERK_JWT_KEY)."""
    load_dotenv()
    return (os.environ.get("CLERK_JWT_KEY") or "").strip()


def derive_frontend_api_from_publishable_key(pk: str) -> str:
    """
    Mirror Clerk JS quickstart: decode the third segment of pk_test_/pk_live_.
    Returns hostname like upright-mouse-4959.clerk.accounts.dev
    """
    pk = (pk or "").strip()
    if not pk or "_" not in pk:
        return ""
    try:
        encoded = pk.split("_", 2)[2]
        pad = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + pad).decode("utf-8", errors="replace")
        host = decoded.rstrip("$").strip()
        if host.startswith("https://"):
            return host[len("https://") :].rstrip("/")
        if host.startswith("http://"):
            return host[len("http://") :].rstrip("/")
        return host.rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def clerk_issuer() -> str:
    """
    JWT issuer / Frontend API URL, e.g. https://verb-noun-00.clerk.accounts.dev
    Preference: CLERK_ISSUER → CLERK_JWT_ISSUER → derive from publishable key.
    """
    load_dotenv()
    explicit = (
        (os.environ.get("CLERK_ISSUER") or os.environ.get("CLERK_JWT_ISSUER") or "")
        .strip()
        .rstrip("/")
    )
    if explicit:
        return explicit
    host = derive_frontend_api_from_publishable_key(clerk_publishable_key())
    return f"https://{host}" if host else ""


def clerk_authorized_parties() -> list[str]:
    """Comma-separated CLERK_AUTHORIZED_PARTIES for azp claim checks."""
    load_dotenv()
    raw = (os.environ.get("CLERK_AUTHORIZED_PARTIES") or "").strip()
    if not raw:
        return [
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ]
    return [p.strip() for p in raw.split(",") if p.strip()]


def clerk_configured() -> bool:
    return bool(clerk_publishable_key() and clerk_secret_key() and clerk_issuer())


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(issuer: str) -> PyJWKClient:
    # Cache JWKS in-process (lifespan=1h) so ~10 concurrent users do not
    # re-fetch Clerk keys on every request. No paid cache required.
    if issuer not in _jwks_clients:
        _jwks_clients[issuer] = PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            lifespan=3600,
        )
    return _jwks_clients[issuer]


def _decode_claims(token: str) -> dict[str, Any]:
    issuer = clerk_issuer()
    if not issuer:
        raise ClerkConfigError(
            "CLERK_ISSUER is required (or a valid CLERK_PUBLISHABLE_KEY to derive it)"
        )
    jwt_key = clerk_jwt_key()
    options = {
        "require": ["exp", "iss", "sub"],
        "verify_aud": False,  # Clerk session tokens typically omit aud
    }
    if jwt_key:
        return jwt.decode(
            token,
            jwt_key,
            algorithms=["RS256"],
            issuer=issuer,
            options=options,
        )
    key = _jwks_client(issuer)
    signing_key = key.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        options=options,
    )


def _org_id_from_claims(claims: dict[str, Any]) -> str:
    org_id = str(claims.get("org_id") or "").strip()
    if org_id:
        return org_id
    o = claims.get("o") or {}
    if isinstance(o, dict):
        return str(o.get("id") or "").strip()
    return ""


def verify_clerk_session_token(token: str) -> ClerkSession:
    """Validate a Clerk session JWT and require an active organization."""
    if not token:
        raise PermissionError("missing bearer token")

    try:
        claims = _decode_claims(token)
    except ClerkConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PermissionError(f"invalid session token: {exc}") from exc

    # Organizations without personal accounts may issue sts=pending
    sts = str(claims.get("sts") or "").strip().lower()
    if sts == "pending":
        raise PermissionError(
            "organization membership pending — create or select an organization"
        )

    azp = str(claims.get("azp") or "").strip()
    permitted = clerk_authorized_parties()
    if azp and permitted and azp not in permitted:
        raise PermissionError("invalid authorized party (azp)")

    org_id = _org_id_from_claims(claims)
    if not org_id:
        raise PermissionError(
            "no active organization — create or select an org in Clerk"
        )

    o = claims.get("o") if isinstance(claims.get("o"), dict) else {}
    org_role = str(
        claims.get("org_role") or o.get("rol") or o.get("role") or ""
    )
    org_slug = str(claims.get("org_slug") or o.get("slg") or "")
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
