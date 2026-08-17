"use client";

/**
 * 批量任务卡 — 单层面板 + SubSection
 */
import { useState } from "react";
import { BookOpenCheck, Tag, Play, Bot } from "lucide-react";
import { getLlmStatus, triggerLlmCheck } from "@/lib/api/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  StatusBadge,
  SubSection,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function LocalLlmBlock() {
  const { data, loading, error, refresh } = usePolling(getLlmStatus, 30000);
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState<string | null>(null);

  const check = data?.last_check;
  const steps = check?.steps ?? [];
  const enabled = Boolean(data?.enabled);

  const onCheck = async () => {
    setChecking(true);
    setCheckMsg(null);
    try {
      const res = await triggerLlmCheck();
      setCheckMsg(res.message || "连通性检查已启动");
      setTimeout(() => {
        refresh();
        setCheckMsg(null);
      }, 8000);
    } catch (e) {
      setCheckMsg(e instanceof Error ? e.message : "检查失败");
    } finally {
      setChecking(false);
    }
  };

  return (
    <SubSection
      title="本地 LLM"
      icon={<Bot className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={<StatusBadge status={enabled ? (data?.checking ? "running" : "ok") : "disabled"} />}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取 LLM 状态…" />
      ) : data ? (
        <>
          <div className="text-[11px] text-muted-foreground space-y-0.5">
            <p>
              配置 #{data.config_id}
              {data.config_found === false && "（未找到对应配置）"}
            </p>
            {(data.host || data.model) && (
              <p>
                {data.host && `主机 ${data.host}`}
                {data.host && data.model && " · "}
                {data.model && `模型 ${data.model}`}
              </p>
            )}
            {data.note && (
              <p className="text-amber-600 dark:text-amber-400">{data.note}</p>
            )}
          </div>

          {steps.length > 0 ? (
            <div className="space-y-1">
              {steps.map((s, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-xs rounded-md border border-border/60 px-2.5 py-1.5"
                >
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "w-2 h-2 rounded-full",
                        s.ok ? "bg-profit" : "bg-loss"
                      )}
                    />
                    {s.name}
                  </span>
                  {s.elapsed != null && (
                    <span className="text-muted-foreground tabular-nums">
                      {s.elapsed.toFixed(1)}s
                    </span>
                  )}
                </div>
              ))}
              <p className="text-[11px] text-muted-foreground">
                通过 {check?.passed ?? 0}/{check?.total ?? 0}
                {check?.elapsed_sec != null && `｜耗时 ${check.elapsed_sec.toFixed(1)}s`}
              </p>
            </div>
          ) : (
            <EmptyBox
              message={
                check?.skipped
                  ? "连通性检查未执行——点击「重新检查」"
                  : "尚无连通性检查结果"
              }
            />
          )}

          <div className="flex items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              onClick={onCheck}
              disabled={checking || data.checking}
            >
              <Play className="w-3.5 h-3.5 mr-1.5" />
              {checking ? "检查中…" : "重新检查"}
            </Button>
            {checkMsg && <span className="text-xs text-primary">{checkMsg}</span>}
          </div>
        </>
      ) : null}
    </SubSection>
  );
}

function WisdomLabelPanel() {
  return (
    <SubSection
      title="Wisdom 打标"
      icon={<BookOpenCheck className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400">
          运行中
        </span>
      }
    >
      <p className="text-[11px] text-muted-foreground">
        由 hermes wisdom_accumulate 写入 trading_wisdom，详细状态见调度日志。
      </p>
    </SubSection>
  );
}

function FactorLabelPanel() {
  return (
    <SubSection
      title="因子语义标注"
      icon={<Tag className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={<StatusBadge status="placeholder" />}
    >
      <p className="text-[11px] text-muted-foreground">
        CodegenCritic 仍为占位，待接入真实本地 LLM。
      </p>
    </SubSection>
  );
}

export function BatchTasksCard() {
  return (
    <ComputePanel title="批量任务" description="本地推理与打标通道">
      <div className="space-y-3">
        <LocalLlmBlock />
        <WisdomLabelPanel />
        <FactorLabelPanel />
      </div>
    </ComputePanel>
  );
}
