# Suggestions Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an async job queue for `POST /suggestions` so Layer 4 (30–65 s) runs in a background worker instead of the request thread.

**Architecture:** FastAPI INSERTs a `suggestion_jobs` row (status `queued`) and LPUSHes the `job_id` onto a Redis list. A separate `worker` Compose service runs a synchronous `BLPOP` loop, claims jobs atomically via `UPDATE … WHERE status='queued'`, calls `run_layer4_sync`, and persists the result as JSONB. Clients poll `GET /suggestions/{job_id}`.

**Tech Stack:** Python 3.12, FastAPI, sync SQLAlchemy 2.0 + Postgres 16, sync `redis` 5.x + Redis 7, Alembic, `pytest`, Docker Compose. Codebase uses sync SQLAlchemy `Session` via `app.core.db.db_dep`, sync redis via `app.core.redis_client.get_redis`, integer `User.id`. Models live in a single `app/core/models.py` file with a shared `Base = DeclarativeBase`.

**Spec:** `docs/superpowers/specs/2026-05-23-suggestions-queue-design.md`.

---

## File Structure

| File | Purpose | New / Modify |
|---|---|---|
| `alembic/versions/0003_suggestion_jobs.py` | Create `pgcrypto`, `suggestion_status` enum, `suggestion_jobs` table, 3 indexes. | Create |
| `app/core/models.py` | Append `SuggestionJob` mapped class. | Modify |
| `app/web/services/queue.py` | Redis client factory, constants (`QUEUE_KEY`), `enqueue_suggestion()` helper. | Create |
| `app/web/controllers/suggestions.py` | `POST /suggestions`, `GET /suggestions/{job_id}`. Replaces stub in `reviews.py`. | Create |
| `app/web/controllers/reviews.py` | Delete (logic moves to `suggestions.py`). | Delete |
| `app/web/routes/suggestions.py` | Delete the leftover `Item` stub. | Delete |
| `app/web/main.py` | Register the new suggestions router. | Modify |
| `app/worker/__init__.py` | Package marker. | Create |
| `app/worker/main.py` | Worker entrypoint: recovery + `BLPOP` loop. | Create |
| `app/worker/runner.py` | `process_job(job_id)` — claim, run Layer 4, write result. | Create |
| `app/worker/recovery.py` | `recover_orphans(redis_client)` — stale-running requeue + queued reconciliation. | Create |
| `docker-compose.yml` | Add a `worker` service sharing the `web` image. | Modify |
| `tests/test_suggestions_endpoints.py` | API tests for POST/GET endpoints. | Create |
| `tests/test_suggestions_queue.py` | Unit tests for `queue.py` enqueue helper. | Create |
| `tests/test_worker_runner.py` | Unit tests for `process_job`. | Create |
| `tests/test_worker_recovery.py` | Unit tests for `recover_orphans`. | Create |
| `tests/test_suggestions_integration.py` | Real-Redis full flow with a stubbed `run_layer4_sync`. | Create |
| `README.md` | Document new endpoints, env vars, the `worker` service. | Modify |

---

## Task 1: Alembic migration for `suggestion_jobs`

**Files:**
- Create: `alembic/versions/0003_suggestion_jobs.py`
- Test: `tests/test_migrations.py` (already runs upgrade/downgrade on every revision via the existing scaffolding — no new test needed here)

- [ ] **Step 1: Write the migration file**

Create `alembic/versions/0003_suggestion_jobs.py`:

```python
"""suggestion_jobs queue table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUGGESTION_STATUS = postgresql.ENUM(
    "queued", "running", "done", "error",
    name="suggestion_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        "CREATE TYPE suggestion_status AS ENUM "
        "('queued', 'running', 'done', 'error')"
    )

    op.create_table(
        "suggestion_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            SUGGESTION_STATUS,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_index(
        "suggestion_jobs_one_active_per_user",
        "suggestion_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "suggestion_jobs_user_created_idx",
        "suggestion_jobs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "suggestion_jobs_recovery_idx",
        "suggestion_jobs",
        ["status", "started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("suggestion_jobs_recovery_idx", table_name="suggestion_jobs")
    op.drop_index("suggestion_jobs_user_created_idx", table_name="suggestion_jobs")
    op.drop_index(
        "suggestion_jobs_one_active_per_user", table_name="suggestion_jobs",
    )
    op.drop_table("suggestion_jobs")
    op.execute("DROP TYPE suggestion_status")
```

- [ ] **Step 2: Run migration against dev DB to verify it works**

```bash
docker compose up -d postgres
alembic upgrade head
```

Expected output ends with: `Running upgrade 0002 -> 0003, suggestion_jobs queue table`.

- [ ] **Step 3: Verify downgrade works**

```bash
alembic downgrade -1
alembic upgrade head
```

Expected: clean apply both ways with no errors.

- [ ] **Step 4: Run existing migration tests**

```bash
pytest tests/test_migrations.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0003_suggestion_jobs.py
git commit -m "feat(db): add suggestion_jobs table + status enum"
```

---

## Task 2: Add `SuggestionJob` model

**Files:**
- Modify: `app/core/models.py` (append at end)
- Test: `tests/test_models.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_suggestion_job_round_trip(db):
    from app.core.models import SuggestionJob, User

    user = User(
        username="sj_user",
        email="sj@example.com",
        password_hash="x",
    )
    db.add(user)
    db.flush()

    job = SuggestionJob(
        user_id=user.id,
        jd_text="Backend engineer",
        model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.flush()

    assert job.id is not None
    assert job.status == "queued"
    assert job.result is None
    assert job.created_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_models.py::test_suggestion_job_round_trip -v
```

Expected: FAIL with `ImportError: cannot import name 'SuggestionJob'`.

- [ ] **Step 3: Implement the model**

Append to `app/core/models.py`:

```python
import uuid as _uuid  # already imported, leave existing import alone

from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID


class SuggestionJob(Base):
    __tablename__ = "suggestion_jobs"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        SAEnum(
            "queued", "running", "done", "error",
            name="suggestion_status",
            create_type=False,
        ),
        nullable=False,
        server_default="queued",
    )
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
```

(`uuid`, `datetime`, `Integer`, `ForeignKey`, `Text`, `TIMESTAMP`, `Mapped`, `mapped_column`, `func`, `Base` are already imported at the top of the file. The two new imports go alongside them: `from sqlalchemy import Enum as SAEnum` and `from sqlalchemy.dialects.postgresql import JSONB, UUID`.)

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_models.py::test_suggestion_job_round_trip -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/models.py tests/test_models.py
git commit -m "feat(models): add SuggestionJob mapped class"
```

---

## Task 3: Queue service — Redis key, client, enqueue helper

**Files:**
- Create: `app/web/services/queue.py`
- Test: `tests/test_suggestions_queue.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_suggestions_queue.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.models import SuggestionJob, User
from app.web.services.queue import QUEUE_KEY, enqueue_suggestion


@pytest.fixture
def make_user(db):
    def _make(username="qu", email="qu@example.com"):
        u = User(username=username, email=email, password_hash="x")
        db.add(u)
        db.flush()
        return u
    return _make


def test_enqueue_inserts_row_and_lpushes(db, redis_client, make_user):
    user = make_user()
    job_id = enqueue_suggestion(
        db, redis_client,
        user_id=user.id,
        jd_text="JD",
        model="gpt-oss:20b-cloud",
    )
    db.flush()

    row = db.get(SuggestionJob, job_id)
    assert row is not None
    assert row.status == "queued"
    assert row.user_id == user.id

    pushed = redis_client.lrange(QUEUE_KEY, 0, -1)
    assert str(job_id) in pushed


def test_enqueue_rejects_second_active_job_per_user(
    db, redis_client, make_user,
):
    user = make_user()
    enqueue_suggestion(
        db, redis_client,
        user_id=user.id, jd_text="JD1", model="gpt-oss:20b-cloud",
    )
    db.flush()

    with pytest.raises(IntegrityError):
        enqueue_suggestion(
            db, redis_client,
            user_id=user.id, jd_text="JD2", model="gpt-oss:20b-cloud",
        )
        db.flush()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_suggestions_queue.py -v
```

Expected: FAIL with `ImportError: cannot import name 'enqueue_suggestion'`.

- [ ] **Step 3: Implement the queue service**

Create `app/web/services/queue.py`:

```python
"""Redis-list-backed job queue for Layer 4 suggestion jobs.

Postgres is the source of truth for state and result. Redis carries the
job_id as a pickup signal only.
"""
from __future__ import annotations

import uuid

import redis
from sqlalchemy.orm import Session

from app.core.models import SuggestionJob

QUEUE_KEY = "suggestions:jobs"


def enqueue_suggestion(
    db: Session,
    rc: redis.Redis,
    *,
    user_id: int,
    jd_text: str,
    model: str,
) -> uuid.UUID:
    """Insert a queued row and push its id onto the Redis list.

    Raises `IntegrityError` if the user already has an active job
    (enforced by partial unique index `suggestion_jobs_one_active_per_user`).
    """
    job = SuggestionJob(user_id=user_id, jd_text=jd_text, model=model)
    db.add(job)
    db.flush()  # populate job.id from gen_random_uuid()

    try:
        rc.lpush(QUEUE_KEY, str(job.id))
    except redis.RedisError:
        # Mark the row as error so the user gets a clean failure and the
        # partial unique index releases for retry. Recovery on worker boot
        # also re-LPUSHes any orphan queued rows, but this is the fast path.
        job.status = "error"
        job.error = "enqueue failed: redis unavailable"
        db.flush()
        raise

    return job.id
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_suggestions_queue.py -v
```

Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add app/web/services/queue.py tests/test_suggestions_queue.py
git commit -m "feat(queue): add Redis-list enqueue helper for suggestion jobs"
```

---

## Task 4: `POST /suggestions` endpoint

**Files:**
- Create: `app/web/controllers/suggestions.py`
- Test: `tests/test_suggestions_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_suggestions_endpoints.py`:

```python
import pytest
from app.core.models import SuggestionJob, User


@pytest.fixture
def signed_in_user(client, db):
    # Sign up + log in to get auth cookies.
    payload = {
        "username": "sjuser",
        "email": "sj@example.com",
        "password": "password123",
    }
    client.post("/signup", json=payload)
    r = client.post(
        "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert r.status_code == 200
    csrf = r.json()["csrf_token"]
    user = db.query(User).filter_by(email=payload["email"]).one()
    return user, csrf


def test_post_suggestions_creates_queued_job(
    client, db, redis_client, signed_in_user,
):
    user, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "Backend engineer", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    row = db.get(SuggestionJob, job_id)
    assert row is not None
    assert row.status == "queued"
    assert row.user_id == user.id

    pushed = redis_client.lrange("suggestions:jobs", 0, -1)
    assert job_id in pushed


def test_post_suggestions_requires_auth(client):
    r = client.post(
        "/suggestions",
        json={"jd_text": "JD", "model": "gpt-oss:20b-cloud"},
    )
    assert r.status_code == 401


def test_post_suggestions_rejects_active_job(
    client, db, redis_client, signed_in_user,
):
    _, csrf = signed_in_user
    body = {"jd_text": "JD", "model": "gpt-oss:20b-cloud"}
    headers = {"X-CSRF-Token": csrf}
    first = client.post("/suggestions", json=body, headers=headers)
    assert first.status_code == 201

    second = client.post("/suggestions", json=body, headers=headers)
    assert second.status_code == 409
    assert second.json()["active_job_id"] == first.json()["job_id"]


def test_post_suggestions_empty_jd_rejected(client, signed_in_user):
    _, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_post_suggestions_invalid_model_rejected(client, signed_in_user):
    _, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "JD", "model": "not-a-model"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_suggestions_endpoints.py -v
```

Expected: FAIL — endpoint not registered, returns 404 (or import error).

- [ ] **Step 3: Implement the POST endpoint**

Create `app/web/controllers/suggestions.py`:

```python
import uuid
from enum import Enum

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import db_dep as get_db
from app.core.models import SuggestionJob, User
from app.core.redis_client import get_redis
from app.web.services.auth import csrf
from app.web.services.auth.dependencies import get_current_user
from app.web.services.queue import enqueue_suggestion


class Model(str, Enum):
    gpt_oss_20b = "gpt-oss:20b-cloud"
    gpt_oss_120b = "gpt-oss:120b-cloud"
    gpt_5_4_mini = "gpt-5.4-mini"


router = APIRouter(tags=["suggestions"])


class SuggestionsRequest(BaseModel):
    jd_text: str = Field(..., min_length=1)
    model: Model


class EnqueueResponse(BaseModel):
    job_id: uuid.UUID


@router.post(
    "/suggestions",
    response_model=EnqueueResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_suggestions(
    payload: SuggestionsRequest,
    db: Session = Depends(get_db),
    rc: redis_lib.Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf.require_csrf),
):
    try:
        job_id = enqueue_suggestion(
            db, rc,
            user_id=user.id,
            jd_text=payload.jd_text,
            model=payload.model.value,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        active = db.execute(
            select(SuggestionJob.id)
            .where(
                SuggestionJob.user_id == user.id,
                SuggestionJob.status.in_(("queued", "running")),
            )
        ).scalar_one()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": "active job exists", "active_job_id": str(active)},
        )
    except redis_lib.RedisError:
        # enqueue_suggestion has already flushed status='error' onto the row
        # so the partial unique index releases. Commit that bookkeeping then
        # surface 503 to the client.
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="enqueue failed",
        )
    return EnqueueResponse(job_id=job_id)
```

- [ ] **Step 4: Register the router**

Modify `app/web/main.py`:

```python
from fastapi import FastAPI

from app.web.controllers.auth import router as auth_router
from app.web.controllers.suggestions import router as suggestions_router
from app.web.services.auth.exceptions import register_auth_error_handler

app = FastAPI()
register_auth_error_handler(app)
app.include_router(auth_router)
app.include_router(suggestions_router)


@app.get("/")
def read_root():
    return {"Hello": "World"}
```

- [ ] **Step 5: Delete stale files**

```bash
git rm app/web/controllers/reviews.py app/web/routes/suggestions.py
```

(Both are empty / leftover stubs not imported anywhere.)

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_suggestions_endpoints.py -v
```

Expected: 4 of 5 PASS. `test_post_suggestions_requires_auth` may need adjustment if the auth middleware returns a different status (verify with existing `tests/test_auth_endpoints.py` for parity).

If `test_post_suggestions_requires_auth` fails, change `assert r.status_code == 401` to match whatever the existing `get_current_user` dep returns when no cookie is present (likely 401 or 403). Document the chosen value in the spec comment.

- [ ] **Step 7: Commit**

```bash
git add app/web/controllers/suggestions.py app/web/main.py tests/test_suggestions_endpoints.py
git commit -m "feat(api): POST /suggestions enqueues async job"
```

---

## Task 5: `GET /suggestions/{job_id}` endpoint

**Files:**
- Modify: `app/web/controllers/suggestions.py`
- Modify: `tests/test_suggestions_endpoints.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_suggestions_endpoints.py`:

```python
import uuid
from datetime import datetime, timezone

from app.core.models import SuggestionJob


def test_get_suggestion_returns_queued_state(
    client, db, signed_in_user,
):
    user, csrf = signed_in_user
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"] == str(job.id)
    assert body["result"] is None
    assert body["error"] is None


def test_get_suggestion_returns_done_payload(
    client, db, signed_in_user,
):
    user, _ = signed_in_user
    job = SuggestionJob(
        user_id=user.id,
        jd_text="JD",
        model="gpt-oss:20b-cloud",
        status="done",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        result={
            "suggestion": {"project_title": "T"},
            "model": "gpt-oss:20b-cloud",
            "elapsed_ms": 1234,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "research": {"queries": [], "sources": []},
        },
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["suggestion"]["project_title"] == "T"
    assert body["elapsed_ms"] == 1234
    assert body["model"] == "gpt-oss:20b-cloud"


def test_get_suggestion_404_when_missing(client, signed_in_user):
    r = client.get(f"/suggestions/{uuid.uuid4()}")
    assert r.status_code == 404


def test_get_suggestion_404_when_other_user(client, db, signed_in_user):
    # signed_in_user is user A. Create a job for a different user B.
    from app.core.models import User
    other = User(username="other", email="other@e.com", password_hash="x")
    db.add(other)
    db.flush()
    job = SuggestionJob(
        user_id=other.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 404


def test_get_suggestion_invalid_uuid(client, signed_in_user):
    r = client.get("/suggestions/not-a-uuid")
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_suggestions_endpoints.py::test_get_suggestion_returns_queued_state -v
```

Expected: FAIL with 404 (endpoint missing).

- [ ] **Step 3: Add the GET endpoint**

Append to `app/web/controllers/suggestions.py`:

```python
from datetime import datetime


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model: str | None = None
    elapsed_ms: int | None = None
    result: dict | None = None
    error: str | None = None


@router.get("/suggestions/{job_id}", response_model=JobStatusResponse)
def get_suggestion(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(SuggestionJob, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    elapsed_ms = None
    model_field = None
    if row.status == "done" and isinstance(row.result, dict):
        elapsed_ms = row.result.get("elapsed_ms")
        model_field = row.result.get("model") or row.model

    return JobStatusResponse(
        job_id=row.id,
        status=row.status,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        model=model_field,
        elapsed_ms=elapsed_ms,
        result=row.result,
        error=row.error,
    )
```

- [ ] **Step 4: Run all suggestion endpoint tests**

```bash
pytest tests/test_suggestions_endpoints.py -v
```

Expected: PASS (all tests, including the 5 added in this task).

- [ ] **Step 5: Commit**

```bash
git add app/web/controllers/suggestions.py tests/test_suggestions_endpoints.py
git commit -m "feat(api): GET /suggestions/{job_id} poll endpoint"
```

---

## Task 6: Worker — `process_job`

**Files:**
- Create: `app/worker/__init__.py` (empty)
- Create: `app/worker/runner.py`
- Test: `tests/test_worker_runner.py`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p app/worker
: > app/worker/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_worker_runner.py`:

```python
from unittest.mock import patch

import pytest

from app.core.models import SuggestionJob, User
from app.pipeline.project_suggester import CallUsage, Layer4Result
from app.pipeline.research import ResearchResult


@pytest.fixture
def make_user(db):
    def _make():
        u = User(username="wr", email="wr@e.com", password_hash="x")
        db.add(u)
        db.flush()
        return u
    return _make


@pytest.fixture
def queued_job(db, make_user):
    user = make_user()
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()
    return job


def _fake_layer4_result(model="gpt-oss:20b-cloud"):
    return Layer4Result(
        suggestion={"project_title": "Fake"},
        model=model,
        elapsed_ms=42,
        usage=CallUsage(prompt_tokens=1, completion_tokens=2),
        research=ResearchResult(),
    )


def test_process_job_success(db, queued_job):
    from app.worker.runner import process_job

    with patch(
        "app.worker.runner.run_layer4_sync",
        return_value=_fake_layer4_result(),
    ):
        process_job(db, str(queued_job.id))

    db.refresh(queued_job)
    assert queued_job.status == "done"
    assert queued_job.result["suggestion"]["project_title"] == "Fake"
    assert queued_job.finished_at is not None
    assert queued_job.started_at is not None


def test_process_job_marks_error_on_exception(db, queued_job):
    from app.worker.runner import process_job

    with patch(
        "app.worker.runner.run_layer4_sync",
        side_effect=RuntimeError("boom"),
    ):
        process_job(db, str(queued_job.id))

    db.refresh(queued_job)
    assert queued_job.status == "error"
    assert "boom" in queued_job.error
    assert queued_job.finished_at is not None


def test_process_job_skips_non_queued(db, queued_job):
    from app.worker.runner import process_job

    queued_job.status = "done"
    queued_job.result = {"already": "done"}
    db.commit()

    with patch("app.worker.runner.run_layer4_sync") as m:
        process_job(db, str(queued_job.id))
        m.assert_not_called()

    db.refresh(queued_job)
    assert queued_job.status == "done"
    assert queued_job.result == {"already": "done"}
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_worker_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker.runner'`.

- [ ] **Step 4: Implement `process_job`**

Create `app/worker/runner.py`:

```python
"""Worker job processor: atomic claim, run Layer 4, persist result."""
from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.core.models import SuggestionJob
from app.pipeline.project_suggester import run_layer4_sync

logger = logging.getLogger(__name__)


def process_job(db: Session, job_id: str) -> None:
    """Claim the job atomically, run Layer 4, write the result.

    If the row is not in 'queued' state, do nothing — another worker
    already claimed it, or the row was already moved to a terminal state.
    """
    claimed = db.execute(
        update(SuggestionJob)
        .where(
            SuggestionJob.id == job_id,
            SuggestionJob.status == "queued",
        )
        .values(status="running", started_at=func.now())
        .returning(SuggestionJob.jd_text, SuggestionJob.model)
    ).one_or_none()
    db.commit()

    if claimed is None:
        logger.info("skip non-queued job", extra={"job_id": job_id})
        return

    jd_text, model = claimed

    try:
        result = run_layer4_sync(jd_text, model=model)
    except Exception as e:
        logger.exception("layer4 failed", extra={"job_id": job_id})
        db.execute(
            update(SuggestionJob)
            .where(SuggestionJob.id == job_id)
            .values(
                status="error",
                error=str(e),
                finished_at=func.now(),
            )
        )
        db.commit()
        return

    db.execute(
        update(SuggestionJob)
        .where(SuggestionJob.id == job_id)
        .values(
            status="done",
            result=result.to_dict(),
            finished_at=func.now(),
        )
    )
    db.commit()
    logger.info(
        "job done",
        extra={"job_id": job_id, "elapsed_ms": result.elapsed_ms},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_worker_runner.py -v
```

Expected: PASS (all 3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/worker/__init__.py app/worker/runner.py tests/test_worker_runner.py
git commit -m "feat(worker): process_job atomic claim + persist result"
```

---

## Task 7: Worker — crash recovery

**Files:**
- Create: `app/worker/recovery.py`
- Test: `tests/test_worker_recovery.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_worker_recovery.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import SuggestionJob, User
from app.web.services.queue import QUEUE_KEY


@pytest.fixture
def user(db):
    u = User(username="rec", email="rec@e.com", password_hash="x")
    db.add(u)
    db.flush()
    return u


def test_recovery_requeues_stale_running(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    stale = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add(stale)
    db.commit()

    recover_orphans(db, redis_client)

    db.refresh(stale)
    assert stale.status == "queued"
    assert stale.started_at is None
    assert str(stale.id) in redis_client.lrange(QUEUE_KEY, 0, -1)


def test_recovery_leaves_fresh_running_alone(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    fresh = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
        status="running",
        started_at=datetime.now(timezone.utc),  # just started
    )
    db.add(fresh)
    db.commit()

    recover_orphans(db, redis_client)

    db.refresh(fresh)
    assert fresh.status == "running"
    assert str(fresh.id) not in redis_client.lrange(QUEUE_KEY, 0, -1)


def test_recovery_requeues_all_queued_rows(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    # Pretend Redis lost the entry — list is empty.
    redis_client.delete(QUEUE_KEY)

    recover_orphans(db, redis_client)

    assert str(job.id) in redis_client.lrange(QUEUE_KEY, 0, -1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_worker_recovery.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker.recovery'`.

- [ ] **Step 3: Implement recovery**

Create `app/worker/recovery.py`:

```python
"""Boot-time recovery for the suggestion worker.

Resets stale 'running' rows back to 'queued' and re-enqueues every queued
row to Redis, covering both worker crashes and a Redis flush.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.core.models import SuggestionJob
from app.web.services.queue import QUEUE_KEY

logger = logging.getLogger(__name__)

STALE_THRESHOLD = timedelta(minutes=10)


def recover_orphans(db: Session, rc: redis.Redis) -> None:
    """Revive stale running rows, then re-LPUSH every queued row."""
    revived = db.execute(
        update(SuggestionJob)
        .where(
            SuggestionJob.status == "running",
            SuggestionJob.started_at
            < func.now() - func.cast(STALE_THRESHOLD, type_=None),
        )
        .values(status="queued", started_at=None)
        .returning(SuggestionJob.id)
    ).all()
    db.commit()

    queued_ids = db.execute(
        select(SuggestionJob.id).where(SuggestionJob.status == "queued")
    ).scalars().all()

    for jid in queued_ids:
        rc.lpush(QUEUE_KEY, str(jid))

    logger.info(
        "recovery complete",
        extra={
            "revived": len(revived),
            "requeued": len(queued_ids),
        },
    )
```

**Note on the `func.cast(STALE_THRESHOLD, …)` line above:** SQLAlchemy + psycopg do not bind a `timedelta` directly into `now() - <interval>` in all versions. If the test fails at this line, replace the `where` clause with an explicit literal:

```python
from sqlalchemy import text

.where(
    SuggestionJob.status == "running",
    SuggestionJob.started_at < func.now() - text("interval '10 minutes'"),
)
```

Pick whichever the test accepts. Don't ship `func.cast(timedelta, type_=None)` if it errored — keep only the working version.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_worker_recovery.py -v
```

Expected: PASS (all 3 tests). If `test_recovery_requeues_stale_running` fails on the interval expression, swap in the `text("interval '10 minutes'")` form shown above and re-run.

- [ ] **Step 5: Commit**

```bash
git add app/worker/recovery.py tests/test_worker_recovery.py
git commit -m "feat(worker): boot recovery for stale running + queued reconciliation"
```

---

## Task 8: Worker — entrypoint with BLPOP loop

**Files:**
- Create: `app/worker/main.py`

- [ ] **Step 1: Implement the entrypoint**

Create `app/worker/main.py`:

```python
"""Suggestion worker entrypoint.

Runs once-at-boot recovery, then blocks on Redis BLPOP forever, calling
process_job for every popped job_id. One SQLAlchemy session per iteration
to keep transactions short.
"""
from __future__ import annotations

import logging
import signal
import sys
import time

import redis as redis_lib

from app.core.db import SessionLocal
from app.core.redis_client import get_redis
from app.web.services.queue import QUEUE_KEY
from app.worker.recovery import recover_orphans
from app.worker.runner import process_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.worker")

BLPOP_TIMEOUT = 5  # seconds; lets the loop check for shutdown signals
_running = True


def _stop(_signum, _frame):
    global _running
    _running = False
    logger.info("shutdown signal received")


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    rc = get_redis()

    # Boot recovery — one session, separate from the loop.
    with SessionLocal() as db:
        recover_orphans(db, rc)

    while _running:
        try:
            popped = rc.blpop(QUEUE_KEY, timeout=BLPOP_TIMEOUT)
        except redis_lib.RedisError as e:
            logger.exception("BLPOP failed: %s", e)
            time.sleep(1)
            continue

        if popped is None:
            continue  # idle tick

        _, job_id = popped
        try:
            with SessionLocal() as db:
                process_job(db, job_id)
        except Exception:
            logger.exception("job processing failed", extra={"job_id": job_id})

    logger.info("worker exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test the entrypoint locally (manual)**

```bash
docker compose up -d postgres redis
alembic upgrade head
python -m app.worker.main &
WORKER_PID=$!
sleep 2
# Enqueue a fake job by hand via redis-cli — worker will pick it up, then
# fail because the row does not exist (process_job claim returns None).
redis-cli -h localhost LPUSH suggestions:jobs 00000000-0000-0000-0000-000000000000
sleep 1
kill -TERM $WORKER_PID
wait $WORKER_PID
```

Expected output includes: `recovery complete`, `skip non-queued job`, `shutdown signal received`, `worker exited cleanly`.

- [ ] **Step 3: Commit**

```bash
git add app/worker/main.py
git commit -m "feat(worker): BLPOP loop with graceful shutdown + recovery on boot"
```

---

## Task 9: Compose service for the worker

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the worker service**

Modify `docker-compose.yml` — append below the existing `web:` block, before `volumes:`:

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
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/code/app
```

- [ ] **Step 2: Verify the stack comes up**

```bash
docker compose up -d --build
docker compose ps
```

Expected: `reviewme-postgres`, `reviewme-redis`, `reviewme-pgadmin`, `reviewme-web`, `reviewme-worker` all `Up` (web + worker `running`, db services `healthy`).

```bash
docker compose logs worker --tail 20
```

Expected to include: `recovery complete`.

- [ ] **Step 3: Tear down**

```bash
docker compose down
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(infra): add worker compose service"
```

---

## Task 10: End-to-end integration test

**Files:**
- Create: `tests/test_suggestions_integration.py`

This test exercises the full path: HTTP enqueue → Redis → `process_job` (with a stubbed Layer 4) → poll endpoint returns `done`.

- [ ] **Step 1: Write the test**

Create `tests/test_suggestions_integration.py`:

```python
"""Integration test for the suggestions queue.

Does NOT spawn the worker process. Calls process_job() directly in-process
after the HTTP enqueue, so the test does not depend on container timing.
Layer 4 is stubbed.
"""
from unittest.mock import patch

import pytest

from app.core.models import User
from app.pipeline.project_suggester import CallUsage, Layer4Result
from app.pipeline.research import ResearchResult
from app.web.services.queue import QUEUE_KEY


@pytest.fixture
def signed_in_user(client, db):
    payload = {
        "username": "intu",
        "email": "intu@e.com",
        "password": "password123",
    }
    client.post("/signup", json=payload)
    r = client.post(
        "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    csrf = r.json()["csrf_token"]
    user = db.query(User).filter_by(email=payload["email"]).one()
    return user, csrf


def _fake_result():
    return Layer4Result(
        suggestion={"project_title": "Integration"},
        model="gpt-oss:20b-cloud",
        elapsed_ms=99,
        usage=CallUsage(prompt_tokens=3, completion_tokens=4),
        research=ResearchResult(),
    )


def test_full_enqueue_process_poll_flow(
    client, db, redis_client, signed_in_user,
):
    _, csrf = signed_in_user

    # 1. Enqueue
    r = client.post(
        "/suggestions",
        json={"jd_text": "Backend", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # 2. Redis carries the job_id
    popped = redis_client.brpop(QUEUE_KEY, timeout=1)
    assert popped is not None
    _, popped_id = popped
    assert popped_id == job_id

    # 3. Run process_job (stubbed Layer 4)
    from app.worker.runner import process_job
    with patch(
        "app.worker.runner.run_layer4_sync",
        return_value=_fake_result(),
    ):
        process_job(db, job_id)

    # 4. Poll endpoint returns the result
    r = client.get(f"/suggestions/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["suggestion"]["project_title"] == "Integration"
    assert body["elapsed_ms"] == 99
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/test_suggestions_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full suite once**

```bash
pytest -x
```

Expected: all tests PASS (including pre-existing auth + research suites).

- [ ] **Step 4: Commit**

```bash
git add tests/test_suggestions_integration.py
git commit -m "test: end-to-end suggestions queue flow"
```

---

## Task 11: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Suggestions queue" section**

Add the following section to `README.md` (under the existing API/endpoints heading; if no such heading exists, append a new one):

```markdown
## Suggestions queue

`POST /suggestions` runs Layer 4 (project suggester) asynchronously. The
request returns immediately with a `job_id`; a background worker picks
up the job from Redis, runs `run_layer4_sync`, and writes the result to
the `suggestion_jobs` table. Clients poll `GET /suggestions/{job_id}`.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/suggestions` | Enqueue a new job. Requires auth + CSRF. One active job per user. |
| `GET` | `/suggestions/{job_id}` | Status + result. Returns 404 for unknown ids or jobs owned by another user. |

### Running locally

```bash
docker compose up -d --build
```

Brings up `postgres`, `redis`, `pgadmin`, `web`, and `worker`. Logs:

```bash
docker compose logs worker -f
```

### Crash recovery

The worker, at startup, resets `running` jobs older than 10 minutes back
to `queued` and re-enqueues every `queued` row to Redis. Layer 4 has no
side effects, so retries are safe.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document suggestions queue endpoints + worker service"
```

---

## Acceptance check

Run the full suite and bring up the stack one more time:

- [ ] `pytest -x` — all green.
- [ ] `docker compose up -d --build` — all 5 services healthy/running.
- [ ] `curl -X POST http://localhost:8000/signup -H 'content-type: application/json' -d '{"username":"a","email":"a@a.com","password":"password123"}'` returns 201.
- [ ] Log in (capture cookies + csrf), POST `/suggestions` with a valid JD returns 201 in under 100 ms.
- [ ] Polling `GET /suggestions/{job_id}` transitions through `queued` → `running` → `done` within Layer 4 elapsed + a few seconds.
- [ ] Posting a second `/suggestions` while the first job is active returns 409 with `active_job_id` in the body.

---

## Self-review notes

- Spec sections all map to tasks: schema → Task 1+2, queue/transport → Task 3, API → Tasks 4+5, worker → Tasks 6+7+8, compose → Task 9, tests → Tasks 3+4+5+6+7+10, docs → Task 11.
- No `TBD` / `TODO` markers.
- Naming consistent across tasks: `QUEUE_KEY`, `enqueue_suggestion`, `process_job`, `recover_orphans`, `SuggestionJob`, `JobStatusResponse`, `STALE_THRESHOLD`.
- `run_layer4_sync` (existing in `app/pipeline/project_suggester.py`) is the sole entry point from worker — not the async `run_layer4` — matching the sync SQLAlchemy world of the rest of the codebase.
- The `func.cast(timedelta, …)` line in Task 7 is flagged with a tested fallback (`text("interval '10 minutes'")`) so the implementer is not blocked.
