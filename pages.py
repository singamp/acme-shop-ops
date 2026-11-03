"""Browser pages. A request that accepts text/html gets a page; everything else (curl,
agents, health checks) gets JSON. Every value shown goes through html.escape, and the
live board's script only ever sets textContent."""

from html import escape

from fastapi import Request
from fastapi.responses import HTMLResponse

# One same-origin script (the board), inline styles, nothing else.
PAGE_HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline';"
    " form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}

STYLE = """
:root { color-scheme: light; --ink:#17201b; --muted:#5b665f; --line:#dfe6e1; --bg:#f4f7f5; --card:#fff;
  --accent:#1f7a4d; --accent-ink:#fff; --ok:#167a43; --ok-bg:#e6f4ec; --warn:#9a5b00; --warn-bg:#fff3dc;
  --bad:#b42318; --bad-bg:#fdecea; }
* { box-sizing:border-box; }
body { margin:0; font:16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif; color:var(--ink); background:var(--bg); }
header { background:var(--card); border-bottom:1px solid var(--line); }
.bar { max-width:1180px; margin:0 auto; padding:16px 20px; display:flex; align-items:center; gap:12px; }
.logo { width:36px; height:36px; border-radius:9px; background:var(--accent); color:var(--accent-ink); display:grid; place-items:center; font-weight:800; }
.brand { font-weight:750; font-size:19px; } .sub { color:var(--muted); font-size:14px; }
nav { margin-left:auto; display:flex; gap:18px; } nav a { color:var(--ink); text-decoration:none; font-weight:600; }
main { max-width:1180px; margin:24px auto 48px; padding:0 20px; }
h1 { font-size:28px; margin:0 0 4px; } h2 { font-size:18px; margin:0 0 12px; } p.lead { color:var(--muted); margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:16px; }
.pill { font-size:13px; font-weight:650; padding:2px 10px; border-radius:999px; white-space:nowrap; }
.ok { color:var(--ok); background:var(--ok-bg); } .warn { color:var(--warn); background:var(--warn-bg); } .bad { color:var(--bad); background:var(--bad-bg); }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:10px; }
.product { border:1px solid var(--line); border-radius:10px; padding:10px 12px; display:flex; flex-direction:column; gap:6px; }
.product .name { font-weight:600; } .price { font-variant-numeric:tabular-nums; color:var(--muted); }
.product input { width:64px; font:inherit; padding:4px 6px; border:1px solid var(--line); border-radius:6px; }
button { font:inherit; font-weight:650; color:var(--accent-ink); background:var(--accent); border:0; border-radius:8px; padding:10px 18px; cursor:pointer; }
.board { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:14px; }
@media (max-width:820px) { .board { grid-template-columns:1fr; } }
.col { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; min-height:320px; }
.col h2 { display:flex; justify-content:space-between; align-items:center; }
.count { font-variant-numeric:tabular-nums; }
.order { border:1px solid var(--line); border-left:4px solid var(--line); border-radius:10px; padding:10px 12px; margin-top:10px; background:#fff; }
.order.waiting { border-left-color:#c5ccc7; } .order.processing { border-left-color:#d08a00; background:#fffaf0; }
.order.done { border-left-color:var(--ok); } .order.on_hold { border-left-color:var(--bad); background:#fff7f6; }
.order .head { display:flex; justify-content:space-between; font-weight:650; }
.order .line { display:flex; justify-content:space-between; color:var(--muted); font-size:14px; font-variant-numeric:tabular-nums; }
.order .total { display:flex; justify-content:space-between; font-weight:650; border-top:1px dashed var(--line); margin-top:6px; padding-top:6px; }
.order .note { font-size:13px; margin-top:6px; }
.enter { animation:enter .45s ease-out; }
@keyframes enter { from { opacity:0; transform:translateY(-8px) scale(.98); } to { opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce) { .enter { animation:none; } }
.alert { display:none; margin-bottom:14px; padding:12px 16px; border-radius:10px; font-weight:650; color:var(--bad); background:var(--bad-bg); }
.alert.show { display:block; }
table { width:100%; border-collapse:collapse; } td, th { text-align:left; padding:8px 6px; border-top:1px solid var(--line); }
th { color:var(--muted); font-weight:600; font-size:14px; border-top:0; }
.err h1 { color:var(--bad); }
"""


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def page(title: str, body: str, status_code: int = 200, script: bool = False, refresh: int | None = None) -> HTMLResponse:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    tag = '<script src="/static/board.js" defer></script>' if script else ""
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}
<title>{escape(title)} · Acme Grocer</title><style>{STYLE}</style>{tag}</head><body>
<header><div class="bar"><div class="logo">A</div><div><div class="brand">Acme Grocer</div>
<div class="sub">Fresh groceries, packed to order</div></div>
<nav><a href="/">Shop</a><a href="/owner">Owner</a></nav></div></header>
<main>{body}</main></body></html>"""
    return HTMLResponse(html, status_code=status_code, headers=PAGE_HEADERS)


def store_page() -> HTMLResponse:
    # Products and the board are filled in by /static/board.js, so this page never
    # touches the database and stays the platform's always-200 health check.
    body = """<h1>Today's orders</h1>
<p class="lead">Every order is queued and packed by our worker, one at a time.</p>
<div id="alert" class="alert" role="status">Orders delayed: we're catching up.</div>
<div class="board">
<section class="col"><h2>Waiting <span class="pill warn count" id="waiting-count">0</span></h2><div id="waiting"></div></section>
<section class="col"><h2>Packing <span class="pill warn count" id="processing-count">0</span></h2><div id="processing"></div></section>
<section class="col"><h2>Done <span class="pill ok count" id="done-count">0</span></h2><div id="done"></div></section>
</div>
<div class="card" style="margin-top:16px"><h2>Place an order</h2>
<form method="post" action="/order/new"><div class="grid" id="products"></div>
<p><button type="submit">Place order</button></p></form></div>"""
    return page("Shop", body, script=True)


def _lines(items: list[dict]) -> str:
    return "".join(
        f'<tr><td>{escape(str(i["name"]))}</td><td>{int(i["quantity"])}</td><td>{money(int(i["unit_cents"]))}</td>'
        f'<td>{money(int(i["line_cents"]))}</td></tr>'
        for i in items
    )


STATUS_PILL = {"waiting": ("warn", "Waiting"), "processing": ("warn", "Packing"), "done": ("ok", "Done"), "on_hold": ("bad", "On hold")}


def order_page(order: dict) -> HTMLResponse:
    css, label = STATUS_PILL[order["status"]]
    reason = f'<p class="note">{escape(order["hold_reason"])}</p>' if order.get("hold_reason") else ""
    done_at = f' · finished {escape(order["processed_at"][:19].replace("T", " "))} GMT' if order.get("processed_at") else ""
    body = f"""<h1>Order {int(order["number"])} <span class="pill {css}">{label}</span></h1>
<p class="lead">Placed {escape(order["created_at"][:19].replace("T", " "))} GMT{done_at}</p>{reason}
<div class="card"><table><tr><th>Item</th><th>Qty</th><th>Price</th><th>Line total</th></tr>{_lines(order["items"])}
<tr><th colspan="3">Total</th><th>{money(int(order["total_cents"]))}</th></tr></table></div>"""
    return page(f"Order {int(order['number'])}", body, refresh=5 if order["status"] in ("waiting", "processing") else None)


def _level(item: dict) -> str:
    if item["stock"] == 0:
        return '<span class="pill bad">Out</span>'
    if item["stock"] <= item["low_at"]:
        return '<span class="pill warn">Low</span>'
    return '<span class="pill ok">OK</span>'


def owner_page(report: dict) -> HTMLResponse:
    when = escape(report["last_check"][:19].replace("T", " ")) + " GMT" if report["last_check"] else "not run yet"
    out = "".join(f'<li>{escape(str(name))}</li>' for name in report["out"]) or "<li>Nothing</li>"
    low = "".join(f'<li>{escape(str(i["name"]))}: {int(i["stock"])} left</li>' for i in report["low"]) or "<li>Nothing</li>"
    held = "".join(
        f'<tr><td><a href="/orders/{int(h["number"])}">{int(h["number"])}</a></td><td>{escape(h["reason"] or "")}</td></tr>'
        for h in report["on_hold"]
    ) or '<tr><td colspan="2">No orders on hold</td></tr>'
    stock = "".join(
        f'<tr><td>{escape(s["name"])}</td><td>{escape(s["category"])}</td><td>{int(s["stock"])}</td><td>{_level(s)}</td></tr>'
        for s in report["stock"]
    )
    body = f"""<h1>Owner dashboard</h1><p class="lead">Last stock check: {when}. The check runs every 2 minutes.</p>
<div class="board">
<section class="card"><h2>Out of stock</h2><ul>{out}</ul></section>
<section class="card"><h2>Running low</h2><ul>{low}</ul></section>
<section class="card"><h2>Orders on hold</h2><table>{held}</table></section>
</div>
<div class="card"><h2>Shelves</h2><table><tr><th>Item</th><th>Category</th><th>Stock</th><th></th></tr>{stock}</table></div>"""
    return page("Owner", body, refresh=10)


def error_page() -> HTMLResponse:
    body = """<div class="err"><h1>Something went wrong</h1>
<p class="lead">We couldn't load this page. Please try again shortly.</p></div>"""
    return page("Error", body, status_code=500)
