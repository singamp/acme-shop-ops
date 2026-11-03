"""The order worker: takes orders off the Redis queue one at a time and packs them.

Run as the Guidal worker service: `python worker.py`. Needs DATABASE_URL (app-db) and
REDIS_URL (cache). Database or Redis outages are waited out; any other error is a bug,
so it is logged and the process exits, and the platform restarts it.
"""

import os
import signal
import sys
import threading
import time

import psycopg
import redis

import db
import shop
from security import get_logger

log = get_logger("worker")
REAP_EVERY_SECONDS = 10
stopping = threading.Event()


def run() -> None:
    url = os.environ.get("REDIS_URL")  # never log: it contains the password
    if not url:
        raise RuntimeError("REDIS_URL not set: link the cache to this service")
    queue = redis.Redis.from_url(url, decode_responses=True, socket_timeout=10, health_check_interval=30)
    with db.connect("worker") as conn:
        db.migrate(conn)
        log.info("worker started", extra={"fields": {"waiting": shop.waiting_count(conn)}})
        shop.reap(conn, queue)
        last_reap = time.monotonic()
        # Finishes the order in hand before stopping, so a redeploy loses nothing.
        while not stopping.is_set():
            shop.process_next(conn, queue)
            if time.monotonic() - last_reap > REAP_EVERY_SECONDS:
                shop.reap(conn, queue)
                last_reap = time.monotonic()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    delay = 1.0
    while not stopping.is_set():
        try:
            run()
        except (psycopg.OperationalError, redis.ConnectionError, redis.TimeoutError) as exc:
            log.warning("database or cache unavailable, retrying", extra={"fields": {"error": type(exc).__name__, "retry_s": delay}})
            stopping.wait(delay)
            delay = min(delay * 2, 30.0)
        except Exception as exc:
            # One log line that names the error (readable in the console and by agents);
            # the traceback rides along in the line's "exc" field. Exit non-zero rather
            # than re-raise, so Python does not print the same traceback again as
            # separate unlabelled lines.
            log.exception(f"worker crashed: {type(exc).__name__}: {exc}")
            sys.exit(1)


if __name__ == "__main__":
    main()
