"use client";

type Reject = {
  factor_id?: string;
  action?: string;
  reason?: string;
  created_at?: string;
  metrics?: unknown;
};

/** 进化日志 action → 中文批注（漏斗条 + 拒绝列表共用） */
const ACTION_ZH: Record<string, string> = {
  card_generated: "报告卡生成",
  chain_step: "链路推进",
  llm_admit: "LLM 准入",
  gate_pass: "门禁通过",
  promote: "晋升成功",
  promote_reject: "晋升拒绝",
  test_reject: "测试拒绝",
  wfo_reject: "滚动验证拒绝",
  wfo_ic_reject: "滚动IC拒绝",
  net_ic_low: "净IC过低",
  quarantine: "隔离入库",
  drift_detected: "漂移检出",
  advance: "状态晋级",
  restore_manual: "人工恢复",
  deactivate: "停用",
  reject: "拒绝",
  eval_all_failed: "评估全失败",
  active_cap: "活跃池容量满",
  unknown: "未知动作",
};

function actionLabel(action: string): string {
  const key = String(action || "unknown");
  return ACTION_ZH[key] || key;
}

export function OpsFunnel({
  counts,
  rejects,
  onActionClick,
}: {
  counts: Record<string, number>;
  rejects: Reject[];
  onActionClick?: (action: string, n: number) => void;
}) {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));

  return (
    <section className="ops-panel ops-area-funnel">
      <div className="ops-panel-head">
        <span className="ops-panel-title">挖矿漏斗 · 7天</span>
        <span className="ops-mono ops-muted">{entries.length} 种动作</span>
      </div>
      <div className="ops-panel-body tight">
        {!entries.length ? (
          <div className="ops-empty">暂无进化日志</div>
        ) : (
          entries.slice(0, 10).map(([action, n]) => (
            <button
              key={action}
              type="button"
              className="ops-funnel-row"
              title={`${action} · ${actionLabel(action)}`}
              style={{
                width: "100%",
                background: "transparent",
                border: 0,
                padding: 0,
                cursor: "pointer",
                color: "inherit",
                textAlign: "left",
              }}
              onClick={() => onActionClick?.(action, n)}
            >
              <span
                style={{
                  fontSize: 11,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  minWidth: 0,
                }}
              >
                <span>{actionLabel(action)}</span>
                <span className="ops-mono ops-muted" style={{ fontSize: 9, marginLeft: 4 }}>
                  {action}
                </span>
              </span>
              <div className="ops-funnel-bar-track">
                <div
                  className="ops-funnel-bar"
                  style={{ width: `${(n / max) * 100}%` }}
                />
              </div>
              <span className="ops-mono ops-muted" style={{ textAlign: "right" }}>
                {n}
              </span>
            </button>
          ))
        )}

        <div className="ops-reject-list">
          <div className="ops-label" style={{ marginBottom: 4 }}>
            拒绝原因（近7天）
          </div>
          {!rejects?.length ? (
            <div className="ops-empty" style={{ padding: 8 }}>
              近 7 天无拒绝记录
            </div>
          ) : (
            rejects.slice(0, 5).map((r, i) => (
              <div
                key={i}
                className="ops-reject-item"
                title={JSON.stringify(r.metrics || {})}
              >
                <span className="ops-muted" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
                  {actionLabel(r.action || "—")}
                </span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <span className="ops-mono">{r.factor_id || "—"}</span>
                  <span className="ops-muted"> · {r.reason || "—"}</span>
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
