"use client";

import { cn } from "@/lib/utils";

type HbItem = {
  task_id: string;
  sla?: string;
  age_human?: string;
  last_status?: string;
};

/** 技术 task_id → 中文名（看不懂英文缩写也能一眼懂在干什么） */
const HB_LABELS: Record<string, { title: string; hint: string }> = {
  pair_selector_watcher: {
    title: "AI选币扫描",
    hint: "盯新币、跑快速矩阵",
  },
  pair_binding_lane: {
    title: "币种绑定车道",
    hint: "通过的候选挂到交易车道",
  },
  scalp_chain_health: {
    title: "短线链路健康",
    hint: "短线决策/执行链路自检",
  },
  scalp_circuit_breaker: {
    title: "短线熔断器",
    hint: "连亏/异常时自动刹车",
  },
  scalp_daily_health: {
    title: "短线每日体检",
    hint: "每日 05:30 全链路健康扫描",
  },
  scalp_symbol_profile: {
    title: "币种画像刷新",
    hint: "每日 05:45 重建币种短线画像",
  },
};

const SLA_CN: Record<string, string> = {
  ok: "正常",
  lag: "滞后",
  down: "中断",
  disabled: "已关闭",
  unknown: "未知",
};

/** 固定展示顺序，避免 API 乱序导致四个格子跳来跳去 */
const HB_ORDER = [
  "pair_selector_watcher",
  "pair_binding_lane",
  "scalp_chain_health",
  "scalp_circuit_breaker",
];

function labelOf(taskId: string) {
  return HB_LABELS[taskId] || { title: taskId, hint: taskId };
}

export function OpsHeartbeatMatrix({ items }: { items: HbItem[] }) {
  const byId = new Map(items.map((h) => [h.task_id, h]));
  const ordered: HbItem[] = [
    ...HB_ORDER.map((id) => byId.get(id)).filter(Boolean) as HbItem[],
    ...items.filter((h) => !HB_ORDER.includes(h.task_id)),
  ];

  return (
    <section className="ops-panel ops-area-hb">
      <div className="ops-panel-head">
        <span className="ops-panel-title">心跳 SLA</span>
        <span className="ops-mono ops-muted">{ordered.length} 路</span>
      </div>
      <div className="ops-panel-body tight">
        {!ordered.length ? (
          <div className="ops-empty">暂无心跳</div>
        ) : (
          <div className="ops-hb-grid">
            {ordered.map((h) => {
              const sla = h.sla || "unknown";
              const lab = labelOf(h.task_id);
              return (
                <div
                  key={h.task_id}
                  className={cn("ops-hb-cell", `sla-${sla}`)}
                  title={`${lab.title} · ${h.task_id}`}
                >
                  <div className="ops-hb-title">{lab.title}</div>
                  <div className="ops-hb-hint">{lab.hint}</div>
                  <div className="ops-hb-meta">
                    <span className={cn(`ops-${sla === "unknown" ? "muted" : sla}`)}>
                      {SLA_CN[sla] || sla}
                    </span>
                    <span className="ops-mono ops-muted">{h.age_human || "—"}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
