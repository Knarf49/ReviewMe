import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# app/pipeline modules import each other as top-level names (e.g. `from
# ai_reviewer import ...`), so the pipeline dir must be on sys.path before
# project_suggester is imported.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "pipeline"))

from app.core.models import SuggestionJob, User  # noqa: E402
from app.pipeline.project_suggester import CallUsage, Layer4Result  # noqa: E402
from app.pipeline.research import ResearchResult  # noqa: E402


@pytest.fixture
def make_user(db):
    def _make():
        u = User(username="wr", email="wr@e.com", password_hash="x")
        db.add(u)
        db.flush()
        return u
    return _make


@pytest.fixture
def queued_job(db, make_user):
    user = make_user()
    job = SuggestionJob(
        user_id=user.id, jd_text="JD", model="gpt-oss:20b-cloud",
    )
    db.add(job)
    db.commit()
    return job


def _fake_layer4_result(model="gpt-oss:20b-cloud"):
    return Layer4Result(
        suggestion={"project_title": "Fake"},
        model=model,
        elapsed_ms=42,
        usage=CallUsage(prompt_tokens=1, completion_tokens=2),
        research=ResearchResult(),
    )


def test_process_job_success(db, queued_job):
    from app.worker.runner import process_job

    with patch(
        "app.worker.runner.run_layer4_sync",
        return_value=_fake_layer4_result(),
    ):
        process_job(db, str(queued_job.id))

    db.refresh(queued_job)
    assert queued_job.status == "done"
    assert queued_job.result["suggestion"]["project_title"] == "Fake"
    assert queued_job.finished_at is not None
    assert queued_job.started_at is not None


def test_process_job_marks_error_on_exception(db, queued_job):
    from app.worker.runner import process_job

    with patch(
        "app.worker.runner.run_layer4_sync",
        side_effect=RuntimeError("boom"),
    ):
        process_job(db, str(queued_job.id))

    db.refresh(queued_job)
    assert queued_job.status == "error"
    assert "boom" in queued_job.error
    assert queued_job.finished_at is not None


def test_process_job_skips_non_queued(db, queued_job):
    from app.worker.runner import process_job

    queued_job.status = "done"
    queued_job.result = {"already": "done"}
    db.commit()

    with patch("app.worker.runner.run_layer4_sync") as m:
        process_job(db, str(queued_job.id))
        m.assert_not_called()

    db.refresh(queued_job)
    assert queued_job.status == "done"
    assert queued_job.result == {"already": "done"}
