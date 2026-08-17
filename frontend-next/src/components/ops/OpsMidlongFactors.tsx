"use client";

/**
 * OpsMidlongFactors — 因子运维台 · 中线因子概况（阶段2-1）
 * 与短线运维台对称：概况（活跃/候选/拒绝、按 4h/1d）、K线预检、
 * 一键快速挖掘（灌库→后台回测）、复检退役、最近回测证据表。
 *
 * 后端：
 *   GET  /api/ops/midlong-factors            概况 + 预检 + 最近回测
 *   POST /api/ops/midlong-factors/mine       一键挖掘（seed + 异步 validate job）
 *   POST /api/ops/midlong-factors/prune      复检退役
 *   GET  /api/factors/jobs/{job_id}          轮询回测进度
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

type MidlongHealth = {
  active?: number;
  candidate?: number;
  rejected?: number;
  avg_active_ic?: number | null;
  by_timeframe?: Record<string, number>;
  top_active?: { factor_id?: string; grade?: string; timeframe?: string; ic_mean?: number; runtime_weight?: number }[];
};

type CandItem = {
  factor_id?: string;
  name?: string;
  timeframe?: string;
  note?: string;
  category?: string;
  source?: string;
};

type RejectedItem = {
  factor_id?: string;
  name?: string;
  timeframe?: string;
  grade?: string;
  scores?: Record<string, unknown>;
  scored_at?: number;
};

type Preflight = {
  symbols?: string[];
  rows?: Record<string, Record<string, number>>;
  need_bars?: number | Record<string, number>;
  ok?: boolean;
  error?: string;
};

type GateConfig = {
  lookback?: number;
  lookback_1d?: number;
  fwd_4h?: number;
  fwd_1d?: number;
  min_sharpe?: number;
  active_max?: number;
  research_enabled?: boolean;
};

type MidlongOverview = {
  health?: MidlongHealth;
  candidates?: CandItem[];
  candidate_count?: number;
  rejected_recent?: RejectedItem[];
  rejected_count?: number;
  preflight?: Preflight;
  gate_config?: GateConfig;
};

type JobState = {
  job_id?: string;
  status?: string;
  percent?: number | null;
  progress?: number;
  total?: number;
  message?: string;
  result?: { scored?: number; promoted?: number };
  error?: string;
};

function num(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? (Math.abs(n) >= 10 ? n.toFixed(2) : n.toFixed(3)) : "—";
}

export function OpsMidlongFactors() {
  const [data, setData] = useState<MidlongOverview | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const inflight = useRef(false);

  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const r = await apiRequest("/ops/midlong-factors", { timeout: 15000 });
      setData(r);
    } catch (e: any) {
      setMsg(`概况加载失败: ${e?.message || e}`);
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(id);
  }, [load]);

  // 轮询回测 job
  useEffect(() => {
    if (!job?.job_id || (job.status !== "pending" && job.status !== "running")) return;
    const id = window.setInterval(async () => {
      try {
        const r = await apiRequest(`/factors/jobs/${job.job_id}`, { timeout: 8000 });
        setJob(r);
        if (r?.status === "done" || r?.status === "error") {
          void load();
          setMsg(
            r?.status === "done"
              ? `回测完成：打分 ${r?.result?.scored ?? "—"}，晋升 ${r?.result?.promoted ?? "—"}`
              : `回测失败：${r?.error || "未知"}`,
          );
        }
      } catch {
        /* 轮询失败不刷屏 */
      }
    }, 4000);
    return () => window.clearInterval(id);
  }, [job?.job_id, job?.status, load]);

  async function mine() {
    if (!window.confirm("一键快速挖掘：灌库 Alpha101 候选（幂等）→ 后台样本外回测（约1-2分钟）。确认？")) return;
    setBusy("mine");
    setMsg(null);
    try {
      const r = await apiRequest("/ops/midlong-factors/mine", { method: "POST", body: JSON.stringify({ validate: true }) });
      setMsg(`灌库：登记 ${r?.seed?.registered ?? 0} / 跳过 ${r?.seed?.skipped ?? 0}；回测已排队`);
      if (r?.validate?.job_id) setJob(r.validate);
      await load();
    } catch (e: any) {
      setMsg(`挖掘失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function prune() {
    if (!window.confirm("对活跃中线因子重跑样本外复检（当前活跃=0 时为空跑）。确认？")) return;
    setBusy("prune");
    setMsg(null);
    try {
      const r = await apiRequest("/ops/midlong-factors/prune", { method: "POST", body: JSON.stringify({}) });
      setMsg(`复检完成：checked=${r?.checked ?? 0} retired=${r?.retired ?? 0} reduced=${r?.reduced ?? 0}${r?.skipped ? ` (${r.skipped})` : ""}`);
      await load();
    } catch (e: any) {
      setMsg(`复检失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  const h = data?.health || {};
  const pf = data?.preflight || {};
  const gc = data?.gate_config || {};
  const running = job?.status === "pending" || job?.status === "running";

  return (
    <section className="ops-panel ops-area-mining" id="ops-midlong-factors">
      <div className="ops-panel-head">
        <span className="ops-panel-title">中线因子 · 阶段2 弹药生产</span>
        <span className={cn("ops-mono", h.active ? "ops-ok" : "ops-muted")}>
          活跃{h.active ?? 0} · 候选{h.candidate ?? 0} · 已拒{h.rejected ?? 0}
          {" · "}4h:{h.by_timeframe?.["4h"] ?? 0} 1d:{h.by_timeframe?.["1d"] ?? 0}
        </span>
      </div>

      <div className="ops-panel-body tight">
        <div className="ops-muted" style={{ fontSize: 11, lineHeight: 1.5, marginBottom: 8 }}>
          中线因子从候选到实盘只走一条路：登记候选 → 4h/1d 样本外回测（IC/ICIR/OOS Sharpe +
          DSR/PBO 多重检验）→ A/B 级晋升 active（上限 {gc.active_max ?? 30}）→ 周度复检退役。
          门禁 fail-closed，宁缺毋滥。
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <button type="button" className="ops-link-btn" disabled={!!busy || running} onClick={() => void mine()}>
            {busy === "mine" ? "挖掘中…" : "一键快速挖掘（灌库+回测）"}
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy} onClick={() => void prune()}>
            {busy === "prune" ? "复检中…" : "复检退役"}
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy} onClick={() => void load()}>
            刷新
          </button>
        </div>

        {running && (
          <div className="ops-lag" style={{ marginTop: 8, fontSize: 11 }}>
            回测进行中 {job?.message || ""} · {job?.progress ?? 0}/{job?.total ?? 0}
            {job?.percent != null ? ` (${job.percent}%)` : ""}
          </div>
        )}
        {msg ? (
          <div className={cn("ops-muted", msg.includes("失败") && "ops-lag")} style={{ marginTop: 8, fontSize: 11 }}>
            {msg}
          </div>
        ) : null}

        {/* K线预检 + 闸门参数 */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: 8,
            marginTop: 10,
            fontSize: 11,
          }}
        >
          <div>
            <div className="ops-muted">打分截面 K 线预检</div>
            <div className="ops-mono">
              {pf.error
                ? `异常 ${pf.error}`
                : pf.ok
                  ? "✅ 数据充足"
                  : "⚠ 数据不足"}
              {pf.symbols?.length ? ` · ${pf.symbols.join(",")}` : ""}
            </div>
            {pf.rows &&
              Object.entries(pf.rows).map(([tf, rows]) => (
                <div key={tf} className="ops-mono" style={{ fontSize: 10 }}>
                  {tf}: {Object.entries(rows).map(([s, n]) => `${s}${n}`).join(" ")} /
                  需{typeof pf.need_bars === "object" && pf.need_bars ? pf.need_bars[tf] ?? "—" : pf.need_bars}
                </div>
              ))}
          </div>
          <div>
            <div className="ops-muted">闸门参数</div>
            <div className="ops-mono" style={{ fontSize: 10 }}>
              lookback {gc.lookback ?? "—"}(4h)/{gc.lookback_1d ?? "—"}(1d) · fwd 4h×{gc.fwd_4h ?? "—"} 1d×{gc.fwd_1d ?? "—"}
              <br />
              min_sharpe {gc.min_sharpe ?? "—"} · 上限 {gc.active_max ?? "—"} · 研究{" "}
              {gc.research_enabled ? "开" : "关"}
            </div>
          </div>
          <div>
            <div className="ops-muted">平均活跃 IC</div>
            <div className="ops-mono">{h.avg_active_ic != null ? num(h.avg_active_ic) : "—"}</div>
          </div>
        </div>

        {/* Top 活跃因子 */}
        {h.top_active && h.top_active.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="ops-muted" style={{ fontSize: 10, marginBottom: 4 }}>Top 活跃因子</div>
            <div className="ops-mono" style={{ fontSize: 10, lineHeight: 1.5 }}>
              {h.top_active.map((t, i) => (
                <div key={t.factor_id ?? i}>
                  {t.factor_id} [{t.grade}] {t.timeframe} IC={num(t.ic_mean)} w={t.runtime_weight}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 最近回测证据（拒绝表） */}
        {data?.rejected_recent && data.rejected_recent.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="ops-muted" style={{ fontSize: 10, marginBottom: 4 }}>
              最近回测（近 {Math.min(data.rejected_recent.length, 20)} 条，全部被 DSR/PBO 闸门拒绝）
            </div>
            <div style={{ maxHeight: 220, overflowY: "auto" }}>
              <table className="ops-mono" style={{ width: "100%", fontSize: 10, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left" }}>
                    <th style={{ padding: "2px 6px" }}>因子</th>
                    <th style={{ padding: "2px 6px" }}>tf</th>
                    <th style={{ padding: "2px 6px" }}>级</th>
                    <th style={{ padding: "2px 6px", textAlign: "right" }}>IC</th>
                    <th style={{ padding: "2px 6px", textAlign: "right" }}>OOS Sharpe</th>
                    <th style={{ padding: "2px 6px" }}>拒绝原因</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rejected_recent.map((r, i) => {
                    const s = r.scores || {};
                    return (
                      <tr key={r.factor_id ?? i} style={{ borderTop: "1px solid var(--border, #333)" }}>
                        <td style={{ padding: "2px 6px" }}>{r.factor_id}</td>
                        <td style={{ padding: "2px 6px" }}>{r.timeframe}</td>
                        <td style={{ padding: "2px 6px" }}>{r.grade}</td>
                        <td style={{ padding: "2px 6px", textAlign: "right" }}>{num(s.ic_mean)}</td>
                        <td style={{ padding: "2px 6px", textAlign: "right" }}>{num(s.oos_sharpe)}</td>
                        <td style={{ padding: "2px 6px", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {String(s.reason || "—").slice(0, 60)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* 候选列表 */}
        {data?.candidates && data.candidates.length > 0 && (
          <div className="ops-muted" style={{ marginTop: 8, fontSize: 10 }}>
            候选 {data.candidate_count ?? 0}：{data.candidates.slice(0, 12).map((c) => c.factor_id).join(", ")}
            {data.candidates.length > 12 ? " …" : ""}
          </div>
        )}
      </div>
    </section>
  );
}
