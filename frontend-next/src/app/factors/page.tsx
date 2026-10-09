"use client";

import { Suspense, useCallback, useEffect, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { BookOpen, FlaskConical, RefreshCw, LayoutDashboard, Pickaxe, Gauge, HeartPulse, LineChart } from "lucide-react";
import { cn } from "@/lib/utils";
import FactorOverviewPanel from "@/components/factors/FactorOverviewPanel";
import { FactorLabPanel } from "@/components/factors/FactorLabPanel";
import { OpsMidlongFactors } from "@/components/ops/OpsMidlongFactors";
import { PulsePanel, PoolPanel, FunnelPanel, GatePanel, LongPanel, LlmProposePanel } from "@/components/factors/FactorSystemPanels";
import "@/app/ops/ops.css";

// [2026-10-01 合并] 用户指令：「合并 因子系统 因子研究中心」。
// 原 `/factors-lab` 独立页并入本页，成为第 7 个 Tab「研究中心」（面板见
// components/factors/FactorLabPanel.tsx）；侧栏只保留一个入口「因子系统」；
// 旧路由 `/factors-lab` 保留为跳转桩 → `/factors?tab=lab`；后端 /api/factors-lab/* 不变。
type Tab = "overview" | "mining" | "values" | "reports" | "gate" | "long" | "lab";
const TAB_KEYS: Tab[] = ["overview", "mining", "values", "reports", "gate", "long", "lab"];

type Factor = {
  name: string;
  value: number;
  normalized: number;
  category: string;
};

const FALLBACK_SYMBOLS = ["BTC", "ETH", "SOL", "BNB"];

const TABS: { id: Tab; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "总览", icon: LayoutDashboard },
  { id: "mining", label: "弹药生产", icon: Pickaxe },
  { id: "values", label: "实时因子值", icon: LineChart },
  { id: "reports", label: "因子报告卡", icon: FlaskConical },
  { id: "gate", label: "门禁", icon: Gauge },
  { id: "long", label: "长线规则", icon: HeartPulse },
  // 合并自原「因子研究中心」页（五Agent闭环：文献→假设→因子工程→回测→反馈）
  { id: "lab", label: "研究中心", icon: BookOpen },
];

export default function FactorsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-xs text-muted-foreground">加载中…</div>}>
      <FactorsPageInner />
    </Suspense>
  );
}

function FactorsPageInner() {
  const searchParams = useSearchParams();
  // 深链：`?tab=` 决定首屏 Tab（旧 /factors-lab 跳转桩带的就是 ?tab=lab），非法值回落总览。
  // 惰性初始化而非 useEffect+setState（避免 react-hooks/set-state-in-effect）。
  const [tab, setTab] = useState<Tab>(() => {
    const t = (searchParams.get("tab") || "").toLowerCase();
    return (TAB_KEYS as string[]).includes(t) ? (t as Tab) : "overview";
  });
  const [symbols, setSymbols] = useState<string[]>(FALLBACK_SYMBOLS);
  const [symbol, setSymbol] = useState<string>("BTC");
  const [factors, setFactors] = useState<Factor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 切 Tab 时把 `?tab=` 同步进地址栏（刷新/复制链接保持同一视图）；静态导出下 replaceState 最稳。 */
  const switchTab = useCallback((k: Tab) => {
    setTab(k);
    if (typeof window === "undefined") return;
    const url = `${window.location.pathname}?tab=${k}`;
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history.replaceState(null, "", url);
    }
  }, []);

  const load = async (sym: string) => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`/api/factors/values/${sym}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setFactors(Array.isArray(json?.factors) ? json.factors : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setFactors([]);
    } finally {
      setLoading(false);
    }
  };

  // 固定币候选池：会话固定币（无 AI 选币也全量出现）；失败回退 BTC/ETH/SOL/BNB
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/ops/training");
        if (!res.ok) return;
        const json = await res.json();
        const pool: string[] = (json?.fixed_pool?.symbols || [])
          .map((s: unknown) => String(s).toUpperCase())
          .filter(Boolean);
        if (cancelled || pool.length === 0) return;
        setSymbols(pool);
        setSymbol((prev) => (pool.includes(prev) ? prev : pool[0]));
      } catch {
        // 保持回退币种
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (tab === "values") void load(symbol);
  }, [tab, symbol]);

  // 按类别分组
  const grouped = useMemo(() => {
    const map = new Map<string, Factor[]>();
    for (const f of factors) {
      const cat = f.category ?? "其他";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(f);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [factors]);

  // 展示层派生值（归一化概览）
  const overview = useMemo(() => {
    const maxAbs = factors.reduce((m, f) => Math.max(m, Math.abs(f.normalized)), 0) || 1;
    const pos = factors.filter((f) => f.normalized > 0).length;
    const neg = factors.filter((f) => f.normalized < 0).length;
    const top = [...factors]
      .sort((a, b) => Math.abs(b.normalized) - Math.abs(a.normalized))
      .slice(0, 8);
    const BINS = 5;
    const bins = new Array<number>(BINS).fill(0);
    for (const f of factors) {
      const x = (f.normalized + maxAbs) / (2 * maxAbs);
      const idx = Math.min(BINS - 1, Math.max(0, Math.floor(x * BINS)));
      bins[idx]++;
    }
    return { maxAbs, pos, neg, zero: factors.length - pos - neg, top, bins };
  }, [factors]);

  const maxCount = Math.max(1, ...overview.bins);

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<FlaskConical className="w-4 h-4" />}
        title="因子系统"
        subtitle="因子生命周期：挖掘 → 因子池 → 门禁 → 活跃 · 实时因子值 · 报告卡 · 研究中心（①文献→②假设→③因子工程→④回测→⑤反馈）"
        breadcrumb={[{ label: "市场 & 分析" }, { label: "因子系统" }]}
        refreshHint="15s 轮询 · 评估任务按需触发"
        actions={
          <Button variant="outline" size="sm" className="btn-glow" onClick={() => tab === "values" && load(symbol)} disabled={loading}>
            <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
            刷新
          </Button>
        }
      />

      {/* Tab 导航 */}
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <Button
              key={t.id}
              variant={tab === t.id ? "default" : "outline"}
              size="sm"
              className="text-xs h-8"
              onClick={() => switchTab(t.id)}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </Button>
          );
        })}
      </div>

      {/* 币种带：values / reports 两视图联动用 */}
      {(tab === "values" || tab === "reports") && (
        <Card className="p-3 glass">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground mr-1">币种（固定币候选池）</span>
            {symbols.map((s) => (
              <Button
                key={s}
                variant={symbol === s ? "default" : "outline"}
                size="sm"
                className="font-mono text-xs h-7"
                onClick={() => setSymbol(s)}
              >
                {s}
              </Button>
            ))}
          </div>
        </Card>
      )}

      {/* ── 总览 ── */}
      {tab === "overview" && (
        <div className="space-y-4">
          <PulsePanel />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <PoolPanel />
            <FunnelPanel />
          </div>
        </div>
      )}

      {/* ── 弹药生产（复用运维台中线因子组件）── */}
      {tab === "mining" && (
        <div className="space-y-4">
          <LlmProposePanel />
          <div className="ops-scope rounded-xl border border-border/40 p-3">
            <OpsMidlongFactors />
          </div>
        </div>
      )}

      {/* ── 实时因子值 ── */}
      {tab === "values" && (
        <>
          {error && (
            <Card className="p-3 text-xs text-loss bg-loss/5 border-loss/30">加载失败：{error}</Card>
          )}
          {loading ? (
            <Card className="p-6 text-center text-xs text-muted-foreground">加载中...</Card>
          ) : grouped.length === 0 ? (
            <Card className="p-6 text-center text-xs text-muted-foreground">暂无因子数据</Card>
          ) : (
            <>
              {/* 玻璃卡片 grid（2:1）：归一化概览 + 因子分布 */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <Card className="p-4 glass lg:col-span-2">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <FlaskConical className="w-4 h-4 text-cyan-300" />
                      归一化概览
                    </div>
                    <Badge variant="secondary" className="text-xs tabular-nums">
                      {symbol} · {factors.length} 因子
                    </Badge>
                  </div>
                  <div className="flex items-center gap-4 mb-3 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-profit" /> 正向
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-loss" /> 负向
                    </span>
                    <span className="ml-auto">以 |归一化| 最大值为满刻度</span>
                  </div>
                  <div className="space-y-3">
                    {overview.top.map((f) => (
                      <div key={f.name}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="flex items-baseline gap-2 min-w-0">
                            <span className="font-mono text-xs font-medium truncate">{f.name}</span>
                            <span className="text-xs text-muted-foreground tabular-nums">raw {formatNum(f.value)}</span>
                          </span>
                          <span className={cn("text-xs font-medium tabular-nums", f.normalized > 0 ? "text-profit" : f.normalized < 0 ? "text-loss" : "text-muted-foreground")}>
                            {f.normalized > 0 ? "+" : ""}
                            {formatNum(f.normalized)}
                          </span>
                        </div>
                        <div className="relative h-2 rounded-full bg-white/5">
                          <div className="absolute left-1/2 top-0 bottom-0 w-px bg-slate-500/60" />
                          {f.normalized > 0 && (
                            <div className="absolute left-1/2 top-0 bottom-0 rounded-r-full bg-profit/70" style={{ width: `${Math.min(50, (f.normalized / overview.maxAbs) * 50)}%` }} />
                          )}
                          {f.normalized < 0 && (
                            <div className="absolute right-1/2 top-0 bottom-0 rounded-l-full bg-loss/70" style={{ width: `${Math.min(50, (Math.abs(f.normalized) / overview.maxAbs) * 50)}%` }} />
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>

                <Card className="p-4 glass">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <FlaskConical className="w-4 h-4 text-violet-300" />
                      因子分布
                    </div>
                    <Badge variant="secondary" className="text-xs">归一化直方图</Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-2 mb-4">
                    <div className="rounded-lg bg-white/5 px-2 py-2 text-center">
                      <div className="text-sm font-bold tabular-nums text-profit">{overview.pos}</div>
                      <div className="text-xs text-muted-foreground">正向</div>
                    </div>
                    <div className="rounded-lg bg-white/5 px-2 py-2 text-center">
                      <div className="text-sm font-bold tabular-nums text-loss">{overview.neg}</div>
                      <div className="text-xs text-muted-foreground">负向</div>
                    </div>
                    <div className="rounded-lg bg-white/5 px-2 py-2 text-center">
                      <div className="text-sm font-bold tabular-nums text-warning">{overview.zero}</div>
                      <div className="text-xs text-muted-foreground">中性</div>
                    </div>
                  </div>
                  <div className="flex items-end gap-1.5 h-28">
                    {overview.bins.map((c, i) => (
                      <div key={i} className="flex-1 h-full flex flex-col items-center justify-end gap-1">
                        <span className="text-xs text-muted-foreground tabular-nums">{c}</span>
                        <div className="w-full rounded-t-sm bg-gradient-to-t from-cyan-400/80 to-violet-500/80" style={{ height: `${Math.max(4, (c / maxCount) * 100)}%` }} />
                      </div>
                    ))}
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground mt-1.5">
                    <span>负向</span>
                    <span>0</span>
                    <span>正向</span>
                  </div>
                </Card>
              </div>

              {/* 全部因子表格 */}
              <Card className="p-0 overflow-hidden glass">
                <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50 bg-muted/30">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <FlaskConical className="w-4 h-4 text-cyan-300" />
                    全部因子
                  </div>
                  <Badge variant="secondary" className="text-xs tabular-nums">
                    {factors.length} 因子 · {overview.pos} 正向 / {overview.neg} 负向
                  </Badge>
                </div>
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th className="font-medium">名称</th>
                        <th className="font-medium">类别</th>
                        <th className="font-medium r">原始值</th>
                        <th className="font-medium r">归一化</th>
                        <th className="font-medium c">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grouped.map(([category, items]) =>
                        items.map((f) => (
                          <tr key={`${category}-${f.name}`} className="border-b border-border/30">
                            <td className="px-4 py-2.5 font-mono text-xs">{f.name}</td>
                            <td className="px-4 py-2.5">
                              <Badge variant="secondary" className="bg-muted text-muted-foreground text-xs">{category}</Badge>
                            </td>
                            <td className="px-4 py-2.5 text-right num text-muted-foreground">{formatNum(f.value)}</td>
                            <td className="px-4 py-2.5 text-right">
                              <span className={cn("num font-medium", f.normalized > 0 ? "text-profit" : f.normalized < 0 ? "text-loss" : "text-muted-foreground")}>
                                {f.normalized > 0 ? "+" : ""}
                                {formatNum(f.normalized)}
                              </span>
                            </td>
                            <td className="px-4 py-2.5 text-center">
                              <Badge variant="secondary" className={cn("text-xs font-medium", f.normalized > 0 ? "text-profit bg-profit/10" : f.normalized < 0 ? "text-loss bg-loss/10" : "text-warning bg-warning/10")}>
                                {f.normalized > 0 ? "正向" : f.normalized < 0 ? "负向" : "中性"}
                              </Badge>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )}
        </>
      )}

      {/* ── 因子报告卡 ── */}
      {tab === "reports" && <FactorOverviewPanel symbol={symbol} />}

      {/* ── 门禁 ── */}
      {tab === "gate" && <GatePanel />}

      {/* ── 长线规则 ── */}
      {tab === "long" && <LongPanel />}

      {/* ── 研究中心（2026-10-01 合并自原 /factors-lab 页） ── */}
      {tab === "lab" && <FactorLabPanel />}
    </div>
  );
}

function formatNum(n: number): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  if (Math.abs(n) >= 1) return n.toFixed(3);
  return n.toFixed(5);
}