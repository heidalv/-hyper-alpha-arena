"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Brain, Zap, Activity, TrendingUp, Clock,
  Signal, Bot, AlertCircle, CheckCircle2,
  Loader2, RefreshCw,
  Layers, TrendingDown, Inbox,
} from "lucide-react";
import Link from "next/link";
import { useSessions, usePositions, useAccounts } from "@/hooks/useTradingData";
import { SessionManager } from "@/components/trading/SessionManager";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AtasDecision, AiDecisionEntry, Position, TickIntervals } from "@/types/api";
import { useMemo, useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
const BACKEND = getBackendUrl().replace(/\/$/, "");

export default function StrategyPage() {
  const { data: sessions, isLoading: sessionsLoading } = useSessions();
  const { data: accounts } = useAccounts();
  const [activeTab, setActiveTab] = useState<"overview" | "session" | "signals" | "decisions">("overview");

  const activeSession = sessions?.find((s) => s.status === "running");

  // 从后端读取实际调度间隔
  const { data: tickData } = useQuery({
    queryKey: ["tick-intervals"],
    queryFn: (): Promise<TickIntervals> =>
      fetch(`${BACKEND}/api/full-auto/tick-intervals`).then((r) => r.json()),
    staleTime: 60_000,
  });
  const tickIntervals = tickData?.intervals ?? { short: 30, mid: 120, long: 240 };

  // 从当前运行会话获取交易账户 ID，确保持仓数据关联正确的会话
  const tradingAccountId = useMemo(() => {
    // 优先：当前活跃会话的 trading_account_id / paper_account_id
    if (activeSession?.trading_account_id) return activeSession.trading_account_id;
    if (activeSession?.paper_account_id) return activeSession.paper_account_id;
    // 回退：账户列表查找（仅当无活跃会话时）
    if (!accounts) return null;
    return accounts.find((a) => a.trading_mode === "paper" && a.auto_trading_enabled)?.id ?? null;
  }, [activeSession, accounts]);

  const { data: positions } = usePositions(tradingAccountId, "open");
  const openPositions = positions ?? [];
  const llmAccount = accounts?.find((a) => a.id === tradingAccountId);

  const scalpPos = openPositions.filter((p) => p.trade_nature === "scalp");
  const swingPos = openPositions.filter((p) => p.trade_nature === "swing");
  const trendPos = openPositions.filter((p) => p.trade_nature === "trend_follow" || p.trade_nature === "position");

  // KPI 汇总（对齐原型 kpi-grid）
  const winPositions = openPositions.filter((p) => (p.unrealized_pnl || 0) > 0);
  const lossPositions = openPositions.filter((p) => (p.unrealized_pnl || 0) < 0);
  const totalOpenPnl = openPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const winOpenPnl = winPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const lossOpenPnl = lossPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);

  return (
    <div className="p-4 space-y-4">
      {/* 标题（Aurora 统一页头） */}
      <PageHeader
        icon={<Brain className="w-4 h-4" />}
        title="AI 策略"
        subtitle="多周期 AI 决策引擎 · 编排器驱动实盘执行"
        refreshHint="决策流实时"
        breadcrumb={[{ label: "策略中心" }, { label: "AI 策略" }]}
        actions={
          <>
            {activeSession ? (
              <Badge variant="default" className="bg-profit/20 text-profit">
                {activeSession.status} · {activeSession.active_count} 活跃
              </Badge>
            ) : (
              <Badge variant="secondary">无活跃会话</Badge>
            )}
          </>
        }
      />

      {/* Tab 切换（分区导航） */}
      <div className="flex gap-1 border-b border-border">
        <TabButton active={activeTab === "overview"} onClick={() => setActiveTab("overview")} icon={Activity} label="三周期总览" />
        <TabButton active={activeTab === "session"} onClick={() => setActiveTab("session")} icon={Bot} label="会话管理" />
        <TabButton active={activeTab === "signals"} onClick={() => setActiveTab("signals")} icon={Signal} label="信号流" />
        <TabButton active={activeTab === "decisions"} onClick={() => setActiveTab("decisions")} icon={Bot} label="AI 决策日志" />
      </div>

      {/* ── Tab: 三周期总览 ── */}
      {activeTab === "overview" && (
        <div className="space-y-4">
          {/* AI 决策会话（LLM 关联 + 会话详情） */}
          <Card className="glass overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
              <div className="flex items-center gap-2">
                <Brain className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">AI 决策会话</h2>
              </div>
              <span className="text-xs text-muted-foreground">LLM 关联 · 自动路由</span>
            </div>
            <div className="px-4 py-3 space-y-3">
              {/* LLM 关联 */}
              {llmAccount && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-x-4 gap-y-2 text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-muted-foreground shrink-0">账户</span>
                    <span className="font-medium truncate">{llmAccount.name}</span>
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-muted-foreground shrink-0">快模型</span>
                    <span className="text-primary truncate">{llmAccount.llm_config_name || "全局默认"}</span>
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-muted-foreground shrink-0">深模型</span>
                    <span className="text-warning truncate">{llmAccount.llm_config_name_deep || "全局默认"}</span>
                  </div>
                  <div className="text-muted-foreground col-span-full">
                    策略生成/深度分析使用深模型；关联顺序：策略指定 → 账户绑定 → 全局默认
                  </div>
                </div>
              )}

              {/* 会话详情 */}
              {activeSession ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs pt-1">
                    <Detail label="会话ID" value={activeSession.session_id.slice(0, 20) + "..."} />
                    <Detail label="状态" value={activeSession.status} />
                    <Detail label="模式" value={activeSession.trading_mode} />
                    <Detail label="活跃策略" value={String(activeSession.active_count)} />
                  </div>
                  <div className="pt-2 border-t border-border/50">
                    <span className="text-xs text-muted-foreground">交易对 ({activeSession.symbols?.length ?? 0}):</span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {activeSession.symbols?.map((s: string) => (
                        <Badge key={s} variant="secondary" className="text-xs">{s}</Badge>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
                  <Inbox className="w-4 h-4 opacity-60" />无活跃会话
                </div>
              )}
            </div>
          </Card>

          {/* KPI 汇总（对齐原型 kpi-grid） */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <KpiCard
              icon={Layers}
              label="持仓数"
              value={String(openPositions.length)}
              tone="grad-text"
              extra={`短线 ${scalpPos.length} · 中线 ${swingPos.length} · 长线 ${trendPos.length}`}
            />
            <KpiCard
              icon={TrendingUp}
              label="盈利持仓"
              value={String(winPositions.length)}
              tone="grad-text-green"
              extra={`合计浮动 +$${winOpenPnl.toFixed(2)}`}
            />
            <KpiCard
              icon={TrendingDown}
              label="亏损持仓"
              value={String(lossPositions.length)}
              tone="grad-text-red"
              extra={`合计浮动 −$${Math.abs(lossOpenPnl).toFixed(2)}`}
            />
            <KpiCard
              icon={Activity}
              label="总浮动"
              value={`${totalOpenPnl >= 0 ? "+" : ""}$${totalOpenPnl.toFixed(2)}`}
              tone={totalOpenPnl >= 0 ? "grad-text-green" : "grad-text-red"}
              extra="实时未实现盈亏"
            />
          </div>

          {/* 双周期策略卡片 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <StrategyTierCard
              name="短线 Scalp"
              icon={Zap}
              color="primary"
              description="因子引擎 · 5m K线"
              positions={scalpPos}
              configPath="/scalp"
              tickInterval={`${tickIntervals.short}s`}
              holdRange="1h~12h"
            />
            <StrategyTierCard
              name="长线 Trend (含中周期)"
              icon={TrendingUp}
              color="warning"
              description="TrendAgent 长线 · 4h/1d；中线因子化(过渡期 MLTO 对照)"
              positions={[...trendPos, ...swingPos]}
              configPath="/long"
              tickInterval={`${tickIntervals.long}s`}
              holdRange="中周期~7天"
            />
          </div>

          {/* 当前持仓表 */}
          <Card className="glass overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">当前持仓</h2>
                <Badge variant="secondary" className="text-xs">{openPositions.length} 个持仓</Badge>
              </div>
              <Link href="/paper-trading" className="text-xs text-primary hover:underline shrink-0">详细管理 →</Link>
            </div>
            {openPositions.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
                <Inbox className="w-6 h-6 opacity-50" />
                <span className="text-sm">暂无持仓</span>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-2 px-2">币种</th>
                      <th className="text-left py-2 px-2">方向</th>
                      <th className="text-left py-2 px-2">周期</th>
                      <th className="sortable text-right py-2 px-2">开仓价 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-right py-2 px-2">杠杆</th>
                      <th className="sortable text-right py-2 px-2">保证金 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">浮盈 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">盈亏% <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-left py-2 px-2">持仓</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map((pos: Position) => <PosRow key={pos.id} pos={pos} />)}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td className="px-3 py-2 text-muted-foreground text-xs">合计 {openPositions.length} 笔</td>
                      <td colSpan={4} />
                      <td className="text-right py-2 num text-muted-foreground">${openPositions.reduce((s, p) => s + (p.margin || 0), 0).toFixed(2)}</td>
                      <td className={cn("text-right py-2 num font-bold",
                        openPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0) >= 0 ? "text-profit" : "text-loss")}>
                        {openPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0) >= 0 ? "+" : ""}${openPositions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0).toFixed(3)}
                      </td>
                      <td colSpan={2} />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* ── Tab: 会话管理 ── */}
      {activeTab === "session" && <SessionManager />}

      {/* ── Tab: 信号流 ── */}
      {activeTab === "signals" && <SignalFlow accountId={tradingAccountId} />}

      {/* ── Tab: AI 决策日志 ── */}
      {activeTab === "decisions" && <DecisionLog accountId={tradingAccountId} />}
    </div>
  );
}

// ═══ KPI 卡片（对齐原型 kpi-grid） ═══
function KpiCard({ icon: Icon, label, value, tone, extra }: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  tone: "grad-text" | "grad-text-green" | "grad-text-red";
  extra: string;
}) {
  return (
    <Card className="glass p-3">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="w-3.5 h-3.5 text-primary" />
        <span>{label}</span>
      </div>
      <div className={cn("mt-1.5 text-xl font-bold tabular-nums", tone)}>{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground tabular-nums">{extra}</div>
    </Card>
  );
}

// ═══ 三周期策略卡片 ═══
function StrategyTierCard({
  name, icon: Icon, color, description, positions, configPath, tickInterval, holdRange,
}: {
  name: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  description: string;
  positions: Position[];
  configPath: string;
  tickInterval: string;
  holdRange: string;
}) {
  const totalPnl = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const wins = positions.filter((p) => (p.unrealized_pnl || 0) > 0).length;
  const losses = positions.filter((p) => (p.unrealized_pnl || 0) < 0).length;

  return (
    <Card className="glass overflow-hidden">
      {/* 卡片头：标题 + 图标 + 徽章/提示 */}
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-2 min-w-0">
          <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center shrink-0", `bg-${color}/10`)}>
            <Icon className={cn("w-4 h-4", `text-${color}`)} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{name}</div>
            <div className="text-xs text-muted-foreground truncate">{description}</div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge variant="secondary" className="text-xs">分析 {tickInterval}</Badge>
          <span className="text-xs text-muted-foreground">持仓 {holdRange}</span>
        </div>
      </div>

      {/* 卡片体：数据 */}
      <div className="px-4 py-3 space-y-3">
        {/* 持仓统计 */}
        <div className="grid grid-cols-3 gap-2">
          <div className="text-center p-2 rounded bg-muted/30">
            <Layers className="w-3.5 h-3.5 mx-auto mb-1 text-cyan-300/70" />
            <div className="text-lg font-bold tabular-nums">{positions.length}</div>
            <div className="text-xs text-muted-foreground">持仓</div>
          </div>
          <div className="text-center p-2 rounded bg-muted/30">
            <TrendingUp className="w-3.5 h-3.5 mx-auto mb-1 text-profit/80" />
            <div className="text-lg font-bold tabular-nums text-profit">{wins}</div>
            <div className="text-xs text-muted-foreground">盈利</div>
          </div>
          <div className="text-center p-2 rounded bg-muted/30">
            <TrendingDown className="w-3.5 h-3.5 mx-auto mb-1 text-loss/80" />
            <div className="text-lg font-bold tabular-nums text-loss">{losses}</div>
            <div className="text-xs text-muted-foreground">亏损</div>
          </div>
        </div>

        {/* 浮动 PnL */}
        <div className="flex justify-between items-center text-xs">
          <span className="text-muted-foreground">总浮动</span>
          <span className={cn("tabular-nums font-bold", totalPnl >= 0 ? "grad-text-green" : "grad-text-red")}>
            {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(3)}
          </span>
        </div>

        {/* 持仓列表 */}
        {positions.length > 0 ? (
          <div className="space-y-1 max-h-32 overflow-y-auto">
            {positions.map((p) => (
              <div key={p.id} className="flex items-center justify-between text-xs py-0.5">
                <div className="flex items-center gap-1.5">
                  <span className="font-medium">{p.symbol}</span>
                  <span className={cn("text-xs px-1.5 py-0.5 rounded", p.side === "long" ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>
                    {p.side === "long" ? "多" : "空"} {(p.leverage || 1)}x
                  </span>
                </div>
                <span className={cn("tabular-nums", (p.unrealized_pnl || 0) >= 0 ? "text-profit" : "text-loss")}>
                  {(p.unrealized_pnl || 0) >= 0 ? "+" : ""}${(p.unrealized_pnl || 0).toFixed(3)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center justify-center gap-1.5 py-3 text-muted-foreground text-xs">
            <Inbox className="w-3.5 h-3.5 opacity-60" />无持仓
          </div>
        )}

        {/* 操作按钮 */}
        <div className="flex gap-2 pt-2 border-t border-border/30">
          <Link href={configPath} className="flex-1 text-xs h-7 flex items-center justify-center rounded border border-border hover:bg-muted/50 transition-colors">配置</Link>
          <button className="text-xs h-7 px-3 text-muted-foreground hover:text-foreground">历史</button>
        </div>
      </div>
    </Card>
  );
}

// ═══ 信号流组件 ═══
function SignalFlow({ accountId }: { accountId: number | null }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["atas-decisions", accountId],
    queryFn: async () => {
      const res = await fetch(`/api/atas/decisions?limit=50`);
      if (!res.ok) return [];
      const json = await res.json();
      return Array.isArray(json) ? json : (json.decisions ?? json.items ?? []);
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const decisions = data ?? [];

  // 统计
  const stats = {
    total: decisions.length,
    buy: decisions.filter((d: AtasDecision) => d.operation === "buy" || d.operation === "add").length,
    sell: decisions.filter((d: AtasDecision) => d.operation === "sell" || d.operation === "reduce" || d.operation === "close").length,
    hold: decisions.filter((d: AtasDecision) => d.operation === "hold").length,
    executed: decisions.filter((d: AtasDecision) => d.executed).length,
  };

  return (
    <div className="space-y-3">
      {/* 统计栏 */}
      <Card className="glass overflow-hidden">
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            <Signal className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-medium">AI 决策流</h2>
            <Badge variant="secondary" className="text-xs">本轮 {stats.total} 条</Badge>
          </div>
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
        <div className="px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <span className="text-profit tabular-nums">买入 {stats.buy}</span>
          <span className="text-loss tabular-nums">卖出 {stats.sell}</span>
          <span className="text-muted-foreground tabular-nums">观望 {stats.hold}</span>
          <span className="text-muted-foreground tabular-nums">已执行 {stats.executed}</span>
        </div>
      </Card>

      <Card className="glass overflow-hidden">
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-medium">决策记录</h2>
          </div>
          <span className="text-xs text-muted-foreground">最近 {decisions.length} 条</span>
        </div>
        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
        ) : decisions.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
            <Inbox className="w-6 h-6 opacity-50" />
            <span className="text-sm">暂无决策记录</span>
          </div>
        ) : (
          <div className="max-h-[600px] overflow-y-auto divide-y divide-border/20">
            {decisions.map((d: AtasDecision, i: number) => {
              const op = d.operation || "hold";
              const isBuy = op === "buy" || op === "add";
              const isSell = op === "sell" || op === "reduce" || op === "close";
              return (
                <div key={d.id || i} className="px-4 py-2.5 hover:bg-muted/10">
                  <div className="flex items-center gap-2 mb-1">
                    {/* 时间 */}
                    <span className="text-xs text-muted-foreground font-mono tabular-nums shrink-0">
                      {d.created_at ? new Date(d.created_at).toLocaleTimeString("zh-CN", { hour12: false }) : "--"}
                    </span>
                    {/* 币种 */}
                    <span className="text-xs font-bold shrink-0">{d.symbol}</span>
                    {/* 操作 */}
                    <Badge className={cn(
                      "text-xs shrink-0",
                      isBuy ? "bg-profit/20 text-profit" : isSell ? "bg-loss/20 text-loss" : "bg-muted text-muted-foreground"
                    )}>
                      {isBuy ? "买入" : isSell ? "卖出" : "观望"}
                    </Badge>
                    {/* 仓位 */}
                    {d.target_portion != null && d.target_portion > 0 && (
                      <span className="text-xs text-muted-foreground tabular-nums shrink-0">
                        目标 {(d.target_portion * 100).toFixed(0)}%
                      </span>
                    )}
                    {/* 执行状态 */}
                    {d.executed ? (
                      <Badge variant="outline" className="text-xs text-profit border-profit/30 shrink-0">已执行</Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs text-muted-foreground shrink-0">未执行</Badge>
                    )}
                  </div>
                  {/* 推理过程 */}
                  {d.reasoning && (
                    <p className="text-xs text-muted-foreground leading-relaxed pl-1 line-clamp-2">{d.reasoning}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

// ═══ AI 决策日志 ═══
function DecisionLog({ accountId }: { accountId: number | null }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["ai-decisions", accountId],
    queryFn: () => accountId ? api.getAiDecisions(accountId, 30) : Promise.resolve({ entries: [] }),
    enabled: !!accountId,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const decisions = data?.entries ?? [];

  return (
    <Card className="glass overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-medium">AI 决策日志</h2>
          <Badge variant="secondary" className="text-xs">{decisions.length} 条</Badge>
        </div>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
      ) : decisions.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
          <Inbox className="w-6 h-6 opacity-50" />
          <span className="text-sm">暂无 AI 决策记录</span>
        </div>
      ) : (
        <div className="max-h-[600px] overflow-y-auto divide-y divide-border/20">
          {decisions.map((dec: AiDecisionEntry, i: number) => {
            const action = dec.operation || dec.action || dec.decision || "—";
            const isBuy = action === "buy" || action === "long" || action === "add";
            const isSell = action === "sell" || action === "short" || action === "reduce" || action === "close";
            const conf = dec.confidence;
            const tier = dec.tier || dec.trade_nature;
            const agent = dec.agent_source;
            return (
              <div key={dec.id || i} className="px-4 py-2.5 hover:bg-muted/10">
                {/* 第一行：时间 + 币种 + 操作 + 标签 */}
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="text-xs text-muted-foreground font-mono tabular-nums shrink-0">
                    {dec.decision_time || dec.created_at
                      ? new Date(dec.decision_time ?? dec.created_at ?? "").toLocaleTimeString("zh-CN", { hour12: false })
                      : "--"}
                  </span>
                  <span className="text-xs font-bold shrink-0">{dec.symbol || "—"}</span>
                  <span className={cn("text-xs font-medium shrink-0", isBuy ? "text-profit" : isSell ? "text-loss" : "text-muted-foreground")}>
                    {isBuy ? "买入" : isSell ? "卖出" : "观望"}
                  </span>
                  {conf != null && (
                    <Badge variant="outline" className={cn("text-xs", conf >= 0.7 ? "text-profit border-profit/30" : conf >= 0.5 ? "text-warning border-warning/30" : "text-muted-foreground")}>
                      置信 {(conf >= 1 ? conf : conf * 100).toFixed(0)}%
                    </Badge>
                  )}
                  {tier && <Badge variant="secondary" className="text-xs">{tier}</Badge>}
                  {agent && <Badge variant="outline" className="text-xs text-muted-foreground">{agent}</Badge>}
                  {dec.executed && <Badge className="bg-profit/20 text-profit text-xs">已执行</Badge>}
                </div>
                {/* 推理 */}
                {dec.reasoning && (
                  <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2 mb-1">{dec.reasoning}</p>
                )}
                {/* SL/TP/杠杆/价格 */}
                <div className="flex gap-3 text-xs text-muted-foreground tabular-nums">
                  {dec.stop_loss_price != null && <span>SL ${Number(dec.stop_loss_price).toFixed(2)}</span>}
                  {dec.take_profit_price != null && <span>TP ${Number(dec.take_profit_price).toFixed(2)}</span>}
                  {dec.leverage != null && Number(dec.leverage) > 0 && <span>{dec.leverage}x</span>}
                  {dec.short_bias && <span>短{dec.short_bias}</span>}
                  {dec.mid_bias && <span>中{dec.mid_bias}</span>}
                  {dec.long_bias && <span>长{dec.long_bias}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

// ═══ 通用组件 ═══

function TabButton({ active, onClick, icon: Icon, label }: {
  active: boolean; onClick: () => void;
  icon: React.ComponentType<{ className?: string }>; label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px",
        active ? "border-primary text-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"
      )}
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </button>
  );
}

function PosRow({ pos }: { pos: Position }) {
  // 每秒自更新已持时长（首秒显示 0m，随后由 interval 校正）
  const [nowMs, setNowMs] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const pnl = pos.unrealized_pnl || 0;
  const margin = pos.margin || 0;
  const pnlPct = margin > 0 ? (pnl / margin) * 100 : 0;
  const openedAt = pos.opened_at ? new Date(pos.opened_at) : null;
  const holdHours = openedAt ? Math.max(0, (nowMs - openedAt.getTime()) / 3600000) : 0;

  return (
    <tr className="border-b border-border/30 hover:bg-muted/20">
      <td className="py-2 px-2 font-medium">{pos.symbol}</td>
      <td className="py-2 px-2">
        <span className={cn("text-xs px-1.5 py-0.5 rounded", pos.side === "long" ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>
          {pos.side === "long" ? "多" : "空"}
        </span>
      </td>
      <td className="py-2 px-2 text-muted-foreground">{
        ({scalp:"短线",swing:"中线",trend_follow:"长线",position:"长线"} as Record<string,string>)[pos.trade_nature] || pos.trade_nature || "—"
      }</td>
      <td className="py-2 px-2 text-right tabular-nums num">{(pos.entry_price || 0).toLocaleString()}</td>
      <td className="py-2 px-2 text-right tabular-nums num">{pos.leverage || 1}x</td>
      <td className="py-2 px-2 text-right tabular-nums num text-muted-foreground">${(margin).toFixed(2)}</td>
      <td className={cn("py-2 px-2 text-right tabular-nums num font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
        {pnl >= 0 ? "+" : ""}${pnl.toFixed(3)}
      </td>
      <td className={cn("py-2 px-2 text-right tabular-nums num", pnlPct >= 0 ? "text-profit" : "text-loss")}>
        {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
      </td>
      <td className="py-2 px-2 text-muted-foreground">
        {holdHours < 1 ? `${(holdHours * 60).toFixed(0)}m` : holdHours < 24 ? `${holdHours.toFixed(1)}h` : `${(holdHours / 24).toFixed(1)}d`}
      </td>
    </tr>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <div className="font-medium truncate">{value}</div>
    </div>
  );
}
