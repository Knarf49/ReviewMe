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
