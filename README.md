# ReviewMe

Skill-aware LLM code reviewer with Gradio UI + FastAPI auth backend.

## One-command stack (recommended)

```bash
cp .env.example .env       # one-time
docker compose up          # starts postgres + redis + web
```

The `web` service auto-applies alembic migrations on start, then launches the
FastAPI dev server on `http://localhost:8000` with hot reload. Source mounts
(`./app`, `./alembic`, `./run.py`) make host-side edits reload inside the
container — no rebuild needed for code changes.

- API docs: `http://localhost:8000/docs`
- pgAdmin (optional): `docker compose up -d pgadmin` → `http://localhost:5050`
- Stop everything: `docker compose down` (data volumes persist)
- Wipe data too: `docker compose down -v`
- Rebuild after `requirements.txt` changes: `docker compose build web && docker compose up -d`
- Tail web logs: `docker compose logs -f web`

## Auth endpoints

| Method | Path           | Notes                                                  |
|--------|----------------|--------------------------------------------------------|
| POST   | `/signup`      | Body `{username, email, password}` → 201 user         |
| POST   | `/login`       | Body `{email, password}` → 200 `{user, csrf_token}` + cookies |
| GET    | `/me`          | Requires access cookie → current user                 |
| POST   | `/refresh`     | Refresh cookie + `X-CSRF-Token` header → new cookies  |
| POST   | `/logout`      | Refresh cookie + CSRF → 204                           |
| POST   | `/logout-all`  | Access cookie + CSRF → 204 (kicks every device)       |

JWT access (2h) + opaque refresh (14d, rotation + reuse detection) in
HttpOnly cookies. Redis-backed instant revocation. Max 5 sessions per user
(oldest evicted on login). See `docs/superpowers/specs/2026-05-23-token-strategy-design.md`.

## Local-only run (without containerizing web)

If you prefer to run the web server on the host (faster iteration on Python
deps without rebuilding the image):

```bash
docker compose up -d postgres redis      # just infra
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python run.py            # http://localhost:8000
```

## Tests

```bash
.\.venv\Scripts\python -m pytest -q \
    --ignore=tests/test_ai_reviewer.py \
    --ignore=tests/test_project_suggester.py \
    --ignore=tests/test_research.py
```

(The ignored modules belong to the older Gradio reviewer feature and need
external services; the rest of the suite covers auth + db + migrations.)

## Migrations

```bash
# Inside the web container is easiest:
docker compose exec web alembic revision --autogenerate -m "describe change"
docker compose exec web alembic upgrade head
docker compose exec web alembic downgrade -1

# Or on the host (needs the venv + DATABASE_URL pointed at localhost:5432):
.\.venv\Scripts\python -m alembic upgrade head
```

## pgAdmin

- URL: `http://localhost:5050`
- Login: `admin@reviewme.dev` / `admin` (from `.env`)
- Server `reviewme-local` is auto-registered. Click it → password = `$POSTGRES_PASSWORD` from `.env`.

## Local password hashing utility

`hash_password.py` (gitignored, lives only on your machine) generates bcrypt
hashes for direct DB seeding or for verifying a row from the DB:

```bash
python hash_password.py 1234abcd                     # one-shot
python hash_password.py                              # interactive prompt
python hash_password.py -v 1234abcd '$2b$12$...'     # verify match
```

The `/signup` endpoint hashes the plain password server-side, so a normal
sign-up flow does **not** need this script — it's only for out-of-band DB
work.

## Port 5432 conflict (Windows)

If `psql`/SQLAlchemy reports `password authentication failed for user "reviewme"`,
a local Windows Postgres service is intercepting port 5432 (its accounts
don't match the docker container's). Stop it from an elevated PowerShell:

```powershell
Stop-Service postgresql-x64-17 -Force
```

Start it again later with `Start-Service postgresql-x64-17`.

## Project layout

```
app/
  core/        # SQLAlchemy models, db engine, redis client
  web/
    controllers/auth.py    # /signup, /login, /me, /logout, /logout-all, /refresh
    services/auth/         # tokens, cookies, csrf, sessions, denylist, dependencies, config, exceptions
    main.py                # FastAPI app
alembic/versions/          # migrations 0001 (users/threads/messages), 0002 (refresh_sessions)
tests/                     # pytest suite (auth + models + migrations)
docs/superpowers/          # design specs and implementation plans
docker-compose.yml         # postgres + redis + pgadmin + web
Dockerfile                 # web service image
run.py                     # uvicorn entrypoint
```
