FROM python:3.13-slim

WORKDIR /code

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# psycopg[binary] ships its own libpq; no system build deps needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini run.py ./

EXPOSE 8000

# Apply pending migrations then start the dev server bound to all interfaces
# so the container's published port is reachable from the host.
CMD ["sh", "-c", "alembic upgrade head && python run.py --host 0.0.0.0"]
