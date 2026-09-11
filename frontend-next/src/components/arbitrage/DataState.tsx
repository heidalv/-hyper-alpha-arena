"use client";

/**
 * DataState — 数据块四态包装（loading / error / empty / stale）
 *
 * 设计 §4「状态规范」：每个数据块必须有 loading（骨架）/ error（原因+重试）/
 * stale（时间戳+灰化）/ empty（引导）。本组件统一处理，避免每页重复。
 *
 * 规则：
 *  - 首屏（无数据 + loading）→ 骨架
 *  - 失败且无数据 → 原因 + 重试按钮
 *  - 空且无数据 → 引导文案
 *  - 有数据 → 渲染内容；stale 时灰化；若同时失败则先显示紧凑错误条
 */
import type { ReactNode } from "react";
import { Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

interface DataStateProps {
  loading: boolean;
  error: string | null;
  hasData: boolean;
  /** 是否为「空」状态（由调用方按数据内容判定） */
  empty?: boolean;
  emptyHint?: ReactNode;
  /** 数据年龄是否已过期（>90s）→ 灰化内容 */
  stale?: boolean;
  onRetry?: () => void;
  children: ReactNode;
  /** 自定义骨架（默认转圈） */
  skeleton?: ReactNode;
  className?: string;
}

export function DataState({
  loading,
  error,
  hasData,
  empty,
  emptyHint,
  stale,
  onRetry,
  children,
  skeleton,
  className,
}: DataStateProps) {
  // 首屏加载（无数据）→ 骨架
  if (loading && !hasData) {
    return (
      <div className={cn("flex min-h-[3.25rem] items-center justify-center", className)}>
        {skeleton ?? <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
      </div>
    );
  }

  // 失败且无数据 → 原因 + 重试
  if (error && !hasData) {
    return (
      <div className={cn("flex items-start gap-2 rounded-lg border border-loss/30 bg-loss/10 px-3 py-2.5 text-xs text-loss", className)}>
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
        <div className="min-w-0 flex-1">
          <div>加载失败：{error}</div>
          {onRetry && (
            <button type="button" onClick={onRetry} className="mt-1 underline underline-offset-2 hover:opacity-80">
              重试
            </button>
          )}
        </div>
      </div>
    );
  }

  // 空且无数据 → 引导
  if (empty && !hasData) {
    return (
      <div className={cn("flex flex-col items-center justify-center gap-2 py-8 text-center text-sm text-muted-foreground", className)}>
        {emptyHint ?? "暂无数据"}
      </div>
    );
  }

  // 有数据（或部分数据）→ 内容；stale 灰化；失败时先显示紧凑错误条
  return (
    <div className={className}>
      {error && (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
          <span className="min-w-0 flex-1">刷新失败：{error}</span>
          {onRetry && (
            <button type="button" onClick={onRetry} className="underline underline-offset-2 hover:opacity-80">
              重试
            </button>
          )}
        </div>
      )}
      <div className={cn(stale && "opacity-55 saturate-50 transition-opacity")}>{children}</div>
    </div>
  );
}
