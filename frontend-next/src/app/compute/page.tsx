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
  AlertTriangle,
  Boxes,
  Cpu,
  FlaskConical,
  Server,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";
import { useComputeStore } from "@/lib/stores/compute";
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

/** torch 环境徽章 + GPU 告警横幅（ComputeHeader 迁入 Aurora 页头体系，保留全部状态） */
function ComputeAlerts() {
  const gpuAlerts = useComputeStore((s) => s.gpuAlerts);
  const torchDegraded = useComputeStore((s) => s.torchDegraded);
  const torchBroken = useComputeStore((s) => s.torchBroken);
  const torchInstallHint = useComputeStore((s) => s.torchInstallHint);

  const danger = gpuAlerts.filter((a) => a.severity === "danger");
  const warn = gpuAlerts.filter((a) => a.severity === "warn");

  return (
    <>
      {torchDegraded && (
        <span
          className={cn(
            "flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border max-w-[28rem]",
            torchBroken
              ? "border-red-500/40 bg-red-500/10 text-red-400"
              : "border-amber-500/40 bg-amber-500/10 text-amber-400"
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
      {(danger.length > 0 || warn.length > 0) && (
        <div
          className={cn(
            "flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm",
            danger.length > 0
              ? "border-red-500/40 bg-red-500/10 text-red-400"
              : "border-amber-500/40 bg-amber-500/10 text-amber-400"
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
    </>
  );
}

export default function ComputePage() {
  const [tab, setTab] = useState<Tab>("overview");
  const gpuAlerts = useComputeStore((s) => s.gpuAlerts);
  const engineDanger = gpuAlerts.some((a) => a.severity === "danger");
  const engineWarn = gpuAlerts.some((a) => a.severity === "warn");

  return (
    <div className="p-4 md:p-6 space-y-5">
      <PageHeader
        icon={<Cpu className="w-4 h-4" />}
        title="算力中心"
        badge={<ComputeAlerts />}
        subtitle="本地推理 · 训练进化 · 向量检索"
        breadcrumb={[{ label: "市场 & 分析" }, { label: "算力中心" }]}
        refreshHint="硬件 5s 采样"
        actions={
          <span
            className={cn(
              "flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md border",
              engineDanger
                ? "border-red-500/40 bg-red-500/10 text-red-400"
                : engineWarn
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-400"
                  : "border-profit/40 bg-profit/10 text-profit"
            )}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                engineDanger ? "bg-loss" : engineWarn ? "bg-warning" : "bg-profit"
              )}
            />
            {engineDanger ? "引擎告警" : engineWarn ? "引擎降级" : "引擎在线"}
          </span>
        }
      />

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
              "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-transparent transition-colors",
              tab === t.key
                ? "bg-gradient-to-r from-cyan-400/15 to-violet-500/15 border-cyan-400/30 text-cyan-300 font-medium"
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
            <div className="flex items-start gap-2 text-xs text-muted-foreground px-0.5">
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
