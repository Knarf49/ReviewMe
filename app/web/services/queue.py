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
