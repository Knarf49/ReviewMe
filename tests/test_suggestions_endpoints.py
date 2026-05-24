import uuid
from datetime import datetime, timezone

import pytest
from app.core.models import SuggestionJob, User


@pytest.fixture
def signed_in_user(client, db):
    # Sign up + log in to get auth cookies.
    payload = {
        "username": "sjuser",
        "email": "sj@example.com",
        "password": "password123",
    }
    client.post("/signup", json=payload)
    r = client.post(
        "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert r.status_code == 200
    csrf = r.json()["csrf_token"]
    user = db.query(User).filter_by(email=payload["email"]).one()
    return user, csrf


def test_post_suggestions_creates_queued_job(
    client, db, redis_client, signed_in_user,
):
    user, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "Backend engineer", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    row = db.get(SuggestionJob, job_id)
    assert row is not None
    assert row.status == "queued"
    assert row.user_id == user.id

    pushed = redis_client.lrange("suggestions:jobs", 0, -1)
    assert job_id in pushed


def test_post_suggestions_requires_auth(client):
    r = client.post(
        "/suggestions",
        json={"jd_text": "JD", "model": "gpt-oss:20b-cloud"},
    )
    assert r.status_code == 401


def test_post_suggestions_rejects_active_job(
    client, db, redis_client, signed_in_user,
):
    _, csrf = signed_in_user
    body = {"jd_text": "JD", "model": "gpt-oss:20b-cloud"}
    headers = {"X-CSRF-Token": csrf}
    first = client.post("/suggestions", json=body, headers=headers)
    assert first.status_code == 201

    second = client.post("/suggestions", json=body, headers=headers)
    assert second.status_code == 409
    assert second.json()["active_job_id"] == first.json()["job_id"]


def test_post_suggestions_empty_jd_rejected(client, signed_in_user):
    _, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_post_suggestions_invalid_model_rejected(client, signed_in_user):
    _, csrf = signed_in_user
    r = client.post(
        "/suggestions",
        json={"jd_text": "JD", "model": "not-a-model"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_get_suggestion_returns_queued_state(
    client, db, signed_in_user,
):
    user, csrf = signed_in_user
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"] == str(job.id)
    assert body["result"] is None
    assert body["error"] is None


def test_get_suggestion_returns_done_payload(
    client, db, signed_in_user,
):
    user, _ = signed_in_user
    job = SuggestionJob(
        user_id=user.id,
        jd_text="JD",
        model="gpt-oss:20b-cloud",
        status="done",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        result={
            "suggestion": {"project_title": "T"},
            "model": "gpt-oss:20b-cloud",
            "elapsed_ms": 1234,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "research": {"queries": [], "sources": []},
        },
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["suggestion"]["project_title"] == "T"
    assert body["elapsed_ms"] == 1234
    assert body["model"] == "gpt-oss:20b-cloud"


def test_get_suggestion_404_when_missing(client, signed_in_user):
    r = client.get(f"/suggestions/{uuid.uuid4()}")
    assert r.status_code == 404


def test_get_suggestion_404_when_other_user(client, db, signed_in_user):
    # signed_in_user is user A. Create a job for a different user B.
    from app.core.models import User
    other = User(username="other", email="other@e.com", password_hash="x")
    db.add(other)
    db.flush()
    job = SuggestionJob(
        user_id=other.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.get(f"/suggestions/{job.id}")
    assert r.status_code == 404


def test_get_suggestion_invalid_uuid(client, signed_in_user):
    r = client.get("/suggestions/not-a-uuid")
    assert r.status_code == 422


def test_delete_suggestion_removes_queued_job(client, db, signed_in_user):
    user, csrf = signed_in_user
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()
    job_id = job.id

    r = client.delete(
        f"/suggestions/{job_id}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 204
    db.expire_all()
    assert db.get(SuggestionJob, job_id) is None


def test_delete_suggestion_404_when_missing(client, signed_in_user):
    _, csrf = signed_in_user
    r = client.delete(
        f"/suggestions/{uuid.uuid4()}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


def test_delete_suggestion_404_when_other_user(client, db, signed_in_user):
    _, csrf = signed_in_user
    other = User(username="other2", email="other2@e.com", password_hash="x")
    db.add(other)
    db.flush()
    job = SuggestionJob(
        user_id=other.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.delete(
        f"/suggestions/{job.id}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


def test_delete_suggestion_409_when_running(client, db, signed_in_user):
    user, csrf = signed_in_user
    job = SuggestionJob(
        user_id=user.id,
        jd_text="JD",
        model="gpt-oss:20b-cloud",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()

    r = client.delete(
        f"/suggestions/{job.id}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409


def test_delete_suggestion_requires_csrf(client, db, signed_in_user):
    user, _ = signed_in_user
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()

    r = client.delete(f"/suggestions/{job.id}")
    assert r.status_code in (401, 403)
