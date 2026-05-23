"""Integration test for the suggestions queue.

Does NOT spawn the worker process. Calls process_job() directly in-process
after the HTTP enqueue, so the test does not depend on container timing.
Layer 4 is stubbed.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# app/pipeline modules import each other as top-level names (e.g. `from
# ai_reviewer import ...`), so the pipeline dir must be on sys.path before
# project_suggester is imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "pipeline"))
from app.core.models import User  # noqa: E402
from app.pipeline.project_suggester import CallUsage, Layer4Result  # noqa: E402
from app.pipeline.research import ResearchResult  # noqa: E402
from app.web.services.queue import QUEUE_KEY  # noqa: E402


@pytest.fixture
def signed_in_user(client, db):
    payload = {
        "username": "intu",
        "email": "intu@e.com",
        "password": "password123",
    }
    client.post("/signup", json=payload)
    r = client.post(
        "/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    csrf = r.json()["csrf_token"]
    user = db.query(User).filter_by(email=payload["email"]).one()
    return user, csrf


def _fake_result():
    return Layer4Result(
        suggestion={"project_title": "Integration"},
        model="gpt-oss:20b-cloud",
        elapsed_ms=99,
        usage=CallUsage(prompt_tokens=3, completion_tokens=4),
        research=ResearchResult(),
    )


def test_full_enqueue_process_poll_flow(
    client, db, redis_client, signed_in_user,
):
    _, csrf = signed_in_user

    # 1. Enqueue
    r = client.post(
        "/suggestions",
        json={"jd_text": "Backend", "model": "gpt-oss:20b-cloud"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # 2. Redis carries the job_id
    popped = redis_client.brpop(QUEUE_KEY, timeout=1)
    assert popped is not None
    _, popped_id = popped
    assert popped_id == job_id

    # 3. Run process_job (stubbed Layer 4)
    from app.worker.runner import process_job
    with patch(
        "app.worker.runner.run_layer4_sync",
        return_value=_fake_result(),
    ):
        process_job(db, job_id)

    # 4. Poll endpoint returns the result
    r = client.get(f"/suggestions/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["suggestion"]["project_title"] == "Integration"
    assert body["elapsed_ms"] == 99
