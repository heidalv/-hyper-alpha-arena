"use client";

/**
 * 套利中心 · 车道
 *
 * 设计 §3.2：车道列表 + 详情（`?lane=<id>`）。
 * 列表：车道 | 模式 | 净期望 | 近7天 | 敞口/预算 | 晋升进度 | 操作。
 * 详情四块：成交与捕获 / 库存与敞口 / 报价参数(只读) / 晋升判定矩阵 + 操作（暂停/降级/影子tick）。
 *
 * 静止导出约束：`useSearchParams` 必须包在 `<Suspense>` 内。
 */
import { Suspense, useCallback, useMemo, useState, type ReactNode } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  GitBranch, PauseCircle, PlayCircle, ShieldAlert, ArrowLeft, Wrench, RotateCw,
  Wallet, Activity, TrendingUp,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPct, fmtNum } from "@/lib/format";
import { confirmDialog } from "@/lib/confirm";
import { toast } from "@/lib/toast";
import { ageFromAsOf, tradingApi, type LaneSummary } from "@/lib/trading-api";
import {
  PageShell, DataState, EdgeBadge, PromotionBoard, InventoryPanel,
} from "@/components/arbitrage";
import {
  useLanes, useAttribution, usePortfolioSummary, useLaneDetail, useLanePromotion,
  useShadowStatus, useShadowReport, useLaneConfig, usePositions,
} from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

/** 按「最近成功拉取时间」判定 stale（用于无 as_of 的端点：lanes / attribution / shadow report） */
function staleByTimestamp(ts: number | null): boolean {
  return ts != null && Date.now() - ts > 90_000;
}

const MODE_TONE: Record<string, string> = {
  paper: "bg-cyan-400/15 text-cyan-300 border-cyan-400/25",
  live: "bg-loss/15 text-loss border-loss/30",
  disabled: "bg-muted/40 text-muted-foreground border-muted",
};

function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium", MODE_TONE[mode] ?? "bg-muted/40 text-muted-foreground border-muted")}>
      {{ paper: "模拟盘", live: "实盘", disabled: "已停用" }[mode] ?? mode}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "active"
      ? "bg-profit text-profit"
      : status === "paused"
        ? "bg-warning text-warning"
        : "bg-muted-foreground text-muted-foreground";
  const label = { active: "运行中", paused: "已暂停", stopped: "已停止" }[status] ?? status;
  return (
    <span className={cn("inline-flex items-center gap-1 text-[11px]", tone.split(" ")[1])}>
      <span className={cn("h-1.5 w-1.5 rounded-full", tone.split(" ")[0])} />
      {label}
    </span>
  );
}

export default function LanesPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 车道" icon={<GitBranch className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "车道" }]} />}>
      <LanesInner />
    </Suspense>
  );
}

function LanesInner() {
  const searchParams = useSearchParams();
  const laneId = searchParams.get("lane");

  if (laneId) return <LaneDetail laneId={laneId} />;
  return <LaneList />;
}

// ════════════════════════════════════════════════════════
// 列表
// ════════════════════════════════════════════════════════
function LaneList() {
  const lanes = useLanes();
  const attribution = useAttribution(7);
  const summary = usePortfolioSummary();
  const stream = useLaneStream();

  const byLane = useMemo(() => {
    const m = new Map<string, number>();
    (attribution.data?.by_lane ?? []).forEach((b) => m.set(b.lane_id, b.net_usd));
    return m;
  }, [attribution.data]);

  const equity = summary.data?.equity ?? null;
  const budgets = summary.data?.lane_budgets ?? {};
  const rows = (lanes.data?.items ?? []).slice();
  rows.sort((a, b) => (budgets[b.lane_id] ?? 0) - (budgets[a.lane_id] ?? 0));

  return (
    <PageShell
      title="套利中心 · 车道"
      subtitle="每条车道赚钱吗？该不该加/减/停？"
      icon={<GitBranch className="h-4 w-4" />}
      mode={stream.mode}
      asOf={summary.data?.as_of ?? null}
      onRefresh={() => { lanes.refresh(); attribution.refresh(); summary.refresh(); }}
      refreshing={lanes.loading && !lanes.data}
      breadcrumb={[{ label: "套利中心" }, { label: "车道" }]}
    >
      <DataState
        loading={lanes.loading}
        error={lanes.error}
        hasData={!!lanes.data}
        onRetry={lanes.refresh}
        stale={staleByTimestamp(lanes.lastUpdated)}
        empty={rows.length === 0}
        emptyHint="暂无车道（可调用 /api/trading/lanes/seed 初始化）"
      >
        <div className="overflow-x-auto rounded-xl border border-border/40">
          <table className="data-table">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left">车道</th>
                <th className="text-left">模式</th>
                <th className="text-right">净期望</th>
                <th className="text-right">近7天净收益</th>
                <th className="text-right">预算</th>
                <th className="text-right">晋升进度</th>
                <th className="text-left">状态</th>
                <th className="text-left">统一账户</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((lane) => {
                const budgetUsd = budgets[lane.lane_id] ?? null;
                const budgetPct = equity && budgetUsd != null ? (budgetUsd / equity) * 100 : null;
                const promo = lane.promotion;
                const pnl7d = byLane.get(lane.lane_id) ?? null;
                return (
                  <tr key={lane.lane_id} className="border-b border-border/20 hover:bg-muted/20">
                    <td className="font-medium">
                      <a
                        href={`/arbitrage/lanes?lane=${encodeURIComponent(lane.lane_id)}`}
                        className="hover:text-cyan-300"
                      >
                        {lane.meta?.name || lane.lane_id}
                      </a>
                    </td>
                    <td><ModeBadge mode={lane.mode} /></td>
                    <td className="text-right"><EdgeBadge edge={lane.edge} /></td>
                    <td className={cn("text-right font-mono tabular-nums", (pnl7d ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                      {pnl7d == null ? "—" : fmtUsd(pnl7d)}
                    </td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">
                      {budgetPct == null ? "—" : `${fmtPct(budgetPct, 0)}（${budgetUsd ?? "—"}）`}
                    </td>
                    <td className="text-right">
                      {promo ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="h-1.5 w-14 overflow-hidden rounded-full bg-muted/30">
                            <span className={cn("block h-full", promo.ready ? "bg-profit" : "bg-cyan-400")} style={{ width: `${Math.min(100, promo.progress_pct)}%` }} />
                          </span>
                          <span className={cn("font-mono tabular-nums", promo.ready ? "text-profit" : "text-muted-foreground")}>
                            {promo.progress_pct.toFixed(0)}%
                          </span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td><StatusDot status={lane.status} /></td>
                    <td>
                      {lane.meta?.paper_account_id || lane.meta?.strategy_type ? (
                        <a
                          href="/arbitrage#unified-account"
                          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-cyan-300"
                          title="查看统一模拟账户视图"
                        >
                          <span className="rounded bg-cyan-400/10 px-1.5 py-0.5 font-mono text-cyan-300">
                            acct {lane.meta?.paper_account_id ?? "—"}
                          </span>
                          {typeof lane.meta?.strategy_type === "string" && lane.meta.strategy_type ? (
                            <span className="rounded border border-border/40 px-1.5 py-0.5 font-medium">{lane.meta.strategy_type}</span>
                          ) : null}
                        </a>
                      ) : (
                        <span className="text-muted-foreground/50">—</span>
                      )}
                    </td>
                    <td className="text-right">
                      <a
                        href={`/arbitrage/lanes?lane=${encodeURIComponent(lane.lane_id)}`}
                        className="inline-flex items-center gap-1 rounded-md border border-border/40 px-2 py-1 text-[11px] text-muted-foreground hover:border-cyan-400/30 hover:text-cyan-300"
                      >
                        详情
                      </a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </DataState>
    </PageShell>
  );
}

// ════════════════════════════════════════════════════════
// 详情
// ════════════════════════════════════════════════════════
function LaneDetail({ laneId }: { laneId: string }) {
  const router = useRouter();
  const lane = useLaneDetail(laneId);
  const promo = useLanePromotion(laneId);
  const shadow = useShadowStatus(laneId);
  const report = useShadowReport(laneId, 30);
  const config = useLaneConfig(laneId);
  const positions = usePositions(laneId);
  const stream = useLaneStream();

  const [busy, setBusy] = useState<string | null>(null);
  const laneData = lane.data;
  const name = laneData?.meta?.name || laneId;

  const mutate = useCallback(
    async (key: string, op: () => Promise<unknown>, msg: string) => {
      setBusy(key);
      try {
        const res = (await op()) as { ok?: boolean };
        if (res && res.ok === false) throw new Error("操作返回失败");
        toast.success(msg);
        // 操作后刷新关联数据
        lane.refresh();
        promo.refresh();
        shadow.refresh();
        report.refresh();
        positions.refresh();
      } catch (e) {
        toast.error(`操作失败：${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setBusy(null);
      }
    },
    [lane, promo, shadow, report, positions]
  );

  const onPause = useCallback(async () => {
    if (!laneData) return;
    const ok = await confirmDialog({
      title: laneData.status === "paused" ? "恢复此车道？" : "暂停此车道？",
      description: `车道「${name}」（${laneId}）${laneData.status === "paused" ? "将恢复为 active" : "将暂停为 paused"}。`,
      tone: laneData.status === "paused" ? "primary" : "warning",
      confirmText: laneData.status === "paused" ? "恢复" : "暂停",
      cancelText: "取消",
    });
    if (!ok) return;
    void mutate("pause", () => tradingApi.setStatus(laneId, laneData.status === "paused" ? "active" : "paused"), laneData.status === "paused" ? "已恢复" : "已暂停");
  }, [laneData, laneId, name, mutate]);

  const onDemote = useCallback(async () => {
    if (!laneData) return;
    const ok = await confirmDialog({
      title: `降级车道「${name}」？`,
      description: `降级将把模式切换为 disabled（停止接收新信号）。该操作需输入确认词「降级」。`,
      tone: "danger",
      requireText: "降级",
      confirmText: "降级",
      cancelText: "取消",
    });
    if (!ok) return;
    void mutate("demote", () => tradingApi.setMode(laneId, "disabled"), "已降级（disabled）");
  }, [laneData, laneId, name, mutate]);

  const onTick = useCallback(async () => {
    void mutate("tick", () => tradingApi.shadowTick(laneId), "已推进一次影子 tick");
  }, [laneId, mutate]);

  return (
    <PageShell
      title={`套利中心 · 车道详情`}
      subtitle={name}
      icon={<GitBranch className="h-4 w-4" />}
      mode={stream.mode}
      asOf={lane.data?.updated_at ?? null}
      onRefresh={() => { lane.refresh(); promo.refresh(); shadow.refresh(); report.refresh(); config.refresh(); positions.refresh(); }}
      refreshing={lane.loading && !lane.data}
      breadcrumb={[{ label: "套利中心", href: "/arbitrage" }, { label: "车道", href: "/arbitrage/lanes" }, { label: name }]}
    >
      {/* 返回 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => router.push("/arbitrage/lanes")}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-cyan-300"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> 返回车道列表
        </button>
      </div>

      {/* [重设计 2026-09-14] ① 账户总览（最显眼：权益 + 盈亏 + 复利） */}
      <Card className="glass border-cyan-400/20 p-4">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
          <div>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Wallet className="h-3.5 w-3.5" />
              模拟账户权益
              <span className="rounded bg-cyan-400/10 px-1.5 py-0.5 font-mono text-cyan-300">
                acct {shadow.data?.account_id ?? laneData?.meta?.paper_account_id ?? "—"}
              </span>
            </div>
            <div className="mt-1 font-mono text-3xl font-bold tabular-nums">
              {fmtUsd(accountEquity(laneData, shadow.data) ?? 0)}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {laneData?.mode === "paper" ? "模拟盘（影子期直跑）" : laneData?.mode}{" "}
              · 场地 {laneData?.meta?.venue ?? "—"} · 币种{" "}
              {(laneData?.meta?.symbols ?? shadow.data?.symbols ?? []).join("/")}
            </div>
            {(shadow.data?.compound_ratio ?? 0) > 0 && (
              <div className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-profit/20 bg-profit/5 px-2 py-0.5 text-[11px] text-profit">
                <TrendingUp className="h-3 w-3" />
                复利开启：每腿 = 权益 × {fmtPct(shadow.data?.compound_ratio ?? 0, 0)}
                （当前 {fmtUsd(shadow.data?.fill_notional ?? 0)}/腿）
              </div>
            )}
          </div>
          <div className="flex gap-6 text-right">
            <div>
              <div className="text-[11px] text-muted-foreground">今日净收益</div>
              <div className={cn("font-mono text-2xl font-bold tabular-nums", pnlTone(laneData?.pnl_today_usd))}>
                {laneData?.pnl_today_usd == null ? "—" : fmtUsd(laneData.pnl_today_usd)}
              </div>
              <div className="text-[11px] text-muted-foreground">
                {laneData?.fills_today == null ? "" : `${laneData.fills_today} 笔`}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-muted-foreground">近 7 天净收益</div>
              <div className={cn("font-mono text-2xl font-bold tabular-nums", pnlTone(laneData?.pnl_7d_usd))}>
                {laneData?.pnl_7d_usd == null ? "—" : fmtUsd(laneData.pnl_7d_usd)}
              </div>
              <div className="text-[11px] text-muted-foreground">
                {laneData?.notional_7d == null ? "" : `名义 ${fmtUsd(laneData.notional_7d)}`}
              </div>
            </div>
            <div className="flex flex-col items-end gap-1">
              <ModeBadge mode={laneData?.mode ?? "paper"} />
              <StatusDot status={laneData?.status ?? "stopped"} />
              <span className="font-mono text-[11px] text-muted-foreground">lane_id: {laneData?.lane_id}</span>
            </div>
          </div>
        </div>
      </Card>

      {/* ② 实时运行与挂单明细 */}
      <Card className="glass p-4">
        <BlockTitle icon={<Activity className="h-3.5 w-3.5" />} title="实时运行与挂单" />
        <DataState
          loading={shadow.loading}
          error={shadow.error}
          hasData={!!shadow.data}
          onRetry={shadow.refresh}
          stale={isStale(shadow.data?.as_of)}
        >
          {shadow.data && (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>ticks <span className="font-mono tabular-nums text-foreground">{shadow.data.ticks}</span></span>
                <span>进程内成交 <span className="font-mono tabular-nums text-foreground">{shadow.data.fills}</span></span>
                <span>数据年龄 <span className={cn("font-mono tabular-nums", (laneData?.data_age_sec ?? 0) > 180 ? "text-loss" : "text-foreground")}>{laneData?.data_age_sec == null ? "—" : `${laneData.data_age_sec.toFixed(0)}s`}</span></span>
                <span>熔断 <span className={cn("font-mono", laneData?.health?.breaker ? "text-loss" : "text-profit")}>{laneData?.health?.breaker ?? "无"}</span></span>
                {shadow.data.last_error && (
                  <span className="text-loss">错误: {shadow.data.last_error}</span>
                )}
              </div>
              <div className="overflow-x-auto rounded-lg border border-border/40">
                <table className="data-table text-xs">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      <th className="text-left">币种</th>
                      <th className="text-right">仓位 qty</th>
                      <th className="text-right">仓位名义</th>
                      <th className="text-right">均价</th>
                      <th className="text-right">买单价</th>
                      <th className="text-right">卖单价</th>
                      <th className="text-right">总挂宽</th>
                      <th className="text-right">波动基准</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(shadow.data?.symbols ?? []).map((sym) => {
                      const st = shadow.data?.states[sym];
                      if (!st) return null;
                      const mid = (st.quote_bid && st.quote_ask)
                        ? (st.quote_bid + st.quote_ask) / 2
                        : st.quote_mid || st.avg_mid || 0;
                      const widthBp = mid && st.quote_bid && st.quote_ask
                        ? ((st.quote_ask - st.quote_bid) / mid) * 1e4
                        : null;
                      return (
                        <tr key={sym} className="border-b border-border/20">
                          <td className="font-medium">{sym}</td>
                          <td className="text-right font-mono tabular-nums">{fmtNum(st.qty ?? 0, 6)}</td>
                          <td className="text-right font-mono tabular-nums">{mid && st.qty ? fmtUsd(Math.abs(st.qty) * mid) : "—"}</td>
                          <td className="text-right font-mono tabular-nums">{st.avg_px ? fmtNum(st.avg_px, 1) : "—"}</td>
                          <td className={cn("text-right font-mono tabular-nums", st.quote_bid ? "text-profit" : "text-muted-foreground/50")}>{st.quote_bid ? fmtNum(st.quote_bid, 1) : "—"}</td>
                          <td className={cn("text-right font-mono tabular-nums", st.quote_ask ? "text-loss" : "text-muted-foreground/50")}>{st.quote_ask ? fmtNum(st.quote_ask, 1) : "—"}</td>
                          <td className="text-right font-mono tabular-nums">{widthBp == null ? "—" : `${widthBp.toFixed(1)}bp`}</td>
                          <td className="text-right font-mono tabular-nums">{st.vol_baseline_bp == null ? "—" : `${fmtNum(st.vol_baseline_bp, 3)}bp`}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                挂单宽 0 表示该侧未挂（减仓侧等待成交或数据间歇）；10bp = 双边各 5bp。
              </p>
            </>
          )}
        </DataState>
      </Card>

      {/* 成交与捕获 + 库存与敞口 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="glass p-4">
          <BlockTitle icon={<RotateCw className="h-3.5 w-3.5" />} title="成交与捕获（30 天）" />
          <DataState
            loading={report.loading}
            error={report.error}
            hasData={!!report.data}
            onRetry={report.refresh}
            stale={isStale(laneData?.updated_at)}
            empty={!report.data || ((report.data.fills ?? 0) === 0 && (report.data.net_usd ?? null) == null && (report.data.net_bp ?? null) === 0)}
            emptyHint="该车道影子期尚无成交记录"
          >
            {report.data && (
              <>
                <div className={cn("mb-2 font-mono text-2xl font-bold tabular-nums", pnlTone(report.data.net_usd))}>
                  {report.data.net_usd == null ? "—" : fmtUsd(report.data.net_usd)}
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <KV k="影子成交" v={fmtNum(report.data.fills ?? 0, 0)} />
                  <KV k="名义" v={fmtUsd(report.data.notional ?? 0)} />
                  <KV k="价差捕获(spread)" v={bp(report.data.spread_bp)} tone={signTone(report.data.spread_bp)} />
                  <KV k="逆选择(price)" v={bp(report.data.price_bp)} tone={signTone(report.data.price_bp)} />
                  <KV k="费率(fee)" v={bp(report.data.fee_bp)} tone={signTone(report.data.fee_bp)} />
                  <KV k="净边际" v={bp(report.data.net_bp)} tone={signTone(report.data.net_bp)} />
                  <KV k="平仓笔数" v={fmtNum(report.data.flattens ?? 0, 0)} />
                  <KV k="maker 费率" v={`${fmtNum(report.data.maker_fee_bp ?? 0, 2)}bp`} />
                </div>
              </>
            )}
          </DataState>
        </Card>

        <Card className="glass p-4">
          <BlockTitle icon={<Wrench className="h-3.5 w-3.5" />} title="库存与敞口" />
          <DataState
            loading={positions.loading}
            error={positions.error}
            hasData={!!positions.data}
            onRetry={positions.refresh}
            stale={isStale(positions.data?.as_of)}
          >
            <InventoryPanel
              positions={positions.data?.items ?? []}
              equity={equityFromMeta(laneData)}
              limitPct={laneData?.risk?.max_net_exposure_pct ?? null}
              limitLabel="净敞口上限"
            />
          </DataState>
        </Card>
      </div>

      {/* 报价参数（只读 + 跳转配置） */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <BlockTitle icon={<GitBranch className="h-3.5 w-3.5" />} title="报价参数（只读）" />
          <a href="/arbitrage/config" className="text-[11px] text-cyan-300 hover:underline">前往配置编辑 →</a>
        </div>
        <DataState
          loading={config.loading}
          error={config.error}
          hasData={!!config.data}
          onRetry={config.refresh}
          stale={isStale(config.data?.as_of)}
        >
          {config.data && (
            <div className="flex flex-wrap gap-x-5 gap-y-2 text-xs">
              {Object.entries(config.data.params).map(([k, v]) => (
                <span key={k} className="text-muted-foreground">
                  {/* 参数值的类型不保证是 number（可能为 null / 字符串 / 布尔）⇒
                      不能直接喂给数值格式化函数（F195：曾因此让整页崩掉 ✗）。 */}
                  {k}{" "}
                  <span className="font-mono tabular-nums text-foreground">
                    {typeof v === "number" ? fmtNum(v, 2) : String(v ?? "—")}
                  </span>
                </span>
              ))}
            </div>
          )}
        </DataState>
      </Card>

      {/* 晋升判定矩阵 */}
      <Card className="glass p-4">
        <BlockTitle icon={<ShieldAlert className="h-3.5 w-3.5" />} title="晋升判定矩阵" />
        <DataState
          loading={promo.loading}
          error={promo.error}
          hasData={!!promo.data}
          onRetry={promo.refresh}
          stale={isStale(promo.data?.promotion?.as_of)}
        >
          <PromotionBoard promotion={promo.data?.promotion} criteria={null} />
        </DataState>
      </Card>

      {/* 操作 */}
      <Card className="glass p-4">
        <BlockTitle icon={<PlayCircle className="h-3.5 w-3.5" />} title="操作" />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onPause}
            disabled={!!busy}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors disabled:opacity-50",
              laneData?.status === "paused"
                ? "border-profit/30 bg-profit/10 text-profit hover:bg-profit/20"
                : "border-warning/30 bg-warning/10 text-warning hover:bg-warning/20"
            )}
          >
            {laneData?.status === "paused" ? <PlayCircle className="h-3.5 w-3.5" /> : <PauseCircle className="h-3.5 w-3.5" />}
            {laneData?.status === "paused" ? "恢复" : "暂停"}
          </button>
          <button
            type="button"
            onClick={onDemote}
            disabled={!!busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-loss/30 bg-loss/10 px-3 py-1.5 text-xs text-loss transition-colors hover:bg-loss/20 disabled:opacity-50"
          >
            <ShieldAlert className="h-3.5 w-3.5" /> 降级（暂停并停用）
          </button>
          <button
            type="button"
            onClick={onTick}
            disabled={!!busy}
            className="inline-flex items-center gap-1.5 rounded-md border border-border/40 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-cyan-400/30 hover:text-cyan-300 disabled:opacity-50"
          >
            <RotateCw className="h-3.5 w-3.5" /> 手动推进影子 tick
          </button>
          {busy && <span className="self-center text-[11px] text-muted-foreground">处理中…</span>}
        </div>
      </Card>
    </PageShell>
  );
}

// ═══ 小部件 ═══
function BlockTitle({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
      <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
        {icon}
      </span>
      {title}
    </h2>
  );
}

function KV({ k, v, tone }: { k: string; v: string; tone?: "profit" | "loss" }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("font-mono tabular-nums", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{v}</span>
    </div>
  );
}

function bp(v: number | null | undefined): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}bp`;
}

function signTone(v: number | null | undefined): "profit" | "loss" | undefined {
  if (v == null) return undefined;
  return v >= 0 ? "profit" : "loss";
}

function equityFromMeta(lane: LaneSummary | null): number | null {
  return lane?.meta?.shadow_equity ?? null;
}

/** 账户权益：优先用影子状态里的真实账户权益（复利模式下随盈亏滚动），回退 meta.shadow_equity */
function accountEquity(lane: LaneSummary | null, shadow: import("@/lib/trading-api").ShadowStatus | null): number | null {
  if (shadow?.account_equity != null && shadow.account_equity > 0) return shadow.account_equity;
  return equityFromMeta(lane);
}

function pnlTone(v: number | null | undefined): string {
  if (v == null) return "text-muted-foreground";
  return v >= 0 ? "text-profit" : "text-loss";
}
