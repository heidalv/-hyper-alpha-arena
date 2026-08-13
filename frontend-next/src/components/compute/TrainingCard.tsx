"use client";

/**
 * GPU 环境与模型训练卡 — 单层面板 + SubSection，禁止套卡
 */
import { useState } from "react";
import { FlaskConical, Ban } from "lucide-react";
import {
  getScalpMetaReport,
  getTasks,
  triggerScalpMetaTrain,
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
  SubSection,
  fmtDt,
  fmtNum,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";

function MetaTrainerBlock() {
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
    <SubSection
      title="元标签分类器"
      icon={<FlaskConical className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={
        usable ? (
          <StatusBadge status="ok" />
        ) : reportStatus === "no_deps" ? (
          <StatusBadge status="degraded" />
        ) : null
      }
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取采集进度…" />
      ) : (
        <>
          <div>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-muted-foreground">样本采集</span>
              <span className="tabular-nums">
                {fmtNum(have)} / {fmtNum(need)}
                {progress?.pos != null && (
                  <span className="text-muted-foreground ml-2">
                    正 {fmtNum(progress.pos)}｜负 {fmtNum(progress.neg)}
                  </span>
                )}
              </span>
            </div>
            <ProgressBar
              percent={percent}
              tone={percent >= 100 ? "ok" : percent > 60 ? "warn" : "bad"}
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              {progress?.ready
                ? `样本已就绪（raw ${fmtNum(progress.raw)} 条）`
                : "样本不足时训练会自动跳过"}
            </p>
          </div>

          {report ? (
            <div className="rounded-md border border-border/60 bg-background/40 px-2.5 py-2 text-xs space-y-1">
              <div className="flex items-center justify-between">
                <span className="font-medium">最近训练报告</span>
                <span className="text-muted-foreground">{fmtDt(report.ts ? String(report.ts) : "")}</span>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
                <span>
                  OOS AUC{" "}
                  <b className="text-foreground tabular-nums">
                    {fmtNum(
                      (report.oos_auc_lgbm as number | undefined) ??
                        (report.auc as number | undefined),
                      3,
                    )}
                  </b>
                </span>
                <span>
                  样本 <b className="text-foreground tabular-nums">{fmtNum(report.n_settled)}</b>
                </span>
                <span>
                  正/负{" "}
                  <b className="text-foreground tabular-nums">
                    {fmtNum(report.pos)}/{fmtNum(report.neg)}
                  </b>
                </span>
              </div>
              {report.error && <p className="text-red-500">{report.error}</p>}
              {!usable && (
                <p className="text-amber-600 dark:text-amber-400">未达可用门控，模型仅作参考</p>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">暂无训练报告</p>
          )}

          <div className="flex items-center gap-3 pt-0.5">
            <Button size="sm" onClick={onTrain} disabled={training}>
              <FlaskConical className="w-3.5 h-3.5 mr-1.5" />
              {training ? "提交中…" : "手动训练"}
            </Button>
            {trainMsg && <span className="text-xs text-primary">{trainMsg}</span>}
          </div>
        </>
      )}
    </SubSection>
  );
}

function SignalFusionBlock() {
  const { data, loading, error, refresh } = usePolling<TasksResponse>(
    () => getTasks(8),
    5000
  );
  const jobs = data?.jobs ?? [];
  const running = Boolean(data?.evolution_running);

  return (
    <SubSection
      title="信号融合任务"
      badge={<StatusBadge status={running ? "running" : jobs.length > 0 ? "ok" : "idle"} />}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground py-2 text-center">
          暂无运行中任务（进化{running ? "运行中" : "空闲"}）
        </p>
      ) : (
        <ul className="space-y-1.5">
          {jobs.map((j, i) => (
            <li
              key={String(j.id ?? j.job_id ?? i)}
              className="flex items-center justify-between text-xs rounded-md border border-border/60 px-2.5 py-1.5"
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
    </SubSection>
  );
}

function RLSection() {
  return (
    <SubSection
      title="RL 因子挖掘"
      icon={<Ban className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-muted text-muted-foreground">
          已下线
        </span>
      }
    >
      <p className="text-[11px] text-muted-foreground">MaskablePPO / DRL 全线已下线，此处仅作状态留档。</p>
    </SubSection>
  );
}

export function TrainingCard() {
  return (
    <ComputePanel title="模型训练" description="元标签分类器与滚动重训任务">
      <div className="space-y-3">
        <MetaTrainerBlock />
        <SignalFusionBlock />
        <RLSection />
      </div>
    </ComputePanel>
  );
}
