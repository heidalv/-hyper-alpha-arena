"use client";

/**
 * FundingMatrixPanel — 多所资金费矩阵 + delta-neutral 净EV机会 + 套利引擎状态
 *
 * [2026-09 修复 P2] 后端 GET /api/rebate/funding-matrix 早已就绪但无前端消费，
 * 本面板补齐展示层：分所费率矩阵（8h 原始口径）、跨所价差套利组合（净年化）、
 * 以及套利引擎开关状态（V3/rebate/live 是否真的在跑），避免「半摆设」困惑。
 *
 * 数据源：GET /api/rebate/funding-matrix
 *   matrix: [{ symbol, venues: { exchange: rate } }]
 *   combos: [{ symbol, long_exchange, short_exchange, net_funding_per_day,
 *              net_apr_at_horizon, sdn_viable, ... }]
 *   arb_status: { v3_statistical_arb: {...}, rebate_points_arb: {...}, live_trading: {...} }
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, Zap, Percent, ShieldOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";

interface ArbStatusInner {
  env_enabled?: boolean;
  session_enabled?: boolean;
  runnable?: boolean;
  paper_mode?: boolean;
  auto_execute?: boolean;
  scan_runnable?: boolean;
  auto_open?: boolean;
  enabled?: boolean;
  note?: string;
}

interface FundingMatrixResponse {
  as_of?: number;
  multi_venue?: boolean;
  venue_count?: number;
  symbol_count?: number;
  combo_count?: number;
  venues?: Record<string, string[]>;
  matrix?: { symbol: string; venues: Record<string, number> }[];
  combos?: Record<string, any>[];
  arb_status?: Record<string, ArbStatusInner>;
  error?: string;
}

const VENUE_ORDER = ["binance", "bybit", "okx", "gateio", "hyperliquid", "asterdex"];

function fmtRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  return `${(rate * 100).toFixed(4)}%`;
}

export default function FundingMatrixPanel() {
  const [data, setData] = useState<FundingMatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${getBackendUrl()}/api/rebate/funding-matrix`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as FundingMatrixResponse;
      if (json.error) throw new Error(json.error);
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  const venueColumns = useMemo(() => {
    const seen = new Set<string>();
    for (const row of data?.matrix ?? []) {
      for (const v of Object.keys(row.venues ?? {})) seen.add(v);
    }
    const ordered = VENUE_ORDER.filter((v) => seen.has(v));
    for (const v of Array.from(seen).sort()) {
      if (!ordered.includes(v)) ordered.push(v);
    }
    return ordered;
  }, [data]);

  const arb = data?.arb_status ?? {};
  const v3 = arb.v3_statistical_arb;
  const rebate = arb.rebate_points_arb;
  const live = arb.live_trading;

  if (loading && !data) {
    return <div className="text-center py-10 text-muted-foreground text-sm">加载中…</div>;
  }
  if (error && !data) {
    return <div className="text-center py-10 text-loss text-sm">加载失败：{error}</div>;
  }

  return (
    <div className="space-y-4">
      {/* 头部统计 + 套利引擎状态 */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={data?.multi_venue ? "secondary" : "outline"}
            className={cn("text-xs", data?.multi_venue ? "text-profit" : "text-muted-foreground")}>
            多所覆盖 {data?.multi_venue ? "✓" : "✗"}
          </Badge>
          <Badge variant="outline" className="text-xs">场所 {data?.venue_count ?? 0}</Badge>
          <Badge variant="outline" className="text-xs">币种 {data?.symbol_count ?? 0}</Badge>
          <Badge variant="outline" className="text-xs">套利组合 {data?.combo_count ?? 0}</Badge>
        </div>
        <Button variant="ghost" size="sm" onClick={load}>
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
      </div>

      {/* 引擎开关状态（半摆设可见化） */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <div className="rounded-lg border border-border/60 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium mb-1.5">
            <Zap className="w-3.5 h-3.5 text-amber-300" /> V3 统计套利（资金费/价差/基差）
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>环境开关 FUNDING_ARB_ENABLED</span>
              <span className={v3?.env_enabled ? "text-profit" : "text-loss"}>
                {v3?.env_enabled ? "开" : "关"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>会话开关 arb_enabled</span>
              <span className={v3?.session_enabled ? "text-profit" : "text-loss"}>
                {v3?.session_enabled ? "开" : "关"}
              </span>
            </div>
            <div className="flex justify-between font-medium">
              <span>是否实际运行</span>
              <span className={v3?.runnable ? "text-profit" : "text-muted-foreground"}>
                {v3?.runnable ? "运行中" : "未运行"}
              </span>
            </div>
          </div>
        </div>
        <div className="rounded-lg border border-border/60 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium mb-1.5">
            <Percent className="w-3.5 h-3.5 text-cyan-300" /> Rebate / Delta-Neutral 刷分
          </div>
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="flex justify-between">
              <span>Paper 模拟</span>
              <span className={rebate?.paper_mode ? "text-profit" : "text-loss"}>
                {rebate?.paper_mode ? "开" : "关"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>自动开仓 auto_execute</span>
              <span className={rebate?.auto_execute ? "text-warning" : "text-muted-foreground"}>
                {rebate?.auto_execute ? "开" : "关"}
              </span>
            </div>
            <div className="flex justify-between font-medium">
              <span>扫描/模拟评估</span>
              <span className={rebate?.scan_runnable ? "text-profit" : "text-loss"}>
                {rebate?.scan_runnable ? "可运行" : "停"}
              </span>
            </div>
          </div>
        </div>
        <div className="rounded-lg border border-border/60 p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium mb-1.5">
            <ShieldOff className="w-3.5 h-3.5 text-muted-foreground" /> 实盘下单
          </div>
          <div className="text-xs text-muted-foreground space-y-1">
            <div className="flex justify-between">
              <span>Phase 5 实盘</span>
              <span className={live?.enabled ? "text-loss" : "text-muted-foreground"}>
                {live?.enabled ? "已启用" : "未启用（全 Paper）"}
              </span>
            </div>
            <div className="text-[10px] leading-relaxed opacity-80">
              {live?.note || "当前全程 Paper，无真实下单。"}
            </div>
          </div>
        </div>
      </div>

      {/* 资金费矩阵 */}
      <div className="rounded-lg border border-border/60 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border/60 bg-background/40">
              <th className="text-left px-3 py-2 font-medium text-muted-foreground">币种</th>
              {venueColumns.map((v) => (
                <th key={v} className="text-right px-3 py-2 font-medium text-muted-foreground">
                  {v}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(data?.matrix ?? []).map((row) => (
              <tr key={row.symbol} className="border-b border-border/30 hover:bg-background/30">
                <td className="px-3 py-1.5 font-medium tabular-nums">{row.symbol}</td>
                {venueColumns.map((v) => {
                  const rate = row.venues?.[v];
                  return (
                    <td key={v} className="px-3 py-1.5 text-right tabular-nums">
                      {rate === undefined ? (
                        <span className="text-muted-foreground opacity-50">—</span>
                      ) : (
                        <span
                          className={cn(
                            rate > 0.0001 ? "text-loss" : rate < 0 ? "text-profit" : ""
                          )}
                        >
                          {fmtRate(rate)}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            {(data?.matrix ?? []).length === 0 && (
              <tr>
                <td
                  colSpan={venueColumns.length + 1}
                  className="px-3 py-6 text-center text-muted-foreground"
                >
                  暂无资金费矩阵数据（多场所采集器未产出或 perp_funding 为空）
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* delta-neutral 组合机会 */}
      <div className="rounded-lg border border-border/60">
        <div className="px-3 py-2 border-b border-border/60 text-xs font-medium text-muted-foreground">
          Delta-Neutral 组合机会（长腿收资金费 - 短腿付资金费，按净年化排序，前 10）
        </div>
        <div className="divide-y divide-border/30">
          {(data?.combos ?? []).slice(0, 10).map((c) => (
            <div key={`${c.symbol}-${c.long_exchange}-${c.short_exchange}`}
              className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs">
              <span className="font-medium tabular-nums">{c.symbol}</span>
              <span className="text-muted-foreground">
                {c.long_exchange} 多 ⇄ {c.short_exchange} 空
              </span>
              <span className="flex items-center gap-2">
                <span className="tabular-nums text-muted-foreground">
                  净年化 {(Number(c.net_apr_at_horizon ?? 0) * 100).toFixed(2)}%
                </span>
                <Badge variant="outline"
                  className={cn("text-[10px]", c.sdn_viable ? "text-profit" : "text-muted-foreground")}>
                  {c.sdn_viable ? "SDN 可行" : "不达标"}
                </Badge>
              </span>
            </div>
          ))}
          {(data?.combos ?? []).length === 0 && (
            <div className="px-3 py-4 text-center text-muted-foreground text-xs">
              暂无可行组合（费率价差不足以覆盖手续费，或只有单所数据）
            </div>
          )}
        </div>
      </div>

      {error && <div className="text-xs text-warning">最近一次刷新失败：{error}</div>}
    </div>
  );
}
