"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  TrendingUp, TrendingDown, Wallet, Banknote, Shield, Layers, RefreshCw, Loader2, AlertTriangle,
  Receipt, CheckCircle2, XCircle,
} from "lucide-react";
import { liveApi } from "@/lib/api";
import type { LiveOrder, LivePosition, AsterLedger } from "@/types/api";
import { useAccounts } from "@/hooks/useTradingData";
import { cn } from "@/lib/utils";
import { sumUnrealizedPnl } from "@/lib/stats";
import { PageHeader } from "@/components/layout/PageHeader";
import { confirmDialog } from "@/lib/confirm";

const fmt = (v: number | undefined | null, d = 2) =>
  v === undefined || v === null || isNaN(Number(v)) ? "—" : Number(v).toFixed(d);

const fmtPrice = (v: number | undefined | null) => {
  if (v === undefined || v === null || !Number(v)) return "—";
  const n = Number(v);
  return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(4);
};

// ═══════════════════════════════════════════════════════════════════
// Asterdex 真实收入账本
// 只展示交易所真实返回的数据：手续费/资金费/已实现盈亏（income 流水）、maker 占比
// （userTrades）、多资产模式 + USDF 抵押（account.assets）、Trade & Earn 本周门槛进度。
// Stage 6 Rh 积分赛季已于 2026-05 结束，不再展示任何积分/空投估值。
// ═══════════════════════════════════════════════════════════════════
const fmtUsd = (v: number | undefined | null, d = 2) =>
  v === undefined || v === null || isNaN(Number(v))
    ? "—"
    : `${Number(v) < 0 ? "-" : ""}$${Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const fmtPct = (v: number | undefined | null, d = 0) =>
  v === undefined || v === null || isNaN(Number(v)) ? "—" : `${(Number(v) * 100).toFixed(d)}%`;

function LedgerStat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "profit" | "loss" | "muted" }) {
  return (
    <div className="p-3 rounded bg-muted/30 min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-lg font-bold tabular-nums truncate", tone === "profit" && "text-profit", tone === "loss" && "text-loss", tone === "muted" && "text-muted-foreground")}>{value}</div>
      {hint ? <div className="text-xs text-muted-foreground truncate">{hint}</div> : null}
    </div>
  );
}

function AsterLedgerCard({
  icoCls, isLoading, isError, keysConfigured, message, ledger, policy, onRefresh,
}: {
  icoCls: string;
  isLoading: boolean;
  isError: boolean;
  keysConfigured?: boolean;
  message?: string;
  ledger: AsterLedger | null;
  policy?: { maker_first?: boolean; maker_first_tiers?: string[]; maker_timeout_s?: number; enabled?: boolean; reason?: string };
  onRefresh: () => void;
}) {
  const acct = ledger?.account;
  const fees = ledger?.fees;
  const ex = ledger?.execution;
  const te = ledger?.trade_and_earn;
  const rw = ledger?.rewards;
  const signTone = (v?: number | null): "profit" | "loss" | undefined =>
    v === undefined || v === null || v === 0 ? undefined : v > 0 ? "profit" : "loss";
  const rewardTypes = Object.entries(rw?.by_type ?? {});

  return (
    <Card className="glass">
      <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={icoCls}><Receipt className="w-3.5 h-3.5" /></span>
          <span className="text-sm font-medium">Asterdex 真实收入账本</span>
          <Badge variant="secondary" className="text-xs">近 {ledger?.window_days ?? 7} 天 · 交易所流水</Badge>
          {policy ? (
            <Badge variant="outline" className={cn("text-xs", policy.maker_first ? "text-profit border-profit/40" : "text-muted-foreground")}>
              Maker 优先 {policy.maker_first ? `开 · ${(policy.maker_first_tiers ?? []).join("/") || "mid/long"} 开仓` : "关"}
            </Badge>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onRefresh} title="刷新">
          <RefreshCw className={cn("w-3.5 h-3.5", isLoading && "animate-spin")} />
        </Button>
      </div>

      <div className="p-4 space-y-4">
        {keysConfigured === false ? (
          <div className="text-xs px-3 py-2 rounded bg-warning/10 text-warning border border-warning/20">
            {message ?? "未配置 Asterdex API Key，无法读取账户流水"}
          </div>
        ) : isLoading && !ledger ? (
          <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
        ) : isError && !ledger ? (
          <div className="text-xs px-3 py-2 rounded bg-loss/10 text-loss border border-loss/20">
            收入账本获取失败（交易所接口不可达或凭证无效），稍后自动重试
          </div>
        ) : ledger ? (
          <>
            {/* 第一行：真实资金流水 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <LedgerStat
                label="手续费支出"
                value={fmtUsd(fees?.commission_paid_usd, 4)}
                hint={`maker ${fmtPct(fees?.maker_rate, 3)} / taker ${fmtPct(fees?.taker_rate, 3)}${fees?.rate_source === "api" ? "（账户真实费率）" : "（费率表）"}`}
                tone={(fees?.commission_paid_usd ?? 0) > 0 ? "loss" : undefined}
              />
              <LedgerStat label="资金费净额" value={fmtUsd(fees?.funding_fee_usd, 4)} hint="正 = 收到资金费" tone={signTone(fees?.funding_fee_usd)} />
              <LedgerStat label="已实现盈亏" value={fmtUsd(fees?.realized_pnl_usd)} hint="交易所 REALIZED_PNL 流水" tone={signTone(fees?.realized_pnl_usd)} />
              <LedgerStat
                label="奖励入账（USDF）"
                value={fmtUsd(rw?.usdf_received, 4)}
                hint={`${rw?.count ?? 0} 条非交易/非转账入账`}
                tone={(rw?.usdf_received ?? 0) > 0 ? "profit" : "muted"}
              />
            </div>

            {/* 第二行：执行质量 + 抵押结构 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <LedgerStat
                label="Maker 成交占比"
                value={ex?.maker_ratio === null || ex?.maker_ratio === undefined ? "—" : fmtPct(ex.maker_ratio)}
                hint={`${ex?.trades ?? 0} 笔 · 名义 ${fmtUsd(ex?.notional_usd, 0)}`}
                tone={(ex?.maker_ratio ?? 0) >= 0.5 ? "profit" : undefined}
              />
              <LedgerStat label="Maker 省下手续费（估）" value={fmtUsd(ex?.fee_saved_est_usd, 4)} hint="maker 名义 × (taker − maker 费率)" tone={(ex?.fee_saved_est_usd ?? 0) > 0 ? "profit" : undefined} />
              <LedgerStat
                label="多资产保证金模式"
                value={acct?.multi_assets_mode === true ? "已开启" : acct?.multi_assets_mode === false ? "未开启" : "未知"}
                hint={`USDF 抵押占比 ${fmtPct(acct?.usdf_share)} · 总保证金 ${fmtUsd(acct?.total_margin_usd, 0)}`}
                tone={acct?.multi_assets_mode === true ? "profit" : acct?.multi_assets_mode === false ? "loss" : "muted"}
              />
              <LedgerStat label="USDF 抵押余额" value={fmtUsd(acct?.usdf_balance)} hint={`奖励计入上限 ${fmtUsd(te?.usdf_cap_usd, 0)}`} />
            </div>

            {/* Trade & Earn 本周进度 */}
            <div className="rounded-lg border border-border/40 p-3 space-y-2">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">Trade &amp; Earn 本周进度</span>
                  <span className="text-xs text-muted-foreground">
                    {te?.week_start ? `${te.week_start.slice(5, 10)} → ${te?.week_end?.slice(5, 10)} UTC` : "周四 00:00 UTC 结算周"}
                  </span>
                </div>
                {te?.eligible_now ? (
                  <Badge variant="outline" className="text-xs text-profit border-profit/40"><CheckCircle2 className="w-3 h-3 mr-1" />交易奖励门槛已达</Badge>
                ) : (
                  <Badge variant="outline" className="text-xs text-warning border-warning/40"><XCircle className="w-3 h-3 mr-1" />仅存款奖励 / 门槛未达</Badge>
                )}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>本周交易量</span>
                    <span className="tabular-nums">{fmtUsd(te?.volume_usd, 0)} / {fmtUsd(te?.volume_threshold_usd, 0)}</span>
                  </div>
                  <div className="h-1.5 rounded bg-muted/40 overflow-hidden">
                    <div className={cn("h-full rounded", (te?.volume_progress ?? 0) >= 1 ? "bg-profit" : "bg-cyan-400/70")} style={{ width: `${Math.min(100, (te?.volume_progress ?? 0) * 100)}%` }} />
                  </div>
                </div>
                <div>
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>活跃交易日</span>
                    <span className="tabular-nums">{te?.active_days ?? 0} / {te?.active_days_threshold ?? 2} 天</span>
                  </div>
                  <div className="h-1.5 rounded bg-muted/40 overflow-hidden">
                    <div className={cn("h-full rounded", (te?.active_days ?? 0) >= (te?.active_days_threshold ?? 2) ? "bg-profit" : "bg-cyan-400/70")} style={{ width: `${Math.min(100, ((te?.active_days ?? 0) / Math.max(1, te?.active_days_threshold ?? 2)) * 100)}%` }} />
                  </div>
                </div>
                <div className="text-xs">
                  <div className="text-muted-foreground">参考周奖励（非官方承诺）</div>
                  <div className="text-base font-bold tabular-nums">{fmtUsd(te?.reference_weekly_reward_usd)}</div>
                  <div className="text-muted-foreground">
                    USDF 计入 {fmtUsd(te?.usdf_counted_usd, 0)} × 年化 {fmtPct((te?.reference_apy?.deposit ?? 0) + (te?.eligible_now ? (te?.reference_apy?.trading ?? 0) : 0), 1)} ÷ 52
                  </div>
                </div>
              </div>
              {te?.blockers?.length ? (
                <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-0.5">
                  {te.blockers.map((b, i) => <li key={i}>{b}</li>)}
                </ul>
              ) : null}
            </div>

            {/* 抵押资产明细 + 奖励类型明细 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="border border-border/40 rounded overflow-hidden">
                <div className="text-xs text-muted-foreground px-2 py-1.5 border-b border-border/40">保证金资产（/fapi/v2/account）</div>
                {acct?.assets?.length ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-muted-foreground border-b border-border/40">
                        <th className="text-left py-1.5 px-2">资产</th>
                        <th className="text-right py-1.5 px-2">余额</th>
                        <th className="text-right py-1.5 px-2">抵押率</th>
                        <th className="text-right py-1.5 px-2">折算 USD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {acct.assets.map((a, i) => (
                        <tr key={i} className="border-b border-border/20 last:border-0">
                          <td className="py-1.5 px-2 font-medium">{a.asset}{a.margin_available === false ? <span className="text-muted-foreground"> (不可作保证金)</span> : null}</td>
                          <td className="py-1.5 px-2 text-right tabular-nums">{fmt(a.wallet_balance, 4)}</td>
                          <td className="py-1.5 px-2 text-right tabular-nums">{a.collateral_ratio === null || a.collateral_ratio === undefined ? "—" : fmtPct(a.collateral_ratio, 2)}</td>
                          <td className="py-1.5 px-2 text-right tabular-nums">{fmtUsd(a.usd_value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="text-xs text-muted-foreground px-2 py-3 text-center">无保证金资产数据</div>
                )}
              </div>
              <div className="border border-border/40 rounded overflow-hidden">
                <div className="text-xs text-muted-foreground px-2 py-1.5 border-b border-border/40">奖励入账明细（/fapi/v1/income）</div>
                {rewardTypes.length ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-muted-foreground border-b border-border/40">
                        <th className="text-left py-1.5 px-2">类型</th>
                        <th className="text-left py-1.5 px-2">资产</th>
                        <th className="text-right py-1.5 px-2">金额</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rewardTypes.flatMap(([type, byAsset]) =>
                        Object.entries(byAsset).map(([asset, amt]) => (
                          <tr key={`${type}-${asset}`} className="border-b border-border/20 last:border-0">
                            <td className="py-1.5 px-2">{type}</td>
                            <td className="py-1.5 px-2">{asset}</td>
                            <td className={cn("py-1.5 px-2 text-right tabular-nums", amt > 0 && "text-profit")}>{fmt(amt, 4)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                ) : (
                  <div className="text-xs text-muted-foreground px-2 py-3 text-center">统计窗口内无奖励类入账</div>
                )}
              </div>
            </div>

            <div className="text-xs text-muted-foreground leading-relaxed space-y-0.5">
              <div>
                Stage 6 积分赛季已于 2026-05 结束（{ledger.programs?.stage6?.status ?? "ended"}），本面板不再展示 Rh 积分/空投估值；
                当前唯一有效激励为 Trade &amp; Earn（USDF 抵押 + 周交易门槛），年化为参考值、官方按周动态浮动。
              </div>
              {ex?.symbols?.length ? (
                <div>成交币种：{ex.symbols.join("、")}{ex.symbols_truncated ? "…（仅统计成交最多的 12 个）" : ""}</div>
              ) : null}
              {ledger.errors?.length ? (
                <div className="text-warning">部分数据不可用：{ledger.errors.slice(0, 3).join("；")}{ledger.errors.length > 3 ? ` 等 ${ledger.errors.length} 项` : ""}</div>
              ) : null}
            </div>
          </>
        ) : (
          <div className="text-center py-6 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
            <RefreshCw className="w-5 h-5 mx-auto mb-2 opacity-40" />
            暂无账本数据
          </div>
        )}
      </div>
    </Card>
  );
}

export default function LiveTradingPage() {
  const { data: accounts } = useAccounts();
  const qc = useQueryClient();
  const liveAccounts = useMemo(
    () => (accounts ?? []).filter((a) => a.trading_mode === "live"),
    [accounts]
  );
  const [accountId, setAccountId] = useState<number | null>(null);
  const activeAccount = liveAccounts.find((a) => a.id === accountId) ?? liveAccounts[0];
  const aid = activeAccount?.id ?? null;

  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [symbol, setSymbol] = useState("BTC");
  const [quantity, setQuantity] = useState("0.001");
  const [leverage, setLeverage] = useState("10");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [price, setPrice] = useState("");
  const [tp, setTp] = useState("");
  const [sl, setSl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [msgErr, setMsgErr] = useState(false);

  const balanceQ = useQuery({
    queryKey: ["live-balance", aid],
    queryFn: () => liveApi.getBalance(aid!),
    enabled: !!aid,
    refetchInterval: 3_000,
    staleTime: 2_000,
  });
  const positionsQ = useQuery({
    queryKey: ["live-positions", aid],
    queryFn: () => liveApi.getPositions(aid!),
    enabled: !!aid,
    refetchInterval: 3_000,
    staleTime: 2_000,
  });
  const ordersQ = useQuery({
    queryKey: ["live-orders", aid],
    queryFn: () => liveApi.getOrders(aid!),
    enabled: !!aid,
    refetchInterval: 5_000,
    staleTime: 3_000,
  });
  const pointsQ = useQuery({
    queryKey: ["live-asterdex-points", aid],
    queryFn: () => liveApi.getAsterdexPoints(aid!),
    enabled: !!aid && activeAccount?.exchange === "asterdex",
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const bal = balanceQ.data;
  const positions = positionsQ.data?.positions ?? [];
  const totalUnrealizedPnl = sumUnrealizedPnl(positions);
  const orders = ordersQ.data?.orders ?? [];
  const keysOk = activeAccount?.keys_configured ?? bal?.keys_configured ?? false;
  const accountActive = activeAccount?.is_active === true;
  const canTrade = !!aid && keysOk && accountActive;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["live-balance", aid] });
    qc.invalidateQueries({ queryKey: ["live-positions", aid] });
    qc.invalidateQueries({ queryKey: ["live-orders", aid] });
  };

  const orderMut = useMutation({
    mutationFn: liveApi.placeOrder,
    onSuccess: (r) => {
      invalidate();
      setMsgErr(!r?.success);
      setMsg(r?.success ? `下单已提交: ${r.symbol} ${r.side}` : `下单失败: ${r?.result?.message ?? ""}`);
    },
    onError: (e: Error) => { setMsgErr(true); setMsg(`下单失败: ${e?.message ?? e}`); },
  });
  const closeMut = useMutation({
    mutationFn: liveApi.closePosition,
    onSuccess: (r) => {
      invalidate();
      setMsgErr(!r?.success);
      setMsg(r?.success ? `平仓已提交: ${r.symbol}` : `平仓失败: ${r?.result?.message ?? ""}`);
    },
    onError: (e: Error) => { setMsgErr(true); setMsg(`平仓失败: ${e?.message ?? e}`); },
  });

  const submitOrder = async () => {
    setMsg(null);
    const sideText = side === "buy" ? "做多" : "做空";
    const priceText = orderType === "market" ? "市价" : `限价 ${price}`;
    // [2026-09-09] 实盘下单是最危险的操作：改为 Aurora 确认框 + 要求输入确认词，
    // 避免回车/误触直接成交。
    const ok = await confirmDialog({
      title: "确认实盘下单？",
      description: `${sideText} ${symbol} ${quantity} @ ${priceText} ${leverage}x\n成交后立即进入真实市场，不可撤销。`,
      tone: "danger",
      confirmText: "下单",
      requireText: "下单",
    });
    if (!ok) return;
    orderMut.mutate({
      account_id: aid,
      symbol,
      side,
      quantity: parseFloat(quantity),
      leverage: parseFloat(leverage),
      order_type: orderType,
      price: orderType === "limit" ? parseFloat(price) : undefined,
      tp_price: tp ? parseFloat(tp) : undefined,
      sl_price: sl ? parseFloat(sl) : undefined,
    });
  };

  const switchMarginType = async (p: LivePosition) => {
    if (!aid) return;
    const next = p.margin_type === "isolated" ? "cross" : "isolated";
    if (!(await confirmDialog({
      title: `将 ${p.symbol} 持仓切换为${next === "cross" ? "全仓" : "逐仓"}？`,
      description: "切换保证金模式会改变强平价格，请确认仓位与风险承受度。",
      tone: "warning",
      confirmText: "切换",
    }))) return;
    try {
      await liveApi.setMarginType(aid, p.symbol, next);
      positionsQ.refetch();
      setMsg(`已切换 ${p.symbol} 为 ${next === "cross" ? "全仓" : "逐仓"}`);
    } catch (e: any) {
      setMsgErr(true);
      setMsg(e?.message || String(e));
    }
  };

  const closePosition = async (pos: LivePosition) => {
    if (!(await confirmDialog({
      title: `确认平仓 ${pos.symbol} ${pos.side === "long" ? "多" : "空"} ${pos.size}？`,
      description: "将以市价平掉该持仓，操作不可撤销。",
      tone: "danger",
      confirmText: "平仓",
    }))) return;
    closeMut.mutate({ account_id: aid, symbol: pos.symbol, side: pos.side });
  };


  const icoCls = "w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300";
  const isAsterdex = activeAccount?.exchange === "asterdex";

  const positionsCard = (
    <Card className="glass overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
        <div className="flex items-center gap-2">
          <span className={icoCls}><Layers className="w-3.5 h-3.5" /></span>
          <span className="text-sm font-medium">当前持仓</span>
          <Badge variant="secondary" className="text-xs">{positions.length} 笔</Badge>
        </div>
        <span className="text-xs text-muted-foreground">{activeAccount?.exchange ?? "asterdex"}</span>
      </div>
      <div className="p-4">
        {positionsQ.isLoading ? (
          <div className="flex justify-center py-10"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
        ) : positions.length === 0 ? (
          <div className="text-center py-10 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
            <Layers className="w-5 h-5 mx-auto mb-2 opacity-40" />
            暂无持仓
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left">币种</th>
                  <th className="text-left">方向</th>
                  <th className="text-left">周期</th>
                  <th className="text-right">开仓价 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">当前价 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">止盈 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">止损 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">数量 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">杠杆 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">强平价 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">保证金 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">保证金比率 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">模式 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">浮盈 <span className="text-cyan-300">▲</span></th>
                  <th className="text-right">盈亏% <span className="text-cyan-300">▲</span></th>
                  <th className="text-left">操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p: LivePosition, i: number) => {
                  const pnl = Number(p.unrealized_pnl || 0);
                  const pct = Number(p.margin) > 0 ? (pnl / Number(p.margin)) * 100 : 0;
                  return (
                    <tr key={i} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                      <td className="py-2 pr-2 font-medium">{p.symbol}</td>
                      <td className="py-2 pr-2">
                        <Badge className={cn("text-xs", p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                          {p.side === "long" ? "多" : "空"}
                        </Badge>
                      </td>
                      <td className="py-2 pr-2">
                        {p.tier && p.tier.length ? (
                          <span className="inline-flex flex-wrap gap-1">
                            {p.tier.map((t) => (
                              <Badge key={t} className={cn("text-[10px]", t === "short" ? "bg-cyan-400/15 text-cyan-300" : t === "mid" ? "bg-violet-400/15 text-violet-300" : "bg-amber-400/15 text-amber-300")}>
                                {t === "short" ? "短线" : t === "mid" ? "中线" : "长线"}
                              </Badge>
                            ))}
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-2 text-right num">{fmtPrice(p.entry_price)}</td>
                      <td className="py-2 pr-2 text-right num">{fmtPrice(p.last_price ?? p.mark_price)}</td>
                      <td className={cn("py-2 pr-2 text-right num", p.tp_price ? "text-profit" : "text-muted-foreground")}>
                        {p.tp_price ? fmtPrice(p.tp_price) : "—"}
                      </td>
                      <td className={cn("py-2 pr-2 text-right num", p.sl_price ? "text-loss" : "text-muted-foreground")}>
                        {p.sl_price ? fmtPrice(p.sl_price) : "—"}
                      </td>
                      <td className="py-2 pr-2 text-right num">{Number(p.size).toFixed(4)}</td>
                      <td className="py-2 pr-2 text-right num">{Number(p.leverage || 1)}x</td>
                      <td className="py-2 pr-2 text-right num">{p.liquidation_price ? fmtPrice(p.liquidation_price) : "—"}</td>
                      <td className="py-2 pr-2 text-right num">${fmt(p.margin)}</td>
                      <td className={cn("py-2 pr-2 text-right num", Number(p.margin_ratio) >= 10 ? "text-loss" : "")}>
                        {Number(p.margin_ratio) > 0 ? `${Number(p.margin_ratio).toFixed(2)}%` : "—"}
                      </td>
                      <td className="py-2 pr-2 text-right text-xs">
                        {p.margin_type === "isolated" || p.margin_type === "crossed" ? (
                          <button
                            className="text-cyan-300 hover:underline cursor-pointer"
                            title="点击切换 全仓/逐仓（币安约 5 秒限速）"
                            onClick={() => switchMarginType(p)}
                          >
                            {p.margin_type === "isolated" ? "逐仓" : "全仓"}
                          </button>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className={cn("py-2 pr-2 text-right num font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
                        {pnl >= 0 ? "+" : ""}${fmt(pnl, 4)}
                      </td>
                      <td className={cn("py-2 pr-2 text-right num", pct >= 0 ? "text-profit" : "text-loss")}>
                        {pct >= 0 ? "+" : ""}{pct.toFixed(1)}%
                      </td>
                      <td className="py-2">
                        <Button size="sm" variant="outline" className="h-7 text-xs"
                          disabled={!canTrade || closeMut.isPending}
                          onClick={() => closePosition(p)}>
                          平仓
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-border/50 bg-muted/20">
                  <td colSpan={9} className="px-3 py-2 text-xs text-muted-foreground">
                    合计 <span className="num font-semibold text-foreground">{positions.length}</span> 笔持仓
                  </td>
                  <td className={cn("px-3 py-2 text-right text-xs num font-bold",
                    totalUnrealizedPnl >= 0 ? "text-profit" : "text-loss")}>
                    {totalUnrealizedPnl >= 0 ? "+" : ""}$
                    {fmt(totalUnrealizedPnl, 4)}
                  </td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </Card>
  );

  return (
    <div className="p-4 space-y-4">
      {/* 标题 + 账户（Aurora 统一页头） */}
      <PageHeader
        icon={<TrendingUp className="w-4 h-4" />}
        title="实盘交易"
        subtitle="真实资金 · 下单前请核对交易所与风控配置"
        refreshHint="实时推送 · 2s 刷新"
        breadcrumb={[{ label: "交易核心" }, { label: "实盘交易" }]}
        actions={
          <>
            {liveAccounts.length > 0 && (
              <select
                value={activeAccount?.id ?? ""}
                onChange={(e) => setAccountId(Number(e.target.value))}
                className="bg-card border border-border text-xs rounded-lg px-3 py-1.5"
              >
                {liveAccounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name} · {a.exchange}</option>
                ))}
              </select>
            )}
            <Button size="sm" variant="outline" onClick={() => { invalidate(); balanceQ.refetch(); positionsQ.refetch(); ordersQ.refetch(); }}>
              <RefreshCw className="w-3.5 h-3.5 mr-1" /> 刷新
            </Button>
          </>
        }
      />

      {/* 安全提示 */}
      {!keysOk && (
        <div className="flex items-center gap-2 text-xs px-3 py-2 rounded bg-warning/10 text-warning border border-warning/20">
          <AlertTriangle className="w-3.5 h-3.5" />
          当前实盘账户未配置交易所 API Key，下单功能已禁用（余额/持仓可正常展示）。
        </div>
      )}
      {keysOk && !accountActive && (
        <div className="flex items-center gap-2 text-xs px-3 py-2 rounded bg-warning/10 text-warning border border-warning/20">
          <AlertTriangle className="w-3.5 h-3.5" /> 实盘账户已停用（is_active=false），禁止下单。
        </div>
      )}

      {/* 账户 KPI 6 张（Aurora 玻璃卡片） */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">总权益</span>
            <span className={icoCls}><Wallet className="w-3.5 h-3.5" /></span>
          </div>
          <div className="text-xl font-bold tabular-nums grad-text">${fmt(bal?.total_equity)}</div>
          <div className="text-xs text-muted-foreground mt-1 truncate">{activeAccount?.name ?? "实盘账户"}</div>
        </Card>
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">可用</span>
            <span className={icoCls}><Banknote className="w-3.5 h-3.5" /></span>
          </div>
          <div className="text-xl font-bold tabular-nums">${fmt(bal?.available_balance)}</div>
          <div className="text-xs text-muted-foreground mt-1 tabular-nums">
            可用率 {bal?.total_equity ? ((Number(bal.available_balance) / Number(bal.total_equity)) * 100).toFixed(2) : "—"}%
          </div>
        </Card>
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">浮动盈亏</span>
            <span className={icoCls}><TrendingUp className="w-3.5 h-3.5" /></span>
          </div>
          <div className={cn("text-xl font-bold tabular-nums", (bal?.unrealized_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
            {(bal?.unrealized_pnl ?? 0) >= 0 ? "+" : ""}${fmt(bal?.unrealized_pnl, 4)}
          </div>
          <div className="text-xs text-muted-foreground mt-1">实时浮动盈亏</div>
        </Card>
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">已用保证金</span>
            <span className={icoCls}><Shield className="w-3.5 h-3.5" /></span>
          </div>
          <div className="text-xl font-bold tabular-nums">${fmt(bal?.frozen_margin)}</div>
          <div className="text-xs text-muted-foreground mt-1 tabular-nums">
            保证金率 {bal?.total_equity ? ((Number(bal.frozen_margin) / Number(bal.total_equity)) * 100).toFixed(2) : "—"}%
          </div>
        </Card>
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">持仓数</span>
            <span className={icoCls}><Layers className="w-3.5 h-3.5" /></span>
          </div>
          <div className="text-xl font-bold tabular-nums">{bal?.position_count ?? positions.length}</div>
          <div className="text-xs text-muted-foreground mt-1 tabular-nums">持仓 {positions.length} 笔</div>
        </Card>
        <Card className="glass p-3.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-xs text-muted-foreground">持仓风险</span>
            <span className={icoCls}><AlertTriangle className="w-3.5 h-3.5" /></span>
          </div>
          <div className="text-xl font-bold tabular-nums text-profit">正常</div>
          <div className="text-xs text-muted-foreground mt-1">实时风控巡检</div>
        </Card>
      </div>

      {msg && (
        <div className={cn("text-xs px-3 py-2 rounded border", msgErr ? "bg-loss/10 text-loss border-loss/20" : "bg-profit/10 text-profit border-profit/20")}>
          {msg}
        </div>
      )}

      {/* 手动交易 + Asterdex 积分 / 当前持仓（1:2 分区） */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
        {/* 手动交易 */}
        <Card className="glass lg:col-span-1">
          <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
            <div className="flex items-center gap-2">
              <span className={icoCls}><Wallet className="w-3.5 h-3.5" /></span>
              <span className="text-sm font-medium">手动交易</span>
            </div>
            <span className="text-xs text-muted-foreground">{activeAccount?.name ?? "实盘账户"}</span>
          </div>
          <div className="p-4 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setSide("buy")}
                className={cn("py-2 rounded text-xs font-medium border transition-colors",
                  side === "buy" ? "bg-profit/15 text-profit border-profit/40" : "border-border text-muted-foreground")}
              ><TrendingUp className="w-3.5 h-3.5 inline mr-1" />做多</button>
              <button
                onClick={() => setSide("sell")}
                className={cn("py-2 rounded text-xs font-medium border transition-colors",
                  side === "sell" ? "bg-loss/15 text-loss border-loss/40" : "border-border text-muted-foreground")}
              ><TrendingDown className="w-3.5 h-3.5 inline mr-1" />做空</button>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">交易对</label>
              <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase().trim())}
                className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" placeholder="BTC" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">数量</label>
                <input type="number" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)}
                  className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">杠杆</label>
                <input type="number" min="1" value={leverage} onChange={(e) => setLeverage(e.target.value)}
                  className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button onClick={() => setOrderType("market")}
                className={cn("py-1.5 rounded text-xs border", orderType === "market" ? "bg-primary/10 text-primary border-primary/30" : "border-border text-muted-foreground")}>
                市价
              </button>
              <button onClick={() => setOrderType("limit")}
                className={cn("py-1.5 rounded text-xs border", orderType === "limit" ? "bg-primary/10 text-primary border-primary/30" : "border-border text-muted-foreground")}>
                限价
              </button>
            </div>
            {orderType === "limit" && (
              <div>
                <label className="text-xs text-muted-foreground block mb-1">限价</label>
                <input type="number" step="any" value={price} onChange={(e) => setPrice(e.target.value)}
                  className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" />
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">止盈 (TP)</label>
                <input type="number" step="any" value={tp} onChange={(e) => setTp(e.target.value)}
                  className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" placeholder="留空不设" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">止损 (SL)</label>
                <input type="number" step="any" value={sl} onChange={(e) => setSl(e.target.value)}
                  className="w-full bg-card border border-border rounded px-2 py-1.5 text-sm" placeholder="留空不设" />
              </div>
            </div>
            <Button className={cn("w-full", side === "buy" && "btn-glow")} disabled={!canTrade || orderMut.isPending}
              onClick={submitOrder}
              variant={side === "buy" ? "default" : "destructive"}>
              {orderMut.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}
              {side === "buy" ? "做多开仓" : "做空开仓"}
            </Button>
          </div>
        </Card>

        {isAsterdex ? (
          <div className="lg:col-span-2 space-y-4">
            {/* [2026-09-03] Asterdex 真实收入账本（替代已结束的 Stage 6 Rh 积分面板） */}
            <AsterLedgerCard
              icoCls={icoCls}
              isLoading={pointsQ.isLoading}
              isError={pointsQ.isError}
              keysConfigured={pointsQ.data?.keys_configured}
              message={pointsQ.data?.message}
              ledger={pointsQ.data?.ledger ?? null}
              policy={pointsQ.data?.policy}
              onRefresh={() => pointsQ.refetch()}
            />
          </div>
        ) : (
          <div className="lg:col-span-2">{positionsCard}</div>
        )}
      </div>

      {isAsterdex && positionsCard}

      {/* 挂单 */}
      <Card className="glass">
        <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
          <div className="flex items-center gap-2">
            <span className={icoCls}><Banknote className="w-3.5 h-3.5" /></span>
            <span className="text-sm font-medium">挂单</span>
            <Badge variant="secondary" className="text-xs">{orders.length} 笔</Badge>
          </div>
          <span className="text-xs text-muted-foreground">全部市场</span>
        </div>
        <div className="p-4">
          {orders.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
              <RefreshCw className="w-5 h-5 mx-auto mb-2 opacity-40" />
              暂无挂单
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left">交易对</th>
                    <th className="text-left">方向</th>
                    <th className="text-left">类型</th>
                    <th className="text-right">价格 <span className="text-cyan-300">▲</span></th>
                    <th className="text-right">数量 <span className="text-cyan-300">▲</span></th>
                    <th className="text-right">已成交 <span className="text-cyan-300">▲</span></th>
                    <th className="text-left">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o: LiveOrder, i: number) => (
                    <tr key={i} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-2 font-medium">{o.symbol}</td>
                      <td className="py-2 pr-2">
                        <Badge className={cn("text-xs", o.side === "buy" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>{o.side}</Badge>
                      </td>
                      <td className="py-2 pr-2 text-muted-foreground">{o.type}</td>
                      <td className="py-2 pr-2 text-right num">{fmtPrice(o.price)}</td>
                      <td className="py-2 pr-2 text-right num">{Number(o.amount).toFixed(4)}</td>
                      <td className="py-2 pr-2 text-right num">{Number(o.filled).toFixed(4)}</td>
                      <td className="py-2 text-muted-foreground">{o.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
