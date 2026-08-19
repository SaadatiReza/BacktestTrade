const BASE = "/api";

export async function runBacktest(payload) {
  const res = await fetch(`${BASE}/backtest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Backtest request failed");
  }
  return res.json();
}

export async function getCandles(symbol, interval, start, end) {
  const params = new URLSearchParams({ symbol, interval, start, end });
  const res = await fetch(`${BASE}/data/candles?${params}`);
  if (!res.ok) throw new Error("Failed to load candles");
  return res.json();
}
