"use client";

/**
 * 算力中心页头 — 标题 + GPU 告警横幅 + torch 降级黄徽章
 *
 * 状态来源：zustand computeStore（由 HardwareOverviewCard 轮询硬件/gpu-env 后写入）
 * 数据源：GET /api/compute/hardware、GET /api/compute/gpu-env
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
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Cpu className="w-5 h-5 text-primary" />
          算力中心
        </h1>
        <div className="flex items-center gap-2">
          {torchDegraded && (
            <span
              className={cn(
                "flex items-center gap-1.5 text-xs px-2 py-1 rounded border",
                torchBroken
                  ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              )}
              title={torchInstallHint || undefined}
            >
              <ShieldAlert className="w-3.5 h-3.5" />
              torch 环境{torchBroken ? "损坏" : "降级"}
              {torchInstallHint && (
                <span className="text-[10px] opacity-80 hidden md:inline">
                  {torchInstallHint}
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {(danger.length > 0 || warn.length > 0) && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
            danger.length > 0
              ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
              : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
          )}
        >
          <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div className="space-y-0.5">
            {[...danger, ...warn].map((a, i) => (
              <p key={i} className="text-xs">
                {a.message}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
