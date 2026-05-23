import os
import pytest


def _load_config():
    # Clear the lru_cache so env mutations take effect on the next call.
    # Do NOT importlib.reload — reload creates a new function object while
    # other modules (e.g. sessions.py) still hold a reference to the original
    # function, keeping a stale cache that no further cache_clear can reach.
    import app.web.services.auth.config as mod
    mod.get_auth_config.cache_clear()
    return mod


def test_load_valid_config(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("JWT_ALG", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "reviewme-auth")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "5")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    cfg = _load_config().get_auth_config()
    assert cfg.jwt_secret == "x" * 40
    assert cfg.jwt_alg == "HS256"
    assert cfg.jwt_issuer == "reviewme-auth"
    assert cfg.access_ttl_seconds == 7200
    assert cfg.refresh_ttl_seconds == 1209600
    assert cfg.cookie_secure is False
    assert cfg.session_limit_per_user == 5
    assert cfg.redis_url == "redis://localhost:6379/0"


def test_short_secret_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "tooshort")
    mod = _load_config()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        mod.get_auth_config()


def test_missing_secret_rejected(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    mod = _load_config()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        mod.get_auth_config()
