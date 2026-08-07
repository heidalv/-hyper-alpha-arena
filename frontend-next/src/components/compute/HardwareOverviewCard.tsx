"use client";

/**
 * 硬件资源总览卡（第十章 10.2）
 *  - GpuGauge      温度/功耗/显存/利用率环形仪表（阈值告警色）
 *  - CpuMemDiskBars 16C/32T 负载、128GB 内存、磁盘 C/D 剩余
 *  - TorchEnvBadge cu124 环境检测：版本/可用/显存预算（损坏如实 degraded）
 *
 * 数据源：
 *  - GET /api/compute/hardware（3s 轮询，后端 3s TTL 缓存）
 *  - GET /api/compute/gpu-env（30s 轮询）
 */
import { useEffect } from "react";
import { Gauge as GaugeIcon, HardDrive, MemoryStick, Cpu } from "lucide-react";
import {
  getHardware,
  getGpuEnv,
  type HardwareSnapshot,
} from "@/lib/api/compute";
import { useComputeStore } from "@/lib/stores/compute";
import {
  ComputePanel,
  GaugeRing,
  LoadingBox,
  PanelError,
  RefreshButton,
  fmtNum,
  fmtPct,
  usePolling,
} from "./common";
import { cn } from "@/lib/utils";

// ───────────────────────────── GPU 环形仪表 ─────────────────────────────

function GpuGauges({ hw }: { hw: HardwareSnapshot | null }) {
  const gpu = hw?.gpu;
  const memTotal = gpu?.mem_total_mb ?? 0;
  const memUsed = gpu?.mem_used_mb ?? 0;
  const memRatio = memTotal > 0 ? (memUsed / memTotal) * 100 : null;
  const powerRatio =
    gpu?.power_w != null && gpu?.power_limit_w
      ? (gpu.power_w / gpu.power_limit_w) * 100
      : null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <GaugeRing
        value={gpu?.temp_c}
        max={110}
        label="温度 (°C)"
        unit="°"
        threshold={83}
      />
      <GaugeRing
        value={powerRatio}
        label="功耗占比"
        threshold={90}
      />
      <GaugeRing
        value={memRatio}
        label="显存占用"
        threshold={90}
      />
      <GaugeRing
        value={gpu?.utilization_pct}
        label="利用率"
      />
    </div>
  );
}

// ───────────────────────────── CPU/内存/磁盘条 ─────────────────────────────

function MetricBar({
  label,
  percent,
  detail,
  dangerThreshold = 90,
}: {
  label: string;
  percent: number | null | undefined;
  detail: string;
  dangerThreshold?: number;
}) {
  const p = typeof percent === "number" && isFinite(percent) ? percent : 0;
  const danger = p > dangerThreshold;
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums">
          {detail}
          <span className={cn("ml-2 font-medium", danger && "text-red-500")}>
            {fmtPct(percent)}
          </span>
        </span>
      </div>
      <div className="w-full h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            danger ? "bg-red-500" : p > 70 ? "bg-amber-500" : "bg-green-500"
          )}
          style={{ width: `${Math.min(100, p)}%` }}
        />
      </div>
    </div>
  );
}

function CpuMemDiskBars({ hw }: { hw: HardwareSnapshot | null }) {
  const cpu = hw?.cpu;
  const mem = hw?.memory;
  const disks = hw?.disk?.disks ?? [];

  return (
    <div className="space-y-3 mt-3">
      <MetricBar
        label="CPU 负载"
        percent={cpu?.usage_pct}
        detail={`${cpu?.physical_cores ?? "—"}C/${cpu?.logical_cores ?? "—"}T`}
      />
      <MetricBar
        label="内存"
        percent={mem?.usage_pct}
        detail={`${fmtNum(mem?.used_gb)}G / ${fmtNum(mem?.total_gb)}G`}
      />
      {disks.map((d) => (
        <MetricBar
          key={d.mount}
          label={`磁盘 ${d.mount}`}
          percent={d.usage_pct}
          detail={`剩余 ${fmtNum(d.free_gb)}G / ${fmtNum(d.total_gb)}G`}
          dangerThreshold={95}
        />
      ))}
    </div>
  );
}

// ───────────────────────────── torch 环境徽章 ─────────────────────────────

function TorchEnvBadge() {
  const { data, loading, error, refresh } = usePolling(getGpuEnv, 30000);
  const setTorchEnv = useComputeStore((s) => s.setTorchEnv);

  useEffect(() => {
    setTorchEnv(data);
  }, [data, setTorchEnv]);

  const probe = data;
  return (
    <div className="mt-3 rounded-md border border-border bg-muted/30 px-3 py-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium flex items-center gap-1.5">
          <GaugeIcon className="w-3.5 h-3.5 text-muted-foreground" />
          torch / CUDA 环境
        </span>
        <span
          className={cn(
            "px-1.5 py-0.5 rounded text-[11px] border",
            probe?.available
              ? "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400"
              : probe?.broken
                ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
                : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
          )}
        >
          {loading && !probe
            ? "探测中…"
            : probe?.available
              ? "可用"
              : probe?.broken
                ? "损坏"
                : "不可用"}
        </span>
      </div>
      <div className="mt-1.5 text-[11px] text-muted-foreground space-y-0.5">
        <p>
          版本：{probe?.version ?? "—"}｜CUDA：{probe?.cuda_available ? "可用" : "不可用"}
          {probe?.cuda_version ? ` (${probe.cuda_version})` : ""}
          {probe?.device_name ? `｜设备：${probe.device_name}` : ""}
        </p>
        {probe?.error && <p className="text-red-500">{probe.error}</p>}
        {probe?.install_hint && (
          <p className="text-amber-600 dark:text-amber-400">{probe.install_hint}</p>
        )}
      </div>
      {error && <p className="text-[11px] text-red-500 mt-1">探测失败：{error}</p>}
      <div className="mt-1.5 flex justify-end">
        <button
          onClick={refresh}
          className="text-[11px] text-primary hover:underline disabled:opacity-50"
          disabled={loading}
        >
          重新探测
        </button>
      </div>
    </div>
  );
}

// ───────────────────────────── 主卡 ─────────────────────────────

export function HardwareOverviewCard() {
  const { data, loading, error, refresh } = usePolling(getHardware, 3000);
  const updateAlerts = useComputeStore((s) => s.updateAlerts);
  const gpuAlerts = useComputeStore((s) => s.gpuAlerts);

  useEffect(() => {
    updateAlerts(data);
  }, [data, updateAlerts]);

  const hw = data;
  const gpu = hw?.gpu;
  const status = !hw ? undefined : gpu?.available ? "ok" : "degraded";

  return (
    <ComputePanel
      title="硬件资源总览"
      description="GPU/CPU/内存/磁盘实时采样（后端 3s TTL 快照）"
      status={gpuAlerts.some((a) => a.severity === "danger") ? "error" : status}
      action={<RefreshButton onClick={refresh} loading={loading} />}
      className={cn(
        gpuAlerts.some((a) => a.severity === "danger") &&
          "border-red-500/50 ring-1 ring-red-500/20"
      )}
    >
      <PanelError error={error || hw?.error || null} />
      {loading && !hw ? (
        <LoadingBox text="采样硬件信息…" />
      ) : (
        <>
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-2">
            <span className="flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5" />
              {gpu?.name ?? "未检测到 GPU"}
              {gpu?.driver ? `｜驱动 ${gpu.driver}` : ""}
            </span>
            <span>{gpu?.health ? `健康：${gpu.health}` : ""}</span>
          </div>

          <GpuGauges hw={hw} />

          <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <MemoryStick className="w-3.5 h-3.5" />
            显存：已用 {fmtNum(gpu?.mem_used_mb)}MB / 共 {fmtNum(gpu?.mem_total_mb)}MB
            {gpu?.mem_available_budget_mb != null && (
              <span>｜可用预算 {fmtNum(gpu.mem_available_budget_mb)}MB</span>
            )}
            <HardDrive className="w-3.5 h-3.5 ml-2" />
            功耗：{gpu?.power_w != null ? `${gpu.power_w}W` : "—"}
            {gpu?.power_limit_w ? ` / ${gpu.power_limit_w}W` : ""}
          </div>

          <CpuMemDiskBars hw={hw} />
          <TorchEnvBadge />
        </>
      )}
    </ComputePanel>
  );
}
