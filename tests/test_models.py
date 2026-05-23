import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.models import Message, Thread, User


def test_create_user_has_defaults(db):
    u = User(
        username="alice",
        email="alice@example.com",
        password_hash="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.flush()
    assert u.id is not None
    assert u.role == "user"
    assert u.created_at is not None


def test_user_unique_username(db):
    db.add(User(username="bob", email="b@x.com", password_hash="h"))
    db.flush()
    db.add(User(username="bob", email="other@x.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_user_unique_email(db):
    db.add(User(username="c1", email="dup@x.com", password_hash="h"))
    db.flush()
    db.add(User(username="c2", email="dup@x.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_thread_belongs_to_user(db):
    u = User(username="dave", email="d@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    assert t.id is not None
    assert t.user_id == u.id


def test_delete_user_cascades_threads(db):
    u = User(username="ed", email="e@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    tid = t.id
    db.delete(u)
    db.flush()
    assert db.get(Thread, tid) is None


def test_message_role_check_rejects_invalid(db):
    u = User(username="fr", email="fr@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    db.add(Message(thread_id=t.id, role="invalid", content="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_message_valid_roles_accepted(db):
    u = User(username="gw", email="gw@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    for role in ("user", "assistant", "system"):
        db.add(Message(thread_id=t.id, role=role, content=f"hi {role}"))
    db.flush()
    msgs = db.scalars(select(Message).where(Message.thread_id == t.id)).all()
    assert len(msgs) == 3


def test_delete_thread_cascades_messages(db):
    u = User(username="hk", email="h@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    m = Message(thread_id=t.id, role="user", content="hello")
    db.add(m)
    db.flush()
    mid = m.id
    db.delete(t)
    db.flush()
    assert db.get(Message, mid) is None


from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.core.models import RefreshSession


def _rs_expires():
    return datetime.now(timezone.utc) + timedelta(days=14)


def test_refresh_session_basic_insert(db):
    u = User(username="rs1", email="rs1@x.com", password_hash="h")
    db.add(u); db.flush()
    s = RefreshSession(
        user_id=u.id,
        family_id=uuid4(),
        token_hash="a" * 64,
        expires_at=_rs_expires(),
    )
    db.add(s); db.flush()
    assert s.id is not None
    assert s.used_at is None
    assert s.parent_id is None


def test_refresh_session_unique_token_hash(db):
    u = User(username="rs2", email="rs2@x.com", password_hash="h")
    db.add(u); db.flush()
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64, expires_at=_rs_expires(),
    ))
    db.flush()
    db.add(RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="b" * 64, expires_at=_rs_expires(),
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_delete_user_cascades_refresh_sessions(db):
    u = User(username="rs3", email="rs3@x.com", password_hash="h")
    db.add(u); db.flush()
    s = RefreshSession(
        user_id=u.id, family_id=uuid4(),
        token_hash="c" * 64, expires_at=_rs_expires(),
    )
    db.add(s); db.flush()
    sid = s.id
    db.delete(u); db.flush()
    db.expire_all()
    assert db.get(RefreshSession, sid) is None


def test_get_db_round_trip():
    """Smoke test: get_db opens a session, commits, closes."""
    from sqlalchemy import text

    from app.core.db import get_db

    with get_db() as session:
        session.execute(text("SELECT 1"))
