import time
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.core.models import User
from app.web.services.auth import tokens
from app.web.services.auth.dependencies import get_current_user


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def _app(db, redis_client):
    from app.core.db import db_dep
    from app.core.redis_client import get_redis
    from app.web.services.auth.exceptions import register_auth_error_handler

    app = FastAPI()
    register_auth_error_handler(app)
    app.dependency_overrides[db_dep] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_client

    @app.get("/me")
    def me(user: User = Depends(get_current_user)):
        return {"id": user.id, "username": user.username}

    return app


def _seed_user(db, name="u1"):
    u = User(username=name, email=f"{name}@x.com", password_hash="h")
    db.add(u); db.flush()
    return u


def test_get_current_user_happy_path(db, redis_client):
    u = _seed_user(db, "gc1")
    token = tokens.encode_access(uid=u.id, sid="sid-1", role="user")
    client = TestClient(_app(db, redis_client))
    client.cookies.set("access_token", token)
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json()["id"] == u.id


def test_get_current_user_missing_cookie_401(db, redis_client):
    client = TestClient(_app(db, redis_client))
    r = client.get("/me")
    assert r.status_code == 401


def test_get_current_user_bad_token_401(db, redis_client):
    client = TestClient(_app(db, redis_client))
    client.cookies.set("access_token", "not-a-jwt")
    r = client.get("/me")
    assert r.status_code == 401


def test_get_current_user_revoked_sid_401(db, redis_client):
    u = _seed_user(db, "gc2")
    token = tokens.encode_access(uid=u.id, sid="sid-rev", role="user")
    from app.web.services.auth import denylist
    denylist.revoke_sid(redis_client, "sid-rev", ttl_seconds=60)
    client = TestClient(_app(db, redis_client))
    client.cookies.set("access_token", token)
    r = client.get("/me")
    assert r.status_code == 401


def test_get_current_user_epoch_bump_invalidates_old_token(db, redis_client):
    u = _seed_user(db, "gc3")
    token = tokens.encode_access(uid=u.id, sid="sid-3", role="user")
    time.sleep(1)
    from app.web.services.auth import denylist
    denylist.bump_user_epoch(redis_client, u.id, ttl_seconds=60)
    client = TestClient(_app(db, redis_client))
    client.cookies.set("access_token", token)
    r = client.get("/me")
    assert r.status_code == 401
