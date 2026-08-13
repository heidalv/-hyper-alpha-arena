"use client";

import { cn } from "@/lib/utils";

type Row = {
  factor_id?: string;
  state?: string;
  source?: string;
  icir?: number | null;
  online_weight?: number | null;
  router_reachable?: boolean;
};

export type PoolView = "tradable" | "research" | "quarantine";

const STATE_ZH: Record<string, string> = {
  ACTIVE: "活跃",
  PAPER: "纸面",
  SMALL_LIVE: "小仓实盘",
  ORTHO: "正交研究",
  QUARANTINE: "隔离",
};

export function OpsFactorPool({
  view,
  callout,
  items,
  counts,
  quarantineReasons,
  onViewChange,
}: {
  view: PoolView;
  callout?: string;
  items: Row[];
  counts?: { tradable?: number; research?: number; quarantine?: number; all?: number };
  quarantineReasons?: { reason: string; n: number }[];
  onViewChange: (v: PoolView) => void;
}) {
  const qn = counts?.quarantine ?? 0;
  const tn = counts?.tradable ?? 0;
  const emptyHint =
    !items.length && view !== "quarantine" && qn > 0
      ? `可交易/研究为空是正常的：库里 ${qn} 个因子都在「隔离」。点上面「隔离」就能看见。`
      : !items.length && view === "quarantine"
        ? "隔离区也是空的（库内无 factor_active_set 行）"
        : !items.length
          ? "池为空（尚无晋升到该状态的因子）"
          : null;

  return (
    <section className="ops-panel ops-area-pool">
      <div className="ops-panel-head">
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="ops-panel-title">因子池</span>
          <div className="ops-toggle">
            <button
              type="button"
              className={cn(view === "tradable" && "active")}
              onClick={() => onViewChange("tradable")}
            >
              可交易{tn > 0 ? ` ${tn}` : ""}
            </button>
            <button
              type="button"
              className={cn(view === "research" && "active research")}
              onClick={() => onViewChange("research")}
            >
              研究{counts?.research ? ` ${counts.research}` : ""}
            </button>
            <button
              type="button"
              className={cn(view === "quarantine" && "active", qn > 0 && "has-badge")}
              onClick={() => onViewChange("quarantine")}
              title="IC 衰减隔离，不进交易"
            >
              隔离{qn > 0 ? ` ${qn}` : ""}
            </button>
          </div>
        </div>
        <span className="ops-mono ops-muted" title={callout}>
          {items.length} 行
          {counts?.all != null ? ` / 库 ${counts.all}` : ""}
        </span>
      </div>
      <div className="ops-panel-body tight" style={{ maxHeight: 320 }}>
        {callout ? (
          <div
            className={cn("ops-pool-callout", !items.length && qn > 0 && "warn")}
            style={{ marginBottom: 8 }}
          >
            {callout}
          </div>
        ) : null}

        {!items.length ? (
          <div className="ops-empty">
            {emptyHint}
            {qn > 0 && view !== "quarantine" ? (
              <button
                type="button"
                className="ops-link-btn"
                style={{ display: "block", marginTop: 8 }}
                onClick={() => onViewChange("quarantine")}
              >
                查看隔离区 →
              </button>
            ) : null}
          </div>
        ) : (
          <table className="ops-table">
            <thead>
              <tr>
                <th>factor_id</th>
                <th>状态</th>
                <th>来源</th>
                <th>ICIR</th>
                <th>在线权</th>
                <th>Router</th>
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 40).map((r) => (
                <tr key={r.factor_id}>
                  <td className="ops-mono">{r.factor_id}</td>
                  <td className={r.state === "QUARANTINE" ? "ops-lag" : undefined}>
                    {STATE_ZH[String(r.state || "")] || r.state}
                  </td>
                  <td className="ops-mono ops-muted" style={{ fontSize: 10 }}>
                    {r.source || "—"}
                  </td>
                  <td className="ops-mono">
                    {r.icir == null ? "—" : Number(r.icir).toFixed(3)}
                  </td>
                  <td className="ops-mono">
                    {r.online_weight == null
                      ? "—"
                      : Number(r.online_weight).toFixed(3)}
                  </td>
                  <td className={r.router_reachable ? "ops-ok" : "ops-muted"}>
                    {r.router_reachable ? "可达" : "不可达"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {view === "quarantine" && (quarantineReasons || []).length > 0 ? (
          <div className="ops-muted" style={{ marginTop: 8, fontSize: 10 }}>
            近 14 天隔离原因：
            {(quarantineReasons || [])
              .slice(0, 3)
              .map((x) => `${x.reason}×${x.n}`)
              .join("；")}
          </div>
        ) : null}
      </div>
    </section>
  );
}
