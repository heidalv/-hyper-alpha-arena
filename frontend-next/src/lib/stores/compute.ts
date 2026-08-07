/**
 * compute store — 算力中心共享状态（zustand）
 *
 * 状态项：
 *   - timeWindow   图表时间范围（1h/24h/7d/30d）
 *   - gpuAlerts    GPU 告警集（温度>83°C / 功耗>90% / 显存<512MB / 后端 alerts）
 *   - torchDegraded / torchBroken / torchInstallHint  torch 环境状态（页头黄徽章）
 *   - drillTaskId  任务耗时图下钻点选 id
 *   - runningJobs  手动操作中的 job 列表
 */
import { create } from "zustand";

export type TimeWindow = "1h" | "24h" | "7d" | "30d";

export interface GpuAlert {
  severity: "warn" | "danger";
  message: string;
}

interface ComputeState {
  timeWindow: TimeWindow;
  gpuAlerts: GpuAlert[];
  torchDegraded: boolean;
  torchBroken: boolean;
  torchInstallHint: string;
  drillTaskId: string | number | null;
  runningJobs: Array<string | number>;

  setTimeWindow: (win: TimeWindow) => void;
  updateAlerts: (hardware: HardwareInput | null) => void;
  setTorchEnv: (probe: TorchProbe | null) => void;
  setDrillTask: (id: string | number | null) => void;
  addRunningJob: (id: string | number) => void;
  removeRunningJob: (id: string | number) => void;
}

interface HardwareInput {
  gpu?: {
    available?: boolean;
    temp_c?: number;
    power_w?: number;
    power_limit_w?: number;
    mem_free_mb?: number;
    alerts?: Array<{ severity: string; message: string }>;
  };
}

interface TorchProbe {
  available: boolean;
  broken?: boolean;
  install_hint?: string;
}

export const useComputeStore = create<ComputeState>((set) => ({
  timeWindow: "24h",
  gpuAlerts: [],
  torchDegraded: false,
  torchBroken: false,
  torchInstallHint: "",
  drillTaskId: null,
  runningJobs: [],

  setTimeWindow: (win) => set({ timeWindow: win }),

  updateAlerts: (hardware) => {
    const alerts: GpuAlert[] = [];
    const gpu = hardware?.gpu;
    if (gpu?.available) {
      if (gpu.temp_c != null && gpu.temp_c > 83) {
        alerts.push({ severity: "danger", message: `GPU 温度 ${gpu.temp_c}°C 超过 83°C 阈值` });
      }
      if (gpu.power_w != null && gpu.power_limit_w && gpu.power_w / gpu.power_limit_w > 0.9) {
        alerts.push({ severity: "warn", message: `GPU 功耗 ${gpu.power_w}W 超限 90%` });
      }
      if (gpu.mem_free_mb != null && gpu.mem_free_mb < 512) {
        alerts.push({ severity: "danger", message: `GPU 空闲显存 ${gpu.mem_free_mb}MB 低于 512MB` });
      }
      for (const a of gpu.alerts ?? []) {
        alerts.push({ severity: a.severity === "error" ? "danger" : "warn", message: a.message });
      }
    }
    set({ gpuAlerts: alerts });
  },

  setTorchEnv: (probe) =>
    set({
      torchDegraded: !probe?.available,
      torchBroken: Boolean(probe?.broken),
      torchInstallHint: probe?.install_hint || "",
    }),

  setDrillTask: (id) => set({ drillTaskId: id }),

  addRunningJob: (id) => set((s) => (s.runningJobs.includes(id) ? s : { runningJobs: [...s.runningJobs, id] })),

  removeRunningJob: (id) => set((s) => ({ runningJobs: s.runningJobs.filter((j) => j !== id) })),
}));
