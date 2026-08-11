"""Supabase access-token verification (JWT, ES256 via JWKS).

The frontend logs in through Supabase and receives an ``access_token`` (a
JWT signed with the project's **JWT Signing Keys** — ECDSA P-256 / ES256).
Business requests carry it as ``Authorization: Bearer <token>``; the backend
verifies the signature against the project's published signing keys and
takes the real user id from the token's ``sub`` claim — it never trusts a
``user_id`` sent by the client.

The signing keys are fetched from the project's JWKS endpoint
(``/auth/v1/.well-known/jwks.json``) and looked up by the token's ``kid``,
so key rotation needs no code change. The project URL is read from ``.env``
via ``AuthConfig`` — never committed or logged.
"""

import logging

import jwt
from jwt import PyJWKClient

from ainote.config.auth_config import get_auth_config

logger = logging.getLogger(__name__)

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient | None:
    """Build (and cache) the JWKS client for the configured Supabase project.

    Returns ``None`` when no Supabase URL is configured (auth disabled).
    ``cache_keys=True`` reuses the fetched key set between requests.
    """
    global _jwks_client
    url = get_auth_config().jwks_url
    if not url:
        return None
    if _jwks_client is None:
        _jwks_client = PyJWKClient(url, cache_keys=True)
    return _jwks_client


def reset_jwks_client() -> None:
    """Clear the cached JWKS client (test hook)."""
    global _jwks_client
    _jwks_client = None


def verify_supabase_token(token: str) -> str | None:
    """Verify a Supabase access token and return the user id (``sub`` claim).

    Verifies the ES256 signature with the signing key matching the token's
    ``kid`` (fetched from the project's JWKS). Returns ``None`` when the
    token is malformed, signed with the wrong key, expired, or when the
    signing keys cannot be fetched — the caller decides whether to reject
    (401) or fall back. Fail-closed: a fetch error never falls back to
    ``"default"``; the dependency turns it into a 401.

    ``exp`` is required: a token without an expiry is treated as invalid.
    ``aud`` must be ``"authenticated"`` (the audience Supabase sets on
    login-issued access tokens) — service-role / anon tokens are rejected.
    """
    client = _get_jwks_client()
    if client is None:
        logger.debug("No Supabase URL configured — cannot verify token")
        return None
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Supabase token verification failed: %s", exc)
        return None
    except Exception as exc:  # JWKS fetch / network failure
        logger.warning("Supabase JWKS fetch failed: %s", exc)
        return None
    return payload["sub"]
