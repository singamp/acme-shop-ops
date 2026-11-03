#!/usr/bin/env bash
# Runs the tests against a throwaway Postgres in Docker, then removes it.
# Usage: ./run-tests.sh   (needs Docker running and python3.12+)
set -euo pipefail
cd "$(dirname "$0")"
NAME=acme-grocer-test-pg
PORT=55432
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=test -p 127.0.0.1:$PORT:5432 postgres:17-alpine >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 1
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q --require-hashes -r requirements.txt
  .venv/bin/pip install -q -r requirements-dev.txt
fi
TEST_DATABASE_URL="postgresql://postgres:test@127.0.0.1:$PORT/postgres" .venv/bin/python -m pytest -q tests "$@"
