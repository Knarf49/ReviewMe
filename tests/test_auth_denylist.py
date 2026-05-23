import time
import pytest

from app.web.services.auth import denylist


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def test_sid_starts_not_revoked(redis_client):
    assert denylist.is_sid_revoked(redis_client, "sid-1") is False


def test_revoke_sid_then_check(redis_client):
    denylist.revoke_sid(redis_client, "sid-1", ttl_seconds=60)
    assert denylist.is_sid_revoked(redis_client, "sid-1") is True


def test_revoke_sid_expires(redis_client):
    denylist.revoke_sid(redis_client, "sid-2", ttl_seconds=1)
    time.sleep(1.2)
    assert denylist.is_sid_revoked(redis_client, "sid-2") is False


def test_user_epoch_initially_none(redis_client):
    assert denylist.get_user_epoch(redis_client, 42) is None


def test_bump_user_epoch_returns_value(redis_client):
    before = int(time.time())
    denylist.bump_user_epoch(redis_client, 42, ttl_seconds=7200)
    epoch = denylist.get_user_epoch(redis_client, 42)
    assert epoch is not None
    assert epoch >= before
