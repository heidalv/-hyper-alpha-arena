"use client";

/**
 * 算力中心（第十章）
 *
 * 信息架构：4 Tab 降维，避免 6 卡同屏堆叠
 *  - 硬件概览：实时资源 + torch 环境
 *  - 训练与进化：元标签 / 信号融合 / GP·MCTS
 *  - 服务与检索：本地 LLM / RAG / QAA
 *  - 趋势与审计：历史图表 + 开源框架审计
 */
import { useState, type ComponentType } from "react";
import {
  Activity,
  Boxes,
  Cpu,
  FlaskConical,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ComputeHeader } from "@/components/compute/ComputeHeader";
import { HardwareOverviewCard } from "@/components/compute/HardwareOverviewCard";
import { TrainingCard } from "@/components/compute/TrainingCard";
import { ParallelBacktestCard } from "@/components/compute/ParallelBacktestCard";
import { BatchTasksCard } from "@/components/compute/BatchTasksCard";
import { EmbeddingCard } from "@/components/compute/EmbeddingCard";
import { OSSIntegrationCard } from "@/components/compute/OSSIntegrationCard";
import { ComputeCharts } from "@/components/compute/ComputeCharts";
import { SectionLabel } from "@/components/compute/common";

type Tab = "overview" | "train" | "services" | "trends";

const TABS: {
  key: Tab;
  label: string;
  icon: ComponentType<{ className?: string }>;
}[] = [
  { key: "overview", label: "硬件概览", icon: Cpu },
  { key: "train", label: "训练与进化", icon: FlaskConical },
  { key: "services", label: "服务与检索", icon: Server },
  { key: "trends", label: "趋势与审计", icon: Activity },
];

export default function ComputePage() {
  const [tab, setTab] = useState<Tab>("overview");

  return (
    <div className="p-4 md:p-6 space-y-5 max-w-6xl mx-auto">
      <ComputeHeader />

      <div
        role="tablist"
        aria-label="算力中心分区"
        className="flex items-center gap-1 flex-wrap border-b border-border/50 pb-2"
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors",
              tab === t.key
                ? "bg-primary/15 text-primary font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-3">
          <SectionLabel title="实时硬件" hint="GPU · CPU · 内存 · 磁盘 · CUDA 环境" />
          <HardwareOverviewCard />
        </div>
      )}

      {tab === "train" && (
        <div className="space-y-3">
          <SectionLabel title="训练与进化" hint="模型训练与因子挖掘并行配置" />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-stretch">
            <TrainingCard />
            <ParallelBacktestCard />
          </div>
        </div>
      )}

      {tab === "services" && (
        <div className="space-y-3">
          <SectionLabel title="服务与检索" hint="本地推理与向量检索状态" />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-stretch">
            <BatchTasksCard />
            <EmbeddingCard />
          </div>
        </div>
      )}

      {tab === "trends" && (
        <div className="space-y-4">
          <div className="space-y-3">
            <SectionLabel title="历史趋势" hint="资源占用 · 任务耗时 · 成功率" />
            <ComputeCharts />
          </div>
          <div className="space-y-3">
            <SectionLabel
              title="开源框架"
              hint="集成审计（静态）"
            />
            <div className="flex items-start gap-2 text-[11px] text-muted-foreground px-0.5">
              <Boxes className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              仅展示实机核验结论，不代表在线开关
            </div>
            <OSSIntegrationCard />
          </div>
        </div>
      )}
    </div>
  );
}
