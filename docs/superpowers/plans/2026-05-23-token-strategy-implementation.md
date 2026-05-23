# Token Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement JWT access + opaque refresh token auth on FastAPI backend per spec `docs/superpowers/specs/2026-05-23-token-strategy-design.md`.

**Architecture:** HttpOnly cookies carry JWT access (2h) and opaque refresh (14d, sha256-hashed in Postgres). Refresh rotation with reuse detection. Redis-backed instant revocation via per-session denylist + per-user epoch. Double-submit CSRF on mutating endpoints. Max 5 concurrent sessions per user.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PyJWT, passlib[bcrypt], redis-py (sync), Postgres 16, Redis 7, pytest.

---

## File map

**Create:**
- `app/web/services/__init__.py`
- `app/web/services/auth/__init__.py`
- `app/web/services/auth/config.py` — env-loaded `AuthConfig`
- `app/web/services/auth/tokens.py` — JWT encode/decode + refresh gen/hash
- `app/web/services/auth/cookies.py` — set/clear cookie helpers
- `app/web/services/auth/csrf.py` — CSRF gen + `require_csrf` dep
- `app/web/services/auth/denylist.py` — Redis revocation wrappers
- `app/web/services/auth/sessions.py` — refresh-session CRUD + rotation + reuse detection
- `app/web/services/auth/dependencies.py` — `get_current_user` dep
- `app/web/services/auth/exceptions.py` — AuthError hierarchy + handler registrar
- `app/core/redis_client.py` — redis client singleton
- `alembic/versions/0002_refresh_sessions.py` — migration
- `scripts/cleanup_expired_sessions.py` — daily cleanup job
- `tests/test_auth_config.py`
- `tests/test_auth_tokens.py`
- `tests/test_auth_cookies.py`
- `tests/test_auth_csrf.py`
- `tests/test_auth_denylist.py`
- `tests/test_auth_sessions.py`
- `tests/test_auth_dependencies.py`
- `tests/test_auth_endpoints.py`
- `tests/test_cleanup_script.py`

**Modify:**
- `requirements.txt` — add PyJWT, passlib[bcrypt], redis, fastapi (if missing), email-validator (already added at runtime, pin here)
- `docker-compose.yml` — add Redis service
- `.env` and `.env.example` — add auth env vars + REDIS_URL
- `app/core/models.py` — add `RefreshSession` model
- `app/web/main.py` — register AuthError handler
- `app/web/controllers/auth.py` — refactor `/login`; add `/logout`, `/logout-all`, `/refresh`
- `tests/test_models.py` — add RefreshSession tests
- `tests/test_migrations.py` — extend round-trip to cover 0002
- `tests/conftest.py` — add `client` (TestClient) and `redis` fixtures

---

## Pre-flight (run once before Task 1)

- [ ] **Verify Postgres test DB reachable**

Run: `psql "$TEST_DATABASE_URL" -c "SELECT 1"` (or use existing test infra).
Expected: `1` returned. If fails, `docker compose up -d postgres` first.

- [ ] **Verify Python venv active and current tests pass**

Run: `pytest -q`
Expected: existing tests pass (signup tests may not exist yet — that's fine; nothing should regress).

---

## Task 1: Add Redis service to docker-compose

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Add Redis service block to `docker-compose.yml`**

Append before the `volumes:` section at the bottom:

```yaml
  redis:
    image: redis:7-alpine
    container_name: reviewme-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: ["redis-server", "--appendonly", "yes"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

And under `volumes:` add:

```yaml
  redis-data:
```

- [ ] **Step 2: Append auth + Redis env vars to `.env`**

```
# Redis
REDIS_URL=redis://localhost:6379/0
TEST_REDIS_URL=redis://localhost:6379/1

# Auth — JWT
JWT_SECRET=dev_only_change_me_to_random_32_byte_base64_value_xxxxx
JWT_ALG=HS256
JWT_ISSUER=reviewme-auth
ACCESS_TTL_SECONDS=7200
REFRESH_TTL_SECONDS=1209600

# Auth — cookies
COOKIE_DOMAIN=
COOKIE_SECURE=false
SESSION_LIMIT_PER_USER=5
```

- [ ] **Step 3: Mirror into `.env.example` (with placeholder JWT_SECRET)**

Same block as above, but set `JWT_SECRET=` (empty) and `COOKIE_SECURE=true` to reflect prod defaults.

- [ ] **Step 4: Start Redis and verify**

Run: `docker compose up -d redis`
Run: `docker exec reviewme-redis redis-cli ping`
Expected: `PONG`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(infra): add Redis service for auth denylist"
```

(Do not commit `.env` — gitignored.)

---

## Task 2: Pin Python dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append auth deps**

Add lines to `requirements.txt`:

```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
pydantic[email]
pyjwt>=2.9
passlib[bcrypt]>=1.7.4
redis>=5.0
python-dotenv>=1.0
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements.txt`
Expected: no errors. PyJWT, redis, passlib installed (some already from earlier session — that's fine).

- [ ] **Step 3: Verify imports**

Run: `python -c "import jwt, redis, passlib.context, fastapi; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: pin auth runtime requirements (PyJWT, passlib, redis)"
```

---

## Task 3: AuthConfig — env-loaded settings with validation

**Files:**
- Create: `app/web/services/__init__.py` (empty)
- Create: `app/web/services/auth/__init__.py` (empty)
- Create: `app/web/services/auth/config.py`
- Test: `tests/test_auth_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_config.py`:

```python
import os
import pytest


def _load_config():
    # Re-import inside test so env mutations take effect.
    import importlib
    import app.web.services.auth.config as mod
    importlib.reload(mod)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_config.py -v`
Expected: `ImportError` or `ModuleNotFoundError` — module doesn't exist yet.

- [ ] **Step 3: Implement `app/web/services/auth/config.py`**

```python
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class AuthConfig:
    jwt_secret: str
    jwt_alg: str
    jwt_issuer: str
    access_ttl_seconds: int
    refresh_ttl_seconds: int
    cookie_domain: str | None
    cookie_secure: bool
    session_limit_per_user: int
    redis_url: str


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    secret = os.environ.get("JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET missing or too short (need >=32 chars). "
            "Generate: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    domain = os.environ.get("COOKIE_DOMAIN", "") or None
    return AuthConfig(
        jwt_secret=secret,
        jwt_alg=os.environ.get("JWT_ALG", "HS256"),
        jwt_issuer=os.environ.get("JWT_ISSUER", "reviewme-auth"),
        access_ttl_seconds=int(os.environ.get("ACCESS_TTL_SECONDS", "7200")),
        refresh_ttl_seconds=int(os.environ.get("REFRESH_TTL_SECONDS", "1209600")),
        cookie_domain=domain,
        cookie_secure=_bool_env("COOKIE_SECURE", True),
        session_limit_per_user=int(os.environ.get("SESSION_LIMIT_PER_USER", "5")),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    )
```

Note: the test calls `importlib.reload` to bypass `lru_cache`. Inside `get_auth_config`, the cache lives on the module object, so reloading clears it.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_config.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/__init__.py app/web/services/auth/__init__.py \
    app/web/services/auth/config.py tests/test_auth_config.py
git commit -m "feat(auth): env-loaded AuthConfig with secret validation"
```

---

## Task 4: tokens.py — JWT encode/decode + refresh token generation

**Files:**
- Create: `app/web/services/auth/tokens.py`
- Test: `tests/test_auth_tokens.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_tokens.py`:

```python
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


def test_decode_rejects_expired_token(monkeypatch):
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "1")
    from app.web.services.auth import config as cfg
    cfg.get_auth_config.cache_clear()
    token = tokens.encode_access(uid=1, sid="s", role="user")
    time.sleep(2)
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access(token)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_tokens.py -v`
Expected: ImportError — module missing.

- [ ] **Step 3: Implement `app/web/services/auth/tokens.py`**

```python
import hashlib
import secrets
import time

import jwt

from app.web.services.auth.config import get_auth_config


class InvalidTokenError(Exception):
    """Raised when an access token fails verification."""


def encode_access(uid: int, sid: str, role: str) -> str:
    cfg = get_auth_config()
    now = int(time.time())
    payload = {
        "uid": uid,
        "sid": sid,
        "role": role,
        "iat": now,
        "exp": now + cfg.access_ttl_seconds,
        "iss": cfg.jwt_issuer,
        "typ": "access",
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_alg)


def decode_access(token: str) -> dict:
    cfg = get_auth_config()
    try:
        claims = jwt.decode(
            token,
            cfg.jwt_secret,
            algorithms=[cfg.jwt_alg],
            issuer=cfg.jwt_issuer,
            leeway=10,
            options={"require": ["exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    if claims.get("typ") != "access":
        raise InvalidTokenError("wrong token type")
    return claims


def gen_refresh() -> tuple[str, str]:
    plain = secrets.token_urlsafe(32)
    return plain, hash_refresh(plain)


def hash_refresh(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_tokens.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/tokens.py tests/test_auth_tokens.py
git commit -m "feat(auth): JWT encode/decode + refresh gen with sha256 hash"
```

---

## Task 5: cookies.py — set/clear auth cookies

**Files:**
- Create: `app/web/services/auth/cookies.py`
- Test: `tests/test_auth_cookies.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_cookies.py`:

```python
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


def test_set_auth_cookies_sets_all_three():
    client = TestClient(_make_app())
    r = client.post("/set")
    raw = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else r.headers.raw
    joined = "\n".join(
        c.decode() if isinstance(c, bytes) else c
        for c in (raw if isinstance(raw, list) else [raw])
    )
    assert "access_token=acc" in joined
    assert "refresh_token=ref" in joined
    assert "csrf_token=csrf" in joined
    # Access + refresh must be HttpOnly; csrf must not.
    assert "access_token=acc" in joined and "HttpOnly" in joined.split("access_token=acc", 1)[1].split("\n", 1)[0]
    assert "refresh_token=ref" in joined and "HttpOnly" in joined.split("refresh_token=ref", 1)[1].split("\n", 1)[0]
    csrf_line = joined.split("csrf_token=csrf", 1)[1].split("\n", 1)[0]
    assert "HttpOnly" not in csrf_line
    assert "SameSite=Lax" in joined
    assert "Path=/" in joined


def test_clear_auth_cookies_sets_max_age_zero():
    client = TestClient(_make_app())
    r = client.post("/clear")
    raw = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else r.headers.raw
    joined = "\n".join(
        c.decode() if isinstance(c, bytes) else c
        for c in (raw if isinstance(raw, list) else [raw])
    )
    assert "access_token=" in joined
    assert "refresh_token=" in joined
    assert "csrf_token=" in joined
    assert "Max-Age=0" in joined or "max-age=0" in joined.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_cookies.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/web/services/auth/cookies.py`**

```python
from fastapi import Response

from app.web.services.auth.config import get_auth_config

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


def set_auth_cookies(response: Response, *, access: str, refresh: str, csrf: str) -> None:
    cfg = get_auth_config()
    common = {
        "secure": cfg.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    if cfg.cookie_domain:
        common["domain"] = cfg.cookie_domain

    response.set_cookie(
        ACCESS_COOKIE, access,
        max_age=cfg.access_ttl_seconds, httponly=True, **common,
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh,
        max_age=cfg.refresh_ttl_seconds, httponly=True, **common,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=cfg.access_ttl_seconds, httponly=False, **common,
    )


def clear_auth_cookies(response: Response) -> None:
    cfg = get_auth_config()
    common = {"path": "/"}
    if cfg.cookie_domain:
        common["domain"] = cfg.cookie_domain
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, **common)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_cookies.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/cookies.py tests/test_auth_cookies.py
git commit -m "feat(auth): set/clear HttpOnly auth cookies helper"
```

---

## Task 6: csrf.py — token gen + require_csrf dependency

**Files:**
- Create: `app/web/services/auth/csrf.py`
- Test: `tests/test_auth_csrf.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_csrf.py`:

```python
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
    r = client.post(
        "/protected",
        cookies={"csrf_token": "abc"},
        headers={"X-CSRF-Token": "abc"},
    )
    assert r.status_code == 200


def test_require_csrf_403_on_mismatch():
    client = TestClient(_make_app())
    r = client.post(
        "/protected",
        cookies={"csrf_token": "abc"},
        headers={"X-CSRF-Token": "def"},
    )
    assert r.status_code == 403


def test_require_csrf_403_when_missing():
    client = TestClient(_make_app())
    r = client.post("/protected")
    assert r.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_csrf.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/web/services/auth/csrf.py`**

```python
import hmac
import secrets

from fastapi import HTTPException, Request, status


def gen_csrf() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    cookie_val = request.cookies.get("csrf_token") or ""
    header_val = request.headers.get("x-csrf-token") or ""
    if not cookie_val or not header_val or not hmac.compare_digest(cookie_val, header_val):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_csrf.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/csrf.py tests/test_auth_csrf.py
git commit -m "feat(auth): double-submit CSRF gen + require_csrf dep"
```

---

## Task 7: exceptions.py — AuthError hierarchy + global handler

**Files:**
- Create: `app/web/services/auth/exceptions.py`
- Modify: `app/web/main.py`

- [ ] **Step 1: Write failing test (inline in test_auth_endpoints.py later — for now, just implement)**

(No standalone test for hierarchy class — covered indirectly by endpoint tests.)

- [ ] **Step 2: Implement `app/web/services/auth/exceptions.py`**

```python
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AuthError(Exception):
    code: str = "auth_error"
    status_code: int = status.HTTP_401_UNAUTHORIZED
    detail: str = "Authentication failed"


class InvalidCredentials(AuthError):
    code = "invalid_credentials"
    detail = "Invalid username or password"


class InvalidToken(AuthError):
    code = "invalid_token"
    detail = "Invalid or expired session"


class ReuseDetected(AuthError):
    code = "reuse_detected"
    detail = "Session terminated"


class CsrfMismatch(AuthError):
    code = "csrf_mismatch"
    status_code = status.HTTP_403_FORBIDDEN
    detail = "CSRF verification failed"


class AuthServiceUnavailable(AuthError):
    code = "auth_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Auth service unavailable"


def register_auth_error_handler(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _handler(_request: Request, exc: AuthError):  # type: ignore[unused-variable]
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
```

- [ ] **Step 3: Wire handler into `app/web/main.py`**

Replace existing `app/web/main.py` body so it reads:

```python
from fastapi import FastAPI

from controllers.auth import router as auth_router
from services.auth.exceptions import register_auth_error_handler

app = FastAPI()
register_auth_error_handler(app)
app.include_router(auth_router)


@app.get("/")
def read_root():
    return {"Hello": "World"}
```

- [ ] **Step 4: Smoke-test imports**

Run from project root:

```bash
PYTHONPATH="app;app/web" python -c "from web.main import app; print(len(app.routes))"
```

Expected: a positive integer (no ImportError).

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/exceptions.py app/web/main.py
git commit -m "feat(auth): AuthError hierarchy + global JSON exception handler"
```

---

## Task 8: Redis client singleton

**Files:**
- Create: `app/core/redis_client.py`
- Modify: `tests/conftest.py` — add `redis_client` fixture

- [ ] **Step 1: Implement `app/core/redis_client.py`**

```python
from functools import lru_cache

import redis

from app.web.services.auth.config import get_auth_config


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    cfg = get_auth_config()
    return redis.Redis.from_url(cfg.redis_url, decode_responses=True)
```

- [ ] **Step 2: Add `redis_client` fixture to `tests/conftest.py`**

Append:

```python
@pytest.fixture
def redis_client():
    import os
    import redis as _redis
    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")
    client = _redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()
```

- [ ] **Step 3: Quick connectivity test**

Create `tests/test_redis_smoke.py`:

```python
def test_redis_reachable(redis_client):
    redis_client.set("k", "v")
    assert redis_client.get("k") == "v"
```

Run: `pytest tests/test_redis_smoke.py -v`
Expected: PASS (requires `docker compose up -d redis` first).

- [ ] **Step 4: Commit**

```bash
git add app/core/redis_client.py tests/conftest.py tests/test_redis_smoke.py
git commit -m "feat(auth): redis client singleton + test fixture"
```

---

## Task 9: denylist.py — Redis revocation wrappers

**Files:**
- Create: `app/web/services/auth/denylist.py`
- Test: `tests/test_auth_denylist.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_denylist.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_denylist.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/web/services/auth/denylist.py`**

```python
import time

import redis


def _sid_key(sid: str) -> str:
    return f"revoked_sid:{sid}"


def _epoch_key(uid: int) -> str:
    return f"user_epoch:{uid}"


def revoke_sid(client: redis.Redis, sid: str, *, ttl_seconds: int) -> None:
    client.set(_sid_key(sid), "1", ex=ttl_seconds)


def is_sid_revoked(client: redis.Redis, sid: str) -> bool:
    return client.exists(_sid_key(sid)) == 1


def bump_user_epoch(client: redis.Redis, uid: int, *, ttl_seconds: int) -> None:
    client.set(_epoch_key(uid), str(int(time.time())), ex=ttl_seconds)


def get_user_epoch(client: redis.Redis, uid: int) -> int | None:
    val = client.get(_epoch_key(uid))
    return int(val) if val is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_denylist.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/denylist.py tests/test_auth_denylist.py
git commit -m "feat(auth): Redis-backed sid denylist + user epoch"
```

---

## Task 10: RefreshSession model

**Files:**
- Modify: `app/core/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Add failing tests in `tests/test_models.py`**

Append to `tests/test_models.py`:

```python
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.core.models import RefreshSession


def _expires():
    return datetime.now(timezone.utc) + timedelta(days=14)


def test_refresh_session_basic_insert(db):
    u = User(username="rs1", email="rs1@x.com", password_hash="h")
    db.add(u); db.flush()
    s = RefreshSession(
        user_id=u.id,
        family_id=uuid4(),
        token_hash="a" * 64,
        expires_at=_expires(),
    )
    db.add(s); db.flush()
    assert s.id is not None
    assert s.used_at is None
    assert s.parent_id is None


def test_refresh_session_unique_token_hash(db):
    u = User(username="rs2", email="rs2@x.com", password_hash="h")
    db.add(u); db.flush()
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64, expires_at=_expires(),
    ))
    db.flush()
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64, expires_at=_expires(),
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_delete_user_cascades_refresh_sessions(db):
    u = User(username="rs3", email="rs3@x.com", password_hash="h")
    db.add(u); db.flush()
    s = RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="c" * 64, expires_at=_expires(),
    )
    db.add(s); db.flush()
    sid = s.id
    db.delete(u); db.flush()
    assert db.get(RefreshSession, sid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: ImportError on `RefreshSession`.

- [ ] **Step 3: Add `RefreshSession` to `app/core/models.py`**

Add imports if missing (`Index`, `ForeignKey`, `TIMESTAMP`, `String` already present; add `Uuid`, `BigInteger`):

```python
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, TIMESTAMP, Uuid
from sqlalchemy.dialects.postgresql import INET
```

Append class at end of file:

```python
class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    family_id: Mapped["uuid.UUID"] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    __table_args__ = (
        Index("ix_refresh_sessions_user_id", "user_id"),
        Index("ix_refresh_sessions_family_id", "family_id"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )
```

Add `import uuid` at top of `models.py` if you used the string-forward annotation; if you prefer `from uuid import UUID`, change the Mapped annotation to `Mapped[UUID]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: all model tests pass (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add app/core/models.py tests/test_models.py
git commit -m "feat(auth): RefreshSession SQLAlchemy model + tests"
```

---

## Task 11: Alembic migration 0002 — refresh_sessions

**Files:**
- Create: `alembic/versions/0002_refresh_sessions.py`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Extend migration round-trip test**

Append to `tests/test_migrations.py` (use existing `clean_db` fixture + `_alembic` helper):

```python
def test_upgrade_creates_refresh_sessions(clean_db):
    _alembic("upgrade", "head")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert "refresh_sessions" in tables


def test_downgrade_drops_refresh_sessions(clean_db):
    _alembic("upgrade", "head")
    _alembic("downgrade", "-1")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert "refresh_sessions" not in tables
    # The 0001 tables still exist after going down one step.
    assert {"users", "threads", "messages"}.issubset(tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -v`
Expected: fails — only `0001` exists.

- [ ] **Step 3: Implement `alembic/versions/0002_refresh_sessions.py`**

```python
"""refresh_sessions

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            sa.ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", INET(), nullable=True),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"])
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0002_refresh_sessions.py tests/test_migrations.py
git commit -m "feat(auth): alembic 0002 — refresh_sessions table"
```

---

## Task 12: sessions.py — refresh session service

**Files:**
- Create: `app/web/services/auth/sessions.py`
- Test: `tests/test_auth_sessions.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_sessions.py`:

```python
from datetime import datetime, timezone, timedelta
from uuid import UUID
import pytest

from app.core.models import RefreshSession, User
from app.web.services.auth import sessions
from app.web.services.auth.exceptions import ReuseDetected, InvalidToken


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "5")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def _make_user(db, name="su"):
    u = User(username=name, email=f"{name}@x.com", password_hash="h")
    db.add(u); db.flush()
    return u


def test_create_session_returns_plain_and_family_and_inserts_row(db):
    u = _make_user(db, "cs1")
    plain, family_id = sessions.create_session(db, user_id=u.id, user_agent="ua", ip="1.2.3.4")
    assert isinstance(plain, str) and len(plain) >= 40
    assert isinstance(family_id, UUID)
    row = db.query(RefreshSession).filter_by(family_id=family_id).one()
    assert row.user_id == u.id
    assert row.used_at is None
    assert row.user_agent == "ua"


def test_rotate_marks_used_and_inserts_new_in_same_family(db, redis_client):
    u = _make_user(db, "ro1")
    plain, family_id = sessions.create_session(db, user_id=u.id)
    new_plain, new_family_id, sid = sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    assert new_family_id == family_id
    assert str(sid) == str(family_id)
    rows = db.query(RefreshSession).filter_by(family_id=family_id).order_by(RefreshSession.id).all()
    assert len(rows) == 2
    assert rows[0].used_at is not None
    assert rows[1].used_at is None
    assert rows[1].parent_id == rows[0].id


def test_rotate_with_unknown_token_raises_invalid_token(db, redis_client):
    with pytest.raises(InvalidToken):
        sessions.rotate(db, redis_client, "definitely-not-a-real-token", user_agent=None, ip=None)


def test_rotate_with_expired_token_raises_invalid_token(db, redis_client):
    u = _make_user(db, "rx1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    # Force expiry
    row = db.query(RefreshSession).filter_by(family_id=fam).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()
    with pytest.raises(InvalidToken):
        sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)


def test_rotate_with_reused_token_kills_family_and_bumps_epoch(db, redis_client):
    u = _make_user(db, "re1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    # First rotate succeeds.
    sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    # Second rotate with same old plain → reuse.
    with pytest.raises(ReuseDetected):
        sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    assert db.query(RefreshSession).filter_by(family_id=fam).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, u.id) is not None


def test_revoke_family_deletes_and_marks_sid_revoked(db, redis_client):
    u = _make_user(db, "rv1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    sessions.revoke_family(db, redis_client, family_id=fam)
    assert db.query(RefreshSession).filter_by(family_id=fam).count() == 0
    from app.web.services.auth import denylist
    assert denylist.is_sid_revoked(redis_client, str(fam)) is True


def test_revoke_all_for_user_deletes_and_bumps_epoch(db, redis_client):
    u = _make_user(db, "ra1")
    sessions.create_session(db, user_id=u.id)
    sessions.create_session(db, user_id=u.id)
    sessions.revoke_all_for_user(db, redis_client, user_id=u.id)
    assert db.query(RefreshSession).filter_by(user_id=u.id).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, u.id) is not None


def test_enforce_limit_evicts_oldest_when_at_cap(db, redis_client, monkeypatch):
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "3")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()
    u = _make_user(db, "lm1")
    for _ in range(3):
        sessions.create_session(db, user_id=u.id)
    sessions.enforce_limit(db, redis_client, user_id=u.id)
    assert db.query(RefreshSession).filter_by(user_id=u.id).count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_sessions.py -v`
Expected: ImportError on sessions.

- [ ] **Step 3: Implement `app/web/services/auth/sessions.py`**

```python
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import RefreshSession
from app.web.services.auth import denylist
from app.web.services.auth.config import get_auth_config
from app.web.services.auth.exceptions import InvalidToken, ReuseDetected
from app.web.services.auth.tokens import gen_refresh, hash_refresh


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(
    db: Session,
    *,
    user_id: int,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, UUID]:
    cfg = get_auth_config()
    plain, hashed = gen_refresh()
    family_id = uuid4()
    row = RefreshSession(
        user_id=user_id,
        family_id=family_id,
        token_hash=hashed,
        parent_id=None,
        used_at=None,
        expires_at=_now() + timedelta(seconds=cfg.refresh_ttl_seconds),
        user_agent=user_agent,
        ip=ip,
    )
    db.add(row)
    db.flush()
    return plain, family_id


def rotate(
    db: Session,
    redis_client,
    plain_token: str,
    *,
    user_agent: str | None,
    ip: str | None,
) -> tuple[str, UUID, UUID]:
    cfg = get_auth_config()
    hashed = hash_refresh(plain_token)
    row = db.execute(
        select(RefreshSession)
        .where(RefreshSession.token_hash == hashed)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise InvalidToken()
    if row.expires_at <= _now():
        raise InvalidToken()
    if row.used_at is not None:
        revoke_family(db, redis_client, family_id=row.family_id)
        denylist.bump_user_epoch(
            redis_client, row.user_id, ttl_seconds=cfg.access_ttl_seconds,
        )
        raise ReuseDetected()

    row.used_at = _now()
    new_plain, new_hashed = gen_refresh()
    new_row = RefreshSession(
        user_id=row.user_id,
        family_id=row.family_id,
        token_hash=new_hashed,
        parent_id=row.id,
        used_at=None,
        expires_at=_now() + timedelta(seconds=cfg.refresh_ttl_seconds),
        user_agent=user_agent,
        ip=ip,
    )
    db.add(new_row)
    db.flush()
    return new_plain, row.family_id, row.family_id


def revoke_family(db: Session, redis_client, *, family_id: UUID) -> None:
    cfg = get_auth_config()
    db.query(RefreshSession).filter(RefreshSession.family_id == family_id).delete(
        synchronize_session=False,
    )
    db.flush()
    denylist.revoke_sid(
        redis_client, str(family_id), ttl_seconds=cfg.access_ttl_seconds,
    )


def revoke_all_for_user(db: Session, redis_client, *, user_id: int) -> None:
    cfg = get_auth_config()
    families = [
        row.family_id
        for row in db.execute(
            select(RefreshSession.family_id)
            .where(RefreshSession.user_id == user_id)
            .distinct()
        ).all()
    ]
    db.query(RefreshSession).filter(RefreshSession.user_id == user_id).delete(
        synchronize_session=False,
    )
    db.flush()
    for fam in families:
        denylist.revoke_sid(
            redis_client, str(fam), ttl_seconds=cfg.access_ttl_seconds,
        )
    denylist.bump_user_epoch(
        redis_client, user_id, ttl_seconds=cfg.access_ttl_seconds,
    )


def enforce_limit(db: Session, redis_client, *, user_id: int) -> None:
    cfg = get_auth_config()
    active = db.execute(
        select(RefreshSession)
        .where(
            RefreshSession.user_id == user_id,
            RefreshSession.used_at.is_(None),
            RefreshSession.expires_at > _now(),
        )
        .order_by(RefreshSession.created_at.asc())
    ).scalars().all()
    while len(active) >= cfg.session_limit_per_user:
        oldest = active.pop(0)
        revoke_family(db, redis_client, family_id=oldest.family_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_sessions.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/sessions.py tests/test_auth_sessions.py
git commit -m "feat(auth): refresh session create/rotate/revoke + limit enforcement"
```

---

## Task 13: dependencies.py — get_current_user

**Files:**
- Create: `app/web/services/auth/dependencies.py`
- Test: `tests/test_auth_dependencies.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_dependencies.py`:

```python
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
    from app.core.db import get_db
    from app.core.redis_client import get_redis
    from app.web.services.auth.exceptions import register_auth_error_handler

    app = FastAPI()
    register_auth_error_handler(app)
    app.dependency_overrides[get_db] = lambda: db
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
    r = client.get("/me", cookies={"access_token": token})
    assert r.status_code == 200
    assert r.json()["id"] == u.id


def test_get_current_user_missing_cookie_401(db, redis_client):
    client = TestClient(_app(db, redis_client))
    r = client.get("/me")
    assert r.status_code == 401


def test_get_current_user_bad_token_401(db, redis_client):
    client = TestClient(_app(db, redis_client))
    r = client.get("/me", cookies={"access_token": "not-a-jwt"})
    assert r.status_code == 401


def test_get_current_user_revoked_sid_401(db, redis_client):
    u = _seed_user(db, "gc2")
    token = tokens.encode_access(uid=u.id, sid="sid-rev", role="user")
    from app.web.services.auth import denylist
    denylist.revoke_sid(redis_client, "sid-rev", ttl_seconds=60)
    client = TestClient(_app(db, redis_client))
    r = client.get("/me", cookies={"access_token": token})
    assert r.status_code == 401


def test_get_current_user_epoch_bump_invalidates_old_token(db, redis_client):
    u = _seed_user(db, "gc3")
    token = tokens.encode_access(uid=u.id, sid="sid-3", role="user")
    time.sleep(1)
    from app.web.services.auth import denylist
    denylist.bump_user_epoch(redis_client, u.id, ttl_seconds=60)
    client = TestClient(_app(db, redis_client))
    r = client.get("/me", cookies={"access_token": token})
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_dependencies.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/web/services/auth/dependencies.py`**

```python
import redis as redis_lib
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.models import User
from app.core.redis_client import get_redis
from app.web.services.auth import denylist, tokens
from app.web.services.auth.exceptions import (
    AuthServiceUnavailable,
    InvalidToken,
)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise InvalidToken()
    try:
        claims = tokens.decode_access(token)
    except tokens.InvalidTokenError:
        raise InvalidToken()

    sid = claims["sid"]
    uid = claims["uid"]
    iat = claims["iat"]

    try:
        if denylist.is_sid_revoked(rc, sid):
            raise InvalidToken()
        epoch = denylist.get_user_epoch(rc, uid)
    except redis_lib.RedisError:
        raise AuthServiceUnavailable()
    if epoch is not None and iat < epoch:
        raise InvalidToken()

    user = db.get(User, uid)
    if user is None:
        raise InvalidToken()
    return user
```

Note: the test uses `app.dependency_overrides[get_db]` and `[get_redis]`. Make sure `get_db` from `app/core/db.py` is importable as a FastAPI dep (i.e., a generator function FastAPI can call). Check current `get_db`:

```bash
PYTHONPATH="app;app/web" python -c "from app.core.db import get_db; print(get_db)"
```

If the existing `get_db` is a context manager (not a generator dep), add a thin generator dep in `app/web/services/auth/dependencies.py` or `app/core/db.py`. (Existing test `test_get_db_round_trip` uses it as a context manager — `with get_db() as session:`. For FastAPI, write a small helper.)

If needed, add to `app/core/db.py`:

```python
def fastapi_db_dep():
    with get_db() as session:
        yield session
```

…and replace `from app.core.db import get_db` with `from app.core.db import fastapi_db_dep as get_db` in the dependency file and the override key in tests. (Adjust both the dependency module and the test to keep names consistent.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_dependencies.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/services/auth/dependencies.py tests/test_auth_dependencies.py
git commit -m "feat(auth): get_current_user dep with JWT + denylist + epoch checks"
```

---

## Task 14: Refactor /login to mint tokens + set cookies

**Files:**
- Modify: `app/web/controllers/auth.py`
- Modify: `tests/conftest.py` — add `client` fixture
- Create: `tests/test_auth_endpoints.py`

- [ ] **Step 1: Add `client` fixture to `tests/conftest.py`**

Append:

```python
@pytest.fixture
def client(db, redis_client):
    from fastapi.testclient import TestClient
    from app.core.db import get_db
    from app.core.redis_client import get_redis
    # Late import to ensure controllers load with env in place.
    from app.web.main import app
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write failing login test**

Create `tests/test_auth_endpoints.py`:

```python
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
    r = client.post("/login", json={"username": "ghost", "password": "pass1234"})
    assert r.status_code == 401


def test_login_session_limit_evicts_oldest(client, db, monkeypatch):
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "2")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()
    _signup(client, username="bob", email="bob@x.com")
    from app.core.models import RefreshSession, User
    for _ in range(3):
        r = client.post("/login", json={"username": "bob", "password": "pass1234"})
        assert r.status_code == 200
    user = db.query(User).filter_by(username="bob").one()
    active = db.query(RefreshSession).filter_by(user_id=user.id).filter(
        RefreshSession.used_at.is_(None)
    ).count()
    assert active == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_auth_endpoints.py -v`
Expected: 4 FAIL (login currently returns user without cookies/csrf and without session row).

- [ ] **Step 4: Replace `app/web/controllers/auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from datetime import datetime
import redis as redis_lib

from core.db import get_db
from core.models import User
from core.redis_client import get_redis
from services.auth import cookies, csrf, sessions, tokens
from services.auth.exceptions import InvalidCredentials

router = APIRouter(tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)


class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

    class Config:
        from_attributes = True


SignupResponse = UserResponse


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=72)


class LoginResponse(BaseModel):
    user: UserResponse
    csrf_token: str


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    new_user = User(
        username=payload.username,
        email=payload.email,
        password_hash=pwd_context.hash(payload.password),
    )
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already taken",
        )
    return new_user


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not pwd_context.verify(payload.password, user.password_hash):
        raise InvalidCredentials()

    sessions.enforce_limit(db, rc, user_id=user.id)
    plain_refresh, family_id = sessions.create_session(
        db,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    db.commit()

    access = tokens.encode_access(uid=user.id, sid=str(family_id), role=user.role)
    csrf_token = csrf.gen_csrf()
    cookies.set_auth_cookies(
        response, access=access, refresh=plain_refresh, csrf=csrf_token,
    )
    return {"user": user, "csrf_token": csrf_token}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_auth_endpoints.py -v -k "login"`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add app/web/controllers/auth.py tests/conftest.py tests/test_auth_endpoints.py
git commit -m "feat(auth): /login mints JWT, opens refresh session, sets cookies + CSRF"
```

---

## Task 15: /logout endpoint (single device)

**Files:**
- Modify: `app/web/controllers/auth.py`
- Modify: `tests/test_auth_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auth_endpoints.py`:

```python
def _login(client, username="alice", password="pass1234"):
    r = client.post("/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["csrf_token"]


def test_logout_deletes_family_and_clears_cookies(client, db, redis_client):
    _signup(client)
    csrf_tok = _login(client)
    r = client.post("/logout", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 204
    from app.core.models import RefreshSession, User
    user = db.query(User).filter_by(username="alice").one()
    assert db.query(RefreshSession).filter_by(user_id=user.id).count() == 0
    set_cookie = "\n".join(r.headers.get_list("set-cookie"))
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()


def test_logout_without_csrf_403(client):
    _signup(client)
    _login(client)
    r = client.post("/logout")
    assert r.status_code == 403


def test_logout_with_no_refresh_cookie_returns_204_anyway(client):
    # Clean-slate logout: nothing to invalidate, but still clear cookies.
    # We do not signal "no session" — 204 is the safe default.
    _signup(client)
    csrf_tok = _login(client)
    client.cookies.clear()
    client.cookies.set("csrf_token", csrf_tok)
    r = client.post("/logout", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_endpoints.py -v -k "logout"`
Expected: fail (no endpoint).

- [ ] **Step 3: Add `/logout` to controller**

Add to `app/web/controllers/auth.py` (after `/login`):

```python
from services.auth.cookies import REFRESH_COOKIE


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
    _csrf: None = Depends(csrf.require_csrf),
):
    plain = request.cookies.get(REFRESH_COOKIE)
    if plain:
        from sqlalchemy import select
        from core.models import RefreshSession
        hashed = tokens.hash_refresh(plain)
        row = db.execute(
            select(RefreshSession).where(RefreshSession.token_hash == hashed)
        ).scalar_one_or_none()
        if row is not None:
            sessions.revoke_family(db, rc, family_id=row.family_id)
            db.commit()
    cookies.clear_auth_cookies(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_endpoints.py -v -k "logout"`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/controllers/auth.py tests/test_auth_endpoints.py
git commit -m "feat(auth): POST /logout — revoke family, clear cookies, CSRF guarded"
```

---

## Task 16: /logout-all endpoint

**Files:**
- Modify: `app/web/controllers/auth.py`
- Modify: `tests/test_auth_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auth_endpoints.py`:

```python
def test_logout_all_deletes_all_sessions_and_bumps_epoch(client, db, redis_client):
    _signup(client)
    csrf_tok = _login(client)
    # Open a second session by logging in again (same user).
    csrf_tok2 = _login(client)
    r = client.post("/logout-all", headers={"X-CSRF-Token": csrf_tok2})
    assert r.status_code == 204
    from app.core.models import RefreshSession, User
    user = db.query(User).filter_by(username="alice").one()
    assert db.query(RefreshSession).filter_by(user_id=user.id).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, user.id) is not None


def test_logout_all_requires_auth(client):
    r = client.post("/logout-all", headers={"X-CSRF-Token": "x"})
    # No access_token cookie → InvalidToken → 401
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_endpoints.py -v -k "logout_all"`
Expected: fail.

- [ ] **Step 3: Add `/logout-all`**

Append to `app/web/controllers/auth.py`:

```python
from services.auth.dependencies import get_current_user


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
    _csrf: None = Depends(csrf.require_csrf),
):
    sessions.revoke_all_for_user(db, rc, user_id=user.id)
    db.commit()
    cookies.clear_auth_cookies(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_endpoints.py -v -k "logout_all"`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/controllers/auth.py tests/test_auth_endpoints.py
git commit -m "feat(auth): POST /logout-all — kick every device + bump user epoch"
```

---

## Task 17: /refresh endpoint

**Files:**
- Modify: `app/web/controllers/auth.py`
- Modify: `tests/test_auth_endpoints.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auth_endpoints.py`:

```python
def test_refresh_happy_rotates_and_returns_new_csrf(client, db, redis_client):
    _signup(client)
    csrf_tok = _login(client)
    r = client.post("/refresh", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 200
    body = r.json()
    assert "csrf_token" in body
    assert body["csrf_token"] != csrf_tok
    from app.core.models import RefreshSession, User
    user = db.query(User).filter_by(username="alice").one()
    rows = db.query(RefreshSession).filter_by(user_id=user.id).all()
    used = [r for r in rows if r.used_at is not None]
    active = [r for r in rows if r.used_at is None]
    assert len(used) == 1
    assert len(active) == 1
    assert active[0].parent_id == used[0].id


def test_refresh_with_reused_token_kills_family(client, db, redis_client):
    _signup(client)
    csrf_tok = _login(client)
    # Capture the refresh cookie value so we can replay it.
    old_refresh = client.cookies.get("refresh_token")
    # First refresh: rotates.
    r1 = client.post("/refresh", headers={"X-CSRF-Token": csrf_tok})
    assert r1.status_code == 200
    new_csrf = r1.json()["csrf_token"]
    # Force the OLD refresh cookie back in.
    client.cookies.set("refresh_token", old_refresh)
    r2 = client.post("/refresh", headers={"X-CSRF-Token": new_csrf})
    assert r2.status_code == 401
    from app.core.models import RefreshSession, User
    user = db.query(User).filter_by(username="alice").one()
    assert db.query(RefreshSession).filter_by(user_id=user.id).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, user.id) is not None


def test_refresh_without_cookie_401(client):
    _signup(client)
    csrf_tok = _login(client)
    client.cookies.delete("refresh_token")
    r = client.post("/refresh", headers={"X-CSRF-Token": csrf_tok})
    assert r.status_code == 401


def test_refresh_csrf_mismatch_403(client):
    _signup(client)
    _login(client)
    r = client.post("/refresh", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_endpoints.py -v -k "refresh"`
Expected: fail.

- [ ] **Step 3: Add `/refresh`**

Append to `app/web/controllers/auth.py`:

```python
class RefreshResponse(BaseModel):
    csrf_token: str


@router.post("/refresh", response_model=RefreshResponse)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
    _csrf: None = Depends(csrf.require_csrf),
):
    plain = request.cookies.get(REFRESH_COOKIE)
    if not plain:
        from services.auth.exceptions import InvalidToken
        cookies.clear_auth_cookies(response)
        raise InvalidToken()

    new_plain, family_id, sid = sessions.rotate(
        db,
        rc,
        plain,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    db.commit()

    from core.models import RefreshSession
    new_row = db.query(RefreshSession).filter_by(
        token_hash=tokens.hash_refresh(new_plain),
    ).one()
    user = db.get(User, new_row.user_id)

    access = tokens.encode_access(uid=user.id, sid=str(sid), role=user.role)
    csrf_token = csrf.gen_csrf()
    cookies.set_auth_cookies(
        response, access=access, refresh=new_plain, csrf=csrf_token,
    )
    return {"csrf_token": csrf_token}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth_endpoints.py -v -k "refresh"`
Expected: 4 PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`
Expected: every test passes (no regressions in models, migrations, etc.).

- [ ] **Step 6: Commit**

```bash
git add app/web/controllers/auth.py tests/test_auth_endpoints.py
git commit -m "feat(auth): POST /refresh — rotate session + detect reuse + new CSRF"
```

---

## Task 18: Cleanup script for expired refresh sessions

**Files:**
- Create: `scripts/cleanup_expired_sessions.py`
- Test: `tests/test_cleanup_script.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_cleanup_script.py`:

```python
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.models import RefreshSession, User


def test_cleanup_removes_rows_older_than_grace(db):
    u = User(username="cu1", email="cu1@x.com", password_hash="h")
    db.add(u); db.flush()
    # Old expired (>7d past expiry).
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(days=8),
    ))
    # Recently expired (<7d past expiry) — keep.
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(days=2),
    ))
    # Still valid — keep.
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="c" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=5),
    ))
    db.flush()

    from scripts.cleanup_expired_sessions import cleanup
    deleted = cleanup(db, grace_days=7)
    assert deleted == 1
    remaining = db.query(RefreshSession).count()
    assert remaining == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cleanup_script.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `scripts/cleanup_expired_sessions.py`**

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.models import RefreshSession


def cleanup(db: Session, *, grace_days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    deleted = (
        db.query(RefreshSession)
        .filter(RefreshSession.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted


def main() -> None:
    from app.core.db import get_db
    with get_db() as session:
        n = cleanup(session, grace_days=7)
        session.commit()
        print(f"deleted {n} expired refresh_sessions rows")


if __name__ == "__main__":
    main()
```

Add `scripts/__init__.py` (empty) if needed for pytest to import.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cleanup_script.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/cleanup_expired_sessions.py scripts/__init__.py tests/test_cleanup_script.py
git commit -m "feat(auth): cleanup script for expired refresh_sessions (grace 7d)"
```

---

## Final verification

- [ ] **Step 1: Full test suite green**

Run: `pytest -q`
Expected: ALL pass.

- [ ] **Step 2: App boots**

Run from project root:
```bash
PYTHONPATH="app;app/web" uvicorn web.main:app --reload --port 8000
```
Hit `http://localhost:8000/docs` in browser, confirm all endpoints listed: `POST /signup`, `POST /login`, `POST /logout`, `POST /logout-all`, `POST /refresh`, `GET /`.

- [ ] **Step 3: Manual smoke test via OpenAPI UI**

Walk through: signup → login → /docs Try It Out logs out cookies; manually copy `csrf_token` into the `X-CSRF-Token` header on `/refresh` and `/logout`. Expect 200/204 with rotated cookies.

- [ ] **Step 4: Tag end-of-feature commit**

```bash
git log --oneline -20
```
Confirm clean linear history of the 18 task commits.

---

## Notes for the executor

- **DB session boundary:** Endpoints call `db.commit()` explicitly after multi-step mutations (login: enforce_limit + create_session; refresh: rotate; logout/logout-all: revoke). `sessions.py` functions only `flush()` — never commit — so the controller owns the transaction.
- **Redis errors:** `get_current_user` already maps `redis.RedisError` to 503. The endpoints that touch Redis directly (login/logout/refresh) do not currently wrap Redis calls — if Redis fails mid-login, the exception will bubble as 500. If you want symmetry, add a top-level `try/except redis_lib.RedisError → AuthServiceUnavailable` in each endpoint. Not required for v1.
- **Cookie `Secure` flag in tests:** `COOKIE_SECURE=false` in test env so `TestClient` cookies survive across requests. Production sets it true.
- **Refresh cookie path:** spec specifies `Path=/`. Keep it that way — narrower paths break `/logout`.
- **CSRF cookie scope:** the CSRF cookie is `SameSite=Lax + Secure` (no HttpOnly). Frontend reads either the cookie or the response body — either works.
- **Don't add features the plan doesn't list.** No password reset, no email verify, no role guards. Those are sub-projects (b) and (c).
