"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { factorsLabApi } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth";
import {
  BookOpen, FlaskConical, Gauge, Loader2, Play, RefreshCw, ShieldCheck,
  Sparkles, FileText, Lightbulb, Activity,
} from "lucide-react";

/**
 * 因子研究中心（factors_lab / ADR-21）面板——五Agent自动闭环。
 * ①文献 → ②假设 → ③因子工程 → ④回测 → ⑤反馈记忆；本面板只读展示 + 管理员操作。
 *
 * [2026-10-01 合并] 用户指令：「合并 因子系统 因子研究中心」。
 * 原 `/factors-lab` 整页并入「因子系统」（`/factors`）作为第 7 个 Tab「研究中心」：
 *   - 本文件 = 原页面的主体（去掉自带 PageHeader 与外层 padding，页头由宿主页统一渲染）；
 *   - 页内 Tab（因子库 / 假设流 / 文献知识卡 / 数据源与配置 / 统一策略）原样保留；
 *   - 旧路由 `/factors-lab` 保留为跳转桩 → `/factors?tab=lab`；
 *   - 后端 `/api/factors-lab/*` 接口不变（见 `lib/api.ts` 的 factorsLabApi）。
 */
type FactorRow = {
  cand_id: string;
  expr_id: string;
  formula?: string | null;
  ast?: unknown;
  note?: string | null;
  hyp_id?: string;
  hypothesis?: string;
  expected_ic_sign?: number | null;
  unit_tests?: { pass?: boolean; reason?: string; finite_ratio?: number } | null;
  unit_status?: string;
  ast_sim_pool?: number | null;
  backtest?: {
    mean_rank_ic?: number; icir?: number; quantile_spread?: number;
    turnover?: number; ic_half_life_bars?: number | null;
    ok?: boolean; round_ts?: number;
  } | null;
  verdict?: string | null;
  applied?: string | null;
  ts?: number;
  ts_ago_min?: number;
};

type HypothesisRow = {
  hyp_id: string; hypothesis?: string; argument?: string; spec?: string;
  observation?: string; knowledge_ref?: string;
  expected_ic_sign?: number; applicable_regime?: string; diversity_note?: string;
  max_recent_jaccard?: number;
  outcome?: { verdict?: string; mode?: string; reason?: string } | null;
  ts?: number;
};

type KnowledgeRow = {
  title?: string; source?: string; url?: string; extract_ok?: boolean;
  card?: { phenomenon?: string; mechanism?: string; testable_claims?: string[];
           applicable_regime?: string; horizon?: string; quality?: number } | null;
  ts?: number;
};

type Status = {
  enabled?: boolean;
  counts?: { knowledge_cards?: number; hypotheses?: number; candidates?: number };
  recent_rounds?: { round_ts?: number; ok?: boolean; elapsed_sec?: number;
                    stats?: Record<string, number> }[];
  latest_round_stats?: Record<string, number>;
};

type UnifiedReport = {
  ok?: boolean; generated_at?: string; week?: number;
  thesis_channel?: { available?: boolean; n_outcomed?: number; hit_rate?: number | null;
                     by_tier?: Record<string, { n: number; hit: number; hit_rate?: number }>;
                     ic_thesis_mixed?: number | null; ic_fused?: number | null; ic_days?: number };
  factor_contribution?: { available?: boolean; n_factors?: number;
                          top3?: [string, number][]; bottom3?: [string, number][]; note?: string };
  paper_live_divergence?: { available?: boolean; summary?: Record<string, unknown> };
  factors_lab?: { available?: boolean; recent_rounds?: unknown[];
                  hypotheses_total?: number; hypotheses_outcomed?: number; candidates_passed?: number };
  guidance?: string;
};

type Config = {
  panel_period?: string;
  budget?: Record<string, number>;
  gates?: Record<string, number>;
  cron?: Record<string, string>;
  sources?: { id: string; kind: string; status: string; url?: string; note?: string }[];
};

const VERDICT_BADGE: Record<string, { label: string; cls: string }> = {
  pass: { label: "通过·待评审", cls: "bg-emerald-500/15 text-emerald-400" },
  weak_ic: { label: "IC不足", cls: "bg-amber-500/15 text-amber-400" },
  crowded: { label: "拥挤", cls: "bg-orange-500/15 text-orange-400" },
  hot_turnover: { label: "换手过高", cls: "bg-orange-500/15 text-orange-400" },
  backtest_fail: { label: "不可回测", cls: "bg-zinc-500/15 text-zinc-400" },
};

function fmtAgo(min?: number) {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)}分钟前`;
  if (min < 1440) return `${Math.round(min / 60)}小时前`;
  return `${Math.round(min / 1440)}天前`;
}

export function FactorLabPanel() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [factors, setFactors] = useState<FactorRow[]>([]);
  const [hypotheses, setHypotheses] = useState<HypothesisRow[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeRow[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [unified, setUnified] = useState<UnifiedReport | null>(null);
  const [feedTitle, setFeedTitle] = useState("");
  const [feedAbstract, setFeedAbstract] = useState("");

  const load = useCallback(async () => {
    try {
      const [st, cfg, lib] = await Promise.all([
        factorsLabApi.status(), factorsLabApi.config(), factorsLabApi.library(150),
      ]);
      setStatus(st); setConfig(cfg);
      try { setUnified(await factorsLabApi.unifiedReport()); } catch { /* 无报告时静默 */ }
      setFactors(lib.factors || []); setHypotheses(lib.hypotheses || []);
      setKnowledge(lib.knowledge || []);
    } catch (e) {
      setMsg(`加载失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  // 首屏加载一次（与合并前 /factors-lab 页逐字相同，行为不变）。
  // 注：react-hooks/set-state-in-effect 会在这行报 error —— 这是**合并前就存在**的既有告警
  // （原文件同一行），随本轮迁移到此文件；与 /factors 页 116 行、/coin-select 等处同类。
  useEffect(() => { load(); }, [load]);

  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    if (!isAdmin) { setMsg("仅管理员可执行"); return; }
    setBusy(key); setMsg(null);
    try {
      await fn(); setMsg(done); await load();
    } catch (e) {
      setMsg(`失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(null); }
  };

  const nPass = factors.filter((f) => f.verdict === "pass").length;
  const nUnitPass = factors.filter((f) => f.unit_status === "unit_pass").length;
  const lastRound = status?.recent_rounds?.[0];

  return (
    <div className="space-y-4">
      {/* 合并前这句话在独立页的 PageHeader 副标题里，现挂在面板顶部，信息不丢 */}
      <div className="text-xs text-muted-foreground">
        ①文献情报 → ②假设生成 → ③因子工程(DSL) → ④回测 → ⑤反馈记忆 · ADR-21 多样性正则 ·
        隔离区不碰实盘，晋级走 REV-P10/P12
      </div>

      {msg && (
        <div className="rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-sm text-blue-300">
          {msg}
        </div>
      )}

      {/* 状态概览 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><Activity className="h-3.5 w-3.5" />闭环</div>
          <div className="mt-1 text-lg font-semibold">
            {status?.enabled ? "运行中" : "已停"}
          </div>
          <div className="text-[11px] text-zinc-500">{config?.cron?.round_daily}</div>
        </Card>
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><FileText className="h-3.5 w-3.5" />文献卡</div>
          <div className="mt-1 text-lg font-semibold">{status?.counts?.knowledge_cards ?? "-"}</div>
        </Card>
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><Lightbulb className="h-3.5 w-3.5" />假设</div>
          <div className="mt-1 text-lg font-semibold">{status?.counts?.hypotheses ?? "-"}</div>
        </Card>
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><FlaskConical className="h-3.5 w-3.5" />因子候选</div>
          <div className="mt-1 text-lg font-semibold">{factors.length}</div>
          <div className="text-[11px] text-zinc-500">单测过 {nUnitPass}</div>
        </Card>
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><ShieldCheck className="h-3.5 w-3.5" />门禁通过</div>
          <div className="mt-1 text-lg font-semibold text-emerald-400">{nPass}</div>
          <div className="text-[11px] text-zinc-500">待 REV-P10/P12</div>
        </Card>
        <Card className="p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400"><Gauge className="h-3.5 w-3.5" />最近轮</div>
          <div className="mt-1 text-lg font-semibold">
            {lastRound ? (lastRound.ok ? `${lastRound.elapsed_sec}s` : "失败") : "-"}
          </div>
          <div className="text-[11px] text-zinc-500">
            {lastRound?.stats
              ? `过${lastRound.stats.pass ?? 0}/拒${lastRound.stats.reject ?? 0}/败${lastRound.stats.fail ?? 0}`
              : ""}
          </div>
        </Card>
      </div>

      {/* 操作区 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={load} disabled={busy === "load"}>
          {busy === "load" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}刷新
        </Button>
        <Button size="sm" onClick={() => act("round", () => factorsLabApi.runRound(true), "闭环轮已触发并完成")}
                disabled={busy === "round"}>
          {busy === "round" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          运行一轮闭环
        </Button>
        <Button variant="outline" size="sm" onClick={() => act("scan", () => factorsLabApi.scanArxiv(), "arXiv 扫描完成")}
                disabled={busy === "scan"}>
          {busy === "scan" ? <Loader2 className="h-4 w-4 animate-spin" /> : <BookOpen className="h-4 w-4" />}arXiv 扫描
        </Button>
        <Button variant="outline" size="sm" onClick={() => act("calib", () => factorsLabApi.calibrate(), "校准回归完成")}
                disabled={busy === "calib"}>
          {busy === "calib" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          校准回归(101)
        </Button>
        <span className="text-xs text-zinc-500">
          门禁: IC≥{config?.gates?.ic_pass ?? "-"} · AST相似≤{config?.gates?.ast_sim ?? "-"} · 假设Jaccard≤{config?.gates?.diversity_jaccard ?? "-"}
        </span>
      </div>

      <Tabs defaultValue="factors">
        <TabsList>
          <TabsTrigger value="factors">因子库（{factors.length}）</TabsTrigger>
          <TabsTrigger value="hypotheses">假设流（{hypotheses.length}）</TabsTrigger>
          <TabsTrigger value="knowledge">文献知识卡（{knowledge.length}）</TabsTrigger>
          <TabsTrigger value="sources">数据源与配置</TabsTrigger>
          <TabsTrigger value="unified">统一策略</TabsTrigger>
        </TabsList>

        {/* 因子库 */}
        <TabsContent value="factors" className="space-y-2">
          <div className="overflow-x-auto rounded-md border border-zinc-800">
            <table className="w-full text-xs">
              <thead className="bg-zinc-900/60 text-zinc-400">
                <tr>
                  <th className="p-2 text-left">因子/描述</th>
                  <th className="p-2 text-left">来源假设</th>
                  <th className="p-2 text-left">公式（DSL）</th>
                  <th className="p-2 text-right">RankIC</th>
                  <th className="p-2 text-right">ICIR</th>
                  <th className="p-2 text-right">换手</th>
                  <th className="p-2 text-right">半衰期</th>
                  <th className="p-2 text-left">单测</th>
                  <th className="p-2 text-left">判定/应用</th>
                  <th className="p-2 text-left">时间</th>
                </tr>
              </thead>
              <tbody>
                {factors.map((f) => {
                  const v = VERDICT_BADGE[f.verdict || ""] || {
                    label: f.verdict || (f.unit_status === "unit_fail" ? "单测未过" : "未评估"),
                    cls: "bg-zinc-500/15 text-zinc-400",
                  };
                  return (
                    <Fragment key={f.cand_id}>
                      <tr className="border-t border-zinc-800/60 hover:bg-zinc-900/40 cursor-pointer"
                          onClick={() => setExpanded(expanded === f.cand_id ? null : f.cand_id)}>
                        <td className="p-2 max-w-[220px]">
                          <div className="font-mono text-[11px] text-zinc-500">{f.expr_id}</div>
                          <div className="truncate text-zinc-200" title={f.note || ""}>{f.note || "-"}</div>
                        </td>
                        <td className="p-2 max-w-[200px] truncate text-zinc-300" title={f.hypothesis}>
                          {f.hypothesis || "-"}
                          {f.expected_ic_sign != null && (
                            <span className="ml-1 text-[10px] text-zinc-500">
                              预期{f.expected_ic_sign > 0 ? "正" : "负"}
                            </span>
                          )}
                        </td>
                        <td className="p-2 max-w-[260px] truncate font-mono text-[11px] text-sky-300" title={f.formula || ""}>
                          {f.formula || "-"}
                        </td>
                        <td className={`p-2 text-right font-mono ${(f.backtest?.mean_rank_ic ?? 0) >= 0.02 ? "text-emerald-400" : "text-zinc-300"}`}>
                          {f.backtest?.mean_rank_ic ?? "-"}
                        </td>
                        <td className="p-2 text-right font-mono text-zinc-300">{f.backtest?.icir ?? "-"}</td>
                        <td className="p-2 text-right font-mono text-zinc-300">{f.backtest?.turnover ?? "-"}</td>
                        <td className="p-2 text-right font-mono text-zinc-300">{f.backtest?.ic_half_life_bars ?? "-"}</td>
                        <td className="p-2">
                          <Badge variant="outline" className={f.unit_status === "unit_pass"
                            ? "border-emerald-500/40 text-emerald-400" : "border-zinc-600 text-zinc-400"}>
                            {f.unit_status === "unit_pass" ? "过" : "未过"}
                          </Badge>
                        </td>
                        <td className="p-2">
                          <Badge className={v.cls}>{v.label}</Badge>
                          {f.applied && <div className="mt-0.5 text-[10px] text-sky-400">{f.applied}</div>}
                        </td>
                        <td className="p-2 text-zinc-500">{fmtAgo(f.ts_ago_min)}</td>
                      </tr>
                      {expanded === f.cand_id && (
                        <tr className="border-t border-zinc-800/60 bg-zinc-950/60">
                          <td colSpan={10} className="p-3">
                            <div className="grid gap-2 md:grid-cols-2">
                              <div>
                                <div className="mb-1 text-[11px] font-semibold text-zinc-400">回测详情</div>
                                <pre className="overflow-x-auto rounded bg-black/40 p-2 text-[11px] text-zinc-300">
{JSON.stringify(f.backtest ?? { note: "未进入回测（单测未过或数据不足）" }, null, 2)}
                                </pre>
                              </div>
                              <div>
                                <div className="mb-1 text-[11px] font-semibold text-zinc-400">单测与AST</div>
                                <div className="text-[11px] text-zinc-400">
                                  单测: {f.unit_tests?.pass ? "通过" : `未过（${f.unit_tests?.reason ?? "?"}）`}
                                  {f.unit_tests?.finite_ratio != null && ` · 有限值率 ${f.unit_tests.finite_ratio}`}
                                  {f.ast_sim_pool != null && ` · 与池AST相似 ${f.ast_sim_pool}`}
                                </div>
                                <pre className="mt-1 max-h-40 overflow-auto rounded bg-black/40 p-2 text-[11px] text-zinc-300">
{JSON.stringify(f.ast, null, 2)}
                                </pre>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {factors.length === 0 && (
                  <tr><td colSpan={10} className="p-6 text-center text-zinc-500">暂无候选因子——点上方「运行一轮闭环」开始</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </TabsContent>

        {/* 假设流 */}
        <TabsContent value="hypotheses" className="space-y-2">
          {hypotheses.map((h) => (
            <Card key={h.hyp_id} className="p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-sm font-medium text-zinc-100">{h.hypothesis}</span>
                <Badge variant="outline" className="text-[10px]">
                  预期{h.expected_ic_sign === -1 ? "负" : "正"}IC
                </Badge>
                <Badge variant="outline" className="text-[10px]">{h.applicable_regime || "any"}</Badge>
                {h.outcome && (
                  <Badge className={(VERDICT_BADGE[h.outcome.mode || ""]?.cls) || "bg-zinc-500/15 text-zinc-400"}>
                    {VERDICT_BADGE[h.outcome.mode || ""]?.label || h.outcome.verdict}
                  </Badge>
                )}
                <span className="ml-auto text-[11px] text-zinc-500">
                  异质度 {h.max_recent_jaccard ?? "-"}
                </span>
              </div>
              <div className="mt-1 text-xs text-zinc-400">论证：{h.argument}</div>
              <div className="mt-0.5 text-xs text-zinc-500">约束：{h.spec} · 差异：{h.diversity_note}</div>
              {h.outcome?.reason && <div className="mt-0.5 text-[11px] text-zinc-500">结果：{h.outcome.reason}</div>}
            </Card>
          ))}
          {hypotheses.length === 0 && <div className="p-6 text-center text-sm text-zinc-500">暂无假设</div>}
        </TabsContent>

        {/* 文献知识卡 */}
        <TabsContent value="knowledge" className="space-y-2">
          {knowledge.map((k, i) => (
            <Card key={i} className="p-3">
              <div className="flex flex-wrap items-center gap-2">
                <BookOpen className="h-3.5 w-3.5 text-sky-400" />
                <span className="text-sm font-medium text-zinc-100">{k.title}</span>
                <Badge variant="outline" className="text-[10px]">{k.source}</Badge>
                <Badge variant="outline" className={k.extract_ok
                  ? "border-emerald-500/40 text-emerald-400" : "border-zinc-600 text-zinc-400"}>
                  {k.extract_ok ? "已抽取" : "仅元数据"}
                </Badge>
                {k.url && (
                  <a href={k.url} target="_blank" rel="noreferrer" className="text-[11px] text-sky-400 hover:underline">
                    原文 ↗
                  </a>
                )}
                {k.card?.quality != null && (
                  <span className="text-[11px] text-zinc-500">质量 {k.card.quality}</span>
                )}
              </div>
              {k.card && (
                <div className="mt-1 space-y-0.5 text-xs">
                  <div className="text-zinc-300">现象：{k.card.phenomenon}</div>
                  <div className="text-zinc-400">机制：{k.card.mechanism}</div>
                  {k.card.testable_claims?.length ? (
                    <div className="text-zinc-500">可检验命题：{k.card.testable_claims.join("；")}</div>
                  ) : null}
                  <div className="text-zinc-500">适用：{k.card.applicable_regime} · 周期：{k.card.horizon}</div>
                </div>
              )}
            </Card>
          ))}
          {knowledge.length === 0 && <div className="p-6 text-center text-sm text-zinc-500">暂无知识卡——arXiv 扫描或人工投喂</div>}
          {/* 人工投喂 */}
          <Card className="p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Sparkles className="h-3.5 w-3.5 text-amber-400" />人工投喂文献
            </div>
            <div className="space-y-2">
              <Input placeholder="论文标题" value={feedTitle} onChange={(e) => setFeedTitle(e.target.value)} />
              <Input placeholder="摘要（≥30字；版权纪律：不贴付费全文，只贴公开摘要）"
                     value={feedAbstract} onChange={(e) => setFeedAbstract(e.target.value)} />
              <Button size="sm" disabled={busy === "feed" || !isAdmin}
                      onClick={() => act("feed", () => factorsLabApi.feed({ title: feedTitle, abstract: feedAbstract }),
                        "投喂成功（LLM 知识卡已抽取）")}>
                {busy === "feed" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}投喂并抽取知识卡
              </Button>
            </div>
          </Card>
        </TabsContent>

        {/* 统一策略（因子 × LLM alpha · 周外循环） */}
        <TabsContent value="unified" className="space-y-2">
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline"
                    onClick={() => act("unified", () => factorsLabApi.unifiedRun(), "周外循环报告已生成")}
                    disabled={busy === "unified"}>
              {busy === "unified" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
              生成周外循环报告
            </Button>
            <span className="text-xs text-zinc-500">
              {unified?.generated_at ? `最近：${unified.generated_at.slice(0, 16).replace("T", " ")} · W${unified.week ?? "-"}` : "尚未生成"}
            </span>
          </div>
          {unified?.ok ? (
            <>
              <Card className="p-3">
                <div className="mb-1 flex items-center gap-2 text-sm font-medium">
                  <Sparkles className="h-3.5 w-3.5 text-amber-400" />本周统一指导
                </div>
                <div className="text-sm text-zinc-200">{unified.guidance}</div>
              </Card>
              <div className="grid gap-2 md:grid-cols-3">
                <Card className="p-3">
                  <div className="text-xs text-zinc-400">thesis 通道（A1 臂）</div>
                  <div className="mt-1 text-lg font-semibold">
                    {unified.thesis_channel?.available
                      ? (unified.thesis_channel?.hit_rate != null ? `${(unified.thesis_channel.hit_rate * 100).toFixed(0)}%` : "积累中")
                      : "不可用"}
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    n={unified.thesis_channel?.n_outcomed ?? 0}
                    {unified.thesis_channel?.ic_thesis_mixed != null && ` · IC=${unified.thesis_channel.ic_thesis_mixed}`}
                  </div>
                </Card>
                <Card className="p-3">
                  <div className="text-xs text-zinc-400">因子逐单归因（近N日）</div>
                  <div className="mt-1 text-sm">
                    {unified.factor_contribution?.available ? (
                      <div className="space-y-0.5 text-[11px]">
                        {(unified.factor_contribution?.top3 || []).map(([k, v]) => (
                          <div key={k} className="text-emerald-400">🟢 {k} {v > 0 ? "+" : ""}{(v * 100).toFixed(1)}%</div>
                        ))}
                        {(unified.factor_contribution?.bottom3 || []).map(([k, v]) => (
                          <div key={k} className="text-red-400">🔴 {k} {(v * 100).toFixed(1)}%</div>
                        ))}
                      </div>
                    ) : <span className="text-zinc-500">{unified.factor_contribution?.note || "样本积累中"}</span>}
                  </div>
                </Card>
                <Card className="p-3">
                  <div className="text-xs text-zinc-400">研究闭环 / 模拟偏差</div>
                  <div className="mt-1 text-sm text-zinc-200">
                    因子候选通过 {unified.factors_lab?.candidates_passed ?? 0}
                    （假设 {unified.factors_lab?.hypotheses_outcomed ?? 0}/{unified.factors_lab?.hypotheses_total ?? 0} 已判）
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    模拟-实盘偏差档案：{unified.paper_live_divergence?.available ? "在档" : "暂无"}
                  </div>
                </Card>
              </div>
            </>
          ) : (
            <div className="p-6 text-center text-sm text-zinc-500">
              尚无周报告——点上方按钮生成（每周一 08:15 自动产出）
            </div>
          )}
        </TabsContent>

        {/* 数据源与配置 */}
        <TabsContent value="sources" className="space-y-2">
          <div className="text-xs text-zinc-400">
            预算：每轮 ≤{config?.budget?.max_hypotheses} 假设 × {config?.budget?.candidates_per_hypothesis} 候选 ·
            宇宙 top{config?.budget?.universe_n} · 超时 {config?.budget?.round_timeout_sec}s ·
            面板周期 {config?.panel_period}
          </div>
          {config?.sources?.map((s) => (
            <Card key={s.id} className="flex flex-wrap items-center gap-2 p-3">
              <Badge className={s.status === "active"
                ? "bg-emerald-500/15 text-emerald-400" : "bg-zinc-500/15 text-zinc-400"}>
                {s.status === "active" ? "在用" : "预留"}
              </Badge>
              <span className="text-sm font-medium text-zinc-100">{s.id}</span>
              <Badge variant="outline" className="text-[10px]">{s.kind}</Badge>
              <span className="text-xs text-zinc-400">{s.url}</span>
              <span className="text-xs text-zinc-500">{s.note}</span>
            </Card>
          ))}
        </TabsContent>
      </Tabs>
    </div>
  );
}
