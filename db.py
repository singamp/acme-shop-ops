"""Postgres access shared by the web service, the worker and the tests: connection
settings, the schema and the seed stock. Nothing here knows about HTTP."""

import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

from catalog import PRODUCTS

# Injected by `guidal db link app-db --service <service>`. Absent until then.
# NEVER log this value: it contains the database password.
DATABASE_URL = os.environ.get("DATABASE_URL")

# Arbitrary constant naming "this app's schema migration" for pg_advisory_xact_lock.
MIGRATION_LOCK_ID = 7_340_002


MIGRATIONS = [
    "CREATE TABLE IF NOT EXISTS products ("
    " sku text PRIMARY KEY,"
    " name text NOT NULL,"
    " category text NOT NULL,"
    " price_cents integer NOT NULL CHECK (price_cents >= 0),"
    " stock integer NOT NULL CHECK (stock >= 0),"
    " low_at integer NOT NULL DEFAULT 5)",
    # Order numbers never repeat, also across demo resets (TRUNCATE keeps the sequence),
    # so a number left in Redis from an earlier run can never match a new order.
    "CREATE TABLE IF NOT EXISTS orders ("
    " number bigint GENERATED ALWAYS AS IDENTITY (START WITH 1001) PRIMARY KEY,"
    " items jsonb NOT NULL,"
    " subtotal_cents integer NOT NULL CHECK (subtotal_cents >= 0),"
    " discount_cents integer NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),"
    " total_cents integer NOT NULL CHECK (total_cents >= 0),"
    " status text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'processing', 'done', 'on_hold')),"
    " hold_reason text,"
    " claimed_at timestamptz,"
    " created_at timestamptz NOT NULL DEFAULT now(),"
    " processed_at timestamptz)",
    "CREATE INDEX IF NOT EXISTS orders_status_number ON orders (status, number)",
    "CREATE TABLE IF NOT EXISTS stock_reports ("
    " id bigserial PRIMARY KEY,"
    " run_at timestamptz NOT NULL DEFAULT now(),"
    " low jsonb NOT NULL,"
    " out jsonb NOT NULL)",
]


def connect_kwargs(application_name: str) -> dict:
    try:
        url_params = conninfo_to_dict(DATABASE_URL)
    except psycopg.ProgrammingError:
        # The message could echo the URL, so it is not logged.
        raise RuntimeError("DATABASE_URL is not a valid connection string") from None
    kwargs = {
        "connect_timeout": 5,
        "application_name": application_name,
        # A runaway query is cancelled by the server instead of holding a connection.
        "options": "-c statement_timeout=5000 -c TimeZone=UTC",  # times shown as GMT
        # Every `with conn.transaction()` is then a real transaction, and a plain
        # execute outside one commits at once.
        "autocommit": True,
    }
    # Guidal's managed Postgres serves TLS, but the injected URL carries no sslmode.
    # libpq's default ("prefer") silently falls back to plaintext, so require TLS unless
    # the URL or PGSSLMODE says otherwise. The server certificate comes from a
    # cluster-internal CA that is not given to apps, so verify-full is not possible yet.
    if "sslmode" not in url_params and "PGSSLMODE" not in os.environ:
        kwargs["sslmode"] = "require"
    return kwargs


def connect(application_name: str) -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set: link app-db to this service")
    return psycopg.connect(DATABASE_URL, **connect_kwargs(application_name))


def migrate(conn: psycopg.Connection) -> None:
    """Idempotent, and safe with web and workers starting at once: the advisory lock
    serialises them. Adds any missing product; never changes existing stock."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        for statement in MIGRATIONS:
            conn.execute(statement)
        conn.cursor().executemany(
            "INSERT INTO products (sku, name, category, price_cents, stock, low_at)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sku) DO NOTHING",
            PRODUCTS,
        )
