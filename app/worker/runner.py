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
