import uuid
from datetime import datetime

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
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

_DEFAULT_MODEL = "gpt-oss:20b-cloud"

router = APIRouter(tags=["suggestions"])


class SuggestionsRequest(BaseModel):
    jd_text: str = Field(..., min_length=1)


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
            model=_DEFAULT_MODEL,
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
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "active job exists", "active_job_id": str(active)},
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


@router.delete(
    "/suggestions/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_suggestion(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _csrf: None = Depends(csrf.require_csrf),
):
    row = db.get(SuggestionJob, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if row.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete running job",
        )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
