"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  TrendingUp, TrendingDown, Wallet, RefreshCw, Loader2, AlertTriangle,
} from "lucide-react";
import { liveApi } from "@/lib/api";
import { useAccounts } from "@/hooks/useTradingData";
import { cn } from "@/lib/utils";

const fmt = (v: number | undefined | null, d = 2) =>
  v === undefined || v === null || isNaN(Number(v)) ? "—" : Number(v).toFixed(d);

const fmtPrice = (v: number | undefined | null) => {
  if (v === undefined || v === null || !Number(v)) return "—";
  const n = Number(v);
  return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(4);
};

export default function LiveTradingPage() {
  const { data: accounts } = useAccounts();
  const qc = useQueryClient();
  const liveAccounts = useMemo(
    () => (accounts ?? []).filter((a: any) => a.trading_mode === "live"),
    [accounts]
  );
  const [accountId, setAccountId] = useState<number | null>(null);
  const activeAccount = liveAccounts.find((a: any) => a.id === accountId) ?? liveAccounts[0];
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
    onError: (e: any) => { setMsgErr(true); setMsg(`下单失败: ${e?.message ?? e}`); },
  });
  const closeMut = useMutation({
    mutationFn: liveApi.closePosition,
    onSuccess: (r) => {
      invalidate();
      setMsgErr(!r?.success);
      setMsg(r?.success ? `平仓已提交: ${r.symbol}` : `平仓失败: ${r?.result?.message ?? ""}`);
    },
    onError: (e: any) => { setMsgErr(true); setMsg(`平仓失败: ${e?.message ?? e}`); },
  });

  const submitOrder = () => {
    setMsg(null);
    if (!window.confirm(`确认实盘下单？\n${side === "buy" ? "做多" : "做空"} ${symbol} ${quantity} @ ${orderType === "market" ? "市价" : `限价 ${price}`} ${leverage}x`)) return;
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

  const closePosition = (pos: any) => {
    if (!window.confirm(`确认平仓 ${pos.symbol} ${pos.side === "long" ? "多" : "空"} ${pos.size}？`)) return;
    closeMut.mutate({ account_id: aid, symbol: pos.symbol, side: pos.side });
  };

  return (
    <div className="p-4 space-y-4">
      {/* 标题 + 账户 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-loss" /> 实盘交易
        </h1>
        <div className="flex items-center gap-2">
          {liveAccounts.length > 0 && (
            <select
              value={activeAccount?.id ?? ""}
              onChange={(e) => setAccountId(Number(e.target.value))}
              className="bg-card border border-border text-sm rounded px-3 py-1.5"
            >
              {liveAccounts.map((a: any) => (
                <option key={a.id} value={a.id}>{a.name} · {a.exchange}</option>
              ))}
            </select>
          )}
          <Button size="sm" variant="outline" onClick={() => { invalidate(); balanceQ.refetch(); positionsQ.refetch(); ordersQ.refetch(); }}>
            <RefreshCw className="w-3.5 h-3.5 mr-1" /> 刷新
          </Button>
        </div>
      </div>

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

      {/* 账户卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card className="p-3">
          <div className="text-xs text-muted-foreground">总权益</div>
          <div className="text-lg font-bold tabular-nums">${fmt(bal?.total_equity)}</div>
        </Card>
        <Card className="p-3">
          <div className="text-xs text-muted-foreground">可用</div>
          <div className="text-lg font-bold tabular-nums">${fmt(bal?.available_balance)}</div>
        </Card>
        <Card className="p-3">
          <div className="text-xs text-muted-foreground">浮动盈亏</div>
          <div className={cn("text-lg font-bold tabular-nums", (bal?.unrealized_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
            {(bal?.unrealized_pnl ?? 0) >= 0 ? "+" : ""}${fmt(bal?.unrealized_pnl, 4)}
          </div>
        </Card>
        <Card className="p-3">
          <div className="text-xs text-muted-foreground">已用保证金</div>
          <div className="text-lg font-bold tabular-nums">${fmt(bal?.frozen_margin)}</div>
        </Card>
        <Card className="p-3">
          <div className="text-xs text-muted-foreground">持仓数</div>
          <div className="text-lg font-bold tabular-nums">{bal?.position_count ?? positions.length}</div>
        </Card>
      </div>

      {msg && (
        <div className={cn("text-xs px-3 py-2 rounded border", msgErr ? "bg-loss/10 text-loss border-loss/20" : "bg-profit/10 text-profit border-profit/20")}>
          {msg}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 手动下单 */}
        <Card className="p-4 space-y-3">
          <div className="text-sm font-medium flex items-center gap-2">
            <Wallet className="w-4 h-4 text-primary" /> 手动下单
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => setSide("buy")}
              className={cn("py-2 rounded text-sm font-medium border transition-colors",
                side === "buy" ? "bg-profit/15 text-profit border-profit/40" : "border-border text-muted-foreground")}
            ><TrendingUp className="w-3.5 h-3.5 inline mr-1" />做多</button>
            <button
              onClick={() => setSide("sell")}
              className={cn("py-2 rounded text-sm font-medium border transition-colors",
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
          <Button className="w-full" disabled={!canTrade || orderMut.isPending}
            onClick={submitOrder}
            variant={side === "buy" ? "default" : "destructive"}>
            {orderMut.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}
            {side === "buy" ? "做多开仓" : "做空开仓"}
          </Button>
        </Card>

        {/* 当前持仓 */}
        <Card className="p-4 lg:col-span-2 overflow-hidden">
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-medium">当前持仓 ({positions.length})</div>
            <Badge variant="secondary" className="text-[10px]">{activeAccount?.exchange ?? "asterdex"}</Badge>
          </div>
          {positionsQ.isLoading ? (
            <div className="flex justify-center py-10"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
          ) : positions.length === 0 ? (
            <div className="text-center py-10 text-muted-foreground text-sm">暂无持仓</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-muted-foreground border-b border-border">
                    <th className="text-left py-2 pr-2">币种</th>
                    <th className="text-left py-2 pr-2">方向</th>
                    <th className="text-right py-2 pr-2">开仓价</th>
                    <th className="text-right py-2 pr-2">当前价</th>
                    <th className="text-right py-2 pr-2">数量</th>
                    <th className="text-right py-2 pr-2">杠杆</th>
                    <th className="text-right py-2 pr-2">保证金</th>
                    <th className="text-right py-2 pr-2">浮盈</th>
                    <th className="text-right py-2 pr-2">盈亏%</th>
                    <th className="text-left py-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p: any, i: number) => {
                    const pnl = Number(p.unrealized_pnl || 0);
                    const pct = Number(p.margin) > 0 ? (pnl / Number(p.margin)) * 100 : 0;
                    return (
                      <tr key={i} className="border-b border-border/40 last:border-0 hover:bg-muted/30">
                        <td className="py-2 pr-2 font-medium">{p.symbol}</td>
                        <td className="py-2 pr-2">
                          <Badge className={cn("text-[10px]", p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                            {p.side === "long" ? "多" : "空"}
                          </Badge>
                        </td>
                        <td className="py-2 pr-2 text-right tabular-nums">{fmtPrice(p.entry_price)}</td>
                        <td className="py-2 pr-2 text-right tabular-nums">{fmtPrice(p.last_price ?? p.mark_price)}</td>
                        <td className="py-2 pr-2 text-right tabular-nums">{Number(p.size).toFixed(4)}</td>
                        <td className="py-2 pr-2 text-right tabular-nums">{Number(p.leverage || 1)}x</td>
                        <td className="py-2 pr-2 text-right tabular-nums">${fmt(p.margin)}</td>
                        <td className={cn("py-2 pr-2 text-right tabular-nums font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
                          {pnl >= 0 ? "+" : ""}${fmt(pnl, 4)}
                        </td>
                        <td className={cn("py-2 pr-2 text-right tabular-nums", pct >= 0 ? "text-profit" : "text-loss")}>
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
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* 挂单 */}
      <Card className="p-4">
        <div className="text-sm font-medium mb-3">挂单 ({orders.length})</div>
        {orders.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground text-sm">暂无挂单</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b border-border">
                  <th className="text-left py-2 pr-2">交易对</th>
                  <th className="text-left py-2 pr-2">方向</th>
                  <th className="text-left py-2 pr-2">类型</th>
                  <th className="text-right py-2 pr-2">价格</th>
                  <th className="text-right py-2 pr-2">数量</th>
                  <th className="text-right py-2 pr-2">已成交</th>
                  <th className="text-left py-2">状态</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o: any, i: number) => (
                  <tr key={i} className="border-b border-border/40 last:border-0">
                    <td className="py-2 pr-2 font-medium">{o.symbol}</td>
                    <td className="py-2 pr-2"><span className={cn("text-[10px] px-1.5 py-0.5 rounded", o.side === "buy" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>{o.side}</span></td>
                    <td className="py-2 pr-2 text-muted-foreground">{o.type}</td>
                    <td className="py-2 pr-2 text-right tabular-nums">{fmtPrice(o.price)}</td>
                    <td className="py-2 pr-2 text-right tabular-nums">{Number(o.amount).toFixed(4)}</td>
                    <td className="py-2 pr-2 text-right tabular-nums">{Number(o.filled).toFixed(4)}</td>
                    <td className="py-2 text-muted-foreground">{o.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Asterdex 专属：合约交易积分 + 收益预期 */}
      {activeAccount?.exchange === "asterdex" && (
        <Card className="p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">Asterdex 合约交易积分 · 收益预期</div>
            <Badge variant="secondary" className="text-[10px]">Rh 积分</Badge>
          </div>
          {pointsQ.data?.keys_configured === false ? (
            <div className="text-xs px-3 py-2 rounded bg-warning/10 text-warning border border-warning/20">
              {pointsQ.data?.message ?? "未配置 Asterdex API Key，无法获取积分数据"}
            </div>
          ) : pointsQ.isLoading ? (
            <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
          ) : pointsQ.isError ? (
            <div className="text-xs px-3 py-2 rounded bg-loss/10 text-loss border border-loss/20">
              积分数据获取失败（需要 Asterdex API Key 与账户授权）
            </div>
          ) : pointsQ.data?.points ? (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="p-3 rounded bg-muted/30">
                  <div className="text-xs text-muted-foreground">当前 Rh 积分</div>
                  <div className="text-lg font-bold tabular-nums">{Number(pointsQ.data.points.points_balance).toLocaleString()}</div>
                  <div className="text-[10px] text-muted-foreground">乘数 x{pointsQ.data.points.points_multiplier}</div>
                </div>
                <div className="p-3 rounded bg-muted/30">
                  <div className="text-xs text-muted-foreground">赛季/阶段</div>
                  <div className="text-lg font-bold">{pointsQ.data.points.season || "—"}</div>
                  <div className="text-[10px] text-muted-foreground">
                    合格 {pointsQ.data.points.qualifying_days}/{pointsQ.data.points.required_days} 天
                  </div>
                </div>
                <div className="p-3 rounded bg-muted/30">
                  <div className="text-xs text-muted-foreground">空投预估价值</div>
                  <div className={cn("text-lg font-bold tabular-nums", pointsQ.data.points.airdrop_eligible ? "text-profit" : "")}>
                    ${fmt(pointsQ.data.points.estimated_airdrop_value)}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {pointsQ.data.points.airdrop_eligible ? "已具备空投资格" : "暂未达标"}
                  </div>
                </div>
                <div className="p-3 rounded bg-muted/30">
                  <div className="text-xs text-muted-foreground">预计月价值</div>
                  <div className="text-lg font-bold tabular-nums">${fmt(pointsQ.data.projection.total_estimated_monthly_value)}</div>
                  <div className="text-[10px] text-muted-foreground">返佣 + 空投估算</div>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div className="p-2.5 rounded bg-muted/20">
                  <div className="text-muted-foreground mb-1">7日交易量</div>
                  <div className="font-medium tabular-nums">${fmt(pointsQ.data.projection.volume_7d_usd)}</div>
                </div>
                <div className="p-2.5 rounded bg-muted/20">
                  <div className="text-muted-foreground mb-1">返佣率 (当前)</div>
                  <div className="font-medium tabular-nums">{(Number(pointsQ.data.projection.rebate_rate) * 100).toFixed(4)}%</div>
                </div>
                <div className="p-2.5 rounded bg-muted/20">
                  <div className="text-muted-foreground mb-1">预计返佣</div>
                  <div className="font-medium tabular-nums">
                    周 ${fmt(pointsQ.data.projection.weekly_rebate_usd)} · 月 ${fmt(pointsQ.data.projection.monthly_rebate_usd)} · 年 ${fmt(pointsQ.data.projection.yearly_rebate_usd)}
                  </div>
                </div>
                <div className="p-2.5 rounded bg-muted/20">
                  <div className="text-muted-foreground mb-1">预计积分</div>
                  <div className="font-medium tabular-nums">
                    日 {fmt(pointsQ.data.projection.daily_points, 1)}
                    {pointsQ.data.projection.points_estimated ? " (估算)" : ""} · 周 {fmt(pointsQ.data.projection.weekly_points, 1)} · 月 {fmt(pointsQ.data.projection.monthly_points, 1)}
                  </div>
                </div>
              </div>

              {pointsQ.data.history?.length > 0 && (
                <div className="pt-1">
                  <div className="text-xs text-muted-foreground mb-2">积分记录（最近 {pointsQ.data.history.length} 条快照）</div>
                  <div className="overflow-x-auto max-h-48 overflow-y-auto border border-border/40 rounded">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-card">
                        <tr className="text-muted-foreground border-b border-border/40">
                          <th className="text-left py-1.5 px-2">时间</th>
                          <th className="text-right py-1.5 px-2">积分</th>
                          <th className="text-right py-1.5 px-2">乘数</th>
                          <th className="text-right py-1.5 px-2">空投预估</th>
                          <th className="text-right py-1.5 px-2">7日交易量</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pointsQ.data.history.map((h: any, i: number) => (
                          <tr key={i} className="border-b border-border/20 last:border-0">
                            <td className="py-1.5 px-2 text-muted-foreground">{String(h.snapshot_time).replace("T", " ").slice(5, 16)}</td>
                            <td className="py-1.5 px-2 text-right tabular-nums">{Number(h.points_balance).toLocaleString()}</td>
                            <td className="py-1.5 px-2 text-right tabular-nums">x{h.points_multiplier}</td>
                            <td className="py-1.5 px-2 text-right tabular-nums">${fmt(h.estimated_airdrop_value)}</td>
                            <td className="py-1.5 px-2 text-right tabular-nums">${fmt(h.volume_7d_usd)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-6 text-muted-foreground text-sm">暂无积分数据</div>
          )}
        </Card>
      )}
    </div>
  );
}
