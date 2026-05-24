"""Boot-time recovery for the suggestion worker.

Resets stale 'running' rows back to 'queued' and re-enqueues every queued
row to Redis, covering both worker crashes and a Redis flush.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import redis
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.core.models import SuggestionJob
from app.web.services.queue import QUEUE_KEY

logger = logging.getLogger(__name__)

STALE_THRESHOLD = timedelta(minutes=10)
_STALE_INTERVAL_SQL = text(
    f"interval '{int(STALE_THRESHOLD.total_seconds())} seconds'"
)


def recover_orphans(db: Session, rc: redis.Redis) -> None:
    """Revive stale running rows, then re-LPUSH every queued row."""
    revived = db.execute(
        update(SuggestionJob)
        .where(
            SuggestionJob.status == "running",
            SuggestionJob.started_at < func.now() - _STALE_INTERVAL_SQL,
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
