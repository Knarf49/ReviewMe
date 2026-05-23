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
        family_id = row.family_id
        user_id = row.user_id
        revoke_family(db, redis_client, family_id=family_id)
        denylist.bump_user_epoch(
            redis_client, user_id, ttl_seconds=cfg.access_ttl_seconds,
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
