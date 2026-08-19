"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Gauge, TrendingUp, FlaskConical, AlertTriangle, HeartPulse, Pickaxe } from "lucide-react";

// ── 类型 ───────────────────────────────────────────────

type Pulse = {
  heartbeat_ok?: number; heartbeat_total?: number; tradable_factors?: number;
  candidates_pass?: number; funnel_promoted_7d?: number; fixed_symbol_count?: number;
  ai_symbols?: number; ai_pass_24h?: number; evo_4h_last?: string | null; evo_5m_last?: string | null;
  fixed_symbols?: string[];
};

type PoolItem = { factor_id?: string; state?: string; icir?: number | null; last_net_ic?: number | null; online_weight?: number | null; activated_at?: string | null; };
type PoolData = { view?: string; total?: number; items?: PoolItem[]; state_dist?: Record<string, number>; counts?: Record<string, number>; quarantine_reasons?: { reason?: string; n?: number }[]; pipeline_health?: { dsr?: { required?: boolean; min_symbols?: number; max_pbo?: number } }; callout?: string; };

type FunnelData = { days?: number; counts?: Record<string, number>; rejects?: { factor_id?: string; action?: string; reason?: string; created_at?: string }[]; error?: string; };
type LongSymbol = { symbol?: string; state?: string; score?: number; strength?: number; close?: number; note?: string; };
type GateConfig = { lookback?: number; lookback_1d?: number; fwd_4h?: number; fwd_1d?: number; min_sharpe?: number; active_max?: number; research_enabled?: boolean; };

function num(v: unknown, digits = 2): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function ageHuman(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

const STATE_COLOR: Record<string, string> = { ACTIVE: "text-profit", PAPER: "text-info", SMALL_LIVE: "text-warning", RESEARCH: "text-warning", QUARANTINE: "text-loss" };

// ── A. 因子脉搏 KPI 带 ─────────────────────────────────

export function PulsePanel() {
  const [pulse, setPulse] = useState<Pulse | null>(null);
  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ops/pipeline");
      if (!res.ok) return;
      const json = await res.json();
      setPulse(json?.pulse || {});
    } catch { /* 保持旧值 */ }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const kpis: { label: string; value: string; tone?: string }[] = [
    { label: "可交易因子", value: pulse?.tradable_factors != null ? String(pulse.tradable_factors) : "—" },
    { label: "候选通过", value: pulse?.candidates_pass != null ? String(pulse.candidates_pass) : "—" },
    { label: "7 天晋升", value: pulse?.funnel_promoted_7d != null ? String(pulse.funnel_promoted_7d) : "—", tone: pulse?.funnel_promoted_7d ? "text-profit" : undefined },
    { label: "固定币", value: pulse?.fixed_symbol_count != null ? String(pulse.fixed_symbol_count) : "—" },
    { label: "AI 选币", value: pulse?.ai_symbols != null ? String(pulse.ai_symbols) : "—" },
    { label: "AI 24h 通过", value: pulse?.ai_pass_24h != null ? String(pulse.ai_pass_24h) : "—" },
    { label: "心跳", value: pulse?.heartbeat_ok != null && pulse?.heartbeat_total != null ? `${pulse.heartbeat_ok}/${pulse.heartbeat_total}` : "—", tone: pulse?.heartbeat_ok === pulse?.heartbeat_total ? "text-profit" : "text-loss" },
    { label: "进化 4h", value: ageHuman(pulse?.evo_4h_last) },
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
      {kpis.map((k) => (
        <Card key={k.label} className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1 truncate">{k.label}</div>
          <div className={cn("text-lg font-bold tabular-nums", k.tone)}>{k.value}</div>
        </Card>
      ))}
    </div>
  );
}

// ── C. 因子池状态（tradable / research / quarantine）──

export function PoolPanel() {
  const [view, setView] = useState<"tradable" | "research" | "quarantine">("tradable");
  const [data, setData] = useState<PoolData | null>(null);
  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/ops/factor-pool?view=${view}&limit=50`);
      if (!res.ok) return;
      setData(await res.json());
    } catch { /* 保持旧值 */ }
  }, [view]);
  useEffect(() => { void load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const views: { id: "tradable" | "research" | "quarantine"; label: string; n?: number }[] = [
    { id: "tradable", label: "可交易", n: data?.counts?.tradable },
    { id: "research", label: "研究", n: data?.counts?.research },
    { id: "quarantine", label: "隔离", n: data?.counts?.quarantine },
  ];
  const dsr = data?.pipeline_health?.dsr;
  return (
    <Card className="p-4 glass">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Gauge className="w-4 h-4 text-cyan-300" />
          因子池状态
        </div>
        <div className="flex gap-1">
          {views.map((v) => (
            <button key={v.id} type="button" onClick={() => setView(v.id)} className={cn(
              "px-2.5 py-1 rounded-md text-xs border transition-colors",
              view === v.id ? "border-primary bg-primary/15 text-primary" : "border-border text-muted-foreground hover:text-foreground"
            )}>
              {v.label}{v.n != null ? ` ${v.n}` : ""}
            </button>
          ))}
        </div>
      </div>

      {data?.callout ? (
        <div className="text-[10px] text-muted-foreground mb-2">{data.callout}</div>
      ) : null}

      {data?.quarantine_reasons && data.quarantine_reasons.length > 0 ? (
        <div className="flex flex-wrap gap-2 mb-3">
          {data.quarantine_reasons.map((q, i) => (
            <Badge key={i} variant="secondary" className="text-[10px] text-loss bg-loss/5 border-loss/30">
              {q.reason} × {q.n}
            </Badge>
          ))}
        </div>
      ) : null}

      {data?.items && data.items.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border/50">
                <th className="px-3 py-2 font-medium">因子</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium text-right">ICIR</th>
                <th className="px-3 py-2 font-medium text-right">净 IC</th>
                <th className="px-3 py-2 font-medium text-right">权重</th>
                <th className="px-3 py-2 font-medium">激活</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => (
                <tr key={it.factor_id} className="border-b border-border/30">
                  <td className="px-3 py-2 font-mono text-xs max-w-[180px] truncate" title={it.factor_id}>{it.factor_id}</td>
                  <td className="px-3 py-2"><span className={cn("text-xs font-medium", STATE_COLOR[it.state || ""])}>{it.state}</span></td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(it.icir, 3)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(it.last_net_ic, 4)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(it.online_weight, 2)}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{it.activated_at ? it.activated_at.slice(0, 10) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-6 text-center text-xs text-muted-foreground">该视图暂无因子</div>
      )}

      {dsr ? (
        <div className="mt-3 flex flex-wrap gap-3 text-[10px] text-muted-foreground border-t border-border/50 pt-2">
          <span>门禁 DSR: {dsr.required ? "开" : "关"}</span>
          <span>min_symbols: {dsr.min_symbols}</span>
          <span>max_pbo: {dsr.max_pbo}</span>
        </div>
      ) : null}
    </Card>
  );
}

// ── E. 进化漏斗 ────────────────────────────────────────

export function FunnelPanel() {
  const [data, setData] = useState<FunnelData | null>(null);
  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ops/evolution-funnel?days=7");
      if (!res.ok) return;
      setData(await res.json());
    } catch { /* 保持旧值 */ }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  const counts = data?.counts || {};
  const entries = Object.entries(counts);
  const max = Math.max(1, ...entries.map(([, n]) => Number(n) || 0));
  return (
    <Card className="p-4 glass">
      <div className="flex items-center gap-2 text-sm font-medium mb-3">
        <TrendingUp className="w-4 h-4 text-cyan-300" />
        进化漏斗（7 天）
      </div>
      {entries.length > 0 ? (
        <div className="space-y-2 mb-3">
          {entries.map(([action, n]) => (
            <div key={action} className="flex items-center gap-2 text-xs">
              <span className="w-28 truncate font-mono text-muted-foreground">{action}</span>
              <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                <div className="h-full rounded-full bg-info" style={{ width: `${Math.max(3, ((Number(n) || 0) / max) * 100)}%` }} />
              </div>
              <span className="w-10 text-right tabular-nums">{n}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground mb-3">{data?.error || "暂无进化记录"}</div>
      )}
      {data?.rejects && data.rejects.length > 0 ? (
        <div className="border-t border-border/50 pt-2 space-y-1">
          <div className="text-[10px] text-muted-foreground mb-1">最近拒绝</div>
          {data.rejects.slice(0, 8).map((r, i) => (
            <div key={i} className="text-xs flex gap-2">
              <span className="font-mono text-muted-foreground truncate max-w-[140px]">{r.factor_id}</span>
              <span className="text-loss shrink-0">{r.action}</span>
              <span className="text-muted-foreground truncate">{r.reason}</span>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

// ── H. 门禁可视化 ──────────────────────────────────────

export function GatePanel() {
  const [gate, setGate] = useState<GateConfig | null>(null);
  const [preflight, setPreflight] = useState<{ ok?: boolean; need_bars?: Record<string, number>; min_bars?: Record<string, number>; insufficient?: Record<string, string[]> } | null>(null);
  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ops/midlong-factors");
      if (!res.ok) return;
      const json = await res.json();
      setGate(json?.gate_config || null);
      setPreflight(json?.preflight || null);
    } catch { /* 保持旧值 */ }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  const rows: { label: string; value: string }[] = [
    { label: "回看目标", value: `${gate?.lookback ?? "—"} (4h) / ${gate?.lookback_1d ?? "—"} (1d)` },
    { label: "前瞻", value: `4h×${gate?.fwd_4h ?? "—"} · 1d×${gate?.fwd_1d ?? "—"}` },
    { label: "min_sharpe", value: String(gate?.min_sharpe ?? "—") },
    { label: "活跃上限", value: String(gate?.active_max ?? "—") },
    { label: "研究模式", value: gate?.research_enabled ? "开" : "关" },
    { label: "最小可用根数", value: preflight?.min_bars ? `4h≥${preflight.min_bars["4h"] ?? "—"} · 1d≥${preflight.min_bars["1d"] ?? "—"}` : "—" },
    { label: "预检", value: preflight?.ok === undefined ? "—" : preflight.ok ? "✅ 可挖（自适应回看）" : "⚠ 数据不足" },
  ];
  const insuff = preflight?.insufficient ? Object.entries(preflight.insufficient).filter(([, v]) => v.length > 0).map(([tf, v]) => `${tf}: ${v.join(",")}`).join(" · ") : "";
  return (
    <Card className="p-4 glass">
      <div className="flex items-center gap-2 text-sm font-medium mb-3">
        <FlaskConical className="w-4 h-4 text-cyan-300" />
        admission 门禁
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {rows.map((r) => (
          <div key={r.label}>
            <div className="text-[10px] text-muted-foreground mb-1">{r.label}</div>
            <div className="text-sm font-medium tabular-nums">{r.value}</div>
          </div>
        ))}
      </div>
      {insuff ? (
        <div className="mt-3 text-[11px] text-loss flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" />
          {insuff}
        </div>
      ) : null}
      <div className="mt-3 text-[10px] text-muted-foreground border-t border-border/50 pt-2">
        门禁 fail-closed：候选 → 4h/1d 样本外回测（IC/ICIR/OOS Sharpe + DSR/PBO 多重检验）→ A/B 级晋升 active → 周度复检退役。
      </div>
    </Card>
  );
}

// ── I. 长线规则 ────────────────────────────────────────

export function LongPanel() {
  const [data, setData] = useState<{ enabled?: boolean; symbols?: LongSymbol[] } | null>(null);
  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/ops/long-trend-v2");
      if (!res.ok) return;
      setData(await res.json());
    } catch { /* 保持旧值 */ }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);
  const syms = data?.symbols || [];
  return (
    <Card className="p-4 glass">
      <div className="flex items-center gap-2 text-sm font-medium mb-3">
        <HeartPulse className="w-4 h-4 text-cyan-300" />
        长线规则 long_trend_v2
        <Badge variant="secondary" className="text-[10px]">{data?.enabled ? "启用" : "关闭"}</Badge>
      </div>
      {syms.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border/50">
                <th className="px-3 py-2 font-medium">币</th>
                <th className="px-3 py-2 font-medium">L1 状态</th>
                <th className="px-3 py-2 font-medium text-right">score</th>
                <th className="px-3 py-2 font-medium text-right">strength</th>
                <th className="px-3 py-2 font-medium">说明</th>
              </tr>
            </thead>
            <tbody>
              {syms.map((s) => (
                <tr key={s.symbol} className="border-b border-border/30">
                  <td className="px-3 py-2 font-mono text-xs">{s.symbol}</td>
                  <td className="px-3 py-2"><span className={cn("text-xs font-medium", s.state === "up" ? "text-profit" : s.state === "down" ? "text-loss" : "text-muted-foreground")}>{s.state}</span></td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(s.score, 1)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(s.strength, 2)}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground max-w-[260px] truncate" title={s.note}>{s.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-6 text-center text-xs text-muted-foreground">暂无长线币或规则关闭</div>
      )}
    </Card>
  );
}

// ── P4. LLM 提案层（被拒因子迭代 → 公式候选，同门禁）──

type LlmProposeResult = {
  ok?: boolean;
  proposed?: number;
  registered?: number;
  rejected?: number;
  skipped?: string;
  error?: string;
};

export function LlmProposePanel() {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<LlmProposeResult | null>(null);

  const propose = async (tier: "midlong" | "scalp") => {
    setBusy(tier);
    setResult(null);
    try {
      const res = await fetch(`/api/ops/factors/llm-propose?tier=${tier}&k=8`, { method: "POST" });
      setResult((await res.json()) as LlmProposeResult);
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="p-4 glass">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Pickaxe className="w-4 h-4 text-cyan-300" />
          LLM 提案层
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className={cn("px-2.5 py-1 rounded-md text-xs border transition-colors", busy ? "opacity-50" : "border-primary bg-primary/15 text-primary hover:bg-primary/25")}
            disabled={!!busy}
            onClick={() => void propose("midlong")}
          >
            {busy === "midlong" ? "提案中…" : "LLM 提案（中线 4h/1d）"}
          </button>
          <button
            type="button"
            className={cn("px-2.5 py-1 rounded-md text-xs border transition-colors", busy ? "opacity-50" : "border-border text-muted-foreground hover:text-foreground")}
            disabled={!!busy}
            onClick={() => void propose("scalp")}
          >
            {busy === "scalp" ? "提案中…" : "LLM 提案（短线 1h）"}
          </button>
        </div>
      </div>
      <div className="text-[10px] text-muted-foreground mb-2">
        每周后台自动 + 手动触发：LLM 读最近被拒因子的结构化病灶（IC/ICIR/PBO/DSR）迭代变体，输出 numpy 公式候选进候选池，走同一道门禁且 LLM 源更严（PBO≤0.4），符号与声明不符直接拒。
      </div>
      {result ? (
        result.error ? (
          <div className="text-[11px] text-loss">{result.error}</div>
        ) : result.skipped ? (
          <div className="text-[11px] text-muted-foreground">{result.skipped}</div>
        ) : (
          <div className="text-[11px] tabular-nums">
            <span className="text-profit">注册 {result.registered ?? 0}</span>
            {" · "}
            <span className="text-loss">拒绝 {result.rejected ?? 0}</span>
            {" · 提案 "}
            {result.proposed ?? 0}
            <span className="text-muted-foreground ml-2">（候选进入弹药生产候选池）</span>
          </div>
        )
      ) : null}
    </Card>
  );
}
