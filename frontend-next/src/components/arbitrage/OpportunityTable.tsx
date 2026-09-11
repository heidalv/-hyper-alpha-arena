"use client";

/**
 * OpportunityTable — 机会表（扣费后净边际，为负时明确显示负值且不可执行）
 *
 * 设计 §3.4 关键：净边际为负的机会必须显示为负并默认不可执行——成本纪律。
 * 本阶段后端无「启用机会」写接口，因此可执行行的按钮点击仅提示「后续阶段接入」，
 * 不可执行行显示禁用按钮 + 原因（不伪造任何写行为）。
 *
 * 阶段2 增强：
 *  - 默认按净边际降序（可用「只看可执行」等过滤器在页面层控制）；
 *  - `suspect=true` 的资金费行显示「数据可疑」并排到末尾；
 *  - 展示资金费 carry 专属列：breakeven_days / annualized_pct / funding_mean_bp / period_hours。
 */
import { useMemo } from "react";
import { ExternalLink, Ban, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtNum } from "@/lib/format";
import { toast } from "@/lib/toast";
import type { Opportunity } from "@/lib/trading-api";

const KIND_LABEL: Record<Opportunity["kind"], string> = {
  market_making: "做市",
  funding_carry: "资金费",
};

const CONF_LABEL: Record<Opportunity["confidence"], string> = {
  measured: "实测",
  unverified: "未验证",
};

function fmtBp(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}bp`;
}

export function OpportunityTable({
  opportunities,
  className,
}: {
  opportunities?: Opportunity[] | null;
  className?: string;
}) {
  const rows = useMemo(() => {
    const items = opportunities ?? [];
    return [...items].sort((a, b) => {
      // suspect 行排末尾；其余按净边际降序（最大的净边际在前）
      if (!!a.suspect !== !!b.suspect) return a.suspect ? 1 : -1;
      return b.net_bp - a.net_bp;
    });
  }, [opportunities]);

  if (!rows.length) {
    return (
      <div className={cn("rounded-lg border border-muted/40 bg-muted/20 px-3 py-6 text-center text-xs text-muted-foreground", className)}>
        暂无机会（可能行情未扫描到新机会）
      </div>
    );
  }

  const executableCount = rows.filter((o) => o.executable).length;
  const suspectCount = rows.filter((o) => o.suspect).length;
  const negativeCount = rows.filter((o) => o.net_bp < 0).length;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="text-xs text-muted-foreground">
        共 {rows.length} 条 · 可执行 {executableCount} 条
        {negativeCount > 0 && <span className="ml-2 text-loss">负边际 {negativeCount} 条（不可执行）</span>}
        {suspectCount > 0 && <span className="ml-2 text-warning">数据可疑 {suspectCount} 条</span>}
      </div>
      <div className="overflow-x-auto rounded-xl border border-border/40">
        <table className="data-table">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left">类型</th>
              <th className="text-left">标的</th>
              <th className="text-left">场地</th>
              <th className="text-right">毛边际</th>
              <th className="text-right">成本</th>
              <th className="text-right">净边际</th>
              <th className="text-right">理论净</th>
              <th className="text-right">资金费均值</th>
              <th className="text-right">平衡天数</th>
              <th className="text-right">年化</th>
              <th className="text-right">容量($)</th>
              <th className="text-left">置信</th>
              <th className="text-center">数据</th>
              <th className="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => {
              const negative = o.net_bp < 0;
              const mk = o.kind === "market_making";
              return (
                <tr
                  key={`${o.kind}-${o.symbol}`}
                  className={cn("border-b border-border/20", !o.executable && "opacity-80", o.suspect && "bg-warning/5")}
                >
                  <td className="font-medium">{KIND_LABEL[o.kind] ?? o.kind}</td>
                  <td className="font-medium">
                    {o.symbol}
                    {o.direction && (
                      <span className="ml-1 block text-[11px] font-normal text-muted-foreground">
                        {o.direction === "short_perp_long_spot" ? "空永续/多现货" : o.direction === "long_perp_short_spot" ? "多永续/空现货" : o.direction}
                      </span>
                    )}
                  </td>
                  <td className="text-muted-foreground">{o.venue}</td>
                  <td className="text-right font-mono tabular-nums">{fmtBp(o.gross_bp)}</td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtBp(o.cost_bp)}</td>
                  <td className={cn("text-right font-mono tabular-nums font-semibold", negative ? "text-loss" : "text-profit")}>
                    {fmtBp(o.net_bp)}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {o.theoretical_net_bp != null ? fmtBp(o.theoretical_net_bp) : "—"}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {!mk && o.funding_mean_bp != null
                      ? `${o.funding_mean_bp >= 0 ? "+" : ""}${o.funding_mean_bp.toFixed(3)}bp${o.period_hours ? `/${o.period_hours}h` : ""}`
                      : "—"}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {!mk && o.breakeven_days != null ? `${o.breakeven_days.toFixed(1)}天` : "—"}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {!mk && o.annualized_pct != null ? `${fmtNum(o.annualized_pct, 2)}%` : "—"}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">
                    {o.capacity_usd ? fmtUsd(o.capacity_usd) : "—"}
                  </td>
                  <td className="text-muted-foreground">{CONF_LABEL[o.confidence] ?? o.confidence}</td>
                  <td className="text-center">
                    {o.suspect ? (
                      <span
                        className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[11px] text-warning"
                        title={o.note}
                      >
                        <AlertTriangle className="h-3 w-3" /> 可疑
                      </span>
                    ) : (
                      <span className="text-muted-foreground/50">—</span>
                    )}
                  </td>
                  <td className="text-right">
                    {o.executable ? (
                      <button
                        type="button"
                        onClick={() => toast.info(`机会 ${o.symbol} 的「启用」动作在后续阶段接入（本阶段仅展示）`)}
                        className="inline-flex items-center gap-1 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-2 py-1 text-[11px] text-cyan-300 hover:bg-cyan-400/20"
                      >
                        <ExternalLink className="h-3 w-3" /> 启用
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled
                        title={o.reason || "不可执行"}
                        className="inline-flex items-center gap-1 rounded-md border border-muted/40 bg-muted/20 px-2 py-1 text-[11px] text-muted-foreground"
                      >
                        <Ban className="h-3 w-3" /> 不可执行
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.some((o) => !o.executable) && (
        <div className="text-[11px] leading-relaxed text-muted-foreground">
          标注「不可执行」的机会扣费后净边际为负、未验证，或缺失现货腿（资金费 carry 的现货腿当前未建成，即使净收益为正也不放行）。
        </div>
      )}
    </div>
  );
}
