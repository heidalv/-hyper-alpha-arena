"use client";

/** 固定币池挖矿 vs AI选币快速扫描 — 两条链一眼分清 */
export function OpsChainProgress({
  fixedPool,
  aiScan,
}: {
  fixedPool?: {
    symbols?: string[];
    evo_symbols?: string[];
    backup_pool?: string[];
    training_core?: string[];
    session_fixed?: string[];
    tradable_factor_rows?: number | null;
    research_factor_rows?: number | null;
    quarantine_rows?: number | null;
    total_factor_rows?: number | null;
    state_dist?: Record<string, number>;
    quarantine_reasons?: { reason: string; n: number }[];
    evo_4h?: { last_at?: string | null; actions_7d?: Record<string, number> };
    evo_5m?: { last_at?: string | null; actions_7d?: Record<string, number> };
    meta?: {
      usable?: boolean;
      oos_auc_lgbm?: number;
      ts?: number;
      status?: string;
      n_settled?: number;
    } | null;
    meta_missing?: boolean;
    note?: string;
  } | null;
  aiScan?: {
    ai_symbols?: string[];
    pending_scan?: string[];
    scanning?: string[];
    scanned_24h?: string[];
    pass_24h?: number;
    candidates_24h?: number;
    last_watcher_at?: string | null;
    by_symbol?: { symbol: string; n_pass: number; n_fail: number; n: number }[];
    note?: string;
    lane_note?: string;
  } | null;
}) {
  const e4 = fixedPool?.evo_4h || {};
  const e5 = fixedPool?.evo_5m || {};
  const a4 = e4.actions_7d || {};
  const a5 = e5.actions_7d || {};
  const fixedSyms =
    fixedPool?.symbols?.length
      ? fixedPool.symbols
      : fixedPool?.session_fixed?.length
        ? fixedPool.session_fixed
        : fixedPool?.evo_symbols || [];
  const ai = aiScan?.ai_symbols || [];
  const pending = aiScan?.pending_scan || [];
  const scanning = aiScan?.scanning || [];
  const tradable = fixedPool?.tradable_factor_rows ?? 0;
  const quarantine = fixedPool?.quarantine_rows ?? 0;
  const totalFac = fixedPool?.total_factor_rows ?? 0;

  return (
    <section className="ops-panel ops-area-chain">
      <div className="ops-panel-head">
        <span className="ops-panel-title">双链进度</span>
        <span className="ops-mono ops-muted">左=固定币 · 右=AI选币</span>
      </div>
      <div className="ops-panel-body tight ops-chain-grid">
        <div className="ops-chain-col">
          <div className="ops-label">固定币（会话当前启用）</div>
          <div className="ops-sym-chips" style={{ marginTop: 4, marginBottom: 6 }}>
            {(fixedPool?.session_fixed?.length
              ? fixedPool.session_fixed
              : fixedSyms
            ).length ? (
              (fixedPool?.session_fixed?.length
                ? fixedPool.session_fixed
                : fixedSyms
              ).map((s) => (
                <span key={s} className="ops-sym-chip ops-mono ops-ok">
                  {s}
                </span>
              ))
            ) : (
              <span className="ops-down" style={{ fontSize: 11 }}>
                未读到固定币
              </span>
            )}
          </div>
          {(fixedPool?.backup_pool || []).length > 0 ? (
            <div className="ops-chain-line">
              <span className="ops-muted">固定币备选池</span>
              <span className="ops-mono">{(fixedPool?.backup_pool || []).join(" ")}</span>
            </div>
          ) : null}
          {(fixedPool?.evo_symbols || []).length > 0 ? (
            <div className="ops-chain-line">
              <span className="ops-muted">进化实际用币</span>
              <span className="ops-mono">{(fixedPool?.evo_symbols || []).join(" ")}</span>
            </div>
          ) : null}

          <div className="ops-chain-line">
            <span className="ops-muted">因子池状态</span>
            <span className="ops-mono">
              可交易{" "}
              <b className={tradable > 0 ? "ops-ok" : "ops-down"}>{tradable}</b>
              {" · "}隔离{" "}
              <b className={quarantine > 0 ? "ops-lag" : "ops-muted"}>{quarantine}</b>
              {" · "}合计 {totalFac}
            </span>
          </div>
          {tradable === 0 && quarantine > 0 ? (
            <div className="ops-down" style={{ fontSize: 11, margin: "4px 0" }}>
              可交易=0 是真的：库内 {quarantine} 行全是 QUARANTINE（IC 衰减隔离），不是看板算错。
            </div>
          ) : null}
          {(fixedPool?.quarantine_reasons || []).length > 0 ? (
            <div className="ops-muted" style={{ fontSize: 10, marginBottom: 4 }}>
              隔离原因：{" "}
              {(fixedPool?.quarantine_reasons || [])
                .slice(0, 2)
                .map((r) => `${r.reason}×${r.n}`)
                .join("；")}
            </div>
          ) : null}

          <div className="ops-chain-line">
            <span className="ops-muted">4h 上次</span>
            <span className="ops-mono">{_shortTs(e4.last_at) || "从未"}</span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">4h 7d</span>
            <span className="ops-mono">
              promote {a4.promote || 0} · 晋升拒绝 {a4.promote_reject || 0}
            </span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">5m 上次</span>
            <span className="ops-mono">{_shortTs(e5.last_at) || "从未"}</span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">元标签</span>
            <span className="ops-mono">
              {fixedPool?.meta_missing
                ? "无报告文件"
                : fixedPool?.meta?.usable
                  ? "可用"
                  : "已训但未达标"}
              {fixedPool?.meta?.oos_auc_lgbm != null
                ? ` · AUC ${Number(fixedPool.meta.oos_auc_lgbm).toFixed(3)}`
                : ""}
              {fixedPool?.meta?.n_settled != null
                ? ` · n=${fixedPool.meta.n_settled}`
                : ""}
            </span>
          </div>
          <div className="ops-muted" style={{ fontSize: 10, marginTop: 4 }}>
            {fixedPool?.note ||
              "固定币走日更因子进化；可交易行=PAPER/SMALL_LIVE/ACTIVE"}
          </div>
        </div>

        <div className="ops-chain-col">
          <div className="ops-label">AI 选币 → 快速扫描</div>
          <div className="ops-sym-chips" style={{ marginTop: 4, marginBottom: 6 }}>
            {ai.length ? (
              ai.map((s) => (
                <span key={s} className="ops-sym-chip ops-mono ops-info">
                  {s}
                </span>
              ))
            ) : (
              <span className="ops-muted" style={{ fontSize: 11 }}>
                （无运行会话选币）
              </span>
            )}
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">待扫 24h</span>
            <span className="ops-mono ops-lag">
              {pending.length ? pending.join(" ") : "无"}
            </span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">本 tick 启动</span>
            <span className="ops-mono ops-info">
              {scanning.length ? scanning.join(" ") : "—"}
            </span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">24h 候选/pass</span>
            <span className="ops-mono">
              {aiScan?.candidates_24h ?? 0} /{" "}
              <span className="ops-ok">{aiScan?.pass_24h ?? 0}</span>
            </span>
          </div>
          <div className="ops-chain-line">
            <span className="ops-muted">观察者</span>
            <span className="ops-mono">{_shortTs(aiScan?.last_watcher_at) || "无心跳"}</span>
          </div>
          {(aiScan?.by_symbol || []).slice(0, 6).length > 0 ? (
            <div className="ops-sym-chips">
              {(aiScan?.by_symbol || []).slice(0, 8).map((s) => (
                <span key={s.symbol} className="ops-sym-chip ops-mono">
                  {s.symbol}
                  <b className={s.n_pass > 0 ? "ops-ok" : "ops-muted"}>
                    {" "}
                    P{s.n_pass}
                  </b>
                  <span className="ops-muted">/{s.n}</span>
                </span>
              ))}
            </div>
          ) : null}
          <div className="ops-muted" style={{ fontSize: 10, marginTop: 4 }}>
            {aiScan?.lane_note ||
              "候选是扫描流水；绑定是历史 pass 晋级后的持久态"}
          </div>
        </div>
      </div>
    </section>
  );
}

function _shortTs(iso?: string | null) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return String(iso).slice(0, 16);
  }
}
