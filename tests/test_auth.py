"""Tests for Supabase JWT auth (ES256 via JWKS).

The dependency layer resolves the caller's identity from
``Authorization: Bearer <access_token>`` — the backend never trusts a
``user_id`` supplied by the client. Supabase signs access tokens with ES256
(ECDSA P-256) using its JWT Signing Keys; the backend fetches the public
keys from the project's JWKS and verifies by ``kid``.

These tests exercise verification with real ES256 signatures: they generate
ECDSA keypairs and monkeypatch the JWKS-client seam, so no network is needed.
"""

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ainote.config.auth_config import reset_auth_config

PROJECT_URL = "https://example.supabase.co"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch):
    """Configure the Supabase URL and reset all caches for every test.

    ``get_auth_config()`` and the JWKS client are module-level singletons, so
    each test must reset them — otherwise an earlier test's cached value
    leaks.
    """
    from app.common.token_service import reset_jwks_client

    monkeypatch.setenv("SUPABASE_URL", PROJECT_URL)
    reset_auth_config()
    reset_jwks_client()
    yield
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    reset_auth_config()
    reset_jwks_client()


def _ec_keypair():
    """Generate an ECDSA P-256 keypair (PEM strings) like Supabase's keys."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def _make_token(
    priv_pem: str,
    sub: str,
    *,
    kid: str = "test-kid",
    expired: bool = False,
    aud: str = "authenticated",
) -> str:
    """Sign an ES256 JWT like Supabase does (sub = real user id).

    ``aud`` defaults to ``"authenticated"`` — the audience Supabase puts on
    login-issued access tokens. Passing a different value (e.g. ``"anon"`` /
    ``"service_role"``) lets tests assert those are rejected.
    """
    payload = {"sub": sub, "aud": aud, "exp": 0 if expired else 9_999_999_999}
    return jwt.encode(payload, priv_pem, algorithm="ES256", headers={"kid": kid})


def _patch_jwks_client(monkeypatch: pytest.MonkeyPatch, pub_pem: str):
    """Serve the given public key through the JWKS-client seam (no network)."""
    from app.common import token_service

    class _FakeKey:
        key = pub_pem

    class _FakeClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeKey()

    monkeypatch.setattr(token_service, "_get_jwks_client", lambda: _FakeClient())


# ── verify_supabase_token ─────────────────────────────────────────────


def test_verify_returns_sub(monkeypatch):
    from app.common.token_service import verify_supabase_token

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    assert verify_supabase_token(_make_token(priv, "user-123")) == "user-123"


def test_verify_rejects_wrong_key(monkeypatch):
    from app.common.token_service import verify_supabase_token

    priv, pub = _ec_keypair()
    _, other_pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, other_pub)  # server key ≠ signing key
    assert verify_supabase_token(_make_token(priv, "u")) is None


def test_verify_rejects_expired(monkeypatch):
    from app.common.token_service import verify_supabase_token

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    assert verify_supabase_token(_make_token(priv, "u", expired=True)) is None


def test_verify_requires_sub_and_exp(monkeypatch):
    from app.common.token_service import verify_supabase_token

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    # No exp / no sub → PyJWTError → None
    tok = jwt.encode({"foo": "bar"}, priv, algorithm="ES256", headers={"kid": "k"})
    assert verify_supabase_token(tok) is None


def test_verify_rejects_non_authenticated_audience(monkeypatch):
    """service_role / anon tokens (aud != 'authenticated') must be rejected."""
    from app.common.token_service import verify_supabase_token

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    for bad_aud in ("service_role", "anon"):
        assert verify_supabase_token(_make_token(priv, "u", aud=bad_aud)) is None


def test_verify_without_url_returns_none(monkeypatch):
    from app.common.token_service import verify_supabase_token

    # Empty string overrides any .env value (pydantic-settings: env > dotenv).
    monkeypatch.setenv("SUPABASE_URL", "")
    reset_auth_config()
    priv, _ = _ec_keypair()
    assert verify_supabase_token(_make_token(priv, "u")) is None


# ── get_current_user_id dependency ────────────────────────────────────


def test_no_header_falls_back_to_default():
    from app.common.dependencies import get_current_user_id

    # "" = no Authorization header (calling directly bypasses FastAPI's
    # Header(default="") marker, so pass the raw string).
    assert get_current_user_id("") == "default"


def test_valid_bearer_returns_sub(monkeypatch):
    from app.common.dependencies import get_current_user_id

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    assert get_current_user_id(f"Bearer {_make_token(priv, 'user-123')}") == "user-123"


def test_invalid_token_raises_401(monkeypatch):
    from fastapi import HTTPException

    from app.common.dependencies import get_current_user_id

    priv, pub = _ec_keypair()
    _patch_jwks_client(monkeypatch, pub)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id("Bearer not-a-jwt")
    assert exc_info.value.status_code == 401


def test_malformed_header_raises_401():
    from fastapi import HTTPException

    from app.common.dependencies import get_current_user_id

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id("Basic abc123")  # not Bearer
    assert exc_info.value.status_code == 401


def test_auth_disabled_without_url(monkeypatch):
    """No Supabase URL configured → dev mode → every request is 'default'."""
    from app.common.dependencies import get_current_user_id

    monkeypatch.setenv("SUPABASE_URL", "")  # override any .env value
    reset_auth_config()
    priv, _ = _ec_keypair()
    assert get_current_user_id(f"Bearer {_make_token(priv, 'u')}") == "default"
