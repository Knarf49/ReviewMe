import uuid
from enum import Enum

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, status
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
