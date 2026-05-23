import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL not set. Copy .env.example to .env and edit, "
        "or export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_dep() -> Iterator[Session]:
    """FastAPI dependency form (generator, not context manager).

    The endpoint owns commit/rollback. This yields a session and only
    closes it; controllers must call db.commit() explicitly.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
