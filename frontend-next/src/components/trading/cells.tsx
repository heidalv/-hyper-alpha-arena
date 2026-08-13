/**
 * 交易页共享展示组件（R5-4 抽取自 dashboard 页面底部私有组件）
 * 供仪表盘/模拟交易/实盘交易等页面复用，避免重复实现。
 */
import { cn } from "@/lib/utils";

// ═══ KPI 单元格（8 列 KPI 带） ═══

const CELL_COLORS: Record<string, string> = {
  profit: "text-profit",
  loss: "text-loss",
  warning: "text-warning",
  muted: "text-muted-foreground",
};

export function KpiCell({
  label,
  value,
  delta,
  deltaColor = "muted",
}: {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: "profit" | "loss" | "warning" | "muted";
}) {
  return (
    <div className="p-2 border-r border-border/20 last:border-r-0 flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className="text-base font-semibold font-mono tracking-tight leading-tight">{value}</div>
      {delta && <div className={cn("text-[9px] font-mono", CELL_COLORS[deltaColor] || "")}>{delta}</div>}
    </div>
  );
}

// ═══ 三周期卡片内统计 ═══

export function TierStatCell({
  value,
  label,
  color,
}: {
  value: string;
  label: string;
  color?: "profit" | "loss";
}) {
  return (
    <div className="text-left pl-1.5 first:pl-0 border-r border-border/10 last:border-r-0 py-1">
      <div className={cn("text-[13px] font-semibold font-mono leading-tight", color ? CELL_COLORS[color] : "")}>
        {value}
      </div>
      <div className="text-[9px] text-muted-foreground mt-0.5 uppercase tracking-wider">{label}</div>
    </div>
  );
}

// ═══ 风险敞口单元格 ═══

export function RiskCell({
  label,
  value,
  ctx,
  valueColor,
}: {
  label: string;
  value: string;
  ctx?: string;
  valueColor?: "profit" | "loss";
}) {
  return (
    <div className="p-2 px-3 border-r border-border/20 last:border-r-0 flex flex-col gap-0.5 min-w-0">
      <span className="text-[9px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      <span className={cn("text-sm font-semibold font-mono", valueColor ? CELL_COLORS[valueColor] : "")}>{value}</span>
      {ctx && <span className="text-[9px] text-muted-foreground font-mono">{ctx}</span>}
    </div>
  );
}

// ═══ 小节标题（SECTION · 右侧操作区） ═══

export function SectionHeader({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between mt-2 mb-1">
      <span className="text-[9px] font-medium text-muted-foreground uppercase tracking-widest">{title}</span>
      {children}
    </div>
  );
}

// ═══ 状态徽章 ═══

export function StatusBadge({
  tone,
  children,
  glow = false,
}: {
  tone: "profit" | "loss" | "warning" | "muted";
  children: React.ReactNode;
  glow?: boolean;
}) {
  const map = {
    profit: "bg-profit/15 text-profit",
    loss: "bg-loss/15 text-loss",
    warning: "bg-warning/15 text-warning",
    muted: "bg-muted/30 text-muted-foreground",
  } as const;
  return (
    <span className={cn("inline-flex items-center gap-1.5 h-[18px] px-1.5 rounded-full text-[11px] font-medium", map[tone])}>
      {glow && <span className={cn("w-1 h-1 rounded-full", tone === "profit" ? "bg-profit" : tone === "loss" ? "bg-loss" : "bg-warning")} style={{ boxShadow: "0 0 6px currentColor" }} />}
      {children}
    </span>
  );
}

// ═══ 空态 ═══

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="text-sm text-muted-foreground">{title}</div>
      {description && <div className="text-xs text-muted-foreground/70 max-w-xs">{description}</div>}
      {action}
    </div>
  );
}
