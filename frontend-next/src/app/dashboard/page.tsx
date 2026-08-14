"use client";

import { Card } from "@/components/ui/card";
import { Loader2 } from "lucide-react";
import {
  useAccounts, usePositions, useSessions, usePaperBalance,
  useTierStatus, useTierActivity,
} from "@/hooks/useTradingData";
import { useMemo, useState } from "react";
import { EquityCurve } from "@/components/charts/EquityCurve";
import {
  KpiCell, TierStatCell, RiskCell, SectionHeader, StatusBadge, EmptyState,
} from "@/components/trading/cells";
import { DecisionTimeline } from "@/components/trading/DecisionTimeline";
import { BlockReportPanel } from "@/components/trading/BlockReportPanel";
import { CooldownMatrixPanel, BlockEventStream } from "@/components/trading/CooldownMatrixPanel";
import type { Account, Position } from "@/types/api";

const TIER_KEYS = ["short", "mid", "long"] as const;
type TierKey = (typeof TIER_KEYS)[number];

const TIER_COLORS: Record<TierKey, string> = { short: "#6366f1", mid: "#22c55e", long: "#eab308" };
const TIER_LABELS: Record<TierKey, string> = {
  short: "短线 Scalp",
  mid: "中线(因子化)",
  long: "固定长线",
};

function positionTierOf(p: Position, tierKey: TierKey): boolean {
  if (tierKey === "short") return p.trade_nature === "scalp";
  if (tierKey === "mid") return p.trade_nature === "swing" || p.timeframe_tier === "mid";
  return p.trade_nature === "trend_follow" || p.trade_nature === "position" || p.timeframe_tier === "long";
}

function fmtMoney(v: number): string {
  return `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`;
}

export default function DashboardPage() {
  const { data: accounts, isLoading: accountsLoading } = useAccounts();
  const { data: sessions } = useSessions();
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [period, setPeriod] = useState("24H");
  const [curvePeriod, setCurvePeriod] = useState<"7d" | "30d" | "all">("7d");

  const paperAccounts: Account[] = useMemo(() => {
    if (!accounts) return [];
    return (accounts as Account[])
      .filter((a) => a.trading_mode === "paper")
      .sort((a, b) => b.id - a.id);
  }, [accounts]);

  // R5-2 魔法数字修复：不再回退到硬编码账户 ID=14；
  // 无 paper 账户时 activeAccountId=null，页面渲染空态引导。
  const activeAccountId: number | null = selectedAccountId ?? paperAccounts[0]?.id ?? null;

  const { data: balance } = usePaperBalance(activeAccountId);
  const { data: positions } = usePositions(activeAccountId, "open");
  const activeSession = (sessions ?? []).find((s) => s.status === "running");

  const { data: tierStatus } = useTierStatus(activeSession?.session_id);
  const { data: tierActivity } = useTierActivity(activeSession?.session_id);

  const openPositions: Position[] = positions ?? [];
  const totalUnrealizedPnl = openPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const tiers = tierStatus?.tiers;
  // F8：活跃策略数取真实契约字段（旧 active_strategies 字段不存在，恒显示 0）
  const activeStrategyCount = tiers
    ? TIER_KEYS.reduce((s, k) => s + (tiers[k]?.active_count ?? 0), 0)
    : 0;
  const totalEquity = balance?.total_equity ?? 0;
  const realizedPnl = balance?.realized_pnl ?? 0;
  const feePaid = balance?.total_fee_paid ?? 0;
  const availableBalance = balance?.available_balance ?? 0;
  const longCount = openPositions.filter((p) => p.side === "long").length;
  const shortCount = openPositions.filter((p) => p.side === "short").length;

  if (accountsLoading) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;
  }

  if (paperAccounts.length === 0) {
    // 无 paper 账户：显式空态（旧逻辑会硬编码显示账户 14 的数据）
    return (
      <div className="flex flex-col gap-2.5 min-w-[1024px]">
        <div className="flex items-center justify-between min-h-8">
          <h1 className="text-base font-semibold tracking-tight">仪表盘</h1>
        </div>
        <Card className="border-border">
          <EmptyState
            title="暂无模拟交易账户"
            description="创建 Paper 账户后即可查看权益曲线、持仓与三周期状态。"
            action={
              <a
                href="/paper-trading"
                className="text-xs px-3 py-1.5 rounded-md bg-primary/15 text-primary border border-primary/30 hover:bg-primary/25 transition-colors"
              >
                去创建账户
              </a>
            }
          />
        </Card>
      </div>
    );
  }

  // P&L 归因
  const scalpPnl = openPositions.filter((p) => p.trade_nature === "scalp").reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const swingPnl = openPositions.filter((p) => p.trade_nature === "swing").reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const trendPnl = openPositions.filter((p) => p.trade_nature === "trend_follow").reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
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
          {activeSession && <StatusBadge tone="profit" glow>{activeSession.status}</StatusBadge>}
          <StatusBadge tone="warning" glow>paper</StatusBadge>
          {paperAccounts.length > 0 && (
            <select
              value={activeAccountId ?? ""}
              onChange={(e) => setSelectedAccountId(Number(e.target.value))}
              aria-label="选择模拟账户"
              className="bg-card border border-border text-xs rounded px-2 py-0.5 h-[26px]"
            >
              {paperAccounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
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
            <span className={totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}>{fmtMoney(totalUnrealizedPnl)}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">手续费</span>
            <span className="text-loss">−${feePaid.toFixed(2)}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground">净 P&L</span>
            <span className={totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}>{fmtMoney(totalUnrealizedPnl - feePaid)}</span>
          </div>
        </div>
        <div className="flex h-2 rounded-sm overflow-hidden bg-muted/20">
          <div className="bg-primary" style={{ width: `${(Math.abs(scalpPnl) / grossPnl) * 100}%` }} title={`短线 scalp: ${fmtMoney(scalpPnl)}`} />
          <div className="bg-profit" style={{ width: `${(Math.abs(swingPnl) / grossPnl) * 100}%` }} title={`中线 swing: ${fmtMoney(swingPnl)}`} />
          <div className="bg-warning" style={{ width: `${(Math.abs(trendPnl) / grossPnl) * 100}%` }} title={`长线 trend: ${fmtMoney(trendPnl)}`} />
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-muted-foreground font-mono">
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-primary" />短线 scalp<span className={scalpPnl >= 0 ? "text-profit" : "text-loss"}>{fmtMoney(scalpPnl)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-profit" />中线 swing<span className={swingPnl >= 0 ? "text-profit" : "text-loss"}>{fmtMoney(swingPnl)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-warning" />长线 trend<span className={trendPnl >= 0 ? "text-profit" : "text-loss"}>{fmtMoney(trendPnl)}</span></span>
          <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm bg-loss" />手续费<span className="text-loss">−${feePaid.toFixed(2)}</span></span>
        </div>
      </div>

      {/* Section: Core Metrics · 24H */}
      <div>
        <SectionHeader title={`核心指标 · ${period}`}>
          <div className="flex gap-0.5">
            {["LIVE", "1H", "24H", "7D", "30D"].map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`text-[9px] px-1.5 py-0.5 rounded tracking-wider border transition-colors ${period === p ? "bg-primary/10 text-primary border-primary/25" : "text-muted-foreground border-transparent hover:text-foreground"}`}
              >
                {p === "LIVE" && <span className="inline-block w-1 h-1 rounded-full bg-primary mr-1" style={{ boxShadow: "0 0 4px currentColor" }} />}{p}
              </button>
            ))}
          </div>
        </SectionHeader>
        {/* 8 KPI Ribbon */}
        <div className="bg-card border border-border rounded-md overflow-hidden grid grid-cols-8">
          <KpiCell label="总权益" value={`$${totalEquity.toFixed(2)}`} delta={`${balance?.return_pct ? (balance.return_pct * 100).toFixed(2) : "0"}%`} deltaColor={(balance?.return_pct ?? 0) >= 0 ? "profit" : "loss"} />
          <KpiCell label="浮动 PnL" value={fmtMoney(totalUnrealizedPnl)} deltaColor={totalUnrealizedPnl >= 0 ? "profit" : "loss"} />
          <KpiCell label="已实现 PnL" value={fmtMoney(realizedPnl)} deltaColor={realizedPnl >= 0 ? "profit" : "loss"} />
          <KpiCell label="可用余额" value={`$${availableBalance.toFixed(2)}`} />
          <KpiCell label="持仓" value={String(openPositions.length)} delta={`${longCount}多 ${shortCount}空`} deltaColor="muted" />
          <KpiCell label="手续费" value={`$${feePaid.toFixed(2)}`} deltaColor="loss" />
          <KpiCell label="活跃策略" value={String(activeStrategyCount)} deltaColor="muted" />
          <KpiCell label="中线AI选币" value={tierStatus?.auto_coin_mid_enabled ? "开启" : "关闭"} deltaColor={tierStatus?.auto_coin_mid_enabled ? "profit" : "muted"} />
        </div>
      </div>

      {/* Section: Tier Cards */}
      {tiers && (
        <div>
          <SectionHeader title="三周期状态">
            <div className="flex gap-0.5">
              <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">预算分配</button>
              <button className="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/25">风险敞口</button>
            </div>
          </SectionHeader>
          <div className="grid grid-cols-3 gap-2">
            {TIER_KEYS.map((tierKey) => {
              const t = tiers[tierKey];
              if (!t) return null;
              const tierPositions = openPositions.filter((p) => positionTierOf(p, tierKey));
              const tierPnl = tierPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
              const acts = tierActivity?.[tierKey] ?? [];
              const lastAct = acts[acts.length - 1];
              return (
                <Card key={tierKey} className="p-2 border-border flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 text-[13px] font-medium">
                      <span className="w-1.5 h-1.5 rounded-sm" style={{ background: TIER_COLORS[tierKey] }} />{TIER_LABELS[tierKey]}
                    </div>
                    <div className="text-[9px] text-muted-foreground font-mono">
                      {t.active_count ?? 0}/{t.strategy_count ?? 0} 策略 · {t.position_count ?? tierPositions.length} 持仓
                    </div>
                  </div>
                  <div className="grid grid-cols-4">
                    <TierStatCell value={fmtMoney(tierPnl)} label="PnL" color={tierPnl >= 0 ? "profit" : "loss"} />
                    <TierStatCell value={t.margin_used != null ? `$${Number(t.margin_used).toFixed(0)}` : "—"} label="保证金" />
                    <TierStatCell value={t.budget_allocated != null ? `$${Number(t.budget_allocated).toFixed(0)}` : "—"} label="预算" />
                    <TierStatCell value={t.budget_utilization != null ? `${Number(t.budget_utilization).toFixed(1)}%` : "—"} label="占用" color={Number(t.budget_utilization ?? 0) > 80 ? "loss" : undefined} />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-mono text-muted-foreground">预算占用</span>
                    <div className="flex-1 h-[3px] bg-muted/30 rounded-sm overflow-hidden">
                      <div className="h-full rounded-sm" style={{ width: `${Math.min(Number(t.budget_utilization) || 0, 100)}%`, background: TIER_COLORS[tierKey] }} />
                    </div>
                    <span className="text-[9px] font-mono text-muted-foreground">{t.budget_utilization ?? 0}%</span>
                  </div>
                  <div className="flex flex-wrap gap-0.5">
                    {tierPositions.length > 0 ? (
                      [...new Set(tierPositions.map((p) => p.symbol))].map((sym) => (
                        <span key={sym} className="text-[9px] leading-none px-1 py-0.5 rounded-sm bg-muted/30 text-muted-foreground font-mono">{sym}</span>
                      ))
                    ) : <span className="text-[9px] text-muted-foreground">无持仓</span>}
                  </div>
                  {lastAct && (
                    <div className="text-[10px] text-muted-foreground leading-snug mt-0.5">
                      {lastAct.time} {lastAct.symbol} {lastAct.action}
                      {" "}{lastAct.allowed === true && <span className="text-profit">✓放行</span>}
                      {lastAct.allowed === false && <span className="text-loss">⛔拦截</span>}
                      {lastAct.block_reason && <span className="text-loss"> · {lastAct.block_reason.slice(0, 50)}</span>}
                      {lastAct.reasoning && <span className="text-muted-foreground/70"> · {(lastAct.reasoning || "").slice(0, 60)}</span>}
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Section: P0-D 冷却/门禁透明化 */}
      {activeSession?.session_id && (
        <div>
          <SectionHeader title="冷却与门禁透明化">
            <span className="text-[9px] text-muted-foreground font-mono">P0-D · 只读 · 10s 轮询</span>
          </SectionHeader>
          <div className="grid grid-cols-2 gap-2">
            <CooldownMatrixPanel sessionId={activeSession.session_id} />
            <BlockEventStream sessionId={activeSession.session_id} />
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
            <div><div className={`text-base font-semibold font-mono ${totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss"}`}>{fmtMoney(totalUnrealizedPnl)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">浮动</div></div>
            <div><div className={`text-base font-semibold font-mono ${realizedPnl >= 0 ? "text-profit" : "text-loss"}`}>{fmtMoney(realizedPnl)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">已实现</div></div>
            <div><div className="text-base font-semibold font-mono">${feePaid.toFixed(2)}</div><div className="text-[9px] text-muted-foreground uppercase tracking-wider">手续费</div></div>
          </div>
          {activeAccountId != null && <EquityCurve accountId={activeAccountId} period={curvePeriod} height={160} />}
        </Card>

        {/* Positions */}
        <Card className="p-2.5 border-border flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h2 className="text-[13px] font-medium">当前持仓 <span className="text-muted-foreground font-mono text-[10px] font-normal">({openPositions.length})</span></h2>
          </div>
          <div className="flex flex-col max-h-[280px] overflow-y-auto">
            {openPositions.length === 0 ? <div className="text-center text-muted-foreground text-xs py-4">无持仓</div> : openPositions.map((p) => (
              <div key={p.id} className="grid grid-cols-[1.4fr_0.7fr_0.9fr_1fr] gap-1.5 py-1.5 border-b border-border/20 items-center text-xs last:border-0">
                <div className="font-semibold font-mono">{p.symbol}</div>
                <div><span className={`text-[9px] px-1 py-0.5 rounded inline-block text-center ${p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{p.side === "long" ? "多" : "空"}</span></div>
                <div className="text-right font-mono text-[10px] text-muted-foreground">{p.trade_nature}</div>
                <div className={`text-right font-mono ${(p.unrealized_pnl || 0) >= 0 ? "text-profit" : "text-loss"}`}>{fmtMoney(p.unrealized_pnl || 0)}</div>
              </div>
            ))}
          </div>
        </Card>

        {/* AI Decisions + BlockReport (R8) */}
        <div className="flex flex-col gap-2">
          <DecisionTimeline activity={tierActivity} loading={!tierActivity} />
          <BlockReportPanel />
        </div>
      </div>

      {/* Section: Strategies Table */}
      <div>
        <SectionHeader title="活跃策略">
          <div className="flex gap-0.5">
            <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">全部</button>
            <button className="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/25">运行中</button>
            <button className="text-[9px] px-1.5 py-0.5 rounded text-muted-foreground hover:text-foreground">已暂停</button>
          </div>
        </SectionHeader>
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
                {openPositions.length > 0 ? openPositions.map((p) => (
                  <tr key={p.id} className="border-b border-border/20 hover:bg-muted/20">
                    <td className="px-3 py-2 font-mono text-xs">{p.strategy_id?.slice(0, 20) || "—"}</td>
                    <td className="py-2"><span className={p.trade_nature === "scalp" ? "text-primary" : p.trade_nature === "swing" ? "text-profit" : "text-warning"}>{p.trade_nature}</span></td>
                    <td className="py-2 font-mono font-semibold text-xs">{p.symbol}</td>
                    <td className="py-2"><span className={`text-[9px] px-1 py-0.5 rounded ${p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{p.side === "long" ? "多" : "空"}</span></td>
                    <td className="text-right py-2 font-mono">{p.entry_price?.toFixed(2) || "—"}</td>
                    <td className="text-right py-2 font-mono">{p.leverage || "—"}x</td>
                    <td className={`text-right py-2 font-mono ${(p.unrealized_pnl || 0) >= 0 ? "text-profit" : "text-loss"}`}>{fmtMoney(p.unrealized_pnl || 0)}</td>
                    <td className="px-3 py-2"><StatusBadge tone="profit" glow>running</StatusBadge></td>
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
        <SectionHeader title="风险敞口" />
        <div className="bg-card border border-border rounded-md overflow-hidden grid grid-cols-6">
          <RiskCell label="当前敞口" value={`$${(openPositions.reduce((s, p) => s + (p.margin || 0), 0)).toFixed(0)}`} ctx={`/ $${totalEquity.toFixed(0)} 上限`} />
          <RiskCell label="浮动盈亏" value={fmtMoney(totalUnrealizedPnl)} valueColor={totalUnrealizedPnl >= 0 ? "profit" : "loss"} ctx={`${openPositions.length} 持仓 · 平均 $${openPositions.length > 0 ? (totalUnrealizedPnl / openPositions.length).toFixed(0) : 0}`} />
          <RiskCell label="已实现 PnL" value={fmtMoney(realizedPnl)} valueColor={realizedPnl >= 0 ? "profit" : "loss"} />
          <RiskCell label="手续费" value={`$${feePaid.toFixed(2)}`} valueColor="loss" />
          <RiskCell label="多空比" value={`${longCount} / ${shortCount}`} ctx={`${longCount + shortCount} 总持仓`} />
          <RiskCell label="活跃策略" value={String(activeStrategyCount)} />
        </div>
      </div>
    </div>
  );
}
