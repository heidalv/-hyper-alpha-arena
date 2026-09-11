"use client";

/**
 * Toast 容器（Aurora 玻璃卡片语言）
 * - 右下角堆叠，最多 4 条（store 内已限流）
 * - aria-live="polite"：错误/成功提示对读屏用户可达（补齐全站 aria-live 缺口）
 * - 点击关闭；错误类不自动消失前也提供关闭按钮
 */
import { CheckCircle2, AlertTriangle, Info, XCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToastStore, type ToastTone } from "@/lib/toast";

const TONE_STYLE: Record<ToastTone, { icon: typeof Info; cls: string; bar: string }> = {
  success: { icon: CheckCircle2, cls: "text-profit", bar: "bg-profit" },
  error: { icon: XCircle, cls: "text-loss", bar: "bg-loss" },
  warning: { icon: AlertTriangle, cls: "text-warning", bar: "bg-warning" },
  info: { icon: Info, cls: "text-cyan-300", bar: "bg-cyan-400" },
};

export function Toaster() {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,360px)] flex-col gap-2"
    >
      {items.map((t) => {
        const style = TONE_STYLE[t.tone];
        const Icon = style.icon;
        return (
          <div
            key={t.id}
            className="pointer-events-auto relative flex items-start gap-2.5 overflow-hidden rounded-xl border border-border bg-popover/95 px-3 py-2.5 shadow-[0_16px_48px_rgba(0,0,0,0.55)] backdrop-blur-md"
            role="status"
          >
            <span className={cn("absolute left-0 top-0 h-full w-[3px]", style.bar)} />
            <Icon className={cn("mt-0.5 h-4 w-4 flex-shrink-0", style.cls)} />
            <span className="min-w-0 flex-1 break-words text-xs leading-relaxed text-foreground">{t.message}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => dismiss(t.id)}
              className="-mr-0.5 -mt-0.5 flex-shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
