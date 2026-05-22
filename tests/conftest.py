import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.models import Base

load_dotenv()

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
    """Per-test transactional rollback fixture."""
    connection = engine.connect()
    trans = connection.begin()
    SessionLocal = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
