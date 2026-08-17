"use client";

/** 全市场数据中台 · 数据中心体检（真实数据：现有/缺失/运行中/入库及时性） */
import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type DCPeriod = {
  exchange: string; period: string; symbols: number; bars: number; days: number;
  oldest_ts: number | null; newest_ts: number | null; stale_sec: number | null; fresh: boolean;
};
type DepthRow = {
  symbol: string; period: string; bars: number; days: number;
  target_bars: number | null; target_days: number; missing_bars: number | null;
  missing_days: number | null; stale_sec: number | null; fresh: boolean;
};
type CollectorRow = {
  exchange: string; period: string; pool: string;
  symbols_ok: number; symbols_fail: number; updated_at: string;
};
type Overview = {
  generated_at: number; active_exchange: string;
  depth_targets: Record<string, number>;
  aggregate_warming?: boolean; aggregate_error?: string | null;
  periods: DCPeriod[]; universe_depth: DepthRow[]; collectors: CollectorRow[];
  backfill: { enabled: boolean; mode: string; symbol_limit: number; cold_enabled: boolean; cold_limit: number; round_max_sec: number; idle_sec: number };
  data_center: { ok?: boolean; uptime_sec?: number; components?: Record<string, string>; error?: string };
  elapsed_ms: number;
};

function fmtStale(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function fmtBars(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function DataCenterOverviewPanel() {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/ops/data-center-overview", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = (await res.json()) as Overview;
        if (!cancelled) { setData(json); setErr(null); }
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    const id = setInterval(() => void load(), 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (loading && !data) {
    return <div className="text-sm text-muted-foreground p-4">加载数据中心体检…（全表聚合首次较慢，请稍候）</div>;
  }
  if (err && !data) {
    return <div className="text-sm text-loss p-4">加载失败: {err}</div>;
  }
  if (!data) return null;

  const periods = data.periods;
  const exchanges = [...new Set(periods.map((p) => p.exchange))].sort();
  const periodOrder = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"];
  const freshCount = periods.filter((p) => p.fresh).length;
  const staleCount = periods.filter((p) => p.stale_sec != null && !p.fresh).length;
  const totalMissingBars = data.universe_depth.reduce((a, r) => a + (r.missing_bars ?? 0), 0);

  return (
    <div className="space-y-3">
      {/* 状态总览卡 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground">数据中心进程</div>
          <div className={cn("text-sm font-semibold", data.data_center.ok ? "text-profit" : "text-loss")}>
            {data.data_center.ok ? `在线 (${Math.round((data.data_center.uptime_sec ?? 0) / 60)}min)` : `离线 ${data.data_center.error ?? ""}`}
          </div>
          <div className="text-[10px] text-muted-foreground mt-1">
            {(Object.entries(data.data_center.components ?? {})).slice(0, 6).map(([k, v]) => (
              <span key={k} className={cn("mr-1.5", String(v).startsWith("up") ? "text-profit" : "text-loss")}>{k}:{String(v).split(":")[0]}</span>
            ))}
          </div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground">分所×周期新鲜度</div>
          <div className="text-sm font-semibold">
            <span className="text-profit">{freshCount} 新鲜</span>
            <span className="text-muted-foreground"> / </span>
            <span className={staleCount > 0 ? "text-warning" : "text-profit"}>{staleCount} 过期</span>
          </div>
          <div className="text-[10px] text-muted-foreground mt-1">口径: 最新bar距今 ≤ 周期×2+60s</div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground">核心币缺口（vs 深度目标）</div>
          <div className={cn("text-sm font-semibold", totalMissingBars === 0 ? "text-profit" : "text-warning")}>
            {fmtBars(totalMissingBars)} 根
          </div>
          <div className="text-[10px] text-muted-foreground mt-1">18 币 × 10 周期合计</div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground">回填模式（分时段批式）</div>
          <div className="text-sm font-semibold">
            {data.backfill.mode} · 每轮 {Math.round(data.backfill.round_max_sec / 60)}min / 休息 {Math.round(data.backfill.idle_sec / 60)}min
          </div>
          <div className="text-[10px] text-muted-foreground mt-1">
            上限 {data.backfill.symbol_limit} 币 · 冷所 {data.backfill.cold_enabled ? data.backfill.cold_limit : "关"}
          </div>
        </Card>
      </div>

      {/* 分所 × 周期 现有数据矩阵 */}
      <Card className="p-3">
        <div className="text-xs font-semibold mb-2">
          现有数据（分所 × 周期，真实库内统计）
          {data.aggregate_warming && (
            <span className="ml-2 text-[10px] text-warning font-normal">
              全表统计预热中（首次约 2~5 分钟，每 10 分钟刷新）——其余板块已是实时数据
            </span>
          )}
          {data.aggregate_error && (
            <span className="ml-2 text-[10px] text-loss font-normal">统计失败: {data.aggregate_error}</span>
          )}
        </div>
        {data.periods.length === 0 ? (
          <div className="text-[11px] text-muted-foreground py-2">
            {data.aggregate_warming ? "正在扫描全表（约 2~5 分钟）…" : "暂无聚合数据"}
          </div>
        ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-left py-1 pr-2">周期</th>
                {exchanges.map((ex) => (
                  <th key={ex} className="text-right py-1 px-2 font-normal">{ex}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {periodOrder.map((p) => (
                <tr key={p} className="border-t border-border/40">
                  <td className="py-1 pr-2 font-mono">{p}</td>
                  {exchanges.map((ex) => {
                    const r = periods.find((x) => x.exchange === ex && x.period === p);
                    if (!r) return <td key={ex} className="text-right py-1 px-2 text-muted-foreground">—</td>;
                    return (
                      <td key={ex} className="text-right py-1 px-2 font-mono">
                        <span className={r.fresh ? "text-profit" : "text-warning"}>
                          {fmtBars(r.bars)}
                        </span>
                        <span className="text-muted-foreground"> /{r.symbols}币</span>
                        <span className="block text-[9px] text-muted-foreground">距{fmtStale(r.stale_sec)}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </Card>

      {/* 核心币深度 vs 目标（缺失） */}
      <Card className="p-3">
        <div className="text-xs font-semibold mb-2">
          核心币深度 vs 回填目标（缺失量）
          <span className="text-[10px] text-muted-foreground ml-2">目标: {Object.entries(data.depth_targets).map(([k, v]) => `${k}=${v}d`).join(" · ")}</span>
        </div>
        <div className="overflow-x-auto max-h-72 overflow-y-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-muted-foreground sticky top-0 bg-card">
                <th className="text-left py-1 pr-2">币种</th>
                {periodOrder.map((p) => <th key={p} className="text-right py-1 px-1.5 font-normal">{p}</th>)}
              </tr>
            </thead>
            <tbody>
              {[...new Set(data.universe_depth.map((r) => r.symbol))].map((sym) => (
                <tr key={sym} className="border-t border-border/40">
                  <td className="py-1 pr-2 font-mono">{sym}</td>
                  {periodOrder.map((p) => {
                    const r = data.universe_depth.find((x) => x.symbol === sym && x.period === p);
                    if (!r) return <td key={p} className="text-right py-1 px-1.5 text-muted-foreground">—</td>;
                    if (r.missing_bars === 0) {
                      return <td key={p} className={cn("text-right py-1 px-1.5 text-profit")}>✓</td>;
                    }
                    return (
                      <td key={p} className={cn("text-right py-1 px-1.5 font-mono", r.missing_bars != null && r.missing_bars > 0 ? "text-warning" : "text-muted-foreground")}>
                        {r.missing_days != null ? `缺${r.missing_days}d` : r.fresh ? "✓" : "缺"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 采集器运行状态（心跳） */}
      <Card className="p-3">
        <div className="text-xs font-semibold mb-2">正在跑的采集器（kline_sync_heartbeat 真实心跳）</div>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-left py-1 pr-2">交易所</th>
                <th className="text-left py-1 px-2">池</th>
                <th className="text-left py-1 px-2">周期</th>
                <th className="text-right py-1 px-2">ok</th>
                <th className="text-right py-1 px-2">fail</th>
                <th className="text-right py-1">最后心跳</th>
              </tr>
            </thead>
            <tbody>
              {data.collectors.slice(0, 40).map((c, i) => (
                <tr key={i} className="border-t border-border/40">
                  <td className="py-1 pr-2 font-mono">{c.exchange}</td>
                  <td className="py-1 px-2 font-mono">{c.pool}</td>
                  <td className="py-1 px-2 font-mono">{c.period}</td>
                  <td className="text-right py-1 px-2 text-profit">{c.symbols_ok}</td>
                  <td className={cn("text-right py-1 px-2", c.symbols_fail > 0 ? "text-loss" : "text-muted-foreground")}>{c.symbols_fail}</td>
                  <td className="text-right py-1 font-mono text-muted-foreground">{c.updated_at?.slice(11) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="text-[10px] text-muted-foreground">
        生成于 {new Date(data.generated_at * 1000).toLocaleTimeString()} · 聚合缓存 5 分钟 · 接口耗时 {data.elapsed_ms}ms
      </div>
    </div>
  );
}
