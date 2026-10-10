/**
 * RAG 检索状态面板（v6 10.2.4 语义检索如实展示 + [2026-10-10] 六 collection 对齐）
 *
 * 各 collection 文档数 + 索引时间 + embedding 模型 + 检索可用性。
 * ready=false（not_initialized）时标红，文档在库但未激活绝不粉饰为"可用"。
 *
 * [2026-10-10 两处修正]
 *   ① 原先硬编码的 5 个 collection 名与实际不符（`proposal_wisdom` / `decision_retro` /
 *      `factor_knowledge` / `outcome_lessons` 都不存在），而真实存在的
 *      `strategy_lessons` / `trade_decisions` / `trade_memory` / `knowledge_cards`
 *      一个都没显示 ⇒ 面板在报"未挂载"的其实是幽灵，真库反而看不见。
 *      现在：**以服务端返回为准**，硬编码表只作 fallback 与中文说明。
 *   ② 新增「参考卡片（书/论文）」块：知识卡片是主脑调参的唯一依据来源，
 *      它的索引状态必须有独立入口（含按车道可见数 + 重建按钮）。
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
import {
  getKbCards, reindexKnowledgeCards,
  type KbCardsPayload, type RagReindexResult,
} from "@/lib/learningLaneApi";
import { SectionCard, RefreshButton, StatCard } from "../operations/IlcUi";
import { cn } from "@/lib/utils";
import { AlertTriangle, CheckCircle2, Database, Loader2, HardDrive, BookOpen } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fmtTime } from "@/lib/format";

/** collection → 中文说明。仅用于展示提示；**不是**枚举白名单（服务端返回什么就显示什么）。 */
const COLLECTION_HINT: Record<string, string> = {
  trade_decisions: "决策快照（每笔决策的推理与结果）",
  strategy_lessons: "策略教训（含高频 mm_asterdex_pp 的自动教训）",
  trading_wisdom: "Wisdom 库（提炼后的交易智慧）",
  trade_memory: "交易记忆聚合（按 币×市况）",
  static_knowledge: "静态知识（K 线形态 + 宏观事件）",
  knowledge_cards: "参考卡片（书/论文蒸馏，带出处）",
};

export function RAGStatusPanel() {
  const [health, setHealth] = useState<RagHealthResponse | null>(null);
  const [stats, setStats] = useState<RagStatsResponse | null>(null);
  const [qaa, setQaa] = useState<QaaRagStatsResponse | null>(null);
  const [kb, setKb] = useState<KbCardsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [reindexResult, setReindexResult] = useState<RagReindexResult | null>(null);
  const [reindexErr, setReindexErr] = useState<string | null>(null);

  const refresh = () => {
    setLoading(true);
    // `getKbCards()` 默认 **不**触发 RAG 懒加载（bge-large 要 26s，会把首屏拖垮）；
    // 冷启动的加载交给后端第一次真实检索，或用户在下面点「载入卡片索引」。
    Promise.allSettled([getRagHealth(), getRagStats(), getQaaRagStats(), getKbCards()])
      .then(([h, s, q, k]) => {
        if (h.status === "fulfilled") setHealth(h.value);
        if (s.status === "fulfilled") setStats(s.value);
        if (q.status === "fulfilled") setQaa(q.value);
        if (k.status === "fulfilled") setKb(k.value);
      })
      .finally(() => setLoading(false));
  };

  const onWarmRag = async () => {
    setLoading(true);
    try {
      setKb(await getKbCards(true));
      // 预热完再取一次 stats（collection 计数此时才可读）
      const s = await getRagStats();
      setStats(s);
    } catch (e) {
      setReindexErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 首屏走宏任务触发（本仓约定：effect 体内不直接同步 setState / 不直接触发级联渲染）
    const t0 = window.setTimeout(refresh, 0);
    const t = setInterval(refresh, 30000);
    return () => {
      window.clearTimeout(t0);
      clearInterval(t);
    };
  }, []);

  const onReindexCards = async () => {
    if (!window.confirm(
      "重建知识卡片索引？\n\n" +
      "把 backend/data/knowledge_cards.json 全量重写进 Chroma 的 knowledge_cards collection。\n" +
      "卡片是静态文件，不影响交易；索引重建期间检索会短暂返回空（约数秒）。"
    )) return;
    setReindexing(true);
    setReindexErr(null);
    try {
      setReindexResult(await reindexKnowledgeCards());
      refresh();
    } catch (e) {
      setReindexErr(e instanceof Error ? e.message : String(e));
    } finally {
      setReindexing(false);
    }
  };

  const ready = stats?.ready ?? health?.ready ?? false;
  const degraded = stats?.degraded ?? health?.degraded ?? false;
  const model = stats?.embedding_model ?? health?.embedding_model ?? null;
  const collections = stats?.collections ?? {};
  // 服务端返回为准；一个都没返回时才用兜底表（避免面板整块空掉）
  const collNames = Object.keys(collections).length > 0
    ? Object.keys(collections)
    : Object.keys(COLLECTION_HINT);
  const totalDocs = health?.total_documents ?? Object.values(collections).reduce((s, c) => s + (c.doc_count ?? 0), 0);
  const cardsColl = collections["knowledge_cards"];
  const cardFileCount = kb?.file?.count ?? 0;
  const cardIndexed = kb?.collection?.doc_count ?? cardsColl?.doc_count ?? null;
  const cardMismatch = cardIndexed !== null && cardFileCount > 0 && cardIndexed !== cardFileCount;

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

        {/* 六 collection 明细（以服务端返回为准） */}
        <div className="text-xs font-medium mb-2 flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5 text-muted-foreground" />
          Collection 明细（{collNames.length} 个）
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
                  !c && "opacity-70",
                  name === "knowledge_cards" && "border-cyan-400/30",
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
                  {COLLECTION_HINT[name] || ""}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {c?.last_indexed ? `索引于 ${fmtTime(c.last_indexed)}` : "无索引时间"}
                </div>
              </div>
            );
          })}
        </div>
      </SectionCard>

      {/* ① 参考卡片（书/论文蒸馏）—— 主脑调参的唯一依据来源 */}
      <SectionCard
        title="参考卡片（书 / 论文蒸馏）"
        description="主脑每次改参数都必须引用这里的 card_id；没有编号的提案直接作废。卡片是参考，与本仓成交账冲突时以账为准"
        action={
          <div className="flex items-center gap-2">
            {kb?.collection?.note || kb?.collection?.ready === false ? (
              <Button variant="outline" size="sm" onClick={() => void onWarmRag()} disabled={loading}>
                {loading ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : null}
                载入卡片索引（约 30s）
              </Button>
            ) : null}
            {reindexResult ? (
              <span className={cn("text-[11px]", reindexResult.indexed ? "text-profit" : "text-amber-300")}>
                重建：{reindexResult.indexed ?? 0} 条
                {reindexResult.file_count ? `（文件 ${reindexResult.file_count} 张）` : ""}
              </span>
            ) : null}
            <Button variant="outline" size="sm" onClick={() => void onReindexCards()} disabled={reindexing}>
              {reindexing ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : null}
              重建卡片索引
            </Button>
          </div>
        }
      >
        {kb?.file?.present === false ? (
          <div className="rounded-lg border border-loss/30 bg-loss/5 p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-loss shrink-0 mt-0.5" />
            <div className="text-xs">
              <div className="font-medium text-loss">卡片文件不存在或为空</div>
              <div className="text-muted-foreground mt-0.5">
                期望路径 backend/data/knowledge_cards.json —— 没有它，主脑会因「无可用卡片」而
                <b>放弃本轮调参</b>（这是设计，不是故障）。
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <StatCard label="卡片文件" value={cardFileCount}
                tone={cardFileCount > 0 ? "good" : "bad"} hint="backend/data/knowledge_cards.json" />
              <StatCard label="已索引" value={cardIndexed ?? "—"}
                tone={cardMismatch ? "warn" : (cardIndexed ? "good" : "bad")}
                hint={cardMismatch
                  ? `与文件不一致（文件 ${cardFileCount} / 索引 ${cardIndexed}）⇒ 建议重建`
                  : "collection knowledge_cards"} />
              <StatCard label="最近索引"
                value={kb?.collection?.last_indexed ? fmtTime(kb.collection.last_indexed) : "—"}
                hint={kb?.collection?.last_indexed_source === "file_mtime"
                  ? "（进程内无记录，取文件 mtime 兜底）" : "计划任务 DSH_KB_CARDS 每日 05:00"} />
              <StatCard label="未核实条目" value={(kb?.file?.unverified || []).length}
                tone={(kb?.file?.unverified || []).length ? "warn" : "good"}
                hint="找不到原始出处 ⇒ 不写成卡片（如实列出）" />
            </div>
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground mb-3">
              <BookOpen className="w-3.5 h-3.5" />
              <span>按车道可见：</span>
              {Object.entries(kb?.by_lane || {}).map(([l, n]) => (
                <span key={l} className="rounded border border-border/60 px-1.5 py-0.5">
                  {l} <b className="text-foreground">{n}</b>
                </span>
              ))}
              <span className="ml-auto">
                {kb?.collection?.ready === false || degraded
                  ? "⚠️ 检索不可用：主脑会放弃本轮调参"
                  : "检索可用"}
              </span>
            </div>
            {(kb?.file?.unverified || []).length > 0 ? (
              <details className="text-[11px] text-muted-foreground">
                <summary className="cursor-pointer">未核实的 4 条（点开看原因）</summary>
                <ul className="mt-1 space-y-0.5 pl-4 list-disc">
                  {(kb?.file?.unverified || []).map((u, i) => (
                    <li key={i}><b>{u.title}</b>：{u.why}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        )}
        {reindexErr ? (
          <div className="mt-2 text-[11px] text-loss">重建失败：{reindexErr}</div>
        ) : null}
        {reindexResult?.warning ? (
          <div className="mt-2 text-[11px] text-amber-300">
            后端提示：{reindexResult.warning}（源名不认识或源里没有数据）
          </div>
        ) : null}
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

export default RAGStatusPanel;
