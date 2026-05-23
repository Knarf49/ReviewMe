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
