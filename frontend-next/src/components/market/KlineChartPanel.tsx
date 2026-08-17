"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { LineChart, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { getWs } from "@/lib/ws";
import { useMarketOverviewAll } from "@/hooks/useTradingData";

const SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ARB", "ASTER", "VIRTUAL", "XPL"];
const PERIODS = [
  { label: "1m", value: "1m" }, { label: "3m", value: "3m" }, { label: "5m", value: "5m" },
  { label: "15m", value: "15m" }, { label: "30m", value: "30m" },
  { label: "1h", value: "1h" }, { label: "2h", value: "2h" }, { label: "4h", value: "4h" },
  { label: "8h", value: "8h" }, { label: "12h", value: "12h" },
  { label: "1d", value: "1d" }, { label: "3d", value: "3d" },
  { label: "1w", value: "1w" }, { label: "1M", value: "1M" },
];
const EXCHANGES = [
  { id: "asterdex", label: "Asterdex" },
  { id: "binance", label: "Binance" },
  { id: "okx", label: "OKX" },
  { id: "bybit", label: "Bybit" },
  { id: "hyperliquid", label: "Hyperliquid" },
];

const INITIAL_COUNT = 300;
const HISTORY_BATCH = 200;
const MAX_BARS = 2000;

const fmtNum = (v: number) =>
  !v || v <= 0 ? "—" : v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v.toFixed(4);

const toTime = (k: any) => {
  let t =
    typeof k.timestamp === "number"
      ? k.timestamp
      : Math.floor(new Date(k.datetime || k.timestamp).getTime() / 1000);
  if (!Number.isFinite(t) || t <= 0) return 0;
  // 毫秒时间戳 → 秒（lightweight-charts 需要秒）
  if (t > 1e12) t = Math.floor(t / 1000);
  return t;
};

function sma(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    const slice = data.slice(i - period + 1, i + 1);
    result.push(slice.reduce((a, b) => a + b, 0) / period);
  }
  return result;
}

function ema(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  const k = 2 / (period + 1);
  let prev: number | null = null;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    if (prev === null) {
      const slice = data.slice(0, period);
      prev = slice.reduce((a, b) => a + b, 0) / period;
      result.push(prev);
    } else {
      prev = data[i] * k + prev * (1 - k);
      result.push(prev);
    }
  }
  return result;
}

function rsi(closes: number[], period: number = 14): (number | null)[] {
  const result: (number | null)[] = [];
  let gains = 0, losses = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) { result.push(null); continue; }
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    if (i <= period) {
      gains += gain; losses += loss;
      if (i === period) {
        gains /= period; losses /= period;
        result.push(losses === 0 ? 100 : 100 - 100 / (1 + gains / losses));
      } else { result.push(null); }
    } else {
      gains = (gains * (period - 1) + gain) / period;
      losses = (losses * (period - 1) + loss) / period;
      result.push(losses === 0 ? 100 : 100 - 100 / (1 + gains / losses));
    }
  }
  return result;
}

function boll(closes: number[], period = 20, mult = 2) {
  const mid = sma(closes, period);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  for (let i = 0; i < closes.length; i++) {
    if (mid[i] == null || i < period - 1) {
      upper.push(null); lower.push(null); continue;
    }
    const slice = closes.slice(i - period + 1, i + 1);
    const mean = mid[i] as number;
    const variance = slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period;
    const std = Math.sqrt(variance);
    upper.push(mean + mult * std);
    lower.push(mean - mult * std);
  }
  return { mid, upper, lower };
}

function macd(closes: number[], fast = 12, slow = 26, signal = 9) {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const dif: (number | null)[] = closes.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? (emaFast[i] as number) - (emaSlow[i] as number) : null
  );
  const difNums = dif.map((v) => (v == null ? 0 : v));
  const deaRaw = ema(difNums, signal);
  // EMA 在前 period-1 为 null，但我们喂了 0；按 dif 有效位对齐
  const dea: (number | null)[] = dif.map((v, i) => (v == null ? null : deaRaw[i]));
  const hist: (number | null)[] = dif.map((v, i) =>
    v != null && dea[i] != null ? v - (dea[i] as number) : null
  );
  return { dif, dea, hist };
}

function dedupeBars(rows: any[]): any[] {
  const byTs = new Map<number, any>();
  for (const k of rows || []) {
    const t = toTime(k);
    if (!t) continue;
    byTs.set(t, k);
  }
  return Array.from(byTs.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, v]) => v);
}

function normalizeSymbol(raw?: string | null): string {
  if (!raw) return "BTC";
  return String(raw)
    .toUpperCase()
    .trim()
    .replace(/USDT$/i, "")
    .split("-")[0]
    .split("/")[0]
    .replace(/[^A-Z0-9]/g, "") || "BTC";
}

/** K 线图表面板（已并入全市场数据中台） */
export function KlineChartPanel({
  embedded = false,
  visible = true,
  initialSymbol,
  initialExchange,
  focusKey,
}: {
  embedded?: boolean;
  /** Tab 是否可见；从隐藏切回时强制按容器尺寸重绘 */
  visible?: boolean;
  /** 从总览点击传入的交易对 */
  initialSymbol?: string;
  /** 从总览点击传入的交易所 */
  initialExchange?: string;
  /** 每次点击递增，保证重复点同一交易对也会切过去 */
  focusKey?: number | string;
}) {
  const EMBED_H = 560;
  const [symbol, setSymbol] = useState(() => normalizeSymbol(initialSymbol) || "BTC");
  const [period, setPeriod] = useState("15m");
  const [exchange, setExchange] = useState(
    initialExchange && initialExchange !== "all" ? initialExchange : "asterdex"
  );
  const [symbolQuery, setSymbolQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    if (initialSymbol) {
      setSymbol(normalizeSymbol(initialSymbol));
      setSymbolQuery("");
      setSearchOpen(false);
    }
    if (initialExchange && initialExchange !== "all") {
      setExchange(initialExchange);
    }
  }, [focusKey, initialSymbol, initialExchange]);
  const [showMA, setShowMA] = useState(true);
  const [showEMA, setShowEMA] = useState(false);
  const [showBOLL, setShowBOLL] = useState(false);
  const [showRSI, setShowRSI] = useState(false);
  const [showMACD, setShowMACD] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [livePrice, setLivePrice] = useState<number | null>(null);

  const { data: allMarket } = useMarketOverviewAll(exchange);
  const symbolMeta = allMarket?.rows?.find((r: any) => r.symbol === symbol);
  const symbolMatches = (allMarket?.rows ?? [])
    .filter((r: any) => {
      const q = symbolQuery.trim().toUpperCase().replace(/USDT$/i, "");
      if (!q) return false;
      return r.symbol.includes(q) || r.symbol.startsWith(q);
    })
    .slice(0, 10);

  const chartRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<any>(null);
  const rsiInstance = useRef<any>(null);
  const macdInstance = useRef<any>(null);
  const candleSeriesRef = useRef<any>(null);
  const volSeriesRef = useRef<any>(null);
  const maSeriesRef = useRef<Record<number, any>>({});
  const emaSeriesRef = useRef<Record<number, any>>({});
  const bollSeriesRef = useRef<Record<string, any>>({});
  const rsiSeriesRef = useRef<any>(null);
  const macdSeriesRef = useRef<Record<string, any>>({});
  const klinesDataRef = useRef<any[]>([]);
  const refreshIndicatorsRef = useRef<(() => void) | null>(null);
  const applyBarsRef = useRef<((rows: any[], opts?: { prepend?: boolean }) => void) | null>(null);
  const symbolRef = useRef(symbol);
  const periodRef = useRef(period);
  const exchangeRef = useRef(exchange);
  const loadingMoreRef = useRef(false);
  const historyDoneRef = useRef(false);

  const displayPrice = livePrice ?? symbolMeta?.price ?? 0;

  const paintAllSeries = useCallback((rows: any[]) => {
    if (!candleSeriesRef.current || !rows.length) return;
    const times = rows.map(toTime);
    const closes = rows.map((k: any) => k.close ?? k.close_price ?? 0);
    candleSeriesRef.current.setData(rows.map((k: any) => ({
      time: toTime(k), open: k.open, high: k.high, low: k.low, close: k.close,
    })));
    volSeriesRef.current?.setData(rows.map((k: any) => ({
      time: toTime(k), value: k.volume || 0,
      color: k.close >= k.open ? "#34D39933" : "#FB718533",
    })));
    [7, 25, 99].forEach((p) => {
      const s = maSeriesRef.current[p];
      if (!s) return;
      const vals = sma(closes, p);
      s.setData(times.map((t, j) => ({ time: t, value: vals[j] })).filter((d: any) => d.value != null));
    });
    [12, 26].forEach((p) => {
      const s = emaSeriesRef.current[p];
      if (!s) return;
      const vals = ema(closes, p);
      s.setData(times.map((t, j) => ({ time: t, value: vals[j] })).filter((d: any) => d.value != null));
    });
    if (bollSeriesRef.current.mid) {
      const b = boll(closes);
      bollSeriesRef.current.mid.setData(times.map((t, j) => ({ time: t, value: b.mid[j] })).filter((d: any) => d.value != null));
      bollSeriesRef.current.upper?.setData(times.map((t, j) => ({ time: t, value: b.upper[j] })).filter((d: any) => d.value != null));
      bollSeriesRef.current.lower?.setData(times.map((t, j) => ({ time: t, value: b.lower[j] })).filter((d: any) => d.value != null));
    }
    if (rsiSeriesRef.current) {
      const vals = rsi(closes, 14);
      rsiSeriesRef.current.setData(times.map((t, j) => ({ time: t, value: vals[j] })).filter((d: any) => d.value != null));
    }
    if (macdSeriesRef.current.dif) {
      const m = macd(closes);
      macdSeriesRef.current.dif.setData(times.map((t, j) => ({ time: t, value: m.dif[j] })).filter((d: any) => d.value != null));
      macdSeriesRef.current.dea?.setData(times.map((t, j) => ({ time: t, value: m.dea[j] })).filter((d: any) => d.value != null));
      macdSeriesRef.current.hist?.setData(times.map((t, j) => ({
        time: t, value: m.hist[j],
        color: (m.hist[j] ?? 0) >= 0 ? "#34D39966" : "#FB718566",
      })).filter((d: any) => d.value != null));
    }
  }, []);

  const loadHistoryBefore = useCallback(async () => {
    if (loadingMoreRef.current || historyDoneRef.current) return;
    const rows = klinesDataRef.current;
    if (!rows.length) return;
    const oldest = toTime(rows[0]);
    if (!oldest) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const resp = await api.getKlines(symbolRef.current, periodRef.current, HISTORY_BATCH, exchangeRef.current, oldest - 1);
      const more = dedupeBars(resp?.data ?? []);
      if (!more.length) {
        historyDoneRef.current = true;
        return;
      }
      const merged = dedupeBars([...more, ...rows]);
      // 若没有更早的数据
      if (toTime(merged[0]) >= oldest) {
        historyDoneRef.current = true;
      }
      if (merged.length > MAX_BARS) {
        klinesDataRef.current = merged.slice(merged.length - MAX_BARS);
      } else {
        klinesDataRef.current = merged;
      }
      paintAllSeries(klinesDataRef.current);
    } catch (e) {
      console.warn("[Charts] history load failed", e);
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }, [paintAllSeries]);

  useEffect(() => {
    let cancelled = false;
    historyDoneRef.current = false;

    const waitForBox = async (el: HTMLElement) => {
      for (let i = 0; i < 40; i++) {
        const w = el.clientWidth;
        const h = el.clientHeight;
        if (w >= 40 && h >= 80) return { w, h };
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
      }
      return {
        w: Math.max(el.clientWidth || 0, el.parentElement?.clientWidth || 0, 640),
        h: Math.max(el.clientHeight || 0, embedded ? EMBED_H : 420),
      };
    };

    const resizeChart = (w: number, h: number) => {
      if (w < 10 || h < 10) return;
      try {
        chartInstance.current?.applyOptions({ width: w, height: h });
        if (rsiInstance.current && rsiRef.current) {
          rsiInstance.current.applyOptions({ width: rsiRef.current.clientWidth || w });
        }
        if (macdInstance.current && macdRef.current) {
          macdInstance.current.applyOptions({ width: macdRef.current.clientWidth || w });
        }
      } catch { /* ignore */ }
    };

    async function loadChart() {
      // 等 DOM 挂载：Tab 刚切换时 ref 偶发尚未就绪
      for (let i = 0; i < 20 && !chartRef.current; i++) {
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
      }
      if (!chartRef.current) {
        if (!cancelled) {
          setError("图表容器未就绪，请重试");
          setLoading(false);
        }
        return;
      }
      setLoading(true);
      setError(null);
      setLivePrice(null);

      try {
        const resp = await api.getKlines(symbol, period, INITIAL_COUNT, exchange);
        const data = dedupeBars(resp?.data ?? []).filter((k: any) => {
          const t = toTime(k);
          return Number.isFinite(t) && t > 0 && k.open != null && k.close != null;
        });
        klinesDataRef.current = data;
        if (cancelled) return;
        if (!data.length) {
          setError("无数据");
          setLoading(false);
          return;
        }

        const lastClose = data[data.length - 1]?.close;
        if (lastClose) setLivePrice(Number(lastClose));

        const { createChart, CandlestickSeries, HistogramSeries, LineSeries } = await import("lightweight-charts");
        if (cancelled || !chartRef.current) return;

        // 清空旧实例，避免同一容器叠两层 canvas
        if (chartInstance.current) { try { chartInstance.current.remove(); } catch {} chartInstance.current = null; }
        if (rsiInstance.current) { try { rsiInstance.current.remove(); } catch {} rsiInstance.current = null; }
        if (macdInstance.current) { try { macdInstance.current.remove(); } catch {} macdInstance.current = null; }
        chartRef.current.innerHTML = "";
        if (rsiRef.current) rsiRef.current.innerHTML = "";
        if (macdRef.current) macdRef.current.innerHTML = "";
        maSeriesRef.current = {};
        emaSeriesRef.current = {};
        bollSeriesRef.current = {};
        macdSeriesRef.current = {};
        candleSeriesRef.current = null;
        volSeriesRef.current = null;
        rsiSeriesRef.current = null;

        const box = await waitForBox(chartRef.current);
        if (cancelled || !chartRef.current) return;
        const chartW = box.w;
        const chartH = embedded ? (showRSI || showMACD ? Math.max(360, EMBED_H - ((showRSI ? 118 : 0) + (showMACD ? 128 : 0))) : EMBED_H) : Math.max(box.h, 320);

        const chart = createChart(chartRef.current, {
          width: chartW,
          height: chartH,
          layout: { background: { color: "transparent" }, textColor: "#94A1BC", fontSize: 11 },
          grid: { vertLines: { color: "rgba(30,37,48,0.3)" }, horzLines: { color: "rgba(30,37,48,0.3)" } },
          crosshair: { mode: 1 },
          rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
          timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
        });
        chartInstance.current = chart;

        const candleSeries = chart.addSeries(CandlestickSeries, {
          upColor: "#34D399", downColor: "#FB7185", borderUpColor: "#34D399", borderDownColor: "#FB7185",
          wickUpColor: "#34D399", wickDownColor: "#FB7185",
        });
        candleSeriesRef.current = candleSeries;

        const volSeries = chart.addSeries(HistogramSeries, {
          color: "#22D3EE44", priceFormat: { type: "volume" }, priceScaleId: "vol",
        });
        volSeriesRef.current = volSeries;
        chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

        const closes = data.map((k: any) => k.close ?? 0);
        const times = data.map(toTime);

        if (showMA) {
          [7, 25, 99].forEach((p, i) => {
            const series = chart.addSeries(LineSeries, {
              color: ["#22D3EE", "#FBBF24", "#8B5CF6"][i], lineWidth: 1,
              priceLineVisible: false, lastValueVisible: false,
            });
            maSeriesRef.current[p] = series;
          });
        }
        if (showEMA) {
          [12, 26].forEach((p, i) => {
            const series = chart.addSeries(LineSeries, {
              color: ["#34D399", "#FB7185"][i], lineWidth: 1, lineStyle: 2,
              priceLineVisible: false, lastValueVisible: false,
            });
            emaSeriesRef.current[p] = series;
          });
        }
        if (showBOLL) {
          bollSeriesRef.current.mid = chart.addSeries(LineSeries, {
            color: "#FBBF24", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
          });
          bollSeriesRef.current.upper = chart.addSeries(LineSeries, {
            color: "#22D3EE88", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
          });
          bollSeriesRef.current.lower = chart.addSeries(LineSeries, {
            color: "#22D3EE88", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
          });
        }

        if (showRSI && rsiRef.current) {
          const rsiChart = createChart(rsiRef.current, {
            width: rsiRef.current.clientWidth || chartW, height: 110,
            layout: { background: { color: "transparent" }, textColor: "#94A1BC", fontSize: 10 },
            grid: { vertLines: { color: "rgba(30,37,48,0.2)" }, horzLines: { color: "rgba(30,37,48,0.2)" } },
            rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
            timeScale: { visible: false },
          });
          rsiInstance.current = rsiChart;
          rsiSeriesRef.current = rsiChart.addSeries(LineSeries, { color: "#8B5CF6", lineWidth: 2 });
          chart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
            if (!range || !rsiInstance.current) return;
            try { rsiInstance.current.timeScale().setVisibleLogicalRange(range); } catch {}
          });
        }

        if (showMACD && macdRef.current) {
          const macdChart = createChart(macdRef.current, {
            width: macdRef.current.clientWidth || chartW, height: 120,
            layout: { background: { color: "transparent" }, textColor: "#94A1BC", fontSize: 10 },
            grid: { vertLines: { color: "rgba(30,37,48,0.2)" }, horzLines: { color: "rgba(30,37,48,0.2)" } },
            rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
            timeScale: { visible: false },
          });
          macdInstance.current = macdChart;
          macdSeriesRef.current.hist = macdChart.addSeries(HistogramSeries, { priceFormat: { type: "price", precision: 4 } });
          macdSeriesRef.current.dif = macdChart.addSeries(LineSeries, { color: "#22D3EE", lineWidth: 1 });
          macdSeriesRef.current.dea = macdChart.addSeries(LineSeries, { color: "#FBBF24", lineWidth: 1 });
          chart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
            if (!range || !macdInstance.current) return;
            try { macdInstance.current.timeScale().setVisibleLogicalRange(range); } catch {}
          });
        }

        paintAllSeries(data);
        void closes; void times;

        chart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
          if (!range) return;
          if (range.from < 5) {
            void loadHistoryBefore();
          }
        });

        chart.timeScale().fitContent();
        // 布局稳定后再强制一次尺寸，避免 Tab 切换后 0 宽
        requestAnimationFrame(() => {
          if (cancelled || !chartRef.current) return;
          const w = Math.max(chartRef.current.clientWidth, chartW);
          const h = Math.max(chartRef.current.clientHeight, chartH);
          resizeChart(w, h);
          try { chart.timeScale().fitContent(); } catch {}
        });
        setLoading(false);
      } catch (e: any) {
        if (!cancelled) { setError(e.message || "加载失败"); setLoading(false); }
      }
    }

    void loadChart();
    return () => {
      cancelled = true;
      if (chartInstance.current) { try { chartInstance.current.remove(); } catch {} chartInstance.current = null; }
      if (rsiInstance.current) { try { rsiInstance.current.remove(); } catch {} rsiInstance.current = null; }
      if (macdInstance.current) { try { macdInstance.current.remove(); } catch {} macdInstance.current = null; }
      candleSeriesRef.current = null;
      volSeriesRef.current = null;
      maSeriesRef.current = {};
      emaSeriesRef.current = {};
      bollSeriesRef.current = {};
      rsiSeriesRef.current = null;
      macdSeriesRef.current = {};
      refreshIndicatorsRef.current = null;
      applyBarsRef.current = null;
      klinesDataRef.current = [];
    };
  }, [symbol, period, exchange, showMA, showEMA, showBOLL, showRSI, showMACD, paintAllSeries, loadHistoryBefore, embedded]);

  // 实时 K 线 + 最新价
  useEffect(() => {
    symbolRef.current = symbol;
    periodRef.current = period;
    exchangeRef.current = exchange;
    const ws = getWs();
    const unsub = ws.subscribe((msg: any) => {
      if (!msg) return;
      // ticker / price_update → 头部实时价
      if (msg.type === "ticker" || msg.type === "price_update") {
        const body = msg.data && typeof msg.data === "object" ? msg.data : msg;
        const sym = String(body.symbol || "").toUpperCase();
        const px = Number(body.price ?? body.last ?? body.close);
        if (sym === symbolRef.current && Number.isFinite(px) && px > 0) {
          setLivePrice(px);
        }
      }
      if (msg.type !== "kline_update") return;
      const body = msg.data && typeof msg.data === "object" ? msg.data : msg;
      if (body.status) return;
      if (body.symbol !== symbolRef.current || body.period !== periodRef.current || !body.bar) return;
      // 若推送带交易所且与当前不一致则忽略
      const ex = String(body.exchange || body.market || "").toLowerCase();
      if (ex && ex !== exchangeRef.current && ex !== "aster" && !(exchangeRef.current === "asterdex" && ex === "aster")) {
        // 无交易所字段时仍接受（兼容旧推送）
        if (body.exchange || body.market) return;
      }
      const bar = body.bar;
      const t = toTime(bar);
      const rows = klinesDataRef.current;
      if (!rows?.length || !candleSeriesRef.current) return;
      const lastT = toTime(rows[rows.length - 1]);
      if (t === lastT) {
        rows[rows.length - 1] = bar;
      } else if (t > lastT) {
        rows.push(bar);
        if (rows.length > MAX_BARS) rows.shift();
      } else {
        return;
      }
      if (bar.close) setLivePrice(Number(bar.close));
      candleSeriesRef.current.update({
        time: t, open: bar.open, high: bar.high, low: bar.low, close: bar.close,
      });
      volSeriesRef.current?.update({
        time: t, value: bar.volume || 0,
        color: bar.close >= bar.open ? "#34D39933" : "#FB718533",
      });
      // 指标轻量刷新（全量重算，数据量可控）
      paintAllSeries(rows);
    });
    ws.send({ type: "subscribe_klines", symbol, period, exchange });
    return () => {
      unsub();
      ws.send({ type: "unsubscribe_klines" });
    };
  }, [symbol, period, exchange, paintAllSeries]);

  // Tab 从隐藏切回可见时，强制按真实容器尺寸重绘（否则可能停在 0x0）
  useEffect(() => {
    if (!visible) return;
    const run = () => {
      const el = chartRef.current;
      if (!el || !chartInstance.current) return;
      const w = el.clientWidth;
      const h = el.clientHeight || (embedded ? EMBED_H : 420);
      if (w < 10) return;
      try {
        chartInstance.current.applyOptions({ width: w, height: h });
        if (rsiInstance.current && rsiRef.current) {
          rsiInstance.current.applyOptions({ width: rsiRef.current.clientWidth || w });
        }
        if (macdInstance.current && macdRef.current) {
          macdInstance.current.applyOptions({ width: macdRef.current.clientWidth || w });
        }
        chartInstance.current.timeScale().fitContent();
      } catch { /* ignore */ }
    };
    const t0 = requestAnimationFrame(run);
    const t1 = window.setTimeout(run, 50);
    const t2 = window.setTimeout(run, 200);
    return () => {
      cancelAnimationFrame(t0);
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [visible, symbol, exchange, period, embedded]);

  // 头部价格：概览 5s 轮询 + 最新 K 线；额外 2s 拉一次概览保证切换交易对后快更新
  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      try {
        const ov = await api.getMarketOverviewAll(exchangeRef.current);
        if (stopped) return;
        const row = (ov?.rows || []).find((r: any) => r.symbol === symbolRef.current);
        if (row?.price) setLivePrice(Number(row.price));
      } catch { /* ignore */ }
    };
    const id = setInterval(tick, 2000);
    void tick();
    return () => { stopped = true; clearInterval(id); };
  }, [symbol, exchange]);

  // 容器尺寸变化时重绘宽度（含侧栏折叠）；忽略 0 尺寸，避免 Tab 隐藏时把图画没
  useEffect(() => {
    const el = chartRef.current;
    const apply = () => {
      if (!chartInstance.current || !chartRef.current) return;
      const w = chartRef.current.clientWidth;
      const h = chartRef.current.clientHeight;
      if (w < 40 || h < 40) return;
      chartInstance.current.applyOptions({ width: w, height: h });
      if (rsiInstance.current && rsiRef.current)
        rsiInstance.current.applyOptions({ width: rsiRef.current.clientWidth || w });
      if (macdInstance.current && macdRef.current)
        macdInstance.current.applyOptions({ width: macdRef.current.clientWidth || w });
    };
    if (!el || typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", apply);
      return () => window.removeEventListener("resize", apply);
    }
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const subH = (showRSI ? 118 : 0) + (showMACD ? 128 : 0);

  return (
    <div className={cn("space-y-3 flex flex-col", embedded ? "min-h-[70vh]" : "p-4 h-full")}>
      <div className="flex items-center gap-3 flex-shrink-0 flex-wrap">
        <span className="text-lg font-bold">{symbol}</span>
        <span className="text-lg font-semibold tabular-nums">{fmtNum(displayPrice)}</span>
        <span className={cn("text-sm font-medium tabular-nums", (symbolMeta?.change_pct ?? 0) >= 0 ? "text-profit" : "text-loss")}>
          {(symbolMeta?.change_pct ?? 0) >= 0 ? "+" : ""}{(symbolMeta?.change_pct ?? 0).toFixed(2)}%
        </span>
        <span className="text-xs text-muted-foreground">
          24h 高 {fmtNum(symbolMeta?.high_24h)} / 低 {fmtNum(symbolMeta?.low_24h)} / 成交额 {fmtNum(symbolMeta?.quote_volume_24h)}
        </span>
        {loadingMore && <Badge variant="outline" className="text-[10px]">补历史中…</Badge>}
        <Badge variant="secondary" className="text-[10px] ml-auto">数据中心 · {exchange}</Badge>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-2 flex-shrink-0">
        <div className="flex items-center gap-2 flex-wrap">
          {!embedded && (
            <h1 className="text-lg font-bold flex items-center gap-2"><LineChart className="w-5 h-5 text-primary" />K 线图表</h1>
          )}
          <div className="flex gap-0.5 flex-wrap">
            {EXCHANGES.map((ex) => (
              <button key={ex.id} onClick={() => setExchange(ex.id)}
                className={cn("px-2 py-1 text-xs rounded transition-colors",
                  exchange === ex.id ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:text-foreground")}>
                {ex.label}
              </button>
            ))}
          </div>
          <div className="flex gap-0.5 flex-wrap border-l border-border pl-2">
            {SYMBOLS.map((s) => (
              <button key={s} onClick={() => setSymbol(s)}
                className={cn("px-2 py-1 text-xs rounded transition-colors",
                  symbol === s ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:text-foreground")}>{s}</button>
            ))}
            <div className="relative">
              <input
                type="text"
                value={symbolQuery || symbol}
                onChange={(e) => { setSymbolQuery(e.target.value.toUpperCase().trim()); setSearchOpen(true); }}
                onFocus={() => setSearchOpen(true)}
                onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && symbolMatches.length > 0) {
                    setSymbol(symbolMatches[0].symbol);
                    setSymbolQuery("");
                    setSearchOpen(false);
                  }
                }}
                placeholder="搜索交易对"
                className="w-28 px-2 py-1 text-xs bg-card border border-border rounded"
              />
              {searchOpen && symbolMatches.length > 0 && (
                <div className="absolute z-30 top-full left-0 mt-1 w-44 max-h-64 overflow-auto bg-card border border-border rounded shadow-lg">
                  {symbolMatches.map((m: any) => (
                    <button
                      key={m.symbol}
                      className={cn("w-full text-left px-2 py-1.5 text-xs hover:bg-muted/60 flex items-center justify-between", m.symbol === symbol && "text-primary")}
                      onMouseDown={(e) => { e.preventDefault(); setSymbol(m.symbol); setSymbolQuery(""); setSearchOpen(false); }}
                    >
                      <span className="font-medium">{m.symbol}</span>
                      <span className="text-muted-foreground tabular-nums">{fmtNum(m.price)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex gap-0.5 flex-wrap">
            {PERIODS.map((p) => (
              <button key={p.value} onClick={() => setPeriod(p.value)}
                className={cn("px-2 py-1 text-xs rounded transition-colors",
                  period === p.value ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:text-foreground")}>{p.label}</button>
            ))}
          </div>
          <div className="flex gap-1 ml-2 border-l border-border pl-2 flex-wrap">
            {[
              { key: "MA", val: showMA, set: setShowMA, color: "#22D3EE" },
              { key: "EMA", val: showEMA, set: setShowEMA, color: "#34D399" },
              { key: "BOLL", val: showBOLL, set: setShowBOLL, color: "#FBBF24" },
              { key: "RSI", val: showRSI, set: setShowRSI, color: "#8B5CF6" },
              { key: "MACD", val: showMACD, set: setShowMACD, color: "#FB7185" },
            ].map(({ key, val, set, color }) => (
              <button key={key} onClick={() => set(!val)}
                className={cn("px-2 py-1 text-xs rounded transition-colors border",
                  val ? "border-transparent font-medium" : "border-border text-muted-foreground")}
                style={val ? { backgroundColor: `${color}20`, color } : {}}>
                {key}
              </button>
            ))}
          </div>
        </div>
      </div>

      <Card className={cn("relative overflow-hidden", embedded ? "min-h-[520px]" : "flex-1 min-h-0 p-2")}>
        <div className="p-2">
          <div
            ref={chartRef}
            className="w-full"
            style={{
              height: embedded
                ? (subH ? Math.max(360, EMBED_H - subH) : EMBED_H)
                : (subH ? `calc(100% - ${subH}px)` : "100%"),
              minHeight: embedded ? 360 : 280,
            }}
          />
          {showRSI && <div ref={rsiRef} className="w-full mt-1" style={{ height: 110 }} />}
          {showMACD && <div ref={macdRef} className="w-full mt-1" style={{ height: 120 }} />}
        </div>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-background/50 z-10">
            <RefreshCw className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-muted-foreground text-sm z-10 bg-background/40">
            {error}
          </div>
        )}
        <p className="absolute bottom-2 left-3 text-[10px] text-muted-foreground pointer-events-none z-0">
          向左拖动可自动补历史 K 线 · 价格随 WS/最新 K 线实时更新
        </p>
      </Card>
    </div>
  );
}
