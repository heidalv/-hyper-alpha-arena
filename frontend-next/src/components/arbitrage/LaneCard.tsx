"use client";

/**
 * LaneCard — 车道摘要卡
 *
 * 设计 §3.1：名称、mode 徽章、净期望 EdgeBadge、今日 P&L（本阶段以「近7天净收益」
 * 兜底，因 /lanes 不提供 per-lane pnl_today）、库存/敞口、晋升进度条、7 日迷你曲线。
 * 点击卡片跳转 `?lane=<id>` 详情。
 */
import { useMemo } from "react";
import { ArrowRight, PauseCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPct } from "@/lib/format";
import { fmtAgeMs, type LaneSummary, type DailyPoint } from "@/lib/trading-api";
import { EdgeBadge } from "./EdgeBadge";

const MODE_META: Record<string, { label: string; tone: string }> = {
  paper: { label: "模拟盘", tone: "bg-cyan-400/15 text-cyan-300 border-cyan-400/25" },
  live: { label: "实盘", tone: "bg-loss/15 text-loss border-loss/30" },
  disabled: { label: "已停用", tone: "bg-muted/40 text-muted-foreground border-muted" },
};

const STATUS_META: Record<string, { label: string; tone: string }> = {
  active: { label: "运行中", tone: "bg-profit/15 text-profit" },
  paused: { label: "已暂停", tone: "bg-warning/15 text-warning" },
  stopped: { label: "已停止", tone: "bg-muted/40 text-muted-foreground" },
};

function ModeBadge({ mode }: { mode: string }) {
  const m = MODE_META[mode] ?? { label: mode, tone: "bg-muted/40 text-muted-foreground border-muted" };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium", m.tone)}>
      {m.label}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const m = STATUS_META[status] ?? { label: status, tone: "bg-muted/40 text-muted-foreground" };
  return (
    <span className={cn("inline-flex items-center gap-1 text-[11px]", m.tone)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", status === "active" ? "bg-profit" : status === "paused" ? "bg-warning" : "bg-muted-foreground")} />
      {m.label}
    </span>
  );
}

/** 迷你 SVG 曲线（近 7 天） */
function Sparkline({ values }: { values: number[] }) {
  const W = 96;
  const H = 22;
  const { path, color } = useMemo(() => {
    if (values.length < 2) return { path: "", color: "var(--muted-foreground)" };
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const pts = values.map((v, i) => {
      const x = (i / (values.length - 1)) * W;
      const y = H - 2 - ((v - min) / range) * (H - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const last = values[values.length - 1];
    const first = values[0];
    return {
      path: `M ${pts.map((p) => p.split(",").join(" ")).join(" L ")}`,
      color: last >= first ? "var(--profit)" : "var(--loss)",
    };
  }, [values]);

  if (values.length < 2) {
    return <div className="h-[22px] text-[11px] leading-[22px] text-muted-foreground">—</div>;
  }
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block">
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function LaneCard({
  lane,
  budgetUsd,
  equity,
  pnl7dUsd,
  series,
  href,
  className,
}: {
  lane: LaneSummary;
  budgetUsd?: number | null;
  equity?: number | null;
  pnl7dUsd?: number | null;
  /** 近 7 天逐日净收益（迷你曲线） */
  series?: DailyPoint[] | null;
  href?: string;
  className?: string;
}) {
  const name = lane.meta?.name || lane.lane_id;
  const budgetPct = equity ? Math.round(((budgetUsd ?? 0) / equity) * 100) : null;
  const promotion = lane.promotion;
  // [F61 阶段2] 今日盈亏取不到（null）→ 显示「无数据」，绝不冒充 0
  const pnlToday = lane.pnl_today_usd;
  const inventory = lane.inventory_usd;
  const dataAge = lane.data_age_sec != null ? fmtAgeMs(lane.data_age_sec * 1000) : null;

  return (
    <div
      className={cn(
        "group relative flex flex-col gap-3 rounded-xl border border-border/40 bg-card p-3.5 transition-colors hover:border-cyan-400/30",
        className
      )}
    >
      {/* 标题 */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{name}</h3>
            <ModeBadge mode={lane.mode} />
          </div>
          <div className="mt-1 flex items-center gap-2 text-muted-foreground">
            <StatusDot status={lane.status} />
            {lane.meta?.venue && <span className="text-[11px]">{lane.meta.venue}</span>}
          </div>
        </div>
        {href && (
          <a
            href={href}
            className="flex-shrink-0 rounded-md border border-border/40 p-1 text-muted-foreground transition-colors hover:border-cyan-400/30 hover:text-cyan-300"
            aria-label={`查看 ${name} 详情`}
          >
            <ArrowRight className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      {/* 净期望 */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">净期望</span>
        <EdgeBadge edge={lane.edge} />
      </div>

      {/* 今日盈亏 + 库存 + 近7天 + 资金权重（数据来源 /lanes，盈亏取不到显示「无数据」） */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-md bg-muted/20 px-2 py-1.5">
          <div className="text-[11px] text-muted-foreground">今日盈亏</div>
          <div className={cn("font-mono tabular-nums font-semibold", pnlToday == null ? "text-muted-foreground" : pnlToday >= 0 ? "text-profit" : "text-loss")}>
            {pnlToday == null ? "无数据" : `${pnlToday >= 0 ? "+" : ""}${fmtUsd(pnlToday)}`}
          </div>
        </div>
        <div className="rounded-md bg-muted/20 px-2 py-1.5">
          <div className="text-[11px] text-muted-foreground">当前库存</div>
          <div className="font-mono tabular-nums font-semibold">{inventory == null ? "无数据" : fmtUsd(inventory)}</div>
        </div>
        <div className="rounded-md bg-muted/20 px-2 py-1.5">
          <div className="text-[11px] text-muted-foreground">近 7 天净收益</div>
          <div className={cn("font-mono tabular-nums font-semibold", (pnl7dUsd ?? 0) >= 0 ? "text-profit" : "text-loss")}>
            {pnl7dUsd == null ? "无数据" : fmtUsd(pnl7dUsd)}
          </div>
        </div>
        <div className="rounded-md bg-muted/20 px-2 py-1.5">
          <div className="text-[11px] text-muted-foreground">资金权重</div>
          <div className="font-mono tabular-nums font-semibold">
            {budgetPct == null ? "—" : fmtPct(budgetPct, 0)}
          </div>
        </div>
      </div>

      {/* 晋升进度 */}
      {promotion ? (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-muted-foreground">晋升进度</span>
            <span className={cn("font-mono tabular-nums", promotion.ready ? "text-profit" : "text-warning")}>
              {promotion.progress_pct.toFixed(0)}%{promotion.ready ? " · 就绪" : ""}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/30">
            <div
              className={cn("h-full", promotion.ready ? "bg-profit" : "bg-cyan-400")}
              style={{ width: `${Math.min(100, promotion.progress_pct)}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-border/50 px-2 py-1.5 text-[11px] text-muted-foreground">
          尚无晋升进度
        </div>
      )}

      {/* 7 日迷你曲线 */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">近 7 天</span>
        {series && series.length > 0 ? (
          <Sparkline values={series.map((s) => s.net_usd)} />
        ) : (
          <PauseCircle className="h-4 w-4 text-muted-foreground" />
        )}
      </div>

      {/* 数据年龄（as_of） */}
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>数据年龄</span>
        <span className="font-mono tabular-nums">{dataAge ?? (lane.updated_at ? "见详情页" : "未知")}</span>
      </div>
    </div>
  );
}
