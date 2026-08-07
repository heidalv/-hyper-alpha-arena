"use client";

import { useEffect, useState, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
    <div className="p-4 space-y-4 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold flex items-center gap-2"><ArrowRightLeft className="w-5 h-5 text-primary" />套利中心</h1>
        <div className="flex items-center gap-2">
          {/* V3 引擎灯 */}
          <Badge variant="secondary" className={cn("text-[9px]", arb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>V3 {arb.engine_enabled ? "ON" : "OFF"}</Badge>
          {/* Rebate 引擎灯 */}
          <Badge variant="secondary" className={cn("text-[9px]", reb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>Rebate {reb.engine_enabled ? "ON" : "OFF"}</Badge>
          {/* 规则闸门 */}
          {gate.rebate_pause && <Badge variant="destructive" className="text-[9px]">规则暂停</Badge>}
          {/* 刷新 */}
          <Button variant="ghost" size="sm" onClick={load} disabled={loading}><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} /></Button>
        </div>
      </div>

      {/* KPI Strip */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
        <KpiTile label="V3持仓" value={String(arb.active_positions ?? 0)} icon={Activity} />
        <KpiTile label="Rebate持仓" value={String(reb.active_positions ?? 0)} icon={TrendingUp} />
        <KpiTile label="净收益" value={`${netPnl >= 0 ? "+" : ""}$${netPnl.toFixed(2)}`} color={netPnl >= 0 ? "profit" : "loss"} icon={DollarSign} />
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
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* V3 引擎 */}
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-medium">V3 统计套利引擎</h2>
                    <Badge variant="secondary" className={cn("text-[9px]", arb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted")}>{arb.mode || "paper"}</Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <Cell label="Tick" value={String(arb.tick_count ?? 0)} />
                    <Cell label="持仓" value={String(arb.active_positions ?? 0)} />
                    <Cell label="扫描" value={String(arb.scanner_scan_count ?? 0)} />
                    <Cell label="缓存机会" value={String(arb.cached_opportunities ?? 0)} />
                    <Cell label="熔断" value={arb.circuit_breaker_active ? "⚠️ 触发" : "正常"} color={arb.circuit_breaker_active ? "loss" : "profit"} />
                    <Cell label="模式" value={arb.mode || "—"} />
                  </div>
                </Card>

                {/* Rebate 引擎 */}
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-medium flex items-center gap-2"><DollarSign className="w-4 h-4 text-warning" />返佣/积分引擎</h2>
                    <Badge variant="secondary" className={cn("text-[9px]", reb.engine_enabled ? "bg-profit/20 text-profit" : "bg-muted")}>{reb.mode || "paper"}</Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <Cell label="扫描" value={String(reb.scan_count ?? rebA.scan_count ?? 0)} />
                    <Cell label="执行" value={String(reb.execution_count ?? rebA.execution_count ?? 0)} />
                    <Cell label="持仓" value={String(reb.active_positions ?? 0)} />
                    <Cell label="总交易" value={String(rebA.total_trades ?? 0)} />
                    <Cell label="胜率" value={`${((rebA.win_rate ?? 0) * 100).toFixed(1)}%`} color={(rebA.win_rate ?? 0) >= 0.5 ? "profit" : "loss"} />
                    <Cell label="PnL" value={`${(rebA.total_pnl ?? 0) >= 0 ? "+" : ""}$${(rebA.total_pnl ?? 0).toFixed(2)}`} color={(rebA.total_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                    <Cell label="返佣" value={`$${(rebA.total_rebate ?? 0).toFixed(2)}`} />
                    <Cell label="积分" value={(rebA.total_points ?? 0).toFixed(0)} />
                    <Cell label="净PnL" value={`${(rebA.net_pnl ?? 0) >= 0 ? "+" : ""}$${(rebA.net_pnl ?? 0).toFixed(2)}`} color={(rebA.net_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                  </div>
                </Card>
              </div>

              {/* 刷量安全 + 规则闸门 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3 flex items-center gap-2"><Shield className="w-4 h-4 text-primary" />刷量安全</h2>
                  <div className="grid grid-cols-3 gap-2 text-xs">
                    <Cell label="安全状态" value={wash.is_safe ? "✅ 安全" : "⚠️ 警告"} color={wash.is_safe ? "profit" : "warning"} />
                    <Cell label="风险等级" value={wash.risk_level || "—"} />
                    <Cell label="今日交易数" value={String(wash.trade_count_today ?? 0)} />
                    <Cell label="今日成交额" value={`$${(wash.daily_volume_usd ?? 0).toFixed(0)}`} />
                    <Cell label="安全间隔" value={`${(wash.next_safe_interval_sec ?? 0).toFixed(0)}s`} />
                  </div>
                </Card>
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3 flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-warning" />规则闸门</h2>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between"><span className="text-muted-foreground">Rebate 暂停</span><span className={gate.rebate_pause ? "text-loss" : "text-profit"}>{gate.rebate_pause ? "是" : "否"}</span></div>
                    <div className="flex justify-between"><span className="text-muted-foreground">V3 暂停</span><span className={gate.v3_pause ? "text-loss" : "text-profit"}>{gate.v3_pause ? "是" : "否"}</span></div>
                    <div className="flex justify-between"><span className="text-muted-foreground">暂停策略数</span><span>{gate.paused_strategies?.length ?? 0}</span></div>
                    {gate.pause_reason && <div className="text-loss">原因: {gate.pause_reason}</div>}
                  </div>
                </Card>
              </div>

              {/* 策略分析 */}
              {rebA.by_strategy && Object.keys(rebA.by_strategy).length > 0 && (
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3">策略分析</h2>
                  <table className="w-full text-xs">
                    <thead><tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-2 px-2">策略</th><th className="text-right py-2 px-2">交易数</th>
                      <th className="text-right py-2 px-2">胜率</th><th className="text-right py-2 px-2">PnL</th>
                      <th className="text-right py-2 px-2">积分</th>
                    </tr></thead>
                    <tbody>
                      {Object.entries(rebA.by_strategy).map(([s, v]: [string, any]) => (
                        <tr key={s} className="border-b border-border/30">
                          <td className="py-2 px-2 font-medium">{s}</td>
                          <td className="py-2 px-2 text-right tabular-nums">{v.count ?? 0}</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums", (v.win_rate ?? 0) >= 0.5 ? "text-profit" : "text-loss")}>{((v.win_rate ?? 0) * 100).toFixed(1)}%</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums", (v.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>{(v.pnl ?? 0) >= 0 ? "+" : ""}${(v.pnl ?? 0).toFixed(2)}</td>
                          <td className="py-2 px-2 text-right tabular-nums text-warning">{(v.points ?? 0).toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </div>
          )}

          {/* ── 持仓 ── */}
          {tab === "positions" && (
            <div className="space-y-3">
              <Card className="p-4">
                <h2 className="text-sm font-medium mb-3">V3 套利持仓 ({arbPos.length})</h2>
                {arbPos.length === 0 ? <Empty /> : <PositionTable positions={arbPos} />}
              </Card>
              <Card className="p-4">
                <h2 className="text-sm font-medium mb-3">返佣持仓 ({rebPos.length})</h2>
                {rebPos.length === 0 ? <Empty /> : <PositionTable positions={rebPos} />}
              </Card>
            </div>
          )}

          {/* ── 机会 ── */}
          {tab === "opportunities" && (
            <div className="space-y-3">
              <Card className="p-4">
                <h2 className="text-sm font-medium mb-3">套利机会 ({arbOpps.length})</h2>
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
              <Card className="p-4">
                <h2 className="text-sm font-medium mb-3">返佣机会 ({rebOpps.length})</h2>
                {rebOpps.length === 0 ? <Empty /> : (
                  <table className="w-full text-xs"><thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-1.5 px-2">币种</th><th className="text-left py-1.5 px-2">交易所</th><th className="text-right py-1.5 px-2">返佣</th><th className="text-right py-1.5 px-2">分数</th></tr></thead>
                  <tbody>{rebOpps.slice(0, 30).map((o: any, i: number) => (<tr key={i} className="border-b border-border/20"><td className="py-1.5 px-2 font-medium">{o.symbol || "—"}</td><td className="py-1.5 px-2 text-muted-foreground">{o.exchange || "—"}</td><td className="py-1.5 px-2 text-right tabular-nums text-warning">${(o.expected_rebate_usd || o.rebate_usd || 0).toFixed(4)}</td><td className="py-1.5 px-2 text-right tabular-nums">{o.score?.toFixed(1) ?? "—"}</td></tr>))}</tbody>
                  </table>
                )}
              </Card>
            </div>
          )}

          {/* ── 分析 ── */}
          {tab === "analytics" && rebA.engine_mode !== undefined && (
            <Card className="p-4">
              <h2 className="text-sm font-medium mb-3">返佣引擎分析</h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <Cell label="引擎模式" value={rebA.engine_mode || "—"} />
                <Cell label="扫描次数" value={String(rebA.scan_count ?? 0)} />
                <Cell label="执行次数" value={String(rebA.execution_count ?? 0)} />
                <Cell label="总交易" value={String(rebA.total_trades ?? 0)} />
                <Cell label="胜率" value={`${((rebA.win_rate ?? 0) * 100).toFixed(1)}%`} color={(rebA.win_rate ?? 0) >= 0.5 ? "profit" : "loss"} />
                <Cell label="总PnL" value={`$${(rebA.total_pnl ?? 0).toFixed(2)}`} color={(rebA.total_pnl ?? 0) >= 0 ? "profit" : "loss"} />
                <Cell label="总返佣" value={`$${(rebA.total_rebate ?? 0).toFixed(2)}`} />
                <Cell label="总积分" value={(rebA.total_points ?? 0).toFixed(0)} />
                <Cell label="净PnL" value={`$${(rebA.net_pnl ?? 0).toFixed(2)}`} color={(rebA.net_pnl ?? 0) >= 0 ? "profit" : "loss"} />
              </div>
              {rebA.by_strategy && (
                <div className="mt-4">
                  <div className="text-xs text-muted-foreground mb-2">按策略分:</div>
                  <table className="w-full text-xs"><thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-1.5 px-2">策略</th><th className="text-right py-1.5 px-2">交易</th><th className="text-right py-1.5 px-2">胜率</th><th className="text-right py-1.5 px-2">PnL</th><th className="text-right py-1.5 px-2">积分</th></tr></thead>
                  <tbody>{Object.entries(rebA.by_strategy).map(([s, v]: [string, any]) => (<tr key={s} className="border-b border-border/20"><td className="py-1.5 px-2 font-medium">{s}</td><td className="py-1.5 px-2 text-right tabular-nums">{v.count ?? 0}</td><td className={cn("py-1.5 px-2 text-right tabular-nums", (v.win_rate ?? 0) >= 0.5 ? "text-profit" : "text-loss")}>{((v.win_rate ?? 0) * 100).toFixed(1)}%</td><td className={cn("py-1.5 px-2 text-right tabular-nums", (v.pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>${(v.pnl ?? 0).toFixed(2)}</td><td className="py-1.5 px-2 text-right tabular-nums text-warning">{(v.points ?? 0).toFixed(0)}</td></tr>))}</tbody>
                  </table>
                </div>
              )}
            </Card>
          )}

          {/* ── 资金池 ── */}
          {tab === "capital" && (
            <div className="space-y-3">
              {rebCap.total_equity !== undefined && (
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3">返佣资金池</h2>
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
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3">V3 资金池</h2>
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
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3">各交易所积分</h2>
                  <table className="w-full text-xs">
                    <thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-2 px-2">交易所</th><th className="text-right py-2 px-2">积分</th><th className="text-right py-2 px-2">估值</th><th className="text-right py-2 px-2">PnL</th><th className="text-right py-2 px-2">持仓</th><th className="text-left py-2 px-2">风险</th></tr></thead>
                    <tbody>
                      {Object.entries(points.exchanges).map(([ex, e]: [string, any]) => (
                        <tr key={ex} className="border-b border-border/30">
                          <td className="py-2 px-2 font-medium">{ex}</td>
                          <td className="py-2 px-2 text-right tabular-nums text-warning">{(e.points_earned_total ?? 0).toFixed(0)}</td>
                          <td className="py-2 px-2 text-right tabular-nums">${(e.estimated_value_usd ?? 0).toFixed(2)}</td>
                          <td className={cn("py-2 px-2 text-right tabular-nums", (e.pnl_from_positions ?? 0) >= 0 ? "text-profit" : "text-loss")}>${(e.pnl_from_positions ?? 0).toFixed(2)}</td>
                          <td className="py-2 px-2 text-right tabular-nums">{e.position_count ?? 0}</td>
                          <td className="py-2 px-2"><Badge variant="secondary" className={cn("text-[9px]", e.risk_status === "healthy" ? "text-profit" : "text-warning")}>{e.risk_status || "—"}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
              {data.incentives?.exchanges && (
                <Card className="p-4">
                  <h2 className="text-sm font-medium mb-3">交易所激励</h2>
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
      await fetch(`/api/rebate/config/strategies/${id}`, {
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
        <Card className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium">引擎配置</h2>
            <Button size="sm" variant="outline" onClick={saveEngine} disabled={saving === "engine"}>
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
        <Card className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium">风控配置</h2>
            <Button size="sm" variant="outline" onClick={saveRisk} disabled={saving === "risk"}>
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
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">已启用交易所</h2>
          <div className="flex flex-wrap gap-2">
            {(config?.exchanges_enabled || []).map((ex: string) => (
              <Badge key={ex} variant="secondary" className="text-[10px] bg-profit/10 text-profit">{ex}</Badge>
            ))}
          </div>
        </Card>
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">已启用策略</h2>
          <div className="flex flex-wrap gap-2">
            {(config?.strategies_enabled || []).map((s: string) => (
              <Badge key={s} variant="secondary" className="text-[10px] bg-primary/10 text-primary">{s}</Badge>
            ))}
          </div>
        </Card>
      </div>

      {/* 策略列表 + 开关 */}
      <Card className="p-4">
        <h2 className="text-sm font-medium mb-3">策略列表</h2>
        <div className="space-y-2">
          {Object.entries(stratList).map(([id, s]: [string, any]) => (
            <div key={id} className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{id}</span>
                {s.enabled ? <Badge variant="secondary" className="text-[9px] bg-profit/20 text-profit">ON</Badge> : <Badge variant="secondary" className="text-[9px]">OFF</Badge>}
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
        <Card className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium">规则同步调度</h2>
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
        <Card className="p-4 border-profit/30">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-profit" />Paper 验证运行中</h2>
            <Badge variant="secondary" className="text-[9px] bg-profit/20 text-profit">账户 #{paperSession.account_id}</Badge>
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
        <Card className="p-4">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">当前模式</span>
            <Badge variant="secondary" className={cn("text-[9px]", config.current_mode === "live" ? "bg-loss/20 text-loss" : "bg-warning/20 text-warning")}>
              {config.current_mode === "live" ? "实盘" : "模拟"}
            </Badge>
          </div>
        </Card>
      )}
    </div>
  );
}

function ParamInput({ label, value, onChange, suffix }: { label: string; value: any; onChange: (v: number) => void; suffix?: string }) {
  return (
    <div>
      <label className="text-[10px] text-muted-foreground block mb-0.5">{label}</label>
      <div className="flex items-center gap-1">
        <input type="number" value={value ?? 0} onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          className="w-full bg-card border border-border text-xs rounded px-2 py-1 tabular-nums" />
        {suffix && <span className="text-[10px] text-muted-foreground">{suffix}</span>}
      </div>
    </div>
  );
}

function KpiTile({ label, value, subValue, color, icon: Icon }: { label: string; value: string; subValue?: string; color?: string; icon: any }) {
  return (
    <Card className="p-2">
      <div className="flex items-center justify-between mb-0.5"><span className="text-[10px] text-muted-foreground truncate">{label}</span><Icon className="w-3 h-3 text-muted-foreground flex-shrink-0" /></div>
      <div className={cn("text-base font-bold tabular-nums", color && `text-${color}`)}>{value}</div>
      {subValue && <div className="text-[9px] text-muted-foreground">{subValue}</div>}
    </Card>
  );
}

function Cell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (<div className="p-1.5 rounded bg-muted/30"><div className="text-muted-foreground text-[10px]">{label}</div><div className={cn("font-bold tabular-nums", color && `text-${color}`)}>{value}</div></div>);
}

function Empty() { return <div className="text-center py-6 text-muted-foreground text-sm">暂无数据</div>; }

function PositionTable({ positions }: { positions: any[] }) {
  return (
    <table className="w-full text-xs">
      <thead><tr className="text-muted-foreground border-b border-border"><th className="text-left py-2 px-2">币种</th><th className="text-left py-2 px-2">策略</th><th className="text-left py-2 px-2">交易所</th><th className="text-right py-2 px-2">方向</th><th className="text-right py-2 px-2">数量</th><th className="text-right py-2 px-2">PnL</th><th className="text-left py-2 px-2">状态</th></tr></thead>
      <tbody>
        {positions.map((p, i) => (
          <tr key={p.id || i} className="border-b border-border/30 hover:bg-muted/20">
            <td className="py-2 px-2 font-medium">{p.symbol || "—"}</td>
            <td className="py-2 px-2 text-muted-foreground">{p.strategy_type || p.strategy || "—"}</td>
            <td className="py-2 px-2 text-muted-foreground">{p.exchange || "—"}</td>
            <td className="py-2 px-2 text-right">{p.side || p.direction || "—"}</td>
            <td className="py-2 px-2 text-right tabular-nums">{(p.quantity || p.size || 0).toFixed(4)}</td>
            <td className={cn("py-2 px-2 text-right tabular-nums", (p.pnl || 0) >= 0 ? "text-profit" : "text-loss")}>{(p.pnl || 0) >= 0 ? "+" : ""}${(p.pnl || 0).toFixed(2)}</td>
            <td className="py-2 px-2"><Badge variant="secondary" className="text-[9px]">{p.status || "active"}</Badge></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
