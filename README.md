# Acme Grocer

A small grocery shop running on Guidal (project `acme-shop`). One codebase, one image, three jobs:

| Guidal resource | What runs | Code |
|---|---|---|
| service `web` | storefront, live order board, owner page, JSON API (port 3000) | `main.py`, `pages.py`, `static/board.js` |
| service `worker` (type worker, command `python worker.py`) | packs orders one at a time, about 4 s each | `worker.py`, `shop.py` |
| cron job `stock-check` (every 2 min, `python stock_check.py`) | asks web to record what is out of stock or low | `stock_check.py` → `POST /internal/stock-check` |
| database `app-db` (Postgres) | products, orders, stock reports | `db.py` |
| database `cache` (Redis) | the order queue: `orders:queue` → `orders:processing` | `shop.py` |

How an order flows: `POST /api/orders` saves it in Postgres as `waiting` and puts its number on the Redis queue. The worker moves the oldest number to `orders:processing`, claims the order in Postgres, packs it (takes the items off the shelves, or puts it `on_hold` if something is out of stock), and only then removes it from Redis. If a worker dies mid-order, the next worker puts the order back (`shop.reap`), so no order is lost or packed twice.

API: `POST /api/orders {"items": [{"sku": "milk", "quantity": 2}]}` · `GET /api/board` · `GET /orders/<number>` · `GET /owner` · `GET /api/products`. All money is in cents.

Logs (one JSON line each): the worker writes `order N processing`, `order N done` (with `waiting`), `order N on hold: <reason>`; the stock check writes `stock check: ...`.

Tests: `./run-tests.sh` (needs Docker: starts a throwaway Postgres, uses an in-memory Redis).

Deploy (the image is the same for web and worker; deploy each service your change affects):
`guidal --reason "<why>" deploy . -s worker` and/or `guidal --reason "<why>" deploy . -s web`.

## The ops agents (`ops/`)

Five AI agents run this shop: on-call, support, data, developer and capacity. Each one takes tickets from this repo's issues and works only with its own access:

- `ops/roles/*.md`: each agent's instructions.
- `ops/claude/*.json`: the Claude Code permissions. Guidal's read tools are allowed; anything that changes the platform is denied unless that role needs it.
- `ops/start-role.sh <role>`: opens one agent in a terminal with only that role's access.
- `ops/next-ticket <role>`: waits for the next open ticket with that role's label and claims it.
