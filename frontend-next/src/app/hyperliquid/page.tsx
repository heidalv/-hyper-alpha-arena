"use client";

import { useEffect, useState, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  CandlestickChart, RefreshCw, Loader2, CheckCircle2, XCircle,
  Wallet, Banknote, TrendingUp, Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
const BACKEND = getBackendUrl().replace(/\/$/, "");

export default function HyperliquidPage() {
  const [statuses, setStatuses] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [balances, setBalances] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const EXCHANGE_NAMES: Record<string, string> = {
    hyperliquid: "Hyperliquid",
    binance: "币安",
    bybit: "Bybit",
    okx: "OKX",
    gateio: "Gate.io",
    asterdex: "Asterdex",
  };
  const getExName = (id: string) => EXCHANGE_NAMES[id] || id;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sts, allPos] = await Promise.all([
        fetch(`${BACKEND}/api/exchange/statuses`).then(r => r.json()).catch(() => []),
        fetch(`${BACKEND}/api/exchange/positions/all`).then(r => r.json()).catch(() => []),
      ]);
      setStatuses(sts);
      setPositions(Array.isArray(allPos) ? allPos : (allPos?.positions || []));

      // 并行获取每个已连接交易所的余额
      const balResults: Record<string, any> = {};
      await Promise.all(sts.filter((s: any) => s.connected).map(async (s: any) => {
        try {
          const b = await fetch(`${BACKEND}/api/exchange/${s.exchange}/balance`).then(r => r.json());
          balResults[s.exchange] = b;
        } catch {}
      }));
      setBalances(balResults);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);

  const connectedCount = statuses.filter(s => s.connected).length;

  return (
    <div className="p-4 space-y-4">
      {/* 标题（Aurora 统一页头） */}
      <PageHeader
        icon={<CandlestickChart className="w-4 h-4" />}
        title="交易所监控"
        subtitle="Hyperliquid 优先 · 跨交易所余额与持仓统一监控"
        refreshHint="WebSocket 实时"
        breadcrumb={[{ label: "交易所" }, { label: "Hyperliquid" }]}
        actions={
          <>
            <Badge variant="secondary" className={cn("text-[9px]", connectedCount > 0 ? "bg-profit/20 text-profit" : "bg-muted")}>
              {connectedCount}/{statuses.length} 已连接
            </Badge>
            <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
              <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
            </Button>
          </>
        }
      />

      {error && <div className="text-sm text-loss bg-loss/10 p-2 rounded">⚠️ {error}</div>}

      {/* KPI 卡片化：跨所总权益 / 可用 / 已用保证金 / 持仓 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Wallet className="w-3.5 h-3.5" />
          </span>
          <div className="text-[10px] text-muted-foreground">总权益</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight grad-text">${Object.values(balances).reduce((s: number, b: any) => s + (b.total_equity || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Banknote className="w-3.5 h-3.5" />
          </span>
          <div className="text-[10px] text-muted-foreground">可用</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">${Object.values(balances).reduce((s: number, b: any) => s + (b.available_balance || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Server className="w-3.5 h-3.5" />
          </span>
          <div className="text-[10px] text-muted-foreground">已用保证金</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">${Object.values(balances).reduce((s: number, b: any) => s + (b.used_margin || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <TrendingUp className="w-3.5 h-3.5" />
          </span>
          <div className="text-[10px] text-muted-foreground">持仓</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">{positions.length}</div>
          <div className="text-[9px] text-muted-foreground">{connectedCount}/{statuses.length} 已连接</div>
        </Card>
      </div>

      {/* 交易所卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {statuses.map((s) => {
          const bal = balances[s.exchange];
          const exPositions = positions.filter((p: any) => p.exchange === s.exchange);
          return (
            <Card key={s.exchange} className={cn("p-4 border glass", s.connected ? "border-profit/30" : "border-border")}>
              {/* 标题 */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className={cn("w-8 h-8 rounded flex items-center justify-center", s.connected ? "bg-profit/10" : "bg-muted")}>
                    <Server className={cn("w-4 h-4", s.connected ? "text-profit" : "text-muted-foreground")} />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{getExName(s.exchange)}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {s.supports_spot && "现货"} {s.supports_futures && "合约"}
                    </div>
                  </div>
                </div>
                <Badge variant="secondary" className={cn("text-[9px]", s.connected ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>
                  {s.connected ? "已连接" : "未连接"}
                </Badge>
              </div>

              {/* 余额 */}
              {s.connected && bal && (
                <div className="space-y-1 mb-3">
                  {bal.total_equity !== null && bal.total_equity !== undefined && (
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground flex items-center gap-1"><Wallet className="w-3 h-3" />总权益</span>
                      <span className="font-bold tabular-nums grad-text">${(bal.total_equity || 0).toFixed(2)}</span>
                    </div>
                  )}
                  {bal.available_balance !== null && bal.available_balance !== undefined && (
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground">可用</span>
                      <span className="tabular-nums">${(bal.available_balance || 0).toFixed(2)}</span>
                    </div>
                  )}
                  {bal.used_margin !== null && bal.used_margin !== undefined && bal.used_margin > 0 && (
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground">已用保证金</span>
                      <span className="tabular-nums">${(bal.used_margin || 0).toFixed(2)}</span>
                    </div>
                  )}
                </div>
              )}

              {/* 持仓摘要 */}
              {s.connected && exPositions.length > 0 && (
                <div className="pt-2 border-t border-border/30">
                  <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                    <TrendingUp className="w-3 h-3" />持仓 ({exPositions.length})
                  </div>
                  <div className="space-y-0.5 max-h-32 overflow-y-auto">
                    {exPositions.slice(0, 8).map((p: any, i: number) => {
                      const pnl = p.unrealized_pnl || p.pnl || 0;
                      const isLong = (p.side || p.position_side) === "long" || (p.side || p.position_side) === "buy";
                      return (
                        <div key={i} className="flex items-center justify-between text-[10px]">
                          <div className="flex items-center gap-1">
                            <span className="font-medium">{p.symbol || "—"}</span>
                            <span className={cn("text-[8px] px-0.5 rounded", isLong ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>
                              {isLong ? "多" : "空"}
                            </span>
                          </div>
                          <span className={cn("tabular-nums", pnl >= 0 ? "text-profit" : "text-loss")}>
                            {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* 未连接提示 */}
              {!s.connected && (
                <div className="text-center py-2 text-[10px] text-muted-foreground">
                  在「交易所管理 → API 凭证」中配置连接
                </div>
              )}
            </Card>
          );
        })}
      </div>

      {/* 空态：暂无交易所连接 */}
      {!loading && statuses.length === 0 && (
        <Card className="p-8 text-center text-muted-foreground text-sm glass">
          <div className="w-11 h-11 mx-auto mb-2 rounded-xl bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/25 flex items-center justify-center">
            <Server className="w-5 h-5 text-cyan-300" />
          </div>
          暂无交易所连接，请先在「交易所管理」中配置 API 凭证
        </Card>
      )}

      {/* 统一持仓表 */}
      {positions.length > 0 && (
        <Card className="p-4 glass">
          <h2 className="text-sm font-medium mb-3">跨所持仓 ({positions.length})</h2>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-2 px-2">交易所</th>
                <th className="text-left py-2 px-2">币种</th>
                <th className="text-left py-2 px-2">方向</th>
                <th className="text-right py-2 px-2">数量 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">入场价 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">标记价 <span className="text-cyan-300">▲</span></th>
                <th className="text-right py-2 px-2">浮盈 <span className="text-cyan-300">▲</span></th>
              </tr></thead>
              <tbody>
                {positions.map((p: any, i: number) => {
                  const pnl = p.unrealized_pnl || p.pnl || 0;
                  const isLong = (p.side || p.position_side) === "long" || (p.side || p.position_side) === "buy";
                  return (
                    <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                      <td className="py-2 px-2">
                        <Badge variant="secondary" className="text-[9px]">{getExName(p.exchange)}</Badge>
                      </td>
                      <td className="py-2 px-2 font-medium">{p.symbol}</td>
                      <td className="py-2 px-2">
                        <span className={cn("text-[10px] px-1 rounded", isLong ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>
                          {isLong ? "多" : "空"}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right tabular-nums num">{(p.quantity || p.size || 0).toFixed(4)}</td>
                      <td className="py-2 px-2 text-right tabular-nums num text-muted-foreground">{(p.entry_price || 0).toLocaleString()}</td>
                      <td className="py-2 px-2 text-right tabular-nums num text-muted-foreground">{(p.mark_price || 0).toLocaleString()}</td>
                      <td className={cn("py-2 px-2 text-right tabular-nums num font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
                        {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td className="px-3 py-2 text-muted-foreground text-xs">合计 {positions.length} 笔</td>
                  <td colSpan={4} />
                  <td className={cn("text-right py-2 num font-bold",
                    positions.reduce((s: number, p: any) => s + ((p.unrealized_pnl || p.pnl) || 0), 0) >= 0 ? "text-profit" : "text-loss")}>
                    {positions.reduce((s: number, p: any) => s + ((p.unrealized_pnl || p.pnl) || 0), 0) >= 0 ? "+" : ""}${positions.reduce((s: number, p: any) => s + ((p.unrealized_pnl || p.pnl) || 0), 0).toFixed(2)}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
