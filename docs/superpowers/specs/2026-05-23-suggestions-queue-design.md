# Suggestions Queue Design

**Date:** 2026-05-23
**Status:** Approved, ready for implementation plan
**Scope:** Async job queue for `POST /suggestions` (Layer 4 project suggester)

---

## Problem

`run_layer4(jd, model)` takes 30–65s per call (research HTTP fetches + 2 LLM calls). Running it inside a synchronous FastAPI request handler would block the worker thread and time out reverse proxies. We need an asynchronous job queue so the HTTP request returns immediately with a `job_id`, and a background worker performs the work and persists the result.

A run log is already produced by `app/pipeline/project_suggester.log_run()` writing to `results/layer4_logs/<run_id>/`. This filesystem log will be replaced by Postgres persistence so all job state and results live in one queryable store.

## Goals

- Decouple HTTP request from Layer 4 execution.
- Persist every job (queued, running, done, error) per user in Postgres.
- Client retrieves status and final result by polling a single endpoint by `job_id`.
- One active job per user at a time, enforced atomically.
- Worker survives crashes — orphaned `running` jobs are recovered on boot.
- Reuse existing Docker Compose infra (Postgres + Redis already running).

## Non-goals

- Job cancellation API.
- Cross-user job priority / fairness.
- Result TTL / auto-delete.
- Prometheus / metrics endpoint.
- WebSocket or Server-Sent Events push.
- Multiple concurrent jobs per user.
- Migrating existing filesystem `results/layer4_logs/` history into Postgres (one-way going forward; existing dir kept as-is, ignored by app).

---

## Architecture

```
        ┌────────────┐    POST /suggestions      ┌──────────────┐
        │  Client    │ ────(auth, jd, model)───▶ │   FastAPI    │
        │            │ ◀──────  201 {job_id} ─── │   web svc    │
        │            │                           └──────┬───────┘
        │            │                                  │ 1. SELECT user active job
        │            │                                  │ 2. INSERT row status=queued
        │            │                                  │ 3. LPUSH job_id
        │            │                                  ▼
        │            │       ┌──────────────┐    ┌──────────────┐
        │            │       │  Postgres    │◀───│  Redis list  │
        │            │       │ suggestion_  │    │ suggestions: │
        │            │       │  jobs table  │    │   jobs       │
        │            │       └──────┬───────┘    └──────┬───────┘
        │            │              ▲                   │ BRPOP
        │            │              │ UPDATE state      │
        │            │              │                   ▼
        │            │              │            ┌──────────────┐
        │            │              └────────────│  worker svc  │
        │            │                           │  run_layer4  │
        │            │       GET /suggestions/{id}└──────────────┘
        │            │ ────(poll every 2-3s)───▶
        │            │ ◀──── {status, result?} ──
        └────────────┘
```

Three application services in `docker-compose.yml`:

- **`web`** (existing) — FastAPI, handles HTTP, enqueues jobs.
- **`worker`** (new) — Python loop, `BRPOP` from Redis, runs `run_layer4`, updates Postgres.
- **`postgres`** + **`redis`** (existing) — Postgres = source of truth (state + result). Redis list = pickup signal (transport only, holds no state).

The Layer 4 pipeline code in `app/pipeline/project_suggester.py` is unchanged. Only its caller changes (worker, not request handler). The filesystem `log_run()` is no longer invoked from the request path.

---

## Postgres schema

New table created by an Alembic migration. The migration must also enable the `pgcrypto` extension to provide `gen_random_uuid()`.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE suggestion_status AS ENUM ('queued', 'running', 'done', 'error');

CREATE TABLE suggestion_jobs (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status       suggestion_status NOT NULL DEFAULT 'queued',
  jd_text      text NOT NULL,
  model        text NOT NULL,
  result       jsonb,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz
);

CREATE UNIQUE INDEX suggestion_jobs_one_active_per_user
  ON suggestion_jobs (user_id)
  WHERE status IN ('queued', 'running');

CREATE INDEX suggestion_jobs_user_created_idx
  ON suggestion_jobs (user_id, created_at DESC);

CREATE INDEX suggestion_jobs_recovery_idx
  ON suggestion_jobs (status, started_at)
  WHERE status = 'running';
```

### Shape of `result` (JSONB)

Mirrors `Layer4Result.to_dict()`:

```json
{
  "suggestion": { "project_title": "...", "must_have": [], "stretch": [], ... },
  "model": "gpt-oss:20b-cloud",
  "elapsed_ms": 65190,
  "usage": { "prompt_tokens": 4595, "completion_tokens": 13055 },
  "research": { "queries": ["..."], "sources": [{...}] }
}
```

### Rationale

- **Single table, JSONB result column** — job lifecycle and result are 1:1; normalizing to a `suggestion_results` table would only produce mandatory joins with no schema benefit.
- **ENUM for status** — DB-enforced valid values; reject typos at the type level.
- **Partial unique index on `(user_id) WHERE status IN ('queued','running')`** — atomically enforces "one active job per user" without application-level locking. A second concurrent INSERT for the same user fails with `UniqueViolation`.
- **DB-side `gen_random_uuid()`** — no application UUID generation required; the INSERT's `RETURNING id` returns the new `job_id`.

---

## API contract

All endpoints require authentication via the existing auth middleware (JWT cookie + CSRF token).

### POST /suggestions

Request body:

```json
{ "jd_text": "string, min length 1", "model": "gpt-oss:20b-cloud" }
```

`model` is constrained to the `Model` enum already defined in `app/web/controllers/reviews.py`:
`gpt-oss:20b-cloud`, `gpt-oss:120b-cloud`, `gpt-5.4-mini`.

Response `201 Created`:

```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000" }
```

Errors:

| Status | Condition | Body |
|---|---|---|
| 401 | No / invalid auth | (auth middleware default) |
| 409 | User has an active (queued or running) job | `{"detail":"active job exists","active_job_id":"<uuid>"}` |
| 422 | Empty `jd_text` or invalid `model` | Pydantic validation error |
| 503 | Postgres INSERT committed but Redis LPUSH failed | `{"detail":"enqueue failed"}` (row marked `error` by cleanup) |

### GET /suggestions/{job_id}

Response shape varies by status. Common fields: `job_id`, `status`, `created_at`.

**Queued:**
```json
{ "job_id": "...", "status": "queued", "created_at": "..." }
```

**Running:**
```json
{ "job_id": "...", "status": "running", "created_at": "...", "started_at": "..." }
```

**Done:**
```json
{
  "job_id": "...", "status": "done",
  "created_at": "...", "started_at": "...", "finished_at": "...",
  "model": "gpt-oss:20b-cloud",
  "elapsed_ms": 65190,
  "result": { "suggestion": {...}, "research": {...}, "usage": {...} }
}
```

**Error:**
```json
{ "job_id": "...", "status": "error", "created_at": "...", "finished_at": "...", "error": "..." }
```

Errors:

| Status | Condition |
|---|---|
| 401 | No / invalid auth |
| 404 | `job_id` not found OR belongs to different user (existence not leaked) |
| 422 | `job_id` not a UUID |

### Pydantic models

In `app/web/controllers/reviews.py` (or renamed to `suggestions.py` — see Migration plan):

- `Model` (existing enum) — kept.
- `SuggestionsRequest { jd_text: str (min_length=1), model: Model }`
- `EnqueueResponse { job_id: UUID }`
- `JobStatusResponse { job_id: UUID, status: str, created_at: datetime, started_at: datetime | None, finished_at: datetime | None, model: str | None, elapsed_ms: int | None, result: dict | None, error: str | None }`

---

## Worker

### Layout

```
app/
  worker/
    main.py         # entrypoint: recovery + BRPOP loop
    runner.py       # process_job(): claim, run layer4, write result
  web/
    services/
      queue.py      # shared: REDIS_URL, QUEUE_KEY, redis client factory, enqueue helper
```

### Entrypoint (`app/worker/main.py`)

```python
async def main():
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await recover_orphans(redis_client)
    while True:
        _, job_id = await redis_client.brpop(QUEUE_KEY)
        try:
            await process_job(job_id)
        except Exception as e:
            logger.exception("process_job failed", extra={"job_id": job_id})
            await mark_error(job_id, str(e))

if __name__ == "__main__":
    asyncio.run(main())
```

### Job processor (`app/worker/runner.py`)

```python
async def process_job(job_id: str):
    # 1. Claim — only proceed if still 'queued'
    async with session() as s:
        row = await s.execute(
            update(SuggestionJob)
            .where(SuggestionJob.id == job_id, SuggestionJob.status == "queued")
            .values(status="running", started_at=func.now())
            .returning(SuggestionJob.jd_text, SuggestionJob.model)
        )
        claimed = row.one_or_none()
        await s.commit()
    if not claimed:
        return  # already terminal, or being processed elsewhere — drop silently

    # 2. Run layer4
    result = await run_layer4(claimed.jd_text, model=claimed.model)

    # 3. Persist result
    async with session() as s:
        await s.execute(
            update(SuggestionJob)
            .where(SuggestionJob.id == job_id)
            .values(status="done", result=result.to_dict(), finished_at=func.now())
        )
        await s.commit()
```

`mark_error(job_id, message)` mirrors the success update with `status='error'`, `error=message`, `finished_at=now()`.

### Crash recovery (`recover_orphans`)

Runs once at worker boot. Two responsibilities:

1. **Stale running** — jobs marked `running` whose `started_at` is older than 10 minutes are assumed orphaned; reset to `queued` and re-enqueue.
2. **Reconciliation** — every row currently `queued` is re-LPUSHed, in case Redis was lost or an INSERT committed before LPUSH crashed. Re-enqueuing duplicates is safe because `process_job`'s claim is atomic via `WHERE status='queued'`.

```python
STALE_THRESHOLD = timedelta(minutes=10)

async def recover_orphans(redis_client):
    async with session() as s:
        stale = await s.execute(
            update(SuggestionJob)
            .where(
                SuggestionJob.status == "running",
                SuggestionJob.started_at < func.now() - STALE_THRESHOLD,
            )
            .values(status="queued", started_at=None)
            .returning(SuggestionJob.id)
        )
        revived_ids = [r.id for r in stale.all()]

        queued_rows = await s.execute(
            select(SuggestionJob.id).where(SuggestionJob.status == "queued")
        )
        all_queued = [r.id for r in queued_rows.all()]
        await s.commit()

    for jid in all_queued:
        await redis_client.lpush(QUEUE_KEY, str(jid))
    logger.info("recovery complete", extra={
        "revived": len(revived_ids), "requeued": len(all_queued),
    })
```

**Why 10 minutes** — the longest observed Layer 4 run in `results/layer4_logs/index.jsonl` is ~65s. 10 min is comfortably above worst-case while still bounding orphan latency.

**Idempotency under retry** — Layer 4 has no external side effects (read-only HN/GitHub HTTP, no DB writes, no email/payment). Re-running after a crash is safe.

### Compose service

```yaml
worker:
  build:
    context: .
    dockerfile: Dockerfile
  container_name: reviewme-worker
  restart: unless-stopped
  command: ["python", "-m", "app.worker.main"]
  env_file: .env
  environment:
    DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
    REDIS_URL: redis://redis:6379/0
  depends_on:
    postgres: { condition: service_healthy }
    redis:    { condition: service_healthy }
  volumes:
    - ./app:/code/app
```

Same image as `web`, different `command`. Horizontal scaling via `docker compose up --scale worker=N` is safe because the atomic claim (`UPDATE WHERE status='queued' RETURNING`) admits exactly one winner per `job_id`.

---

## Error handling and edge cases

### Enqueue path

| Case | Handling |
|---|---|
| User has active job | Partial unique index raises `UniqueViolation` → 409 with `active_job_id` |
| Redis down at LPUSH | Two-step pattern: INSERT commits first, then LPUSH. On Redis error, UPDATE the row to `status='error'`, return 503. Boot reconciliation also requeues any orphaned `queued` row. |
| Empty `jd_text` | Pydantic `min_length=1` → 422 |
| Invalid `model` | Enum validation → 422 |
| No auth token | 401 from existing middleware |

### Worker path

| Case | Handling |
|---|---|
| `run_layer4` raises | catch → `mark_error(job_id, str(e))` → continue loop |
| Worker killed mid-job | Row stays `running`; recovery requeues at next boot once `started_at` is older than 10 min |
| Redis disconnect during BRPOP | `redis.asyncio` reconnects automatically; wrap loop in try/except with 1s sleep on error |
| Job popped, row gone (race) | `process_job` claim returns `None` → skip silently |
| Layer 4 returns invalid suggestion | Layer 4 already validates schema internally; on failure raises → caught → mark error |

### Poll path

| Case | Handling |
|---|---|
| `job_id` not UUID | 422 |
| `job_id` not found | 404 |
| Job belongs to another user | 404 (existence not leaked) |
| Client never stops polling | No server-side enforcement; client SHOULD stop on terminal status |

### Concurrency

- **Multi-worker safe** — claim is atomic via `UPDATE … WHERE status='queued' RETURNING`. Two workers cannot both win the same `job_id`.
- **One active job per user** — partial unique index is the authoritative enforcement, even if two concurrent POSTs race past any Python-side check.
- **Worker restart safe** — boot recovery covers stale running + queued reconciliation.

---

## Observability

Minimum for v1:

- Structured logs at each state transition with fields `job_id`, `user_id`, `model`, and (where applicable) `elapsed_ms`.
- Event names: `enqueued`, `claimed`, `done`, `error`, `recovery_complete`.

No metrics endpoint, dashboard, or tracing in scope. Defer.

---

## Testing

### Unit (`tests/unit/`)
- `test_queue_service.py` — INSERT + LPUSH happy path; 409 on conflict; Redis failure triggers cleanup UPDATE.
- `test_worker_runner.py` — `process_job` no-ops when status ≠ queued; success path writes result; exception path marks error.
- `test_recovery.py` — stale running → requeued; fresh running → untouched; every queued row re-LPUSHed.

Mocks: `run_layer4` returns a canned `Layer4Result`; Redis via `fakeredis` or async mock. Real Postgres via existing test fixture.

### Integration (`tests/integration/`)
- `test_suggestions_flow.py` — real Postgres + real Redis (via `testcontainers` or the dev Compose stack).
  - POST → 201 → row exists `queued` → Redis list contains the id.
  - Worker BRPOP → status transitions `queued` → `running` → `done`, poll returns the result.
  - Second POST while job is running → 409.
  - Kill worker mid-job → restart → recovery requeues and completes the job.

### API (`tests/api/`)
- `test_suggestions_endpoints.py` — `TestClient`-driven.
  - Unauthenticated → 401.
  - Cross-user `GET` → 404.
  - Invalid model / empty JD → 422.
  - Response shape matches the four documented states.

### Test isolation

Worker tests replace `run_layer4` with a stub returning a canned `Layer4Result` so the suite never hits real LLMs or the network. One opt-in end-to-end test marked `@pytest.mark.slow` exercises a real Layer 4 run.

---

## Migration plan

Implementation order:

1. **Alembic revision** — `pgcrypto` extension, `suggestion_status` enum, `suggestion_jobs` table, three indexes.
2. **Shared queue service** — `app/web/services/queue.py`: `REDIS_URL`, `QUEUE_KEY = "suggestions:jobs"`, `get_redis()` factory, `enqueue_suggestion(session, user_id, jd_text, model) -> uuid`.
3. **SQLAlchemy model** — `app/db/models/suggestion_job.py` matching the schema above.
4. **Worker** — `app/worker/main.py` + `app/worker/runner.py`.
5. **FastAPI endpoints** — fill the `POST /suggestions` stub in `app/web/controllers/reviews.py` and add `GET /suggestions/{job_id}`. Rename the file to `suggestions.py` (the empty `app/web/routes/suggestions.py` indicates this name was intended); update the import in `app/web/main.py`.
6. **Compose** — add `worker` service to `docker-compose.yml`.
7. **Tests** — unit → integration → API.
8. **Docs** — README updates: new endpoints, `worker` service, env vars.

## Acceptance criteria

- `docker compose up` brings web, worker, postgres, and redis to healthy.
- `POST /suggestions` returns 201 with a `job_id` in < 100 ms.
- `GET /suggestions/{id}` returns a terminal status (done or error) within the Layer 4 elapsed time + ~2 s polling jitter.
- A second `POST /suggestions` while the user has an active job returns 409.
- Killing the worker mid-job and restarting it results in the job completing within the recovery threshold (10 min) plus the Layer 4 runtime.
- All tests pass.

## Risks

| Risk | Mitigation |
|---|---|
| Layer 4 runs sometimes exceed 10 min, triggering false-positive recovery | Tune `STALE_THRESHOLD` after observing the production distribution; start at 10 min based on `results/layer4_logs/index.jsonl` worst case (~65 s). |
| `gen_random_uuid()` unavailable on the Postgres image | Alembic migration explicitly creates `pgcrypto`. |
| `redis.asyncio` connection pool exhaustion under load | One shared client per process, `decode_responses=True`. |
| `result` JSONB row too large | Observed bundles are 10–50 KB; Postgres jsonb limit is ~1 GB. If outliers appear, cap source count in `research_jd`. |
