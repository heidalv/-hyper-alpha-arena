"use client";

/**
 * 账户收益曲线（2026-09-23 全新实现，**不复用**主页旧的 `EquityCurve.tsx`）。
 *
 * ## 为什么重做（旧实现的问题，见后端 `build_paper_equity_series` docstring）
 * 1. 旧曲线用 `paper_orders` 逐单 pnl−fee 累加，而订单表没有 `position_id`、历史 fee 漏记 ⇒ 口径不是权威；
 * 2. 旧曲线历史段是"已实现"，却把**末点强制改写为含浮动的 total_equity** ⇒ 两种口径混在一条线上；
 * 3. 旧曲线不含资金费；4. 旧曲线降采样 `[::step]` 等步长抽点 ⇒ 吃掉峰谷，回撤读数不可信。
 *
 * ## 本实现
 * - 数据源二选一（同一契约）：
 *   · `source="paper"` → `/api/paper/equity-series`（逐笔平仓事件溯源：已实现 = 初始 + Σ(PnL−手续费−资金费)）；
 *   · `source="hft"`   → `/api/hft/equity-series`（lane_ledger 逐笔 `notional × net_bp / 1e4` 累计净收益）；
 * - **日期选择**：7d / 30d / 90d / 全部（组件自带，切换即重取）；
 * - `已实现 / 含浮动` 两条口径可切换（HFT 无资金账户 ⇒ 无浮动，自动隐藏该切换）；
 * - 顶部**对账徽标**：与账户余额表的差异原样显示；无对账基准（HFT）时明确写"无对账基准"，不谎称一致；
 * - 峰值/最大回撤由后端在未降采样序列上算；面积图 + 悬停十字线读数。
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleCheck, CircleHelp, TriangleAlert } from "lucide-react";

import { Card } from "@/components/ui/card";
import { paperApi } from "@/lib/api";
import { hftApi } from "@/lib/hft-api";
import { cn } from "@/lib/utils";

type Mode = "realized" | "total";
export type EquityPeriod = "7d" | "30d" | "90d" | "all";

const PERIODS: { key: EquityPeriod; label: string }[] = [
  { key: "7d", label: "7天" },
  { key: "30d", label: "30天" },
  { key: "90d", label: "90天" },
  { key: "all", label: "全部" },
];

/** 两种数据源的公共形状（`/paper/equity-series` 与 `/hft/equity-series` 同契约） */
type SeriesPayload = {
  period: string;
  initial_balance: number;
  realized_end: number;
  floating_now: number | null;
  equity_total_now: number;
  peak_equity: number;
  max_drawdown_usd: number;
  max_drawdown_pct: number;
  costs_in_window: { fees: number; funding_net: number | null };
  points: { t: number; v: number }[];
  /** HFT 专有：曲线被切分到哪个活动账户（用户反馈"把历史账户也算进来了"，故显式出示范围） */
  account_id?: number | null;
  account_name?: string | null;
  account_created_at?: string | null;
  reconcile: {
    realized_plus_floating: number;
    balance_total_equity: number | null;
    diff: number | null;
    ok: boolean | null;
    note?: string;
  };
  baseline?: { note?: string };
};

export function EquitySeriesCard({
  accountId,
  source = "paper",
  title = "账户收益曲线",
  initialPeriod = "30d",
  className,
}: {
  accountId?: number | null;
  source?: "paper" | "hft";
  title?: string;
  initialPeriod?: EquityPeriod;
  className?: string;
}) {
  const [mode, setMode] = useState<Mode>("realized");
  const [period, setPeriod] = useState<EquityPeriod>(initialPeriod);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  useEffect(() => setPeriod(initialPeriod), [initialPeriod]);

  const query = useQuery({
    queryKey: ["equity-series", source, accountId, period],
    queryFn: async (): Promise<SeriesPayload> => {
      if (source === "hft") {
        const days = period === "7d" ? 7 : period === "30d" ? 30 : period === "90d" ? 90 : 365;
        return (await hftApi.equitySeries(days)) as unknown as SeriesPayload;
      }
      return (await paperApi.getEquitySeries(accountId!, period)) as unknown as SeriesPayload;
    },
    enabled: source === "hft" || !!accountId,
    staleTime: 10_000,
    refetchInterval: 15_000, // 权益曲线不需要 5s（避免与聚合轮询抢 GIL）
  });
  const { data, isLoading, isError } = query;

  const pts = data?.points ?? [];
  const hasFloating = data?.floating_now != null;
  const effectiveMode: Mode = hasFloating ? mode : "realized";

  const view = useMemo(() => {
    if (pts.length === 0) return null;
    const initial = data?.initial_balance ?? 0;
    const series = pts.map((p, i) =>
      effectiveMode === "total" && i === pts.length - 1
        ? { t: p.t, v: data?.equity_total_now ?? p.v }
        : { t: p.t, v: p.v },
    );
    const vals = series.map((p) => p.v).concat(initial > 0 ? [initial] : []);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.12, Math.abs(hi) * 0.0005, 0.5);
    return { series, lo: lo - pad, hi: hi + pad, initial };
  }, [pts, effectiveMode, data]);

  const W = 1000;
  const H = 260;
  const path = useMemo(() => {
    if (!view) return { line: "", area: "" };
    const { series, lo, hi } = view;
    const x = (i: number) => (series.length <= 1 ? 0 : (i / (series.length - 1)) * W);
    const y = (v: number) => H - ((v - lo) / Math.max(1e-9, hi - lo)) * H;
    let d = "";
    series.forEach((p, i) => {
      d += `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(p.v).toFixed(2)}`;
    });
    return { line: d, area: `${d}L${W},${H}L0,${H}Z` };
  }, [view]);

  const last = pts.length > 0 ? pts[pts.length - 1] : null;
  const shownValue =
    effectiveMode === "total" ? (data?.equity_total_now ?? last?.v ?? 0) : (data?.realized_end ?? last?.v ?? 0);
  const base = data?.initial_balance ?? 0;
  const pnl = base > 0 ? shownValue - base : shownValue; // HFT 无初始资金 ⇒ 直接是累计净收益
  const pct = base > 0 ? (pnl / base) * 100 : 0;
  const up = pnl >= 0;
  const hover = hoverIdx != null && view ? { idx: hoverIdx, p: view.series[hoverIdx] } : null;
  const rc = data?.reconcile;

  return (
    <Card className={cn("p-3 glass h-full flex flex-col", className)} data-testid="equity-series-card">
      <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
        <h2 className="text-sm font-medium flex items-center gap-1.5">
          <Activity className="w-3.5 h-3.5" /> {title}
        </h2>
        <div className="flex items-center gap-2">
          {/* 日期选择 */}
          <div className="flex items-center gap-0.5" data-testid="equity-period-picker">
            {PERIODS.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setPeriod(p.key)}
                data-testid={`equity-period-${p.key}`}
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                  period === p.key
                    ? "border-cyan-400/60 text-cyan-300 bg-cyan-400/10"
                    : "border-border/50 text-muted-foreground hover:text-foreground",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          {/* 口径切换（HFT 无浮动 ⇒ 隐藏） */}
          {hasFloating && (
            <div className="flex items-center gap-0.5">
              {(["realized", "total"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  data-testid={`equity-mode-${m}`}
                  className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded border transition-colors",
                    mode === m
                      ? "border-cyan-400/60 text-cyan-300 bg-cyan-400/10"
                      : "border-border/50 text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m === "realized" ? "已实现" : "含浮动"}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 口径与自检：把"账本差异"直接摊开，而不是藏起来 */}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1.5 gap-2 flex-wrap">
        <span>
          {source === "hft"
            ? "口径：lane_ledger 逐笔（名义 × net_bp）累计净收益"
            : effectiveMode === "realized"
              ? "口径：逐笔平仓事件溯源（权威）"
              : "口径：已实现 + 当前浮盈（末点）"}
          {" · "}
          {PERIODS.find((p) => p.key === period)?.label}
          {/* [2026-09-23 · 用户反馈] HFT 曲线必须写明"只算当前活动账户"：
              账户创建时间之前的历史期数已被剔除，此处把账户与起点摊开给用户核对。 */}
          {source === "hft" && data?.account_created_at && (
            <span className="ml-1" data-testid="equity-account-scope">
              · 账户 {data.account_name ?? `#${data.account_id}`}（自{" "}
              {new Date(data.account_created_at).toLocaleString("zh-CN", {
                year: "2-digit",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })}
              起）
            </span>
          )}
        </span>
        {rc &&
          (rc.ok === true ? (
            <span
              className="flex items-center gap-1 text-profit"
              title={`已实现+浮动 ${rc.realized_plus_floating} vs 余额表 ${rc.balance_total_equity}`}
            >
              <CircleCheck className="w-3 h-3" /> 对账一致
            </span>
          ) : rc.ok === false ? (
            <span
              className="flex items-center gap-1 text-warning"
              title={`已实现+浮动 ${rc.realized_plus_floating} − 余额表 ${rc.balance_total_equity} = ${rc.diff}`}
              data-testid="equity-reconcile-warn"
            >
              <TriangleAlert className="w-3 h-3" /> 与余额表差 ${Math.abs(rc.diff ?? 0).toFixed(2)}
            </span>
          ) : (
            <span
              className="flex items-center gap-1"
              title={rc.note || "无对账基准"}
              data-testid="equity-reconcile-na"
            >
              <CircleHelp className="w-3 h-3" /> 无对账基准
            </span>
          ))}
      </div>

      <div className="flex items-baseline gap-2 mb-1 flex-wrap">
        <span className="text-lg font-bold tabular-nums num" data-testid="equity-current">
          {base > 0 ? "$" : ""}
          {shownValue.toFixed(2)}
        </span>
        <span className={cn("text-xs tabular-nums num", up ? "text-profit" : "text-loss")}>
          {up ? "+" : ""}
          {pnl.toFixed(2)}
          {base > 0 ? ` (${pct.toFixed(2)}%)` : " 累计"}
        </span>
        {effectiveMode === "total" && hasFloating && (
          <span className="text-[10px] text-muted-foreground">
            = 已实现 ${(data?.realized_end ?? 0).toFixed(2)} + 浮盈 ${(data?.floating_now ?? 0).toFixed(2)}
          </span>
        )}
      </div>

      <div
        className="relative flex-1 min-h-0"
        onMouseLeave={() => setHoverIdx(null)}
        onMouseMove={(e) => {
          if (!view) return;
          const r = e.currentTarget.getBoundingClientRect();
          const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / Math.max(1, r.width)));
          setHoverIdx(Math.round(ratio * (view.series.length - 1)));
        }}
      >
        {isLoading && !view ? (
          <div className="h-full flex items-center justify-center text-[11px] text-muted-foreground">加载中…</div>
        ) : isError || !view ? (
          <div className="h-full flex items-center justify-center text-[11px] text-muted-foreground">暂无权益数据</div>
        ) : (
          <>
            <svg
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              className="w-full h-full overflow-visible"
              data-testid="equity-svg"
            >
              {view.initial > 0 && (
                <line
                  x1={0}
                  x2={W}
                  y1={H - ((view.initial - view.lo) / Math.max(1e-9, view.hi - view.lo)) * H}
                  y2={H - ((view.initial - view.lo) / Math.max(1e-9, view.hi - view.lo)) * H}
                  stroke="currentColor"
                  className="text-muted-foreground/40"
                  strokeDasharray="4 4"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <path d={path.area} className={up ? "fill-profit/10" : "fill-loss/10"} stroke="none" />
              <path
                d={path.line}
                fill="none"
                className={up ? "stroke-profit" : "stroke-loss"}
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
                strokeLinejoin="round"
              />
              {hover && (
                <line
                  x1={(hover.idx / Math.max(1, view.series.length - 1)) * W}
                  x2={(hover.idx / Math.max(1, view.series.length - 1)) * W}
                  y1={0}
                  y2={H}
                  stroke="currentColor"
                  className="text-cyan-300/60"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {hover && (
              <div
                className="absolute top-0 -translate-x-1/2 rounded border border-border/60 bg-background/95 px-1.5 py-0.5 text-[10px] tabular-nums num pointer-events-none whitespace-nowrap"
                style={{ left: `${(hover.idx / Math.max(1, view.series.length - 1)) * 100}%` }}
              >
                <div>
                  {new Date(hover.p.t * 1000).toLocaleString("zh-CN", {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
                <div className={cn(base > 0 ? (hover.p.v >= base ? "text-profit" : "text-loss") : hover.p.v >= 0 ? "text-profit" : "text-loss")}>
                  ${hover.p.v.toFixed(2)}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex items-center justify-between text-[10px] text-muted-foreground mt-1.5 tabular-nums num gap-2 flex-wrap">
        <span>峰值 ${(data?.peak_equity ?? 0).toFixed(2)}</span>
        <span className="text-loss">
          最大回撤 ${(data?.max_drawdown_usd ?? 0).toFixed(2)}
          {data?.max_drawdown_pct != null ? `（${data.max_drawdown_pct.toFixed(2)}%）` : "（无初始资金，仅 USD）"}
        </span>
        <span>窗口手续费 ${Math.abs(data?.costs_in_window?.fees ?? 0).toFixed(2)}</span>
      </div>
    </Card>
  );
}
