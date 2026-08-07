"use client";

/**
 * 调度&日志面板（迁移自旧 /evolution「调度&日志」Tab，去除已删除的 opencode log-tail 引用）
 * - Hermes 调度任务表（/api/hermes/dashboard.schedule：L1-L4 任务 + last_status/last_error + 手动触发）
 * - 提示词同步状态（/api/hermes/prompts/diff：磁盘 vs 数据库 active 版本）
 * - LearningLoop 5 job tick（/api/learning/loop/status）
 */
import { useEffect, useState, useCallback } from "react";
import {
  Clock, CheckCircle2, XCircle, Loader2, Play, AlertTriangle, HeartPulse,
} from "lucide-react";
import { getBackendUrl } from "@/lib/backend-config";
import { SectionCard, RefreshButton, EmptyState } from "@/components/operations/IlcUi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const BACKEND = getBackendUrl().replace(/\/$/, "");

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatInterval(s?: number): string {
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}min`;
  return `${(s / 3600).toFixed(1)}h`;
}

export default function ScheduleLogPanel() {
  const [schedule, setSchedule] = useState<any[]>([]);
  const [loop, setLoop] = useState<any>({});
  const [diff, setDiff] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dash, lp, df] = await Promise.allSettled([
        fetch(`${BACKEND}/api/hermes/dashboard`).then(r => r.json()),
        fetch(`${BACKEND}/api/learning/loop/status`).then(r => r.json()),
        fetch(`${BACKEND}/api/hermes/prompts/diff`).then(r => r.json()),
      ]);
      if (dash.status === "fulfilled") setSchedule(dash.value?.schedule ?? []);
      if (lp.status === "fulfilled") setLoop(lp.value ?? {});
      if (df.status === "fulfilled") setDiff(df.value ?? null);
      else setDiff(undefined);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const triggerTask = async (jobId: string) => {
    setTriggering(jobId);
    try {
      await fetch(`${BACKEND}/api/learning/hermes/run/${jobId.split("_").slice(-1)[0]}`, { method: "POST" });
    } catch {
      // 触发失败不阻塞：任务名映射非全部存在，仅尝试
    } finally {
      setTriggering(null);
      load();
    }
  };

  const loopJobs = [
    { key: "learning_loop_heartbeat", label: "闭环-WS心跳", interval: "30s" },
    { key: "learning_loop_outcome_batch", label: "闭环-结果批处理", interval: "5min" },
    { key: "learning_loop_paper_outcome_backfill", label: "闭环-paper补偿", interval: "10min" },
    { key: "learning_loop_kelly_portfolio", label: "闭环-Kelly聚合", interval: "30min" },
    { key: "learning_loop_coordinator", label: "闭环-系统协调器", interval: "1h" },
  ];

  return (
    <div className="space-y-4">
      <SectionCard
        title="Hermes 调度任务"
        description="L1-L4 任务注册与最近运行状态（/api/hermes/dashboard.schedule）；L2/L3/L4 依赖 LLM sidecar，当前断链会如实标红"
        action={<RefreshButton onClick={load} loading={loading} />}
      >
        {schedule.length === 0 ? (
          <EmptyState message="暂无调度任务（接口加载失败或为空）" />
        ) : (
          <div className="divide-y divide-border/20">
            {schedule.map((job: any, i: number) => (
              <div key={i} className="flex items-center gap-3 py-2 text-xs">
                <Badge variant="secondary" className="font-mono text-[9px] shrink-0">{job.layer}</Badge>
                <div className="flex-1 min-w-0">
                  <div className="truncate">{job.label}</div>
                  <div className="text-[10px] text-muted-foreground truncate">{job.desc ?? job.job_id}</div>
                </div>
                <span className="text-muted-foreground tabular-nums shrink-0">{formatInterval(job.interval_s)}</span>
                {job.is_running ? (
                  <Badge className="bg-primary/20 text-primary text-[9px]">
                    <Loader2 className="w-2.5 h-2.5 animate-spin mr-0.5" />运行中
                  </Badge>
                ) : job.last_status === "ok" ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-profit shrink-0" />
                ) : job.last_status === "error" ? (
                  <XCircle className="w-3.5 h-3.5 text-loss shrink-0" />
                ) : (
                  <Clock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                )}
                {job.last_error && (
                  <span className="text-[10px] text-loss truncate max-w-40" title={job.last_error}>{job.last_error}</span>
                )}
                <Button
                  size="sm" variant="ghost" className="h-6 text-[10px] px-2 shrink-0"
                  disabled={triggering === job.job_id || job.is_running}
                  onClick={() => triggerTask(job.job_id)}
                >
                  {triggering === job.job_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                </Button>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="提示词同步状态"
        description="磁盘 .md 版本 vs 数据库 active 版本（/api/hermes/prompts/diff），L2 通道唯一真实信号"
      >
        {diff === undefined ? (
          <div className="flex items-center gap-2 text-xs text-warning">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            提示词同步状态未知（diff 接口查询超时），稍后自动重试
          </div>
        ) : diff === null ? (
          <EmptyState message="提示词同步数据为空" />
        ) : (diff.needs_sync_count ?? 0) > 0 ? (
          <div className="flex items-center gap-2 text-xs text-loss">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            提示词不同步：{diff.needs_sync_count} 个需同步（磁盘版本高于数据库 active 版本）
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-profit">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            提示词已同步 — 磁盘版本与数据库 active 版本一致
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="LearningLoop 5 Job 真实 tick"
        description="定时批处理中枢最近运行记录（/api/learning/loop/status），30s 轮询"
      >
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border/50">
                <th className="py-1.5 pr-2 font-medium">闭环</th>
                <th className="py-1.5 pr-2 font-medium">间隔</th>
                <th className="py-1.5 pr-2 font-medium">上次 tick</th>
                <th className="py-1.5 pr-2 font-medium">下次 tick</th>
              </tr>
            </thead>
            <tbody>
              {loopJobs.map(job => {
                const last = loop.last_tick_at?.[job.key];
                const next = loop.next_tick_at?.[job.key];
                return (
                  <tr key={job.key} className="border-b border-border/20">
                    <td className="py-1.5 pr-2">{job.label}</td>
                    <td className="py-1.5 pr-2 text-muted-foreground">{job.interval}</td>
                    <td className="py-1.5 pr-2">
                      {last ? (
                        <span className="flex items-center gap-1 text-profit">
                          <HeartPulse className="w-3 h-3" />
                          {fmtTime(last)}
                        </span>
                      ) : (
                        <span className="text-loss">从未 tick</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-2 text-muted-foreground">{next ? fmtTime(next) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <p className="text-[10px] text-muted-foreground">
        数据源：/api/hermes/dashboard + /api/hermes/prompts/diff + /api/learning/loop/status（30s 轮询）
      </p>
    </div>
  );
}
