"use client";

/**
 * 嵌入/检索卡（第十章 10.2.4）
 *  - RagHealthPanel RAG 健康：模型名/持久化目录/degraded 计数 + 重建按钮
 *  - QaaVectorPanel QAA 向量库 collection 统计
 *
 * 数据源：GET /api/rag/stats、GET /api/rag/health、POST /api/rag/reindex
 * 说明：实机 bge-large-zh-v1.5（RAG）+ bge-small-zh-v1.5（QAA），非文档 bge-m3；
 *       torch 损坏时 RAG 持续 degraded（检索降级 SQL），如实展示。
 */
import { useState } from "react";
import { Database, HardDrive, Wrench } from "lucide-react";
import {
  getRagHealth,
  getRagStats,
  triggerRagReindex,
  type RagStats,
  type RagHealth,
} from "@/lib/api/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  StatusBadge,
  fmtDt,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";

// ───────────────────────────── RAG 健康 ─────────────────────────────

function RagHealthPanel() {
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
    <ComputePanel
      title="RAG 检索"
      description="持久化向量库 + degraded 降级计数（GET /api/rag/stats、/api/rag/health）"
      status={stats?.ready ? (degraded ? "degraded" : "ok") : "degraded"}
      action={<RefreshButton onClick={refreshHealth} loading={loading} />}
    >
      <PanelError error={error} />
      {loading && !stats && !health ? (
        <LoadingBox text="读取 RAG 状态…" />
      ) : (
        <>
          <div className="flex items-center justify-between text-xs mb-2">
            <span className="text-muted-foreground">就绪状态</span>
            <span className="flex items-center gap-2">
              <StatusBadge status={stats?.ready ? (degraded ? "degraded" : "ok") : "error"} />
              {stats?.degraded_query_count != null && (
                <span className="text-[11px] tabular-nums">
                  degraded 查询 {stats.degraded_query_count} 次
                </span>
              )}
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground space-y-0.5">
            <p className="flex items-center gap-1.5">
              <Database className="w-3 h-3" />
              嵌入模型：<b className="text-foreground">{stats?.embedding_model ?? health?.embedding_model ?? "—"}</b>
              <span className="text-[10px] opacity-70">（实机 bge 系列，非文档 bge-m3）</span>
            </p>
            {stats?.persist_dir && (
              <p className="flex items-center gap-1.5">
                <HardDrive className="w-3 h-3" />
                持久化目录：{stats.persist_dir}
              </p>
            )}
            {health?.total_documents != null && (
              <p>文档总数：<b className="text-foreground tabular-nums">{health.total_documents}</b></p>
            )}
            {degraded && (
              <p className="text-amber-600 dark:text-amber-400">
                检索全降级 SQL（torch 损坏 → RAG=False），修复 torch 后自动恢复
              </p>
            )}
          </div>

          <div className="mt-3 flex items-center gap-3">
            <Button size="sm" variant="outline" onClick={onReindex} disabled={reindexing}>
              <Wrench className="w-3.5 h-3.5 mr-1.5" />
              {reindexing ? "重建中…" : "重建向量库"}
            </Button>
            {reindexMsg && <span className="text-xs text-primary">{reindexMsg}</span>}
          </div>
        </>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── QAA 向量库 ─────────────────────────────

function QaaVectorPanel() {
  const { data, loading, error, refresh } = usePolling<RagStats>(getRagStats, 30000);
  const collections = data?.collections ?? {};
  const entries = Object.entries(collections);

  return (
    <ComputePanel
      title="QAA 向量库"
      description="collection 统计（GET /api/rag/stats）"
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
              className="flex items-center justify-between text-xs rounded border border-border px-2.5 py-1.5"
            >
              <span>{name}</span>
              <span className="text-muted-foreground tabular-nums">
                {c.doc_count} 文档
                {c.last_indexed ? `｜索引 ${fmtDt(c.last_indexed)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── 主卡 ─────────────────────────────

export function EmbeddingCard() {
  return (
    <ComputePanel
      title="嵌入 / 检索"
      description="RAG 与 QAA 向量库（bge 系列嵌入）"
    >
      <div className="space-y-3">
        <RagHealthPanel />
        <QaaVectorPanel />
      </div>
    </ComputePanel>
  );
}
