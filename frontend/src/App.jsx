import { useState } from "react";
import { getCandles, runBacktest } from "./api";
import Chart from "./Chart";

const DEFAULT_CONFIG = {
  spike_lookback: 3,
  spike_atr_len: 14,
  spike_atr_mult: 1.5,
  min_body_ratio: 0.65,
  use_context_filter: true,
  context_ema_len: 50,
  swing_fractal_width: 2,
  min_2l_swings: 2,
  structure_invalidation_buffer: 0,
  entry_mode: "retest",
  require_rejection_candle: true,
  sl_buffer: 0,
  max_risk_price: null,
  tp_mode: "split",
  rr_target: 2,
  max_holding_bars: 200,
  allow_concurrent_trades: false,
};

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isoDaysAgo(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 16);
}

export default function App() {
  const [symbol, setSymbol] = useState("XAU/USD");
  const [interval, setInterval_] = useState("1h");
  const [start, setStart] = useState(isoDaysAgo(60));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [config, setConfig] = useState(DEFAULT_CONFIG);

  const [candles, setCandles] = useState([]);
  const [result, setResult] = useState(null);
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [riskPerTrade, setRiskPerTrade] = useState(10);

  function updateConfig(key, value) {
    setConfig((c) => ({ ...c, [key]: value }));
  }

  async function handleRun() {
    setLoading(true);
    setError(null);
    setSelectedTrade(null);
    try {
      const payload = {
        symbol,
        interval,
        start: new Date(start).toISOString(),
        end: new Date(end).toISOString(),
        config,
      };
      const res = await runBacktest(payload);
      setResult(res);
      const c = await getCandles(symbol, interval, payload.start, payload.end);
      setCandles(c);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const stats = result?.stats;
  const trades = result?.trades || [];

  // ماشین حساب سود: ریسک هر معامله (به دلار) ضرب در R حاصل‌شده از هر معامله.
  // این یعنی همون مبلغی که در صورت SL خوردن یک معامله از دست می‌دادید.
  const closedTrades = trades.filter((t) => t.result !== "open");
  const openTrades = trades.filter((t) => t.result === "open");
  const closedR = closedTrades.reduce((sum, t) => sum + (t.r_multiple || 0), 0);
  const openR = openTrades.reduce((sum, t) => sum + (t.r_multiple || 0), 0);
  const closedProfit = closedR * riskPerTrade;
  const openProfit = openR * riskPerTrade;

  return (
    <div className="app">
      <div className="toolbar">
        <div className="field">
          <label>نماد</label>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ width: 100 }} />
        </div>
        <div className="field">
          <label>تایم‌فریم</label>
          <select value={interval} onChange={(e) => setInterval_(e.target.value)}>
            {["1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"].map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>شروع</label>
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div className="field">
          <label>پایان</label>
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <div className="field">
          <label>Entry Mode</label>
          <select value={config.entry_mode} onChange={(e) => updateConfig("entry_mode", e.target.value)}>
            <option value="retest">Retest</option>
            <option value="breakout">Breakout</option>
          </select>
        </div>
        <div className="field">
          <label>TP Mode</label>
          <select value={config.tp_mode} onChange={(e) => updateConfig("tp_mode", e.target.value)}>
            <option value="split">Split (1R + {config.rr_target}R)</option>
            <option value="single">Single ({config.rr_target}R)</option>
          </select>
        </div>
        <div className="field">
          <label>R:R Target</label>
          <input
            type="number"
            step="0.1"
            value={config.rr_target}
            onChange={(e) => updateConfig("rr_target", parseFloat(e.target.value))}
            style={{ width: 60 }}
          />
        </div>
        <div className="field">
          <label>Context Filter</label>
          <input
            type="checkbox"
            checked={config.use_context_filter}
            onChange={(e) => updateConfig("use_context_filter", e.target.checked)}
          />
        </div>
        <div className="field">
          <label>ریسک هر معامله ($)</label>
          <input
            type="number"
            step="1"
            min="0"
            value={riskPerTrade}
            onChange={(e) => setRiskPerTrade(parseFloat(e.target.value) || 0)}
            style={{ width: 70 }}
          />
        </div>
        <button onClick={handleRun} disabled={loading}>
          {loading ? "در حال اجرا..." : "اجرای بک‌تست"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="main">
        <div className="chart-col">
          <Chart candles={candles} trades={trades} selectedTrade={selectedTrade} />
        </div>
        <div className="side-col">
          {stats && (
            <div className="stats-grid">
              <StatCard label="تعداد معاملات" value={stats.total_trades} />
              <StatCard label="Winrate" value={`${(stats.winrate * 100).toFixed(1)}%`} />
              <StatCard label="Avg R" value={stats.avg_r.toFixed(2)} />
              <StatCard label="Profit Factor" value={stats.profit_factor?.toFixed(2) ?? "—"} />
              <StatCard label="Max Drawdown (R)" value={stats.max_drawdown_r.toFixed(2)} />
              <StatCard label="باز" value={stats.open_trades} />
              <StatCard
                label={`سود/زیان بسته‌شده ($${riskPerTrade}/معامله)`}
                value={`${closedProfit >= 0 ? "+" : ""}$${closedProfit.toFixed(2)}`}
                positive={closedProfit >= 0}
              />
              {stats.open_trades > 0 && (
                <StatCard
                  label="سود/زیان شناور (معاملات باز)"
                  value={`${openProfit >= 0 ? "+" : ""}$${openProfit.toFixed(2)}`}
                  positive={openProfit >= 0}
                />
              )}
            </div>
          )}

          <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>تاریخ/ساعت ورود</th>
                <th>تاریخ/ساعت خروج</th>
                <th>جهت</th>
                <th>ورود</th>
                <th>خروج</th>
                <th>R</th>
                <th>$</th>
                <th>نتیجه</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const dollar = (t.r_multiple || 0) * riskPerTrade;
                const isSelected = selectedTrade?.id === t.id;
                return (
                  <tr
                    key={t.id}
                    onClick={() => setSelectedTrade(isSelected ? null : t)}
                    style={{ cursor: "pointer", background: isSelected ? "#1f2433" : "transparent" }}
                  >
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(t.entry_time)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(t.exit_time)}</td>
                    <td>{t.direction === "bullish" ? "خرید" : "فروش"}</td>
                    <td>{t.entry_price.toFixed(2)}</td>
                    <td>{t.exit_price?.toFixed(2) ?? "—"}</td>
                    <td>{t.r_multiple?.toFixed(2) ?? "—"}</td>
                    <td className={dollar >= 0 ? "win" : "loss"}>
                      {dollar >= 0 ? "+" : ""}
                      {dollar.toFixed(2)}
                    </td>
                    <td className={t.result}>{t.result}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>

          <details>
            <summary>تنظیمات پیشرفته استراتژی</summary>
            <div className="config-grid">
              <NumberField label="Spike lookback" value={config.spike_lookback} onChange={(v) => updateConfig("spike_lookback", v)} />
              <NumberField label="ATR length" value={config.spike_atr_len} onChange={(v) => updateConfig("spike_atr_len", v)} />
              <NumberField label="ATR mult" value={config.spike_atr_mult} step={0.1} onChange={(v) => updateConfig("spike_atr_mult", v)} />
              <NumberField label="Min body ratio" value={config.min_body_ratio} step={0.05} onChange={(v) => updateConfig("min_body_ratio", v)} />
              <NumberField label="Context EMA len" value={config.context_ema_len} onChange={(v) => updateConfig("context_ema_len", v)} />
              <NumberField label="Swing width" value={config.swing_fractal_width} onChange={(v) => updateConfig("swing_fractal_width", v)} />
              <NumberField label="Min 2L swings" value={config.min_2l_swings} onChange={(v) => updateConfig("min_2l_swings", v)} />
              <NumberField label="Max holding bars" value={config.max_holding_bars} onChange={(v) => updateConfig("max_holding_bars", v)} />
              <div className="field">
                <label title="اگه خاموش باشه، تا معامله‌ی قبلی بسته نشه سیگنال بعدی گرفته نمی‌شه (رفتار واقعی معامله‌گری). اگه روشن باشه، همه‌ی ستاپ‌های شناسایی‌شده مستقل از هم‌پوشانی زمانی حساب می‌شن.">
                  اجازه‌ی معاملات هم‌زمان/هم‌پوشان
                </label>
                <input
                  type="checkbox"
                  checked={config.allow_concurrent_trades}
                  onChange={(e) => updateConfig("allow_concurrent_trades", e.target.checked)}
                />
              </div>
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, positive }) {
  const colorClass = positive === true ? "win" : positive === false ? "loss" : "";
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div className={`value ${colorClass}`}>{value}</div>
    </div>
  );
}

function NumberField({ label, value, onChange, step = 1 }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" step={step} value={value} onChange={(e) => onChange(parseFloat(e.target.value))} />
    </div>
  );
}
