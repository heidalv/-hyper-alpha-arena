"use client";

/**
 * 算力中心页头 — 标题 + 副文案 + GPU 告警横幅 + torch 降级徽章
 */
import { Cpu, AlertTriangle, ShieldAlert } from "lucide-react";
import { useComputeStore } from "@/lib/stores/compute";
import { cn } from "@/lib/utils";

export function ComputeHeader() {
  const gpuAlerts = useComputeStore((s) => s.gpuAlerts);
  const torchDegraded = useComputeStore((s) => s.torchDegraded);
  const torchBroken = useComputeStore((s) => s.torchBroken);
  const torchInstallHint = useComputeStore((s) => s.torchInstallHint);

  const danger = gpuAlerts.filter((a) => a.severity === "danger");
  const warn = gpuAlerts.filter((a) => a.severity === "warn");

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h1 className="text-lg font-bold flex items-center gap-2 tracking-tight">
            <Cpu className="w-5 h-5 text-primary flex-shrink-0" />
            算力中心
          </h1>
          <p className="text-xs text-muted-foreground pl-7">
            硬件状态 · 训练任务 · 本地推理 · 资源趋势
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {torchDegraded && (
            <span
              className={cn(
                "flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border max-w-[28rem]",
                torchBroken
                  ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              )}
              title={torchInstallHint || undefined}
            >
              <ShieldAlert className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="truncate">
                torch {torchBroken ? "损坏" : "降级"}
                {torchInstallHint ? (
                  <span className="opacity-80 hidden lg:inline"> · {torchInstallHint}</span>
                ) : null}
              </span>
            </span>
          )}
        </div>
      </div>

      {(danger.length > 0 || warn.length > 0) && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm",
            danger.length > 0
              ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
              : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
          )}
        >
          <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div className="space-y-0.5 min-w-0">
            {[...danger, ...warn].map((a, i) => (
              <p key={i} className="text-xs leading-relaxed">
                {a.message}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
