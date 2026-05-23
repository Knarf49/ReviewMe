"""Suggestion worker entrypoint.

Runs once-at-boot recovery, then blocks on Redis BRPOP forever, calling
process_job for every popped job_id. One SQLAlchemy session per iteration
to keep transactions short. Producer uses LPUSH, so BRPOP gives FIFO.
"""
from __future__ import annotations

import logging
import signal
import sys
import time

import redis as redis_lib

from app.core.db import SessionLocal
from app.core.redis_client import get_redis
from app.web.services.queue import QUEUE_KEY
from app.worker.recovery import recover_orphans
from app.worker.runner import process_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.worker")

BRPOP_TIMEOUT = 5  # seconds; lets the loop check for shutdown signals
_running = True


def _stop(_signum, _frame):
    global _running
    _running = False
    logger.info("shutdown signal received")


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    rc = get_redis()

    # Boot recovery — one session, separate from the loop.
    with SessionLocal() as db:
        recover_orphans(db, rc)

    while _running:
        try:
            popped = rc.brpop(QUEUE_KEY, timeout=BRPOP_TIMEOUT)
        except redis_lib.RedisError as e:
            logger.exception("BRPOP failed: %s", e)
            time.sleep(1)
            continue

        if popped is None:
            continue  # idle tick

        _, job_id = popped
        try:
            with SessionLocal() as db:
                process_job(db, job_id)
        except Exception:
            logger.exception("job processing failed", extra={"job_id": job_id})

    logger.info("worker exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
