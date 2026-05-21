# Postgres Chat Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Postgres-backed chat history schema (`users`, `threads`, `messages`) with a one-command Docker dev stack (Postgres + pgAdmin), Alembic migrations, and tests.

**Architecture:** SQLAlchemy 2.0 declarative models as the canonical schema source. Alembic owns the migration lifecycle. Postgres + pgAdmin run via `docker compose`. App connection lives in `app/core/db.py` (engine + session factory). Spec: `docs/superpowers/specs/2026-05-21-postgres-chat-schema-design.md`.

**Tech Stack:** Postgres 16, SQLAlchemy 2.0, psycopg 3, Alembic, dpage/pgadmin4:8, pytest, python-dotenv.

---

## File map

**Create:**
- `requirements.txt` (none exists at root today)
- `.env.example`
- `docker-compose.yml`
- `db/init/01_create_test_db.sql`
- `db/pgadmin/servers.json`
- `alembic.ini`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/0001_initial.py`
- `tests/conftest.py`
- `tests/test_models.py`
- `tests/test_migrations.py`
- `README.md` (none exists at root today)

**Modify (overwrite empty stubs):**
- `app/core/__init__.py`
- `app/core/models.py`
- `app/core/db.py`

**Touch:**
- `.gitignore` (`.env` already present — no change required; verify only)

---

## Task 1: Install new dependencies and create `requirements.txt`

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Install runtime deps into the existing venv**

Run (PowerShell):
```powershell
.\.venv\Scripts\python.exe -m pip install "sqlalchemy>=2.0" "psycopg[binary]>=3.2" "alembic>=1.13"
```
Expected: three packages install successfully.

- [ ] **Step 2: Create `requirements.txt` listing only the new deps**

```
sqlalchemy>=2.0
psycopg[binary]>=3.2
alembic>=1.13
```

(The project previously installed everything ad-hoc. We add a root `requirements.txt` containing the new DB-layer deps. Existing deps are not enumerated — adding them is out of scope.)

- [ ] **Step 3: Verify imports work**

Run:
```powershell
.\.venv\Scripts\python.exe -c "import sqlalchemy, psycopg, alembic; print(sqlalchemy.__version__, psycopg.__version__, alembic.__version__)"
```
Expected: three version strings print, no errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat(db): add sqlalchemy, psycopg, alembic deps"
```

---

## Task 2: Create `.env.example` and local `.env`

**Files:**
- Create: `.env.example`
- Create: `.env` (gitignored — not committed)

- [ ] **Step 1: Create `.env.example`**

```
# Postgres
POSTGRES_USER=reviewme
POSTGRES_PASSWORD=reviewme_dev
POSTGRES_DB=reviewme

# pgAdmin
PGADMIN_DEFAULT_EMAIL=admin@reviewme.local
PGADMIN_DEFAULT_PASSWORD=admin

# App DB connection (host machine → exposed postgres port)
DATABASE_URL=postgresql+psycopg://reviewme:reviewme_dev@localhost:5432/reviewme

# Tests use a separate database on the same container
TEST_DATABASE_URL=postgresql+psycopg://reviewme:reviewme_dev@localhost:5432/reviewme_test
```

- [ ] **Step 2: Copy to `.env` for local use**

Run (PowerShell):
```powershell
Copy-Item .env.example .env
```

- [ ] **Step 3: Verify `.gitignore` covers `.env`**

Run:
```bash
git check-ignore .env
```
Expected: prints `.env` (already ignored).

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "feat(db): add .env.example for postgres + pgadmin config"
```

---

## Task 3: Create `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write the compose file**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: reviewme-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 5

  pgadmin:
    image: dpage/pgadmin4:8
    container_name: reviewme-pgadmin
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      PGADMIN_DEFAULT_EMAIL: ${PGADMIN_DEFAULT_EMAIL}
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_DEFAULT_PASSWORD}
      PGADMIN_CONFIG_SERVER_MODE: "False"
    ports:
      - "5050:80"
    volumes:
      - pgadmin_data:/var/lib/pgadmin
      - ./db/pgadmin/servers.json:/pgadmin4/servers.json:ro

volumes:
  pgdata:
  pgadmin_data:
```

- [ ] **Step 2: Commit (compose will fail to start until Tasks 4–5 add the mounted files; we commit per-step to keep history clean)**

```bash
git add docker-compose.yml
git commit -m "feat(db): add docker-compose for postgres + pgadmin"
```

---

## Task 4: Create Postgres init script (creates the test DB)

**Files:**
- Create: `db/init/01_create_test_db.sql`

- [ ] **Step 1: Write the init SQL**

```sql
-- Runs once when the postgres container first initializes its data volume.
-- POSTGRES_DB (reviewme) is created automatically by the entrypoint;
-- here we additionally create the test database.
CREATE DATABASE reviewme_test;
```

- [ ] **Step 2: Commit**

```bash
git add db/init/01_create_test_db.sql
git commit -m "feat(db): add postgres init script for test database"
```

---

## Task 5: Create pgAdmin auto-register servers.json

**Files:**
- Create: `db/pgadmin/servers.json`

- [ ] **Step 1: Write the servers.json**

```json
{
  "Servers": {
    "1": {
      "Name": "reviewme-local",
      "Group": "Servers",
      "Host": "postgres",
      "Port": 5432,
      "MaintenanceDB": "reviewme",
      "Username": "reviewme",
      "SSLMode": "prefer"
    }
  }
}
```

Note: pgAdmin will prompt for the password the first time you click the server (paste `$POSTGRES_PASSWORD` from `.env`). servers.json does not interpolate environment variables, so `Username` is hard-coded — keep in sync with `.env` if you change it.

- [ ] **Step 2: Commit**

```bash
git add db/pgadmin/servers.json
git commit -m "feat(db): pgadmin servers.json auto-register"
```

---

## Task 6: Smoke test the compose stack

**Files:** none

- [ ] **Step 1: Bring up the stack**

Run:
```bash
docker compose up -d
```
Expected: both `reviewme-postgres` and `reviewme-pgadmin` containers start. Postgres becomes healthy within ~10 seconds.

- [ ] **Step 2: Confirm health**

Run:
```bash
docker compose ps
```
Expected: `reviewme-postgres` status shows `(healthy)`.

- [ ] **Step 3: Confirm test DB exists**

Run:
```bash
docker compose exec postgres psql -U reviewme -d reviewme_test -c "SELECT 1;"
```
Expected: returns one row with value `1`.

- [ ] **Step 4: Confirm pgAdmin auto-registered the server**

Open `http://localhost:5050` in a browser. Log in with `admin@reviewme.local` / `admin`. In the left tree, `Servers → reviewme-local` should be visible (no manual add). Click it → enter `reviewme_dev` for password.

- [ ] **Step 5: Leave the stack running for the rest of the plan; no commit (no file changes)**

---

## Task 7: Write the failing test for the `User` model

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 2: Create `tests/test_models.py` with the first User test**

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.models import User


def test_create_user_has_defaults(db):
    u = User(
        username="alice",
        email="alice@example.com",
        password_hash="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.flush()
    assert u.id is not None
    assert u.role == "user"
    assert u.created_at is not None
```

- [ ] **Step 3: Run and verify it fails**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_create_user_has_defaults -v
```
Expected: FAIL with `ImportError: cannot import name 'User'` (or `ModuleNotFoundError` if `models.py` is still empty).

---

## Task 8: Implement the `User` model

**Files:**
- Modify: `app/core/__init__.py`
- Modify: `app/core/models.py`

- [ ] **Step 1: Ensure `app/core/__init__.py` is a valid (empty) package marker**

Overwrite `app/core/__init__.py` with empty content (it is already 0 bytes — confirm).

- [ ] **Step 2: Write `app/core/models.py`**

```python
from datetime import datetime

from sqlalchemy import Integer, String, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
```

- [ ] **Step 3: Run the test and verify it passes**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_create_user_has_defaults -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/core/__init__.py app/core/models.py tests/conftest.py tests/test_models.py
git commit -m "feat(db): add User model + test fixture"
```

---

## Task 9: Add uniqueness tests for `User`

**Files:**
- Modify: `tests/test_models.py`

- [ ] **Step 1: Append two uniqueness tests**

```python
def test_user_unique_username(db):
    db.add(User(username="bob", email="b@x.com", password_hash="h"))
    db.flush()
    db.add(User(username="bob", email="other@x.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_user_unique_email(db):
    db.add(User(username="c1", email="dup@x.com", password_hash="h"))
    db.flush()
    db.add(User(username="c2", email="dup@x.com", password_hash="h"))
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 2: Run the tests and verify both pass**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```
Expected: all three User tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_models.py
git commit -m "test(db): assert unique username + email constraints"
```

---

## Task 10: Write the failing test for the `Thread` model + cascade

**Files:**
- Modify: `tests/test_models.py`

- [ ] **Step 1: Append two Thread tests**

```python
from app.core.models import Thread  # add to imports at top of file


def test_thread_belongs_to_user(db):
    u = User(username="dave", email="d@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    assert t.id is not None
    assert t.user_id == u.id


def test_delete_user_cascades_threads(db):
    u = User(username="ed", email="e@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    tid = t.id
    db.delete(u)
    db.flush()
    assert db.get(Thread, tid) is None
```

Place the `from app.core.models import Thread` line next to the existing `User` import.

- [ ] **Step 2: Run and verify both fail**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_thread_belongs_to_user tests/test_models.py::test_delete_user_cascades_threads -v
```
Expected: both FAIL with `ImportError: cannot import name 'Thread'`.

---

## Task 11: Implement the `Thread` model

**Files:**
- Modify: `app/core/models.py`

- [ ] **Step 1: Extend `app/core/models.py`**

Replace the existing file contents with:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    threads: Mapped[list["Thread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="threads")

    __table_args__ = (
        Index("idx_threads_user_id", "user_id"),
    )
```

- [ ] **Step 2: Run tests and verify they pass**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```
Expected: all five tests PASS (3 User + 2 Thread).

- [ ] **Step 3: Commit**

```bash
git add app/core/models.py tests/test_models.py
git commit -m "feat(db): add Thread model with cascade-on-user-delete"
```

---

## Task 12: Write the failing tests for the `Message` model

**Files:**
- Modify: `tests/test_models.py`

- [ ] **Step 1: Append Message tests**

```python
from sqlalchemy import select  # add to imports at top
from app.core.models import Message  # add to model imports


def test_message_role_check_rejects_invalid(db):
    u = User(username="fr", email="fr@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    db.add(Message(thread_id=t.id, role="invalid", content="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_message_valid_roles_accepted(db):
    u = User(username="gw", email="gw@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    for role in ("user", "assistant", "system"):
        db.add(Message(thread_id=t.id, role=role, content=f"hi {role}"))
    db.flush()
    msgs = db.scalars(select(Message).where(Message.thread_id == t.id)).all()
    assert len(msgs) == 3


def test_delete_thread_cascades_messages(db):
    u = User(username="hk", email="h@x.com", password_hash="h")
    db.add(u)
    db.flush()
    t = Thread(user_id=u.id)
    db.add(t)
    db.flush()
    m = Message(thread_id=t.id, role="user", content="hello")
    db.add(m)
    db.flush()
    mid = m.id
    db.delete(t)
    db.flush()
    assert db.get(Message, mid) is None
```

- [ ] **Step 2: Run and verify all three Message tests fail**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v -k message
```
Expected: 3 FAIL with `ImportError: cannot import name 'Message'`.

---

## Task 13: Implement the `Message` model

**Files:**
- Modify: `app/core/models.py`

- [ ] **Step 1: Update imports and add the `Message` class to `app/core/models.py`**

Replace the file contents with:

```python
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    threads: Mapped[list["Thread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="threads")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_threads_user_id", "user_id"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped["Thread"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="messages_role_check",
        ),
        Index("idx_messages_thread_created", "thread_id", "created_at"),
    )
```

- [ ] **Step 2: Run all model tests and verify they pass**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py -v
```
Expected: 8 tests PASS (3 User + 2 Thread + 3 Message).

- [ ] **Step 3: Commit**

```bash
git add app/core/models.py tests/test_models.py
git commit -m "feat(db): add Message model with role CHECK + cascade + index"
```

---

## Task 14: Wire `app/core/db.py` (engine + session factory)

**Files:**
- Modify: `app/core/db.py`

- [ ] **Step 1: Write a smoke test for `get_db`**

Append to `tests/test_models.py`:

```python
def test_get_db_round_trip():
    """Smoke test: get_db opens a session, commits, closes."""
    from app.core.db import get_db
    from app.core.models import User

    with get_db() as session:
        # Just open + close inside the test database container.
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
```

Note: this test uses the production `DATABASE_URL` (not the test database). Confirm `.env` is set so that the connection succeeds.

- [ ] **Step 2: Run and verify it fails**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_get_db_round_trip -v
```
Expected: FAIL with `ImportError: cannot import name 'get_db'`.

- [ ] **Step 3: Write `app/core/db.py`**

```python
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
```

- [ ] **Step 4: Run and verify it passes**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_get_db_round_trip -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/db.py tests/test_models.py
git commit -m "feat(db): add engine + SessionLocal + get_db context manager"
```

---

## Task 15: Set up Alembic scaffolding

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`

- [ ] **Step 1: Write `alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Write `alembic/env.py`**

```python
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from app.core.models import Base

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL not set. Copy .env.example to .env and edit."
    )
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Write `alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Write the initial migration `alembic/versions/0001_initial.py`**

```python
"""initial schema: users, threads, messages

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.String(32),
            nullable=False,
            server_default="user",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_threads_user_id", "threads", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "thread_id",
            sa.Integer(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="messages_role_check",
        ),
    )
    op.create_index(
        "idx_messages_thread_created",
        "messages",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_messages_thread_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_threads_user_id", table_name="threads")
    op.drop_table("threads")
    op.drop_table("users")
```

- [ ] **Step 5: Smoke-test the migration against the main (non-test) DB**

Run:
```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```
Expected: output ends with `Running upgrade  -> 0001, initial schema: users, threads, messages`.

Verify via psql:
```bash
docker compose exec postgres psql -U reviewme -d reviewme -c "\dt"
```
Expected: `users`, `threads`, `messages`, and `alembic_version` listed.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat(db): add alembic scaffolding + initial migration"
```

---

## Task 16: Write Alembic round-trip tests

**Files:**
- Create: `tests/test_migrations.py`

- [ ] **Step 1: Write the test file**

```python
import os
import subprocess

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

from app.core.models import Base

load_dotenv()

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://reviewme:reviewme_dev@localhost:5432/reviewme_test",
)


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    proc = subprocess.run(
        ["alembic", *args],
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
        Base.metadata.drop_all(conn)
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    yield eng
    with eng.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
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
```

- [ ] **Step 2: Run the tests and verify they pass**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -v
```
Expected: 2 PASS.

- [ ] **Step 3: Run the full suite one more time**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py tests/test_migrations.py -v
```
Expected: 10 PASS (8 model + 2 migration).

- [ ] **Step 4: Commit**

```bash
git add tests/test_migrations.py
git commit -m "test(db): alembic upgrade + downgrade round-trip"
```

---

## Task 17: Write README quickstart

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

```markdown
# ReviewMe

Skill-aware LLM code reviewer with Gradio UI.

## Quickstart (DB dev stack)

1. **Copy env:**
   ```bash
   cp .env.example .env
   ```

2. **Bring up Postgres + pgAdmin:**
   ```bash
   docker compose up -d
   docker compose ps   # wait for postgres → (healthy)
   ```

3. **Install Python deps (one-time):**
   ```bash
   .\.venv\Scripts\pip install -r requirements.txt
   ```

4. **Apply migrations:**
   ```bash
   .\.venv\Scripts\python -m alembic upgrade head
   ```

5. **Run tests:**
   ```bash
   .\.venv\Scripts\python -m pytest tests/test_models.py tests/test_migrations.py -v
   ```

6. **Launch the app (existing entrypoint):**
   ```bash
   .\.venv\Scripts\python app.py
   ```

## pgAdmin

- URL: `http://localhost:5050`
- Login: `admin@reviewme.local` / `admin` (from `.env`)
- Server `reviewme-local` is auto-registered. Click it → password = `$POSTGRES_PASSWORD` from `.env`.

## Migrations

```bash
# Create a new migration after editing app/core/models.py
.\.venv\Scripts\python -m alembic revision --autogenerate -m "describe change"

# Apply
.\.venv\Scripts\python -m alembic upgrade head

# Roll back one step
.\.venv\Scripts\python -m alembic downgrade -1
```

## Reset the DB

```bash
docker compose down -v   # WIPES pgdata + pgadmin_data
docker compose up -d
.\.venv\Scripts\python -m alembic upgrade head
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with DB dev stack quickstart"
```

---

## Final verification

- [ ] **Step 1: Full pytest run**

Run:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_models.py tests/test_migrations.py -v
```
Expected: 10 PASS.

- [ ] **Step 2: Cold-boot verification**

```bash
docker compose down -v
docker compose up -d
docker compose exec postgres psql -U reviewme -d reviewme_test -c "SELECT 1;"
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic downgrade base
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m pytest tests/test_models.py tests/test_migrations.py -v
```
Expected: every step succeeds; 10 PASS at the end.

- [ ] **Step 3: pgAdmin verification**

Open `http://localhost:5050`. Confirm `reviewme-local` server appears in the tree and connects to `reviewme` (3 tables visible) and `reviewme_test` (3 tables visible when tests have run).

---

## Self-review notes

Spec coverage (cross-referenced against `2026-05-21-postgres-chat-schema-design.md`):

| Spec section                            | Implementing task(s)        |
|-----------------------------------------|-----------------------------|
| Goal: 3 tables + dev stack              | Tasks 1–17                  |
| Postgres + pgAdmin containers           | Tasks 3, 5                  |
| `db/`, `app/core/`, `alembic/` layout   | Tasks 4, 5, 8, 11, 13, 14, 15 |
| Dependencies                            | Task 1                      |
| Lowercase pluralized tables             | Tasks 8, 11, 13, 15         |
| `password_hash` rename                  | Tasks 8, 15                 |
| `TIMESTAMPTZ` + `now()` defaults        | Tasks 8, 11, 13, 15         |
| `CHECK` on `messages.role`              | Tasks 12, 13, 15            |
| `ON DELETE CASCADE` FKs                 | Tasks 10, 11, 13, 15        |
| Indexes (`idx_threads_user_id`, `idx_messages_thread_created`) | Tasks 11, 13, 15 |
| `.env.example` + `.env`                 | Task 2                      |
| pgAdmin auto-register via servers.json  | Task 5                      |
| Healthcheck + depends_on                | Task 3                      |
| Engine + SessionLocal + `get_db()`      | Task 14                     |
| Pool pre-ping + transactional context   | Task 14                     |
| Alembic env.py reads DATABASE_URL       | Task 15                     |
| Initial migration                       | Task 15                     |
| Tests: round-trip, cascade, CHECK, uniqueness, defaults | Tasks 7–13, 16 |
| Migration round-trip test               | Task 16                     |
| README                                  | Task 17                     |

No placeholders. Type/name consistency verified (e.g., `password_hash` everywhere, `idx_threads_user_id` / `idx_messages_thread_created` consistent, `Base` re-exported from `app.core.models`).
