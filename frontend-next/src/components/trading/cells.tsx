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

/** 异常值防御：百分比数值超过 500% 视为脏数据，显示「数据异常」而非裸显 */
function anomalyPct(delta?: string): boolean {
  if (!delta) return false;
  const m = delta.match(/(-?\d+(?:\.\d+)?)\s*%?$/);
  if (!m) return false;
  const n = Math.abs(parseFloat(m[1]));
  return !Number.isFinite(n) || n > 500;
}

export function KpiCell({
  label,
  value,
  delta,
  deltaColor = "muted",
  grad,
  icon,
}: {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: "profit" | "loss" | "warning" | "muted";
  /** 渐变数字（Aurora 签名元素） */
  grad?: "cyan" | "green" | "red";
  /** 右上角图标徽章（设计稿 KPI 卡元素） */
  icon?: React.ReactNode;
}) {
  const bad = anomalyPct(delta);
  const gradCls =
    grad === "cyan" ? "grad-text" : grad === "green" ? "grad-text-green" : grad === "red" ? "grad-text-red" : "";
  return (
    <div className="relative p-3.5 border-r border-border/20 last:border-r-0 flex flex-col gap-1 min-w-0 transition-colors hover:bg-white/[0.03]">
      {icon && (
        <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
          {icon}
        </span>
      )}
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={cn("text-lg font-semibold font-mono tracking-tight leading-tight", gradCls)}>{value}</div>
      {delta && (
        bad ? (
          <span className="inline-flex items-center gap-1 text-[9px] font-mono text-warning">
            {delta}
            <span className="px-1 py-px rounded-sm bg-warning/15 border border-warning/30 text-warning text-[8px]">数据异常</span>
          </span>
        ) : (
          <div className={cn("text-[9px] font-mono", CELL_COLORS[deltaColor] || "")}>{delta}</div>
        )
      )}
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

// ═══ 小节标题（SECTION · 渐变指示条 + 右侧操作区） ═══

export function SectionHeader({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between mt-2.5 mb-1.5">
      <span className="flex items-center gap-1.5 text-[9px] font-medium text-muted-foreground uppercase tracking-widest">
        <span className="w-[3px] h-3 rounded-r bg-gradient-to-b from-cyan-400 to-violet-500 shadow-[0_0_6px_rgba(34,211,238,0.5)]" />
        {title}
      </span>
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

// ═══ 空态（Aurora 虚线框 + 渐变图标位） ═══

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
    <div className="flex flex-col items-center justify-center gap-2.5 py-10 text-center">
      <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/25 flex items-center justify-center">
        <span className="text-cyan-300 text-lg leading-none">α</span>
      </div>
      <div className="text-sm text-muted-foreground">{title}</div>
      {description && <div className="text-xs text-muted-foreground/70 max-w-xs">{description}</div>}
      {action}
    </div>
  );
}
