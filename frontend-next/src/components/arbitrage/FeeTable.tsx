"use client";

/**
 * FeeTable — 费率表（每所 maker/taker + 返程）
 *
 * 设计 §3.6：只读展示 + 「重新拉取」。**Aster 必须显示 maker 0% / taker 0.04%**
 * （后端 `fee_schedule_service` 是引擎唯一权威口径）。
 * 「重新拉取」调用方传入 onRefresh（即重新请求 GET /config/fees，重新读取费率服务）。
 */
import { RefreshCw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import type { FeeRow } from "@/lib/trading-api";

function fmtBp(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(2)}bp`;
}

export function FeeTable({
  items,
  refreshing,
  onRefresh,
  className,
}: {
  items?: FeeRow[] | null;
  refreshing?: boolean;
  onRefresh?: () => void;
  className?: string;
}) {
  const list = items ?? [];
  if (list.length === 0) {
    return (
      <div className={cn("rounded-lg border border-muted/40 bg-muted/20 px-3 py-6 text-center text-xs text-muted-foreground", className)}>
        暂无费率数据（数据源：fee_schedule_service）
      </div>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>数据源：fee_schedule_service（引擎唯一权威）</span>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1 rounded-md border border-border/40 px-2 py-1 text-[11px] text-muted-foreground hover:border-cyan-400/30 hover:text-cyan-300 disabled:opacity-50"
          >
            {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
            重新拉取
          </button>
        )}
      </div>
      <div className="overflow-x-auto rounded-xl border border-border/40">
        <table className="data-table">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left">交易所</th>
              <th className="text-right">maker(bp)</th>
              <th className="text-right">taker(bp)</th>
              <th className="text-right">maker(%)</th>
              <th className="text-right">taker(%)</th>
              <th className="text-right">返程(maker)</th>
              <th className="text-right">返程(taker)</th>
              <th className="text-right">最小名义($)</th>
            </tr>
          </thead>
          <tbody>
            {list.map((r) => {
              const isAster = r.exchange?.toLowerCase() === "asterdex";
              return (
                <tr key={r.exchange} className={cn("border-b border-border/20", isAster && "bg-cyan-400/5")}>
                  <td className={cn("font-medium", isAster && "text-cyan-300")}>
                    {r.exchange}
                    {isAster && <span className="ml-1 text-[11px] font-normal text-muted-foreground">（页面对齐：maker 0% / taker 0.04%）</span>}
                  </td>
                  <td className="text-right font-mono tabular-nums">{fmtBp(r.maker_bp)}</td>
                  <td className="text-right font-mono tabular-nums">{fmtBp(r.taker_bp)}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{r.maker_pct != null ? `${r.maker_pct.toFixed(3)}%` : "—"}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{r.taker_pct != null ? `${r.taker_pct.toFixed(3)}%` : "—"}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtBp(r.round_trip_maker_bp)}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtBp(r.round_trip_taker_bp)}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {r.min_notional_usd != null ? fmtNum(r.min_notional_usd, 0) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
