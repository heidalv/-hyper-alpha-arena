"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * 全站统一页头（Aurora 设计系统）
 * 标题 + 可选图标/徽章 + 副标题 + 面包屑 + 右侧操作区 + 自动刷新倒计时
 */
export function PageHeader({
  title,
  subtitle,
  icon,
  badge,
  actions,
  refreshHint,
  breadcrumb,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  /** 如 "10s 轮询" —— 自动解析秒数并实时倒计时（设计稿 .cd-auto） */
  refreshHint?: string;
  /** 面包屑：最后一项为当前页 */
  breadcrumb?: { label: string; href?: string }[];
  className?: string;
}) {
  // 解析 refreshHint 中的秒数（"10s 轮询" → 10），驱动活倒计时
  const seconds = refreshHint ? (refreshHint.match(/(\d+)s/) ?? [])[1] : undefined;
  const [left, setLeft] = useState<number | null>(seconds ? Number(seconds) : null);

  useEffect(() => {
    if (!seconds) return;
    const total = Number(seconds);
    const id = setInterval(() => {
      setLeft((v) => (v == null || v <= 1 ? total : v - 1));
    }, 1000);
    return () => clearInterval(id);
  }, [seconds]);

  return (
    <div className={cn("flex items-start justify-between gap-4 mb-4", className)}>
      <div className="min-w-0">
        {breadcrumb && breadcrumb.length > 0 && (
          <nav className="flex items-center gap-1.5 mb-1.5 text-[11px] text-slate-500" aria-label="面包屑">
            {breadcrumb.map((c, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && <span className="text-slate-700">/</span>}
                {c.href && i < breadcrumb.length - 1 ? (
                  <a href={c.href} className="text-slate-400 hover:text-cyan-300 transition-colors">
                    {c.label}
                  </a>
                ) : (
                  <span className={i === breadcrumb.length - 1 ? "text-slate-300" : ""}>{c.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}
        <h1 className="flex items-center gap-2.5 text-lg font-bold tracking-tight">
          {icon && (
            <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300 flex-shrink-0">
              {icon}
            </span>
          )}
          <span className="truncate">{title}</span>
          {badge}
        </h1>
        {subtitle && <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>}
        {refreshHint && (
          <p className="mt-0.5 text-[10px] font-mono flex items-center gap-1.5">
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                left != null && left <= 5
                  ? "bg-loss shadow-[0_0_4px_rgba(251,113,133,0.8)] animate-pulse"
                  : left != null && left <= 10
                    ? "bg-warning shadow-[0_0_4px_rgba(251,191,36,0.8)]"
                    : "bg-cyan-400 shadow-[0_0_4px_rgba(34,211,238,0.8)]"
              )}
            />
            {left != null ? (
              <span className={cn(left <= 5 ? "text-loss" : left <= 10 ? "text-warning" : "text-muted-foreground/80")}>
                {refreshHint.replace(/\d+s/, `${left}s`)}
              </span>
            ) : (
              <span className="text-muted-foreground/80">{refreshHint}</span>
            )}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">{actions}</div>}
    </div>
  );
}
