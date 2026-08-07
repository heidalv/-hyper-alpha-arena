"use client";

/**
 * GPU 环境与模型训练卡（第十章 10.2.1）
 *  - MetaTrainerPanel 元标签分类器：采集进度 n/need + usable 徽章 + 报告指标 + 手动训练
 *  - SignalFusionPanel 信号融合：滚动重训状态（自有 WFO/DSR/PBO 管线）+ 最近任务
 *  - RLSection        DRL 已下线标识（如实展示）
 *
 * 数据源：
 *  - GET /api/factors/scalp-meta-report（10s 轮询）
 *  - POST /api/factors/scalp-meta/train（手动训练，写操作）
 *  - GET /api/compute/tasks（5s 轮询）
 */
import { useState } from "react";
import { FlaskConical, Dices, GitMerge, Ban } from "lucide-react";
import {
  getScalpMetaReport,
  getTasks,
  triggerScalpMetaTrain,
  type ScalpMetaReport,
  type TasksResponse,
} from "@/lib/api/compute";
import { useComputeStore } from "@/lib/stores/compute";
import {
  ComputePanel,
  LoadingBox,
  PanelError,
  ProgressBar,
  RefreshButton,
  StatusBadge,
  fmtDt,
  fmtNum,
  fmtPct,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ───────────────────────────── 元标签分类器 ─────────────────────────────

function MetaTrainerPanel() {
  const { data, loading, error, refresh } = usePolling(getScalpMetaReport, 10000);
  const [training, setTraining] = useState(false);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const addRunningJob = useComputeStore((s) => s.addRunningJob);

  const progress = data?.progress;
  const report = data?.report;
  const need = progress?.need ?? 800;
  const have = progress?.have ?? 0;
  const percent = progress?.percent ?? (need > 0 ? (have / need) * 100 : 0);
  const usable = Boolean(report?.usable);
  const reportStatus = report?.status ?? null;

  const onTrain = async () => {
    if (!window.confirm("确认手动触发元标签分类器训练？（walk-forward + usable 门控）")) return;
    setTraining(true);
    setTrainMsg(null);
    try {
      const res = await triggerScalpMetaTrain();
      const jobId =
        (res as Record<string, unknown>)?.job_id ??
        (res as Record<string, unknown>)?.jobId ??
        null;
      if (jobId != null) addRunningJob(jobId as string | number);
      setTrainMsg(
        (res as Record<string, unknown>)?.message
          ? String((res as Record<string, unknown>).message)
          : "训练任务已提交"
      );
    } catch (e) {
      setTrainMsg(e instanceof Error ? `训练失败：${e.message}` : "训练失败");
    } finally {
      setTraining(false);
      setTimeout(() => setTrainMsg(null), 8000);
    }
  };

  return (
    <ComputePanel
      title="元标签分类器"
      description="scalp_meta_trainer：walk-forward + usable 门控（GET /api/factors/scalp-meta-report）"
      status={usable ? "ok" : reportStatus === "no_deps" ? "degraded" : undefined}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取采集进度…" />
      ) : (
        <>
          {/* 采集进度 */}
          <div className="mb-3">
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-muted-foreground">样本采集进度</span>
              <span className="tabular-nums">
                {fmtNum(have)} / {fmtNum(need)}
                {progress?.pos != null && (
                  <span className="text-muted-foreground ml-2">
                    正 {fmtNum(progress.pos)}｜负 {fmtNum(progress.neg)}
                    {progress.need_per_class != null && `（每类需 ${fmtNum(progress.need_per_class)}）`}
                  </span>
                )}
              </span>
            </div>
            <ProgressBar
              percent={percent}
              tone={percent >= 100 ? "ok" : percent > 60 ? "warn" : "bad"}
            />
            {progress?.ready ? (
              <p className="text-[11px] text-green-600 dark:text-green-400 mt-1">
                样本已就绪（raw {fmtNum(progress.raw)} 条）
              </p>
            ) : (
              <p className="text-[11px] text-muted-foreground mt-1">样本不足，训练自动优雅跳过</p>
            )}
          </div>

          {/* 最近报告 */}
          {report ? (
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-medium">最近训练报告</span>
                <span className="flex items-center gap-2">
                  <StatusBadge status={usable ? "ok" : reportStatus} />
                  <span className="text-muted-foreground">{fmtDt(report.ts ? String(report.ts) : "")}</span>
                </span>
              </div>
              <div className="flex flex-wrap gap-x-4 text-muted-foreground">
                <span>
                  AUC：<b className="text-foreground tabular-nums">{fmtNum(report.auc, 3)}</b>
                </span>
                <span>
                  已结算样本：
                  <b className="text-foreground tabular-nums">{fmtNum(report.n_settled)}</b>
                </span>
                <span>
                  正/负：<b className="text-foreground tabular-nums">{fmtNum(report.pos)}/{fmtNum(report.neg)}</b>
                </span>
                <span>
                  状态：<b className="text-foreground">{report.status ?? "—"}</b>
                </span>
              </div>
              {report.error && <p className="text-red-500">{report.error}</p>}
              {!usable && (
                <p className="text-amber-600 dark:text-amber-400">
                  未达 usable 门控（SCALP_META_GATE_AUC）——模型停用旧版，仅作参考
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">暂无训练报告（样本积累后自动产出）</p>
          )}

          <div className="mt-3 flex items-center gap-3">
            <Button size="sm" onClick={onTrain} disabled={training}>
              <FlaskConical className="w-3.5 h-3.5 mr-1.5" />
              {training ? "提交中…" : "手动训练"}
            </Button>
            {trainMsg && <span className="text-xs text-primary">{trainMsg}</span>}
          </div>
        </>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── 信号融合 ─────────────────────────────

function SignalFusionPanel() {
  const { data, loading, error, refresh } = usePolling<TasksResponse>(
    () => getTasks(8),
    5000
  );
  const jobs = data?.jobs ?? [];
  const running = Boolean(data?.evolution_running);

  return (
    <ComputePanel
      title="信号融合"
      description="滚动重训→DSR/PBO→admission 自有管线（WFO/DSR/PBO 真实计算）"
      status={running ? "running" : jobs.length > 0 ? "ok" : undefined}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      <p className="text-xs text-muted-foreground mb-3">
        数据源：GET /api/compute/tasks（job_manager + 调度任务 + 运行线程聚合）
      </p>
      {jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground py-3 text-center">
          暂无运行中任务（进化{running ? "运行中" : "空闲"}）
        </p>
      ) : (
        <ul className="space-y-1.5">
          {jobs.map((j, i) => (
            <li
              key={String(j.id ?? j.job_id ?? i)}
              className="flex items-center justify-between text-xs rounded border border-border px-2.5 py-1.5"
            >
              <span className="truncate">
                {String(j.type ?? "任务")}
                <span className="text-muted-foreground ml-2">
                  {fmtDt(String(j.created_at ?? ""))}
                </span>
              </span>
              <StatusBadge status={String(j.status ?? "")} />
            </li>
          ))}
        </ul>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── DRL 已下线 ─────────────────────────────

function RLSection() {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs">
        <Ban className="w-3.5 h-3.5 text-muted-foreground" />
        <span className="font-medium flex items-center gap-1.5">
          RL 因子挖掘（MaskablePPO）
          <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-muted text-muted-foreground">
            已下线 2026-06-11
          </span>
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground mt-1">
        DRL 全线已下线（rl_routes.py 注明），方向性变更文档未同步；此处如实展示遗留状态，不伪造数据。
      </p>
    </div>
  );
}

// ───────────────────────────── 主卡 ─────────────────────────────

export function TrainingCard() {
  return (
    <ComputePanel
      title="GPU 环境与模型训练"
      description="元标签分类器｜信号融合｜DRL 状态"
      className={cn("space-y-0")}
    >
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-3">
        <Dices className="w-3.5 h-3.5" />
        训练依赖 torch/cu124 —— 实机 torch 损坏时以上状态如实降级展示
      </div>
      <div className="space-y-3">
        <MetaTrainerPanel />
        <SignalFusionPanel />
        <RLSection />
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <GitMerge className="w-3 h-3" />
          开源红线：产出全走 purge→lifecycle→shadow_judge→online_weights，不开新实盘链路
        </div>
      </div>
    </ComputePanel>
  );
}
