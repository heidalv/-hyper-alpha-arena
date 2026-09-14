"use client";

/**
 * 套利中心 · 风险与资金
 *
 * 设计 §3.5：
 *  - 熔断矩阵（车道 × data/fee/daily_loss/toxic_flow，状态灯 + 触发时间 + 原因 + 手动复位 confirmDialog）；
 *  - 敞口表（单币 / 总敞口 / 上限 / 使用率，含 max_symbol_exposure_pct）；
 *  - 熔断历史（breaker_history）；
 *  - 资金池（GET /api/trading/capital/pool：各模拟账户余额，含可用/冻结/配置）；
 *  - 「模拟触发组合级熔断」演练（POST /api/trading/risk/drill：**真写** lane health.drill，
 *    影子期调度器会真的跳过该车道；仅 paper 生效，实盘进 skipped；开启后可一键解除）。
 */
import { Suspense, useCallback, useMemo, useState } from "react";
import { ShieldAlert, FlaskConical, Wallet, Beaker, Undo2, Info, Layers } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtNum, fmtPct } from "@/lib/format";
import { confirmDialog } from "@/lib/confirm";
import { toast } from "@/lib/toast";
import { ageFromAsOf, tradingApi, type UnifiedAccountResponse } from "@/lib/trading-api";
import {
  PageShell, DataState, BreakerMatrix, ExposureTable,
} from "@/components/arbitrage";
import { useRiskSummary, useRiskBreakers, usePositions, useLanes, useCapitalPool, useUnifiedAccount } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

const LANE_LABEL: Record<string, { label: string; tone: string }> = {
  paper: { label: "模拟盘", tone: "text-cyan-300" },
  live: { label: "实盘", tone: "text-loss" },
  disabled: { label: "已停用", tone: "text-muted-foreground" },
};

export default function RiskPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 风险与资金" icon={<ShieldAlert className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "风险与资金" }]} />}>
      <RiskInner />
    </Suspense>
  );
}

function RiskInner() {
  const risk = useRiskSummary(30);
  const breakers = useRiskBreakers();
  const positions = usePositions();
  const lanes = useLanes();
  const pool = useCapitalPool();
  // 不传 account_id：后端按做市车道 meta.paper_account_id 自动定位统一账户
  const unified = useUnifiedAccount();
  const stream = useLaneStream();
  const [busyReset, setBusyReset] = useState<string | null>(null);
  const [drillBusy, setDrillBusy] = useState<boolean>(false);

  const riskData = risk.data;
  const breakerData = breakers.data;
  const equity = riskData?.equity ?? null;
  const maxSymbolPct = riskData?.limits?.max_symbol_exposure_pct ?? null;
  const maxNetPct = riskData?.limits?.max_net_exposure_pct ?? null;

  const hasPaperLane = (lanes.data?.items ?? []).some((l) => l.mode === "paper");

  // 演练是否激活：任意车道的熔断 reason 为 "drill"（后端把 health.breaker 写成 drill）即视为激活
  const drillActive = (breakerData?.items ?? []).some((r) => r.reason === "drill");

  const laneModeOf = useMemo(() => {
    const m = new Map<string, string>();
    (lanes.data?.items ?? []).forEach((l) => m.set(l.lane_id, l.mode));
    return m;
  }, [lanes.data]);

  const onResetBreaker = useCallback(async (laneId: string, breaker: string) => {
    const ok = await confirmDialog({
      title: `手动复位熔断「${breaker}」？`,
      description: `车道 ${laneId} 的「${breaker}」熔断将被复位为正常。该操作会修改后端状态。`,
      tone: "warning",
      confirmText: "确认复位",
      cancelText: "取消",
    });
    if (!ok) return;
    setBusyReset(`${laneId}::${breaker}`);
    try {
      const res = await tradingApi.resetBreaker(laneId, breaker);
      if (res && res.ok === false) throw new Error("复位失败");
      toast.success(`已复位 ${laneId} 的「${breaker}」熔断`);
      breakers.refresh();
      risk.refresh();
      lanes.refresh();
    } catch (e) {
      toast.error(`复位失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyReset(null);
    }
  }, [breakers, risk, lanes]);

  const runDrill = useCallback(async (enable: boolean) => {
    setDrillBusy(true);
    try {
      const res = await tradingApi.riskDrill(enable, undefined, "手动组合级熔断演练");
      const affected = res.affected ?? [];
      const skipped = res.skipped ?? [];
      if (enable) {
        if (affected.length === 0) {
          toast.warning("演练未覆盖任何车道（全部被跳过）：" + skipped.map((s) => `${s.lane_id}(${s.reason})`).join("、"));
        } else {
          toast.success(`演练已开启：${affected.length} 条 paper 车道已进入演练（影子期将跳过报价）`);
          if (skipped.length) toast.info("被跳过：" + skipped.map((s) => `${s.lane_id}(${s.reason})`).join("、"));
        }
      } else {
        toast.success("演练已解除：" + (affected.length ? `${affected.join("、")} 恢复正常` : "无激活车道"));
      }
      breakers.refresh();
      risk.refresh();
      lanes.refresh();
    } catch (e) {
      toast.error(`演练操作失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDrillBusy(false);
    }
  }, [breakers, risk, lanes]);

  const onDrillEnable = useCallback(async () => {
    if (!hasPaperLane) {
      toast.warning("无 paper 模式车道，演练不会命中任何车道");
      return;
    }
    const ok = await confirmDialog({
      title: "模拟触发组合级熔断演练？",
      description: "这是**真演练**：会把 paper 车道 health.drill 置为 true，影子期调度器将真的跳过报价（不再挂单）。仅 paper 生效，实盘会进 skipped。",
      tone: "danger",
      requireText: "触发",
      confirmText: "确认触发",
      cancelText: "取消",
    });
    if (!ok) return;
    void runDrill(true);
  }, [hasPaperLane, runDrill]);

  const onDrillDisable = useCallback(async () => {
    const ok = await confirmDialog({
      title: "解除组合级熔断演练？",
      description: "将把已演练的 paper 车道 health.drill 置为 false，恢复影子期正常报价。",
      tone: "warning",
      confirmText: "确认解除",
      cancelText: "取消",
    });
    if (!ok) return;
    void runDrill(false);
  }, [runDrill]);

  const breakerHistoryTotal = breakerData?.history_total_30d ?? riskData?.breaker_history_total ?? 0;

  return (
    <PageShell
      title="套利中心 · 风险与资金"
      subtitle="熔断了吗？钱够不够？"
      icon={<ShieldAlert className="h-4 w-4" />}
      mode={stream.mode}
      asOf={riskData?.as_of ?? null}
      onRefresh={() => { risk.refresh(); breakers.refresh(); positions.refresh(); lanes.refresh(); pool.refresh(); unified.refresh(); }}
      refreshing={risk.loading && !risk.data}
      breadcrumb={[{ label: "套利中心" }, { label: "风险与资金" }]}
    >
      {/* 熔断矩阵 */}
      <Card className="glass p-4">
        <BlockTitle icon={<ShieldAlert className="h-3.5 w-3.5" />} title="熔断矩阵 · 车道 × 类型" right={busyReset ? `复位中：${busyReset}…` : `近 30 天触发 ${fmtNum(breakerHistoryTotal, 0)} 次`} />
        <DataState
          loading={breakers.loading}
          error={breakers.error}
          hasData={!!breakerData}
          onRetry={breakers.refresh}
          stale={isStale(breakerData?.as_of)}
          empty={!breakerData || (breakerData.items.length === 0 && breakerData.history.length === 0)}
          emptyHint="暂无熔断数据（车道尚未上报健康状态）"
        >
          <BreakerMatrix
            rows={breakerData?.items ?? []}
            history={breakerData?.history ?? []}
            onReset={onResetBreaker}
          />
        </DataState>
      </Card>

      {/* 敞口表 */}
      <Card className="glass p-4">
        <BlockTitle icon={<Wallet className="h-3.5 w-3.5" />} title="敞口表" right={`权益 ${fmtUsd(equity ?? 0)} · ${riskData?.equity_source ?? ""}`} />
        <DataState
          loading={positions.loading}
          error={positions.error}
          hasData={!!positions.data}
          onRetry={positions.refresh}
          stale={isStale(positions.data?.as_of)}
        >
          <ExposureTable
            positions={positions.data?.items ?? []}
            equity={equity}
            maxSymbolPct={maxSymbolPct}
            maxNetPct={maxNetPct}
          />
        </DataState>
      </Card>

      {/* 统一账户 · 做市敞口与策略预算（GET /api/trading/account/unified） */}
      <Card className="glass p-4">
        <BlockTitle icon={<Layers className="h-3.5 w-3.5" />} title="统一账户 · 做市敞口与策略预算" right={`来源 /api/trading/account/unified`} />
        <DataState
          loading={unified.loading}
          error={unified.error}
          hasData={!!unified.data}
          onRetry={unified.refresh}
          stale={isStale(unified.data?.as_of)}
          empty={!unified.data}
          emptyHint="暂无统一账户数据"
        >
          {unified.data && (
            <BudgetPanel data={unified.data} />
          )}
        </DataState>
      </Card>

      {/* 资金池（GET /api/trading/capital/pool） */}
      <Card className="glass p-4">
        <BlockTitle icon={<Wallet className="h-3.5 w-3.5" />} title="资金池 · 模拟账户"
          right={`车道占用 ${fmtUsd(pool.data?.lane_bound_equity ?? equity ?? 0)}`
            + ((pool.data?.unbound_equity ?? 0) > 0
              ? ` · 另有历史账户 ${fmtUsd(pool.data?.unbound_equity ?? 0)}（未被任何车道使用）`
              : "")
            + ` · 合计 ${fmtUsd(pool.data?.total_equity ?? equity ?? 0)}`} />
        <DataState
          loading={pool.loading}
          error={pool.error}
          hasData={!!pool.data}
          onRetry={pool.refresh}
          stale={isStale(pool.data?.as_of)}
          empty={!pool.data || pool.data.items.length === 0}
          emptyHint="暂无模拟账户资金池记录"
        >
          <div className="overflow-x-auto rounded-xl border border-border/40">
            <table className="data-table">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left">账户</th>
                  <th className="text-right">account_id</th>
                  <th className="text-right">总权益</th>
                  <th className="text-right">可用</th>
                  <th className="text-right">冻结</th>
                  <th className="text-left">状态</th>
                  <th className="text-left">配置</th>
                  <th className="text-left">车道绑定</th>
                </tr>
              </thead>
              <tbody>
                {(pool.data?.items ?? []).map((a) => (
                  <tr key={a.account_id} className="border-b border-border/20">
                    <td className="font-medium">{a.name}</td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">{a.account_id}</td>
                    <td className="text-right font-mono tabular-nums">{fmtUsd(a.total_equity)}</td>
                    <td className="text-right font-mono tabular-nums text-profit">{fmtUsd(a.available_balance)}</td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtUsd(a.frozen_balance)}</td>
                    <td className="text-muted-foreground">{a.status}</td>
                    <td className="text-muted-foreground">{a.preset ?? "—"}</td>
                    <td className="font-mono text-[11px]">
                      {a.bound
                        ? <span className="text-cyan-300">{(a.bound_lanes ?? []).join(", ")}</span>
                        : <span className="text-muted-foreground/70">未绑定（历史账户，不计入车道占用）</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DataState>
      </Card>

      {/* 组合级熔断演练（POST /api/trading/risk/drill） */}
      <Card className="glass p-4">
        <BlockTitle icon={<FlaskConical className="h-3.5 w-3.5" />} title="组合级熔断演练" right={drillActive ? "演练激活中" : "演练（真写后端状态）"} />
        <div className="flex flex-wrap items-center gap-3">
          {drillActive ? (
            <button
              type="button"
              onClick={onDrillDisable}
              disabled={drillBusy}
              className="inline-flex items-center gap-1.5 rounded-md border border-profit/30 bg-profit/10 px-3 py-1.5 text-xs text-profit transition-colors hover:bg-profit/20 disabled:opacity-50"
            >
              <Undo2 className="h-3.5 w-3.5" /> 解除演练
            </button>
          ) : (
            <button
              type="button"
              onClick={onDrillEnable}
              disabled={drillBusy}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs transition-colors disabled:opacity-50",
                hasPaperLane
                  ? "border-warning/30 bg-warning/10 text-warning hover:bg-warning/20"
                  : "border-border/40 bg-muted/20 text-muted-foreground"
              )}
            >
              <Beaker className="h-3.5 w-3.5" /> 模拟触发组合级熔断
            </button>
          )}
          <span className="text-[11px] leading-relaxed text-muted-foreground">
            {drillActive
              ? "演练已激活：paper 车道影子期会真的跳过报价（不再挂单）。点击「解除演练」恢复。"
              : hasPaperLane
                ? "这是真演练：会把 paper 车道 health.drill 置为 true，影子期调度器真的跳过该车道；实盘进 skipped。开启后请及时解除。"
                : "当前无 paper 模式车道，演练不会命中任何车道（实盘一律 skip）。"}
          </span>
        </div>
        {laneModeOf.size > 0 && (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
            {Array.from(laneModeOf.entries()).map(([lid, m]) => (
              <span key={lid}>
                {lid}：<span className={cn(LANE_LABEL[m]?.tone ?? "text-muted-foreground")}>{LANE_LABEL[m]?.label ?? m}</span>
              </span>
            ))}
          </div>
        )}
        <div className="mt-2 flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 flex-shrink-0" />
          <span>演练会改变车道 health.drill 状态（真实写入）；若需完整复现「熔断触发态」，仍建议结合熔断矩阵的复位功能排查。</span>
        </div>
      </Card>
    </PageShell>
  );
}

function BlockTitle({ icon, title, right }: { icon: React.ReactNode; title: string; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
          {icon}
        </span>
        {title}
      </h2>
      {right && <span className="text-[11px] text-muted-foreground">{right}</span>}
    </div>
  );
}

function BudgetPanel({ data }: { data: UnifiedAccountResponse }) {
  const ex = data.exposure;
  // 各交易所策略预算占用（strategy_budgets 之和 vs allocated_usd）
  const budgetRows = data.exchanges
    .filter((e) => e.allocated_usd > 0 || Object.keys(e.strategy_budgets ?? {}).length > 0)
    .map((e) => {
      const budgets = Object.entries(e.strategy_budgets ?? {});
      const limits = e.strategy_limits ?? {};
      const sum = budgets.reduce((s, [, v]) => s + (v || 0), 0);
      return { exchange: e.exchange, allocated: e.allocated_usd, budgets, limits, sum, over: e.allocated_usd > 0 && sum > e.allocated_usd };
    });

  const strategies = data.strategies ?? [];

  return (
    <div className="space-y-3">
      {/* 账户级做市敞口 */}
      <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <RiskStat label="做市名义" value={fmtUsd(ex.mm_notional_usd ?? 0)} />
        <RiskStat label="做市占比（账户）" value={fmtPct(ex.mm_notional_pct ?? 0, 2)} />
        <RiskStat label="净敞口(账户)" value={fmtUsd(data.account?.available_balance ?? 0)} />
      </div>

      {/* 各策略预算占用（strategy_budgets 之和 vs allocated_usd） */}
      <div>
        <div className="mb-1.5 text-xs text-muted-foreground">策略预算占用 · 超配显式提示</div>
        {budgetRows.length === 0 ? (
          <div className="rounded-lg border border-muted/40 bg-muted/20 px-3 py-4 text-center text-xs text-muted-foreground">
            暂无策略预算数据（strategy_budgets 未配置）
          </div>
        ) : (
          <div className="space-y-2">
            {budgetRows.map((r) => (
              <div key={r.exchange} className={cn("rounded-xl border p-3", r.over ? "border-loss/30 bg-loss/5" : "border-border/40")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{r.exchange}</span>
                  <span className="font-mono tabular-nums text-[11px] text-muted-foreground">
                    分配 {fmtUsd(r.allocated)} · 预算合计 {fmtUsd(r.sum)} · 使用率 {r.allocated > 0 ? fmtPct((r.sum / r.allocated) * 100, 1) : "—"}
                  </span>
                </div>
                {r.over && (
                  <div className="mt-1 flex items-center gap-1.5 text-[11px] text-loss">
                    <Info className="h-3 w-3 flex-shrink-0" /> 超配：策略预算合计（{fmtUsd(r.sum)}）超过该交易所分配（{fmtUsd(r.allocated)}），请调整 strategy_budgets。
                  </div>
                )}
                {r.budgets.length === 0 ? (
                  <div className="mt-1 text-[11px] text-muted-foreground">未配置各策略预算</div>
                ) : (
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                    {r.budgets.map(([sid, usd]) => {
                      const limitPct = r.limits[sid] != null ? r.limits[sid] * 100 : null;
                      return (
                        <span key={sid} className="inline-flex items-center gap-1 rounded-md border border-border/40 px-1.5 py-0.5">
                          <span className="font-medium">{sid}</span>
                          <span className="font-mono tabular-nums">{fmtUsd(usd || 0)}</span>
                          {limitPct != null && <span className="text-muted-foreground">· {fmtPct(limitPct, 2)}</span>}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 各策略资金占用 */}
      {strategies.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs text-muted-foreground">策略资金占用（capital_usd）</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
            {strategies.map((s) => (
              <span key={s.strategy_type}>{s.strategy_type}: {fmtUsd(s.capital_usd)}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RiskStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/20 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="font-mono tabular-nums font-semibold">{value}</div>
    </div>
  );
}
