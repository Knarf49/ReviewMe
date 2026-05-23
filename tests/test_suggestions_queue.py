import pytest
from sqlalchemy.exc import IntegrityError

from app.core.models import SuggestionJob, User
from app.web.services.queue import QUEUE_KEY, enqueue_suggestion


@pytest.fixture
def make_user(db):
    def _make(username="qu", email="qu@example.com"):
        u = User(username=username, email=email, password_hash="x")
        db.add(u)
        db.flush()
        return u
    return _make


def test_enqueue_inserts_row_and_lpushes(db, redis_client, make_user):
    user = make_user()
    job_id = enqueue_suggestion(
        db, redis_client,
        user_id=user.id,
        jd_text="JD",
        model="gpt-oss:20b-cloud",
    )
    db.flush()

    row = db.get(SuggestionJob, job_id)
    assert row is not None
    assert row.status == "queued"
    assert row.user_id == user.id

    pushed = redis_client.lrange(QUEUE_KEY, 0, -1)
    assert str(job_id) in pushed


def test_enqueue_rejects_second_active_job_per_user(
    db, redis_client, make_user,
):
    user = make_user()
    enqueue_suggestion(
        db, redis_client,
        user_id=user.id, jd_text="JD1", model="gpt-oss:20b-cloud",
    )
    db.flush()

    with pytest.raises(IntegrityError):
        enqueue_suggestion(
            db, redis_client,
            user_id=user.id, jd_text="JD2", model="gpt-oss:20b-cloud",
        )
        db.flush()
