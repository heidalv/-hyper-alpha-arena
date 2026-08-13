"use client";

import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

export function OpsHeader({
  ago,
  alertCount,
  loading,
  clock,
  onRefresh,
}: {
  ago: number;
  alertCount: number;
  loading: boolean;
  clock: string;
  onRefresh: () => void;
}) {
  return (
    <div className="ops-header">
      <div className="ops-header-brand">
        <span className="ops-sub">Control Room</span>
        <h1>因子运维台</h1>
      </div>
      <div className="ops-header-right">
        <div className="ops-pulse-live ops-mono">
          <span
            className={cn(
              "ops-pulse-dot",
              alertCount > 0 ? "is-alert" : "is-ok",
            )}
          />
          <span className="ops-muted">实时</span>
          <span>距上次刷新 {ago}s</span>
          {alertCount > 0 ? (
            <span className="ops-down">P0/P1 {alertCount}</span>
          ) : null}
        </div>
        <span className="ops-mono ops-muted">{clock}</span>
        <button
          type="button"
          className="ops-btn"
          onClick={onRefresh}
          disabled={loading}
        >
          <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
          刷新
        </button>
      </div>
    </div>
  );
}

export function OpsKpiStrip({
  items,
}: {
  items: {
    label: string;
    value: string | number;
    tone?: "ok" | "lag" | "down" | "info" | "";
    title?: string;
    dense?: boolean;
  }[];
}) {
  return (
    <div className="ops-kpi-strip">
      {items.map((it) => (
        <div key={it.label} className="ops-kpi" title={it.title || undefined}>
          <div className="ops-label">{it.label}</div>
          <div
            className={cn(
              "ops-kpi-value ops-mono",
              it.tone || "",
              it.dense && "ops-kpi-value--dense",
            )}
          >
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}
