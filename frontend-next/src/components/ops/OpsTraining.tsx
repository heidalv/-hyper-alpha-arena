"use client";

import { cn } from "@/lib/utils";

type FilterBlock = {
  win_rate?: number;
  net_ret?: number;
  n?: number;
  coverage?: number;
} | null;

type MetaReport = {
  oos_auc_lgbm?: number;
  oos_auc_linear?: number;
  auc?: number;
  usable?: boolean;
  n_settled?: number;
  n_settled_raw?: number;
  pos?: number;
  neg?: number;
  features?: number;
  status?: string;
  error?: string;
  note?: string;
  ts?: number;
  gate_reasons?: string[];
  baseline?: { win_rate?: number; net_ret?: number };
  filter_top30pct?: FilterBlock;
  filter_top15pct?: FilterBlock;
  top_importance?: { name?: string; importance?: number }[];
} | null;

const STATUS_ZH: Record<string, string> = {
  trained: "已训练",
  insufficient: "样本不足",
  imbalanced: "类别不均",
  error: "训练失败",
  no_deps: "缺依赖",
  no_report: "无报告",
};

function pct(v?: number | null, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

function fmtTs(ts?: number): string {
  if (!ts) return "—";
  // trainer 偶发写错时钟；正常秒级 unix
  const ms = ts > 1e12 ? ts : ts * 1000;
  try {
    return new Date(ms).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "—";
  }
}

export function OpsTraining({
  report,
  laneEnabled,
  cbEnabled,
  laneNote,
}: {
  report?: MetaReport;
  laneEnabled?: boolean;
  cbEnabled?: boolean;
  laneNote?: string;
}) {
  const auc = report?.oos_auc_lgbm ?? report?.auc;
  const aucLin = report?.oos_auc_linear;
  const statusZh = STATUS_ZH[String(report?.status || "")] || report?.status || "—";
  const reasons = report?.gate_reasons || [];
  const f30 = report?.filter_top30pct;
  const top = report?.top_importance || [];

  return (
    <section className="ops-panel ops-area-train">
      <div className="ops-panel-head">
        <span className="ops-panel-title">元标签 / 车道</span>
        <span className={cn("ops-mono", report?.usable ? "ops-ok" : "ops-lag")}>
          {report?.usable ? "可用" : "未达标"}
        </span>
      </div>
      <div className="ops-panel-body">
        {!report ? (
          <div className="ops-empty">暂无训练报告</div>
        ) : (
          <>
            <div className="ops-train-grid">
              <div className="ops-train-metric">
                <div className="ops-label">样本外 AUC（树模型）</div>
                <div className="ops-kpi-value ops-mono">
                  {auc == null ? "—" : Number(auc).toFixed(3)}
                </div>
                <div className="ops-muted" style={{ fontSize: 10, marginTop: 2 }}>
                  门槛约 0.53 · 线性 {aucLin == null ? "—" : Number(aucLin).toFixed(3)}
                </div>
              </div>
              <div className="ops-train-metric">
                <div className="ops-label">有效样本</div>
                <div className="ops-kpi-value ops-mono">
                  {report.n_settled ?? "—"}
                </div>
                <div className="ops-muted" style={{ fontSize: 10, marginTop: 2 }}>
                  原始 {report.n_settled_raw ?? "—"} · 赢{report.pos ?? "—"}/亏{report.neg ?? "—"}
                </div>
              </div>
              <div className="ops-train-metric">
                <div className="ops-label">训练状态</div>
                <div className="ops-mono" style={{ fontSize: 13, marginTop: 4 }}>
                  {statusZh}
                </div>
                <div className="ops-muted" style={{ fontSize: 10, marginTop: 2 }}>
                  {fmtTs(report.ts)} · 特征 {report.features ?? "—"}
                </div>
              </div>
              <div className="ops-train-metric">
                <div className="ops-label">是否可用</div>
                <div
                  className={cn("ops-mono", report.usable ? "ops-ok" : "ops-lag")}
                  style={{ fontSize: 16, marginTop: 4 }}
                >
                  {report.usable ? "是（可进决策）" : "否（仅影子）"}
                </div>
              </div>
            </div>

            <div className="ops-train-compare">
              <div>
                <div className="ops-label">基线（全样本）</div>
                <div className="ops-mono" style={{ fontSize: 11 }}>
                  胜率 {pct(report.baseline?.win_rate)} · 净收益 {pct(report.baseline?.net_ret, 3)}
                </div>
              </div>
              <div>
                <div className="ops-label">过滤前 30%</div>
                <div className="ops-mono" style={{ fontSize: 11 }}>
                  {f30
                    ? `胜率 ${pct(f30.win_rate)} · 净收益 ${pct(f30.net_ret, 3)} · n=${f30.n ?? "—"}`
                    : "—"}
                </div>
              </div>
            </div>

            {!report.usable && reasons.length > 0 ? (
              <div className="ops-train-reasons">
                <div className="ops-label">未达标原因</div>
                <ul>
                  {reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {report.note ? (
              <div className="ops-muted" style={{ marginTop: 6, fontSize: 11 }}>
                {report.note}
              </div>
            ) : null}

            {top.length > 0 ? (
              <div className="ops-train-fi">
                <div className="ops-label">重要特征 Top5</div>
                <div className="ops-train-fi-list">
                  {top.map((t) => (
                    <span key={String(t.name)} className="ops-mono">
                      {t.name}
                      <span className="ops-muted">
                        {" "}
                        {t.importance == null ? "" : Number(t.importance).toFixed(3)}
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}

        {report?.error ? (
          <div className="ops-down" style={{ marginTop: 8, fontSize: 11 }}>
            {report.error}
          </div>
        ) : null}

        <div className="ops-lane-box">
          <div>
            <span className="ops-muted">绑定车道 </span>
            <span className={cn("ops-mono", laneEnabled ? "ops-lag" : "ops-ok")}>
              {laneEnabled ? "已启用（会真实开仓）" : "干跑（只写心跳，不开仓）"}
            </span>
          </div>
          <div>
            <span className="ops-muted">短线熔断 apply </span>
            <span className={cn("ops-mono", cbEnabled ? "ops-lag" : "ops-ok")}>
              {cbEnabled ? "已开启" : "干跑（不真正熔断）"}
            </span>
          </div>
          {laneNote ? (
            <div className="ops-muted" style={{ fontSize: 10, lineHeight: 1.4 }}>
              {laneNote}
            </div>
          ) : (
            <div className="ops-muted" style={{ fontSize: 10, lineHeight: 1.4 }}>
              干跑=调度只写心跳不开仓；running 绑定会一直挂着，不等于已经在实盘下单。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
