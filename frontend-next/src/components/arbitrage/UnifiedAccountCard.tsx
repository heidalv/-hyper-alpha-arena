"use client";

/**
 * UnifiedAccountCard — 统一模拟账户视图卡（账户头 + 按策略分账 + 交易所预算）
 *
 * 设计 §3.1 阶段4：把做市策略并入统一账户后，用户要知道「影子交易 = 模拟账户测试」，
 * 各周期（S3/S8/MM）如何共享同一账户、额度如何分。数据源 `GET /api/trading/account/unified`。
 *
 * 展示三部分：
 *  1. 账户头：名称 / 账户 id / 权益 / 可用 / 冻结 / 已实现盈亏 / 状态 / 预设
 *  2. 按策略分账表：strategy_type | 净额 | 盈亏 | 手续费 | 资金 | 成交数
 *  3. 交易所预算：allocated/available/frozen + strategy_limits(%) / strategy_budgets(usd)
 *     （空 budgets 如实显示「未配置」，不补假值）
 */
import { useMemo } from "react";
import { Wallet, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtNum, fmtPct } from "@/lib/format";
import { fmtAgeMs, ageFromAsOf, type UnifiedAccountResponse } from "@/lib/trading-api";

const STATUS_LABEL: Record<string, { label: string; tone: string }> = {
  active: { label: "活跃", tone: "bg-profit/15 text-profit" },
  running: { label: "运行中", tone: "bg-profit/15 text-profit" },
  paused: { label: "已暂停", tone: "bg-warning/15 text-warning" },
  frozen: { label: "已冻结", tone: "bg-warning/15 text-warning" },
  deleted: { label: "已删除", tone: "bg-muted/40 text-muted-foreground" },
};

function signTone(v: number) {
  return v >= 0 ? "text-profit" : "text-loss";
}

export function UnifiedAccountCard({
  data,
  className,
}: {
  data?: UnifiedAccountResponse | null;
  className?: string;
}) {
  const acct = data?.account;
  const strategies = data?.strategies ?? [];
  const activeExchanges = useMemo(
    () => (data?.exchanges ?? []).filter((e) => e.allocated_usd > 0 || e.available_usd > 0 || e.frozen_usd > 0),
    [data]
  );

  if (!acct) return null;

  const statusMeta = STATUS_LABEL[acct.status] ?? { label: acct.status, tone: "bg-muted/40 text-muted-foreground" };

  return (
    <div className={cn("space-y-4", className)}>
      {/* 账户头 */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <Wallet className="h-3.5 w-3.5" />
            </span>
            <h2 className="text-sm font-semibold">{acct.name}</h2>
            <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium", statusMeta.tone)}>
              {statusMeta.label}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">account_id={acct.account_id}</span>
            {acct.preset && <span className="text-[11px] text-muted-foreground">预设：{acct.preset}</span>}
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">统一模拟账户 · 做市已并入该账户</div>
        </div>
        <div className="grid grid-cols-2 gap-x-5 gap-y-1 text-xs sm:grid-cols-3">
          <Metric label="权益" value={fmtUsd(acct.total_equity)} grad />
          <Metric label="可用" value={fmtUsd(acct.available_balance)} />
          <Metric label="冻结" value={fmtUsd(acct.frozen_balance)} />
          <Metric label="已实现盈亏" value={`${acct.realized_pnl >= 0 ? "+" : ""}${fmtUsd(acct.realized_pnl)}`} tone={signTone(acct.realized_pnl)} />
        </div>
      </div>

      {/* 按策略分账表 */}
      <div>
        <div className="mb-1.5 text-xs text-muted-foreground">按策略分账（各周期相互配合）</div>
        <div className="overflow-x-auto rounded-xl border border-border/40">
          <table className="data-table">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left">策略</th>
                <th className="text-right">净额</th>
                <th className="text-right">盈亏</th>
                <th className="text-right">手续费</th>
                <th className="text-right">资金</th>
                <th className="text-right">成交数</th>
              </tr>
            </thead>
            <tbody>
              {strategies.length === 0 ? (
                <tr className="border-b border-border/20">
                  <td colSpan={6} className="text-center text-muted-foreground">暂无策略分账数据</td>
                </tr>
              ) : (
                strategies.map((s) => (
                  <tr key={s.strategy_type} className="border-b border-border/20">
                    <td className="font-medium">{s.strategy_type}</td>
                    <td className={cn("text-right font-mono tabular-nums font-semibold", signTone(s.net_usd))}>{fmtUsd(s.net_usd)}</td>
                    <td className={cn("text-right font-mono tabular-nums", signTone(s.pnl_usd))}>{fmtUsd(s.pnl_usd)}</td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtUsd(s.fee_usd)}</td>
                    <td className="text-right font-mono tabular-nums">{fmtUsd(s.capital_usd)}</td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtNum(s.fills ?? s.entries, 0)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 交易所预算 */}
      <div>
        <div className="mb-1.5 text-xs text-muted-foreground">交易所预算（{activeExchanges.length} 个有余额）</div>
        {activeExchanges.length === 0 ? (
          <div className="rounded-lg border border-muted/40 bg-muted/20 px-3 py-4 text-center text-xs text-muted-foreground">
            暂无交易所预算分配
          </div>
        ) : (
          <div className="space-y-2">
            {activeExchanges.map((e) => {
              const budgetKeys = Object.keys(e.strategy_budgets ?? {});
              const limitKeys = Object.keys(e.strategy_limits ?? {});
              return (
                <div key={e.exchange} className="rounded-xl border border-border/40 p-3">
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="font-medium">{e.exchange}</span>
                    <span className="font-mono tabular-nums text-[11px] text-muted-foreground">
                      分配 {fmtUsd(e.allocated_usd)} · 可用 {fmtUsd(e.available_usd)} · 冻结 {fmtUsd(e.frozen_usd)}
                    </span>
                  </div>
                  {budgetKeys.length === 0 && limitKeys.length === 0 ? (
                    <div className="text-[11px] text-muted-foreground">未配置 strategy_budgets / strategy_limits</div>
                  ) : (
                    <div className="space-y-1.5">
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                        {budgetKeys.length > 0 ? (
                          budgetKeys.map((k) => {
                            const usd = e.strategy_budgets[k] ?? 0;
                            const pct = limitKeys.includes(k) ? e.strategy_limits[k] : undefined;
                            return (
                              <span key={k} className="inline-flex items-center gap-1 rounded-md border border-border/40 px-1.5 py-0.5">
                                <span className="font-medium">{k}</span>
                                <span className="font-mono tabular-nums">{fmtUsd(usd)}</span>
                                {pct != null && <span className="text-muted-foreground">· {fmtPct(pct * 100, 2)}</span>}
                              </span>
                            );
                          })
                        ) : (
                          limitKeys.map((k) => (
                            <span key={k} className="inline-flex items-center gap-1 rounded-md border border-border/40 px-1.5 py-0.5">
                              <span className="font-medium">{k}</span>
                              <span className="font-mono tabular-nums text-muted-foreground">{fmtPct((e.strategy_limits[k] ?? 0) * 100, 2)}</span>
                            </span>
                          ))
                        )}
                      </div>
                      {budgetKeys.length > 1 && e.allocated_usd > 0 && (
                        <BudgetUsage budgets={Object.values(e.strategy_budgets)} allocated={e.allocated_usd} />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 数据年龄 */}
      {data?.as_of && (
        <div className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          <span>as of {new Date(data.as_of).toLocaleTimeString("zh-CN", { hour12: false })}</span>
          <span className="text-muted-foreground/70">（{fmtAgeMs(ageFromAsOf(data.as_of))}）</span>
        </div>
      )}
    </div>
  );
}

function BudgetUsage({ budgets, allocated }: { budgets: number[]; allocated: number }) {
  const sum = budgets.reduce((s, v) => s + (v || 0), 0);
  const usage = allocated > 0 ? (sum / allocated) * 100 : 0;
  const over = allocated > 0 && sum > allocated;
  return (
    <div className={cn("flex items-center gap-1.5 text-[11px] font-mono tabular-nums", over ? "text-loss" : "text-muted-foreground")}>
      <span>预算合计 {fmtUsd(sum)} / 分配 {fmtUsd(allocated)} = {fmtPct(usage, 1)}</span>
      {over && <span className="text-loss">（超配）</span>}
    </div>
  );
}

function Metric({ label, value, tone, grad }: { label: string; value: string; tone?: string; grad?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("font-mono tabular-nums font-semibold", tone ?? "text-foreground", grad && "grad-text")}>{value}</div>
    </div>
  );
}
