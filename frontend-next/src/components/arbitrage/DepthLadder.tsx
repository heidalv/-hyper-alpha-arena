"use client";

/**
 * DepthLadder — 纵向价格梯（每币一张卡片）
 *
 * 设计依据：`docs/MM_实时深度看板设计_F246补全.md`（F246 补全版）。
 * 布局要求（用户 2026-09-20）：**挂单深度要实时、上下显示** —— 自上而下
 * 「卖 20 档 → 中价 → 买 20 档」，我方挂单在梯上单独一行高亮。
 *
 * 三条硬约束（沿用 F246 原设计的「只展示真实存在的数据」原则）：
 *   1. `has_depth === false` 时**绝不画假深度**，如实留白并区分
 *      「该币本就无深度采集」与「采集停了」——两者文案不同；
 *   2. 深度带时间戳，卡片显示 **depth 年龄**，>2s 标黄、>10s 标红；
 *   3. 我方挂单必须显示**队列前方量**（更优价的累计名义额）——这才是"深度"
 *      对做市的意义所在；取不到时显示「—」，不猜。
 *
 * 为什么柱宽用 log 缩放：20 档量级可差 3 个数量级（实测 ASTER 买一 $40 到
 * 深档 $27万），线性缩放会让所有浅档挤成一条线、完全看不出结构。
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { fmtNum, fmtPrice, fmtUsd } from "@/lib/format";
import type { BoardCard, LadderLevel } from "@/lib/trading-api";

/** 深度年龄阈值（ms）——超过就说明"实时"已经不成立 */
const AGE_WARN_MS = 2_000;
const AGE_BAD_MS = 10_000;

function ageTone(ageMs: number | null | undefined): string {
  if (ageMs == null) return "text-muted-foreground";
  if (ageMs > AGE_BAD_MS) return "text-loss";
  if (ageMs > AGE_WARN_MS) return "text-warning";
  return "text-muted-foreground";
}

function fmtAge(ageMs: number | null | undefined): string {
  if (ageMs == null) return "—";
  if (ageMs < 1_000) return `${Math.round(ageMs)}ms`;
  if (ageMs < 60_000) return `${(ageMs / 1_000).toFixed(1)}s`;
  return `${(ageMs / 60_000).toFixed(1)}m`;
}

function fmtQty(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (a >= 1_000) return `${(v / 1_000).toFixed(2)}K`;
  return v.toFixed(2);
}

function fmtUsdShort(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (a >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

/** log 归一化柱宽（0–100%）。分母取全梯最大量，避免浅档不可见。 */
function useBarScale(card: BoardCard) {
  return useMemo(() => {
    const all: number[] = [];
    for (const lv of card.ladder?.bids ?? []) all.push(lv[1]);
    for (const lv of card.ladder?.asks ?? []) all.push(lv[1]);
    const max = all.length ? Math.max(...all) : 0;
    const denom = Math.log10(Math.max(max, 1e-9) + 1);
    return (q: number) =>
      denom > 0 ? Math.max(2, Math.min(100, (Math.log10(Math.max(q, 0) + 1) / denom) * 100)) : 0;
  }, [card.ladder]);
}

function LadderRow({
  level,
  side,
  priceDigitsSymbol,
  width,
  isMine,
}: {
  level: LadderLevel;
  side: "bid" | "ask";
  priceDigitsSymbol: string;
  width: number;
  isMine: boolean;
}) {
  const [px, qty] = level;
  return (
    <div
      className={cn(
        "relative flex h-[18px] items-center gap-2 px-2 font-mono text-[11px] leading-none",
        isMine && "ring-1 ring-inset ring-cyan-400/70"
      )}
    >
      {/* 量柱：卖侧从右向左、买侧从左向右，形成镜像盘口 */}
      <div
        className={cn(
          "absolute inset-y-[2px] rounded-sm",
          side === "ask" ? "right-0 bg-loss/25" : "left-0 bg-profit/25"
        )}
        style={{ width: `${width}%` }}
      />
      <span
        className={cn(
          "relative z-10 w-[86px] flex-shrink-0 text-right tabular-nums",
          side === "ask" ? "text-loss" : "text-profit"
        )}
      >
        {fmtPrice(priceDigitsSymbol, px)}
      </span>
      <span className="relative z-10 w-[68px] flex-shrink-0 text-right tabular-nums text-muted-foreground">
        {fmtQty(qty)}
      </span>
      {isMine && (
        <span className="relative z-10 rounded bg-cyan-500/20 px-1 py-[1px] text-[9px] font-semibold text-cyan-300">
          我方
        </span>
      )}
    </div>
  );
}

export function DepthLadder({ card, className }: { card: BoardCard; className?: string }) {
  const width = useBarScale(card);
  const lad = card.ladder;
  const mine = card.mine;
  const pos = card.position;

  // 梯子按「上卖下买」渲染：asks 升序 → 反转后最优卖价贴中价；
  // bids 升序（末位最优）→ 直接用，最优买价贴中价。
  const asks = useMemo(() => [...(lad?.asks ?? [])].reverse(), [lad]);
  const bids = useMemo(() => [...(lad?.bids ?? [])].reverse(), [lad]);

  const eps = 1e-9;
  const isMyBid = (px: number) => mine.bid != null && Math.abs(px - mine.bid) < eps;
  const isMyAsk = (px: number) => mine.ask != null && Math.abs(px - mine.ask) < eps;

  return (
    <div className={cn("rounded-lg border border-muted/40 bg-card/40 p-2", className)}>
      {/* 头部：标的 + 中价 + 点差 + 深度年龄 */}
      <div className="mb-1.5 flex items-baseline justify-between gap-2 px-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold">{card.symbol}</span>
          <span className="font-mono text-[13px] font-semibold tabular-nums">
            {fmtPrice(card.symbol, card.mid)}
          </span>
        </div>
        <div className="flex items-baseline gap-2 text-[10px]">
          <span className="text-muted-foreground">
            点差{" "}
            <span className="font-mono tabular-nums text-foreground">
              {card.spread_bp == null ? "—" : `${fmtNum(card.spread_bp, 2)}bp`}
            </span>
          </span>
          <span className={cn("font-mono tabular-nums", ageTone(card.depth_age_ms))}>
            {card.has_depth ? `depth ${fmtAge(card.depth_age_ms)}` : "无深度"}
          </span>
        </div>
      </div>

      {card.has_depth && lad ? (
        <>
          <div className="rounded border border-muted/30 bg-background/40 py-[2px]">
            {asks.map((lv, i) => (
              <LadderRow
                key={`a${i}`}
                level={lv}
                side="ask"
                priceDigitsSymbol={card.symbol}
                width={width(lv[1])}
                isMine={isMyAsk(lv[0])}
              />
            ))}
          </div>

          {/* 中价分隔线 */}
          <div className="my-[2px] flex items-center gap-2 px-2">
            <div className="h-px flex-1 bg-muted/50" />
            <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
              mid {fmtPrice(card.symbol, card.mid)}
            </span>
            <div className="h-px flex-1 bg-muted/50" />
          </div>

          <div className="rounded border border-muted/30 bg-background/40 py-[2px]">
            {bids.map((lv, i) => (
              <LadderRow
                key={`b${i}`}
                level={lv}
                side="bid"
                priceDigitsSymbol={card.symbol}
                width={width(lv[1])}
                isMine={isMyBid(lv[0])}
              />
            ))}
          </div>
        </>
      ) : (
        <div className="flex min-h-[120px] flex-col items-center justify-center gap-1 rounded border border-dashed border-muted/40 bg-muted/10 px-3 py-6 text-center">
          <span className="text-[11px] text-muted-foreground">
            {card.depth_expected
              ? "深度采集未覆盖该币或已停采"
              : "该币无深度采集"}
          </span>
          {card.top && (
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
              最优买 {fmtPrice(card.symbol, card.top.bid)} / 最优卖{" "}
              {fmtPrice(card.symbol, card.top.ask)}
            </span>
          )}
          <span className="text-[10px] text-muted-foreground/70">
            不画假深度（仅 10 个币有 20 档采集）
          </span>
        </div>
      )}

      {/* 我方挂单 + 队列前方量 */}
      <div className="mt-1.5 space-y-[3px] border-t border-muted/30 px-1 pt-1.5 text-[10px]">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">
            我方买{" "}
            <span className="font-mono tabular-nums text-cyan-300">
              {mine.bid == null ? "—" : fmtPrice(card.symbol, mine.bid)}
            </span>
            {mine.bid_width_bp != null && (
              <span className="ml-1 text-muted-foreground">
                {fmtNum(mine.bid_width_bp, 2)}bp
              </span>
            )}
          </span>
          <span className="text-muted-foreground">
            队列前{" "}
            <span className="font-mono tabular-nums text-foreground">
              {fmtUsdShort(mine.bid_queue_ahead_usd)}
            </span>
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">
            我方卖{" "}
            <span className="font-mono tabular-nums text-cyan-300">
              {mine.ask == null ? "—" : fmtPrice(card.symbol, mine.ask)}
            </span>
            {mine.ask_width_bp != null && (
              <span className="ml-1 text-muted-foreground">
                {fmtNum(mine.ask_width_bp, 2)}bp
              </span>
            )}
          </span>
          <span className="text-muted-foreground">
            队列前{" "}
            <span className="font-mono tabular-nums text-foreground">
              {fmtUsdShort(mine.ask_queue_ahead_usd)}
            </span>
          </span>
        </div>
      </div>

      {/* 持仓 */}
      <div className="mt-1 flex items-center justify-between gap-2 border-t border-muted/30 px-1 pt-1.5 text-[10px]">
        <span className="text-muted-foreground">
          持仓{" "}
          <span
            className={cn(
              "font-mono tabular-nums",
              pos.qty > 0 ? "text-profit" : pos.qty < 0 ? "text-loss" : "text-foreground"
            )}
          >
            {pos.qty === 0 ? "0" : fmtQty(pos.qty)}
          </span>
          {pos.avg_mid != null && (
            <span className="ml-1 font-mono tabular-nums text-muted-foreground">
              @{fmtPrice(card.symbol, pos.avg_mid)}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2">
          {pos.hold_ms != null && (
            <span className="font-mono tabular-nums text-muted-foreground">
              {fmtAge(pos.hold_ms)}
            </span>
          )}
          <span
            className={cn(
              "font-mono tabular-nums",
              (pos.unrealized_usd ?? 0) > 0
                ? "text-profit"
                : (pos.unrealized_usd ?? 0) < 0
                  ? "text-loss"
                  : "text-muted-foreground"
            )}
          >
            {fmtUsd(pos.unrealized_usd)}
          </span>
        </span>
      </div>

      {/* 最近成交 */}
      {card.recent_fills.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-[2px] border-t border-muted/30 px-1 pt-1.5 text-[9px]">
          {card.recent_fills.slice(0, 5).map((f, i) => (
            <span key={i} className="flex items-center gap-1">
              <span
                className={cn(
                  "inline-block h-1.5 w-1.5 rounded-full",
                  f.side === "buy" ? "bg-profit" : "bg-loss"
                )}
              />
              <span className="font-mono tabular-nums text-muted-foreground">
                {fmtQty(f.qty)}
                {f.is_close && <span className="ml-[2px] text-[8px]">平</span>}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function TradingBoard({
  cards,
  className,
}: {
  cards: BoardCard[];
  className?: string;
}) {
  if (!cards.length) {
    return (
      <div
        className={cn(
          "rounded-lg border border-muted/40 bg-muted/20 px-3 py-5 text-center text-xs text-muted-foreground",
          className
        )}
      >
        该车道未配置标的（`meta.symbols` 为空）
      </div>
    );
  }
  return (
    <div className={cn("grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3", className)}>
      {cards.map((c) => (
        <DepthLadder key={c.symbol} card={c} />
      ))}
    </div>
  );
}
