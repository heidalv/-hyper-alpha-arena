"use client";

/**
 * DataQualityPanel — 数据三链路健康卡（雏形）
 *
 * v6 计划 9.3：行情 / K线 / 链上三链路缺口·延迟·异常状态 + 告警列表。
 *
 * 数据源：GET /api/monitor/data-quality
 *   source_health: { name: { total_calls, success_rate, avg_latency_ms,
 *                            last_success, last_failure, last_error, healthy } }
 *   recent_alerts: [{ level, source, symbol, message, timestamp, details }]
 *   kline_freshness: { running, last_check_at, summary:{alerts,critical,warning},
 *                      last_alerts, ... }
 *
 * 三链路归类（按 source 名）：
 *   行情 = ticker / price / market / quote
 *   K线  = kline + kline_freshness 巡检
 *   链上 = onchain / chain / whale / netflow / cvd
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, Activity, CandlestickChart, Waves, BellRing } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";

interface SourceHealth {
  total_calls: number;
  success_rate: number;
  avg_latency_ms: number;
  last_success: number;
  last_failure: number;
  last_error: string;
  healthy: boolean;
}

interface AlertItem {
  level: string;
  source: string;
  symbol: string;
  message: string;
  timestamp: number;
  details?: Record<string, unknown>;
}

interface FreshnessSummary {
  alerts: number;
  critical: number;
  warning: number;
}

interface KlineFreshness {
  running: boolean;
  last_check_at: string | null;
  interval_s: number;
  summary: FreshnessSummary;
  last_alerts: AlertItem[];
}

interface DataQualityResponse {
  source_health?: Record<string, SourceHealth>;
  recent_alerts?: AlertItem[];
  stale_threshold_sec?: number;
  kline_freshness?: KlineFreshness;
  error?: string;
}

type LinkStatus = "ok" | "warn" | "dead" | "n/a";

const STATUS_META: Record<LinkStatus, { label: string; color: string; bg: string }> = {
  ok: { label: "正常", color: "#22c55e", bg: "rgba(34,197,94,0.12)" },
  warn: { label: "告警", color: "#eab308", bg: "rgba(234,179,8,0.12)" },
  dead: { label: "断链", color: "#ef4444", bg: "rgba(239,68,68,0.12)" },
  "n/a": { label: "无数据", color: "#94a3b8", bg: "rgba(148,163,184,0.12)" },
};

function classifySource(name: string): "market" | "kline" | "onchain" | null {
  const n = name.toLowerCase();
  if (/(ticker|price|market|quote)/.test(n)) return "market";
  if (/kline/.test(n)) return "kline";
  if (/(onchain|chain|whale|netflow|cvd|funding)/.test(n)) return "onchain";
  return null;
}

function ageMin(ts: number | undefined): string {
  if (!ts) return "—";
  const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

function linkStatus(sources: NamedSource[]): LinkStatus {
  if (sources.length === 0) return "n/a";
  const anyDead = sources.some((s) => !s.health.healthy || s.health.total_calls === 0);
  const anyWarn = sources.some((s) => s.health.success_rate < 0.9);
  if (anyDead) return "dead";
  if (anyWarn) return "warn";
  return "ok";
}

interface NamedSource {
  name: string;
  health: SourceHealth;
}

function LinkCard({
  icon,
  title,
  status,
  extra,
  sources,
}: {
  icon: React.ReactNode;
  title: string;
  status: LinkStatus;
  extra?: string;
  sources: NamedSource[];
}) {
  const meta = STATUS_META[status];
  return (
    <Card className="p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-xs font-medium">
          {icon}
          {title}
        </div>
        <Badge variant="secondary" className="font-medium" style={{ color: meta.color, backgroundColor: meta.bg }}>
          {meta.label}
        </Badge>
      </div>
      {extra && <div className="text-[10px] text-muted-foreground mb-1">{extra}</div>}
      {sources.length === 0 ? (
        <div className="text-[10px] text-muted-foreground">该链路暂无采集记录（记录器未接入或从未调用）</div>
      ) : (
        <div className="space-y-1.5">
          {sources.slice(0, 4).map((s) => (
            <div key={s.name} className="flex items-center justify-between text-[10px]">
              <span className="font-mono text-muted-foreground truncate max-w-[110px]" title={s.name}>
                {s.name}
              </span>
              <span className="flex items-center gap-2 tabular-nums">
                <span className="text-muted-foreground">延迟 {s.health.avg_latency_ms || 0}ms</span>
                <span className="text-muted-foreground">成功 {Math.round(s.health.success_rate * 100)}%</span>
                <span className={cn(s.health.healthy ? "text-profit" : "text-loss")}>
                  {s.health.healthy ? "正常" : "异常"}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function DataQualityPanel() {
  const [data, setData] = useState<DataQualityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${getBackendUrl()}/api/monitor/data-quality`);      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as DataQualityResponse;
      if (json.error) throw new Error(json.error);
      setData(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const { market, kline, onchain } = useMemo(() => {
    const sh = data?.source_health ?? {};
    const entries = Object.entries(sh);
    return {
      market: entries.filter(([n]) => classifySource(n) === "market").map(([n, v]) => ({ name: n, health: v })),
      kline: entries.filter(([n]) => classifySource(n) === "kline").map(([n, v]) => ({ name: n, health: v })),
      onchain: entries.filter(([n]) => classifySource(n) === "onchain").map(([n, v]) => ({ name: n, health: v })),
    };
  }, [data]);

  const kf = data?.kline_freshness;
  const klineStatus: LinkStatus =
    kline.length > 0 || kf
      ? kf && kf.summary && kf.summary.critical > 0
        ? "dead"
        : linkStatus(kline) === "n/a"
          ? "ok"
          : linkStatus(kline) === "dead"
            ? "dead"
            : kf && kf.summary && kf.summary.warning > 0
              ? "warn"
              : linkStatus(kline)
      : "n/a";

  const alerts = data?.recent_alerts ?? [];
  const staleSec = data?.stale_threshold_sec ?? 300;

  return (
    <div className="space-y-4">
      {/* 三链路健康卡 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <LinkCard
          icon={<Activity className="w-3.5 h-3.5 text-primary" />}
          title="行情链路"
          status={linkStatus(market)}
          extra={`ticker/行情源 · 过期阈值 ${staleSec}s · 缺口 = last_success 距今`}
          sources={market}
        />
        <LinkCard
          icon={<CandlestickChart className="w-3.5 h-3.5 text-primary" />}
          title="K线链路"
          status={klineStatus}
          extra={
            kf
              ? `巡检 ${kf.running ? "运行中" : "停止"} · 最近巡检 ${kf.last_check_at ? ageMin(new Date(kf.last_check_at).getTime() / 1000) : "—"} · 巡检告警 ${kf.summary?.alerts ?? 0} 条`
              : "K线新鲜度巡检未返回"
          }
          sources={kline}
        />
        <LinkCard
          icon={<Waves className="w-3.5 h-3.5 text-primary" />}
          title="链上链路"
          status={linkStatus(onchain)}
          extra="onchain/whale/netflow 等链上数据源"
          sources={onchain}
        />
      </div>

      {/* 告警列表 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border/50 bg-muted/30 text-xs font-medium flex items-center gap-1.5">
          <BellRing className="w-3.5 h-3.5 text-primary" />
          近期告警（{alerts.length} 条）
        </div>
        {loading ? (
          <div className="p-6 text-center text-xs text-muted-foreground">加载中...</div>
        ) : error ? (
          <div className="p-3 text-xs text-loss bg-loss/5 border-loss/30">加载失败：{error}</div>
        ) : alerts.length === 0 ? (
          <div className="p-6 text-center text-xs text-muted-foreground">暂无告警</div>
        ) : (
          <div className="divide-y max-h-72 overflow-y-auto">
            {alerts.slice(0, 20).map((a, i) => {
              const isCritical = a.level === "critical";
              return (
                <div key={i} className="flex items-start gap-2 px-4 py-2">
                  <span
                    className={cn(
                      "mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0",
                      isCritical ? "bg-loss" : "bg-warning"
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs">
                      <span className={cn("font-medium", isCritical ? "text-loss" : "text-warning")}>
                        [{a.level}]
                      </span>{" "}
                      <span className="font-mono text-[10px] text-muted-foreground">{a.source}</span>
                      {a.symbol && (
                        <span className="font-mono text-[10px] text-muted-foreground"> {a.symbol}</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground truncate" title={a.message}>
                      {a.message}
                    </div>
                  </div>
                  <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0">
                    {ageMin(a.timestamp)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={load} disabled={loading} className="h-6 text-xs">
          <RefreshCw className={cn("w-3 h-3 mr-1", loading && "animate-spin")} />
          {loading ? "刷新中..." : "刷新"}
        </Button>
      </div>
    </div>
  );
}
