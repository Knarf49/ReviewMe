import time
import pytest
import jwt

from app.web.services.auth import tokens


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("JWT_ALG", "HS256")
    monkeypatch.setenv("JWT_ISSUER", "reviewme-auth")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    # Reset config cache.
    from app.web.services.auth import config as cfg
    cfg.get_auth_config.cache_clear()


def test_encode_decode_round_trip():
    token = tokens.encode_access(uid=42, sid="sid-1", role="user")
    claims = tokens.decode_access(token)
    assert claims["uid"] == 42
    assert claims["sid"] == "sid-1"
    assert claims["role"] == "user"
    assert claims["typ"] == "access"
    assert claims["iss"] == "reviewme-auth"
    assert claims["exp"] > claims["iat"]


def test_decode_rejects_expired_token():
    cfg_mod = __import__("app.web.services.auth.config", fromlist=["get_auth_config"])
    cfg = cfg_mod.get_auth_config()
    now = int(time.time())
    payload = {
        "uid": 1, "sid": "s", "role": "user",
        "iat": now - 600, "exp": now - 60,  # past leeway window (10s)
        "iss": cfg.jwt_issuer, "typ": "access",
    }
    expired = jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access(expired)


def test_decode_rejects_bad_signature():
    token = tokens.encode_access(uid=1, sid="s", role="user")
    # Tamper last char.
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access(bad)


def test_decode_rejects_wrong_typ():
    cfg_mod = __import__("app.web.services.auth.config", fromlist=["get_auth_config"])
    cfg = cfg_mod.get_auth_config()
    payload = {
        "uid": 1, "sid": "s", "role": "user",
        "iat": int(time.time()), "exp": int(time.time()) + 60,
        "iss": cfg.jwt_issuer, "typ": "refresh",
    }
    bogus = jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access(bogus)


def test_decode_rejects_wrong_issuer():
    cfg_mod = __import__("app.web.services.auth.config", fromlist=["get_auth_config"])
    cfg = cfg_mod.get_auth_config()
    payload = {
        "uid": 1, "sid": "s", "role": "user",
        "iat": int(time.time()), "exp": int(time.time()) + 60,
        "iss": "evil-issuer", "typ": "access",
    }
    bogus = jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access(bogus)


def test_gen_refresh_returns_plain_and_hash():
    plain, hashed = tokens.gen_refresh()
    assert isinstance(plain, str)
    assert isinstance(hashed, str)
    assert len(plain) >= 40  # token_urlsafe(32) ~ 43 chars
    assert len(hashed) == 64  # sha256 hex
    assert tokens.hash_refresh(plain) == hashed


def test_hash_refresh_deterministic():
    assert tokens.hash_refresh("abc") == tokens.hash_refresh("abc")
    assert tokens.hash_refresh("abc") != tokens.hash_refresh("abd")
