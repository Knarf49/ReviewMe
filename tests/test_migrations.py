import os
import subprocess
import sys

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

load_dotenv()

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://reviewme:reviewme_dev@localhost:5432/reviewme_test",
)


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"alembic {args!r} failed: {proc.stderr}"


@pytest.fixture
def clean_db():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")
    yield eng
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")
    eng.dispose()


def test_upgrade_creates_all_tables(clean_db):
    _alembic("upgrade", "head")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert {"users", "threads", "messages"}.issubset(tables)


def test_upgrade_then_downgrade_is_clean(clean_db):
    _alembic("upgrade", "head")
    _alembic("downgrade", "base")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert "users" not in tables
    assert "threads" not in tables
    assert "messages" not in tables


def test_upgrade_creates_refresh_sessions(clean_db):
    _alembic("upgrade", "head")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert "refresh_sessions" in tables


def test_downgrade_drops_refresh_sessions(clean_db):
    _alembic("upgrade", "head")
    _alembic("downgrade", "0001")
    insp = inspect(clean_db)
    tables = set(insp.get_table_names())
    assert "refresh_sessions" not in tables
    assert {"users", "threads", "messages"}.issubset(tables)
