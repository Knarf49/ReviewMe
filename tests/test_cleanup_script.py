from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.models import RefreshSession, User


def test_cleanup_removes_rows_older_than_grace(db):
    u = User(username="cu1", email="cu1@x.com", password_hash="h")
    db.add(u); db.flush()
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(days=8),
    ))
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64,
        expires_at=datetime.now(timezone.utc) - timedelta(days=2),
    ))
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="c" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(days=5),
    ))
    db.flush()

    from scripts.cleanup_expired_sessions import cleanup
    deleted = cleanup(db, grace_days=7)
    assert deleted == 1
    remaining = db.query(RefreshSession).count()
    assert remaining == 3 - 1  # 2 kept, 1 deleted
