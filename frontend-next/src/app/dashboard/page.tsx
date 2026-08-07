"use client";

import { Card } from "@/components/ui/card";
import { Loader2 } from "lucide-react";
import { useAccounts, usePositions, useSessions, usePaperBalance } from "@/hooks/useTradingData";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { EquityCurve } from "@/components/charts/EquityCurve";
import { getAccessToken } from "@/lib/stores/auth";

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export default function DashboardPage() {
  const { data: accounts, isLoading: accountsLoading } = useAccounts();
  const { data: sessions } = useSessions();
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [period, setPeriod] = useState("24H");
  const [curvePeriod, setCurvePeriod] = useState<"7d" | "30d" | "all">("7d");

  const paperAccounts = useMemo(() => {
    if (!accounts) return [];
    return accounts.filter((a: any) => a.trading_mode === "paper").sort((a: any, b: any) => b.id - a.id);
  }, [accounts]);

  const activeAccountId = selectedAccountId ?? paperAccounts[0]?.id ?? 14;
  const { data: balance } = usePaperBalance(activeAccountId);
  const { data: positions } = usePositions(activeAccountId, "open");
  const activeSession = sessions?.find((s: any) => s.status === "running");

  const { data: tierStatus } = useQuery({
    queryKey: ["tier-status", activeSession?.session_id],
    queryFn: () =>
      fetch(`/api/full-auto/tier-status/${activeSession!.session_id}`, { headers: authHeaders() }).then((r) => r.json()),
    enabled: !!activeSession?.session_id, staleTime: 30_000, refetchInterval: 60_000,
  });

  const { data: tierActivity } = useQuery({
    queryKey: ["tier-activity", activeSession?.session_id],
    queryFn: () =>
      fetch(`/api/full-auto/tier-activity/${activeSession!.session_id}`, { headers: authHeaders() }).then((r) => r.json()),
    enabled: !!activeSession?.session_id, staleTime: 15_000, refetchInterval: 30_000,
  });

  const { data: strategies } = useQuery({
    queryKey: ["strategies", activeAccountId],
    queryFn: () =>
      fetch(`/api/strategies?account_id=${activeAccountId}&status=active`, { headers: authHeaders() })
        .then((r) => r.json())
        .catch(() => []),
    enabled: !!activeAccountId, staleTime: 30_000,
  });

  const openPositions = positions ?? [];
  const totalUnrealizedPnl = openPositions.reduce((s: number, p: any) => s + (p.unrealized_pnl || 0), 0);
  const tiers = tierStatus?.tiers;
  const totalEquity = balance?.total_equity ?? 0;
  const realizedPnl = balance?.realized_pnl ?? 0;
  const feePaid = balance?.total_fee_paid ?? 0;
  const availableBalance = balance?.available_balance ?? 0;
  const longCount = openPositions.filter((p: any) => p.side === "long").length;
  const shortCount = openPositions.filter((p: any) => p.side === "short").length;

  if (accountsLoading) return <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  // P&L 归因
  const scalpPnl = openPositions.filter((p: any) => p.trade_nature === "scalp").reduce((s: number, p: any) => s + (p.unrealized_pnl || 0), 0);
  const swingPnl = openPositions.filter((p: any) => p.trade_nature === "swing").reduce((s: number, p: any) => s + (p.unrealized_pnl || 0), 0);
  const trendPnl = openPositions.filter((p: any) => p.trade_nature === "trend_follow").reduce((s: number, p: any) => s + (p.unrealized_pnl || 0), 0);
  const grossPnl = Math.abs(scalpPnl) + Math.abs(swingPnl) + Math.abs(trendPnl) || 1;

  return (
    <div className="flex flex-col gap-2.5 min-w-[1024px]">
      {/* Page Header */}
      <div className="flex items-center justify-between min-h-8">
        <div className="flex items-baseline gap-2.5">
          <h1 className="text-base font-semibold tracking-tight">仪表盘</h1>
          <span className="text-[11px] text-muted-foreground">· 实时账户视图 · {new Date().toLocaleString("zh-CN")}</span>
        </div>
        <div className="flex items-center gap-2">
          {activeSession && (
            <span className="inline-flex items-center gap-1.5 h-[18px] px-1.5 rounded-full text-[11px] font-medium bg-profit/15 text-profit">
              <span className="w-1 h-1 rounded-full bg-profit" style={{ boxShadow: "0 0 6px currentColor" }} />{activeSession.status}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 h-[18px] px-1.5 rounded-full text-[11px] font-medium bg-warning/15 text-warning">
            <span className="w-1 h-1 rounded-full bg-warning" />paper
          </span>
          {paperAccounts.length > 0 && (
            <select value={activeAccountId} onChange={(e) => setSelectedAccountId(Number(e.target.value))} className="bg-card border border-border text-xs rounded px-2 py-0.5 h-[26px]">
              {paperAccounts.map((a: any) => (<option key={a.id} value={a.id}>{a.name}</option>))}
            </select>
          )}
        </div>
      </div>

      {/* P&L Attribution */}
      <div className="bg-card border border-border rounded-md px-3 py-2.5 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-muted-foreground uppercase tracking-widest font-medium">今日 P&L 归因</span>
          <div className="flex gap-3 text-[10px] font-mono">
            <span className="text-muted-foreground">毛收益</span>
            <span className={totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}>{totalUnrealizedPnl >= 0 ? "+" : ""}${Math.abs(totalUnrealizedPnl).toFixed(2)}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">手续费</span>
            <span className="text-loss">−${feePaid.toFixed(2)}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">净 P&L</span>
            <span className={totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}>{totalUnrealizedPnl >= 0 ? "+" : ""}${(totalUnrealizedPnl - feePaid).toFixed(2)}</span>
          </div>
        </div>
        <div className="flex h-2 rounded-sm overflow-hidden bg-muted/20">
          <div className="bg-primary" style={{ width: `${(Math.abs(scalpPnl) / grossPnl) * 100}%` }} title={`短线 scalp: ${scalpPnl >= 0 ? "+" : ""}$${scalpPnl.toFixed(2)}`} />
          <div className="bg-profit" style={{ width: `${(Math.abs(swingPnl) / grossPnl) * 100}%` }} title={`中线 swing: ${swingPnl >= 0 ? "+" : ""}$${swingPnl.toFixed(2)}`} />
          <div className="bg-warning" style={{ width: `${(Math.abs(trendPnl) / grossPnl) * 100}%` }} title={`长线 trend: ${trendPnl >= 0 ? "+" : ""}$${trendPnl.toFixed(2)}`} />
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-muted-foreground font-mono">
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-primary" />短线 scalp<span className={scalpPnl >= 0 ? "text-profit" : "text-loss"}>{scalpPnl >= 0 ? "+" : ""}${scalpPnl.toFixed(2)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-profit" />中线 swing<span className={swingPnl >= 0 ? "text-profit" : "text-loss"}>{swingPnl >= 0 ? "+" : ""}${swingPnl.toFixed(2)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-warning" />长线 trend<span className={trendPnl >= 0 ? "text-profit" : "text-loss"}>{trendPnl >= 0 ? "+" : ""}${trendPnl.toFixed(2)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-loss" />手续费<span className="text-loss">−${feePaid.toFixed(2)}</span></span>
        </div>
      </div>

      {/* Section: Core Metrics · 24H */}
      <div>
        <div className="flex items-center justify-between mt-2 mb-1">
          <span className="text-[9px] font-medium text-muted-foreground uppercase tracking-widest">核心指标 · {period}</span>
          <div className="flex gap-0.5">
            {["LIVE", "1H", "24H", "7D", "30D"].map((p) => (
              <button key={p} onClick={() => setPeriod(p)} className={`text-[9px] px-1.5 py-0.5 rounded tracking-wider border transition-colors ${period === p ? "bg-primary/10 text-primary border-primary/25" : "text-muted-foreground border-transparent hover:text-foreground"}`}>
                {p === "LIVE" && <span className="inline-block w-1 h-1 rounded-full bg-primary mr-1" style={{ boxShadow: "0 0 4px currentColor" }} />}{p}
              </button>
            ))}
          </div>
        </div>
        {/* 8 KPI Ribbon */}
        <div className="bg-card border border-border rounded-md overflow-hidden grid grid-cols-8">
          <MetricCell label="总权益" value={`$${totalEquity.toFixed(2)}`} delta={`${balance?.return_pct ? (balance.return_pct * 100).toFixed(2) : "0"}%`} deltaColor={(balance?.return_pct ?? 0) >= 0 ? "profit" : "loss"} />
          <MetricCell label="浮动 PnL" value={`${totalUnrealizedPnl >= 0 ? "+" : ""}$${totalUnrealizedPnl.toFixed(2)}`} deltaColor={totalUnrealizedPnl >= 0 ? "profit" : "loss"} />
          <MetricCell label="已实现 PnL" value={`${realizedPnl >= 0 ? "+" : ""}$${realizedPnl.toFixed(2)}`} deltaColor={realizedPnl >= 0 ? "profit" : "loss"} />
          <MetricCell label="可用余额" value={`$${availableBalance.toFixed(2)}`} />
          <MetricCell label="持仓" value={String(openPositions.length)} delta={`${longCount}多 ${shortCount}空`} deltaColor="muted" />
          <MetricCell label="手续费" value={`$${feePaid.toFixed(2)}`} deltaColor="loss" />
          <MetricCell label="胜率" value={tierStatus?.win_rate ? `${(tierStatus.win_rate * 100).toFixed(1)}%` : "—"} deltaColor="muted" />
          <MetricCell label="交易笔数" value={String(tierStatus?.total_trades || "—")} deltaColor="muted" />
        </div>
      </div>

      {/* Section: Tier Cards */}
      {tiers && (
        <div>
          <div className="flex items-center justify-between mt-2 mb-1">
            <span className="text-[9px] font-medium text-muted-foreground uppercase tracking-widest">三周期状态</span>
            <div className="flex gap-0.5">
              <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">预算分配</button>
              <button className="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/25">风险敞口</button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(["short", "long"] as const).map((tierKey) => {
              const t = tiers[tierKey];
              if (!t) return null;
              const colors: Record<string, string> = { short: "#6366f1", long: "#eab308" };
              const labels: Record<string, string> = { short: "短线 Scalp", long: "长线 Trend (含中周期)" };
              const tierPositions = openPositions.filter((p: any) => (tierKey === "short" && p.trade_nature === "scalp") || (tierKey === "long" && (p.trade_nature === "trend_follow" || p.trade_nature === "swing" || p.trade_nature === "position")));
              const tierPnl = tierPositions.reduce((s: number, p: any) => s + (p.unrealized_pnl || 0), 0);
              const acts = tierActivity?.[tierKey] || [];
              const lastAct = acts[acts.length - 1];
              return (
                <Card key={tierKey} className="p-2 border-border flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 text-[13px] font-medium">
                      <span className="w-1.5 h-1.5 rounded-sm" style={{ background: colors[tierKey] }} />{labels[tierKey]}
                    </div>
                    <div className="text-[9px] text-muted-foreground font-mono">{t.active_strategies || 0} 策略 · {tierPositions.length} 持仓</div>
                  </div>
                  <div className="grid grid-cols-4">
                    <TierStat value={`${tierPnl >= 0 ? "+" : ""}$${tierPnl.toFixed(2)}`} label="PnL" color={tierPnl >= 0 ? "profit" : "loss"} />
                    <TierStat value={t.budget_used ? `$${t.budget_used}` : "—"} label="保证" />
                    <TierStat value={t.drawdown ? `${t.drawdown}%` : "—"} label="DD" color="loss" />
                    <TierStat value={t.win_rate ? `${t.win_rate}%` : "—"} label="胜率" />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-mono text-muted-foreground">预算</span>
                    <div className="flex-1 h-[3px] bg-muted/30 rounded-sm overflow-hidden">
                      <div className="h-full rounded-sm" style={{ width: `${Math.min(t.budget_pct || 25, 100)}%`, background: colors[tierKey] }} />
                    </div>
                    <span className="text-[9px] font-mono text-muted-foreground">{t.budget_pct || 25}%</span>
                  </div>
                  <div className="flex flex-wrap gap-0.5">
                    {tierPositions.length > 0 ? ([...new Set(tierPositions.map((p: any) => p.symbol))] as string[]).map((sym: string) => (
                      <span key={sym} className="text-[9px] leading-none px-1 py-0.5 rounded-sm bg-muted/30 text-muted-foreground font-mono">{sym}</span>
                    )) : <span className="text-[9px] text-muted-foreground">无持仓</span>}
                  </div>
                  {lastAct && <div className="text-[10px] text-muted-foreground leading-snug mt-0.5">{lastAct.time} {lastAct.symbol} {lastAct.action} {(lastAct.reasoning || "").slice(0, 80)}</div>}
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* 3-col: Equity + Positions + AI Decisions */}
      <div className="grid grid-cols-[2fr_1fr_1fr] gap-2">
        {/* Equity Curve */}
        <Card className="p-2.5 border-border flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[13px] font-medium">权益曲线</h2>
            <div className="flex gap-0.5">
              {([
                ["7D", "7d"],
                ["30D", "30d"],
                ["ALL", "all"],
              ] as const).map(([label, value]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCurvePeriod(value)}
                  className={`text-[9px] px-1.5 py-0.5 rounded ${
                    curvePeriod === value
                      ? "bg-primary/15 text-primary border border-primary/30"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex gap-3.5 mb-1">
            <div><div className="text-base font-semibold font-mono">${totalEquity.toFixed(2)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">当前</div></div>
            <div><div className={`text-base font-semibold font-mono ${totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}`}>{totalUnrealizedPnl >= 0 ? "+" : ""}${totalUnrealizedPnl.toFixed(2)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">浮动</div></div>
            <div><div className={`text-base font-semibold font-mono ${realizedPnl >= 0 ? "text-profit" : "text-loss"}`}>{realizedPnl >= 0 ? "+" : ""}${realizedPnl.toFixed(2)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">已实现</div></div>
            <div><div className="text-base font-semibold font-mono">${feePaid.toFixed(2)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">手续费</div></div>
          </div>
          {activeAccountId && <EquityCurve accountId={activeAccountId} period={curvePeriod} height={160} />}
        </Card>

        {/* Positions */}
        <Card className="p-2.5 border-border flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[13px] font-medium">当前持仓 <span className="text-muted-foreground font-mono text-[10px] font-normal">({openPositions.length})</span></h2>
          </div>
          <div className="flex flex-col max-h-[280px] overflow-y-auto">
            {openPositions.length === 0 ? <div className="text-center text-muted-foreground text-xs py-4">无持仓</div> : openPositions.map((p: any) => (
              <div key={p.id} className="grid grid-cols-[1.4fr_0.7fr_0.9fr_1fr] gap-1.5 py-1.5 border-b border-border/20 items-center text-xs last:border-0">
                <div className="font-semibold font-mono">{p.symbol}</div>
                <div><span className={`text-[9px] px-1 py-0.5 rounded inline-block text-center ${p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{p.side === "long" ? "多" : "空"}</span></div>
                <div className="text-right font-mono text-[10px] text-muted-foreground">{p.trade_nature}</div>
                <div className={`text-right font-mono ${(p.unrealized_pnl || 0) >= 0 ? "text-profit" : "text-loss"}`}>{(p.unrealized_pnl || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl || 0).toFixed(2)}</div>
              </div>
            ))}
          </div>
        </Card>

        {/* AI Decision Stream */}
        <Card className="p-2.5 border-border flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[13px] font-medium">AI 决策流</h2>
          </div>
          <div className="flex flex-col gap-1 overflow-y-auto max-h-[200px]">
            {[...(tierActivity?.short || []).slice(-4), ...(tierActivity?.mid || []).slice(-2), ...(tierActivity?.long || []).slice(-2)].map((a: any, i: number) => (
              <div key={i} className="py-1.5 border-b border-border/20 last:border-0">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[9px] text-muted-foreground font-mono">{a.time}</span>
                  <span className={`text-[9px] px-1 py-0.5 rounded font-medium ${a.action === "开多" ? "bg-profit/15 text-profit" : a.action === "开空" ? "bg-loss/15 text-loss" : "bg-muted/30 text-muted-foreground"}`}>{a.action} {a.symbol}</span>
                </div>
                <div className="text-xs leading-snug text-foreground/85">{(a.reasoning || "").slice(0, 100)}</div>
              </div>
            ))}
            {!tierActivity && <div className="text-center text-muted-foreground text-xs py-4">加载中...</div>}
          </div>
        </Card>
      </div>

      {/* Section: Strategies Table */}
      <div>
        <div className="flex items-center justify-between mt-2 mb-1">
          <span className="text-[9px] font-medium text-muted-foreground uppercase tracking-widest">活跃策略</span>
          <div className="flex gap-0.5">
            <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">全部</button>
            <button className="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/25">运行中</button>
            <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">已暂停</button>
          </div>
        </div>
        <Card className="border-border p-0 overflow-hidden">
          <div className="max-h-[360px] overflow-auto">
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-card z-10">
              <tr className="border-b border-border">
                <th className="text-left font-medium text-[9px] text-muted-foreground uppercase tracking-wider px-3 py-1.5">策略</th>
                <th className="text-left font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">周期</th>
                <th className="text-left font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">标的</th>
                <th className="text-left font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">方向</th>
                <th className="text-right font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">入场</th>
                <th className="text-right font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">杠杆</th>
                <th className="text-right font-medium text-[9px] text-muted-foreground uppercase tracking-wider py-1.5">PnL</th>
                <th className="text-right font-medium text-[9px] text-muted-foreground uppercase tracking-wider px-3 py-1.5">状态</th>
              </tr>
            </thead>
            <tbody>
              {openPositions.length > 0 ? openPositions.map((p: any) => (
                <tr key={p.id} className="border-b border-border/20 hover:bg-muted/20">
                  <td className="px-3 py-2 font-mono text-xs">{p.strategy_id?.slice(0, 20) || "—"}</td>
                  <td className="py-2"><span className={p.trade_nature === "scalp" ? "text-primary" : p.trade_nature === "swing" ? "text-profit" : "text-warning"}>{p.trade_nature}</span></td>
                  <td className="py-2 font-mono font-semibold text-xs">{p.symbol}</td>
                  <td className="py-2"><span className={`text-[9px] px-1 py-0.5 rounded ${p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{p.side === "long" ? "多" : "空"}</span></td>
                  <td className="text-right py-2 font-mono">{p.entry_price?.toFixed(2) || "—"}</td>
                  <td className="text-right py-2 font-mono">{p.leverage || "—"}x</td>
                  <td className={`text-right py-2 font-mono ${(p.unrealized_pnl || 0) >= 0 ? "text-profit" : "text-loss"}`}>{(p.unrealized_pnl || 0) >= 0 ? "+" : ""}{(p.unrealized_pnl || 0).toFixed(2)}</td>
                  <td className="px-3 py-2"><span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-profit/15 text-profit"><span className="w-1 h-1 rounded-full bg-profit" style={{ boxShadow: "0 0 4px currentColor" }} />running</span></td>
                </tr>
              )) : (
                <tr><td colSpan={8} className="text-center text-muted-foreground py-4">暂无活跃策略</td></tr>
              )}
            </tbody>
          </table>
          </div>
        </Card>
      </div>

      {/* Section: Risk Ribbon */}
      <div>
        <div className="flex items-center justify-between mt-2 mb-1">
          <span className="text-[9px] font-medium text-muted-foreground uppercase tracking-widest">风险敞口</span>
        </div>
        <div className="bg-card border border-border rounded-md overflow-hidden grid grid-cols-6">
          <RiskCell label="当前敞口" value={`$${(openPositions.reduce((s: number, p: any) => s + (p.margin || 0), 0)).toFixed(0)}`} ctx={`/ $${totalEquity.toFixed(0)} 上限`} />
          <RiskCell label="浮动盈亏" value={`${totalUnrealizedPnl >= 0 ? "+" : ""}$${totalUnrealizedPnl.toFixed(2)}`} valueColor={totalUnrealizedPnl >= 0 ? "profit" : "loss"} ctx={`${openPositions.length} 持仓 · 平均 $${openPositions.length > 0 ? (totalUnrealizedPnl / openPositions.length).toFixed(0) : 0}`} />
          <RiskCell label="已实现 PnL" value={`${realizedPnl >= 0 ? "+" : ""}$${realizedPnl.toFixed(2)}`} valueColor={realizedPnl >= 0 ? "profit" : "loss"} />
          <RiskCell label="手续费" value={`$${feePaid.toFixed(2)}`} valueColor="loss" />
          <RiskCell label="多空比" value={`${longCount} / ${shortCount}`} ctx={`${longCount + shortCount} 总持仓`} />
          <RiskCell label="活跃策略" value={String(tiers ? (tiers.short?.active_strategies || 0) + (tiers.mid?.active_strategies || 0) + (tiers.long?.active_strategies || 0) : "—")} />
        </div>
      </div>
    </div>
  );
}

function MetricCell({ label, value, delta, deltaColor = "muted" }: { label: string; value: string; delta?: string; deltaColor?: string }) {
  const colors: Record<string, string> = { profit: "text-profit", loss: "text-loss", muted: "text-muted-foreground" };
  return (
    <div className="p-2 border-r border-border/20 last:border-r-0 flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className="text-base font-semibold font-mono tracking-tight leading-tight">{value}</div>
      {delta && <div className={`text-[9px] font-mono ${colors[deltaColor] || ""}`}>{delta}</div>}
    </div>
  );
}
function TierStat({ value, label, color }: { value: string; label: string; color?: string }) {
  const colors: Record<string, string> = { profit: "text-profit", loss: "text-loss" };
  return (
    <div className="text-left pl-1.5 first:pl-0 border-r border-border/10 last:border-r-0 py-1">
      <div className={`text-[13px] font-semibold font-mono leading-tight ${color ? colors[color] || "" : ""}`}>{value}</div>
      <div className="text-[9px] text-muted-foreground mt-0.5 uppercase tracking-wider">{label}</div>
    </div>
  );
}
function RiskCell({ label, value, ctx, valueColor }: { label: string; value: string; ctx?: string; valueColor?: string }) {
  const colors: Record<string, string> = { profit: "text-profit", loss: "text-loss" };
  return (
    <div className="p-2 px-3 border-r border-border/20 last:border-r-0 flex flex-col gap-0.5 min-w-0">
      <span className="text-[9px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      <span className={`text-sm font-semibold font-mono ${valueColor ? colors[valueColor] || "" : ""}`}>{value}</span>
      {ctx && <span className="text-[9px] text-muted-foreground font-mono">{ctx}</span>}
    </div>
  );
}
