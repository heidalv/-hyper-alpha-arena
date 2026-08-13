"use client";

type Cand = {
  id: number;
  symbol?: string;
  period?: string;
  factor_set?: string;
  gate_verdict?: string;
  gate_reasons?: string[];
  metrics?: { n?: number; pf?: number; t_stat?: number };
};

type Bind = {
  id: number;
  symbol?: string;
  period?: string;
  status?: string;
  candidate_id?: number | null;
  in_ai_pool?: boolean | null;
  link_note?: string;
};

type SymRow = {
  symbol: string;
  n: number;
  n_pass: number;
  n_promising?: number;
  n_fail: number;
};

export function OpsPairs({
  candidates,
  passCount,
  passCountGlobal,
  bySymbol,
  callout,
  bindings,
  laneEnabled,
  laneNote,
  aiSymbols,
  onEnable,
  onPause,
}: {
  candidates: Cand[];
  passCount: number;
  passCountGlobal?: number;
  bySymbol?: SymRow[];
  callout?: string;
  bindings: Bind[];
  laneEnabled?: boolean;
  laneNote?: string;
  aiSymbols?: string[];
  onEnable: (id: number) => void;
  onPause: (id: number) => void;
}) {
  return (
    <section className="ops-panel ops-area-pairs">
      <div className="ops-panel-head">
        <span className="ops-panel-title">候选 / 绑定</span>
        <span className="ops-mono ops-muted" title={laneNote}>
          车道 {laneEnabled ? "交易开" : "干跑"} · 全表pass{" "}
          <span className="ops-ok">{passCountGlobal ?? passCount}</span>
        </span>
      </div>

      {(bySymbol || []).length > 0 ? (
        <div className="ops-sym-bar">
          <span className="ops-label">24h 按币</span>
          {(bySymbol || []).slice(0, 10).map((s) => (
            <span key={s.symbol} className="ops-sym-chip ops-mono">
              {s.symbol}{" "}
              <b className={s.n_pass > 0 ? "ops-ok" : "ops-down"}>P{s.n_pass}</b>
              <span className="ops-muted">
                /F{s.n_fail}/{s.n}
              </span>
            </span>
          ))}
        </div>
      ) : null}

      {aiSymbols && aiSymbols.length > 0 ? (
        <div className="ops-sym-bar" style={{ borderTop: "none", paddingTop: 0 }}>
          <span className="ops-label">AI池</span>
          <span className="ops-mono ops-info">{aiSymbols.join(" ")}</span>
        </div>
      ) : null}

      {callout ? (
        <div className="ops-muted" style={{ padding: "4px 10px", fontSize: 10 }}>
          {callout}
        </div>
      ) : null}

      <div className="ops-pairs-split">
        <section>
          <div className="ops-panel-head" style={{ background: "transparent" }}>
            <span className="ops-label">候选（pass优先·每币最新）</span>
            <span className="ops-mono ops-ok">页内pass {passCount}</span>
          </div>
          <div className="ops-panel-body tight">
            {!candidates.length ? (
              <div className="ops-empty">暂无候选</div>
            ) : (
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>币</th>
                    <th>门禁</th>
                    <th>原因</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {candidates.slice(0, 16).map((c) => (
                    <tr key={c.id}>
                      <td className="ops-mono">{c.id}</td>
                      <td className="ops-mono">
                        {c.symbol}/{c.period}
                        <div className="ops-muted" style={{ fontSize: 9 }}>
                          {c.factor_set}
                          {c.metrics?.pf != null
                            ? ` · pf${Number(c.metrics.pf).toFixed(2)}`
                            : ""}
                        </div>
                      </td>
                      <td
                        className={
                          c.gate_verdict === "pass"
                            ? "ops-ok"
                            : c.gate_verdict === "promising"
                              ? "ops-lag"
                              : "ops-down"
                        }
                      >
                        {c.gate_verdict}
                      </td>
                      <td
                        style={{
                          maxWidth: 160,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          fontSize: 10,
                        }}
                        title={(c.gate_reasons || []).join("；")}
                      >
                        {(c.gate_reasons || []).slice(0, 2).join("；") || "—"}
                      </td>
                      <td>
                        {c.gate_verdict === "pass" ? (
                          <button
                            type="button"
                            className="ops-link-btn ok"
                            onClick={() => onEnable(c.id)}
                          >
                            启用
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
        <section>
          <div className="ops-panel-head" style={{ background: "transparent" }}>
            <span className="ops-label">绑定（持久态≠候选窗口）</span>
            <span className="ops-mono ops-muted">
              running{" "}
              {bindings.filter((b) => b.status === "running").length}
            </span>
          </div>
          <div className="ops-panel-body tight">
            {!bindings.length ? (
              <div className="ops-empty">暂无绑定</div>
            ) : (
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>币</th>
                    <th>状态</th>
                    <th>接线</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {bindings.slice(0, 12).map((b) => (
                    <tr key={b.id}>
                      <td className="ops-mono">{b.id}</td>
                      <td className="ops-mono">
                        {b.symbol}/{b.period}
                        {b.candidate_id != null ? (
                          <div className="ops-muted" style={{ fontSize: 9 }}>
                            ←候选#{b.candidate_id}
                          </div>
                        ) : null}
                      </td>
                      <td>{b.status}</td>
                      <td style={{ fontSize: 10 }}>
                        {b.in_ai_pool === true ? (
                          <span className="ops-ok">仍在AI池</span>
                        ) : b.in_ai_pool === false ? (
                          <span className="ops-lag">已不在AI池</span>
                        ) : (
                          <span className="ops-muted">—</span>
                        )}
                      </td>
                      <td>
                        {b.status === "running" ? (
                          <button
                            type="button"
                            className="ops-link-btn down"
                            onClick={() => onPause(b.id)}
                          >
                            暂停
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {laneNote ? (
              <div className="ops-muted" style={{ fontSize: 10, marginTop: 6 }}>
                {laneNote}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </section>
  );
}
