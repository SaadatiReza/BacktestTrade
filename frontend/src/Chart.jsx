import { createChart, CrosshairMode } from "lightweight-charts";
import { useEffect, useRef } from "react";

const RESULT_COLOR = {
  win: "#22c55e",
  loss: "#ef4444",
  breakeven: "#9aa0ab",
};

export default function Chart({ candles, trades, selectedTrade }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const priceLinesRef = useRef([]);
  const tpZoneRef = useRef(null);
  const slZoneRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#0f1117" }, textColor: "#e6e6e6" },
      grid: {
        vertLines: { color: "#1c2030" },
        horzLines: { color: "#1c2030" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    // shaded reward zone (entry -> TP) and risk zone (entry -> SL), bounded
    // to the selected trade's time window via baseline series: the fill
    // sits between each series' line value and its baseValue (= entry price)
    const zoneCommon = {
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      topLineColor: "rgba(0,0,0,0)",
      bottomLineColor: "rgba(0,0,0,0)",
    };
    const tpZone = chart.addBaselineSeries({
      ...zoneCommon,
      baseValue: { type: "price", price: 0 },
      topFillColor1: "rgba(34,197,94,0.18)",
      topFillColor2: "rgba(34,197,94,0.18)",
      bottomFillColor1: "rgba(34,197,94,0.18)",
      bottomFillColor2: "rgba(34,197,94,0.18)",
    });
    const slZone = chart.addBaselineSeries({
      ...zoneCommon,
      baseValue: { type: "price", price: 0 },
      topFillColor1: "rgba(239,68,68,0.18)",
      topFillColor2: "rgba(239,68,68,0.18)",
      bottomFillColor1: "rgba(239,68,68,0.18)",
      bottomFillColor2: "rgba(239,68,68,0.18)",
    });

    chartRef.current = chart;
    seriesRef.current = series;
    tpZoneRef.current = tpZone;
    slZoneRef.current = slZone;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !candles?.length) return;
    const data = candles.map((c) => ({
      time: Math.floor(new Date(c.time).getTime() / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    if (!seriesRef.current) return;
    let markers;
    if (selectedTrade) {
      markers = [
        {
          time: Math.floor(new Date(selectedTrade.entry_time).getTime() / 1000),
          position: selectedTrade.direction === "bullish" ? "belowBar" : "aboveBar",
          color: "#3b82f6",
          shape: selectedTrade.direction === "bullish" ? "arrowUp" : "arrowDown",
          text: "ورود",
        },
      ];
      if (selectedTrade.exit_time) {
        markers.push({
          time: Math.floor(new Date(selectedTrade.exit_time).getTime() / 1000),
          position: selectedTrade.direction === "bullish" ? "aboveBar" : "belowBar",
          color: RESULT_COLOR[selectedTrade.result] || "#9aa0ab",
          shape: "circle",
          text: `خروج ${selectedTrade.r_multiple != null ? selectedTrade.r_multiple.toFixed(2) + "R" : ""}`,
        });
      }
    } else {
      markers = (trades || []).map((t) => ({
        time: Math.floor(new Date(t.entry_time).getTime() / 1000),
        position: t.direction === "bullish" ? "belowBar" : "aboveBar",
        color: RESULT_COLOR[t.result] || "#9aa0ab",
        shape: t.direction === "bullish" ? "arrowUp" : "arrowDown",
        text: `${t.direction === "bullish" ? "L" : "S"} ${t.r_multiple != null ? t.r_multiple.toFixed(2) + "R" : ""}`,
      }));
    }
    markers.sort((a, b) => a.time - b.time);
    seriesRef.current.setMarkers(markers);
  }, [trades, selectedTrade]);

  useEffect(() => {
    if (!tpZoneRef.current || !slZoneRef.current) return;
    if (!selectedTrade) {
      tpZoneRef.current.setData([]);
      slZoneRef.current.setData([]);
      return;
    }

    const entryT = Math.floor(new Date(selectedTrade.entry_time).getTime() / 1000);
    const exitT = selectedTrade.exit_time ? Math.floor(new Date(selectedTrade.exit_time).getTime() / 1000) : entryT;
    const tp = selectedTrade.tp2_price ?? selectedTrade.tp1_price;

    tpZoneRef.current.applyOptions({ baseValue: { type: "price", price: selectedTrade.entry_price } });
    slZoneRef.current.applyOptions({ baseValue: { type: "price", price: selectedTrade.entry_price } });

    if (exitT > entryT) {
      tpZoneRef.current.setData(
        tp != null
          ? [
              { time: entryT, value: tp },
              { time: exitT, value: tp },
            ]
          : []
      );
      slZoneRef.current.setData([
        { time: entryT, value: selectedTrade.sl_price },
        { time: exitT, value: selectedTrade.sl_price },
      ]);
    } else {
      tpZoneRef.current.setData([]);
      slZoneRef.current.setData([]);
    }
  }, [selectedTrade]);

  useEffect(() => {
    if (!seriesRef.current) return;
    priceLinesRef.current.forEach((l) => seriesRef.current.removePriceLine(l));
    priceLinesRef.current = [];
    if (!selectedTrade) return;

    const lines = [
      { price: selectedTrade.entry_price, color: "#3b82f6", title: "Entry" },
      { price: selectedTrade.sl_price, color: "#ef4444", title: "SL" },
    ];
    if (selectedTrade.tp1_price) lines.push({ price: selectedTrade.tp1_price, color: "#22c55e", title: "TP1" });
    if (selectedTrade.tp2_price) lines.push({ price: selectedTrade.tp2_price, color: "#16a34a", title: "TP2" });
    if (selectedTrade.meta?.level_price) lines.push({ price: selectedTrade.meta.level_price, color: "#a855f7", title: "Level" });

    priceLinesRef.current = lines.map((l) =>
      seriesRef.current.createPriceLine({
        price: l.price,
        color: l.color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: l.title,
      })
    );
  }, [selectedTrade]);

  // clicking a trade in the report should isolate it: zoom the timeline to
  // that trade's spike-to-exit window instead of leaving the whole range
  // cluttered with every other trade's marker
  useEffect(() => {
    if (!chartRef.current) return;
    if (!selectedTrade) {
      chartRef.current.timeScale().fitContent();
      return;
    }
    const fromTime = selectedTrade.meta?.spike_start_time || selectedTrade.entry_time;
    const toTime = selectedTrade.exit_time || selectedTrade.entry_time;
    const from = Math.floor(new Date(fromTime).getTime() / 1000);
    const to = Math.floor(new Date(toTime).getTime() / 1000);

    let barSeconds = 3600;
    if (candles && candles.length > 1) {
      const diff = Math.abs(new Date(candles[1].time).getTime() - new Date(candles[0].time).getTime()) / 1000;
      if (diff > 0) barSeconds = diff;
    }
    const pad = barSeconds * 15;
    chartRef.current.timeScale().setVisibleRange({ from: from - pad, to: to + pad });
  }, [selectedTrade, candles]);

  return <div ref={containerRef} className="chart-container" />;
}
