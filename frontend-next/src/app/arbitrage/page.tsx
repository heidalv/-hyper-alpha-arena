"use client";

import { useEffect, useState, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  ArrowRightLeft, Activity, Shield, Zap, TrendingUp, TrendingDown,
  RefreshCw, Loader2, DollarSign, AlertTriangle, CheckCircle2, Clock,
  Coins, Flame, Wallet, Settings2, Save,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
const BACKEND = getBackendUrl().replace(/\/$/, "");

type Tab = "overview" | "positions" | "opportunities" | "analytics" | "capital" | "rebate" | "config";

export default function ArbitragePage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      fetch(`${BACKEND}/api/arbitrage/status`).then(r => r.json()),
      fetch(`${BACKEND}/api/arbitrage/positions?status=active`).then(r => r.json()),
      fetch(`${BACKEND}/api/arbitrage/opportunities`).then(r => r.json()),
      fetch(`${BACKEND}/api/arbitrage/capital-pool`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/status`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/opportunities`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/positions?status=active`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/capital`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/wash-trade/status`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/analytics`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/points/summary`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/rules/gate`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/programs`).then(r => r.json()),
      fetch(`${BACKEND}/api/rebate/incentives`).then(r => r.json()),
    ]);
    const keys = ["arbStatus","arbPositions","arbOpps","arbCapital","rebStatus","rebOpps","rebPositions","rebCapital","washStatus","rebAnalytics","pointsSummary","ruleGate","programs","incentives"];
    const next: any = {};
    results.forEach((r, i) => { if (r.status === "fulfilled") next[keys[i]] = r.value; });
    setData(next);
    setLoading(false);
  }, []);

  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);

  const arb = data.arbStatus || {};
  const reb = data.rebStatus || {};
  const rebA = data.rebAnalytics || {};
  const wash = data.washStatus || {};
  const gate = data.ruleGate || {};
  const rebCap = data.rebCapital || {};
  const arbCap = data.arbCapital || {};
  const points = data.pointsSummary || {};
  const arbPos = data.arbPositions?.positions || (Array.isArray(data.arbPositions) ? data.arbPositions : []);
  const rebPos = data.rebPositions?.positions || (Array.isArray(data.rebPositions) ? data.rebPositions : []);
  const arbOpps = data.arbOpps?.opportunities || (Array.isArray(data.arbOpps) ? data.arbOpps : []);
  const rebOpps = data.rebOpps?.opportunities || (Array.isArray(data.rebOpps) ? data.rebOpps : []);

  const totalPoints = Object.values(points.exchanges || {}).reduce((s: number, e: any) => s + (e.points_earned_total || 0), 0);
  const totalPointsUsd = Object.values(points.exchanges || {}).reduce((s: number, e: any) => s + (e.estimated_value_usd || 0), 0);
  const netPnl = rebA.net_pnl ?? rebA.total_pnl ?? 0;

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: "overview", label: "总览", icon: Activity },
    { key: "positions", label: "持仓", icon: TrendingUp },
    { key: "opportunities", label: "机会", icon: Zap },
    { key: "analytics", label: "分析", icon: Coins },
    { key: "capital", label: "资金池", icon: Wallet },
    { key: "rebate", label: "积分/返佣", icon: DollarSign },
    { key: "config", label: "配置", icon: Settings2 },
  ];

  return (
    <div className="p-4 space-y-4">
      {/* 标题（Aurora 统一页头） */}
      <PageHeader
        icon={<ArrowRightLeft className="w-4 h-4" />}
        title="套利中心"
        subtitle="V3 统计套利 + 返佣积分引擎 · 多交易所机会扫描与风控闸门"
        refreshHint="多交易所扫描"
        breadcrumb={[{ label: "交易核心" }, { label: "套利中心" }]}
        actions={
          <>
            {/* V3 引擎灯 */}
            <Badge variant="secondary" className={cn("text-xs", arb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>V3 {arb.engine_enabled ? "ON" : "OFF"}</Badge>
            {/* Rebate 引擎灯 */}
            <Badge variant="secondary" className={cn("text-xs", reb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>Rebate {reb.engine_enabled ? "ON" : "OFF"}</Badge>
            {/* 规则闸门 */}
            {gate.rebate_pause && <Badge variant="destructive" className="text-xs">规则暂停</Badge>}
            {/* 刷新 */}
            <Button variant="ghost" size="sm" onClick={load} disabled={loading}><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} /></Button>
          </>
        }
      />

      {/* KPI Strip */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        <KpiTile label="V3持仓" value={String(arb.active_positions ?? 0)} icon={Activity} />
        <KpiTile label="Rebate持仓" value={String(reb.active_positions ?? 0)} icon={TrendingUp} />
        <KpiTile label="净收益" value={`${netPnl >= 0 ? "+" : ""}$${netPnl.toFixed(2)}`} color={netPnl >= 0 ? "profit" : "loss"} icon={DollarSign} grad />
        <KpiTile label="总积分" value={totalPoints.toFixed(0)} subValue={`≈$${totalPointsUsd.toFixed(2)}`} icon={Coins} />
        <KpiTile label="刷量安全" value={wash.is_safe ? "✅" : "⚠️"} color={wash.is_safe ? "profit" : "warning"} icon={Shield} />
        <KpiTile label="扫描次数" value={String(rebA.scan_count ?? reb.scan_count ?? 0)} icon={RefreshCw} />
      </div>

      {/* Tab */}
      <div className="flex gap-1 border-b border-border overflow-x-auto">
        {tabs.map(t => {
          const Icon = t.icon;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={cn("flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px whitespace-nowrap",
                tab === t.key ? "border-primary text-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>
              <Icon className="w-3.5 h-3.5" />{t.label}
            </button>
          );
        })}
      </div>

      {loading && !data.arbStatus ? (
        <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
      ) : (
        <>
          {/* ── 总览 ── */}
          {tab === "overview" && (
            <div className="space-y-3">
              {/* 引擎状态 grid3 */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {/* V3 引擎 */}
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <HeadIcon icon={Zap} />
                      <h2 className="text-sm font-medium truncate">V3 统计套利引擎</h2>
                    </div>
                    <Badge variant="secondary" className={cn("text-xs flex-shrink-0", arb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>
                      <span className={cn("w-1.5 h-1.5 rounded-full", arb.engine_enabled ? "bg-profit" : "bg-muted-foreground")} />
                      {arb.engine_enabled ? "运行中" : "已停止"}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <Kv k="扫描" v={String(arb.scanner_scan_count ?? 0)} />
                    <Kv k="持仓" v={String(arb.active_positions ?? 0)} />
                    <Kv k="缓存机会" v={String(arb.cached_opportunities ?? 0)} />
                    <Kv k="Tick" v={String(arb.tick_count ?? 0)} />
                    <Kv k="熔断" v={arb.circuit_breaker_active ? "已触发" : "正常"} color={arb.circuit_breaker_active ? "loss" : "profit"} />
                    <Kv k="模式" v={arb.mode || "—"} />
                  </div>
                </Card>

                {/* Rebate 引擎 */}
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <HeadIcon icon={DollarSign} />
                      <h2 className="text-sm font-medium truncate">返佣/积分引擎</h2>
                    </div>
                    <Badge variant="secondary" className={cn("text-xs flex-shrink-0", reb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>
                      <span className={cn("w-1.5 h-1.5 rounded-full", reb.engine_enabled ? "bg-profit" : "bg-muted-foreground")} />
                      {reb.engine_enabled ? "运行中" : "已停止"}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <Kv k="扫描" v={String(reb.scan_count ?? rebA.scan_count ?? 0)} />
                    <Kv k="执行" v={String(reb.execution_count ?? rebA.execution_count ?? 0)} />
                    <Kv k="持仓" v={String(reb.active_positions ?? 0)} />
                    <Kv k="总交易" v={String(rebA.total_trades ?? 0)} />
                    <Kv k="胜率" v={`${((rebA.win_rate ?? 0) * 100).toFixed(1)}%`} color={(rebA.win_rate ?? 0) >= 0.5 ? "profit" : "loss"} />
                    <Kv k="PnL" v={`${(rebA.total_pnl ?? 0) >= 0 ? "+" : ""}${(rebA.total_pnl ?? 0).toFixed(2)}`} color={(rebA.total_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                    <Kv k="返佣" v={`$${(rebA.total_rebate ?? 0).toFixed(2)}`} />
                    <Kv k="积分" v={(rebA.total_points ?? 0).toFixed(0)} color="warning" />
                    <Kv k="净PnL" v={`${(rebA.net_pnl ?? 0) >= 0 ? "+" : ""}$${(rebA.net_pnl ?? 0).toFixed(2)}`} color={(rebA.net_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                  </div>
                </Card>

                {/* 刷量安全 */}
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <HeadIcon icon={Shield} />
                      <h2 className="text-sm font-medium truncate">刷量安全</h2>
                    </div>
                    <Badge variant="secondary" className={cn("text-xs flex-shrink-0", wash.is_safe ? "bg-profit/20 text-profit" : "bg-warning/20 text-warning")}>
                      <span className={cn("w-1.5 h-1.5 rounded-full", wash.is_safe ? "bg-profit" : "bg-warning")} />
                      {wash.is_safe ? "已启用" : "告警"}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <Kv k="安全状态" v={wash.is_safe ? "安全" : "警告"} color={wash.is_safe ? "profit" : "warning"} />
                    <Kv k="风险等级" v={wash.risk_level || "—"} />
                    <Kv k="今日交易数" v={String(wash.trade_count_today ?? 0)} />
                    <Kv k="今日成交额" v={`$${(wash.daily_volume_usd ?? 0).toFixed(0)}`} />
                    <Kv k="安全间隔" v={`${(wash.next_safe_interval_sec ?? 0).toFixed(0)}s`} />
                  </div>
                </Card>
              </div>

              {/* 规则闸门 */}
              <Card className="p-4 glass">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <HeadIcon icon={AlertTriangle} />
                    <h2 className="text-sm font-medium">规则闸门</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">同步于 · 自动生效</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-1.5 text-xs">
                  <Kv k="Rebate 暂停" v={gate.rebate_pause ? "是" : "否"} color={gate.rebate_pause ? "loss" : "profit"} />
                  <Kv k="V3 暂停" v={gate.v3_pause ? "是" : "否"} color={gate.v3_pause ? "loss" : "profit"} />
                  <Kv k="暂停策略数" v={String(gate.paused_strategies?.length ?? 0)} />
                </div>
                {gate.pause_reason && <div className="mt-2 text-xs text-loss">原因: {gate.pause_reason}</div>}
              </Card>

              {/* 策略分析 */}
              {rebA.by_strategy && Object.keys(rebA.by_strategy).length > 0 && (
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2">
                      <HeadIcon icon={TrendingUp} />
                      <h2 className="text-sm font-medium">策略分析</h2>
                    </div>
                    <span className="text-xs text-muted-foreground">今日 · 自动更新</span>
                  </div>
                  <table className="data-table">
                    <thead><tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-2 px-2">策略</th><th className="text-right py-2 px-2">交易数 <span className="text-cyan-300">▲</span></th>
                      <th className="text-right py-2 px-2">胜率 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">PnL <span className="text-cyan-300">▲</span></th>
                      <th className="text-right py-2 px-2">积分 <span className="text-cyan-300">▲</span></th>
                    </tr></thead>
                    <tbody>
                      {Object.entries(rebA.by_strategy).map(([s, v]: [string, any]) => (
                        <tr key={s} className="border-b border-border/30">
                          <td className="py-2 px-2 font-medium">{s}</td>
                          <td className="py-2 px-2 text-right tabular-nums num">{v.count ?? 0}</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums num", (v.win_rate ?? 0) >= 0.5 ? "text-profit" : "text-loss")}>{((v.win_rate ?? 0) * 100).toFixed(1)}%</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums num", (v.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>{(v.pnl ?? 0) >= 0 ? "+" : ""}${(v.pnl ?? 0).toFixed(2)}</td>
                          <td className="py-2 px-2 text-right tabular-nums num text-warning">{(v.points ?? 0).toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td className="px-3 py-2 text-muted-foreground text-xs">合计 {Object.keys(rebA.by_strategy).length} 策略</td>
                        <td className="text-right py-2 num text-muted-foreground">{Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.count ?? 0), 0)}</td>
                        <td />
                        <td className={cn("text-right py-2 num font-bold",
                          Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.pnl ?? 0), 0) >= 0 ? "text-profit" : "text-loss")}>
                          {Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.pnl ?? 0), 0) >= 0 ? "+" : ""}${Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.pnl ?? 0), 0).toFixed(2)}
                        </td>
                        <td className="text-right py-2 num text-warning">{Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.points ?? 0), 0).toFixed(0)}</td>
                      </tr>
                    </tfoot>
                  </table>
                </Card>
              )}
            </div>
          )}

          {/* ── 持仓 ── */}
          {tab === "positions" && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
              <Card className="p-4 glass">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <HeadIcon icon={ArrowRightLeft} />
                    <h2 className="text-sm font-medium">套利持仓</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">{arbPos.length} 笔对冲</span>
                </div>
                {arbPos.length === 0 ? <Empty /> : <PositionTable positions={arbPos} />}
              </Card>
              <Card className="p-4 glass">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <HeadIcon icon={DollarSign} />
                    <h2 className="text-sm font-medium">返佣持仓</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">{rebPos.length} 笔挂单</span>
                </div>
                {rebPos.length === 0 ? <Empty /> : <PositionTable positions={rebPos} />}
              </Card>
            </div>
          )}

          {/* ── 机会 ── */}
          {tab === "opportunities" && (
            <div className="space-y-3">
              <Card className="p-4 glass">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <HeadIcon icon={Zap} />
                    <h2 className="text-sm font-medium">套利机会</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">{arbOpps.length} 个</span>
                </div>
                {arbOpps.length === 0 ? <Empty /> : (
                  <div className="space-y-1">
                    {arbOpps.slice(0, 20).map((o: any, i: number) => (
                      <div key={i} className="flex items-center justify-between py-1.5 border-b border-border/20 text-xs">
                        <div className="flex items-center gap-2"><span className="font-medium">{o.symbol || "—"}</span><span className="text-muted-foreground">{o.strategy || o.type || "—"}</span></div>
                        <span className="text-muted-foreground tabular-nums">{((o.spread || o.profit_pct || 0) * 100).toFixed(3)}%</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
              <Card className="p-4 glass">
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2">
                    <HeadIcon icon={CheckCircle2} />
                    <h2 className="text-sm font-medium">返佣机会</h2>
                  </div>
                  <span className="text-xs text-muted-foreground">实时评分 · 每 10s 更新</span>
                </div>
                {rebOpps.length === 0 ? <Empty /> : (
                  <table className="data-table"><thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-1.5 px-2">币种</th><th className="text-left py-1.5 px-2">交易所</th><th className="text-right py-1.5 px-2">返佣 <span className="text-cyan-300">▲</span></th><th className="text-right py-1.5 px-2">分数 <span className="text-cyan-300">▲</span></th></tr></thead>
                  <tbody>{rebOpps.slice(0, 30).map((o: any, i: number) => (<tr key={i} className="border-b border-border/20"><td className="py-1.5 px-2 font-medium">{o.symbol || "—"}</td><td className="py-1.5 px-2 text-muted-foreground">{o.exchange || "—"}</td><td className="py-1.5 px-2 text-right tabular-nums num text-warning">${(o.expected_rebate_usd || o.rebate_usd || 0).toFixed(4)}</td><td className="py-1.5 px-2 text-right tabular-nums num">{o.score?.toFixed(1) ?? "—"}</td></tr>))}</tbody>
                  <tfoot><tr><td className="px-3 py-1.5 text-muted-foreground text-xs">合计 {rebOpps.length} 机会</td><td colSpan={3} /></tr></tfoot>
                  </table>
                )}
              </Card>
            </div>
          )}

          {/* ── 分析 ── */}
          {tab === "analytics" && rebA.engine_mode !== undefined && (
            <Card className="p-4 glass">
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2">
                  <HeadIcon icon={Coins} />
                  <h2 className="text-sm font-medium">返佣引擎分析</h2>
                </div>
                <span className="text-xs text-muted-foreground">按策略分表</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <Cell label="引擎模式" value={rebA.engine_mode || "—"} />
                <Cell label="扫描次数" value={String(rebA.scan_count ?? 0)} />
                <Cell label="执行次数" value={String(rebA.execution_count ?? 0)} />
                <Cell label="总交易" value={String(rebA.total_trades ?? 0)} />
                <Cell label="胜率" value={`${((rebA.win_rate ?? 0) * 100).toFixed(1)}%`} color={(rebA.win_rate ?? 0) >= 0.5 ? "profit" : "loss"} />
                <Cell label="总PnL" value={`$${(rebA.total_pnl ?? 0).toFixed(2)}`} color={(rebA.total_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                <Cell label="总返佣" value={`$${(rebA.total_rebate ?? 0).toFixed(2)}`} />
                <Cell label="总积分" value={(rebA.total_points ?? 0).toFixed(0)} color="warning" />
                <Cell label="净PnL" value={`$${(rebA.net_pnl ?? 0).toFixed(2)}`} color={(rebA.net_pnl ?? 0) >= 0 ? "profit" : "loss"} />
              </div>
              {rebA.by_strategy && (
                <div className="mt-4">
                  <div className="text-xs text-muted-foreground mb-2">按策略分:</div>
                  <table className="data-table"><thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-1.5 px-2">策略</th><th className="text-right py-1.5 px-2">交易 <span className="text-cyan-300">▲</span></th><th className="text-right py-1.5 px-2">胜率 <span className="text-cyan-300">▲</span></th><th className="text-right py-1.5 px-2">PnL <span className="text-cyan-300">▲</span></th><th className="text-right py-1.5 px-2">积分 <span className="text-cyan-300">▲</span></th></tr></thead>
                  <tbody>{Object.entries(rebA.by_strategy).map(([s, v]: [string, any]) => (<tr key={s} className="border-b border-border/20"><td className="py-1.5 px-2 font-medium">{s}</td><td className="py-1.5 px-2 text-right tabular-nums num">{v.count ?? 0}</td><td className={cn("py-1.5 px-2 text-right tabular-nums num", (v.win_rate ?? 0) >= 0.5 ? "text-profit" : "text-loss")}>{((v.win_rate ?? 0) * 100).toFixed(1)}%</td><td className={cn("py-1.5 px-2 text-right tabular-nums num", (v.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>${(v.pnl ?? 0).toFixed(2)}</td><td className="py-1.5 px-2 text-right tabular-nums num text-warning">{(v.points ?? 0).toFixed(0)}</td></tr>))}</tbody>
                  <tfoot><tr><td className="px-3 py-1.5 text-muted-foreground text-xs">合计 {Object.keys(rebA.by_strategy).length} 策略</td><td className="text-right py-1.5 num text-muted-foreground">{Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.count ?? 0), 0)}</td><td /><td className={cn("text-right py-1.5 num font-bold", Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.pnl ?? 0), 0) >= 0 ? "text-profit" : "text-loss")}>${Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.pnl ?? 0), 0).toFixed(2)}</td><td className="text-right py-1.5 num text-warning">{Object.values(rebA.by_strategy).reduce((s: number, v: any) => s + (v.points ?? 0), 0).toFixed(0)}</td></tr></tfoot>
                  </table>
                </div>
              )}
            </Card>
          )}

          {/* ── 资金池 ── */}
          {tab === "capital" && (
            <div className="space-y-3">
              {rebCap.total_equity !== undefined && (
                <Card className="relative p-4 glass">
                  <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
                    <Coins className="w-3.5 h-3.5" />
                  </span>
                  <h2 className="text-sm font-medium mb-3 pr-8">返佣资金池</h2>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
                    <Cell label="总权益" value={`$${(rebCap.total_equity ?? 0).toFixed(2)}`} />
                    {(rebCap.allocations || {}) && Object.entries(rebCap.allocations).map(([k, v]: [string, any]) => (
                      <Cell key={k} label={`${k} 分配`} value={`$${(v || 0).toFixed(2)}`} />
                    ))}
                    {(rebCap.used || {}) && Object.entries(rebCap.used).map(([k, v]: [string, any]) => (
                      <Cell key={k} label={`${k} 已用`} value={`$${(v || 0).toFixed(2)}`} />
                    ))}
                  </div>
                </Card>
              )}
              {arbCap.total_pool_usd !== undefined && (
                <Card className="relative p-4 glass">
                  <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
                    <Wallet className="w-3.5 h-3.5" />
                  </span>
                  <h2 className="text-sm font-medium mb-3 pr-8">V3 资金池</h2>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                    <Cell label="总额" value={`$${(arbCap.total_pool_usd ?? 0).toFixed(2)}`} />
                    <Cell label="已分配" value={`$${(arbCap.allocated_usd ?? 0).toFixed(2)}`} />
                    <Cell label="可用" value={`$${(arbCap.available_usd ?? 0).toFixed(2)}`} />
                    <Cell label="使用率" value={`${((arbCap.utilization_pct ?? 0)).toFixed(1)}%`} />
                    <Cell label="日亏损限制" value={`${((arbCap.daily_loss_limit_pct ?? 0) * 100).toFixed(1)}%`} />
                    <Cell label="日已实现亏损" value={`$${(arbCap.daily_realized_loss ?? 0).toFixed(2)}`} />
                    <Cell label="最大权益占比" value={`${((arbCap.max_pool_pct_of_equity ?? 0) * 100).toFixed(0)}%`} />
                  </div>
                </Card>
              )}
            </div>
          )}

          {/* ── 积分/返佣 ── */}
          {tab === "rebate" && (
            <div className="space-y-3">
              {points.exchanges && (
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2">
                      <HeadIcon icon={Coins} />
                      <h2 className="text-sm font-medium">各交易所积分</h2>
                    </div>
                    <span className="text-xs text-muted-foreground">{Object.keys(points.exchanges).length} 交易所</span>
                  </div>
                  <table className="data-table">
                    <thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-2 px-2">交易所</th><th className="text-right py-2 px-2">积分 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">估值 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">PnL <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">持仓 <span className="text-cyan-300">▲</span></th><th className="text-left py-2 px-2">风险</th></tr></thead>
                    <tbody>
                      {Object.entries(points.exchanges).map(([ex, e]: [string, any]) => (
                        <tr key={ex} className="border-b border-border/30">
                          <td className="py-2 px-2 font-medium">{ex}</td>
                          <td className="py-2 px-2 text-right tabular-nums num text-warning">{(e.points_earned_total ?? 0).toFixed(0)}</td>
                          <td className="py-2 px-2 text-right tabular-nums num">${(e.estimated_value_usd ?? 0).toFixed(2)}</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums num", (e.pnl_from_positions ?? 0) >= 0 ? "text-profit" : "text-loss")}>${(e.pnl_from_positions ?? 0).toFixed(2)}</td>
                          <td className="py-2 px-2 text-right tabular-nums num">{e.position_count ?? 0}</td>
                          <td className="py-2 px-2"><Badge variant="secondary" className={cn("text-xs", e.risk_status === "healthy" ? "text-profit" : "text-warning")}>{e.risk_status || "—"}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr>
                        <td className="px-3 py-2 text-muted-foreground text-xs">合计 {Object.keys(points.exchanges).length} 交易所</td>
                        <td className="text-right py-2 num text-warning">{totalPoints.toFixed(0)}</td>
                        <td className="text-right py-2 num">${totalPointsUsd.toFixed(2)}</td>
                        <td className={cn("text-right py-2 num font-bold",
                          Object.values(points.exchanges).reduce((s: number, e: any) => s + ((e.pnl_from_positions ?? 0) || 0), 0) >= 0 ? "text-profit" : "text-loss")}>
                          ${Object.values(points.exchanges).reduce((s: number, e: any) => s + ((e.pnl_from_positions ?? 0) || 0), 0).toFixed(2)}
                        </td>
                        <td colSpan={2} />
                      </tr>
                    </tfoot>
                  </table>
                </Card>
              )}
              {data.incentives?.exchanges && (
                <Card className="p-4 glass">
                  <div className="flex items-center justify-between gap-2 mb-3">
                    <div className="flex items-center gap-2">
                      <HeadIcon icon={DollarSign} />
                      <h2 className="text-sm font-medium">交易所激励</h2>
                    </div>
                  </div>
                  <div className="space-y-1">
                    {Object.entries(data.incentives.exchanges).slice(0, 10).map(([ex, info]: [string, any]) => (
                      <div key={ex} className="flex items-center justify-between py-1.5 border-b border-border/20 text-xs">
                        <span className="font-medium">{ex}</span>
                        <div className="flex gap-3 text-muted-foreground">
                          {info.fee_tier && <span>费率: {info.fee_tier}</span>}
                          {info.rebate_rate && <span className="text-warning">返佣: {(info.rebate_rate * 100).toFixed(3)}%</span>}
                          {info.score && <span>评分: {info.score.toFixed(1)}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </div>
          )}

          {/* ── 配置 ── */}
          {tab === "config" && <ConfigTab />}

        </>
      )}
    </div>
  );
}

// ═══ 配置 Tab ═══
function ConfigTab() {
  const [config, setConfig] = useState<any>(null);
  const [strategies, setStrategies] = useState<any>(null);
  const [scheduler, setScheduler] = useState<any>(null);
  const [paperSession, setPaperSession] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [engineDraft, setEngineDraft] = useState<any>({});
  const [riskDraft, setRiskDraft] = useState<any>({});
  const [strategyDrafts, setStrategyDrafts] = useState<Record<string, any>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, strat, sched, ps] = await Promise.all([
        fetch(`${BACKEND}/api/rebate/config`).then(r => r.json()).catch(() => null),
        fetch(`${BACKEND}/api/rebate/config/strategies`).then(r => r.json()).catch(() => null),
        fetch(`${BACKEND}/api/rebate/rules/scheduler`).then(r => r.json()).catch(() => null),
        fetch(`${BACKEND}/api/arbitrage-paper/session`).then(r => r.json()).catch(() => null),
      ]);
      setConfig(cfg); setStrategies(strat); setScheduler(sched); setPaperSession(ps);
      setEngineDraft(cfg?.engine || {});
      setRiskDraft(cfg?.risk_gate || {});
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveEngine = async () => {
    setSaving("engine");
    try { await fetch(`${BACKEND}/api/rebate/config/engine`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(engineDraft) }); } catch {}
    setSaving(null);
  };
  const saveRisk = async () => {
    setSaving("risk");
    try { await fetch(`${BACKEND}/api/rebate/config/risk-gate`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(riskDraft) }); } catch {}
    setSaving(null);
  };
  const toggleStrategy = async (id: string, enabled: boolean) => {
    setSaving(`strat-${id}`);
    try {
      await fetch(`${BACKEND}/api/rebate/config/strategies/${id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      load();
    } catch {}
    setSaving(null);
  };
  const toggleScheduler = async () => {
    setSaving("sched");
    try {
      if (scheduler?.enabled) {
        await fetch(`${BACKEND}/api/rebate/rules/pause`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "manual" }) });
      } else {
        await fetch(`${BACKEND}/api/rebate/rules/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "manual", risk_acknowledged: true }) });
      }
      load();
    } catch {}
    setSaving(null);
  };

  if (loading) return <div className="flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>;

  const stratList = strategies?.strategies || {};

  return (
    <div className="space-y-3">
      {/* 引擎配置 */}
      {config?.engine && (
        <Card className="p-4 glass">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <HeadIcon icon={Settings2} />
              <h2 className="text-sm font-medium">引擎配置</h2>
            </div>
            <Button size="sm" variant="outline" className="btn-glow" onClick={saveEngine} disabled={saving === "engine"}>
              {saving === "engine" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存
            </Button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <ParamInput label="最小月度价值($)" value={engineDraft.min_monthly_value} onChange={(v) => setEngineDraft({ ...engineDraft, min_monthly_value: v })} />
            <ParamInput label="最大单仓($)" value={engineDraft.max_position_usd} onChange={(v) => setEngineDraft({ ...engineDraft, max_position_usd: v })} />
            <ParamInput label="7日最大成交($)" value={engineDraft.max_total_volume_7d} onChange={(v) => setEngineDraft({ ...engineDraft, max_total_volume_7d: v })} />
            <ParamInput label="最大持仓天数" value={engineDraft.max_holding_days} onChange={(v) => setEngineDraft({ ...engineDraft, max_holding_days: v })} />
          </div>
        </Card>
      )}

      {/* 风控配置 */}
      {config?.risk_gate && (
        <Card className="p-4 glass">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <HeadIcon icon={Shield} />
              <h2 className="text-sm font-medium">风控配置</h2>
            </div>
            <Button size="sm" variant="outline" className="btn-glow" onClick={saveRisk} disabled={saving === "risk"}>
              {saving === "risk" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存
            </Button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <ParamInput label="日成交上限/所($)" value={riskDraft.max_daily_volume_per_exchange} onChange={(v) => setRiskDraft({ ...riskDraft, max_daily_volume_per_exchange: v })} />
            <ParamInput label="周成交上限/所($)" value={riskDraft.max_weekly_volume_per_exchange} onChange={(v) => setRiskDraft({ ...riskDraft, max_weekly_volume_per_exchange: v })} />
            <ParamInput label="日亏损限制(%)" value={riskDraft.max_daily_loss_pct * 100} onChange={(v) => setRiskDraft({ ...riskDraft, max_daily_loss_pct: v / 100 })} suffix="%" />
          </div>
        </Card>
      )}

      {/* 已启用交易所 + 策略 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Card className="p-4 glass">
          <div className="flex items-center gap-2 mb-3">
            <HeadIcon icon={Wallet} />
            <h2 className="text-sm font-medium">已启用交易所</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {(config?.exchanges_enabled || []).map((ex: string) => (
              <Badge key={ex} variant="secondary" className="text-xs bg-profit/10 text-profit">{ex}</Badge>
            ))}
          </div>
        </Card>
        <Card className="p-4 glass">
          <div className="flex items-center gap-2 mb-3">
            <HeadIcon icon={Settings2} />
            <h2 className="text-sm font-medium">已启用策略</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {(config?.strategies_enabled || []).map((s: string) => (
              <Badge key={s} variant="secondary" className="text-xs bg-primary/10 text-primary">{s}</Badge>
            ))}
          </div>
        </Card>
      </div>

      {/* 策略列表 + 开关 */}
      <Card className="p-4 glass">
        <div className="flex items-center gap-2 mb-3">
          <HeadIcon icon={Activity} />
          <h2 className="text-sm font-medium">策略列表</h2>
        </div>
        <div className="space-y-2">
          {Object.entries(stratList).map(([id, s]: [string, any]) => (
            <div key={id} className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{id}</span>
                {s.enabled ? <Badge variant="secondary" className="text-xs bg-profit/20 text-profit">ON</Badge> : <Badge variant="secondary" className="text-xs">OFF</Badge>}
              </div>
              <button onClick={() => toggleStrategy(id, !s.enabled)} disabled={saving === `strat-${id}`}
                className={cn("relative w-10 h-5 rounded-full transition-colors", s.enabled ? "bg-primary" : "bg-muted")}>
                <span className={cn("absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform", s.enabled ? "left-5" : "left-0.5")} />
              </button>
            </div>
          ))}
        </div>
      </Card>

      {/* 规则同步调度 */}
      {scheduler && (
        <Card className="p-4 glass">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <HeadIcon icon={Clock} />
              <h2 className="text-sm font-medium">规则同步调度</h2>
            </div>
            <Button size="sm" variant="outline" onClick={toggleScheduler} disabled={saving === "sched"}>
              {scheduler.enabled ? "暂停" : "恢复"}
            </Button>
          </div>
          <div className="grid grid-cols-3 gap-3 text-xs">
            <Cell label="状态" value={scheduler.enabled ? "✅ 运行中" : "⏸ 已暂停"} color={scheduler.enabled ? "profit" : "warning"} />
            <Cell label="间隔" value={`${(scheduler.interval_seconds / 3600).toFixed(0)}h`} />
            <Cell label="下次运行" value={scheduler.next_run_time ? scheduler.next_run_time.slice(11, 19) : "—"} />
          </div>
        </Card>
      )}

      {/* Paper 会话 */}
      {paperSession?.running && (
        <Card className="p-4 glass border-profit/30">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <HeadIcon icon={CheckCircle2} />
              <h2 className="text-sm font-medium">Paper 验证运行中</h2>
            </div>
            <Badge variant="secondary" className="text-xs bg-profit/20 text-profit">账户 #{paperSession.account_id}</Badge>
          </div>
          <div className="grid grid-cols-3 gap-3 text-xs">
            <Cell label="策略" value={(paperSession.strategies || []).join(", ") || "—"} />
            <Cell label="交易员" value={paperSession.trader_profile?.account_name || "—"} />
            <Cell label="LLM" value={paperSession.trader_profile?.llm_config_name || "—"} />
          </div>
        </Card>
      )}

      {/* 当前模式 */}
      {config?.current_mode && (
        <Card className="p-4 glass">
          <div className="flex items-center justify-between gap-2 text-sm">
            <div className="flex items-center gap-2">
              <HeadIcon icon={Flame} />
              <span className="text-muted-foreground">当前模式</span>
            </div>
            <Badge variant="secondary" className={cn("text-xs", config.current_mode === "live" ? "bg-loss/20 text-loss" : "bg-warning/20 text-warning")}>
              {config.current_mode === "live" ? "实盘" : "模拟"}
            </Badge>
          </div>
        </Card>
      )}
    </div>
  );
}

function HeadIcon({ icon: Icon }: { icon: any }) {
  return (
    <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300 flex-shrink-0">
      <Icon className="w-3.5 h-3.5" />
    </span>
  );
}

function Kv({ k, v, color }: { k: string; v: string; color?: "profit" | "loss" | "warning" }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("font-medium tabular-nums text-right", color === "profit" && "text-profit", color === "loss" && "text-loss", color === "warning" && "text-warning")}>{v}</span>
    </div>
  );
}

function ParamInput({ label, value, onChange, suffix }: { label: string; value: any; onChange: (v: number) => void; suffix?: string }) {
  return (
    <div>
      <label className="text-xs text-muted-foreground block mb-0.5">{label}</label>
      <div className="flex items-center gap-1">
        <input type="number" value={value ?? 0} onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          className="w-full bg-card border border-border text-xs rounded px-2 py-1 tabular-nums" />
        {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
      </div>
    </div>
  );
}

function KpiTile({ label, value, subValue, color, icon: Icon, grad }: { label: string; value: string; subValue?: string; color?: string; icon: any; grad?: boolean }) {
  return (
    <Card className="relative p-3 glass">
      <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
        <Icon className="w-3.5 h-3.5" />
      </span>
      <div className="text-xs text-muted-foreground truncate pr-8">{label}</div>
      <div className={cn(
        "text-lg font-bold font-mono tabular-nums tracking-tight leading-tight",
        grad
          ? color === "profit" ? "grad-text-green" : color === "loss" ? "grad-text-red" : "grad-text"
          : color === "profit" ? "text-profit" : color === "loss" ? "text-loss" : color === "warning" ? "text-warning" : ""
      )}>{value}</div>
      {subValue && <div className="text-xs text-muted-foreground">{subValue}</div>}
    </Card>
  );
}

function Cell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (<div className="p-2 rounded bg-muted/30"><div className="text-muted-foreground text-xs">{label}</div><div className={cn("font-bold tabular-nums", color === "profit" && "text-profit", color === "loss" && "text-loss", color === "warning" && "text-warning")}>{value}</div></div>);
}

function Empty() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/25 flex items-center justify-center">
        <span className="text-cyan-300 text-base leading-none">α</span>
      </div>
      <div className="text-sm text-muted-foreground">暂无数据</div>
    </div>
  );
}

function PositionTable({ positions }: { positions: any[] }) {
  return (
    <table className="data-table">
      <thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-2 px-2">币种</th><th className="text-left py-2 px-2">策略</th><th className="text-left py-2 px-2">交易所</th><th className="text-right py-2 px-2">方向</th><th className="text-right py-2 px-2">数量 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">PnL <span className="text-cyan-300">▲</span></th><th className="text-left py-2 px-2">状态</th></tr></thead>
      <tbody>
        {positions.map((p, i) => (
          <tr key={p.id || i} className="border-b border-border/30 hover:bg-muted/20">
            <td className="py-2 px-2 font-medium">{p.symbol || "—"}</td>
            <td className="py-2 px-2 text-muted-foreground">{p.strategy_type || p.strategy || "—"}</td>
            <td className="py-2 px-2 text-muted-foreground">{p.exchange || "—"}</td>
            <td className="py-2 px-2 text-right">{p.side || p.direction || "—"}</td>
            <td className="py-2 px-2 text-right tabular-nums num">{(p.quantity || p.size || 0).toFixed(4)}</td>
            <td className={cn("py-2 px-2 text-right tabular-nums num", (p.pnl || 0) >= 0 ? "text-profit" : "text-loss")}>{(p.pnl || 0) >= 0 ? "+" : ""}${(p.pnl || 0).toFixed(2)}</td>
            <td className="py-2 px-2"><Badge variant="secondary" className="text-xs">{p.status || "active"}</Badge></td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td className="px-3 py-2 text-muted-foreground text-xs">合计 {positions.length} 笔</td>
          <td colSpan={4} />
          <td className={cn("text-right py-2 num font-bold",
            positions.reduce((s: number, p: any) => s + (p.pnl || 0), 0) >= 0 ? "text-profit" : "text-loss")}>
            {positions.reduce((s: number, p: any) => s + (p.pnl || 0), 0) >= 0 ? "+" : ""}${positions.reduce((s: number, p: any) => s + (p.pnl || 0), 0).toFixed(2)}
          </td>
          <td />
        </tr>
      </tfoot>
    </table>
  );
}
