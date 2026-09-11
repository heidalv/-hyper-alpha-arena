"use client";

/**
 * Aurora 确认框容器
 * - 玻璃卡片 + 遮罩，ESC 取消 / Enter 确认
 * - 危险操作（tone=danger）用 --loss 强调
 * - requireText：必须原样输入确认词才能点确认（实盘下单等）
 * - 带 role="dialog" + aria-modal，打开时焦点落在取消按钮（危险操作默认不聚焦确认）
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { useConfirmStore, type ConfirmRequest } from "@/lib/confirm";

const TONE: Record<string, { ring: string; btn: string; icon: string; Icon: typeof AlertTriangle }> = {
  danger: {
    ring: "border-loss/35",
    btn: "bg-loss text-[#2B0A12] hover:brightness-110",
    icon: "text-loss bg-loss/15 border-loss/30",
    Icon: ShieldAlert,
  },
  warning: {
    ring: "border-warning/35",
    btn: "bg-warning text-[#2B1A02] hover:brightness-110",
    icon: "text-warning bg-warning/15 border-warning/30",
    Icon: AlertTriangle,
  },
  primary: {
    ring: "border-cyan-400/30",
    btn: "bg-gradient-to-br from-cyan-400 to-violet-500 text-[#041018] hover:brightness-110",
    icon: "text-cyan-300 bg-cyan-400/15 border-cyan-400/25",
    Icon: AlertTriangle,
  },
};

export function ConfirmDialog() {
  const current = useConfirmStore((s) => s.current);
  if (!current) return null;
  // key=id：每次新请求都重新挂载，typed 等本地状态自然归零（避免在 effect 里 setState）
  return <ConfirmBody key={current.id} req={current} />;
}

function ConfirmBody({ req }: { req: ConfirmRequest }) {
  const settle = useConfirmStore((s) => s.settle);
  const [typed, setTyped] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const needText = req.requireText;
  const textOk = !needText || typed.trim() === needText;

  useEffect(() => {
    // 危险操作默认聚焦输入框/取消按钮，避免回车误确认
    if (needText) inputRef.current?.focus();
    else cancelRef.current?.focus();
  }, [needText]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        settle(false);
      } else if (e.key === "Enter" && !needText) {
        e.preventDefault();
        settle(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [needText, settle]);

  const tone = TONE[req.tone ?? "primary"] ?? TONE.primary;
  const Icon = tone.Icon;

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
        onClick={() => settle(false)}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="arena-confirm-title"
        className={cn(
          "relative w-full max-w-md rounded-xl border bg-popover/97 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.65)] backdrop-blur-md",
          tone.ring
        )}
      >
        <div className="flex items-start gap-3">
          <span className={cn("flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border", tone.icon)}>
            <Icon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="arena-confirm-title" className="text-sm font-semibold leading-relaxed">
              {req.title}
            </h2>
            {req.description && (
              <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                {req.description}
              </p>
            )}

            {needText && (
              <div className="mt-3">
                <label className="block text-[11px] text-muted-foreground">
                  请输入 <span className="font-mono text-warning">{needText}</span> 以确认
                </label>
                <input
                  ref={inputRef}
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  className="mt-1.5 h-8 w-full rounded-md border border-border bg-input px-2.5 font-mono text-xs text-foreground outline-none focus:border-cyan-400/60"
                  placeholder={needText}
                />
              </div>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                ref={cancelRef}
                type="button"
                onClick={() => settle(false)}
                className="h-8 rounded-md border border-border px-3 text-xs text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
              >
                {req.cancelText}
              </button>
              <button
                type="button"
                disabled={!textOk}
                onClick={() => settle(true)}
                className={cn(
                  "h-8 rounded-md px-3.5 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40",
                  tone.btn
                )}
              >
                {req.confirmText}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
