"use client";

/**
 * 套利中心 · 持仓
 *
 * 设计 §3.3：跨车道统一持仓表。列 = 车道 | 标的 | 方向 | 名义 | 开仓价 | 现价 |
 * spread | funding | price | fee | slip | 净 | 持有 | 操作。
 *  - 支持按车道/标的筛选（客户端过滤）；
 *  - 行内展开显示六维归因（该持仓的 spread/funding/price/fee/slippage 净）；
 *  - 「净」列颜色化（正=profit，负=loss）；
 *  - 「一键平该车道全部」走 confirmDialog（danger + requireText）。
 *
 * 数据源 `GET /api/trading/positions`（六维账本重建）。方向无独立字段，由 qty 符号推导：
 * qty>0 多，qty<0 空，qty=0 已平。
 */
import { Suspense, useMemo, useState } from "react";
import { Wallet, ChevronRight, ChevronDown, Ban, X, Layers } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPrice, fmtPct } from "@/lib/format";
import { confirmDialog } from "@/lib/confirm";
import { toast } from "@/lib/toast";
import { ageFromAsOf, type Position, type PositionsResponse } from "@/lib/trading-api";
import { PageShell, DataState } from "@/components/arbitrage";
import { usePositions, useUnifiedAccount } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

function fmtHold(sec: number): string {
  if (!sec || sec <= 0) return "—";
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m < 60 ? `${m}m${r}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

function directionOf(p: Position): { label: string; tone: string } {
  // [F61 阶段3] 优先用后端推导的 side（long/short/flat）；旧快照无 side 时再回退 qty 推导
  if (p.side === "long") return { label: "多", tone: "text-profit" };
  if (p.side === "short") return { label: "空", tone: "text-loss" };
  if (p.side === "flat") return { label: "已平", tone: "text-muted-foreground" };
  if (p.qty > 1e-12) return { label: "多", tone: "text-profit" };
  if (p.qty < -1e-12) return { label: "空", tone: "text-loss" };
  return { label: "已平", tone: "text-muted-foreground" };
}

function bp(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}bp`;
}

export default function PositionsPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 持仓" icon={<Wallet className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "持仓" }]} />}>
      <PositionsInner />
    </Suspense>
  );
}

function PositionsInner() {
  const positions = usePositions();
  // 不传 account_id：后端按做市车道 meta.paper_account_id 自动定位统一账户
  const unified = useUnifiedAccount();
  const stream = useLaneStream();
  const [laneFilter, setLaneFilter] = useState<string>("");
  const [symbolFilter, setSymbolFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const data: PositionsResponse | null = positions.data;

  const lanes = useMemo(() => {
    return Array.from(new Set((data?.items ?? []).map((p) => p.lane_id))).sort();
  }, [data]);

  const symbols = useMemo(() => {
    return Array.from(new Set((data?.items ?? []).map((p) => p.symbol))).sort();
  }, [data]);

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    return items.filter((p) => {
      if (laneFilter && p.lane_id !== laneFilter) return false;
      if (symbolFilter && p.symbol !== symbolFilter) return false;
      return true;
    });
  }, [data, laneFilter, symbolFilter]);

  const openCount = data?.open_count ?? 0;

  const onFlattenLane = async (laneId: string) => {
    const ok = await confirmDialog({
      title: `一键平仓「${laneId}」全部持仓？`,
      description: `将平掉该车道全部持仓（本阶段后端 /api/trading/positions 无平仓写接口，仅演练确认流程，不真正改状态）。`,
      tone: "danger",
      requireText: "平仓",
      confirmText: "确认平仓",
      cancelText: "取消",
    });
    if (!ok) return;
    // 诚实提示：后端尚未提供该写接口，不伪造成功
    toast.info(`「${laneId}」平仓动作：后端未提供 /api/trading/positions 的平仓写接口，本次仅完成确认流程。`);
  };

  return (
    <PageShell
      title="套利中心 · 持仓"
      subtitle="跨车道统一持仓：钱现在压在哪些仓位"
      icon={<Wallet className="h-4 w-4" />}
      mode={stream.mode}
      asOf={data?.as_of ?? null}
      onRefresh={positions.refresh}
      refreshing={positions.loading && !positions.data}
      breadcrumb={[{ label: "套利中心" }, { label: "持仓" }]}
    >
      {/* 筛选 */}
      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect label="车道" value={laneFilter} onChange={setLaneFilter} options={lanes} allLabel="全部车道" />
        <FilterSelect label="标的" value={symbolFilter} onChange={setSymbolFilter} options={symbols} allLabel="全部标的" />
        {(laneFilter || symbolFilter) && (
          <button
            type="button"
            onClick={() => { setLaneFilter(""); setSymbolFilter(""); }}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-cyan-300"
          >
            <X className="h-3 w-3" /> 清除筛选
          </button>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          未平仓 {openCount} 笔 · 当前显示 {rows.length} 笔
        </span>
      </div>

      {/* 汇总条 */}
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <SumCell label="总敞口(gross)" value={fmtUsd(data?.gross_exposure_usd ?? 0)} />
        <SumCell label="净敞口(net)" value={`${fmtUsd(data?.net_exposure_usd ?? 0)}`} tone={(data?.net_exposure_usd ?? 0) >= 0 ? "profit" : "loss"} />
        <SumCell label="浮动盈亏" value={fmtUsd(data?.unrealized_usd ?? 0)} tone={(data?.unrealized_usd ?? 0) >= 0 ? "profit" : "loss"} />
        <SumCell label="已实现盈亏" value={fmtUsd((data?.items ?? []).reduce((s, p) => s + p.realized_usd, 0))} tone="muted" />
      </div>

      {/* 做市持仓 · 统一账户（positions.mm） */}
      <div>
        <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <Layers className="h-3.5 w-3.5" />
          </span>
          做市持仓 · 统一账户
          <span className="text-[11px] font-normal text-muted-foreground">数据源 /api/trading/account/unified · positions.mm</span>
        </h2>
        <DataState
          loading={unified.loading}
          error={unified.error}
          hasData={!!unified.data}
          onRetry={unified.refresh}
          stale={isStale(unified.data?.as_of)}
          empty={!unified.data || (unified.data.positions?.count ?? 0) === 0}
          emptyHint="当前无做市持仓（统一账户做市腿已平）"
        >
          {unified.data && (
            <>
              <div className="mb-2 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <SumCell label="做市名义" value={fmtUsd(unified.data.exposure?.mm_notional_usd ?? 0)} />
                <SumCell label="做市占比" value={fmtPct(unified.data.exposure?.mm_notional_pct ?? 0, 2)} />
                <SumCell label="做市持仓数" value={String(unified.data.positions?.count ?? 0)} />
              </div>
              <div className="overflow-x-auto rounded-xl border border-border/40">
                <table className="data-table">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left">标的</th>
                      <th className="text-center">方向</th>
                      <th className="text-right">名义</th>
                      <th className="text-right">现价</th>
                      <th className="text-right">浮动盈亏</th>
                      <th className="text-right">spread</th>
                      <th className="text-right">price</th>
                      <th className="text-right">fee</th>
                      <th className="text-right">净</th>
                      <th className="text-right">持有</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(unified.data.positions?.mm ?? []).map((p) => {
                      const dir = directionOf(p);
                      return (
                        <tr key={`${p.lane_id}-${p.symbol}`} className="border-b border-border/20">
                          <td className="font-medium">{p.symbol}</td>
                          <td className={`text-center font-medium ${dir.tone}`}>{dir.label}</td>
                          <td className="text-right font-mono tabular-nums">{fmtUsd(p.notional_usd)}</td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">{p.mark_px ? fmtPrice(p.symbol, p.mark_px) : "—"}</td>
                          <td className={cn("text-right font-mono tabular-nums", (p.unrealized_usd ?? 0) >= 0 ? "text-profit" : "text-loss")}>{fmtUsd(p.unrealized_usd ?? 0)}</td>
                          <td className="text-right font-mono tabular-nums">{bp(p.spread_bp)}</td>
                          <td className="text-right font-mono tabular-nums">{bp(p.price_bp)}</td>
                          <td className="text-right font-mono tabular-nums">{bp(p.fee_bp)}</td>
                          <td className={cn("text-right font-mono tabular-nums font-semibold", p.net_bp >= 0 ? "text-profit" : "text-loss")}>{bp(p.net_bp)}</td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtHold(p.hold_sec)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </DataState>
      </div>

      {/* 持仓表 */}
      <div>
        <DataState
          loading={positions.loading}
          error={positions.error}
          hasData={!!data}
          onRetry={positions.refresh}
          stale={isStale(data?.as_of)}
          empty={rows.length === 0}
          emptyHint={laneFilter || symbolFilter ? "当前筛选条件下无持仓" : "近 30 天无成交记录（当前无持仓）"}
        >
          <div className="overflow-x-auto rounded-xl border border-border/40">
            <table className="data-table">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="w-6" />
                  <th className="text-left">车道</th>
                  <th className="text-left">标的</th>
                  <th className="text-center">方向</th>
                  <th className="text-right">名义</th>
                  <th className="text-right">开仓价</th>
                  <th className="text-right">现价</th>
                  <th className="text-right">spread</th>
                  <th className="text-right">funding</th>
                  <th className="text-right">price</th>
                  <th className="text-right">fee</th>
                  <th className="text-right">slip</th>
                  <th className="text-right">净</th>
                  <th className="text-right">持有</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => {
                  const key = `${p.lane_id}-${p.symbol}`;
                  const dir = directionOf(p);
                  const open = expanded === key;
                  return (
                    <PositionRows
                      key={key}
                      p={p}
                      dir={dir}
                      open={open}
                      onToggle={() => setExpanded(open ? null : key)}
                      onFlatten={() => onFlattenLane(p.lane_id)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </DataState>
      </div>
    </PageShell>
  );
}

function PositionRows({
  p,
  dir,
  open,
  onToggle,
  onFlatten,
}: {
  p: Position;
  dir: { label: string; tone: string };
  open: boolean;
  onToggle: () => void;
  onFlatten: () => void;
}) {
  const negative = p.net_bp < 0;
  const closed = Math.abs(p.qty) <= 1e-12;
  return (
    <>
      <tr className={cn("border-b border-border/20 hover:bg-muted/20", closed && "opacity-80")}>
        <td className="text-center">
          <button type="button" onClick={onToggle} className="inline-flex text-muted-foreground hover:text-cyan-300" aria-label="展开归因">
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        </td>
        <td className="font-medium">{p.lane_id}</td>
        <td className="font-medium">{p.symbol}</td>
        <td className={`text-center font-medium ${dir.tone}`}>{dir.label}</td>
        <td className="text-right font-mono tabular-nums">{fmtUsd(p.notional_usd)}</td>
        <td className="text-right font-mono tabular-nums text-muted-foreground">{p.avg_px > 0 ? fmtPrice(p.symbol, p.avg_px) : "—"}</td>
        <td className="text-right font-mono tabular-nums text-muted-foreground">{p.mark_px ? fmtPrice(p.symbol, p.mark_px) : "—"}</td>
        <td className="text-right font-mono tabular-nums">{bp(p.spread_bp)}</td>
        <td className="text-right font-mono tabular-nums">{bp(p.funding_bp)}</td>
        <td className="text-right font-mono tabular-nums">{bp(p.price_bp)}</td>
        <td className="text-right font-mono tabular-nums">{bp(p.fee_bp)}</td>
        <td className="text-right font-mono tabular-nums">{bp(p.slippage_bp)}</td>
        <td className={cn("text-right font-mono tabular-nums font-semibold", negative ? "text-loss" : "text-profit")}>{bp(p.net_bp)}</td>
        <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtHold(p.hold_sec)}</td>
        <td className="text-right">
          <button
            type="button"
            onClick={onFlatten}
            className="inline-flex items-center gap-1 rounded-md border border-loss/30 bg-loss/10 px-2 py-1 text-[11px] text-loss hover:bg-loss/20"
          >
            <Ban className="h-3 w-3" /> 平该车道全部
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border/20 bg-muted/10">
          <td colSpan={15} className="px-4 py-3">
            <DetailRow p={p} />
          </td>
        </tr>
      )}
    </>
  );
}

function DetailRow({ p }: { p: Position }) {
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-muted-foreground">六维归因（该持仓，bp 加权口径） · 近 {p.fills} 笔成交</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <Attr k="spread" v={bp(p.spread_bp)} />
        <Attr k="funding" v={bp(p.funding_bp)} />
        <Attr k="price" v={bp(p.price_bp)} />
        <Attr k="fee" v={bp(p.fee_bp)} />
        <Attr k="slippage" v={bp(p.slippage_bp)} />
        <Attr k="净" v={bp(p.net_bp)} tone={p.net_bp >= 0 ? "profit" : "loss"} />
        <Attr k="已实现" v={fmtUsd(p.realized_usd)} tone={p.realized_usd >= 0 ? "profit" : "loss"} />
        <Attr k="points" v={fmtUsd(p.points_usd)} />
      </div>
    </div>
  );
}

function Attr({ k, v, tone }: { k: string; v: string; tone?: "profit" | "loss" }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("font-mono tabular-nums", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{v}</span>
    </div>
  );
}

function SumCell({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" | "muted" }) {
  return (
    <div className="rounded-md bg-muted/20 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("font-mono tabular-nums font-semibold", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{value}</div>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  allLabel: string;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <span>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-xs text-foreground outline-none focus:border-cyan-400/40"
      >
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}
