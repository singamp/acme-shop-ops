#!/usr/bin/env bash
# Simulated customers: one random order every 4 s (never pumpkins, so only the seeded
# support order goes on hold). Run in a plain Terminal; Ctrl-C to stop.
set -u
URL="${SHOP_URL:-$(cat "$HOME/guidal-demo/.shop-url")}"
SKUS=(apples bananas milk eggs cheese bread coffee rice chocolate candy-corn caramel-apple)
while true; do
  lines=""
  for _ in $(seq $((RANDOM % 3 + 1))); do
    sku=${SKUS[$((RANDOM % ${#SKUS[@]}))]}
    lines+="{\"sku\":\"$sku\",\"quantity\":$((RANDOM % 3 + 1))},"
  done
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/api/orders" \
           -H 'content-type: application/json' -d "{\"items\":[${lines%,}]}")
  echo "$(date -u +%H:%M:%S) order -> $code"
  sleep 4
done
