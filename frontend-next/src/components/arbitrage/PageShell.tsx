"use client";

/**
 * PageShell — 页面头（标题 + 模式徽章 + 数据年龄 + 刷新按钮）+ 内容
 *
 * 设计 §3.1/§3.2：每个视图顶部统一展示「套利中心 · <视图名>」、通道模式（ws/轮询中）、
 * 数据年龄、手动刷新。复用 Aurora `PageHeader` 保持一致语言。
 */
import type { ReactNode } from "react";
import { RefreshCw, Loader2, Radio, WifiOff } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataAgeBadge } from "./DataAgeBadge";
import { cn } from "@/lib/utils";

export function PageShell({
  title,
  subtitle,
  icon,
  mode,
  asOf,
  onRefresh,
  refreshing,
  breadcrumb,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  /** 通道模式：实时 或 轮询中（不传则不显示徽章） */
  mode?: "ws" | "polling";
  asOf?: string | null;
  onRefresh?: () => void;
  refreshing?: boolean;
  breadcrumb?: { label: string; href?: string }[];
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const streamBadge = mode ? (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        mode === "ws" ? "border-profit/30 bg-profit/10 text-profit" : "border-warning/25 bg-warning/10 text-warning"
      )}
    >
      {mode === "ws" ? <Radio className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
      {mode === "ws" ? "实时" : "轮询中"}
    </span>
  ) : null;

  const refreshBtn = onRefresh && (
    <button
      type="button"
      onClick={onRefresh}
      disabled={refreshing}
      className="inline-flex h-7 items-center gap-1 rounded-md border border-border/40 px-2 text-xs text-muted-foreground transition-colors hover:border-cyan-400/30 hover:text-cyan-300 disabled:opacity-50"
    >
      {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
      刷新
    </button>
  );

  const ageBadge = asOf ? <DataAgeBadge asOf={asOf} /> : null;

  return (
    <div className={cn("p-4", className)}>
      <PageHeader
        icon={icon}
        title={
          <>
            {title}
            {streamBadge && <span className="ml-0.5">{streamBadge}</span>}
          </>
        }
        subtitle={subtitle}
        breadcrumb={breadcrumb}
        actions={
          <>
            {ageBadge}
            {refreshBtn}
            {actions}
          </>
        }
      />
      {children && <div className="space-y-4">{children}</div>}
    </div>
  );
}
