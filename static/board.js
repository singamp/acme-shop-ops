// Live order board: polls /api/board every second and redraws the three columns.
// Only textContent is ever set from data, never innerHTML.
"use strict";

const money = (cents) => "$" + (cents / 100).toFixed(2);
const seen = new Map(); // order number -> column it was last drawn in

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function card(order, column) {
  const node = el("div", "order " + order.status);
  const head = el("div", "head");
  head.append(el("span", "", "Order " + order.number));
  head.append(el("span", "", money(order.total_cents)));
  node.append(head);
  for (const item of order.items) {
    const line = el("div", "line");
    line.append(el("span", "", item.quantity + " × " + item.name));
    line.append(el("span", "", money(item.line_cents)));
    node.append(line);
  }
  if (order.discount_cents > 0) {
    const line = el("div", "line");
    line.append(el("span", "", "Discount"));
    line.append(el("span", "", "−" + money(order.discount_cents)));
    node.append(line);
  }
  if (order.status === "on_hold") node.append(el("div", "note pill bad", "On hold: " + order.hold_reason));
  if (order.status === "done" && order.processed_at) {
    node.append(el("div", "note", "Packed " + order.processed_at.slice(11, 19) + " GMT"));
  }
  // Animate only when the order arrives in this column (new, or moved here).
  if (seen.get(order.number) !== column) node.classList.add("enter");
  seen.set(order.number, column);
  return node;
}

function fill(column, orders, count) {
  document.getElementById(column).replaceChildren(...orders.map((o) => card(o, column)));
  document.getElementById(column + "-count").textContent = String(count);
}

async function refresh() {
  try {
    const response = await fetch("/api/board", { headers: { accept: "application/json" } });
    if (!response.ok) return;
    const board = await response.json();
    fill("waiting", board.waiting, board.waiting_count);
    fill("processing", board.processing, board.processing.length);
    fill("done", board.done, board.done_count);
    document.getElementById("alert").classList.toggle("show", board.oldest_waiting_seconds > 30);
  } catch (_) {
    // Network blip: keep the last board and try again next second.
  }
}

async function loadProducts() {
  try {
    const response = await fetch("/api/products", { headers: { accept: "application/json" } });
    if (!response.ok) return;
    const grid = document.getElementById("products");
    for (const product of await response.json()) {
      const box = el("label", "product");
      box.append(el("span", "name", product.name));
      box.append(el("span", "price", money(product.price_cents) + (product.in_stock ? "" : " · out of stock")));
      const qty = el("input");
      Object.assign(qty, { type: "number", name: product.sku, min: "0", max: "20", value: "0" });
      box.append(qty);
      grid.append(box);
    }
  } catch (_) {
    // The board still works without the product list.
  }
}

loadProducts();
refresh();
setInterval(refresh, 1000);
