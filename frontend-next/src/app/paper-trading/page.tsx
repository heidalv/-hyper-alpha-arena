"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/PageHeader";
import { EquitySeriesCard } from "@/components/charts/EquitySeriesCard";
import {
  FlaskConical, TrendingUp, TrendingDown, Wallet, Clock,
  Loader2, RefreshCw, Plus, Trash2, DollarSign,
  Banknote, Shield, Layers, Receipt, Percent, ListOrdered,
  History, Inbox, PackageOpen, AlertTriangle, CheckCircle2,
} from "lucide-react";
import {
  useAccounts, usePaperBalance, usePositions, useOrders, usePaperSummary,
  useClosePosition, useCreateAccount, useDeleteAccount, invalidatePaperData,
  useSessions,
} from "@/hooks/useTradingData";
import { accountApi, paperApi } from "@/lib/api";
import type { PaperOrder, Position } from "@/types/api";
import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { sumBy, sumUnrealizedPnl } from "@/lib/stats";
import { formatCloseReason } from "@/lib/close-reason";
import { useQueryClient } from "@tanstack/react-query";
import { confirmDialog } from "@/lib/confirm";
import { useActivePaperAccountId } from "@/hooks/useActivePaperAccountId";

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

  // [2026-09-18 切页提速] 账户列表未返回时用"上次用过的账户"乐观兜底，
  // 让下面 4 个数据请求与账户列表**并行**发出（原先必须等列表 → 两级瀑布）。
  const activeAccountId = useActivePaperAccountId(accounts, selectedAccountId);
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

  // ── 账户操作（[2026-10-02 修复] 原先四个按钮：window.prompt 在 Electron 不可用 +
  //    无校验 + 无 try/catch ⇒ 后端 400/500 一律"点了没反应"）──
  const [amountDraft, setAmountDraft] = useState("");
  const [opBusy, setOpBusy] = useState<string | null>(null);
  const [opMsg, setOpMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const amountNum = Number(amountDraft);
  const amountValid =
    amountDraft.trim() !== "" && Number.isFinite(amountNum) && amountNum > 0 && amountNum <= 1e9;

  // 该账户是否有运行中的交易会话：决定「重置/改金额」是否会被循环立刻覆盖
  const { data: sessions } = useSessions();
  const runningSessionIds = useMemo(
    () =>
      (sessions ?? [])
        .filter(
          (s: any) =>
            (s.status === "running" || s.status === "defensive") &&
            (s.account_id === activeAccountId || s.paper_account_id === activeAccountId),
        )
        .map((s: any) => String(s.session_id)),
    [sessions, activeAccountId],
  );

  /** 统一操作包装：忙态 + 成功/失败反馈 + 全量失效（含权益曲线/账户缓存）。 */
  const runOp = async (key: string, fn: () => Promise<unknown>, okText: string) => {
    setOpBusy(key);
    setOpMsg(null);
    try {
      const res: any = await fn();
      invalidatePaperData(qc, activeAccountId);
      const running: string[] = res?.running_sessions ?? [];
      const isDelete = key === "disable" || key === "hardDelete";
      setOpMsg({
        ok: true,
        text:
          okText +
          (!isDelete && running.length
            ? `（注意：会话 ${running.join(" / ")} 仍在运行，会继续开仓）`
            : ""),
      });
      if (key === "amount") setAmountDraft("");
    } catch (e) {
      // apiRequest 抛 ApiError(detail) —— 后端的中文原因在这里第一次真正显示给用户
      setOpMsg({ ok: false, text: `操作失败：${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setOpBusy(null);
    }
  };

  const handleSetBalance = async () => {
    if (!amountValid || opBusy || activeAccountId == null) return;
    const val = amountNum;
    const acct = activeAccountId;
    const nPos = openPositions?.length ?? 0;
    // [2026-10-03 用户口径] 改金额 = 连带重置：破坏性动作，必须二次确认（有持仓时尤其要说清）
    if (!(await confirmDialog({
      title: `把初始金额改为 $${val} 并重置账户？`,
      description:
        `当前 $${Number(initialBal).toFixed(2)}。保存后：钱包基准变为 $${val}，`
        + `**持仓与订单会被清空**（当前 ${nPos} 个持仓）${runningSessionIds.length
          ? `；注意会话 ${runningSessionIds.join(" / ")} 仍在运行，会立刻按新金额继续开仓` : ""}。`
        + "（只想改基准、保留持仓？用旧口径的 reset_positions=false，本页不提供。）",
      tone: "danger",
      confirmText: "改金额并重置",
      requireText: nPos > 0 ? "重置" : undefined,
    }))) return;
    await runOp(
      "amount",
      () => paperApi.setBalance(acct, val, true),
      `初始资金已改为 $${val}，持仓/订单已随之重置`,
    );
  };

  /** [2026-10-03 用户需求] 一键平仓：平掉该账户全部 open 持仓。 */
  const handleCloseAll = async () => {
    if (opBusy || activeAccountId == null) return;
    const acct = activeAccountId;
    const nPos = openPositions?.length ?? 0;
    if (nPos === 0) return;
    if (!(await confirmDialog({
      title: `一键平仓：平掉全部 ${nPos} 个持仓？`,
      description: "逐笔按市价平仓（与单笔「平仓」同一路径）。平仓后该币会进入再开仓冷却。",
      tone: "warning",
      confirmText: `平掉 ${nPos} 笔`,
    }))) return;
    await runOp(
      "closeAll",
      async () => {
        const r = await paperApi.closeAll(acct);
        if (r?.failed_count) {
          throw new Error(`部分失败：成功 ${r.closed_count} / 失败 ${r.failed_count}（${r.failed?.slice(0, 3).map((f) => `${f.symbol}:${f.error}`).join("；")}）`);
        }
        return r;
      },
      `一键平仓完成：已平 ${nPos} 笔`,
    );
  };

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

  const totalPnl = sumUnrealizedPnl(openPositions ?? []);
  const totalMargin = sumBy(openPositions ?? [], (p) => p.margin || 0);
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
        breadcrumb={[{ label: "交易核心" }, { label: "实盘交易" }, { label: "模拟交易" }]}
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

          {/* 挂单情况（2026-09-23）——原「手动下单」面板已移除：该模块无实际用途，
              改为展示交易所侧自动挂出的止盈/止损条件单（用户要求）。 */}
          {/* [2026-09-23 对齐修复 v2 · 用户要求] ① 当前持仓**不滚动、全部显示**（槽位固定 10 个，
              内容高度自驱）；② 挂单卡**跟随持仓卡高度**（表格决定行高，挂单卡绝对定位填满同一高度，
              挂单超出时只在挂单框内滚动）。这样两卡严格等高、右侧不会被撑长也不留空白。 */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 lg:items-stretch">
            <div className="lg:col-span-1 relative min-h-0" data-testid="left-column">
              {/* [2026-09-23] 左列上下两卡：挂单情况 + 账户收益曲线（新卡，独立实现，不复用主页曲线）。
                  宽度约束（实测）：持仓表自然宽 995px，行宽 1352px ⇒ 三卡并排装不下
                  （995+340+330+间距 ≈1690）。故曲线与挂单同列上下堆叠，持仓保持原宽不失真。 */}
              <div className="lg:absolute lg:inset-0 flex flex-col gap-3">
                <PendingOrdersPanel
                  orders={orders ?? []}
                  positions={openPositions ?? []}
                  className="flex-1 min-h-0"
                />
                <EquitySeriesCard
                  accountId={activeAccountId}
                  initialPeriod="30d"
                  className="flex-1 min-h-0"
                />
              </div>
            </div>

            {/* 持仓列表（10 槽位，全部显示，不滚动） */}
            <div className="lg:col-span-2 min-h-0">
          <Card className="p-4 glass h-full flex flex-col" data-testid="open-positions-card">
            <div className="flex items-center justify-between mb-3 gap-2">
              <h2 className="text-sm font-medium">当前持仓 ({openPositions?.length ?? 0})</h2>
              {/* [2026-10-03 用户需求] 一键平仓（此前只有逐笔「平仓」） */}
              <Button
                variant="outline"
                size="sm"
                className="text-loss h-7"
                disabled={opBusy !== null || (openPositions?.length ?? 0) === 0}
                title={(openPositions?.length ?? 0) === 0 ? "当前无持仓" : "逐笔按市价平掉全部持仓"}
                onClick={handleCloseAll}
              >
                {opBusy === "closeAll"
                  ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                  : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                一键平仓
              </Button>
            </div>
            {openPositions && openPositions.length > 0 ? (
              <div className="overflow-x-auto" data-testid="open-positions-scroll">
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
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-muted-foreground flex-1">
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
                        sumBy(filteredOrders, (o) => o.pnl || 0) >= 0 ? "text-profit" : "text-loss")}>
                        {sumBy(filteredOrders, (o) => o.pnl || 0) >= 0 ? "+" : ""}${sumBy(filteredOrders, (o) => o.pnl || 0).toFixed(3)}
                      </td>
                      <td className="text-right py-2 num text-muted-foreground">${sumBy(filteredOrders, (o) => o.fee || 0).toFixed(4)}</td>
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
            <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
              <h2 className="text-sm font-medium">账户操作</h2>
              <span className="text-xs text-muted-foreground tabular-nums">
                当前初始资金 ${Number(initialBal).toFixed(2)} · 权益 ${Number(balance?.total_equity ?? 0).toFixed(2)}
              </span>
            </div>

            {/* 运行中会话警示（[2026-10-02] 用户"重置无效"的真因之一：交易循环仍在开仓） */}
            {runningSessionIds.length > 0 && (
              <div className="mb-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>
                  该账户有<strong>运行中的交易会话</strong>（{runningSessionIds.join(" / ")}）：重置或改金额后它会立刻按新资金继续开仓，
                  看起来像「重置没生效」。要真正停手，请先到「<strong>策略配置 → 交易总控 → 会话管理</strong>」停止会话
                  （原「AI 策略」入口已并入交易总控）。
                </span>
              </div>
            )}

            {/* 分配金额：原实现用 window.prompt —— Electron 不支持 prompt（按钮点了没反应），
                且无校验、无错误提示。现改为页内输入框 + 明确反馈。 */}
            <div className="mb-3 rounded-lg border border-border/60 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs text-muted-foreground shrink-0">分配金额（初始资金）</span>
                <Input
                  value={amountDraft}
                  onChange={(e) => setAmountDraft(e.target.value)}
                  placeholder={String(Number(initialBal).toFixed(0))}
                  inputMode="decimal"
                  aria-label="分配金额"
                  className="h-8 w-32 text-xs"
                />
                <Button
                  size="sm"
                  className="btn-glow"
                  disabled={opBusy !== null || !amountValid}
                  onClick={handleSetBalance}
                >
                  {opBusy === "amount" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <DollarSign className="w-3.5 h-3.5 mr-1" />}
                  保存金额
                </Button>
                <span className="text-[11px] text-muted-foreground">
                  正数，≤1e9 · <strong>改金额 = 连带重置</strong>（清空持仓与订单，钱包落到新金额）——
                  与「完整重置」一致，避免旧仓按旧基准计价
                </span>
              </div>
            </div>

            <div className="flex gap-2 flex-wrap">
              <Button
                variant="outline"
                size="sm"
                disabled={opBusy !== null}
                onClick={async () => {
                  if (!(await confirmDialog({
                    title: "软重置钱包？",
                    description: "只把已实现盈亏与手续费归零（保留持仓与交易记录）。持仓的浮动盈亏仍会继续计入权益。",
                    tone: "warning",
                    confirmText: "软重置",
                  }))) return;
                  await runOp("soft", () => paperApi.resetBalance(activeAccountId),
                    "软重置完成：已实现盈亏/手续费归零，持仓与交易记录保留");
                }}
              >
                {opBusy === "soft" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
                软重置
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-warning"
                disabled={opBusy !== null}
                onClick={async () => {
                  if (!(await confirmDialog({
                    title: "完整重置模拟账户？",
                    description:
                      "清除该账户全部持仓与订单，资金回到初始金额；不可恢复。"
                      + (runningSessionIds.length
                        ? `注意：会话 ${runningSessionIds.join(" / ")} 仍在运行，重置后会立刻重新开仓。`
                        : ""),
                    tone: "danger",
                    confirmText: "完整重置",
                    requireText: "重置",
                  }))) return;
                  await runOp("full", () => paperApi.fullReset(activeAccountId),
                    "完整重置完成：持仓/订单已清空，资金回到初始金额");
                }}
              >
                {opBusy === "full" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
                完整重置
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={opBusy !== null}
                onClick={async () => {
                  if (!(await confirmDialog({
                    title: "停用此模拟账户？",
                    description: "停用 = is_active/自动交易关闭 + 停掉绑定会话；历史数据保留（可在账户管理里彻底删除）。",
                    tone: "warning",
                    confirmText: "停用",
                  }))) return;
                  await runOp("disable", async () => deleteMut.mutateAsync(activeAccountId), "账户已停用（历史数据保留）");
                }}
              >
                {opBusy === "disable" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                停用账户
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-loss"
                disabled={opBusy !== null || (openPositions?.length ?? 0) > 0}
                title={(openPositions?.length ?? 0) > 0 ? "仍有持仓：先平仓或先做完整重置" : "只清状态（open 持仓 / pending 订单）；已平仓历史保留，用于决策归因与学习（PAPER_RESET_KEEP_HISTORY 可回滚）"}
                onClick={async () => {
                  if (!(await confirmDialog({
                    title: "彻底删除此模拟账户？",
                    description: "清除该账户的模拟资金/持仓/订单与凭证配置，账户行匿名化保留（历史审计表需要外键归属）。",
                    tone: "danger",
                    confirmText: "彻底删除",
                    requireText: "删除",
                  }))) return;
                  await runOp("hardDelete", async () => {
                    const r = await accountApi.delete(activeAccountId, { hard: true });
                    void qc.invalidateQueries({ queryKey: ["accounts"] });
                    return r;
                  }, "账户已彻底删除（资金/持仓/订单已清除）");
                }}
              >
                {opBusy === "hardDelete" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                彻底删除
              </Button>
            </div>

            {/* 操作反馈：原实现四个按钮全都没有 try/catch ⇒ 后端 400/500 一律"点了没反应" */}
            {opMsg && (
              <div className={cn(
                "mt-3 rounded-md border px-3 py-2 text-xs flex items-start gap-2",
                opMsg.ok ? "border-profit/40 bg-profit/10 text-profit" : "border-loss/40 bg-loss/10 text-loss",
              )}>
                {opMsg.ok ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                <span className="break-all">{opMsg.text}</span>
              </div>
            )}
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
              invalidatePaperData(qc, activeAccountId);
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
        // [2026-09-17] 短线车道已停：scalp/intraday 按存量口径标注（intraday=日内波段归中线）
        ({scalp:"短线(存量)",intraday:"中线",swing:"中线",trend_follow:"长线",position:"长线"} as Record<string,string>)[pos.trade_nature] || pos.trade_nature || "—"
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
          ? ({scalp:"短线(存量)",intraday:"中线",swing:"中线",trend_follow:"长线",position:"长线"} as Record<string,string>)[order.trade_nature] || order.trade_nature
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
      <td className="py-1.5 px-2 text-[10px] text-muted-foreground" title={order.close_reason || undefined}>
        {formatCloseReason(order.close_reason)}
      </td>
    </tr>
  );
}

/** 挂单情况（2026-09-23 取代原「手动下单」面板）。
 *
 * 展示交易所侧自动挂出的条件单（`order_type` = take_profit / stop_loss，`status` = pending）：
 * 这些单在开仓时由引擎挂出（`_upsert("take_profit"|"stop_loss")`），触发即市价平仓。
 * 每张单显示：触发价、距当前标记价的百分比（正=还在上方）、数量、挂出时间。
 */
function PendingOrdersPanel({
  orders,
  positions,
  className,
}: {
  orders: PaperOrder[];
  positions: Position[];
  className?: string;
}) {
  const pending = useMemo(
    () =>
      orders
        .filter((o) => o.status === "pending")
        .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))),
    [orders],
  );
  const markBySymbol = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of positions) {
      if (p.mark_price) m[p.symbol] = p.mark_price;
    }
    return m;
  }, [positions]);
  const nTp = pending.filter((o) => o.order_type === "take_profit").length;
  const nSl = pending.length - nTp;

  return (
    <Card className={cn("p-4 glass h-full flex flex-col", className)} data-testid="pending-orders-card">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-medium flex items-center gap-1.5">
          <ListOrdered className="w-3.5 h-3.5" /> 挂单情况 ({pending.length})
        </h2>
        {pending.length > 0 && (
          <span className="text-[10px] text-muted-foreground">止盈 {nTp} · 止损 {nSl}</span>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground mb-2">
        开仓后自动挂到交易所侧的止盈/止损条件单，触发即市价平仓
      </p>
      {pending.length === 0 ? (
        <div className="text-xs text-muted-foreground py-6 flex-1 flex flex-col items-center justify-center gap-1">
          <Inbox className="w-4 h-4 opacity-50" />
          当前无挂单
        </div>
      ) : (
        // [2026-09-23 修复] 原为 max-h-[320px]：10 张单只露 5 张、下方留大片空白（用户实测反馈）。
        // 改为 flex-1 + min-h-0：列表占满卡片剩余高度，超出才滚动，不再固定截断。
        <div
          data-testid="pending-orders-list"
          className="space-y-1.5 overflow-y-auto pr-1 flex-1 min-h-0"
        >
          {pending.map((o) => {
            const mark = markBySymbol[o.symbol];
            const px = o.price || 0;
            const dist = mark && px ? ((px - mark) / mark) * 100 : null;
            const isTp = o.order_type === "take_profit";
            return (
              <div
                key={o.id}
                className="rounded border border-border/40 px-2 py-1 hover:bg-muted/10"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-xs">{o.symbol}</span>
                  <span
                    className={cn(
                      "text-[10px] px-1.5 py-0.5 rounded border",
                      isTp ? "text-profit border-profit/40" : "text-loss border-loss/40",
                    )}
                  >
                    {isTp ? "止盈" : "止损"}
                  </span>
                </div>
                {/* [2026-09-23 紧凑化] 原为三行（触发/数量/时间各一行）⇒ 8 条就把框撑出滚动条。
                    合并为一行（触发价 · 距离 · 数量 · 时间），10 条槽位×2 张单也能全部落在框内。 */}
                <div className="flex items-center gap-2 mt-0.5 text-[10px] text-muted-foreground tabular-nums num whitespace-nowrap">
                  <span>触发 {px.toLocaleString()}</span>
                  <span className={cn(dist === null ? "" : dist >= 0 ? "text-profit" : "text-loss")}>
                    {dist === null ? "—" : `${dist >= 0 ? "+" : ""}${dist.toFixed(2)}%`}
                  </span>
                  <span className="truncate">{o.quantity !== undefined ? o.quantity.toFixed(4) : "—"}</span>
                  <span className="ml-auto">
                    {o.created_at
                      ? new Date(o.created_at).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
