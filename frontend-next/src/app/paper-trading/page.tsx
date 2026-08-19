"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  FlaskConical, TrendingUp, TrendingDown, Wallet, Clock,
  Loader2, RefreshCw, Plus, Trash2, DollarSign,
  Banknote, Shield, Layers, Receipt, Percent, ListOrdered,
  History, Inbox, PackageOpen,
} from "lucide-react";
import { OrderForm } from "@/components/trading/OrderForm";
import {
  useAccounts, usePaperBalance, usePositions, useOrders, usePaperSummary,
  useClosePosition, useCreateAccount, useDeleteAccount,
} from "@/hooks/useTradingData";
import { paperApi } from "@/lib/api";
import type { PaperOrder, Position } from "@/types/api";
import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

export default function PaperTradingPage() {
  const { data: accounts } = useAccounts();
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const qc = useQueryClient();

  // 旧前端逻辑：过滤 paper 账户，默认选 id 最大的
  const paperAccounts = useMemo(() => {
    if (!accounts) return [];
    return accounts
      .filter((a) => a.trading_mode === "paper")
      .sort((a, b) => b.id - a.id);
  }, [accounts]);

  const activeAccountId = selectedAccountId ?? paperAccounts[0]?.id ?? null;
  const activeAccount = paperAccounts.find((a) => a.id === activeAccountId);

  // 对齐旧前端 loadData 的 5 个并行请求
  const { data: balance, isLoading: balanceLoading } = usePaperBalance(activeAccountId);
  const { data: openPositions } = usePositions(activeAccountId, "open");
  const { data: orders } = useOrders(activeAccountId);
  const { data: summary } = usePaperSummary(activeAccountId);

  const closeMut = useClosePosition();

  const [showCreate, setShowCreate] = useState(false);
  const [newAccountName, setNewAccountName] = useState("");
  const [newAccountBalance, setNewAccountBalance] = useState("500");
  const [recordFilter, setRecordFilter] = useState<"filled" | "all">("filled");
  const createMut = useCreateAccount();
  const deleteMut = useDeleteAccount();

  const handleCreate = async () => {
    if (!newAccountName.trim()) return;
    const created = await createMut.mutateAsync({
      name: newAccountName.trim(),
      trading_mode: "paper",
      account_type: "PAPER",
      initial_capital: parseFloat(newAccountBalance) || 500,
    });
    // 初始化模拟资金（否则 balance 404 → 账户未初始化，无法交易）
    const accountId = (created as any)?.id;
    if (accountId) {
      await paperApi.initialize(accountId, parseFloat(newAccountBalance) || 500);
    }
    setShowCreate(false);
    setNewAccountName("");
  };

  const filteredOrders = orders?.filter((o) =>
    recordFilter === "filled" ? o.status === "filled" : true
  ) ?? [];

  const totalPnl = openPositions?.reduce((s, p) => s + (p.unrealized_pnl || 0), 0) ?? 0;
  const totalMargin = openPositions?.reduce((s, p) => s + (p.margin || 0), 0) ?? 0;
  const feePaid = balance?.total_fee_paid ?? summary?.total_fees ?? 0;
  const initialBal = balance?.initial_balance ?? 500;
  // 总收益 = 当前权益 - 初始资金（已扣手续费后的真实盈亏）
  const totalReturn = balance
    ? (balance.total_equity ?? 0) - initialBal
    : (summary?.total_pnl ?? 0);
  const returnPct = balance?.return_pct ?? summary?.return_pct ?? 0;
  const summaryTrades =
    summary?.total_trades ?? summary?.total_closes ?? summary?.total_orders ?? 0;

  return (
    <div className="p-4 space-y-4">
      {/* 标题 + 账户选择（Aurora 统一页头） */}
      <PageHeader
        icon={<FlaskConical className="w-4 h-4" />}
        title="模拟交易"
        subtitle="Paper 验证运行中 · 模拟撮合不触达真实资金"
        refreshHint="2s 轮询"
        breadcrumb={[{ label: "交易核心" }, { label: "模拟交易" }]}
        actions={
          <>
            {paperAccounts.length > 0 && (
              <select
                value={activeAccountId ?? ""}
                onChange={(e) => setSelectedAccountId(Number(e.target.value))}
                className="bg-card border border-border text-sm rounded px-3 py-1.5"
              >
                {paperAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} (${a.current_cash?.toFixed(0)})
                  </option>
                ))}
              </select>
            )}
            <Button size="sm" variant="outline" onClick={() => setShowCreate(!showCreate)}>
              <Plus className="w-3.5 h-3.5 mr-1" />新建
            </Button>
          </>
        }
      />

      {/* 创建账户 */}
      {showCreate && (
        <Card className="p-4 border-primary/30">
          <div className="flex gap-2 items-end">
            <div className="flex-1">
              <label className="text-xs text-muted-foreground block mb-1">账户名称</label>
              <input
                type="text"
                value={newAccountName}
                onChange={(e) => setNewAccountName(e.target.value)}
                placeholder="如：测试账户"
                className="w-full bg-card border border-border text-sm rounded px-2 py-1.5"
              />
            </div>
            <div className="w-32">
              <label className="text-xs text-muted-foreground block mb-1">初始资金</label>
              <input
                type="number"
                value={newAccountBalance}
                onChange={(e) => setNewAccountBalance(e.target.value)}
                className="w-full bg-card border border-border text-sm rounded px-2 py-1.5"
              />
            </div>
            <Button size="sm" className="btn-glow" onClick={handleCreate} disabled={createMut.isPending || !newAccountName.trim()}>
              {createMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "创建"}
            </Button>
          </div>
        </Card>
      )}

      {/* 账户概览 */}
      {activeAccountId && (
        <>
          {/* KPI：账户实时数字（始终展示手续费 / 总收益） */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <StatCard
              label="总权益"
              value={balance ? `$${balance.total_equity?.toFixed(2)}` : balanceLoading ? "..." : "未初始化"}
              icon={Wallet}
              grad
            />
            <StatCard
              label="可用"
              value={balance ? `$${(balance.available_balance ?? balance.available_cash ?? 0).toFixed(2)}` : "—"}
              icon={Banknote}
            />
            <StatCard
              label="浮动盈亏"
              value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(3)}`}
              icon={totalPnl >= 0 ? TrendingUp : TrendingDown}
              color={totalPnl >= 0 ? "profit" : "loss"}
              grad
            />
            <StatCard
              label="已用保证金"
              value={`$${totalMargin.toFixed(2)}`}
              icon={Shield}
            />
            <StatCard
              label="持仓数"
              value={String(openPositions?.length ?? 0)}
              icon={Layers}
            />
            <StatCard
              label="手续费"
              value={balance || summary ? `$${Number(feePaid).toFixed(2)}` : "—"}
              icon={Receipt}
              color="loss"
            />
            <StatCard
              label="总收益"
              value={
                balance || summary
                  ? `${totalReturn >= 0 ? "+" : ""}$${totalReturn.toFixed(2)} (${returnPct >= 0 ? "+" : ""}${Number(returnPct).toFixed(1)}%)`
                  : "—"
              }
              icon={totalReturn >= 0 ? TrendingUp : TrendingDown}
              color={totalReturn >= 0 ? "profit" : "loss"}
              grad
            />
          </div>

          {/* 统计摘要 */}
          {summary && summaryTrades > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <StatCard label="总交易" value={String(summaryTrades)} icon={ListOrdered} />
              <StatCard
                label="胜率"
                value={`${(summary.win_rate * 100).toFixed(1)}%`}
                icon={summary.win_rate >= 0.5 ? TrendingUp : TrendingDown}
                color={summary.win_rate >= 0.5 ? "profit" : "loss"}
              />
              <StatCard
                label="已实现盈亏"
                value={`${(summary.realized_pnl ?? summary.total_pnl) >= 0 ? "+" : ""}$${(summary.realized_pnl ?? summary.total_pnl)?.toFixed(2)}`}
                color={(summary.realized_pnl ?? summary.total_pnl) >= 0 ? "profit" : "loss"}
                icon={DollarSign}
              />
              <StatCard
                label="累计手续费"
                value={`$${(summary.total_fees ?? feePaid).toFixed(2)}`}
                color="loss"
                icon={Receipt}
              />
              <StatCard label="盈亏比" value={summary.profit_factor?.toFixed(2) ?? "—"} icon={TrendingUp} />
              <StatCard
                label="收益率"
                value={`${summary.return_pct?.toFixed(1) ?? 0}%`}
                color={(summary.return_pct ?? 0) >= 0 ? "profit" : "loss"}
                icon={Percent}
              />
            </div>
          )}

          {/* 手动下单面板 */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
            <div className="lg:col-span-1">
              <OrderForm accountId={activeAccountId} />
            </div>

            {/* 持仓列表 */}
            <div className="lg:col-span-2">
          <Card className="p-4 glass">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium">当前持仓 ({openPositions?.length ?? 0})</h2>
            </div>
            {openPositions && openPositions.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-2 px-2">币种</th>
                      <th className="text-left py-2 px-2">方向</th>
                      <th className="text-left py-2 px-2">类型</th>
                      <th className="sortable text-right py-2 px-2">开仓价 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">当前价 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-right py-2 px-2">数量</th>
                      <th className="text-right py-2 px-2">杠杆</th>
                      <th className="sortable text-right py-2 px-2">保证金 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">浮盈 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">盈亏% <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-left py-2 px-2">持仓</th>
                      <th className="text-left py-2 px-2">止盈/止损</th>
                      <th className="text-center py-2 px-2">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map((pos) => (
                      <PositionRow
                        key={pos.id}
                        pos={pos}
                        onClose={() => closeMut.mutate({
                          accountId: activeAccountId,
                          symbol: pos.symbol,
                          side: pos.side,
                        })}
                        onPartialClose={(pct) => {
                          const qty = (pos.quantity || 0) * (pct / 100);
                          closeMut.mutate({
                            accountId: activeAccountId,
                            symbol: pos.symbol,
                            side: pos.side,
                            quantity: pct < 100 ? qty : undefined,
                          });
                        }}
                        closing={closeMut.isPending}
                      />
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td className="px-3 py-2 text-muted-foreground text-xs">合计 {openPositions?.length ?? 0} 笔</td>
                      <td colSpan={6} />
                      <td className="text-right py-2 num text-muted-foreground">${totalMargin.toFixed(2)}</td>
                      <td className={cn("text-right py-2 num font-bold", totalPnl >= 0 ? "text-profit" : "text-loss")}>
                        {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(3)}
                      </td>
                      <td colSpan={4} />
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
                <PackageOpen className="w-6 h-6 opacity-50" />
                <span className="text-sm">暂无持仓</span>
              </div>
            )}
          </Card>

            </div>
          </div>

          {/* 历史记录 */}
          <Card className="p-4 glass">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium">历史记录</h2>
              <div className="flex gap-1">
                <button
                  onClick={() => setRecordFilter("filled")}
                  className={cn("px-2 py-0.5 text-xs rounded", recordFilter === "filled" ? "bg-primary/10 text-primary" : "text-muted-foreground")}
                >已成交</button>
                <button
                  onClick={() => setRecordFilter("all")}
                  className={cn("px-2 py-0.5 text-xs rounded", recordFilter === "all" ? "bg-primary/10 text-primary" : "text-muted-foreground")}
                >全部</button>
              </div>
            </div>
            {filteredOrders.length > 0 ? (
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="data-table">
                  <thead className="sticky top-0 bg-card">
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-2 px-2">时间</th>
                      <th className="text-left py-2 px-2">币种</th>
                      <th className="text-left py-2 px-2">方向</th>
                      <th className="text-left py-2 px-2">类型</th>
                      <th className="sortable text-right py-2 px-2">价格 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-right py-2 px-2">数量</th>
                      <th className="text-right py-2 px-2">杠杆</th>
                      <th className="sortable text-right py-2 px-2">盈亏 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="sortable text-right py-2 px-2">手续费 <span className="sort-ico text-cyan-300">▲</span></th>
                      <th className="text-left py-2 px-2">状态</th>
                      <th className="text-left py-2 px-2">平仓原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredOrders.map((order) => (
                      <OrderRow key={order.id} order={order} />
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td className="px-3 py-2 text-muted-foreground text-xs">合计 {filteredOrders.length} 笔</td>
                      <td colSpan={6} />
                      <td className={cn("text-right py-2 num font-bold",
                        filteredOrders.reduce((s, o) => s + (o.pnl || 0), 0) >= 0 ? "text-profit" : "text-loss")}>
                        {filteredOrders.reduce((s, o) => s + (o.pnl || 0), 0) >= 0 ? "+" : ""}${filteredOrders.reduce((s, o) => s + (o.pnl || 0), 0).toFixed(3)}
                      </td>
                      <td className="text-right py-2 num text-muted-foreground">${filteredOrders.reduce((s, o) => s + (o.fee || 0), 0).toFixed(4)}</td>
                      <td colSpan={2} />
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground">
                <History className="w-6 h-6 opacity-50" />
                <span className="text-sm">暂无历史记录</span>
              </div>
            )}
          </Card>

          {/* 账户操作 */}
          <Card className="p-4">
            <h2 className="text-sm font-medium mb-3">账户操作</h2>
            <div className="flex gap-2 flex-wrap">
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  const val = prompt("输入新的初始余额：", "500");
                  if (val) {
                    await paperApi.setBalance(activeAccountId, parseFloat(val));
                    qc.invalidateQueries({ queryKey: ["balance", activeAccountId] });
                  }
                }}
              >
                <DollarSign className="w-3.5 h-3.5 mr-1" />设置余额
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  await paperApi.resetBalance(activeAccountId);
                  qc.invalidateQueries({ queryKey: ["balance", activeAccountId] });
                }}
              >
                <RefreshCw className="w-3.5 h-3.5 mr-1" />软重置
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-warning"
                onClick={async () => {
                  if (confirm("确认完整重置？所有持仓和订单将被清除！")) {
                    await paperApi.fullReset(activeAccountId);
                    qc.invalidateQueries({ queryKey: ["balance", activeAccountId] });
                    qc.invalidateQueries({ queryKey: ["positions", activeAccountId] });
                    qc.invalidateQueries({ queryKey: ["orders", activeAccountId] });
                  }
                }}
              >
                <RefreshCw className="w-3.5 h-3.5 mr-1" />完整重置
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-loss"
                onClick={() => {
                  if (confirm("确认删除此账户？所有数据将被清除。")) {
                    deleteMut.mutate(activeAccountId);
                  }
                }}
              >
                <Trash2 className="w-3.5 h-3.5 mr-1" />删除账户
              </Button>
            </div>
          </Card>
        </>
      )}

      {/* 未初始化提示 */}
      {activeAccountId && !balance && !balanceLoading && (
        <Card className="p-6 text-center border-warning/30">
          <p className="text-sm text-muted-foreground mb-3">此账户尚未初始化模拟交易钱包</p>
          <Button
            className="btn-glow"
            onClick={async () => {
              await paperApi.initialize(activeAccountId, activeAccount?.initial_capital || 500);
              qc.invalidateQueries({ queryKey: ["balance", activeAccountId] });
            }}
          >
            初始化钱包
          </Button>
        </Card>
      )}

      {/* 无 paper 账户 */}
      {!activeAccountId && !showCreate && (
        <Card className="p-6 text-center">
          <p className="text-sm text-muted-foreground mb-3">暂无模拟交易账户</p>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="w-3.5 h-3.5 mr-1" />创建账户
          </Button>
        </Card>
      )}
    </div>
  );
}

// ═══ 组件 ═══

function StatCard({
  label, value, icon: Icon, color, grad,
}: {
  label: string; value: string;
  icon: React.ComponentType<{ className?: string }>;
  color?: string;
  /** Aurora 渐变数字：grad + color=profit/loss → grad-text-green/red；无 color → grad-text */
  grad?: boolean;
}) {
  return (
    <Card className="relative p-3.5 glass">
      {/* 右上角图标徽章（设计稿 KPI 卡元素，与 dashboard KpiCell 同款） */}
      <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
        <Icon className="w-3.5 h-3.5" />
      </span>
      <div className="mb-2">
        <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={cn(
        "text-xl font-bold font-mono tabular-nums tracking-tight",
        grad
          ? color === "profit" ? "grad-text-green" : color === "loss" ? "grad-text-red" : "grad-text"
          : color && `text-${color}`
      )}>{value}</div>
    </Card>
  );
}

function formatPosPrice(v: number | null | undefined): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  if (n >= 0.01) return n.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 6 });
  return n.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 8 });
}

function PositionRow({ pos, onClose, closing, onPartialClose }: { pos: Position; onClose: () => void; closing: boolean; onPartialClose?: (pct: number) => void }) {
  const pnl = pos.unrealized_pnl || 0;
  const margin = pos.margin || 0;
  const pnlPct = margin > 0 ? (pnl / margin) * 100 : 0;
  // 已持时长由 HoldTimeCell 内部每秒自算；fallback 仅用于 opened_at 缺失（记 0）
  const holdHours = 0;
  const isLong = pos.side === "long";
  const [showPct, setShowPct] = useState(false);
  const entry = Number(pos.entry_price || 0);
  const mark = Number(pos.mark_price || pos.current_price || 0);
  const priceMovePct =
    entry > 0 && mark > 0 ? ((mark - entry) / entry) * 100 * (isLong ? 1 : -1) : null;
  const tpPct = (entry > 0 && pos.tp_price) ? ((pos.tp_price - entry) / entry) * 100 * (isLong ? 1 : -1) : null;
  const slPct = (entry > 0 && pos.sl_price) ? ((pos.sl_price - entry) / entry) * 100 * (isLong ? 1 : -1) : null;

  return (
    <tr className="border-b border-border/30 hover:bg-muted/20">
      <td className="py-2 px-2 font-medium">{pos.symbol}</td>
      <td className="py-2 px-2">
        <span className={cn("text-[10px] px-1 rounded", isLong ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>
          {isLong ? "多" : "空"}
        </span>
      </td>
      <td className="py-2 px-2 text-muted-foreground">{
        ({scalp:"短线",swing:"中线",trend_follow:"长线"} as Record<string,string>)[pos.trade_nature] || pos.trade_nature || "—"
      }</td>
      <td className="py-2 px-2 text-right tabular-nums num text-muted-foreground">{formatPosPrice(entry)}</td>
      <td className="py-2 px-2 text-right tabular-nums num">
        <div className={cn(
          "font-medium",
          mark > 0 && entry > 0
            ? (isLong ? mark >= entry : mark <= entry) ? "text-profit" : "text-loss"
            : "text-muted-foreground",
        )}>
          {formatPosPrice(mark > 0 ? mark : null)}
        </div>
        {priceMovePct != null && (
          <div className={cn("text-[9px]", priceMovePct >= 0 ? "text-profit" : "text-loss")}>
            {priceMovePct >= 0 ? "+" : ""}{priceMovePct.toFixed(2)}%
          </div>
        )}
      </td>
      <td className="py-2 px-2 text-right tabular-nums num">{(pos.size || pos.quantity || 0).toFixed(4)}</td>
      <td className="py-2 px-2 text-right tabular-nums num">{pos.leverage || 1}x</td>
      <td className="py-2 px-2 text-right tabular-nums num text-muted-foreground">${(margin).toFixed(2)}</td>
      <td className={cn("py-2 px-2 text-right tabular-nums num font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
        {pnl >= 0 ? "+" : ""}${pnl.toFixed(3)}
      </td>
      <td className={cn("py-2 px-2 text-right tabular-nums num", pnlPct >= 0 ? "text-profit" : "text-loss")}>
        {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
      </td>
      <td className="py-2 px-2 text-muted-foreground">
        <HoldTimeCell pos={pos} fallbackAgeHours={holdHours} />
      </td>
      <td className="py-2 px-2 text-[10px] space-y-0.5 min-w-20">
        {pos.tp_price && <div className="text-profit tabular-nums">TP {formatPosPrice(pos.tp_price)}{tpPct != null ? ` (${tpPct >= 0 ? "+" : ""}${tpPct.toFixed(1)}%)` : ""}</div>}
        {pos.sl_price && <div className="text-loss tabular-nums">SL {formatPosPrice(pos.sl_price)}{slPct != null ? ` (${slPct >= 0 ? "+" : ""}${slPct.toFixed(1)}%)` : ""}</div>}
      </td>
      <td className="py-2 px-2 text-center">
        {showPct && onPartialClose ? (
          <div className="flex gap-0.5 justify-center">
            {[25, 50, 75, 100].map(pct => (
              <button key={pct} onClick={() => { onPartialClose(pct); setShowPct(false); }} disabled={closing}
                className="text-[9px] px-1 py-0.5 rounded bg-loss/10 text-loss hover:bg-loss/20 transition-colors">{pct}%</button>
            ))}
            <button onClick={() => setShowPct(false)} className="text-[9px] text-muted-foreground px-1">✕</button>
          </div>
        ) : (
          <button onClick={() => onPartialClose ? setShowPct(true) : onClose()} disabled={closing}
            className="text-[10px] text-loss hover:text-loss/80 px-2 py-0.5 rounded hover:bg-loss/10 transition-colors">
            {closing ? <Loader2 className="w-3 h-3 animate-spin mx-auto" /> : "平仓"}
          </button>
        )}
      </td>
    </tr>
  );
}

/**
 * 持仓剩余时间倒计时 + AI 延长提示。
 * 对齐旧前端 PaperTradingPanel 的 hold_* 字段展示（已持/最大/剩余/进度/可延长范围），
 * 并补上实时秒级倒计时（旧版只是每次轮询时的静态快照）。
 */
function HoldTimeCell({ pos, fallbackAgeHours }: { pos: Position; fallbackAgeHours: number }) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const openedAtMs = pos.opened_at ? new Date(pos.opened_at).getTime() : null;
  const ageHours = openedAtMs != null ? (nowMs - openedAtMs) / 3600000 : fallbackAgeHours;
  const maxHoldHours: number | null = pos.max_hold_hours ?? null;
  const deadlineMs = openedAtMs != null && maxHoldHours ? openedAtMs + maxHoldHours * 3600000 : null;
  const remainingMs = deadlineMs != null ? deadlineMs - nowMs : null;

  const expired = Boolean(pos.hold_expired) || (remainingMs != null && remainingMs <= 0);
  const nearTimeout =
    !expired &&
    (Boolean(pos.hold_near_timeout) ||
      (remainingMs != null && maxHoldHours ? remainingMs <= maxHoldHours * 3600000 * 0.15 : false));
  const progressPct =
    pos.hold_progress_pct ?? (maxHoldHours ? Math.min(100, (ageHours / maxHoldHours) * 100) : null);

  const fmtHours = (h: number) => {
    const v = Math.max(0, h);
    if (v < 1) return `${Math.round(v * 60)}m`;
    if (v < 24) return `${v.toFixed(1)}h`;
    return `${(v / 24).toFixed(1)}天`;
  };
  const fmtCountdown = (ms: number) => {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(totalSec / 86400);
    const h = Math.floor((totalSec % 86400) / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (d > 0) return `${d}天${h}h`;
    if (h > 0) return `${h}h${m}m`;
    return `${m}m${s}s`;
  };

  const toneClass = expired ? "text-loss" : nearTimeout ? "text-warning" : "text-muted-foreground";
  const extendMin = pos.extend_step_hours_min ?? 4;
  const extendMax = pos.extend_step_hours_max ?? 16;
  const extendableH = pos.extendable_hours ?? null;
  const absCapH = pos.absolute_cap_hours ?? null;
  const canExtend = extendableH != null && extendableH > 0.05;

  return (
    <div className="space-y-0.5 min-w-[92px]">
      <div className="tabular-nums">已持{fmtHours(ageHours)}</div>
      {remainingMs != null && (
        <div className={cn("text-[10px] tabular-nums flex items-center gap-0.5", toneClass)}>
          <Clock className="w-2.5 h-2.5" />
          {expired ? "已超时" : `剩${fmtCountdown(remainingMs)}`}
          {progressPct != null ? ` (${progressPct.toFixed(0)}%)` : ""}
        </div>
      )}
      {(nearTimeout || expired) && (
        <div className={cn("text-[9px]", toneClass)}>
          {expired
            ? (pos.hold_ai_reviewable === false ? "短线已超时·待硬平" : "待AI平/延")
            : "待AI复审"}
          {pos.hold_ai_reviewable === false
            ? " · 短线禁延长"
            : canExtend
              ? ` · 可延+${extendMin}~${extendMax}h${absCapH != null ? `/至${fmtHours(absCapH)}` : ""}`
              : " · 已达延长上限"}
        </div>
      )}
      {pos.hold_ai_extended && <div className="text-[9px] text-primary">AI已延长</div>}
    </div>
  );
}

function OrderRow({ order }: { order: PaperOrder }) {
  const pnl = order.pnl || 0;
  const isLong = order.side === "buy" || order.side === "long";
  const statusColor =
    order.status === "filled" ? "text-profit" :
    order.status === "cancelled" || order.status === "rejected" ? "text-muted-foreground" :
    "text-warning";

  return (
    <tr className="border-b border-border/20 hover:bg-muted/10">
      <td className="py-1.5 px-2 text-muted-foreground text-[10px]">
        {order.created_at ? new Date(order.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
      </td>
      <td className="py-1.5 px-2 font-medium">{order.symbol}</td>
      <td className="py-1.5 px-2">
        <span className={cn("text-[10px]", isLong ? "text-profit" : "text-loss")}>
          {isLong ? "买" : "卖"}
        </span>
      </td>
      <td className="py-1.5 px-2 text-muted-foreground">{
        order.trade_nature
          ? ({scalp:"短线",swing:"中线",trend_follow:"长线",position:"长线"} as Record<string,string>)[order.trade_nature] || order.trade_nature
          : "—"
      }</td>
      <td className="py-1.5 px-2 text-right tabular-nums num text-muted-foreground">
        {(order.filled_price || order.entry_price || order.price || 0).toLocaleString()}
      </td>
      <td className="py-1.5 px-2 text-right tabular-nums num">{(order.filled_quantity || order.quantity || 0).toFixed(4)}</td>
      <td className="py-1.5 px-2 text-right tabular-nums num text-muted-foreground text-[10px]">
        {order.leverage ? `${order.leverage}x` : "—"}
      </td>
      <td className={cn("py-1.5 px-2 text-right tabular-nums num", pnl >= 0 ? "text-profit" : pnl < 0 ? "text-loss" : "text-muted-foreground")}>
        {pnl !== 0 ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(3)}` : "—"}
      </td>
      <td className="py-1.5 px-2 text-right tabular-nums num text-muted-foreground">
        {order.fee ? `$${order.fee.toFixed(4)}` : "—"}
      </td>
      <td className={cn("py-1.5 px-2 text-[10px]", statusColor)}>{
        ({filled:"已成交",pending:"待成交",cancelled:"已取消",rejected:"已拒绝",expired:"已过期"} as Record<string,string>)[order.status] || order.status
      }</td>
      <td className="py-1.5 px-2 text-[10px] text-muted-foreground">{
        order.close_reason ? ({
          "sl":"止损平仓","tp":"止盈平仓","manual":"手动平仓","hold_timeout_review":"持仓超时AI复审",
          "trend_review_reduce_30%":"趋势复审减仓30%","trend_review_reduce_50%":"趋势复审减仓50%",
          "trend_review_reduce_70%":"趋势复审减仓70%","trend_review_close":"趋势复审清仓",
          "master_close":"总控平仓","master_close_tiny_loss":"总控微亏平仓","master_reduce_min_loss":"总控减仓",
          "master_running_reduce":"总控运行中减仓","master_running_close":"总控运行中平仓",
          "circuit_breaker":"熔断平仓","daily_loss_limit":"日亏损限额","forced_liquidation":"强平",
          "trailing_stop":"追踪止损","signal_exit":"信号退出","reversal":"反向平仓",
          "auto_close":"自动平仓","expired":"过期平仓","partial_close":"部分平仓",
          "dust_cleanup":"零碎仓位清理","funding_exit":"资金费率平仓","funding_take":"资金费率止盈",
          "basis_close":"基差平仓","rebalance":"再平衡","risk_reduce":"风控减仓",
          "timeout_close":"超时平仓","profit_take":"止盈","loss_cut":"止损",
        } as Record<string,string>)[order.close_reason] || order.close_reason : "—"
      }</td>
    </tr>
  );
}
