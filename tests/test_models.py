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
