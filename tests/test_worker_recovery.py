from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import SuggestionJob, User
from app.web.services.queue import QUEUE_KEY


@pytest.fixture
def user(db):
    u = User(username="rec", email="rec@e.com", password_hash="x")
    db.add(u)
    db.flush()
    return u


def test_recovery_requeues_stale_running(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    stale = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
        status="running",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add(stale)
    db.commit()

    recover_orphans(db, redis_client)

    db.refresh(stale)
    assert stale.status == "queued"
    assert stale.started_at is None
    assert str(stale.id) in redis_client.lrange(QUEUE_KEY, 0, -1)


def test_recovery_leaves_fresh_running_alone(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    fresh = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
        status="running",
        started_at=datetime.now(timezone.utc),  # just started
    )
    db.add(fresh)
    db.commit()

    recover_orphans(db, redis_client)

    db.refresh(fresh)
    assert fresh.status == "running"
    assert str(fresh.id) not in redis_client.lrange(QUEUE_KEY, 0, -1)


def test_recovery_requeues_all_queued_rows(db, redis_client, user):
    from app.worker.recovery import recover_orphans

    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    # Pretend Redis lost the entry — list is empty.
    redis_client.delete(QUEUE_KEY)

    recover_orphans(db, redis_client)

    assert str(job.id) in redis_client.lrange(QUEUE_KEY, 0, -1)
