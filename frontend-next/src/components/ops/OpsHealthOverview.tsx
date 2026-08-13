/**
 * OpsHealthOverview — 健康总览条（R6-2）
 * 消费 GET /api/system/health-overview：DB 连通/延迟、P0/P1、学习闭环、
 * 资金费采集器、uptime、磁盘。只展示真实数据，缺失即如实显示「—」。
 */

import { Database, AlertTriangle, Workflow, Radio, Clock, HardDrive } from "lucide-react";
import { cn } from "@/lib/utils";

export interface HealthOverviewData {
  checked_at?: string;
  uptime_sec?: number;
  db?: { ok?: boolean; latency_ms?: number | null; error?: string };
  errors?: { ok?: boolean; counts?: { P0?: number; P1?: number } };
  learning_loops?: { overall?: string; items?: { name?: string; status?: string }[] };
  funding_collector?: { has_report?: boolean; venue_report?: Record<string, unknown>; error?: string };
  resources?: { disk_free_mb?: number; disk_total_mb?: number; mem_rss_mb?: number };
  ok?: boolean;
  error?: string;
}

function fmtUptime(sec?: number): string {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  return `${m}m`;
}

function Item({
  icon: Icon,
  label,
  value,
  tone,
  title,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  tone: "ok" | "warn" | "bad" | "muted";
  title?: string;
}) {
  const tones = {
    ok: "text-profit",
    warn: "text-warning",
    bad: "text-loss",
    muted: "text-muted-foreground",
  } as const;
  return (
    <div
      title={title}
      className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-card border border-border min-w-0"
    >
      <Icon className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
      <div className="flex flex-col min-w-0 leading-tight">
        <span className="text-[9px] uppercase tracking-wider text-muted-foreground truncate">{label}</span>
        <span className={cn("text-xs font-mono tabular-nums truncate", tones[tone])}>{value}</span>
      </div>
    </div>
  );
}

export function OpsHealthOverview({ data }: { data: HealthOverviewData | null | undefined }) {
  if (!data) return null;
  if (data.ok === false) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-loss/30 bg-loss/10 text-loss text-xs">
        <AlertTriangle className="w-3.5 h-3.5" /> 健康总览不可用：{data.error ?? "未知错误"}
      </div>
    );
  }

  const db = data.db ?? {};
  const counts = data.errors?.counts ?? {};
  const loops = data.learning_loops ?? {};
  const loopsDead = Array.isArray(loops.items)
    ? loops.items.filter((i) => i.status === "dead").length
    : 0;
  const venueCount = Object.keys(data.funding_collector?.venue_report ?? {}).length;
  const disk = data.resources ?? {};

  return (
    <div className="flex flex-wrap gap-2">
      <Item
        icon={Database}
        label="DB"
        value={db.ok ? `${db.latency_ms ?? "—"}ms` : "DOWN"}
        tone={db.ok ? "ok" : "bad"}
        title={db.error ? `DB 错误: ${db.error}` : "数据库连通性"}
      />
      <Item
        icon={AlertTriangle}
        label="P0/P1"
        value={`${Number(counts.P0 ?? 0)} / ${Number(counts.P1 ?? 0)}`}
        tone={Number(counts.P0 ?? 0) > 0 ? "bad" : Number(counts.P1 ?? 0) > 0 ? "warn" : "ok"}
      />
      <Item
        icon={Workflow}
        label="学习闭环"
        value={loopsDead > 0 ? `${loopsDead} 断` : (loops.overall ?? "—")}
        tone={loopsDead > 0 ? "bad" : loops.overall === "ok" ? "ok" : loops.overall === "warn" ? "warn" : "muted"}
      />
      <Item
        icon={Radio}
        label="资金费采集"
        value={data.funding_collector?.has_report ? `${venueCount} 场所` : "无快照"}
        tone={data.funding_collector?.has_report ? (venueCount > 0 ? "ok" : "warn") : "muted"}
      />
      <Item
        icon={Clock}
        label="Uptime"
        value={fmtUptime(data.uptime_sec)}
        tone="muted"
      />
      <Item
        icon={HardDrive}
        label="磁盘余量"
        value={disk.disk_free_mb != null ? `${(disk.disk_free_mb / 1024).toFixed(1)}GB` : "—"}
        tone={disk.disk_free_mb != null && disk.disk_free_mb < 10_000 ? "warn" : "muted"}
      />
    </div>
  );
}
