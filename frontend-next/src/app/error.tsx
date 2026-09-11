"use client";

/**
 * 路由级错误边界（Next.js 16：props 为 error + unstable_retry + reset）
 *
 * 背景：此前全站没有任何 error.tsx / global-error.tsx，任一页面渲染抛错
 * 都会让整个客户端树白屏（shell 一起消失），用户只看到空白。
 * 本文件位于 app/ 根，包裹所有页面但不包裹 root layout，
 * 因此出错时侧栏/顶栏仍在，只替换主内容区。
 */
import { useEffect } from "react";
import { AlertTriangle, RotateCw, Home } from "lucide-react";

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    // 保留原始堆栈，便于从浏览器控制台/Electron 日志回溯
    console.error("[arena] 页面渲染异常:", error);
  }, [error]);

  return (
    <div className="flex h-full min-h-[320px] items-center justify-center p-6">
      <div className="glass w-full max-w-lg rounded-xl border border-border p-6">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-warning/30 bg-warning/15 text-warning">
            <AlertTriangle className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold tracking-tight">此页面渲染出错</h2>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              后端与交易会话未受影响，其他页面仍可正常使用。可先重试；若持续报错请到
              <a href="/ops#ops-errors" className="mx-1 text-cyan-300 underline-offset-2 hover:underline">
                报错中心
              </a>
              查看详情。
            </p>

            <div className="mt-3 overflow-hidden rounded-lg border border-border/60 bg-black/25">
              <div className="border-b border-border/40 px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                {error.name || "Error"}
                {error.digest ? ` · digest ${error.digest}` : ""}
              </div>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-loss">
                {error.message || String(error)}
              </pre>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => unstable_retry()}
                className="btn-glow inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium"
              >
                <RotateCw className="h-3.5 w-3.5" />
                重试
              </button>
              <a
                href="/dashboard"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-muted-foreground transition-colors hover:bg-white/[0.06] hover:text-foreground"
              >
                <Home className="h-3.5 w-3.5" />
                回到仪表盘
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
