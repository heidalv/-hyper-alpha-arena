"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

type Preflight = {
  ready?: boolean;
  ok_symbols?: string[];
  short_symbols?: string[];
  need_bars?: number;
  error?: string;
};

type Diagnostics = {
  l1_signals?: Record<string, number>;
  top_reasons?: { bucket: string; action: string; reason: string; n: number }[];
  error?: string;
};

type EvoStatus = {
  running?: boolean;
  mining_boost_auto?: boolean;
  last_error?: string | null;
  last_activity_at?: string | null;
  schedule?: Record<string, string>;
  recent_activity?: {
    phase?: string;
    action?: string;
    factor_id?: string;
    reason?: string;
    created_at?: string;
  }[];
  runtime?: {
    running?: boolean;
    period?: string | null;
    source?: string | null;
    started_at?: string | null;
    elapsed_sec?: number | null;
    last_finished_at?: string | null;
    last_period?: string | null;
    last_elapsed_sec?: number | null;
    last_error?: string | null;
    last_report?: {
      n_candidates?: number;
      n_evaluated?: number;
      n_survivors?: number;
      n_promoted?: number;
      error?: string;
      message?: string;
      elapsed_sec?: number;
    } | null;
    boost_applied?: { ok?: boolean } | null;
  };
  active_factors?: { state_dist?: Record<string, number>; total?: number };
};

function fmtAgo(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s前`;
  if (sec < 3600) return `${Math.floor(sec / 60)}分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}小时前`;
  return `${Math.floor(sec / 86400)}天前`;
}

export function OpsMiningBoost() {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [status, setStatus] = useState<EvoStatus | null>(null);
  const statusInflight = useRef(false);

  const loadStatus = useCallback(async () => {
    if (statusInflight.current) return;
    statusInflight.current = true;
    try {
      const r = await apiRequest("/compute/evolution/status", { timeout: 8000 });
      setStatus(r);
    } catch {
      /* 轮询失败不刷屏 */
    } finally {
      statusInflight.current = false;
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const id = window.setInterval(() => void loadStatus(), 10000);
    return () => window.clearInterval(id);
  }, [loadStatus]);

  async function setAuto(enabled: boolean) {
    setBusy("auto");
    setMsg(null);
    try {
      const r = await apiRequest("/compute/evolution/mining-boost-auto", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      setMsg(r?.message || (enabled ? "已开启自动加强" : "已关闭自动加强"));
      await loadStatus();
    } catch (e: any) {
      setMsg(`自动开关失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function check(period: string) {
    setBusy(`pre-${period}`);
    try {
      const r = await apiRequest(`/compute/evolution/preflight?period=${period}`);
      setPreflight(r);
      setMsg(
        r?.ready
          ? `预检通过 ${period}：${(r.ok_symbols || []).join(",")}`
          : `预检不足 ${period}：缺 ${(r.short_symbols || []).join(",") || r?.error || "未知"}`,
      );
    } catch (e: any) {
      setMsg(`预检失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function trigger(period: string) {
    if (!window.confirm(`确认触发因子进化 period=${period}？后台单飞，可能跑很久。`)) return;
    setBusy(`run-${period}`);
    setMsg(null);
    try {
      const r = await apiRequest("/compute/evolution/trigger", {
        method: "POST",
        body: JSON.stringify({ period }),
      });
      setMsg(r?.success ? r.message : `触发失败: ${r?.message || JSON.stringify(r)}`);
      await loadStatus();
    } catch (e: any) {
      setMsg(`触发失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function abortRun() {
    if (!window.confirm("确认强制结束卡住的进化任务？")) return;
    setBusy("abort");
    setMsg(null);
    try {
      const r = await apiRequest("/compute/evolution/abort", {
        method: "POST",
        body: JSON.stringify({ reason: "ops_manual" }),
      });
      setMsg(r?.message || (r?.aborted ? "已结束" : "当时没有运行中任务"));
      await loadStatus();
    } catch (e: any) {
      setMsg(`结束失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function repromote() {
    if (!window.confirm("对隔离因子做复评，达标者回 PAPER（纸面），确认？")) return;
    setBusy("repromote");
    setMsg(null);
    try {
      const r = await apiRequest("/compute/evolution/repromote-quarantine", {
        method: "POST",
        body: JSON.stringify({ period: "4h", limit: 40 }),
      });
      setMsg(r?.message || JSON.stringify(r));
      await loadStatus();
    } catch (e: any) {
      setMsg(`复评失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function loadDiag() {
    setBusy("diag");
    try {
      const r = await apiRequest("/compute/evolution/mining-diagnostics?days=7");
      setDiag(r);
      const s = r?.l1_signals || {};
      setMsg(
        r?.error
          ? `诊断失败: ${r.error}`
          : `近7日拒因：DSR/PBO ${s.purge_dsr_pbo_reject || 0} · WFO ${s.wfo_reject || 0} · WFO错 ${s.wfo_error || 0} · 容量缺 ${s.capacity_missing || 0} · 测试集门 ${s.test_fail_closed || 0}`,
      );
    } catch (e: any) {
      setMsg(`诊断失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  const rt = status?.runtime || {};
  const running = !!(status?.running || rt.running);
  const auto = !!status?.mining_boost_auto;
  const rep = rt.last_report || {};
  const byState = status?.active_factors?.state_dist || {};
  const recent = status?.recent_activity || [];
  // 运行中不刷旧 abort；空闲时也不把历史 aborted 当红错
  const errText = running
    ? null
    : (() => {
        const e = rt.last_error || status?.last_error;
        if (!e) return null;
        if (String(e).startsWith("aborted:")) return null;
        return e;
      })();

  return (
    <section className="ops-panel ops-area-mining">
      <div className="ops-panel-head">
        <span className="ops-panel-title">挖矿加强 · 运行情况</span>
        <span className={cn("ops-mono", running ? "ops-lag" : "ops-muted")}>
          {running
            ? `运行中 ${rt.period || ""} · ${rt.source || ""} · ${rt.elapsed_sec ?? "—"}s`
            : "空闲"}
        </span>
      </div>
      <div className="ops-panel-body tight">
        <div className="ops-muted" style={{ fontSize: 11, lineHeight: 1.5, marginBottom: 8 }}>
          在这里开「自动加强」即可：以后每天凌晨 3 点（4h）和 4 点（5m）挖矿会自动用加强档，不必每次手点。
          加强档只加大搜索，不降低门禁。
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            className="ops-link-btn"
            disabled={!!busy}
            onClick={() => void setAuto(!auto)}
            title="开启后定时/手动进化前自动套用 mining_boost"
          >
            {busy === "auto" ? "切换中…" : auto ? "自动加强：开" : "自动加强：关"}
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy || running} onClick={() => void check("5m")}>
            预检 5m
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy || running} onClick={() => void check("4h")}>
            预检 4h
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy || running} onClick={() => void trigger("5m")}>
            {busy === "run-5m" ? "已触发…" : "立刻跑 5m"}
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy || running} onClick={() => void trigger("4h")}>
            {busy === "run-4h" ? "已触发…" : "立刻跑 4h"}
          </button>
          <button
            type="button"
            className="ops-link-btn"
            disabled={!!busy || !running}
            onClick={() => void abortRun()}
            title="结束卡住的 quick/进化标记"
          >
            {busy === "abort" ? "结束中…" : "结束卡住任务"}
          </button>
          <button
            type="button"
            className="ops-link-btn"
            disabled={!!busy || running}
            onClick={() => void repromote()}
            title="隔离因子复评，达标回 PAPER"
          >
            {busy === "repromote" ? "复评中…" : "隔离复评晋升"}
          </button>
          <button type="button" className="ops-link-btn" disabled={!!busy} onClick={() => void loadDiag()}>
            {busy === "diag" ? "诊断中…" : "拒因诊断"}
          </button>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: 8,
            marginTop: 10,
            fontSize: 11,
          }}
        >
          <div>
            <div className="ops-muted">自动加强</div>
            <div className="ops-mono">{auto ? "已开" : "未开"}</div>
          </div>
          <div>
            <div className="ops-muted">上次活动</div>
            <div className="ops-mono">{fmtAgo(status?.last_activity_at)}</div>
          </div>
          <div>
            <div className="ops-muted">上次跑完</div>
            <div className="ops-mono">
              {rt.last_period || "—"} · {fmtAgo(rt.last_finished_at)} · {rt.last_elapsed_sec ?? "—"}s
            </div>
          </div>
          <div>
            <div className="ops-muted">上次结果</div>
            <div className="ops-mono">
              {rep.error
                ? `失败 ${rep.error}`
                : `候选${rep.n_candidates ?? "—"}→评${rep.n_evaluated ?? "—"}→晋${rep.n_promoted ?? "—"}`}
            </div>
          </div>
          <div>
            <div className="ops-muted">因子池状态</div>
            <div className="ops-mono">
              活{byState.ACTIVE ?? 0}/纸{byState.PAPER ?? 0}/隔{byState.QUARANTINE ?? 0}
            </div>
          </div>
          <div>
            <div className="ops-muted">定时</div>
            <div className="ops-mono" style={{ fontSize: 10 }}>
              03:00 4h · 04:00 5m
            </div>
          </div>
        </div>

        {errText ? (
          <div className="ops-lag" style={{ marginTop: 8, fontSize: 11 }}>
            错误：{errText}
          </div>
        ) : null}

        {msg ? (
          <div className={cn("ops-muted", msg.includes("失败") && "ops-lag")} style={{ marginTop: 8, fontSize: 11 }}>
            {msg}
          </div>
        ) : null}

        {preflight?.ok_symbols?.length ? (
          <div className="ops-muted" style={{ marginTop: 6, fontSize: 10 }}>
            可用币 {(preflight.ok_symbols || []).join(", ")} · need≈{preflight.need_bars}
          </div>
        ) : null}

        {recent.length ? (
          <div style={{ marginTop: 10 }}>
            <div className="ops-muted" style={{ fontSize: 10, marginBottom: 4 }}>
              最近进化动作
            </div>
            <div className="ops-muted" style={{ fontSize: 10, lineHeight: 1.45 }}>
              {recent.slice(0, 6).map((row, i) => (
                <div key={`${row.created_at}-${i}`}>
                  [{row.action || row.phase}] {row.factor_id || ""} {(row.reason || "").slice(0, 80)}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {diag?.top_reasons?.length ? (
          <div className="ops-muted" style={{ marginTop: 8, fontSize: 10, lineHeight: 1.45 }}>
            {diag.top_reasons.slice(0, 5).map((row, i) => (
              <div key={`${row.action}-${i}`}>
                [{row.bucket}] ×{row.n} {row.reason || row.action}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
