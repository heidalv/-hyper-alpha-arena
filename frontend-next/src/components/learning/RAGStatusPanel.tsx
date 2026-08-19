/**
 * RAG 检索状态面板（v6 10.2.4 语义检索如实展示）
 *
 * 五 collection 文档数 + 索引时间 + embedding 模型 + 检索可用性。
 * ready=false（not_initialized）时标红，文档在库但未激活绝不粉饰为"可用"。
 */
"use client";

import { useEffect, useState } from "react";
import {
  getRagHealth,
  getRagStats,
  getQaaRagStats,
  type RagHealthResponse,
  type RagStatsResponse,
  type QaaRagStatsResponse,
} from "@/lib/intelligentLearningApi";
import { SectionCard, RefreshButton, StatCard } from "../operations/IlcUi";
import { cn } from "@/lib/utils";
import { AlertTriangle, CheckCircle2, Database, Boxes, Loader2, HardDrive } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const KNOWN_COLLECTIONS = ["trading_wisdom", "proposal_wisdom", "decision_retro", "factor_knowledge", "outcome_lessons"];

export function RAGStatusPanel() {
  const [health, setHealth] = useState<RagHealthResponse | null>(null);
  const [stats, setStats] = useState<RagStatsResponse | null>(null);
  const [qaa, setQaa] = useState<QaaRagStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    Promise.allSettled([getRagHealth(), getRagStats(), getQaaRagStats()])
      .then(([h, s, q]) => {
        if (h.status === "fulfilled") setHealth(h.value);
        if (s.status === "fulfilled") setStats(s.value);
        if (q.status === "fulfilled") setQaa(q.value);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const ready = stats?.ready ?? health?.ready ?? false;
  const degraded = stats?.degraded ?? health?.degraded ?? false;
  const model = stats?.embedding_model ?? health?.embedding_model ?? null;
  const collections = stats?.collections ?? {};
  const collNames = Object.keys(collections).length > 0 ? Object.keys(collections) : KNOWN_COLLECTIONS;
  const totalDocs = health?.total_documents ?? Object.values(collections).reduce((s, c) => s + (c.doc_count ?? 0), 0);

  return (
    <div className="space-y-4">
      <SectionCard
        title="检索与知识库（RAG）"
        description="v6 10.2.4 语义检索载体：BAAI/bge-large-zh-v1.5 + ChromaDB；ready=false 即标红，如实展示断链"
        action={<RefreshButton onClick={refresh} loading={loading} />}
      >
        {/* 就绪状态横幅 */}
        {ready ? (
          <div className="rounded-lg border border-profit/30 bg-profit/5 p-3 flex items-center gap-2 mb-4">
            <CheckCircle2 className="w-4 h-4 text-profit shrink-0" />
            <div className="text-xs">
              <div className="font-medium text-profit">语义检索可用</div>
              <div className="text-muted-foreground mt-0.5">注入质量升级路径（trading_wisdom collection）已激活</div>
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-loss/30 bg-loss/5 p-3 flex items-start gap-2 mb-4">
            <AlertTriangle className="w-4 h-4 text-loss shrink-0 mt-0.5" />
            <div className="text-xs">
              <div className="font-medium text-loss">
                语义检索断链（{health?.status ?? "not_initialized"}）
              </div>
              <div className="text-muted-foreground mt-0.5">
                {totalDocs.toLocaleString()} 条历史文档在库但嵌入模型未加载
                {model ? `（期望 ${model}）` : "（embedding_model=null）"}；
                wisdom 注入仍走最近 N 条退化逻辑（ORDER BY id DESC）
              </div>
              {degraded && <div className="text-warning mt-0.5">已降级运行（degraded=true，部分查询走退路）</div>}
              {health?.error && <div className="text-muted-foreground mt-0.5">错误：{health.error}</div>}
            </div>
          </div>
        )}

        {/* 核心指标 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <StatCard
            label="就绪状态"
            value={ready ? "ready" : "not_initialized"}
            tone={ready ? "good" : "bad"}
            hint="embedding 模型加载 + collection 挂载"
          />
          <StatCard label="全库文档" value={totalDocs.toLocaleString()} hint="ChromaDB 历史沉淀总量" />
          <StatCard
            label="Embedding 模型"
            value={model ?? "未加载"}
            tone={model ? "default" : "bad"}
            hint="BAAI/bge-large-zh-v1.5（约 1.3GB，下载后 init 激活）"
          />
          <StatCard
            label="Collection 数"
            value={collNames.length}
            tone="default"
            hint="五类知识集合（trading_wisdom / proposal_wisdom 等）"
          />
        </div>

        {/* 五 collection 明细 */}
        <div className="text-xs font-medium mb-2 flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5 text-muted-foreground" />
          Collection 明细
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
          {collNames.map((name) => {
            const c = collections[name];
            const docs = c?.doc_count ?? 0;
            return (
              <div
                key={name}
                className={cn(
                  "glass rounded-lg p-3",
                  !c && "opacity-70"
                )}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-[11px] truncate">{name}</span>
                  {c ? (
                    <Badge variant="outline" className="text-[9px] font-normal tabular-nums border-profit/40 bg-profit/10 text-profit">
                      <span className="w-1 h-1 rounded-full bg-profit shadow-[0_0_6px_currentColor]" />
                      {docs.toLocaleString()} 文档
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[9px] font-normal border-amber-500/40 bg-amber-500/10 text-warning">
                      <AlertTriangle className="w-2.5 h-2.5" />
                      未挂载
                    </Badge>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {c?.last_indexed ? `索引于 ${fmtTime(c.last_indexed)}` : "无索引时间"}
                </div>
              </div>
            );
          })}
        </div>
      </SectionCard>

      {/* QAA 实时沉淀库：平仓教训实时写入的那套（区别于上方 reindex 库） */}
      <SectionCard
        title="QAA 实时沉淀库（平仓教训）"
        description="paper_trading_engine 每笔平仓经 qaa_trade_memory_bridge 实时写入的 ChromaDB，与上方 reindex 库（bge-large）是两套独立存储"
        action={null}
      >
        {qaa?.exists === false ? (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="text-xs">
              <div className="font-medium text-warning">实时库尚未生成</div>
              <div className="text-muted-foreground mt-0.5">暂无平仓教训写入（需 QAA_KNOWLEDGE_BACKEND=chroma 且发生过带 lesson 的平仓）</div>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="实时沉淀文档"
                value={(qaa?.total_docs ?? 0).toLocaleString()}
                tone={qaa?.total_docs ? "good" : "default"}
                hint="每笔平仓教训实时写入"
              />
              <StatCard
                label="库体积"
                value={qaa?.file_size_mb != null ? qaa.file_size_mb + " MB" : "—"}
                hint="qaa_chromadb/chroma.sqlite3"
              />
              <StatCard
                label="最后写入"
                value={qaa?.last_write_at ? fmtTime(qaa.last_write_at) : "—"}
                tone={qaa?.last_write_at ? "good" : "default"}
                hint="平仓教训仍在持续沉淀"
              />
              <StatCard
                label="写入链路"
                value={qaa?.configured?.knowledge_backend === "chroma" ? "chroma ✓" : (qaa?.configured?.knowledge_backend ?? "?")}
                tone={qaa?.configured?.knowledge_backend === "chroma" ? "good" : "bad"}
                hint={(qaa?.configured?.embedding_model ?? "?") + " / " + (qaa?.configured?.embedding_backend ?? "?")}
              />
            </div>
            {qaa?.error && (
              <div className="text-[11px] text-loss">读取统计失败：{qaa.error}</div>
            )}
            <div className="text-xs font-medium mb-2 flex items-center gap-1.5">
              <HardDrive className="w-3.5 h-3.5 text-muted-foreground" />
              Collection 明细（实时库）
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {Object.entries(qaa?.collections ?? {}).length === 0 ? (
                <div className="text-[11px] text-muted-foreground">无 collection 数据</div>
              ) : (
                Object.entries(qaa?.collections ?? {})
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 6)
                  .map(([name, cnt]) => (
                    <div key={name} className="glass rounded-lg p-3">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-[11px] truncate">{name}</span>
                        <Badge variant="outline" className="text-[9px] font-normal tabular-nums border-profit/40 bg-profit/10 text-profit">
                          <span className="w-1 h-1 rounded-full bg-profit shadow-[0_0_6px_currentColor]" />
                          {cnt.toLocaleString()} 文档
                        </Badge>
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function fmtTime(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export default RAGStatusPanel;
