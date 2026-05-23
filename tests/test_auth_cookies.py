import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.web.services.auth import cookies


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def _make_app():
    app = FastAPI()

    @app.post("/set")
    def set_endpoint(response: Response):
        cookies.set_auth_cookies(response, access="acc", refresh="ref", csrf="csrf")
        return {}

    @app.post("/clear")
    def clear_endpoint(response: Response):
        cookies.clear_auth_cookies(response)
        return {}

    return app


def _set_cookie_lines(response) -> list[str]:
    raw = response.headers.get_list("set-cookie") if hasattr(response.headers, "get_list") else None
    if raw is None:
        raw = [v for k, v in response.raw_headers if k.lower() == b"set-cookie"]
        raw = [v.decode() if isinstance(v, bytes) else v for v in raw]
    return raw


def test_set_auth_cookies_sets_all_three():
    client = TestClient(_make_app())
    r = client.post("/set")
    lines = _set_cookie_lines(r)
    by_name = {line.split("=", 1)[0]: line for line in lines}
    assert "access_token" in by_name
    assert "refresh_token" in by_name
    assert "csrf_token" in by_name
    assert "HttpOnly" in by_name["access_token"]
    assert "HttpOnly" in by_name["refresh_token"]
    assert "HttpOnly" not in by_name["csrf_token"]
    joined = "\n".join(lines)
    assert "SameSite=lax" in joined.lower().replace("samesite=lax", "SameSite=lax").lower() or "samesite=lax" in joined.lower()
    assert "Path=/" in joined


def test_clear_auth_cookies_sets_max_age_zero():
    client = TestClient(_make_app())
    r = client.post("/clear")
    joined = "\n".join(_set_cookie_lines(r)).lower()
    assert "access_token=" in joined
    assert "refresh_token=" in joined
    assert "csrf_token=" in joined
    assert "max-age=0" in joined
