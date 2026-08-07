"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { paperApi } from "@/lib/api";
import { Loader2 } from "lucide-react";

type Period = "7d" | "30d" | "all";
type Point = { time: number; value: number };

function toSeries(points: any[]): Point[] {
  const series = (points || [])
    .map((p) => ({
      time: Math.floor(Number(p.time)),
      value: Number(p.value),
    }))
    .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.value))
    .sort((a, b) => a.time - b.time);

  const seen = new Set<number>();
  return series.filter((d) => {
    if (seen.has(d.time)) return false;
    seen.add(d.time);
    return true;
  });
}

function nextWiderPeriod(p: Period): Period | null {
  if (p === "7d") return "30d";
  if (p === "30d") return "all";
  return null;
}

function paintSeries(series: any, points: Point[]) {
  if (!series || points.length < 2) return;
  const first = points[0].value;
  const last = points[points.length - 1].value;
  const up = last >= first;
  series.applyOptions({
    lineColor: up ? "#22c55e" : "#ef4444",
    topColor: up ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)",
    bottomColor: up ? "rgba(34,197,94,0.02)" : "rgba(239,68,68,0.02)",
  });
  series.setData(points.map((d) => ({ time: d.time as any, value: d.value })));
}

export function EquityCurve({
  accountId,
  period = "7d",
  height = 256,
}: {
  accountId?: number | null;
  period?: Period;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const dataRef = useRef<Point[]>([]);
  const loadingMoreRef = useRef(false);
  /** 实际已加载的最长周期（可被拖动自动扩大） */
  const loadedPeriodRef = useRef<Period>(period);
  const accountRef = useRef(accountId);
  const [expandHint, setExpandHint] = useState<string | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    accountRef.current = accountId;
  }, [accountId]);

  // 用户点选周期：重置已加载范围
  useEffect(() => {
    loadedPeriodRef.current = period;
  }, [period]);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["paper-equity-curve", accountId, period],
    queryFn: () => paperApi.getEquityCurve(accountId!, period),
    enabled: !!accountId,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const loadWiderHistory = async () => {
    if (loadingMoreRef.current || !accountRef.current) return;
    const wider = nextWiderPeriod(loadedPeriodRef.current);
    if (!wider) return;

    loadingMoreRef.current = true;
    setExpandHint(`补${wider === "all" ? "全部" : wider}历史…`);
    try {
      const more = await paperApi.getEquityCurve(accountRef.current, wider);
      const merged = toSeries([...(dataRef.current || []), ...(more?.points || [])]);
      if (merged.length < 2 || !seriesRef.current || !chartRef.current) return;

      const oldRange = chartRef.current.timeScale().getVisibleLogicalRange?.();
      const oldLen = dataRef.current.length;

      dataRef.current = merged;
      loadedPeriodRef.current = wider;
      paintSeries(seriesRef.current, merged);

      if (oldRange) {
        const added = Math.max(0, merged.length - oldLen);
        try {
          chartRef.current.timeScale().setVisibleLogicalRange({
            from: oldRange.from + added,
            to: oldRange.to + added,
          });
        } catch {
          /* ignore */
        }
      }
      qc.setQueryData(["paper-equity-curve", accountRef.current, wider], more);
    } catch (e) {
      console.warn("[EquityCurve] load wider history failed", e);
    } finally {
      loadingMoreRef.current = false;
      setExpandHint(null);
    }
  };

  // 建图 / 用户切换周期或账户时重建
  useEffect(() => {
    if (!accountId || !containerRef.current) return;

    let cancelled = false;
    loadingMoreRef.current = false;

    // 销毁旧图
    if (chartRef.current) {
      try {
        chartRef.current.remove();
      } catch {
        /* ignore */
      }
      chartRef.current = null;
      seriesRef.current = null;
      dataRef.current = [];
    }

    (async () => {
      // 等本轮 query 数据；若缓存已有可直接用
      let points = toSeries(data?.points || []);
      if (points.length < 2) {
        try {
          const fresh = await paperApi.getEquityCurve(accountId, period);
          points = toSeries(fresh?.points || []);
        } catch {
          return;
        }
      }
      if (cancelled || !containerRef.current || points.length < 2) return;

      const { createChart, AreaSeries } = await import("lightweight-charts");
      if (cancelled || !containerRef.current) return;

      containerRef.current.innerHTML = "";
      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height,
        layout: {
          background: { color: "transparent" },
          textColor: "#6B7785",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "rgba(30, 37, 48, 0.3)" },
          horzLines: { color: "rgba(30, 37, 48, 0.3)" },
        },
        rightPriceScale: { borderColor: "#1E2530" },
        timeScale: {
          borderColor: "#1E2530",
          timeVisible: true,
          secondsVisible: false,
          // 关键：禁止拖出数据边界，避免左边/右边空白
          fixLeftEdge: true,
          fixRightEdge: true,
          rightOffset: 4,
          minBarSpacing: 0.05,
          lockVisibleTimeRangeOnResize: true,
        },
        handleScroll: {
          mouseWheel: true,
          pressedMouseMove: true,
          horzTouchDrag: true,
          vertTouchDrag: false,
        },
        handleScale: {
          axisPressedMouseMove: true,
          mouseWheel: true,
          pinch: true,
        },
        crosshair: { mode: 1 },
      });
      chartRef.current = chart;
      dataRef.current = points;
      loadedPeriodRef.current = period;

      const areaSeries = chart.addSeries(AreaSeries, {
        lineWidth: 2,
        priceLineVisible: false,
      });
      seriesRef.current = areaSeries;
      paintSeries(areaSeries, points);
      chart.timeScale().fitContent();

      chart.timeScale().subscribeVisibleLogicalRangeChange((range: any) => {
        if (!range) return;
        // 放大后顶到左边界 → 自动扩周期补历史（7d→30d→all）
        if (range.from <= 1.5) {
          void loadWiderHistory();
        }
      });
    })();

    return () => {
      cancelled = true;
    };
    // data 轮询不重建；仅账户/周期/高度变化重建
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, period, height]);

  // 同周期轮询：只刷新数据，不重建图
  useEffect(() => {
    const points = toSeries(data?.points || []);
    if (points.length < 2 || !seriesRef.current) return;
    // 若已自动扩到更长周期，不要用短周期轮询覆盖
    if (loadedPeriodRef.current !== period) return;
    dataRef.current = points;
    paintSeries(seriesRef.current, points);
  }, [data, period]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const apply = () => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({
          width: containerRef.current.clientWidth,
          height,
        });
      }
    };
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(apply);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [height]);

  useEffect(() => {
    return () => {
      if (chartRef.current) {
        try {
          chartRef.current.remove();
        } catch {
          /* ignore */
        }
        chartRef.current = null;
        seriesRef.current = null;
      }
    };
  }, []);

  const empty = !isLoading && (!data?.points || data.points.length < 2);

  return (
    <div className="relative" style={{ height }}>
      <div ref={containerRef} className="w-full h-full" />
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
        </div>
      )}
      {(isError || empty) && !isLoading && (
        <div className="absolute inset-0 flex items-center justify-center text-muted-foreground text-sm">
          暂无权益走势数据
        </div>
      )}
      {expandHint && (
        <div className="absolute top-1 right-2 text-[10px] text-muted-foreground bg-background/70 px-1.5 py-0.5 rounded">
          {expandHint}
        </div>
      )}
    </div>
  );
}
