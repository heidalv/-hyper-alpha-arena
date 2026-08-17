/**
 * 并行评估与本地算力面板（v6 第十章 10.2.1/10.2.2/10.2.3 如实展示）
 *
 * - 10.2.2 并行评估通道：GP/MCTS 挖掘器 workers 配置生效值（/api/compute/evolution/status）
 * - 10.2.1 本地模型训练：未部署如实显示（torch CPU 版、无训练管线）
 * - 10.2.3 批量打标：无 ollama/llama.cpp，未落地如实显示
 */
"use client";

import { useEffect, useState } from "react";
import { getEvolutionStatus, getLlmStatus, type EvolutionStatus, type LlmStatus } from "@/lib/api/compute";
import { SectionCard, RefreshButton, StatCard } from "../operations/IlcUi";
import { cn } from "@/lib/utils";
import { Cpu, Gauge, HardDrive, Wrench, XCircle, CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function ComputeChannelsPanel() {
  const [evo, setEvo] = useState<EvolutionStatus | null>(null);
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    Promise.allSettled([getEvolutionStatus(), getLlmStatus()])
      .then(([e, l]) => {
        if (e.status === "fulfilled") setEvo(e.value);
        if (l.status === "fulfilled") setLlm(l.value);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const config = evo?.config ?? [];
  const gpWorkers = config.find((c) => c.key === "FACTOR_GP_MAX_WORKERS");
  const gpSeeds = config.find((c) => c.key === "FACTOR_GP_SEEDS");
  const mctsEnabled = config.find((c) => c.key === "FACTOR_MCTS_ENABLED");
  const mctsIterations = config.find((c) => c.key === "FACTOR_MCTS_ITERATIONS");

  return (
    <div className="space-y-4">
      <SectionCard
        title="并行评估通道（10.2.2）"
        description="GP/MCTS 挖掘器并行评估：joblib loky 进程池优先（实测 0.84x 的 ThreadPool 为次选）；配置生效值来自 /api/compute/evolution/status"
        action={<RefreshButton onClick={refresh} loading={loading} />}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <StatCard
            label="GP 并行评估"
            value={gpWorkers ? `${gpWorkers.value} workers` : "—"}
            tone="good"
            hint={gpWorkers?.desc ?? "joblib loky 进程数"}
          />
          <StatCard
            label="GP 种子数"
            value={gpSeeds ? `${gpSeeds.value}` : "—"}
            hint={gpSeeds?.desc ?? "幻方多种子方法论"}
          />
          <StatCard
            label="MCTS 挖掘"
            value={mctsEnabled ? (String(mctsEnabled.value) === "1" || mctsEnabled.value === true ? "已启用" : "已关闭") : "—"}
            tone={(String(mctsEnabled?.value) === "1" || mctsEnabled?.value === true) ? "good" : "warn"}
            hint={mctsEnabled?.desc}
          />
          <StatCard
            label="MCTS 迭代预算"
            value={mctsIterations ? `${mctsIterations.value}` : "—"}
            hint={mctsIterations?.desc}
          />
        </div>

        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">因子进化运行中</span>
          {evo?.running ? (
            <Badge className="font-normal border-profit/40 bg-profit/10 text-profit">
              <span className="w-1.5 h-1.5 rounded-full bg-profit shadow-[0_0_6px_currentColor]" />
              运行中
            </Badge>
          ) : (
            <Badge className="font-normal border-border/60 bg-muted/30 text-muted-foreground">
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
              空闲
            </Badge>
          )}
        </div>
        {evo?.last_error && (
          <p className="text-[10px] text-loss mt-1.5">上次错误：{evo.last_error}</p>
        )}
      </SectionCard>

      <SectionCard
        title="本地模型部署状态（10.2.1 / 10.2.3）"
        description="torch 2.6.0+cpu（CUDA 未启用）· 无 ollama/llama.cpp · 无训练/打标管线——未落地如实显示"
        action={null}
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* 10.2.1 本地模型训练 */}
          <div className="rounded-lg border border-loss/30 bg-loss/5 p-3 space-y-2">
            <div className="flex items-center gap-1.5">
              <HardDrive className="w-4 h-4 text-loss" />
              <span className="text-sm font-medium">本地模型训练（10.2.1）</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <XCircle className="w-3.5 h-3.5 text-loss" />
              <span className="text-loss font-medium">未部署</span>
            </div>
            <p className="text-[10px] text-muted-foreground">
              torch 2.6.0+cpu、CUDA 未启用；无训练管线与 checkpoint
            </p>
          </div>

          {/* 10.2.3 批量打标 */}
          <div className="rounded-lg border border-loss/30 bg-loss/5 p-3 space-y-2">
            <div className="flex items-center gap-1.5">
              <Wrench className="w-4 h-4 text-loss" />
              <span className="text-sm font-medium">批量打标（10.2.3）</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <XCircle className="w-3.5 h-3.5 text-loss" />
              <span className="text-loss font-medium">未落地</span>
            </div>
            <p className="text-[10px] text-muted-foreground">
              未安装 ollama / llama.cpp，无本地 LLM 打标服务；观察池 min_samples=3 无本地支撑
            </p>
          </div>

          {/* 本地 LLM 网关 */}
          <div className="glass rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-1.5">
              <Cpu className="w-4 h-4 text-muted-foreground" />
              <span className="text-sm font-medium">LLM 网关（compute）</span>
            </div>
            {llm ? (
              <div className="flex items-center gap-1.5 text-xs">
                {llm.enabled ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5 text-profit" />
                    <span className="text-profit font-medium">已配置</span>
                  </>
                ) : (
                  <>
                    <XCircle className="w-3.5 h-3.5 text-warning" />
                    <span className="text-warning font-medium">未启用</span>
                  </>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-1.5 text-xs">
                <XCircle className="w-3.5 h-3.5 text-warning" />
                <span className="text-warning font-medium">未配置</span>
              </div>
            )}
            <p className="text-[10px] text-muted-foreground truncate" title={llm?.note ?? "—"}>
              {llm?.model ? `model=${llm.model}` : llm?.note ?? "无 LLM 配置（Governor 本地优化优先 55）"}
            </p>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <Gauge className="w-3.5 h-3.5" />
          并行评估通道已接线（10.2.2 落地）；训练/打标为 v6 10.2.1/10.2.3 未实施项，前端如实展示"未部署"
        </div>
      </SectionCard>
    </div>
  );
}

export default ComputeChannelsPanel;
