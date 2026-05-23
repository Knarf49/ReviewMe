import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "5")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def _signup(client, username="alice", email="alice@x.com", password="pass1234"):
    return client.post(
        "/signup",
        json={"username": username, "email": email, "password": password},
    )


def test_login_happy_path_sets_cookies_and_returns_csrf(client):
    _signup(client)
    r = client.post("/login", json={"username": "alice", "password": "pass1234"})
    assert r.status_code == 200
    body = r.json()
    assert "user" in body and "csrf_token" in body
    assert body["user"]["username"] == "alice"
    set_cookie = r.headers.get_list("set-cookie")
    joined = "\n".join(set_cookie)
    assert "access_token=" in joined
    assert "refresh_token=" in joined
    assert "csrf_token=" in joined


def test_login_wrong_password_401(client):
    _signup(client)
    r = client.post("/login", json={"username": "alice", "password": "wrong-pw"})
    assert r.status_code == 401


def test_login_unknown_user_401(client):
    r = client.post("/login", json={"username": "ghostuser", "password": "pass1234"})
    assert r.status_code == 401


def _login(client, username="alice", password="pass1234"):
    r = client.post("/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["csrf_token"]


def test_logout_deletes_family_and_clears_cookies(client, db, redis_client):
    _signup(client)
    csrf_tok = _login(client)
    r = client.post("/logout", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 204
    db.expire_all()
    from app.core.models import RefreshSession, User
    user = db.query(User).filter_by(username="alice").one()
    assert db.query(RefreshSession).filter_by(user_id=user.id).count() == 0
    set_cookie = "\n".join(r.headers.get_list("set-cookie")).lower()
    assert "max-age=0" in set_cookie


def test_logout_without_csrf_403(client):
    _signup(client)
    _login(client)
    r = client.post("/logout")
    assert r.status_code == 403


def test_logout_with_no_refresh_cookie_returns_204_anyway(client):
    _signup(client)
    csrf_tok = _login(client)
    client.cookies.delete("refresh_token")
    r = client.post("/logout", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 204


def test_login_session_limit_evicts_oldest(client, db, monkeypatch):
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "2")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()
    _signup(client, username="bob", email="bob@x.com")
    from app.core.models import RefreshSession, User
    for _ in range(3):
        r = client.post("/login", json={"username": "bob", "password": "pass1234"})
        assert r.status_code == 200
    db.expire_all()
    user = db.query(User).filter_by(username="bob").one()
    active = db.query(RefreshSession).filter_by(user_id=user.id).filter(
        RefreshSession.used_at.is_(None)
    ).count()
    assert active == 2
