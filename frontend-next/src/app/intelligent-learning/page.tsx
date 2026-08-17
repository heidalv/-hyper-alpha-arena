"use client";

/**
 * 智能学习中心（v6 9.2 主线：唯一学习进化前端入口）
 *
 * 7 Tab 信息架构：
 * - Wisdom 生命周期（五步流水 + 三率 + slot + 检索注入）
 * - 三通道健康看板（证据 / 参数 / 因子 + 闭环 5 job）
 * - 决策链路
 * - 选币反馈
 * - 检索与本地算力（RAG 状态 + 并行评估/本地模型）
 * - 学习血缘（旧 /evolution 迁入：ledger + 特性开关 + 事件流）
 * - 调度&日志（旧 /evolution 迁入：Hermes 调度 + 提示词同步 + 5 job tick）
 * 原则：数据只对接后端真实接口；断链/停摆/未部署一律如实展示，不做假数据。
 * 旧 /evolution 路由已删除（8 Tab 中 4 个为死通道，与本站重复）。
 */
import { useState } from "react";
import { Sparkles, GitBranch, ListTree, Coins, ServerCog, Workflow, Layers, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";
import { WisdomLifecyclePanel } from "@/components/learning/WisdomLifecyclePanel";
import { HealthChannelsPanel } from "@/components/learning/HealthChannelsPanel";
import { RAGStatusPanel } from "@/components/learning/RAGStatusPanel";
import { ComputeChannelsPanel } from "@/components/learning/ComputeChannelsPanel";
import LearningLineagePanel from "@/components/learning/LearningLineagePanel";
import ScheduleLogPanel from "@/components/learning/ScheduleLogPanel";
import { DecisionChainPanel } from "@/components/operations/DecisionChainPanel";
import { CoinFeedbackPanel } from "@/components/operations/CoinFeedbackPanel";

type Tab = "lifecycle" | "channels" | "decision" | "coin" | "compute" | "lineage" | "schedule";

const TABS: { key: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: "lifecycle", label: "Wisdom 生命周期", icon: Sparkles },
  { key: "channels", label: "三通道健康", icon: GitBranch },
  { key: "decision", label: "决策链路", icon: ListTree },
  { key: "coin", label: "选币反馈", icon: Coins },
  { key: "compute", label: "检索与算力", icon: ServerCog },
  { key: "lineage", label: "学习血缘", icon: Layers },
  { key: "schedule", label: "调度&日志", icon: Clock },
];

export default function IntelligentLearningPage() {
  const [tab, setTab] = useState<Tab>("lifecycle");

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Workflow className="w-4 h-4" />}
        title="智能学习中心"
        subtitle="唯一学习进化前端入口 · 只对接后端真实接口"
        breadcrumb={[{ label: "市场 & 分析" }, { label: "智能学习" }]}
        refreshHint="学习闭环 30s tick"
      />

      {/* Tab 导航 */}
      <div className="flex items-center gap-1 flex-wrap border-b border-border/50 pb-2">
        {TABS.map((t) => (
          <button
            key={t.key}
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

      {/* Tab 内容 */}
      {tab === "lifecycle" && <WisdomLifecyclePanel />}
      {tab === "channels" && <HealthChannelsPanel />}
      {tab === "decision" && <DecisionChainPanel />}
      {tab === "coin" && <CoinFeedbackPanel />}
      {tab === "compute" && (
        <div className="space-y-4">
          <RAGStatusPanel />
          <ComputeChannelsPanel />
        </div>
      )}
      {tab === "lineage" && <LearningLineagePanel />}
      {tab === "schedule" && <ScheduleLogPanel />}
    </div>
  );
}
