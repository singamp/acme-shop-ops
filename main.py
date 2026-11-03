"""Acme Grocer web service: the storefront, the live order board, the owner page and
the JSON API. Orders are saved in Postgres and queued in Redis for the worker."""

import hmac
import os
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import parse_qs

import psycopg
import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

import db
import shop
from pages import order_page, owner_page, store_page, wants_html
from security import get_logger, secure

log = get_logger("app")

# One short-lived connection per request, at most MAX_CONNECTIONS at a time per
# replica: robust to database restarts, and leaves the small database's slots for the
# worker. Switch to psycopg_pool when the per-request connect time starts to matter.
MAX_CONNECTIONS = 5
slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
schema_ready = threading.Event()
stopping = threading.Event()

# Injected by `guidal db link cache --service web`. NEVER log it: it holds the password.
REDIS_URL = os.environ.get("REDIS_URL")
queue = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2) if REDIS_URL else None
# Project secret shared with the stock-check cron job. NEVER log it.
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
BOARD_JS = (Path(__file__).parent / "static" / "board.js").read_bytes()


class _NoQueue:
    """Stands in when the cache is not linked: orders are still saved, and the worker's
    reaper queues them once it runs."""

    def lpush(self, *_):
        raise RuntimeError("REDIS_URL not set")


@contextmanager
def connection():
    if not db.DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL not set: link app-db to this service")
    if not schema_ready.is_set():
        raise HTTPException(503, "database not ready yet, retry shortly")
    if not slots.acquire(timeout=5):
        raise HTTPException(503, "too many concurrent database requests, retry shortly")
    try:
        with db.connect("web") as conn:
            yield conn
    except psycopg.errors.QueryCanceled:
        # statement_timeout fired. Checked first: it is a subclass of OperationalError.
        log.warning("query timed out")
        raise HTTPException(503, "query timed out") from None
    except psycopg.OperationalError as exc:
        log.warning("database unreachable", extra={"fields": {"error": type(exc).__name__}})
        raise HTTPException(503, "database unreachable") from None
    finally:
        slots.release()


def migrate_in_background() -> None:
    # Requests never wait for this: until the schema is ready they get a quick 503,
    # so a database that is still starting cannot pile up request threads.
    delay = 1.0
    while not stopping.is_set() and not schema_ready.is_set():
        try:
            with slots, db.connect("web-migrate") as conn:
                db.migrate(conn)
            schema_ready.set()
            log.info("schema ready")
        except Exception as exc:
            log.warning("migration not done yet, retrying", extra={"fields": {"error": type(exc).__name__, "retry_s": delay}})
            stopping.wait(delay)
            delay = min(delay * 2, 30.0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if db.DATABASE_URL:
        threading.Thread(target=migrate_in_background, daemon=True).start()
    yield
    stopping.set()


app = secure(FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan))


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    quantity: int = Field(ge=1, le=20)


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Line] = Field(min_length=1, max_length=10)


def create_order(lines: list[tuple[str, int]]) -> dict:
    with connection() as conn:
        try:
            return shop.place_order(conn, queue or _NoQueue(), lines)
        except shop.OrderError as exc:
            raise HTTPException(422, str(exc)) from None


@app.get("/")
async def root(request: Request):
    # The platform health check: always 200 and never touches the database or Redis.
    # Browsers get the storefront; its board loads itself from /api/board.
    if wants_html(request):
        return store_page()
    return {"status": "ok", "database": "linked" if db.DATABASE_URL else "not linked", "cache": "linked" if REDIS_URL else "not linked"}


@app.get("/static/board.js")
async def board_js():
    return Response(BOARD_JS, media_type="text/javascript", headers={"cache-control": "no-cache"})


@app.get("/api/products")
def products():
    with connection() as conn:
        rows = conn.execute("SELECT sku, name, category, price_cents, stock FROM products ORDER BY category, name").fetchall()
    return [{"sku": s, "name": n, "category": c, "price_cents": p, "in_stock": st > 0} for s, n, c, p, st in rows]


@app.post("/api/orders", status_code=201)
def post_order(body: OrderIn):
    return create_order([(line.sku, line.quantity) for line in body.items])


@app.post("/order/new")
async def post_order_form(request: Request):
    # The storefront form posts sku=quantity fields; parsed here so the app needs no
    # form library. Empty or invalid baskets just go back to the shop.
    fields = parse_qs((await request.body()).decode("utf-8", "replace"))
    try:
        body = OrderIn(items=[
            Line(sku=sku, quantity=int(values[0]))
            for sku, values in fields.items() if values and values[0].strip() not in ("", "0")
        ])
    except ValueError:
        return RedirectResponse("/", status_code=303)
    placed = await run_in_threadpool(create_order, [(line.sku, line.quantity) for line in body.items])
    return RedirectResponse(f"/orders/{placed['number']}", status_code=303)


@app.get("/api/board")
def board():
    with connection() as conn:
        return shop.board(conn)


@app.get("/orders/{number}")
def get_order(number: int, request: Request):
    with connection() as conn:
        found = shop.order(conn, number)
    if found is None:
        raise HTTPException(404, "no such order")
    return order_page(found) if wants_html(request) else found


@app.get("/owner")
def owner(request: Request):
    with connection() as conn:
        report = shop.owner_report(conn)
    return owner_page(report) if wants_html(request) else report


@app.post("/internal/stock-check")
def internal_stock_check(request: Request):
    # Called by the stock-check cron job over the private network. A wrong or missing
    # token gets the same 403 as no token configured, so it reveals nothing.
    given = request.headers.get("x-internal-token", "")
    if not INTERNAL_TOKEN or not hmac.compare_digest(given.encode(), INTERNAL_TOKEN.encode()):
        raise HTTPException(403, "forbidden")
    with connection() as conn:
        return shop.stock_check(conn)
