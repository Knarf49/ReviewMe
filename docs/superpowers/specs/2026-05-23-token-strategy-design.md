# Token Strategy — Design Spec

**Date:** 2026-05-23
**Status:** Draft (awaiting user review)
**Scope:** Sub-project (a) of backend auth. Token issuance, transport, rotation, revocation. Excludes auth flows like password reset / email verify (sub-project b) and RBAC (sub-project c).

## Goals

- SPA-friendly auth on FastAPI + Postgres backend.
- Short-lived access (2h) + long-lived refresh (14d) with rotation.
- Logout one device or all devices, with instant access-token revocation.
- Detect refresh-token reuse (theft signal) and kill compromised chains.
- Max 5 concurrent sessions per user; oldest evicted on overflow.
- No new attack surface beyond standard SPA + cookie patterns.

## Non-goals

- OAuth/OIDC, social login, SSO.
- API keys, machine-to-machine auth.
- Email verification, password reset (sub-project b).
- Role-based authorization beyond carrying `role` in JWT claim (sub-project c).
- Refresh token sliding TTL extension (decision deferred to sub-project b).

## Decisions (locked by brainstorming Q&A)

| # | Decision | Choice |
|---|---|---|
| 1 | Client | Browser SPA |
| 2 | Storage | HttpOnly cookies (both tokens) |
| 3 | Access token format | JWT, HS256 |
| 4 | Refresh token format | Opaque random, sha256-hashed in DB |
| 5 | Access TTL | 2 hours |
| 6 | Refresh TTL | 14 days |
| 7 | Refresh rotation | Rotate every use + reuse detection |
| 8 | Logout-all | Yes (kill all sessions for user) |
| 9 | Instant access revocation | Yes, via Redis denylist |
| 10 | CSRF defense | SameSite=Lax + double-submit CSRF token |
| 11 | Concurrent sessions per user | Max 5; oldest evicted |
| 12 | Redis | Added to docker-compose (new service) |

## Architecture

### Components

| Module | Responsibility |
|---|---|
| `controllers/auth.py` | Endpoints: signup, login, logout, logout-all, refresh |
| `services/auth/tokens.py` | JWT encode/decode; refresh token gen + hash (pure) |
| `services/auth/sessions.py` | Refresh-session CRUD, rotation, reuse detection, session limit |
| `services/auth/cookies.py` | Set/clear HttpOnly cookies (pure response helpers) |
| `services/auth/csrf.py` | Gen CSRF token; `require_csrf` FastAPI dependency |
| `services/auth/denylist.py` | Redis wrappers: sid revoke, user-epoch bump/check |
| `services/auth/dependencies.py` | `get_current_user` dependency |
| `services/auth/exceptions.py` | Auth error hierarchy → HTTP mapping |
| `services/auth/config.py` | Env-loaded `JWTConfig` (validated on import) |
| `core/redis_client.py` | `redis.asyncio` singleton |
| `core/models.py` | + `RefreshSession` SQLAlchemy model |
| `alembic/versions/XXXX_add_refresh_sessions.py` | Migration |
| `docker-compose.yml` | + Redis service |

### File tree

```
app/
  web/
    controllers/
      auth.py
    services/
      auth/
        __init__.py
        tokens.py
        sessions.py
        cookies.py
        csrf.py
        denylist.py
        dependencies.py
        exceptions.py
        config.py
  core/
    models.py
    redis_client.py
  alembic/versions/
    XXXX_add_refresh_sessions.py
docker-compose.yml
```

## Data flow

### Login

```
POST /login {username, password}
  → verify password (bcrypt)
  → enforce_limit(user_id, max=5):
      if active_family_count >= 5: DELETE oldest family (cascade rows + revoke_sid in Redis)
  → family_id = uuid4()
  → plain_refresh = secrets.token_urlsafe(32)
  → INSERT refresh_sessions (user_id, family_id, token_hash=sha256(plain_refresh),
      parent_id=NULL, used_at=NULL, expires_at=now()+14d, user_agent, ip)
  → access_jwt = encode({uid, sid=family_id, role, iat, exp=iat+2h, iss, typ=access})
  → csrf = secrets.token_urlsafe(32)
  → set cookies: access_token (HttpOnly), refresh_token (HttpOnly, Path=/auth), csrf_token (JS-readable)
  → 200 {user, csrf_token}
```

### Refresh

```
POST /refresh  (cookie: refresh_token + header: X-CSRF-Token)
  → require_csrf
  → row = SELECT FROM refresh_sessions WHERE token_hash=sha256(cookie) FOR UPDATE
  → if row is None: 401 + clear cookies
  → if row.expires_at < now(): 401 + clear cookies (no family kill)
  → if row.used_at IS NOT NULL:
        DELETE FROM refresh_sessions WHERE family_id = row.family_id
        bump_user_epoch(row.user_id)
        clear cookies
        401 "Session terminated"
  → UPDATE row SET used_at = now()
  → new_plain = secrets.token_urlsafe(32)
  → INSERT refresh_sessions (same family_id, parent_id=row.id, token_hash=sha256(new_plain),
      used_at=NULL, expires_at=now()+14d, ...)
  → new access JWT (sid = same family_id)
  → rotate cookies + new CSRF
  → 200 {csrf_token}
```

### Protected request

```
any protected endpoint  (cookie: access_token, + X-CSRF-Token if mutating)
  → require_csrf (mutating only)
  → claims = decode_access(access_token):
        verify HS256, exp, iss, typ=access (with 10s leeway)
  → if is_sid_revoked(claims.sid): 401
  → epoch = get_user_epoch(claims.uid)
  → if epoch is not None and claims.iat < epoch: 401
  → user = db.get(User, claims.uid)
  → if user is None: 401
  → return user
```

### Logout (single device)

```
POST /logout  (cookie: refresh_token + X-CSRF-Token)
  → require_csrf
  → row = SELECT FROM refresh_sessions WHERE token_hash=sha256(cookie)
  → if row: family_id = row.family_id
            sid = family_id
            DELETE FROM refresh_sessions WHERE family_id = family_id
            revoke_sid(sid, ttl=access_remaining_seconds)
  → clear cookies (access, refresh, csrf)
  → 204
```

### Logout-all

```
POST /logout-all  (auth: Depends(get_current_user) + X-CSRF-Token)
  → require_csrf
  → DELETE FROM refresh_sessions WHERE user_id = user.id
  → bump_user_epoch(user.id, ttl=access_ttl)
  → clear cookies
  → 204
```

## DB schema

```sql
CREATE TABLE refresh_sessions (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id    UUID NOT NULL,
    token_hash   CHAR(64) NOT NULL UNIQUE,
    parent_id    BIGINT REFERENCES refresh_sessions(id),
    used_at      TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent   VARCHAR(255),
    ip           INET
);
CREATE INDEX ix_refresh_sessions_user_id    ON refresh_sessions(user_id);
CREATE INDEX ix_refresh_sessions_family_id  ON refresh_sessions(family_id);
CREATE INDEX ix_refresh_sessions_expires_at ON refresh_sessions(expires_at);
```

**Column rationale:**

- `token_hash` — sha256 (refresh = 32-byte random, high entropy → no bcrypt needed). DB leak still requires preimage to use.
- `family_id` UUID — represents one device login chain. Rotation preserves family_id; new login creates new family_id.
- `parent_id` — chain link for audit / debug.
- `used_at` — NULL = active. Set on rotation. Active row = `used_at IS NULL AND expires_at > now()`.
- `user_agent`, `ip` — audit only; never authoritative (spoofable).

## JWT claims (access token, HS256)

```json
{
  "uid": 42,
  "sid": "550e8400-e29b-41d4-a716-446655440000",
  "role": "user",
  "iat": 1748000000,
  "exp": 1748007200,
  "iss": "reviewme-auth",
  "typ": "access"
}
```

- `sid` = `family_id` (enables per-session revocation via Redis denylist).
- `role` cached in JWT — role change requires `/logout-all` to take effect (accepted trade-off).
- `typ` distinguishes future token kinds.
- Verification: HS256 signature, `exp`, `iss`, `typ == "access"`, 10s leeway.

## Cookies

| Cookie | Value | Attributes |
|---|---|---|
| `access_token` | JWT | `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=7200` |
| `refresh_token` | opaque 32-byte URL-safe | `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=1209600` |
| `csrf_token` | opaque 32-byte URL-safe | `Secure; SameSite=Lax; Path=/; Max-Age=7200` (no HttpOnly — JS reads it) |

- `Secure` controlled by `COOKIE_SECURE` env (`false` for local HTTP dev only).
- All cookies use `Path=/` — auth endpoints (`/login`, `/logout`, `/logout-all`, `/refresh`) live at root, not under a prefix. Stricter path scoping for `refresh_token` (e.g. `/refresh`) breaks `/logout` which also needs the refresh cookie. Acceptable: HttpOnly already prevents JS access; cookie travels with all same-site requests, which the SameSite=Lax + CSRF defenses already cover.
- Clear: set same name + path with `Max-Age=0`.

## CSRF (double-submit)

- On login + refresh: server generates `csrf_token` (random 32-byte URL-safe), sets non-HttpOnly cookie, returns same value in JSON body.
- Frontend reads from response body, stores in memory, sends as `X-CSRF-Token` header on all mutating requests (POST, PATCH, PUT, DELETE).
- Dependency `require_csrf`: compare `request.cookies["csrf_token"] == request.headers["X-CSRF-Token"]`. Constant-time comparison. Mismatch → 403.
- GET endpoints skip CSRF check.
- CSRF rotates with every access-token mint.

## Redis usage

Singleton `redis.asyncio` client. Keys:

| Key | Type | Purpose | TTL |
|---|---|---|---|
| `revoked_sid:{family_id}` | STRING `"1"` | Per-session denylist (logout single device, reuse detection eviction) | 2h (= `ACCESS_TTL_SECONDS`; safe upper bound — older JWTs already expired by signature check) |
| `user_epoch:{user_id}` | STRING (unix ts) | Per-user epoch; access JWTs with `iat < epoch` rejected (used by logout-all, reuse detection, future password change) | access TTL (2h) |

**Why epoch instead of jti list:** logout-all must invalidate every outstanding access JWT for a user. Listing all jtis is unbounded; epoch is O(1).

**Fail-closed:** if Redis is unreachable, `get_current_user` returns 503 (do not bypass denylist).

## Error model

```
AuthError (base, code: str, status: int)
├── InvalidCredentials    → 401 "Invalid username or password"
├── InvalidToken          → 401 "Invalid or expired session"
├── ReuseDetected         → 401 "Session terminated"
├── CsrfMismatch          → 403 "CSRF verification failed"
└── AuthServiceUnavailable → 503 "Auth service unavailable"
```

Global handler in `main.py` maps `AuthError` → JSON `{detail, code}`. Login never differentiates "user not found" vs "wrong password" — both return generic 401.

**Logging events** (no tokens, no passwords; structured):
- `auth.signup.success`
- `auth.login.success`, `auth.login.failure`
- `auth.refresh.success`, `auth.refresh.expired`, `auth.refresh.reuse_detected`
- `auth.logout.success`, `auth.logout_all.success`
- `auth.csrf.mismatch`
- `auth.redis.unavailable`

Each event includes `user_id`, `family_id`, `ip`, `user_agent` where applicable.

## Edge cases

| Case | Behavior |
|---|---|
| Clock skew | 10s leeway in JWT verification |
| Refresh cookie present, DB row missing | 401, clear cookies, no family kill |
| Refresh row exists but `used_at` set | Reuse → delete family + bump epoch + 401 + clear cookies |
| Refresh row exists but expired | 401, clear cookies, no family kill |
| User deleted while session active | `get_current_user` step 5 → 401; cascade FK already cleaned sessions |
| Password change (future) | Bump epoch + DELETE all refresh_sessions |
| Role change (future) | Same as password change |
| Redis unavailable | 503 fail-closed, do not bypass |
| Concurrent refresh from same browser (multi-tab) | `SELECT FOR UPDATE` on refresh row; loser triggers reuse → family killed. Mitigation: frontend single-flight refresh (separate spec). |
| Network retry of refresh after success | Browser commits Set-Cookie before app sees response, retry uses new token; if not, family killed (rare). |
| Login on new device | Independent `/login` flow → new `family_id`. Does not touch existing families. Not a reuse trigger. |
| Session limit hit on login (6th login) | Delete oldest active family + revoke its sid in Redis BEFORE inserting new family |
| IP / UA change mid-session | Not enforced; logged for audit only |

## Environment

```
JWT_SECRET=<base64 32 bytes>
JWT_ALG=HS256
JWT_ISSUER=reviewme-auth
ACCESS_TTL_SECONDS=7200
REFRESH_TTL_SECONDS=1209600
COOKIE_DOMAIN=
COOKIE_SECURE=true
SESSION_LIMIT_PER_USER=5
REDIS_URL=redis://redis:6379/0
```

`services/auth/config.py` validates on import: `len(JWT_SECRET) >= 32`. Fail-fast on misconfig.

## docker-compose

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports: ["6379:6379"]
    volumes: [redis-data:/data]
    command: ["redis-server", "--appendonly", "yes"]
volumes:
  redis-data:
```

The `web` service gains `depends_on: [postgres, redis]` and reads `REDIS_URL` from env.

## Cleanup job

Script `scripts/cleanup_expired_sessions.py`:
```sql
DELETE FROM refresh_sessions WHERE expires_at < now() - interval '7 days';
```

Schedule daily (cron / scheduled task). Redis cleans itself via TTL.

## Endpoint contracts

### `POST /signup` (existing)
- Body: `{username, email, password}`
- 201: `{id, username, email, created_at}`
- 409: username or email taken
- Does **not** issue tokens. User must `/login` next.

### `POST /login`
- Body: `{username, password}`
- 200: `{user: {id, username, email, created_at}, csrf_token}` + cookies set
- 401: invalid credentials

### `POST /logout`
- Cookie: `refresh_token`; Header: `X-CSRF-Token`
- 204
- 401 if no/invalid refresh cookie; 403 if CSRF mismatch

### `POST /logout-all`
- Auth: valid access token (`Depends(get_current_user)`); Header: `X-CSRF-Token`
- 204
- 401 if access invalid; 403 if CSRF mismatch

### `POST /refresh`
- Cookie: `refresh_token`; Header: `X-CSRF-Token`
- 200: `{csrf_token}` + rotated cookies
- 401: refresh missing / invalid / expired
- 401 (with side effects: family delete + epoch bump): reuse detected
- 403: CSRF mismatch

### Protected endpoint pattern

```python
@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user
```

## Testing

### Unit (no DB, no Redis)
- `tokens.py`: encode/decode round-trip; reject expired, bad signature, wrong `typ`, malformed.
- `cookies.py`: correct flags on set; clear sets Max-Age=0.
- `csrf.py`: gen produces 32-byte URL-safe; compare equal vs mismatch; constant-time compare.

### Integration (Postgres + Redis, via docker-compose or testcontainers)
- `POST /signup` happy path; password hashed; duplicates → 409.
- `POST /login` happy path: 200, cookies set, DB row inserted, CSRF token returned.
- `POST /login` wrong password / unknown user → 401, no row.
- `POST /login` 6th concurrent session: oldest family deleted from DB, sid revoked in Redis, count stays at 5.
- `POST /refresh` happy: new cookies, old row `used_at` set, new row has same `family_id` and `parent_id = old.id`.
- `POST /refresh` with already-used token: 401, entire family deleted from DB, user epoch bumped in Redis.
- `POST /refresh` with expired token: 401, cookies cleared, family NOT deleted.
- `POST /refresh` missing CSRF / mismatched CSRF → 403.
- `POST /logout`: family deleted, sid present in Redis, cookies cleared on response.
- `POST /logout-all`: all user families deleted, user epoch bumped.
- `GET /me` using OLD access JWT after `/logout-all` → 401 (epoch check rejects).
- `GET /me` using access JWT from revoked sid → 401.
- Redis down sim → 503 on protected endpoints.
- Clock skew: token with `iat` 5s in future passes; 15s rejected.

## Future hooks (out of scope here)

- Sub-project (b) — Auth flows: password reset, email verify, change-password kicks all sessions (bump epoch + delete sessions).
- Sub-project (c) — RBAC: `require_role("admin")` dependency reading `role` claim.
- Sliding refresh TTL (refresh extends every rotation) — decision in (b).
- Suspicious-login signals (new IP / new UA notification) — future.

## Approval

Decisions Q1–Q12 confirmed via brainstorming session 2026-05-23.
Design sections 1–5 reviewed inline and approved.
Spec to be reviewed by user; on approval, hand off to `writing-plans` skill.
