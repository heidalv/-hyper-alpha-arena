"use client";

/**
 * 批量任务卡（第十章 10.2.3）
 *  - LocalLlmPanel  本地 LLM 双机连通性 4 项 + LOCAL_LLM_CONFIG_ID 启用状态 + 重新检查
 *  - WisdomLabelPanel wisdom 打标（hermes wisdom_accumulate 运行中）
 *  - FactorLabelPanel 因子语义标注（CodegenCritic 占位 + 待接 LLM 提示，如实展示）
 *
 * 数据源：GET /api/compute/llm/status、POST /api/compute/llm/check
 */
import { useState } from "react";
import { BookOpenCheck, Tag, Play } from "lucide-react";
import {
  getLlmStatus,
  triggerLlmCheck,
} from "@/lib/api/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  StatusBadge,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ───────────────────────────── 本地 LLM 双机 ─────────────────────────────

function LocalLlmPanel() {
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
      setCheckMsg(res.message || "连通性检查已启动（后台执行）");
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
    <ComputePanel
      title="本地 LLM（双机架构）"
      description="Qwen3-30B-A3B 内网 GPU 机｜Governor 优先级 55（GET /api/compute/llm/status）"
      status={enabled ? (data?.checking ? "running" : "ok") : "disabled"}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取 LLM 状态…" />
      ) : data ? (
        <>
          <div className="flex items-center justify-between text-xs mb-2">
            <span className="text-muted-foreground">
              LOCAL_LLM_CONFIG_ID：<b className="text-foreground tabular-nums">{data.config_id}</b>
              {data.config_found === false && "（未找到对应 LLMConfig）"}
            </span>
            <StatusBadge status={enabled ? "ok" : "disabled"} />
          </div>
          {(data.base_url || data.host || data.model) && (
            <p className="text-[11px] text-muted-foreground mb-2">
              {data.host && `主机 ${data.host}｜`}
              {data.base_url && `base_url ${data.base_url}｜`}
              {data.model && `模型 ${data.model}`}
            </p>
          )}
          {data.note && <p className="text-[11px] text-amber-600 dark:text-amber-400 mb-2">{data.note}</p>}

          {steps.length > 0 ? (
            <div className="space-y-1 mb-2">
              {steps.map((s, i) => (
                <div key={i} className="flex items-center justify-between text-xs rounded border border-border px-2.5 py-1.5">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "w-2 h-2 rounded-full",
                        s.ok ? "bg-green-500" : "bg-red-500"
                      )}
                    />
                    {s.name}
                    {s.model && (
                      <span className="text-muted-foreground text-[10px]">({s.model})</span>
                    )}
                  </span>
                  {s.elapsed != null && (
                    <span className="text-muted-foreground tabular-nums">{s.elapsed.toFixed(1)}s</span>
                  )}
                </div>
              ))}
              <p className="text-[11px] text-muted-foreground">
                通过 {check?.passed ?? 0}/{check?.total ?? 0} 项
                {check?.skipped && "（上次跳过，无缓存）"}
                {check?.elapsed_sec != null && `｜耗时 ${check.elapsed_sec.toFixed(1)}s`}
                {check?.error && <span className="text-red-500">｜{check.error}</span>}
              </p>
            </div>
          ) : (
            <EmptyBox
              message={
                check?.skipped
                  ? "连通性检查未执行（默认跳过）——点击「重新检查」后台运行"
                  : "尚无连通性检查结果"
              }
            />
          )}

          <div className="flex items-center gap-3">
            <Button size="sm" variant="outline" onClick={onCheck} disabled={checking || data.checking}>
              <Play className="w-3.5 h-3.5 mr-1.5" />
              {checking ? "检查中…" : "重新检查"}
            </Button>
            {checkMsg && <span className="text-xs text-primary">{checkMsg}</span>}
          </div>
        </>
      ) : null}
    </ComputePanel>
  );
}

// ───────────────────────────── Wisdom 打标 ─────────────────────────────

function WisdomLabelPanel() {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-3 py-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          <BookOpenCheck className="w-3.5 h-3.5 text-muted-foreground" />
          Wisdom 打标
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400">
          hermes wisdom_accumulate 运行中
        </span>
      </div>
      <p className="text-[11px] text-muted-foreground mt-1">
        数据落 trading_wisdom 表；今日曾有 psycopg 偶发错误（P3 缺口），状态以 hermes 日志为准。
      </p>
    </div>
  );
}

// ───────────────────────────── 因子语义标注 ─────────────────────────────

function FactorLabelPanel() {
  return (
    <div className="rounded-md border border-border bg-muted/30 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium">
        <Tag className="w-3.5 h-3.5 text-muted-foreground" />
        因子语义标注
        <StatusBadge status="placeholder" />
      </div>
      <p className="text-[11px] text-muted-foreground mt-1">
        alpha_miner.py CodegenCritic 为纯占位（_has_llm=False，返回硬编码示例）。
        待接真实 LLM（P1 缺口：内网 30B-A3B 双机）。
      </p>
    </div>
  );
}

// ───────────────────────────── 主卡 ─────────────────────────────

export function BatchTasksCard() {
  return (
    <ComputePanel
      title="批量任务"
      description="本地 LLM｜Wisdom 打标｜因子语义标注"
    >
      <div className="space-y-3">
        <LocalLlmPanel />
        <WisdomLabelPanel />
        <FactorLabelPanel />
      </div>
    </ComputePanel>
  );
}
