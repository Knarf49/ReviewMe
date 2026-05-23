import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import Base

load_dotenv()


@pytest.fixture(autouse=True)
def _clear_auth_config_cache():
    """Ensure each test starts with a fresh AuthConfig cache.

    Tests that monkeypatch env vars must see them on the next
    get_auth_config() call. Without this, a previous test's cached
    config can leak between tests.
    """
    try:
        from app.web.services.auth import config as _cfg
        _cfg.get_auth_config.cache_clear()
    except Exception:
        pass
    try:
        from app.core import redis_client as _rc
        _rc.get_redis.cache_clear()
    except Exception:
        pass
    yield
    try:
        from app.web.services.auth import config as _cfg
        _cfg.get_auth_config.cache_clear()
    except Exception:
        pass


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://reviewme:reviewme_dev@localhost:5432/reviewme_test",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    with eng.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    yield eng
    with eng.begin() as conn:
        Base.metadata.drop_all(conn)
    eng.dispose()


@pytest.fixture
def db(engine) -> Session:
    """Per-test transactional rollback fixture.

    Uses the SAVEPOINT pattern so that endpoint-level `db.commit()` calls
    only release the inner savepoint while the outer transaction stays
    open and rolls back at teardown — keeping each test isolated.
    """
    from sqlalchemy import event

    connection = engine.connect()
    trans = connection.begin()
    SessionLocal = sessionmaker(
        bind=connection, expire_on_commit=False, future=True,
        join_transaction_mode="create_savepoint",
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        if trans.is_active:
            trans.rollback()
        connection.close()


@pytest.fixture
def redis_client():
    import redis as _redis
    url = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")
    client = _redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def client(db, redis_client):
    from fastapi.testclient import TestClient
    from app.core.db import db_dep
    from app.core.redis_client import get_redis
    from app.web.main import app
    app.dependency_overrides[db_dep] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
