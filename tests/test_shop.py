"""Order logic against a real Postgres (TEST_DATABASE_URL) and an in-memory Redis.
Run with ./run-tests.sh, which starts a throwaway Postgres in Docker."""

import os
import threading

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set (use ./run-tests.sh)")
os.environ.setdefault("PGSSLMODE", "disable")
if URL:
    os.environ["DATABASE_URL"] = URL

import fakeredis  # noqa: E402

import db  # noqa: E402
import shop  # noqa: E402


def test_order_lines_reads_quantity():
    # The API stores each line as {"sku", "quantity", ...}: see shop.price().
    assert shop.order_lines([{"sku": "milk", "quantity": 2, "name": "Milk, 1 L"}]) == [("milk", 2)]


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(shop, "PROCESS_SECONDS", 0)
    with db.connect("tests") as c:
        c.execute("DROP TABLE IF EXISTS orders, products, stock_reports")
        db.migrate(c)
        yield c


@pytest.fixture
def queue():
    return fakeredis.FakeRedis(decode_responses=True)


def stock(conn, sku):
    return conn.execute("SELECT stock FROM products WHERE sku = %s", (sku,)).fetchone()[0]


def status(conn, number):
    return conn.execute("SELECT status FROM orders WHERE number = %s", (number,)).fetchone()[0]


def test_unknown_item_is_refused(conn, queue):
    with pytest.raises(shop.OrderError, match="unknown item: nope"):
        shop.place_order(conn, queue, [("nope", 1)])
    assert queue.llen(shop.QUEUE) == 0


def test_orders_are_packed_in_order_and_priced(conn, queue):
    first = shop.place_order(conn, queue, [("milk", 2), ("bread", 1)])
    second = shop.place_order(conn, queue, [("apples", 1)])
    assert first["total_cents"] == 2 * 129 + 429
    assert shop.process_next(conn, queue, 0.1) == first["number"]
    assert shop.process_next(conn, queue, 0.1) == second["number"]
    assert status(conn, first["number"]) == status(conn, second["number"]) == "done"
    assert stock(conn, "milk") == 598
    assert queue.llen(shop.PROCESSING) == 0


def test_out_of_stock_goes_on_hold_and_takes_nothing(conn, queue):
    shop.stock_check(conn)
    held = shop.place_order(conn, queue, [("milk", 1), ("pumpkin", 1)])
    shop.process_next(conn, queue, 0.1)
    assert status(conn, held["number"]) == "on_hold"
    assert stock(conn, "milk") == 600  # all or nothing
    reason = conn.execute("SELECT hold_reason FROM orders WHERE number = %s", (held["number"],)).fetchone()[0]
    assert reason.startswith("out of stock: Pumpkin (stock check at ")


def test_two_workers_never_sell_the_last_item_twice(conn, queue):
    conn.execute("UPDATE products SET stock = 1 WHERE sku = 'pumpkin'")
    a = shop.place_order(conn, queue, [("pumpkin", 1)])["number"]
    b = shop.place_order(conn, queue, [("pumpkin", 1)])["number"]
    barrier = threading.Barrier(2)

    def worker(number):
        with db.connect("tests") as c:
            lines, claimed_at = shop.claim(c, number)
            barrier.wait()
            shop.finish(c, number, lines, claimed_at)

    threads = [threading.Thread(target=worker, args=(n,)) for n in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted([status(conn, a), status(conn, b)]) == ["done", "on_hold"]
    assert stock(conn, "pumpkin") == 0


def test_order_survives_a_worker_crash_and_is_packed_once(conn, queue):
    number = shop.place_order(conn, queue, [("eggs", 1)])["number"]
    # A worker takes it, claims it, then dies before finishing.
    queue.blmove(shop.QUEUE, shop.PROCESSING, 0.1, "RIGHT", "LEFT")
    shop.claim(conn, number)
    conn.execute("UPDATE orders SET claimed_at = now() - interval '1 minute' WHERE number = %s", (number,))
    # A new worker (new pod name, nothing in memory) starts and reaps.
    assert shop.reap(conn, queue) == 1
    assert status(conn, number) == "waiting"
    assert shop.process_next(conn, queue, 0.1) == number
    assert status(conn, number) == "done"
    assert stock(conn, "eggs") == 399
    assert queue.llen(shop.QUEUE) == queue.llen(shop.PROCESSING) == 0


def test_crash_before_the_claim_commits_leaves_the_order_waiting(conn, queue, monkeypatch):
    number = shop.place_order(conn, queue, [("rice", 1)])["number"]

    def boom(_items):
        raise KeyError("qty")

    monkeypatch.setattr(shop, "order_lines", boom)
    with pytest.raises(KeyError):
        shop.process_next(conn, queue, 0.1)
    monkeypatch.undo()
    assert status(conn, number) == "waiting"
    assert shop.reap(conn, queue) == 1  # back from the processing list to the queue
    shop.process_next(conn, queue, 0.1)
    assert status(conn, number) == "done"


def test_reaper_queues_an_order_that_was_never_queued(conn, queue):
    number = conn.execute(
        "INSERT INTO orders (items, subtotal_cents, total_cents, created_at)"
        " VALUES ('[{\"sku\": \"coffee\", \"name\": \"Coffee\", \"quantity\": 1, \"unit_cents\": 899, \"line_cents\": 899}]',"
        " 899, 899, now() - interval '1 minute') RETURNING number"
    ).fetchone()[0]
    queue.lpush(shop.QUEUE, 999_999)  # left over from before a reset: no such order
    assert shop.reap(conn, queue) == 1
    assert shop.process_next(conn, queue, 0.1) == number  # taken first
    assert shop.process_next(conn, queue, 0.1) is None  # the stale number is dropped
    assert queue.llen(shop.QUEUE) == queue.llen(shop.PROCESSING) == 0


def test_a_stalled_worker_cannot_pack_an_order_twice(conn, queue):
    number = shop.place_order(conn, queue, [("eggs", 1)])["number"]
    lines, stalled_claim = shop.claim(conn, number)  # worker A claims, then stalls
    conn.execute("UPDATE orders SET claimed_at = now() - interval '1 minute' WHERE number = %s", (number,))
    shop.reap(conn, queue)  # the reaper gives the order back
    assert shop.process_next(conn, queue, 0.1) == number  # worker B packs it
    assert shop.finish(conn, number, lines, stalled_claim) is None  # A wakes up: nothing happens
    assert stock(conn, "eggs") == 399
    assert status(conn, number) == "done"
