"use client";

/**
 * 套利中心 · 持仓 —— **做市库存与产能**
 *
 * 设计动机（2026-09-14 用户反馈「没有实时数据、都是莫名其妙的数据、一个有用的
 * 都没有」）：旧页面按「方向性交易」组织——重复两遍的开仓价/持有时间/浮动盈亏，
 * 还有一个自认「后端没接口、仅演练确认」的假平仓按钮。对做市商而言这些信息量为
 * 零：做市是高频往返吃价差，真正要回答的是
 *   ① 现在挂着什么单、有没有被闸门挡住（实时运行）
 *   ② 钱压在哪些库存上、离敞口上限还有多远（库存与敞口）
 *   ③ 产能与收益：多少笔/小时、净额多少 bp、六维拆在哪（产能与收益）
 *   ④ 链路是否健康：数据年龄、熔断、孤儿持仓、**双账是否一致**（链路健康）
 *
 * 数据源（全部实时，接口每次现算）：
 *  - `GET /lanes/{id}/shadow`         每币运行态：库存、挂单 bid/ask/时间、孤儿持仓
 *  - `GET /lanes/{id}/shadow/report`  时代口径：成交/速率/平仓占比/六维/按标的/回撤
 *  - `GET /lanes/{id}/reconcile`      双账对账：运行态 vs 账本重建（ok=false 告警）
 *  - `GET /positions?lane_id=…`       账本重建持仓（开仓价/现价/浮动/持有）
 *  - `GET /lanes/{id}`                健康：行情年龄、熔断
 *  - `GET /config/lanes/{id}`         限额：敞口上限比例、单边超时秒数
 */
import { Suspense, useMemo, useState } from "react";
import {
  Wallet, Activity, Layers, Gauge, ShieldCheck, AlertTriangle, ChevronRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPrice, fmtNum } from "@/lib/format";
import type { Position } from "@/lib/trading-api";
import { PageShell, DataState, InventoryPanel } from "@/components/arbitrage";
import { useLanes, useLaneDetail, useLaneConfig, usePositions, useShadowStatus, useShadowReport, useReconcile } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function ageSec(asOf?: string | null): number | null {
  if (!asOf) return null;
  const t = Date.parse(asOf);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 1000;
}

function fmtHold(sec: number): string {
  if (!sec || sec <= 0) return "—";
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${s % 60}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

function bp(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}bp`;
}

function dirOf(qty: number): { label: string; tone: string } {
  if (qty > 1e-12) return { label: "多", tone: "text-profit" };
  if (qty < -1e-12) return { label: "空", tone: "text-loss" };
  return { label: "空仓", tone: "text-muted-foreground" };
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-border/40 bg-card/40 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("font-mono text-sm font-semibold tabular-nums", tone)}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

function SectionTitle({ icon, title, hint }: { icon: React.ReactNode; title: string; hint?: string }) {
  return (
    <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
      <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
        {icon}
      </span>
      {title}
      {hint && <span className="text-[11px] font-normal text-muted-foreground">{hint}</span>}
    </h2>
  );
}

export default function PositionsPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 持仓" icon={<Wallet className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "持仓" }]} />}>
      <PositionsInner />
    </Suspense>
  );
}

function PositionsInner() {
  const stream = useLaneStream();
  const lanes = useLanes();
  const allPositions = usePositions();

  // 做市车道：优先「绑定了统一账户」的车道（后端 account/unified 也是这个口径）
  const mmLanes = useMemo(() => {
    const items = lanes.data?.items ?? [];
    const bound = items.filter((l) => l.meta?.paper_account_id);
    const ms = (bound.length ? bound : items.filter((l) => (l.lane_id || "").startsWith("mm_")))
      .map((l) => ({ lane_id: l.lane_id, name: l.meta?.name || l.lane_id }));
    return ms;
  }, [lanes.data]);

  const [laneId, setLaneId] = useState<string>("");
  const activeLane = laneId || mmLanes[0]?.lane_id || "";

  const detail = useLaneDetail(activeLane);
  const shadow = useShadowStatus(activeLane);
  const report = useShadowReport(activeLane, 30);
  const rec = useReconcile(activeLane);
  const cfg = useLaneConfig(activeLane);
  const pos = usePositions(activeLane);

  const rows: Position[] = useMemo(
    () => (pos.data?.items ?? []).filter((p) => Math.abs(p.qty) > 1e-12),
    [pos.data],
  );
  const ledBySymbol = useMemo(() => {
    const m = new Map<string, Position>();
    for (const p of pos.data?.items ?? []) m.set(p.symbol, p);
    return m;
  }, [pos.data]);

  const equity = cfg.data ? Number(shadow.data?.account_equity ?? shadow.data?.equity ?? 0) : (shadow.data?.account_equity ?? shadow.data?.equity ?? 0);
  // [F94b] 上限口径必须与后端**实际执行**的闸门一致：
  //   - 组合净敞口上限 = equity × max_net_exposure_ratio（下单预留口径，单位=权益倍数）
  //   - 单币库存上限   = equity × max_net_directional_ratio
  //   - 隐含总敞口上限 = 单币上限 × 宇宙币数（严格可约束：平仓不会推高总敞口）
  const netRatio = Number(cfg.data?.limits?.max_net_exposure_ratio ?? 1);
  const symRatio = Number(cfg.data?.limits?.max_net_directional_ratio ?? 1);
  const limitUsd = equity * netRatio;
  const symLimitUsd = equity * symRatio;
  const grossLimitUsd = symLimitUsd * Math.max(1, (shadow.data?.symbols ?? []).length);
  const maxOneSide = Number(cfg.data?.limits?.max_one_side_seconds ?? 0);

  const netExposure = rows.reduce((s, p) => s + p.notional_usd * (p.qty > 0 ? 1 : -1), 0);
  const unreal = rows.reduce((s, p) => s + (p.unrealized_usd ?? 0), 0);
  const usePct = limitUsd > 0 ? (Math.abs(netExposure) / limitUsd) * 100 : 0;

  const universe = shadow.data?.symbols ?? detail.data?.meta?.symbols ?? [];
  const rate = report.data?.fill_rate_stats;
  const fl = report.data?.flatten_stats;
  const perSym = report.data?.per_symbol ?? {};
  const fillsPerHour = rate?.span_hours && rate.span_hours > 0
    ? (rate.fills ?? 0) / rate.span_hours : null;

  const orphan = Object.entries(shadow.data?.orphan_inventory ?? {}).filter(([, q]) => Math.abs(q) > 1e-12);
  const stale = (ageSec(shadow.data?.as_of) ?? 0) > 90;
  const dataAge = detail.data?.data_age_sec;
  const breaker = detail.data?.health?.breaker ?? (detail.data as { breaker?: string } | null)?.breaker ?? null;
  const lastErr = shadow.data?.last_error;

  // 非做市车道的持仓（避免与上面重复展示）
  const otherRows = useMemo(
    () => (allPositions.data?.items ?? []).filter((p) => Math.abs(p.qty) > 1e-12 && p.lane_id !== activeLane),
    [allPositions.data, activeLane],
  );

  return (
    <PageShell
      title="套利中心 · 持仓"
      subtitle="做市库存与产能（实时）"
      icon={<Wallet className="h-4 w-4" />}
      mode={stream.mode}
      asOf={shadow.data?.as_of ?? null}
      onRefresh={() => { shadow.refresh(); report.refresh(); rec.refresh(); pos.refresh(); detail.refresh(); }}
      refreshing={(shadow.loading || pos.loading) && !shadow.data}
      breadcrumb={[{ label: "套利中心" }, { label: "持仓" }]}
    >
      {/* 车道切换 */}
      {mmLanes.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">做市车道</span>
          {mmLanes.map((l) => (
            <button
              key={l.lane_id}
              type="button"
              onClick={() => setLaneId(l.lane_id)}
              className={cn(
                "rounded-lg border px-2 py-1 font-mono transition",
                l.lane_id === activeLane
                  ? "border-cyan-400/40 bg-cyan-400/10 text-cyan-200"
                  : "border-border/40 text-muted-foreground hover:text-foreground",
              )}
            >
              {l.lane_id}
            </button>
          ))}
        </div>
      )}

      {!activeLane ? (
        <div className="rounded-lg border border-muted/40 bg-muted/20 px-3 py-6 text-center text-xs text-muted-foreground">
          未发现做市车道（`/api/trading/lanes` 为空或未绑定模拟账户）
        </div>
      ) : (
        <>
          {/* KPI：做市真正关心的四个数 */}
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Kpi
              label="净敞口 / 组合上限"
              value={`${fmtUsd(netExposure)} / ${fmtUsd(limitUsd)}`}
              sub={`利用率 ${usePct.toFixed(1)}% · 单币上限 ${fmtUsd(symLimitUsd)}（下单侧预留口径）`}
              tone={netExposure >= 0 ? "text-profit" : "text-loss"}
            />
            <Kpi
              label="库存标的 / 宇宙"
              value={`${rows.length} / ${universe.length}`}
              sub={`浮动 ${fmtUsd(unreal)}`}
              tone={unreal >= 0 ? "text-profit" : "text-loss"}
            />
            <Kpi
              label="时代净额"
              value={report.data?.net_usd != null ? fmtUsd(report.data.net_usd) : "无数据"}
              sub={`${bp(report.data?.net_bp)} · ${report.data?.fills ?? 0} 笔`}
              tone={(report.data?.net_usd ?? 0) >= 0 ? "text-profit" : "text-loss"}
            />
            <Kpi
              label="成交速率"
              value={shadow.data?.fills_per_hour != null
                ? `${Number(shadow.data.fills_per_hour).toFixed(0)} 笔/小时`
                : (fillsPerHour != null ? `${fillsPerHour.toFixed(0)} 笔/小时` : "无数据")}
              sub={shadow.data?.fills_per_hour != null
                ? `本进程 ${Math.round((shadow.data.process_window_sec ?? 0) / 60)} 分钟 ${shadow.data.fills ?? 0} 笔`
                : (rate?.per_symbol_hour != null
                  ? `${rate.per_symbol_hour.toFixed(2)} 笔/标的/h × ${rate.symbols ?? 0} 标的 · 跨度 ${(rate.span_hours ?? 0).toFixed(1)}h`
                  : "等待成交")}
            />
          </div>

          {/* ① 库存与敞口 */}
          <div>
            <SectionTitle icon={<Layers className="h-3.5 w-3.5" />} title="库存与敞口"
              hint="数据源 /positions?lane_id=（账本重建，时代口径）" />
            {stale && (
              <div className="mb-2 flex items-center gap-1.5 rounded-lg border border-loss/30 bg-loss/5 px-2 py-1 text-[11px] text-loss">
                <AlertTriangle className="h-3 w-3" /> 运行态快照超过 90s 未更新（后端 tick 可能已停）
              </div>
            )}
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-xl border border-border/40 p-3">
                <InventoryPanel positions={rows} equity={equity} limitPct={netRatio * 100}
                  limitLabel="组合净敞口上限（下单预留口径）" />
                <p className="mt-2 text-[11px] text-muted-foreground">
                  上限口径：组合净敞口 ≤ {netRatio}×权益（{fmtUsd(limitUsd)}，下单时按「在挂同向腿
                  全部成交」的最坏情形预留）；单币库存 ≤ {symRatio}×权益（{fmtUsd(symLimitUsd)}）；
                  隐含总敞口上限 {fmtUsd(grossLimitUsd)}（{universe.length} 币 × 单币上限，
                  平仓不会推高总敞口 ⇒ 这是**严格**可兜住的口径）。
                  净敞口在对冲腿被平掉时会变大，故它是「尽力而为」而非硬约束。
                </p>
              </div>
              <div className="overflow-x-auto rounded-xl border border-border/40">
                <table className="data-table">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left">标的</th>
                      <th className="text-center">库存</th>
                      <th className="text-right">名义</th>
                      <th className="text-right">开仓价</th>
                      <th className="text-right">现价</th>
                      <th className="text-right">浮动</th>
                      <th className="text-right">持有</th>
                      <th className="text-center">挂单</th>
                    </tr>
                  </thead>
                  <tbody>
                    {universe.map((sym) => {
                      const st = shadow.data?.states?.[sym];
                      const p = ledBySymbol.get(sym);
                      const qty = st?.qty ?? 0;
                      const d = dirOf(qty);
                      const bid = st?.quote_bid ?? 0;
                      const ask = st?.quote_ask ?? 0;
                      const quoteAge = st?.quote_ts ? Date.now() / 1000 - st.quote_ts : 0;
                      const hold = p?.hold_sec ?? 0;
                      const left = maxOneSide > 0 && hold > 0 ? Math.max(0, maxOneSide - hold) : null;
                      return (
                        <tr key={sym} className="border-b border-border/20">
                          <td className="font-medium">{sym}</td>
                          <td className={cn("text-center font-medium", d.tone)}>{d.label}</td>
                          <td className="text-right font-mono tabular-nums">
                            {p ? fmtUsd(p.notional_usd) : "—"}
                          </td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">
                            {p && p.avg_px ? fmtPrice(sym, p.avg_px) : "—"}
                          </td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">
                            {p?.mark_px ? fmtPrice(sym, p.mark_px) : "—"}
                          </td>
                          <td className={cn("text-right font-mono tabular-nums",
                            (p?.unrealized_usd ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                            {p ? fmtUsd(p.unrealized_usd ?? 0) : "—"}
                          </td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">
                            {hold > 0 ? (
                              <span title={left != null ? `距超时平仓 ${Math.round(left)}s` : undefined}>
                                {fmtHold(hold)}{left != null && left < 120 ? " ⏳" : ""}
                              </span>
                            ) : "—"}
                          </td>
                          <td className="text-center text-[11px] font-mono">
                            {bid > 0 || ask > 0 ? (
                              <span className="text-muted-foreground">
                                {bid > 0 ? "买" : "—"}/{ask > 0 ? "卖" : "—"}
                                <span className="ml-1">{quoteAge > 0 ? `${Math.round(quoteAge)}s` : ""}</span>
                              </span>
                            ) : <span className="text-muted-foreground/50">未挂</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ② 产能与收益 */}
          <div>
            <SectionTitle icon={<Gauge className="h-3.5 w-3.5" />} title="产能与收益"
              hint="数据源 /lanes/{id}/shadow/report（时代口径，六维归因）" />
            <DataState
              loading={report.loading} error={report.error} hasData={!!report.data}
              onRetry={report.refresh} stale={stale}
              empty={!report.data || (report.data.fills ?? 0) === 0}
              emptyHint="本时代暂无成交（等待挂单被动成交）"
            >
              {report.data && (
                <div className="space-y-2">
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                    <Kpi label="成交笔数" value={String(report.data.fills ?? 0)} />
                    <Kpi label="平仓占比"
                      value={fl?.flatten_share != null ? `${(fl.flatten_share * 100).toFixed(1)}%` : "—"}
                      sub={`平仓 ${report.data.flattens ?? 0} 笔`} />
                    <Kpi label="最大回撤"
                      value={report.data.max_dd_pct != null ? `${report.data.max_dd_pct.toFixed(2)}%` : "—"}
                      sub={`权益 ${fmtUsd(report.data.equity ?? equity)}`} />
                    <Kpi label="价差捕获" value={bp(report.data.spread_bp)}
                      sub="挂单边际（挂单中价基准）" tone="text-profit" />
                    <Kpi label="价格漂移" value={bp(report.data.price_bp)}
                      sub="逆选择成本" tone={(report.data.price_bp ?? 0) >= 0 ? "text-profit" : "text-loss"} />
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-border/40">
                    <table className="data-table">
                      <thead>
                        <tr className="border-b border-border text-muted-foreground">
                          <th className="text-left">标的</th>
                          <th className="text-right">笔数</th>
                          <th className="text-right">名义</th>
                          <th className="text-right">净额</th>
                          <th className="text-right">净(bp)</th>
                          <th className="text-right">价差</th>
                          <th className="text-right">价格</th>
                          <th className="text-right">费</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(perSym).map(([sym, v]) => (
                          <tr key={sym} className="border-b border-border/20">
                            <td className="font-medium">{sym}</td>
                            <td className="text-right font-mono tabular-nums">{v.n}</td>
                            <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtUsd(v.notional)}</td>
                            <td className={cn("text-right font-mono tabular-nums font-semibold",
                              v.net_usd >= 0 ? "text-profit" : "text-loss")}>{fmtUsd(v.net_usd)}</td>
                            <td className={cn("text-right font-mono tabular-nums",
                              v.net_bp >= 0 ? "text-profit" : "text-loss")}>{bp(v.net_bp)}</td>
                            <td className="text-right font-mono tabular-nums">{bp(v.spread_bp)}</td>
                            <td className="text-right font-mono tabular-nums">{bp(v.price_bp)}</td>
                            <td className="text-right font-mono tabular-nums">{bp(v.fee_bp, 3)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    净额 = 价差 + 价格 + 费 + 资金费 + 滑点；做市的收入项是**价差捕获**，
                    主要成本是**价格漂移**（被动成交后的逆选择）。平仓占比越低越好
                    （说明靠被动腿平仓，而不是打对手价）。
                  </p>
                </div>
              )}
            </DataState>
          </div>

          {/* ③ 链路健康 */}
          <div>
            <SectionTitle icon={<ShieldCheck className="h-3.5 w-3.5" />} title="链路健康"
              hint="行情年龄 / 熔断 / 孤儿持仓 / 双账一致性" />
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Kpi label="行情数据年龄"
                value={dataAge != null ? `${Number(dataAge).toFixed(1)}s` : "—"}
                sub={dataAge != null && Number(dataAge) > 180 ? "超阈值：不报价" : "正常（≤180s）"}
                tone={dataAge != null && Number(dataAge) > 180 ? "text-loss" : "text-profit"} />
              <Kpi label="熔断" value={breaker ? String(breaker).slice(0, 28) : "无"}
                sub={lastErr ? `last_error: ${String(lastErr).slice(0, 24)}` : "无异常"}
                tone={breaker || lastErr ? "text-loss" : "text-profit"} />
              <Kpi label="孤儿持仓"
                value={orphan.length === 0 ? "无" : orphan.map(([s, q]) => `${s} ${fmtNum(q, 4)}`).join(" · ")}
                sub="宇宙外残留仓位（应为空）"
                tone={orphan.length === 0 ? "text-profit" : "text-loss"} />
              <Kpi label="双账一致"
                value={rec.data ? (rec.data.ok ? "一致 ✅" : `分叉 ${rec.data.mismatches.length} ❌`) : "—"}
                sub={rec.data?.ok
                  ? `运行态 == 账本重建（${rec.data.checked} 币种）`
                  : (rec.data?.mismatches ?? []).map((m) => `${m.symbol} ${fmtUsd(m.diff_usd)}`).join(" · ")}
                tone={rec.data?.ok === false ? "text-loss" : rec.data ? "text-profit" : undefined} />
            </div>
            {rec.data && !rec.data.ok && (
              <div className="mt-2 rounded-lg border border-loss/40 bg-loss/5 px-3 py-2 text-[11px] text-loss">
                <div className="flex items-center gap-1.5 font-medium">
                  <AlertTriangle className="h-3 w-3" /> 账本与运行态分叉：前端持仓可能包含实盘并不存在的仓位
                </div>
                <div className="mt-1 font-mono">
                  {(rec.data.mismatches ?? []).map((m) => (
                    <div key={m.symbol}>
                      {m.symbol}: 运行态 {fmtNum(m.runtime_qty, 8)} / 账本 {fmtNum(m.ledger_qty, 8)} = {fmtUsd(m.diff_usd)}
                    </div>
                  ))}
                </div>
                <div className="mt-1">修复：`python scripts/mm_reconcile_positions.py --fix`（写净额为 0 的调整行，不制造盈亏）</div>
              </div>
            )}
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground md:grid-cols-4">
              <div>本进程 tick <span className="font-mono text-foreground">{shadow.data?.ticks ?? 0}</span></div>
              <div>本进程成交 <span className="font-mono text-foreground">{shadow.data?.fills ?? 0}</span></div>
              <div>腿量 <span className="font-mono text-foreground">{fmtUsd(shadow.data?.fill_notional ?? 0)}</span>（复利 {shadow.data?.compound_ratio ?? 0}）</div>
              <div>账户 <span className="font-mono text-foreground">#{shadow.data?.account_id ?? "—"}</span> · {shadow.data?.venue ?? ""}</div>
            </div>
            {/* [F98] 实盘实际挂宽/σ：k_vol>0 后挂宽随波动变化，成交变少时要能立刻看到是不是挂太宽 */}
            {shadow.data?.avg_width_bp && (
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                <span>实盘平均挂宽（半宽）</span>
                <span className="font-mono text-foreground">
                  买 {shadow.data.avg_width_bp.bid != null ? `${shadow.data.avg_width_bp.bid.toFixed(2)}bp` : "—"}
                  {" / "}
                  卖 {shadow.data.avg_width_bp.ask != null ? `${shadow.data.avg_width_bp.ask.toFixed(2)}bp` : "—"}
                </span>
                <span>平均 σ <span className="font-mono text-foreground">{shadow.data.avg_sigma != null ? shadow.data.avg_sigma.toFixed(2) : "—"}</span></span>
                <span>报价决策 <span className="font-mono text-foreground">{shadow.data.quoted_decisions ?? 0}</span></span>
                <span className="text-muted-foreground/70">
                  （半宽 ×2 = 双边跨度；基准 {`w_base × (1 + k_vol×σ)`}）
                </span>
              </div>
            )}
            {/* [F95] 闸门拦截分布：回答「过去这一小时是哪道闸门在吃成交」 */}
            {(shadow.data?.skip_counts && Object.keys(shadow.data.skip_counts).length > 0) && (
              <div className="mt-2 rounded-lg border border-border/40 px-3 py-2">
                <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span>闸门拦截分布（本进程累计）</span>
                  {shadow.data.side_counts && (
                    <span className="font-mono">
                      双边报价 {shadow.data.side_counts.both ?? 0} · 单边 {shadow.data.side_counts.one ?? 0} · 未挂 {shadow.data.side_counts.none ?? 0}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-mono">
                  {Object.entries(shadow.data.skip_counts).map(([k, v]) => (
                    <span key={k} className="text-muted-foreground">
                      {k} <span className="text-foreground">{v}</span>
                    </span>
                  ))}
                </div>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  net_exposure/symbol_exposure = 敞口闸（下单侧预留口径）；ofi_toxic_* = 流向毒性闸；
                  vol_pause = 波动闸。拦截多≠异常，但可据此判断哪道闸在限制产能。
                </p>
              </div>
            )}
          </div>

          {/* ④ 其它车道持仓（做市已在上方展开，这里只列别的车道） */}
          {otherRows.length > 0 && (
            <div>
              <SectionTitle icon={<Activity className="h-3.5 w-3.5" />} title="其它车道持仓" />
              <div className="overflow-x-auto rounded-xl border border-border/40">
                <table className="data-table">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left">车道</th>
                      <th className="text-left">标的</th>
                      <th className="text-center">方向</th>
                      <th className="text-right">名义</th>
                      <th className="text-right">开仓价</th>
                      <th className="text-right">现价</th>
                      <th className="text-right">浮动</th>
                      <th className="text-right">持有</th>
                    </tr>
                  </thead>
                  <tbody>
                    {otherRows.map((p) => {
                      const d = dirOf(p.qty);
                      return (
                        <tr key={`${p.lane_id}-${p.symbol}`} className="border-b border-border/20">
                          <td className="font-mono text-[11px]">{p.lane_id}</td>
                          <td className="font-medium">{p.symbol}</td>
                          <td className={cn("text-center font-medium", d.tone)}>{d.label}</td>
                          <td className="text-right font-mono tabular-nums">{fmtUsd(p.notional_usd)}</td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">{p.avg_px ? fmtPrice(p.symbol, p.avg_px) : "—"}</td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">{p.mark_px ? fmtPrice(p.symbol, p.mark_px) : "—"}</td>
                          <td className={cn("text-right font-mono tabular-nums", (p.unrealized_usd ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                            {fmtUsd(p.unrealized_usd ?? 0)}
                          </td>
                          <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtHold(p.hold_sec)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                做市车道以外的持仓（六维归因重建）。做市车道的库存请看上方「库存与敞口」。
                <ChevronRight className="ml-1 inline h-3 w-3" />
                平仓操作需实时风控接口（当前后端未提供写接口，故本页不放置无法生效的按钮）。
              </p>
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
