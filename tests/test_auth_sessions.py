from datetime import datetime, timezone, timedelta
from uuid import UUID
import pytest

from app.core.models import RefreshSession, User
from app.web.services.auth import sessions
from app.web.services.auth.exceptions import ReuseDetected, InvalidToken


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("REFRESH_TTL_SECONDS", "1209600")
    monkeypatch.setenv("ACCESS_TTL_SECONDS", "7200")
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "5")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()


def _make_user(db, name="su"):
    u = User(username=name, email=f"{name}@x.com", password_hash="h")
    db.add(u); db.flush()
    return u


def test_create_session_returns_plain_and_family_and_inserts_row(db):
    u = _make_user(db, "cs1")
    plain, family_id = sessions.create_session(db, user_id=u.id, user_agent="ua", ip="1.2.3.4")
    assert isinstance(plain, str) and len(plain) >= 40
    assert isinstance(family_id, UUID)
    row = db.query(RefreshSession).filter_by(family_id=family_id).one()
    assert row.user_id == u.id
    assert row.used_at is None
    assert row.user_agent == "ua"


def test_rotate_marks_used_and_inserts_new_in_same_family(db, redis_client):
    u = _make_user(db, "ro1")
    plain, family_id = sessions.create_session(db, user_id=u.id)
    new_plain, new_family_id, sid = sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    assert new_family_id == family_id
    assert str(sid) == str(family_id)
    rows = db.query(RefreshSession).filter_by(family_id=family_id).order_by(RefreshSession.id).all()
    assert len(rows) == 2
    assert rows[0].used_at is not None
    assert rows[1].used_at is None
    assert rows[1].parent_id == rows[0].id


def test_rotate_with_unknown_token_raises_invalid_token(db, redis_client):
    with pytest.raises(InvalidToken):
        sessions.rotate(db, redis_client, "definitely-not-a-real-token", user_agent=None, ip=None)


def test_rotate_with_expired_token_raises_invalid_token(db, redis_client):
    u = _make_user(db, "rx1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    row = db.query(RefreshSession).filter_by(family_id=fam).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()
    with pytest.raises(InvalidToken):
        sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)


def test_rotate_with_reused_token_kills_family_and_bumps_epoch(db, redis_client):
    u = _make_user(db, "re1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    with pytest.raises(ReuseDetected):
        sessions.rotate(db, redis_client, plain, user_agent=None, ip=None)
    assert db.query(RefreshSession).filter_by(family_id=fam).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, u.id) is not None


def test_revoke_family_deletes_and_marks_sid_revoked(db, redis_client):
    u = _make_user(db, "rv1")
    plain, fam = sessions.create_session(db, user_id=u.id)
    sessions.revoke_family(db, redis_client, family_id=fam)
    assert db.query(RefreshSession).filter_by(family_id=fam).count() == 0
    from app.web.services.auth import denylist
    assert denylist.is_sid_revoked(redis_client, str(fam)) is True


def test_revoke_all_for_user_deletes_and_bumps_epoch(db, redis_client):
    u = _make_user(db, "ra1")
    sessions.create_session(db, user_id=u.id)
    sessions.create_session(db, user_id=u.id)
    sessions.revoke_all_for_user(db, redis_client, user_id=u.id)
    assert db.query(RefreshSession).filter_by(user_id=u.id).count() == 0
    from app.web.services.auth import denylist
    assert denylist.get_user_epoch(redis_client, u.id) is not None


def test_enforce_limit_evicts_oldest_when_at_cap(db, redis_client, monkeypatch):
    monkeypatch.setenv("SESSION_LIMIT_PER_USER", "3")
    from app.web.services.auth import config
    config.get_auth_config.cache_clear()
    u = _make_user(db, "lm1")
    for _ in range(3):
        sessions.create_session(db, user_id=u.id)
    sessions.enforce_limit(db, redis_client, user_id=u.id)
    assert db.query(RefreshSession).filter_by(user_id=u.id).count() == 2
