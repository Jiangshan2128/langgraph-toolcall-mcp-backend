"""Tests for the WeChat msgSecCheck content-safety wrapper.

``check_text_safety`` is the only public function; these tests exercise its
risk/fail-open logic by monkeypatching the HTTP seams (``_access_token`` and
``_msg_sec_check``) so no network is touched. The auth config is stubbed with
a fake ``WECHAT_APPID``/``WECHAT_APPSECRET`` so ``_access_token`` isn't a
dead shortcut.

Test matrix:
  - risk content (errcode 87014)     → reject (False)
  - safe content (errcode 0)         → allow (True)
  - not configured (no appid)        → allow (True, no check available)
  - transient network / 45009        → allow (True, fail-open)
  - invalid token (40001)            → refresh once, retry succeeds
"""

import pytest

from ainote.config.auth_config import reset_auth_config
from app.common import content_safety


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch):
    """Configure WECHAT_APPID/SECRET and reset the config singleton."""
    monkeypatch.setenv("WECHAT_APPID", "wx-test-appid")
    monkeypatch.setenv("WECHAT_APPSECRET", "test-secret")
    reset_auth_config()
    # Reset module-level token cache between tests.
    content_safety._cache.clear()
    yield
    reset_auth_config()


# ── Risk / safe ─────────────────────────────────────────────────────────


def test_risk_content_is_rejected(monkeypatch):
    monkeypatch.setattr(
        content_safety, "_access_token", lambda cfg: "tok"
    )
    monkeypatch.setattr(
        content_safety,
        "_msg_sec_check",
        lambda cfg, token, content, version, scene: {"errcode": 87014, "errmsg": "risky"},
    )
    assert content_safety.check_text_safety("违规内容") is False


def test_safe_content_is_allowed(monkeypatch):
    monkeypatch.setattr(content_safety, "_access_token", lambda cfg: "tok")
    monkeypatch.setattr(
        content_safety,
        "_msg_sec_check",
        lambda cfg, token, content, version, scene: {"errcode": 0, "errmsg": "ok"},
    )
    assert content_safety.check_text_safety("明天我要去买鸡蛋") is True


# ── Fail-open (service unavailable) ─────────────────────────────────────


def test_not_configured_skips_check(monkeypatch):
    """No appid/secret → check skipped (fail-open), HTTP seam never called."""
    from types import SimpleNamespace

    # A config with empty WeChat credentials. _access_token must return None
    # without hitting the network.
    monkeypatch.setattr(
        content_safety,
        "get_auth_config",
        lambda: SimpleNamespace(WECHAT_APPID="", WECHAT_APPSECRET="", SUPABASE_URL=""),
    )
    monkeypatch.setattr(content_safety, "_access_token", lambda cfg: None)
    assert content_safety.check_text_safety("任意内容") is True


def test_network_failure_fails_open(monkeypatch):
    monkeypatch.setattr(content_safety, "_access_token", lambda cfg: "tok")
    monkeypatch.setattr(
        content_safety,
        "_msg_sec_check",
        lambda cfg, token, content, version, scene: {"errcode": -1, "errmsg": "timeout"},
    )
    assert content_safety.check_text_safety("正常内容") is True


def test_rate_limit_fails_open(monkeypatch):
    monkeypatch.setattr(content_safety, "_access_token", lambda cfg: "tok")
    monkeypatch.setattr(
        content_safety,
        "_msg_sec_check",
        lambda cfg, token, content, version, scene: {"errcode": 45009, "errmsg": "rate limit"},
    )
    assert content_safety.check_text_safety("正常内容") is True


# ── Token refresh retry ─────────────────────────────────────────────────


def test_invalid_token_refreshes_once_and_succeeds(monkeypatch):
    """40001 (invalid token) → refresh access_token → retry with the new one."""
    calls = []

    def fake_token(cfg):
        # First call "old", after clearing cache the second call "new".
        return "new" if len(calls) > 0 else "old"

    def fake_check(cfg, token, content, version, scene):
        calls.append(token)
        if token == "old":
            return {"errcode": 40001, "errmsg": "invalid token"}
        return {"errcode": 0, "errmsg": "ok"}

    monkeypatch.setattr(content_safety, "_access_token", fake_token)
    monkeypatch.setattr(content_safety, "_msg_sec_check", fake_check)
    # Prime the cache so _access_token is not called again from cache.
    monkeypatch.setattr(content_safety, "_cache", {"wx-test-appid:test-secret": "old"})

    assert content_safety.check_text_safety("正常内容") is True
    assert calls == ["old", "new"]


# ── filter_risky_reply (AI 输出) ────────────────────────────────────────


def test_filter_risky_reply_safe_passthrough(monkeypatch):
    monkeypatch.setattr(content_safety, "check_text_safety", lambda content, scene=2: True)
    reply = "好的，已为你记录任务"
    assert content_safety.filter_risky_reply(reply) == reply


def test_filter_risky_reply_replaces_risky(monkeypatch):
    monkeypatch.setattr(content_safety, "check_text_safety", lambda content, scene=2: False)
    out = content_safety.filter_risky_reply("违规的 AI 回复内容")
    assert out == content_safety.RISKY_REPLY_FALLBACK
    assert "违规" not in out


def test_filter_risky_reply_empty_returns_empty(monkeypatch):
    # 空回复不经检测,原样返回(不触发 HTTP)。
    monkeypatch.setattr(content_safety, "check_text_safety", lambda content, scene=2: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert content_safety.filter_risky_reply("") == ""
    assert content_safety.filter_risky_reply("   ") == "   "
