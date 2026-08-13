"use client";

import { cn } from "@/lib/utils";

type ErrItem = {
  severity?: string;
  source?: string;
  message?: string;
  timestamp?: string;
};

export function OpsErrors({
  items,
  counts,
}: {
  items: ErrItem[];
  counts?: { P0?: number; P1?: number; total?: number };
}) {
  const sorted = [...(items || [])].sort((a, b) => {
    const rank = (s?: string) =>
      s === "P0" ? 0 : s === "P1" ? 1 : s === "P2" ? 2 : 3;
    return rank(a.severity) - rank(b.severity);
  });

  return (
    <section id="ops-errors" className="ops-panel ops-area-err">
      <div className="ops-panel-head">
        <span className="ops-panel-title">报错中心</span>
        <div className="ops-err-counts ops-mono">
          <span className="ops-down">P0 {counts?.P0 ?? 0}</span>
          <span className="ops-lag">P1 {counts?.P1 ?? 0}</span>
          <span className="ops-muted">合计 {counts?.total ?? 0}</span>
        </div>
      </div>
      <div className="ops-panel-body tight ops-err-body">
        {!sorted.length ? (
          <div className="ops-empty">暂无 P0–P3 报错</div>
        ) : (
          <table className="ops-table">
            <thead>
              <tr>
                <th>级</th>
                <th>来源</th>
                <th>信息</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 40).map((e, i) => (
                <tr key={i}>
                  <td
                    className={cn(
                      e.severity === "P0"
                        ? "ops-down"
                        : e.severity === "P1"
                          ? "ops-lag"
                          : "ops-muted",
                    )}
                  >
                    {e.severity}
                  </td>
                  <td>{e.source}</td>
                  <td
                    style={{
                      maxWidth: 640,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={e.message}
                  >
                    {e.message}
                  </td>
                  <td className="ops-mono" style={{ fontSize: 9 }}>
                    {e.timestamp || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
