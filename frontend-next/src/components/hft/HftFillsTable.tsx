"use client";

/**
 * [F250] 中短期高频交易 · 成交记录表
 *
 * ## 为什么必须有这个组件
 *
 * 控制面板此前只有「执行状态」（挂单价/持仓），**没有成交明细**。
 * 用户实测反馈："完全没有交易记录"——无法判断是"真的没成交"还是"界面没显示"。
 * 这是可观测性缺口：一条车道最重要的是**它到底成交了什么、赚亏在哪一维**。
 *
 * ## 六维归因（后端 `lane_ledger` 口径）
 *
 *     spread_bp   价差捕获（用**挂单时中价**做基准 ⇒ 挂单意图的边际）
 *     price_bp    行情从挂单到成交的移动 ⇒ **逆选择落在这里**
 *     fee_bp      费率（Aster maker=0 ⇒ 被动腿应为 0）
 *     funding_bp  资金费
 *     slippage_bp 滑点
 *     net_bp      六维净额
 *
 * 看这张表的正确方式：**先看 price_bp 的绝对值**。
 * 它比 spread_bp 大时，说明赚的点差被逆选择吃掉——这正是研究结论里
 * "往返净 ≈ 点差 + 成交后 markout" 的现场体现。
 */
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtNum, fmtUsd } from "@/lib/format";
import { DataState } from "@/components/arbitrage";
import type { HftFillsResponse } from "@/lib/hft-api";

function bp(v: number, digits = 2): string {
  return `${v >= 0 ? "+" : ""}${fmtNum(v, digits)}`;
}

function tone(v: number): string {
  return v > 0 ? "text-profit" : v < 0 ? "text-loss" : "text-muted-foreground";
}

/** 4 位小数金额 —— 本模块单腿 $30、一笔往返赚 $0.0045，2 位会把盈亏显示成 $0.00。 */
function usd4(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v < 0 ? "−" : v > 0 ? "+" : ""}$${Math.abs(v).toFixed(4)}`;
}

export function HftFillsTable({
  data,
  hours = 24,
  className,
}: {
  data: HftFillsResponse | null;
  hours?: number;
  className?: string;
}) {
  const s = data?.summary;
  return (
    <Card className={cn("glass p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <span className="text-[11px]">↓</span>
          </span>
          成交记录（近 {hours}h）
        </h2>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="text-muted-foreground">
            成交 <span className="font-mono tabular-nums text-foreground">{s?.fills ?? "—"}</span> 笔
          </span>
          <span className="text-muted-foreground">
            名义 <span className="font-mono tabular-nums text-foreground">
              {s ? fmtUsd(s.notional_sum) : "—"}
            </span>
          </span>
          <span className="text-muted-foreground">
            净额{" "}
            <span className={cn("font-mono tabular-nums font-semibold text-sm",
              s ? tone(s.net_usd_sum) : "text-muted-foreground")}>
              {s ? usd4(s.net_usd_sum) : "—"}
            </span>
          </span>
          {s?.net_bp_w != null && (
            <span className="text-muted-foreground">
              加权{" "}
              <span className={cn("font-mono tabular-nums", tone(s.net_bp_w))}>
                {bp(s.net_bp_w)}bp
              </span>
            </span>
          )}
        </div>
      </div>

      <DataState
        loading={!data}
        error={null}
        hasData={!!data}
        empty={!!data && ((data.items ?? []).length === 0)}
        emptyHint={
          "该区间内没有成交。若长时间为空，检查报价挂宽是否远大于实际点差" +
          "（挂宽 > 半价差时物理上不可能成交），以及趋势/波动闸是否封掉了整侧。"
        }
      >
        {data && (data.items ?? []).length > 0 && (
          <>
            {/* 六维归因 —— **名义加权**，不是每笔等权均值。
                等权均值会把 $0.26 的碎腿和 $90 的整腿同等对待，在腿量不齐时系统性失真；
                加权 bp = 该维金额 ÷ 总名义 × 1e4，是唯一有金融含义的"平均 bp"。
                金额列同时给出，避免用户只能靠 bp 猜"到底赚亏多少钱"。 */}
            {s && s.fills > 0 && (
              <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {([
                  ["价差捕获", s.spread_bp_w ?? null, s.spread_usd_sum ?? null],
                  ["逆选择", s.price_bp_w ?? null, s.price_usd_sum ?? null],
                  ["费率", s.fee_bp_w ?? null, s.fee_usd_sum ?? null],
                  ["滑点", null, s.slippage_usd_sum ?? null],
                  ["净边际", s.net_bp_w ?? null, s.net_usd_sum],
                ] as const).map(([label, w, usd]) => (
                  <div key={label} className="rounded border border-muted/40 bg-muted/10 px-2 py-1.5">
                    <div className="text-[10px] text-muted-foreground">{label}（加权）</div>
                    <div className={cn("font-mono text-[13px] font-semibold tabular-nums",
                      usd == null ? "text-muted-foreground" : tone(usd))}>
                      {w == null ? "—" : `${bp(w)}bp`}
                    </div>
                    <div className={cn("font-mono text-[11px] tabular-nums",
                      usd == null ? "text-muted-foreground" : tone(usd))}>
                      {usd4(usd)}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* ⚠️ 必须有高度约束 + 内部滚动：
                此前表格无 max-height ⇒ 50 行全部展开、页面被无限向下撑长
                （用户实测反馈「一直在无限向下延申」）。表头 sticky 保证滚动时仍可读。 */}
            <div className="max-h-[320px] overflow-y-auto rounded border border-muted/30">
              <table className="data-table w-full text-xs">
                <thead className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                  <tr className="text-left text-[10px] text-muted-foreground">
                    <th className="py-1.5 pl-2 pr-2">时间</th>
                    <th className="py-1.5 pr-2">标的</th>
                    <th className="py-1.5 pr-2 text-right">名义</th>
                    <th className="py-1.5 pr-2 text-right">价差</th>
                    <th className="py-1.5 pr-2 text-right">逆选择</th>
                    <th className="py-1.5 pr-2 text-right">费率</th>
                    <th className="py-1.5 pr-2 text-right">净额bp</th>
                    <th className="py-1.5 pr-2 text-right">净额$</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.items ?? []).map((f, i) => (
                    <tr key={`${f.ts}-${i}`} className="border-t border-muted/20">
                      <td className="py-1 pl-2 pr-2 font-mono text-[10px] tabular-nums text-muted-foreground">
                        {f.ts ? f.ts.slice(5, 19) : "—"}
                      </td>
                      <td className="py-1 pr-2 font-medium">{f.symbol}</td>
                      <td className="py-1 pr-2 text-right font-mono tabular-nums">
                        {fmtUsd(f.notional_usd)}
                      </td>
                      <td className={cn("py-1 pr-2 text-right font-mono tabular-nums", tone(f.spread_bp))}>
                        {bp(f.spread_bp)}
                      </td>
                      <td className={cn("py-1 pr-2 text-right font-mono tabular-nums", tone(f.price_bp))}>
                        {bp(f.price_bp)}
                      </td>
                      <td className={cn("py-1 pr-2 text-right font-mono tabular-nums", tone(f.fee_bp))}>
                        {bp(f.fee_bp)}
                      </td>
                      <td className={cn("py-1 pr-2 text-right font-mono tabular-nums font-semibold", tone(f.net_bp))}>
                        {bp(f.net_bp)}
                      </td>
                      <td className={cn("py-1 pr-2 text-right font-mono tabular-nums", tone(f.net_usd))}>
                        {usd4(f.net_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(data.items ?? []).length >= 50 && (
              <div className="mt-1 text-[10px] text-muted-foreground">
                仅显示最近 50 笔（可滚动查看）
              </div>
            )}
            <p className="mt-2 text-[11px] text-muted-foreground">
              六维来自 <span className="font-mono">lane_ledger</span>。判断要点：**先看「逆选择」列**——
              它的绝对值超过「价差」时，赚到的点差被成交后的行情反向移动吃掉了。
              被动腿的「费率」应为 0（Aster maker 0%）。
              金额为**逐行**折算后求和（<span className="font-mono">net_bp/1e4 × notional</span>），
              与下方明细列逐笔一致；加权 bp = 金额 ÷ 名义 × 1e4。
            </p>
          </>
        )}
      </DataState>
    </Card>
  );
}
