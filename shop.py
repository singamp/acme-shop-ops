"""The shop's order logic, shared by the web service and the worker.

Postgres holds the truth about every order; Redis only holds the queue of order
numbers. Customers' orders go in at the left of `orders:queue`; a worker moves the
oldest one (right end) to `orders:processing`, works on it, and removes it from there
only after Postgres has the result. The reaper puts back anything a crashed worker
left behind, so no order is ever lost or done twice.
"""

import os
import time
from datetime import datetime, timezone

from psycopg import Connection
from psycopg.types.json import Jsonb

from security import get_logger

log = get_logger("shop")

QUEUE = "orders:queue"
PROCESSING = "orders:processing"
# How long the worker spends packing one order. 4 s so one worker exactly keeps up
# with the demo traffic (one order every 4 s).
PROCESS_SECONDS = float(os.environ.get("PROCESS_SECONDS", "4"))
# A claim older than this belongs to a worker that died mid-order.
STALE_SECONDS = 15
# A waiting order this old that is in no Redis list was never queued (Redis down when
# it was placed, or inserted by hand): the reaper queues it.
UNQUEUED_SECONDS = 20


class OrderError(ValueError):
    """The customer's order is not valid; the message is safe to show them."""


def price(conn: Connection, lines: list[tuple[str, int]]) -> tuple[list[dict], int, int, int]:
    """Prices an order from the product table: (items, subtotal, discount, total), in cents."""
    skus = [sku for sku, _ in lines]
    rows = conn.execute("SELECT sku, name, price_cents FROM products WHERE sku = ANY(%s)", (skus,)).fetchall()
    known = {sku: (name, cents) for sku, name, cents in rows}
    unknown = sorted(set(skus) - known.keys())
    if unknown:
        raise OrderError(f"unknown item: {', '.join(unknown)}")
    items = [
        {"sku": sku, "name": known[sku][0], "quantity": qty, "unit_cents": known[sku][1], "line_cents": known[sku][1] * qty}
        for sku, qty in lines
    ]
    subtotal = sum(item["line_cents"] for item in items)
    discount = 0
    return items, subtotal, discount, subtotal - discount


def place_order(conn: Connection, redis, lines: list[tuple[str, int]]) -> dict:
    """Saves the order, then queues its number. If Redis is down the order is still
    saved, and the reaper queues it within UNQUEUED_SECONDS."""
    items, subtotal, discount, total = price(conn, lines)
    number = conn.execute(
        "INSERT INTO orders (items, subtotal_cents, discount_cents, total_cents) VALUES (%s, %s, %s, %s) RETURNING number",
        (Jsonb(items), subtotal, discount, total),
    ).fetchone()[0]
    try:
        redis.lpush(QUEUE, number)
    except Exception as exc:
        log.warning("order saved but not queued; the worker will pick it up", extra={"fields": {"order": number, "error": type(exc).__name__}})
    return {"number": number, "items": items, "subtotal_cents": subtotal, "discount_cents": discount, "total_cents": total, "status": "waiting"}


def order_lines(items: list[dict]) -> list[tuple[str, int]]:
    """What to pick from the shelves for one order: (sku, how many)."""
    return [(item["sku"], item["qty"]) for item in items]


def claim(conn: Connection, number: int) -> tuple[list[tuple[str, int]], datetime] | None:
    """Marks the order as being processed by this worker: (lines, claim time). None when
    another worker has it, it is finished, or it no longer exists (e.g. left in Redis from
    before a reset). The claim time proves ownership later, in finish()."""
    with conn.transaction():
        row = conn.execute(
            "SELECT items FROM orders WHERE number = %s AND (status = 'waiting'"
            " OR (status = 'processing' AND claimed_at < now() - make_interval(secs => %s))) FOR UPDATE",
            (number, STALE_SECONDS),
        ).fetchone()
        if row is None:
            return None
        lines = order_lines(row[0])
        claimed_at = conn.execute(
            "UPDATE orders SET status = 'processing', claimed_at = clock_timestamp() WHERE number = %s RETURNING claimed_at",
            (number,),
        ).fetchone()[0]
    return lines, claimed_at


def finish(conn: Connection, number: int, lines: list[tuple[str, int]], claimed_at: datetime) -> tuple[str, str | None] | None:
    """Takes the items off the shelves, or puts the order on hold if any item is short.
    All or nothing, in one transaction; two workers can never sell the same last item.
    None if this worker no longer owns the order (it stalled, and the reaper gave the
    order to another worker): then nothing is touched."""
    with conn.transaction():
        owned = conn.execute(
            "SELECT 1 FROM orders WHERE number = %s AND status = 'processing' AND claimed_at = %s FOR UPDATE",
            (number, claimed_at),
        ).fetchone()
        if owned is None:
            return None
        wanted: dict[str, int] = {}
        for sku, qty in lines:
            wanted[sku] = wanted.get(sku, 0) + qty
        # Lock the rows in a fixed order, so two workers never deadlock on them.
        rows = conn.execute(
            "SELECT sku, name, stock FROM products WHERE sku = ANY(%s) ORDER BY sku FOR UPDATE", (sorted(wanted),)
        ).fetchall()
        short = [name for sku, name, stock in rows if stock < wanted[sku]]
        if short:
            reason = f"out of stock: {', '.join(short)}"
            report = conn.execute("SELECT run_at FROM stock_reports ORDER BY id DESC LIMIT 1").fetchone()
            if report:
                reason += f" (stock check at {report[0].astimezone(timezone.utc):%H:%M} GMT)"
            status = "on_hold"
        else:
            for sku, qty in wanted.items():
                conn.execute("UPDATE products SET stock = stock - %s WHERE sku = %s", (qty, sku))
            reason, status = None, "done"
        conn.execute(
            "UPDATE orders SET status = %s, hold_reason = %s, processed_at = now() WHERE number = %s",
            (status, reason, number),
        )
    return status, reason


def waiting_count(conn: Connection) -> int:
    return conn.execute("SELECT count(*) FROM orders WHERE status = 'waiting'").fetchone()[0]


def process_next(conn: Connection, redis, block_seconds: float = 2) -> int | None:
    """Takes the oldest queued order and processes it. Returns its number, or None if the
    queue was empty or the order was not ours to process."""
    raw = redis.blmove(QUEUE, PROCESSING, block_seconds, "RIGHT", "LEFT")
    if raw is None:
        return None
    number = int(raw)
    claimed = claim(conn, number)
    if claimed is None:
        redis.lrem(PROCESSING, 1, raw)
        return None
    lines, claimed_at = claimed
    log.info(f"order {number} processing", extra={"fields": {"order": number}})
    time.sleep(PROCESS_SECONDS)
    result = finish(conn, number, lines, claimed_at)
    redis.lrem(PROCESSING, 1, raw)  # only after Postgres has the result
    if result is None:
        log.warning(f"order {number} was taken over by another worker; skipped", extra={"fields": {"order": number}})
        return None
    status, reason = result
    waiting = waiting_count(conn)
    if status == "done":
        log.info(f"order {number} done, waiting={waiting}", extra={"fields": {"order": number, "waiting": waiting}})
    else:
        log.warning(f"order {number} on hold: {reason}, waiting={waiting}", extra={"fields": {"order": number, "waiting": waiting}})
    return number


def reap(conn: Connection, redis) -> int:
    """Puts back orders a crashed worker left behind, and queues orders that were never
    queued. Safe to run in every worker at any time; returns how many it re-queued."""
    requeued = 0
    # 1. Claimed by a worker that has since died: back to waiting, front of the queue.
    for (number,) in conn.execute(
        "UPDATE orders SET status = 'waiting', claimed_at = NULL WHERE status = 'processing'"
        " AND claimed_at < now() - make_interval(secs => %s) RETURNING number",
        (STALE_SECONDS,),
    ).fetchall():
        redis.lrem(PROCESSING, 0, number)
        redis.rpush(QUEUE, number)  # right end = taken next
        requeued += 1
    # 2. In the processing list but not being processed: a worker crashed before its
    # claim committed (still waiting), or the order is finished or gone.
    in_processing = [int(n) for n in redis.lrange(PROCESSING, 0, -1)]
    if in_processing:
        status = dict(conn.execute("SELECT number, status FROM orders WHERE number = ANY(%s)", (in_processing,)).fetchall())
        for number in in_processing:
            if status.get(number) == "processing":
                continue  # a live worker has it; rule 1 handles it if that worker dies
            redis.lrem(PROCESSING, 0, number)
            if status.get(number) == "waiting":
                redis.rpush(QUEUE, number)
                requeued += 1
    # 3. Waiting but in no list: queue the oldest so it is taken first.
    listed = {int(n) for n in redis.lrange(QUEUE, 0, -1)} | {int(n) for n in redis.lrange(PROCESSING, 0, -1)}
    rows = conn.execute(
        "SELECT number FROM orders WHERE status = 'waiting' AND created_at < now() - make_interval(secs => %s)"
        " ORDER BY number DESC",
        (UNQUEUED_SECONDS,),
    ).fetchall()
    for (number,) in rows:
        if number not in listed:
            redis.rpush(QUEUE, number)
            requeued += 1
    if requeued:
        log.info("re-queued orders", extra={"fields": {"count": requeued}})
    return requeued


def stock_check(conn: Connection) -> dict:
    """Records which products are out of stock or running low; the owner page shows the latest."""
    rows = conn.execute("SELECT name, stock, low_at FROM products ORDER BY name").fetchall()
    out = [name for name, stock, _ in rows if stock == 0]
    low = [{"name": name, "stock": stock} for name, stock, low_at in rows if 0 < stock <= low_at]
    run_at: datetime = conn.execute(
        "INSERT INTO stock_reports (low, out) VALUES (%s, %s) RETURNING run_at", (Jsonb(low), Jsonb(out))
    ).fetchone()[0]
    parts = []
    if out:
        parts.append("out of stock: " + ", ".join(out))
    if low:
        parts.append("low: " + ", ".join(f"{item['name']} ({item['stock']})" for item in low))
    summary = "; ".join(parts) or "all items in stock"
    log.info(f"stock check: {summary}", extra={"fields": {"out": len(out), "low": len(low)}})
    return {"run_at": run_at.isoformat(), "out": out, "low": low}


def board(conn: Connection) -> dict:
    """What the live board shows: queued and in-progress orders, plus the latest finished."""
    def rows(where: str, order: str, limit: int) -> list[dict]:
        return [
            {
                "number": number, "items": items, "subtotal_cents": sub, "discount_cents": disc, "total_cents": total,
                "status": status, "hold_reason": reason, "created_at": created.isoformat(),
                "processed_at": processed.isoformat() if processed else None,
            }
            for number, items, sub, disc, total, status, reason, created, processed in conn.execute(
                "SELECT number, items, subtotal_cents, discount_cents, total_cents, status, hold_reason, created_at, processed_at"
                f" FROM orders WHERE {where} ORDER BY {order} LIMIT %s",
                (limit,),
            ).fetchall()
        ]

    oldest = conn.execute(
        "SELECT extract(epoch FROM now() - min(created_at)) FROM orders WHERE status = 'waiting'"
    ).fetchone()[0]
    return {
        "waiting_count": waiting_count(conn),
        "done_count": conn.execute("SELECT count(*) FROM orders WHERE status IN ('done', 'on_hold')").fetchone()[0],
        "oldest_waiting_seconds": round(float(oldest)) if oldest is not None else 0,
        "waiting": rows("status = 'waiting'", "number", 12),
        "processing": rows("status = 'processing'", "number", 6),
        "done": rows("status IN ('done', 'on_hold')", "processed_at DESC", 12),
    }


def order(conn: Connection, number: int) -> dict | None:
    row = conn.execute(
        "SELECT number, items, total_cents, status, hold_reason, created_at, processed_at FROM orders WHERE number = %s",
        (number,),
    ).fetchone()
    if row is None:
        return None
    number, items, total, status, reason, created, processed = row
    return {
        "number": number, "items": items, "total_cents": total, "status": status, "hold_reason": reason,
        "created_at": created.isoformat(), "processed_at": processed.isoformat() if processed else None,
    }


def owner_report(conn: Connection) -> dict:
    report = conn.execute("SELECT run_at, low, out FROM stock_reports ORDER BY id DESC LIMIT 1").fetchone()
    on_hold = conn.execute(
        "SELECT number, hold_reason, processed_at FROM orders WHERE status = 'on_hold' ORDER BY number"
    ).fetchall()
    stock = conn.execute("SELECT name, category, stock, low_at FROM products ORDER BY category, name").fetchall()
    return {
        "last_check": report[0].isoformat() if report else None,
        "low": report[1] if report else [],
        "out": report[2] if report else [],
        "on_hold": [{"number": n, "reason": r, "at": a.isoformat() if a else None} for n, r, a in on_hold],
        "stock": [{"name": n, "category": c, "stock": s, "low_at": l} for n, c, s, l in stock],
    }

