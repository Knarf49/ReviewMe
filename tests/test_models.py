import pytest
from sqlalchemy.exc import IntegrityError

from app.core.models import User


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
