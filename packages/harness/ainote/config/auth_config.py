"""Supabase JWT auth settings (read from ``.env``).

Auth config is a single scalar — the Supabase project URL — used to reach the
project's JWKS endpoint (``/auth/v1/.well-known/jwks.json``) for ES256
verification of access tokens. Unlike the model factory (which reads
structured ``config.yaml``), this follows the focused config convention: a
pydantic-settings class reading env vars directly from ``.env``, exactly like
``DatabaseConfig`` / ``ToolConfig``.

Empty URL = auth disabled (dev mode; every request falls back to
``user_id="default"``).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    """Supabase project URL used to verify ``Authorization: Bearer`` tokens.

    Supabase signs access tokens with the project's **JWT Signing Keys**
    (ECDSA P-256 / ES256) instead of the legacy JWT secret (HS256). The
    backend fetches the project's public signing keys from
    ``<url>/auth/v1/.well-known/jwks.json`` and verifies by ``kid``, so key
    rotation works without redeploys. The URL lives only in ``.env`` (never
    committed or logged). Empty = auth disabled.
    """

    SUPABASE_URL: str = ""
    # GoTrue API key (anon/publishable key) — forwarded as the `apikey` header
    # by the auth proxy so the frontend never needs to call Supabase directly.
    SUPABASE_ANON_KEY: str = ""
    # Supabase service_role key — used ONLY server-side to create email-confirmed
    # users for WeChat login (admin endpoint). Never exposed to the frontend.
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    # WeChat Mini Program credentials — from 微信公众平台 → 开发 → 开发设置.
    # WECHAT_APPSECRET is server-only; never ship it to the client.
    WECHAT_APPID: str = ""
    WECHAT_APPSECRET: str = ""
    # Optional independent secret used to derive the WeChat pseudo-password.
    # If set, rotating WECHAT_APPSECRET won't invalidate existing WeChat users.
    WECHAT_PASSWORD_SECRET: str = ""
    # Comma-separated whitelist of user_ids allowed to run privileged ops
    # (e.g. DingTalk MCP toggle). Empty = nobody is an admin.
    ADMIN_USER_IDS: str = ""
    # DingTalk OAuth (第三方应用授权) — 应用身份凭证, 由运营方在 .env 配置一次.
    # DINGTALK_CLIENT_ID 即钉钉 AppKey; DINGTALK_CLIENT_SECRET 即 AppSecret.
    DINGTALK_CLIENT_ID: str = ""
    DINGTALK_CLIENT_SECRET: str = ""
    # 钉钉 OAuth 回调地址(钉钉授权后回调到后端). 本地开发用 ngrok 公网地址,
    # 部署到云托管后填云托管域名. 必须与钉钉应用后台配置的回调地址一致.
    DINGTALK_REDIRECT_URI: str = ""
    # 钉钉 OAuth scope: 企业内部应用用 "openid corpid"; 第三方个人应用用 "openid".
    DINGTALK_SCOPE: str = "openid"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def admin_user_ids(self) -> set[str]:
        """Set of admin user ids parsed from ``ADMIN_USER_IDS``."""
        return {u.strip() for u in self.ADMIN_USER_IDS.split(",") if u.strip()}

    def is_admin(self, user_id: str) -> bool:
        """True when ``user_id`` is in the admin whitelist."""
        return user_id in self.admin_user_ids

    @property
    def jwks_url(self) -> str:
        """The project's GoTrue JWKS endpoint (empty string = auth disabled)."""
        url = self.SUPABASE_URL.strip().rstrip("/")
        return f"{url}/auth/v1/.well-known/jwks.json" if url else ""

    @property
    def enabled(self) -> bool:
        """True when auth is configured (a Supabase URL is set)."""
        return bool(self.jwks_url)

    @property
    def wechat_enabled(self) -> bool:
        """True when WeChat login is fully configured (URL + service_role + appid + secret)."""
        return bool(
            self.SUPABASE_URL
            and self.SUPABASE_SERVICE_ROLE_KEY
            and self.WECHAT_APPID
            and self.WECHAT_APPSECRET
        )

    @property
    def dingtalk_oauth_enabled(self) -> bool:
        """True when DingTalk OAuth is fully configured (app credentials + callback)."""
        return bool(
            self.DINGTALK_CLIENT_ID
            and self.DINGTALK_CLIENT_SECRET
            and self.DINGTALK_REDIRECT_URI
        )


_auth_config: AuthConfig | None = None


def get_auth_config() -> AuthConfig:
    """Return the cached ``AuthConfig`` singleton."""
    global _auth_config
    if _auth_config is None:
        _auth_config = AuthConfig()
    return _auth_config


def reset_auth_config() -> None:
    """Clear the cached ``AuthConfig`` singleton (test hook)."""
    global _auth_config
    _auth_config = None
