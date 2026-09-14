"use client";

/**
 * 套利中心 · 总览（默认页）
 *
 * 设计 §3.1：顶部组合权益/今日/风险预算/熔断计数 → 车道卡片网格 → 近7天归因堆叠条 → 风险速览。
 * 数据来源（/api/trading/*）：
 *  - portfolio/summary  → equity/pnl_today/budget_used_pct/breakers_active/lane_budgets
 *  - lanes              → 车道 edge/mode/promotion/risk
 *  - portfolio/attribution → 六维归因堆叠条 + 每条车道近7天净收益
 *  - risk/summary       → 风险速览（最大单币敞口 / 最差单日）
 *  - useLaneStream      → WS 通道模式（实时/轮询中）
 */
import { useMemo } from "react";
import { ArrowRightLeft, TrendingUp, TrendingDown, Shield, Gauge, Activity, Layers } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPct } from "@/lib/format";
import { ageFromAsOf, type Attribution } from "@/lib/trading-api";
import {
  PageShell, DataState, LaneCard, AttributionBar, UnifiedAccountCard,
} from "@/components/arbitrage";
import { usePortfolioSummary, useLanes, useAttribution, useRiskSummary, useUnifiedAccount } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

/** 按「最近成功拉取时间」判定 stale（用于无 as_of 的端点：lanes / attribution / shadow report） */
function staleByTimestamp(ts: number | null): boolean {
  return ts != null && Date.now() - ts > 90_000;
}

export default function ArbitragePage() {
  const summary = usePortfolioSummary();
  const lanes = useLanes();
  const attribution = useAttribution(7);
  const risk = useRiskSummary(30);
  // 不传 account_id：后端按做市车道 meta.paper_account_id 自动定位统一账户
  const unified = useUnifiedAccount();
  const stream = useLaneStream();

  const summaryAsOf = summary.data?.as_of ?? null;

  // 车道卡片按资金权重降序
  const laneCards = useMemo(() => {
    const list = lanes.data?.items ?? [];
    const budgets = summary.data?.lane_budgets ?? {};
    const equity = summary.data?.equity ?? null;
    const byLane = new Map<string, number>();
    (attribution.data?.by_lane ?? []).forEach((b) => byLane.set(b.lane_id, b.net_usd));

    return list
      .map((lane) => ({
        lane,
        budgetUsd: budgets[lane.lane_id] ?? null,
        equity,
        pnl7dUsd: byLane.get(lane.lane_id) ?? null,
      }))
      .sort((a, b) => (b.budgetUsd ?? 0) - (a.budgetUsd ?? 0));
  }, [lanes.data, summary.data, attribution.data]);

  const attr: Attribution | null = attribution.data ?? null;
  const riskAsOf = risk.data?.as_of ?? null;
  const totalNetUsd = attr?.total?.net_usd;

  return (
    <PageShell
      title="套利中心 · 组合总览"
      subtitle="我现在整体怎么样？（按资金权重排序的车道）"
      icon={<ArrowRightLeft className="h-4 w-4" />}
      mode={stream.mode}
      asOf={summaryAsOf}
      onRefresh={() => {
        summary.refresh();
        lanes.refresh();
        attribution.refresh();
        risk.refresh();
        unified.refresh();
      }}
      refreshing={summary.loading && !summary.data}
      breadcrumb={[{ label: "交易核心" }, { label: "套利中心" }]}
    >
      {/* ── KPI 条 ── */}
      <DataState
        loading={summary.loading}
        error={summary.error}
        hasData={!!summary.data}
        onRetry={summary.refresh}
        stale={staleByTimestamp(summary.lastUpdated)}
      >
        {summary.data && (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <KpiCard
              label="组合权益"
              value={fmtUsd(summary.data.equity)}
              sub={equitySourceLabel(summary.data.equity_source)}
              icon={Gauge}
            />
            <KpiCard
              label="今日"
              value={`${summary.data.pnl_today_usd >= 0 ? "+" : ""}${fmtUsd(summary.data.pnl_today_usd)}`}
              sub={`${summary.data.pnl_today_pct >= 0 ? "+" : ""}${fmtPct(summary.data.pnl_today_pct, 2)}`}
              tone={summary.data.pnl_today_usd >= 0 ? "profit" : "loss"}
              icon={summary.data.pnl_today_usd >= 0 ? TrendingUp : TrendingDown}
            />
            <KpiCard
              label="风险预算"
              value={fmtPct(summary.data.budget_used_pct, 1)}
              sub={fmtUsd(summary.data.budget_used_usd)}
              icon={Shield}
            />
            <KpiCard
              label="熔断"
              value={`${summary.data.breakers_active}/${summary.data.lanes_total}`}
              sub={`${summary.data.lanes_active} 条车道运行中`}
              tone={summary.data.breakers_active > 0 ? "loss" : "profit"}
              icon={Activity}
            />
          </div>
        )}
      </DataState>

      {/* ── 统一模拟账户（各周期相互配合） ── */}
      <Card id="unified-account" className="glass p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <Layers className="h-3.5 w-3.5" />
          </span>
          统一模拟账户
          <span className="text-[11px] font-normal text-muted-foreground">影子交易 = 统一模拟账户 · 做市已并入</span>
        </div>
        <DataState
          loading={unified.loading}
          error={unified.error}
          hasData={!!unified.data}
          onRetry={unified.refresh}
          stale={isStale(unified.data?.as_of)}
          empty={!unified.data}
          emptyHint="暂无统一模拟账户数据"
        >
          <UnifiedAccountCard data={unified.data} />
        </DataState>
      </Card>

      {/* ── 车道卡片 ── */}
      <div>
        <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          车道
          <span className="text-[11px] font-normal text-muted-foreground">按资金权重排序</span>
        </h2>
        <DataState
          loading={lanes.loading}
          error={lanes.error}
          hasData={!!lanes.data}
          onRetry={lanes.refresh}
          stale={staleByTimestamp(lanes.lastUpdated)}
          empty={laneCards.length === 0}
          emptyHint="暂无车道（可调用 /api/trading/lanes/seed 初始化）"
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {laneCards.map((c) => (
              <LaneCard
                key={c.lane.lane_id}
                lane={c.lane}
                budgetUsd={c.budgetUsd}
                equity={c.equity}
                pnl7dUsd={c.pnl7dUsd}
                href={`/arbitrage/lanes?lane=${encodeURIComponent(c.lane.lane_id)}`}
              />
            ))}
          </div>
        </DataState>
      </div>

      {/* ── 归因堆叠条（近 7 天，全部车道合计） ── */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <TrendingUp className="h-3.5 w-3.5" />
            </span>
            近 7 天归因（全部车道合计）
          </h2>
          <span className="text-[11px] text-muted-foreground">六维分解 · 60s 刷新</span>
        </div>
        <DataState
          loading={attribution.loading}
          error={attribution.error}
          hasData={!!attribution.data}
          onRetry={attribution.refresh}
          stale={staleByTimestamp(attribution.lastUpdated)}
          empty={!attr || (totalNetUsd == null && (attr?.total?.n ?? 0) === 0)}
          emptyHint="近 7 天尚无成交记录，归因暂无数据"
        >
          <AttributionBar total={attr?.total} />
        </DataState>
      </Card>

      {/* ── 风险速览 ── */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <Shield className="h-3.5 w-3.5" />
            </span>
            风险速览
          </h2>
          <span className="text-[11px] text-muted-foreground">近 30 天</span>
        </div>
        <DataState
          loading={risk.loading}
          error={risk.error}
          hasData={!!risk.data}
          onRetry={risk.refresh}
          stale={isStale(riskAsOf)}
        >
          {risk.data && (
            <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-3">
              <RiskCell
                label="最大单币敞口"
                value={risk.data.max_symbol ? `${risk.data.max_symbol} ${fmtPct(risk.data.max_symbol_exposure_pct, 1)}` : "无持仓"}
                tone={risk.data.max_symbol_exposure_pct > (risk.data.limits.max_net_exposure_pct ?? Infinity) ? "loss" : "profit"}
              />
              <RiskCell
                label="最差单日"
                value={risk.data.worst_day ? `${risk.data.worst_day} ${fmtUsd(risk.data.worst_day_usd)}` : "—"}
                tone={risk.data.worst_day_usd >= 0 ? "muted" : "loss"}
              />
              <RiskCell
                label="净敞口"
                value={`${fmtUsd(risk.data.net_exposure_usd)} · ${fmtPct(Math.abs(risk.data.net_exposure_pct), 1)}`}
                tone="muted"
              />
            </div>
          )}
        </DataState>
      </Card>
    </PageShell>
  );
}

function KpiCard({
  label,
  value,
  sub,
  tone,
  icon: Icon,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "profit" | "loss" | "warning";
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card className="relative overflow-hidden p-3.5 glass">
      <span className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-lg border border-cyan-400/20 bg-gradient-to-br from-cyan-400/15 to-violet-500/15 text-cyan-300">
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="pr-8 text-[11px] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-0.5 font-mono text-lg font-bold tracking-tight tabular-nums",
          tone === "profit" && "grad-text-green",
          tone === "loss" && "grad-text-red",
          tone !== "profit" && tone !== "loss" && "grad-text"
        )}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{sub}</div>}
    </Card>
  );
}

/** 权益来源标签（后端 equity_source → 人话）：让「这个数字是什么口径」一眼可见 */
function equitySourceLabel(src?: string | null): string {
  if (!src) return "来源未知";
  if (src.startsWith("lane_accounts(")) return "车道绑定账户合计（中心自有资金）";
  if (src.startsWith("lane_account(")) return `车道账户 ${src.replace(/[^0-9]/g, "")}`;
  if (src.startsWith("paper_accounts(")) return "全部模拟账户合计（含非车道账户）";
  if (src === "default_reference") return "默认参考值（未取到真实账户）";
  return src;
}

function RiskCell({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" | "muted" }) {  return (
    <div className="rounded-md bg-muted/20 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "font-mono tabular-nums font-semibold",
          tone === "profit" && "text-profit",
          tone === "loss" && "text-loss"
        )}
      >
        {value}
      </div>
    </div>
  );
}
