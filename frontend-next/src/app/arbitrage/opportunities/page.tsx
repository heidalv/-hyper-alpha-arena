"use client";

/**
 * 套利中心 · 机会
 *
 * 设计 §3.4：机会表一律展示扣费后净边际，负边际明确显示为负并不可执行。
 * 阶段2：
 *  - 数据源 `GET /api/trading/opportunities`（新增 min_days / tradable_only / suspect 字段）；
 *  - 提供 venue 切换 + min_days / tradable_only 筛选；
 *  - 无可执行机会时给出原因（如资金费 carry 的现货腿未建成）。
 */
import { Suspense, useMemo, useState } from "react";
import { Zap, Info } from "lucide-react";
import { ageFromAsOf } from "@/lib/trading-api";
import { PageShell, DataState, OpportunityTable } from "@/components/arbitrage";
import { useOpportunities } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

const VENUES = ["asterdex", "binance", "hyperliquid", "okx", "bybit"];
const MIN_DAYS_OPTIONS = [7, 14, 30];

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

export default function OpportunitiesPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 机会" icon={<Zap className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "机会" }]} />}>
      <OpportunitiesInner />
    </Suspense>
  );
}

function OpportunitiesInner() {
  const [venue, setVenue] = useState("asterdex");
  const [minDays, setMinDays] = useState(7);
  const [tradableOnly, setTradableOnly] = useState(true);
  const opps = useOpportunities(venue, minDays, tradableOnly);
  const stream = useLaneStream();

  const data = opps.data;

  const emptyHint = useMemo(() => {
    if (data && data.items.length > 0 && data.executable_count === 0) {
      return "当前无可执行机会：资金费 carry 的现货腿未建成（设计 §1.3 待建），或净边际为负/未验证。";
    }
    if (data && data.items.length === 0) {
      return `该场地（${venue}）在 min_days=${minDays} / tradable_only=${tradableOnly} 条件下暂无机会；可尝试放宽条件或切换场地。`;
    }
    return "当前无可执行机会";
  }, [data, venue, minDays, tradableOnly]);

  return (
    <PageShell
      title="套利中心 · 机会"
      subtitle="扣费后还有没有肉：机会扫描与成本纪律"
      icon={<Zap className="h-4 w-4" />}
      mode={stream.mode}
      asOf={data?.as_of ?? null}
      onRefresh={opps.refresh}
      refreshing={opps.loading && !opps.data}
      breadcrumb={[{ label: "套利中心" }, { label: "机会" }]}
    >
      {/* 筛选 */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border/40 bg-card/40 p-3">
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span>场地</span>
          <select
            value={venue}
            onChange={(e) => setVenue(e.target.value)}
            className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-xs text-foreground outline-none focus:border-cyan-400/40"
          >
            {VENUES.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span>最小样本天数</span>
          <select
            value={minDays}
            onChange={(e) => setMinDays(Number(e.target.value))}
            className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-xs text-foreground outline-none focus:border-cyan-400/40"
          >
            {MIN_DAYS_OPTIONS.map((d) => (
              <option key={d} value={d}>{d} 天</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={tradableOnly}
            onChange={(e) => setTradableOnly(e.target.checked)}
            className="accent-cyan-400"
          />
          仅可交易（有盘口）
        </label>
        <span className="ml-auto text-[11px] text-muted-foreground">
          数据来源：/api/trading/opportunities · filters: min_days={data?.filters?.min_days ?? minDays}, tradable_only={String(data?.filters?.tradable_only ?? tradableOnly)}
        </span>
      </div>

      {/* 无可执行机会提示条 */}
      {data && data.items.length > 0 && data.executable_count === 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2.5 text-xs text-warning">
          <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          <span>{emptyHint}</span>
        </div>
      )}

      <div>
        <DataState
          loading={opps.loading}
          error={opps.error}
          hasData={!!data}
          onRetry={opps.refresh}
          stale={isStale(data?.as_of)}
          empty={!data || data.items.length === 0}
          emptyHint={emptyHint}
        >
          <OpportunityTable opportunities={data?.items ?? []} />
        </DataState>
      </div>
    </PageShell>
  );
}
