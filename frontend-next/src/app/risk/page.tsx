"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Shield, AlertTriangle, Activity, Zap, TrendingDown, RefreshCw, Loader2 } from "lucide-react";
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
    <div className="p-4 space-y-4 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold flex items-center gap-2"><Shield className="w-5 h-5 text-primary" />风控监控</h1>
        <Button variant="ghost" size="sm" onClick={() => { refetchStatus(); }}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>

      {/* 监控状态 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="爆仓监控" value={mon?.running ? "运行中" : "停止"} color={mon?.running ? "profit" : "loss"} icon={Activity} />
        <KpiCard label="监控持仓" value={String(mon?.positions_monitored ?? 0)} icon={Shield} />
        <KpiCard label="告警总数" value={String(mon?.alerts_total ?? 0)} color={mon?.alerts_total > 0 ? "warning" : undefined} icon={AlertTriangle} />
        <KpiCard label="今日告警" value={String(alertHistory?.today_count ?? 0)} color={alertHistory?.today_count > 0 ? "warning" : undefined} icon={Zap} />
      </div>

      {/* 风险等级分布 */}
      {mon?.risk_counts && (
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">持仓风险分布</h2>
          <div className="grid grid-cols-4 gap-3">
            {(["safe", "warning", "danger", "critical"] as const).map(level => {
              const count = mon.risk_counts[level] ?? 0;
              const color = { safe: "text-profit", warning: "text-warning", danger: "text-loss", critical: "text-loss" }[level];
              const bg = { safe: "bg-profit/10", warning: "bg-warning/10", danger: "bg-loss/10", critical: "bg-loss/20" }[level];
              return (
                <div key={level} className={cn("p-3 rounded text-center", bg)}>
                  <div className={cn("text-2xl font-bold tabular-nums", color)}>{count}</div>
                  <div className="text-xs text-muted-foreground">{level}</div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* 风控配置 */}
      {cfg && (
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">风控参数</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
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

      {/* 账户风险摘要 */}
      {summaries.length > 0 && (
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">账户风险摘要</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-2 px-2">账户</th>
                <th className="text-right py-2 px-2">权益</th>
                <th className="text-right py-2 px-2">日亏损率</th>
                <th className="text-right py-2 px-2">保证金使用</th>
                <th className="text-right py-2 px-2">日交易</th>
                <th className="text-right py-2 px-2">连亏</th>
                <th className="text-center py-2 px-2">熔断</th>
              </tr></thead>
              <tbody>
                {summaries.map((s: any) => (
                  <tr key={s.account_id} className="border-b border-border/30 hover:bg-muted/20">
                    <td className="py-2 px-2 font-medium">#{s.account_id}</td>
                    <td className="py-2 px-2 text-right tabular-nums">${(s.total_equity || 0).toFixed(2)}</td>
                    <td className={cn("py-2 px-2 text-right tabular-nums", s.daily_loss_ratio > 0.03 ? "text-loss" : "text-muted-foreground")}>
                      {(s.daily_loss_ratio * 100).toFixed(2)}%
                    </td>
                    <td className="py-2 px-2 text-right tabular-nums">{(s.margin_usage_percent * 100).toFixed(1)}%</td>
                    <td className="py-2 px-2 text-right tabular-nums">{s.daily_trades}</td>
                    <td className={cn("py-2 px-2 text-right tabular-nums", s.consecutive_losses >= 3 ? "text-warning" : "")}>{s.consecutive_losses}</td>
                    <td className="py-2 px-2 text-center">
                      {s.is_circuit_breaker_active ? <Badge variant="destructive" className="text-[9px]">熔断中</Badge> : <span className="text-muted-foreground">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* 盈亏回撤保护 */}
      {pdPositions.length > 0 && (
        <Card className="p-4">
          <h2 className="text-sm font-medium mb-3">盈亏回撤保护 ({pdPositions.length})</h2>
          <div className="space-y-2">
            {pdPositions.map((p: any) => (
              <div key={p.position_id} className="flex items-center justify-between py-2 border-b border-border/30 last:border-0 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{p.symbol}</span>
                  <span className={cn("text-[10px] px-1 rounded", p.side === "long" ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>{p.side}</span>
                  <span className="text-muted-foreground">{p.nature}</span>
                </div>
                <div className="flex items-center gap-3 text-muted-foreground">
                  <span>入场: <span className="tabular-nums">{p.entry_price?.toLocaleString()}</span></span>
                  <span>现价: <span className="tabular-nums">{p.current_price?.toLocaleString()}</span></span>
                  <span className={cn("tabular-nums font-medium", p.current_upnl >= 0 ? "text-profit" : "text-loss")}>
                    {(p.current_upnl || 0) >= 0 ? "+" : ""}${(p.current_upnl || 0).toFixed(2)}
                  </span>
                  {p.protection_active ? <Badge variant="destructive" className="text-[9px]">保护触发</Badge> : null}
                  {p.protection_would_trigger && !p.protection_active ? <Badge className="text-[9px] bg-warning/20 text-warning">即将触发</Badge> : null}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* 告警历史 */}
      <Card className="p-4">
        <h2 className="text-sm font-medium mb-3">告警历史 ({alerts.length})</h2>
        {alerts.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground text-sm">暂无告警记录</div>
        ) : (
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {alerts.map((a: any, i: number) => (
              <div key={a.id || i} className="flex items-center justify-between py-1.5 border-b border-border/20 text-xs">
                <div className="flex items-center gap-2">
                  <span className={cn("w-1.5 h-1.5 rounded-full",
                    a.severity === "critical" ? "bg-loss" : a.severity === "warning" ? "bg-warning" : "bg-primary")} />
                  <span className="font-medium">{a.symbol || "—"}</span>
                  <span className="text-muted-foreground">{a.type || a.alert_type || "—"}</span>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span>{a.message?.slice(0, 60) || "—"}</span>
                  <span className="font-mono text-[10px]">{a.created_at ? new Date(a.created_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function KpiCard({ label, value, color, icon: Icon }: { label: string; value: string; color?: string; icon: any }) {
  return (
    <Card className="p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <Icon className="w-3.5 h-3.5 text-muted-foreground" />
      </div>
      <div className={cn("text-lg font-bold tabular-nums", color && `text-${color}`)}>{value}</div>
    </Card>
  );
}

function ConfigItem({ label, value }: { label: string; value: any; limit?: number }) {
  return (
    <div className="p-2 rounded bg-muted/30">
      <div className="text-muted-foreground">{label}</div>
      <div className="font-bold tabular-nums">{value}</div>
    </div>
  );
}
