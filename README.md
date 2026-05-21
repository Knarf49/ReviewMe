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
- Login: `admin@reviewme.dev` / `admin` (from `.env`)
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

## Port 5432 conflict (Windows)

If `psql`/SQLAlchemy reports `password authentication failed for user "reviewme"`,
a local Windows Postgres service may be intercepting port 5432. Stop it (admin PowerShell):

```powershell
Stop-Service postgresql-x64-17 -Force
```
