"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { Shield, Activity, Zap, TrendingDown, RefreshCw, Loader2, Bell, Layers } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
const BACKEND = getBackendUrl().replace(/\/$/, "");

export default function RiskPage() {
  const { data: riskStatus, refetch: refetchStatus, isLoading: loadingStatus } = useQuery({
    queryKey: ["risk-status"],
    queryFn: () => api.getHealth().then(() => fetch(`${BACKEND}/api/risk/status`).then(r => r.json())),
    staleTime: 15_000, refetchInterval: 30_000,
  });

  const { data: liqRisks, isLoading: loadingLiq } = useQuery({
    queryKey: ["risk-liquidation"],
    queryFn: () => fetch(`${BACKEND}/api/risk/liquidation-risks`).then(r => r.json()),
    staleTime: 15_000, refetchInterval: 30_000,
  });

  const { data: pdStatus, isLoading: loadingPd } = useQuery({
    queryKey: ["risk-pd"],
    queryFn: () => fetch(`${BACKEND}/api/risk/profit-drawdown/status`).then(r => r.json()),
    staleTime: 15_000, refetchInterval: 30_000,
  });

  const { data: alertHistory } = useQuery({
    queryKey: ["risk-alerts"],
    queryFn: () => fetch(`${BACKEND}/api/risk/alert-history?limit=20`).then(r => r.json()),
    staleTime: 30_000, refetchInterval: 60_000,
  });

  const mon = riskStatus?.liquidation_monitor;
  const cfg = riskStatus?.risk_config;
  const summaries = liqRisks?.summaries ?? [];
  const risks = liqRisks?.risks ?? [];
  const pdPositions = pdStatus?.positions ?? [];
  const alerts = alertHistory?.alerts ?? [];

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Shield className="w-4 h-4" />}
        title="风控监控"
        subtitle="多账户统一风控 · 熔断保护"
        refreshHint="15s 巡检"
        breadcrumb={[{ label: "系统" }, { label: "风控监控" }]}
        badge={
          <span className="chip-capsule">
            <span className={cn("w-1.5 h-1.5 rounded-full", mon?.running ? "bg-profit" : "bg-loss")} />
            {mon?.running ? "运行中" : "已停止"}
          </span>
        }
        actions={
          <Button variant="ghost" size="sm" onClick={() => { refetchStatus(); }}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        }
      />

      {/* 监控状态 KPI */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="爆仓监控" value={mon?.running ? "运行中" : "停止"} color={mon?.running ? "profit" : "loss"} icon={Activity} />
        <KpiCard label="监控持仓" value={String(mon?.positions_monitored ?? 0)} icon={Layers} />
        <KpiCard label="告警总数" value={String(mon?.alerts_total ?? 0)} color={mon?.alerts_total > 0 ? "warning" : undefined} icon={Bell} />
        <KpiCard label="今日告警" value={String(alertHistory?.today_count ?? 0)} color={alertHistory?.today_count > 0 ? "warning" : undefined} icon={Zap} />
      </div>

      {/* 账户风险摘要 */}
      {summaries.length > 0 && (
        <Card className="glass p-4">
          <CardHead icon={Layers} title="账户风险摘要" hint={`共 ${summaries.length} 账户`} />
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-2 px-2">账户</th>
                <th className="text-right py-2 px-2">权益 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">日亏损率 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">保证金使用 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">日交易 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">连亏 <span className="text-cyan-300">▲</span></th>
                <th className="text-center py-2 px-2">熔断</th>
              </tr></thead>
              <tbody>
                {summaries.map((s: any) => (
                  <tr key={s.account_id} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-2 font-medium">#{s.account_id}</td>
                    <td className="py-2 px-2 text-right num tabular-nums">${(s.total_equity || 0).toFixed(2)}</td>
                    <td className={cn("py-2 px-2 text-right num tabular-nums", s.daily_loss_ratio > 0.03 ? "text-loss" : "text-muted-foreground")}>
                      {(s.daily_loss_ratio * 100).toFixed(2)}%
                    </td>
                    <td className="py-2 px-2 text-right num tabular-nums">{(s.margin_usage_percent * 100).toFixed(1)}%</td>
                    <td className="py-2 px-2 text-right num tabular-nums">{s.daily_trades}</td>
                    <td className={cn("py-2 px-2 text-right num tabular-nums", s.consecutive_losses >= 3 ? "text-warning" : "")}>{s.consecutive_losses}</td>
                    <td className="py-2 px-2 text-center">
                      {s.is_circuit_breaker_active ? <Badge variant="destructive">熔断中</Badge> : <span className="text-muted-foreground">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border/50 bg-muted/20">
                  <td colSpan={5} className="px-2 py-2 text-xs text-muted-foreground">
                    合计 <span className="num font-semibold text-foreground">{summaries.length}</span> 账户
                  </td>
                  <td colSpan={2} className="px-2 py-2 text-center text-xs text-muted-foreground">
                    熔断{" "}
                    <span className={cn("num font-semibold", summaries.filter((s: any) => s.is_circuit_breaker_active).length > 0 ? "text-warning" : "text-foreground")}>
                      {summaries.filter((s: any) => s.is_circuit_breaker_active).length}
                    </span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}

      {/* 持仓风险分布 + 风控参数 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {mon?.risk_counts && (
          <Card className="glass p-4 lg:col-span-2">
            <CardHead icon={TrendingDown} title="持仓风险分布" hint="按风险等级" />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {(["safe", "warning", "danger", "critical"] as const).map(level => {
                const count = mon.risk_counts[level] ?? 0;
                const color = { safe: "text-profit", warning: "text-warning", danger: "text-loss", critical: "text-loss" }[level];
                const bg = { safe: "bg-profit/10 border-profit/20", warning: "bg-warning/10 border-warning/20", danger: "bg-loss/10 border-loss/20", critical: "bg-loss/20 border-loss/30" }[level];
                const label = { safe: "安全", warning: "预警", danger: "危险", critical: "严重" }[level];
                return (
                  <div key={level} className={cn("p-3 rounded-lg border text-center", bg)}>
                    <div className={cn("text-2xl font-bold tabular-nums", color)}>{count}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
                  </div>
                );
              })}
            </div>
          </Card>
        )}
        {cfg && (
          <Card className="glass p-4">
            <CardHead icon={Shield} title="风控参数" hint="全局生效" />
            <div className="text-xs">
              <ConfigItem label="最大日交易数" value={cfg.max_daily_trades} limit={cfg.max_daily_trades} />
              <ConfigItem label="单笔仓位上限" value={`${(cfg.max_position_per_trade_ratio * 100).toFixed(0)}%`} />
              <ConfigItem label="最大杠杆" value={`${cfg.max_leverage}x`} />
              <ConfigItem label="日亏损限制" value={`${(cfg.daily_loss_limit_ratio * 100).toFixed(1)}%`} />
              <ConfigItem label="连亏减仓阈值" value={cfg.consecutive_loss_reduce_threshold} />
              <ConfigItem label="连亏暂停阈值" value={cfg.consecutive_loss_pause_threshold} />
              <ConfigItem label="单币上限" value={`${(cfg.max_single_symbol_ratio * 100).toFixed(0)}%`} />
            </div>
          </Card>
        )}
      </div>

      {/* 盈亏回撤保护 + 告警记录 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {pdPositions.length > 0 && (
          <Card className="glass p-4 lg:col-span-2">
            <CardHead icon={Shield} title="盈亏回撤保护" hint={`保护价缓冲 · ${pdPositions.length} 持仓`} />
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead><tr>
                  <th className="text-left">币种</th>
                  <th className="text-right">入场</th>
                  <th className="text-right">现价</th>
                  <th className="text-right">未实现盈亏</th>
                  <th className="text-center">保护状态</th>
                </tr></thead>
                <tbody>
                  {pdPositions.map((p: any) => (
                    <tr key={p.position_id} className="border-b border-border/30 hover:bg-muted/20">
                      <td className="py-2 px-2">
                        <span className="font-medium">{p.symbol}</span>
                        <span className={cn("ml-2 text-xs px-1.5 py-0.5 rounded", p.side === "long" ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>{p.side}</span>
                        {p.nature && <span className="ml-1.5 text-xs text-muted-foreground">{p.nature}</span>}
                      </td>
                      <td className="py-2 px-2 text-right num tabular-nums">${p.entry_price?.toLocaleString()}</td>
                      <td className="py-2 px-2 text-right num tabular-nums">${p.current_price?.toLocaleString()}</td>
                      <td className={cn("py-2 px-2 text-right num tabular-nums font-medium", p.current_upnl >= 0 ? "text-profit" : "text-loss")}>
                        {(p.current_upnl || 0) >= 0 ? "+" : ""}${(p.current_upnl || 0).toFixed(2)}
                      </td>
                      <td className="py-2 px-2 text-center">
                        {p.protection_active ? <Badge variant="destructive">保护触发</Badge> : p.protection_would_trigger ? <Badge className="bg-warning/20 text-warning">即将触发</Badge> : <span className="text-xs text-muted-foreground">正常</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Card className="glass p-4">
          <CardHead icon={Bell} title="告警记录" hint={alertHistory?.today_count ? `今日 ${alertHistory.today_count} 条` : undefined} />
          {alerts.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
              <Bell className="w-5 h-5 mx-auto mb-2 opacity-40" />
              暂无告警记录
            </div>
          ) : (
            <div className="space-y-1 max-h-72 overflow-y-auto">
              {alerts.map((a: any, i: number) => (
                <div key={a.id || i} className="flex items-start justify-between gap-2 py-2 border-b border-border/20 last:border-0 text-xs">
                  <div className="flex items-start gap-2 min-w-0">
                    <span className={cn("mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0",
                      a.severity === "critical" ? "bg-loss" : a.severity === "warning" ? "bg-warning" : "bg-primary")} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium">{a.symbol || "—"}</span>
                        <span className="text-muted-foreground">{a.type || a.alert_type || "—"}</span>
                      </div>
                      <div className="text-muted-foreground truncate">{a.message?.slice(0, 60) || "—"}</div>
                    </div>
                  </div>
                  <span className="font-mono text-xs text-muted-foreground flex-shrink-0 tabular-nums">
                    {a.created_at ? new Date(a.created_at).toLocaleString("zh-CN", { hour12: false }) : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function CardHead({ icon: Icon, title, hint, badge }: { icon: any; title: string; hint?: string; badge?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2 min-w-0">
        <span className="w-6 h-6 rounded-md bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300 flex-shrink-0">
          <Icon className="w-3.5 h-3.5" />
        </span>
        <h2 className="text-sm font-semibold truncate">{title}</h2>
        {badge}
      </div>
      {hint && <span className="text-xs text-muted-foreground flex-shrink-0">{hint}</span>}
    </div>
  );
}

function KpiCard({ label, value, color, icon: Icon }: { label: string; value: string; color?: string; icon: any }) {
  const valueClass =
    color === "profit" ? "grad-text-green" :
    color === "loss" ? "grad-text-red" :
    color === "warning" ? "text-warning" :
    "grad-text";
  return (
    <Card className="relative p-3 glass">
      <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
        <Icon className="w-3.5 h-3.5" />
      </span>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-xl font-bold tabular-nums", valueClass)}>{value}</div>
    </Card>
  );
}

function ConfigItem({ label, value }: { label: string; value: any; limit?: number }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs font-semibold tabular-nums">{value}</span>
    </div>
  );
}
