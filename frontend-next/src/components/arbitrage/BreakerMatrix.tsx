"use client";

/**
 * BreakerMatrix — 车道 × 熔断类型状态灯矩阵 + 熔断历史
 *
 * 设计 §3.5/§4：车道（行）× 熔断类型（data/fee/daily_loss/toxic_flow，列），
 * 每格状态灯（绿=ok，红=tripped）+ 触发时间 + 原因。
 *
 * 阶段2 增强：
 *  - 支持 `history`（近 30 天熔断历史）展示；
 *  - 支持 `onReset(laneId, breaker)` 回调：触发的格子上提供「复位」按钮，
 *    由调用方（风险页）走 confirmDialog 后调用后端。
 */
import { useMemo } from "react";
import { RotateCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtTime, fmtNum } from "@/lib/format";
import type { BreakerKind, BreakerRow, BreakerHistoryEntry } from "@/lib/trading-api";

const BREAKER_LABEL: Record<string, string> = {
  data: "数据",
  fee: "费率",
  daily_loss: "日亏",
  toxic_flow: "毒性流",
};

const BREAKER_ORDER: BreakerKind[] = ["data", "fee", "daily_loss", "toxic_flow"];

export function BreakerMatrix({
  rows,
  history,
  onReset,
  className,
}: {
  rows?: BreakerRow[] | null;
  history?: BreakerHistoryEntry[] | null;
  onReset?: (laneId: string, breaker: string) => void;
  className?: string;
}) {
  const s = useMemo(() => {
    const list = rows ?? [];
    const lanes = Array.from(new Set(list.map((r) => r.lane_id)));
    const byCell = new Map<string, BreakerRow>();
    for (const r of list) byCell.set(`${r.lane_id}::${r.breaker}`, r);
    const trippedCount = list.filter((r) => r.state === "tripped").length;
    return { lanes, byCell, trippedCount };
  }, [rows]);

  // 没有行也未必没有历史；二者皆空才显示空态
  const hasAny = (rows && rows.length > 0) || (history && history.length > 0);

  if (!hasAny) {
    return (
      <div className={cn("rounded-lg border border-muted/40 bg-muted/20 px-3 py-6 text-center text-xs text-muted-foreground", className)}>
        暂无熔断数据（车道尚未上报健康状态）
      </div>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      {/* 状态灯矩阵 */}
      <div>
        <div className="mb-2 text-xs text-muted-foreground">
          {s.lanes.length} 条车道 × {BREAKER_ORDER.length} 类熔断 · 触发 {s.trippedCount} 处
        </div>
        <div className="overflow-x-auto rounded-xl border border-border/40">
          <table className="data-table">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left">车道</th>
                {BREAKER_ORDER.map((b) => (
                  <th key={b} className="text-center">{BREAKER_LABEL[b] ?? b}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {s.lanes.map((laneId) => (
                <tr key={laneId} className="border-b border-border/20">
                  <td className="font-medium">{laneId}</td>
                  {BREAKER_ORDER.map((b) => {
                    const cell = s.byCell.get(`${laneId}::${b}`);
                    const tripped = cell?.state === "tripped";
                    // [F61 阶段3] 原样展示 reason（如 stale_data(...) / drill），不要截断成「未知」
                    const reason = cell?.reason || "";
                    const notable = reason.length > 0;
                    return (
                      <td key={b} className="text-center">
                        <span
                          className={cn(
                            "inline-flex h-4 w-4 rounded-full",
                            tripped
                              ? "bg-loss shadow-[0_0_6px_rgba(251,113,133,0.7)]"
                              : notable
                                ? "bg-warning/80 shadow-[0_0_5px_rgba(250,204,21,0.6)]"
                                : "bg-profit/70"
                          )}
                          title={reason || (tripped ? "已触发" : "正常")}
                        />
                        {cell?.ts && (
                          <div className="mt-0.5 text-[11px] text-muted-foreground">{fmtTime(cell.ts)}</div>
                        )}
                        {notable && (
                          <div
                            className={cn(
                              "mx-auto mt-0.5 max-w-[9rem] break-words text-[11px] leading-tight",
                              tripped ? "text-loss" : "text-warning"
                            )}
                            title={reason}
                          >
                            {reason}
                          </div>
                        )}
                        {tripped && onReset && (
                          <button
                            type="button"
                            onClick={() => onReset(laneId, b)}
                            className="mt-1 inline-flex items-center gap-0.5 rounded border border-loss/30 bg-loss/10 px-1.5 py-0.5 text-[11px] text-loss hover:bg-loss/20"
                          >
                            <RotateCw className="h-3 w-3" /> 复位
                          </button>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 熔断历史（近 30 天） */}
      {history && history.length > 0 && (
        <div>
          <div className="mb-2 text-xs text-muted-foreground">熔断历史 · 近 30 天（{history.length} 类）</div>
          <div className="overflow-x-auto rounded-xl border border-border/40">
            <table className="data-table">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left">车道</th>
                  <th className="text-left">类型</th>
                  <th className="text-right">触发次数</th>
                  <th className="text-left">最近触发</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={`${h.lane_id}-${h.breaker}`} className="border-b border-border/20">
                    <td className="font-medium">{h.lane_id}</td>
                    <td>{BREAKER_LABEL[h.breaker] ?? h.breaker}</td>
                    <td className="text-right font-mono tabular-nums">{fmtNum(h.trips, 0)}</td>
                    <td className="text-muted-foreground">{h.last_ts ? fmtTime(h.last_ts) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
