import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.web.services.auth import csrf


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def test_gen_csrf_returns_unique_tokens():
    a = csrf.gen_csrf()
    b = csrf.gen_csrf()
    assert a != b
    assert len(a) >= 40


def _make_app():
    app = FastAPI()

    @app.post("/protected", dependencies=[Depends(csrf.require_csrf)])
    def protected():
        return {"ok": True}

    return app


def test_require_csrf_passes_when_match():
    client = TestClient(_make_app())
    client.cookies.set("csrf_token", "abc")
    r = client.post("/protected", headers={"X-CSRF-Token": "abc"})
    assert r.status_code == 200


def test_require_csrf_403_on_mismatch():
    client = TestClient(_make_app())
    client.cookies.set("csrf_token", "abc")
    r = client.post("/protected", headers={"X-CSRF-Token": "def"})
    assert r.status_code == 403


def test_require_csrf_403_when_missing():
    client = TestClient(_make_app())
    r = client.post("/protected")
    assert r.status_code == 403
