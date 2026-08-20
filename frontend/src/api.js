const BASE = "/api";

function formatErrorDetail(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors: [{loc, msg, ...}, ...]
    return detail.map((e) => (e.loc ? `${e.loc.slice(-1)[0]}: ${e.msg}` : e.msg || JSON.stringify(e))).join("; ");
  }
  return JSON.stringify(detail);
}

export async function getStrategies() {
  const res = await fetch(`${BASE}/strategies`);
  if (!res.ok) throw new Error("Failed to load strategies");
  return res.json();
}

export async function runBacktest(payload) {
  const res = await fetch(`${BASE}/backtest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(formatErrorDetail(err.detail));
  }
  return res.json();
}

export async function getCandles(symbol, interval, start, end) {
  const params = new URLSearchParams({ symbol, interval, start, end });
  const res = await fetch(`${BASE}/data/candles?${params}`);
  if (!res.ok) throw new Error("Failed to load candles");
  return res.json();
}
