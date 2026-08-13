"use client";

/**
 * 嵌入/检索卡 — 单层面板 + SubSection
 */
import { useState } from "react";
import { Database, HardDrive, Wrench, Layers } from "lucide-react";
import {
  getRagHealth,
  getRagStats,
  triggerRagReindex,
  type RagStats,
} from "@/lib/api/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  StatusBadge,
  SubSection,
  fmtDt,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";

function RagHealthBlock() {
  const { data: health, loading: hl, error: he, refresh: refreshHealth } =
    usePolling(getRagHealth, 30000);
  const { data: stats, loading: sl, error: se, refresh: refreshStats } =
    usePolling(getRagStats, 30000);

  const [reindexing, setReindexing] = useState(false);
  const [reindexMsg, setReindexMsg] = useState<string | null>(null);

  const loading = hl || sl;
  const error = he || se;
  const degraded = Boolean(stats?.degraded ?? health?.degraded);

  const onReindex = async () => {
    if (!window.confirm("确认全量重建 RAG 向量库？（耗时较长，期间检索降级）")) return;
    setReindexing(true);
    setReindexMsg(null);
    try {
      const res = await triggerRagReindex();
      const msg =
        (res as Record<string, unknown>)?.message ??
        (res as Record<string, unknown>)?.status ??
        "重建任务已提交";
      setReindexMsg(String(msg));
      setTimeout(refreshStats, 5000);
    } catch (e) {
      setReindexMsg(e instanceof Error ? e.message : "重建失败");
    } finally {
      setReindexing(false);
      setTimeout(() => setReindexMsg(null), 10000);
    }
  };

  return (
    <SubSection
      title="RAG 检索"
      icon={<Database className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={
        <StatusBadge status={stats?.ready ? (degraded ? "degraded" : "ok") : "degraded"} />
      }
      action={
        <RefreshButton
          onClick={() => {
            refreshHealth();
            refreshStats();
          }}
          loading={loading}
        />
      }
    >
      <PanelError error={error} />
      {loading && !stats && !health ? (
        <LoadingBox text="读取 RAG 状态…" />
      ) : (
        <>
          <div className="text-[11px] text-muted-foreground space-y-1">
            <p className="flex items-center gap-1.5 flex-wrap">
              <Database className="w-3 h-3 flex-shrink-0" />
              模型{" "}
              <b className="text-foreground">
                {stats?.embedding_model ?? health?.embedding_model ?? "—"}
              </b>
              {stats?.degraded_query_count != null && (
                <span>｜降级查询 {stats.degraded_query_count} 次</span>
              )}
            </p>
            {stats?.persist_dir && (
              <p className="flex items-start gap-1.5">
                <HardDrive className="w-3 h-3 mt-0.5 flex-shrink-0" />
                <span className="break-all">{stats.persist_dir}</span>
              </p>
            )}
            {health?.total_documents != null && (
              <p>
                文档总数{" "}
                <b className="text-foreground tabular-nums">{health.total_documents}</b>
              </p>
            )}
            {degraded && (
              <p className="text-amber-600 dark:text-amber-400">
                检索已降级为 SQL，修复 torch 后自动恢复
              </p>
            )}
          </div>

          <div className="flex items-center gap-3">
            <Button size="sm" variant="outline" onClick={onReindex} disabled={reindexing}>
              <Wrench className="w-3.5 h-3.5 mr-1.5" />
              {reindexing ? "重建中…" : "重建向量库"}
            </Button>
            {reindexMsg && <span className="text-xs text-primary">{reindexMsg}</span>}
          </div>
        </>
      )}
    </SubSection>
  );
}

function QaaVectorBlock() {
  const { data, loading, error, refresh } = usePolling<RagStats>(getRagStats, 30000);
  const collections = data?.collections ?? {};
  const entries = Object.entries(collections);

  return (
    <SubSection
      title="QAA 向量库"
      icon={<Layers className="w-3.5 h-3.5 text-muted-foreground" />}
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取向量库…" />
      ) : entries.length === 0 ? (
        <EmptyBox message="尚无 collection（首次索引后自动出现）" />
      ) : (
        <ul className="space-y-1.5">
          {entries.map(([name, c]) => (
            <li
              key={name}
              className="flex items-center justify-between text-xs rounded-md border border-border/60 px-2.5 py-1.5 gap-2"
            >
              <span className="truncate font-medium">{name}</span>
              <span className="text-muted-foreground tabular-nums flex-shrink-0">
                {c.doc_count} 文档
                {c.last_indexed ? `｜${fmtDt(c.last_indexed)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SubSection>
  );
}

export function EmbeddingCard() {
  return (
    <ComputePanel title="嵌入 / 检索" description="RAG 与 QAA 向量库状态">
      <div className="space-y-3">
        <RagHealthBlock />
        <QaaVectorBlock />
      </div>
    </ComputePanel>
  );
}
