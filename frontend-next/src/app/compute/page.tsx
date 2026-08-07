"use client";

/**
 * 算力中心（第十章）
 *
 * 布局：顶部标题/全局状态条 → 6 张功能卡 2 列网格 → 底部历史趋势区（3 图横排）
 * 数据流：react 轮询（usePolling）→ GET /api/compute/* → 卡片渲染；
 *         操作（训练/触发/重建/配置下发）→ POST/PUT → 回显结果；
 *         告警：GPU 温度>83°C/功耗>90%/显存<512MB → 顶部横幅（zustand 联动）。
 */
import { ComputeHeader } from "@/components/compute/ComputeHeader";
import { HardwareOverviewCard } from "@/components/compute/HardwareOverviewCard";
import { TrainingCard } from "@/components/compute/TrainingCard";
import { ParallelBacktestCard } from "@/components/compute/ParallelBacktestCard";
import { BatchTasksCard } from "@/components/compute/BatchTasksCard";
import { EmbeddingCard } from "@/components/compute/EmbeddingCard";
import { OSSIntegrationCard } from "@/components/compute/OSSIntegrationCard";
import { ComputeCharts } from "@/components/compute/ComputeCharts";

export default function ComputePage() {
  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      <ComputeHeader />

      {/* 6 张功能卡 2 列网格 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        <HardwareOverviewCard />
        <TrainingCard />
        <ParallelBacktestCard />
        <BatchTasksCard />
        <EmbeddingCard />
        <OSSIntegrationCard />
      </div>

      {/* 历史趋势区 */}
      <ComputeCharts />
    </div>
  );
}
