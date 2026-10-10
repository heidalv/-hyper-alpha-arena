"use client";

/**
 * 车道顾问面板（`/intelligent-learning?tab=advisor`）
 *
 * [2026-10-10 前端升级] 把「大模型一张嘴 + 一道关」变成可审计的时间轴：
 *   · 每轮只提**一个**白名单旋钮；
 *   · **必须引用卡片编号**，没有编号这轮作废（时间轴里直接标红）；
 *   · 该车道的统计关与桶门反事实关的通过/拒绝原因；
 *   · 落地后 30 分钟内变差就退回（`self_tuner_verdict`）—— 待判/已判两小块。
 *
 * 设计原则（与 `ChainOverviewPanel` 同款）：判据写在卡片里（`rules`），
 * 空态要说明**为什么空**（触发条件/开关），不显示"0 = 一切正常"的假象。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Activity, BrainCircuit, ChevronDown, ChevronRight,
  ExternalLink, FileText, Info, Loader2, ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SectionCard, RefreshButton } from "@/components/operations/IlcUi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fmtNum, fmtTime } from "@/lib/format";
import {
  getAdvisor, getKbCards,
  type AdvisorJournalItem, type AdvisorPayload, type KnowledgeCard,
} from "@/lib/learningLaneApi";

/** 决策 → 视觉。`cls` 给徽章（含文字色），`border` 给行容器（只染边框/底色）。 */
const DECISION_STYLE: Record<string, { cls: string; border: string; label: string; hint: string }> = {
  applied: { cls: "text-profit border-profit/40 bg-profit/10", border: "border-profit/30 bg-profit/5", label: "已落地", hint: "通过白名单+引用+统计关，已写入参数文件" },
  proposal_only: { cls: "text-cyan-300 border-cyan-400/40 bg-cyan-400/10", border: "border-cyan-400/30 bg-cyan-400/5", label: "只出提案", hint: "MM_BRAIN_APPLY=0，提案只落审计日志" },
  hold: { cls: "text-muted-foreground border-border bg-muted/20", border: "border-border/60", label: "不动", hint: "模型自述证据不足或无改进方向（正确结论之一）" },
  rejected: { cls: "text-loss border-loss/40 bg-loss/10", border: "border-loss/30 bg-loss/5", label: "作废", hint: "越界/无引用/现值不一致 ⇒ 提案被丢弃" },
  blocked_by_gate: { cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", border: "border-amber-400/30 bg-amber-400/5", label: "统计关未过", hint: "该车道近期样本不足或平均非正 ⇒ 参数不动" },
  blocked_by_counterfactual: { cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", border: "border-amber-400/30 bg-amber-400/5", label: "反事实未过", hint: "桶门阈值必须等于历史往返重放出的最优值" },
  kb_unavailable: { cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", border: "border-amber-400/30 bg-amber-400/5", label: "知识库不可用", hint: "检索降级或无可用卡片 ⇒ 本轮放弃（无依据不许改）" },
  llm_invalid: { cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", border: "border-amber-400/30 bg-amber-400/5", label: "模型不可用", hint: "调用失败或返回非合法 JSON ⇒ 本轮放弃" },
  not_due: { cls: "text-muted-foreground border-border bg-muted/20", border: "border-border/60", label: "未到点", hint: "样本未达门槛且距上轮不足间隔" },
  no_whitelist: { cls: "text-muted-foreground border-border bg-muted/20", border: "border-border/60", label: "无白名单", hint: "该车道未登记可调旋钮 ⇒ 结构上只能不动" },
  disabled: { cls: "text-muted-foreground border-border bg-muted/20", border: "border-border/60", label: "已停摆", hint: "MM_BRAIN_ENABLED=0" },
  apply_failed: { cls: "text-loss border-loss/40 bg-loss/10", border: "border-loss/30 bg-loss/5", label: "落地失败", hint: "被钳制/现值冲突/写盘失败 ⇒ 视为失败" },
  error: { cls: "text-loss border-loss/40 bg-loss/10", border: "border-loss/30 bg-loss/5", label: "异常", hint: "顾问自身报错" },
  unknown: { cls: "text-muted-foreground border-border bg-muted/20", border: "border-border/60", label: "未知", hint: "" },
};

const LANE_LABEL: Record<string, string> = { hft: "高频", mid: "中线", long: "长线", all: "全部" };

function decisionStyle(d: string) {
  return DECISION_STYLE[d] || DECISION_STYLE.unknown;
}

export function LaneAdvisorPanel() {
  const [lane, setLane] = useState<string>("");
  const [data, setData] = useState<AdvisorPayload | null>(null);
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [cardDrawer, setCardDrawer] = useState<KnowledgeCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const [adv, kb] = await Promise.allSettled([
        getAdvisor({ lane: lane || undefined, limit: 60, includeRaw: true }),
        getKbCards(),
      ]);
      if (adv.status === "fulfilled") setData(adv.value);
      else setErr(adv.reason instanceof Error ? adv.reason.message : String(adv.reason));
      if (kb.status === "fulfilled") setCards(kb.value?.cards ?? []);
    } finally {
      setBusy(false);
    }
  }, [lane]);

  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    const iv = window.setInterval(() => void load(), 60000);
    return () => {
      window.clearTimeout(t);
      window.clearInterval(iv);
    };
  }, [load]);

  const cardById = useMemo(() => {
    const m = new Map<string, KnowledgeCard>();
    for (const c of cards) m.set(c.id, c);
    return m;
  }, [cards]);

  const counts = data?.decision_counts || {};
  const totalRows = Object.values(counts).reduce((a, b) => a + b, 0);
  const applied = counts.applied || 0;
  const lanes = ["", "hft", "mid", "long"];

  return (
    <div className="space-y-4">
      {/* 结论条 */}
      <div className="glass rounded-lg p-4 flex flex-wrap items-center gap-3">
        <BrainCircuit className="w-4 h-4 text-primary" />
        <span className="text-sm">
          近 {totalRows} 轮：<span className="text-profit">{applied} 轮改了参数</span>
          {" · "}{counts.hold || 0} 轮不动
          {" · "}{counts.rejected || 0} 轮作废
          {" · "}{(counts.blocked_by_gate || 0) + (counts.blocked_by_counterfactual || 0)} 轮未过统计关
        </span>
        <span className="text-xs text-muted-foreground">
          待判 {(data?.pending_verdicts || []).length} · 已判 {(data?.verdict_history || []).length}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <div className="flex items-center gap-1">
            {lanes.map((l) => (
              <button
                key={l || "all"}
                onClick={() => setLane(l)}
                data-testid={`advisor-lane-${l || "all"}`}
                className={cn(
                  "px-2 py-1 text-[11px] rounded border transition-colors",
                  lane === l
                    ? "border-cyan-400/40 bg-cyan-400/10 text-cyan-300"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {l ? (LANE_LABEL[l] || l) : "全部"}
              </button>
            ))}
          </div>
          <RefreshButton onClick={() => void load()} loading={busy} />
        </div>
      </div>

      {!data?.journal_present && !busy ? (
        <div className="rounded-md border border-border/60 bg-muted/10 px-3 py-2 text-xs text-muted-foreground flex gap-2">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <div>
            尚无顾问记录（<code className="font-mono">{data?.sources?.journal ?? "data/lane_advisor_journal.jsonl"}</code>）。
            触发条件：某车道新增约 20 笔（<code className="font-mono">MM_BRAIN_MIN_N</code>）
            或距上轮 1 小时（<code className="font-mono">MM_BRAIN_MAX_GAP_S</code>），先到先算。
            <b>“没有提案”不等于“系统在改参数”</b>——大多数轮次本该是 hold。
          </div>
        </div>
      ) : null}

      {err ? (
        <div className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5" />{err}
        </div>
      ) : null}

      {/* 决策分布 */}
      <SectionCard
        title="最近一轮的决策分布"
        description="每类都在下面给出判据；点卡片可筛掉该类的行（在时间轴里看细节）"
        action={<Activity className="w-3.5 h-3.5 text-primary" />}
      >
        {totalRows === 0 ? (
          <div className="text-xs text-muted-foreground">本次过滤范围内没有记录</div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
            {Object.entries(counts)
              .sort((a, b) => b[1] - a[1])
              .map(([d, n]) => {
                const st = decisionStyle(d);
                return (
                  <div key={d} className={cn("rounded border px-2 py-1.5", st.cls)}
                       title={st.hint} data-testid={`advisor-count-${d}`}>
                    <div className="text-[10px] opacity-80">{st.label}</div>
                    <div className="text-lg font-bold tabular-nums">{n}</div>
                  </div>
                );
              })}
          </div>
        )}
      </SectionCard>

      {/* 白名单 */}
      <SectionCard
        title="旋钮白名单（每轮只许动一个）"
        description="现值 = 该键此刻的真实值；区间 = 后端硬边界。中/长线只收 runtime_tuning 的热生效键：其它 MIDLONG_*/TREND_* 是 import 期读 env，改完要重启，会被改后判定误判成变差"
        action={<ShieldCheck className="w-3.5 h-3.5 text-primary" />}
      >
        <div className="space-y-3">
          {["hft", "mid", "long"].map((ln) => {
            const wl = data?.whitelists?.[ln] || {};
            const cur = data?.current?.[ln] || {};
            const keys = Object.keys(wl);
            return (
              <div key={ln}>
                <div className="text-xs font-medium mb-1">
                  {LANE_LABEL[ln] || ln}
                  <span className="text-muted-foreground font-normal">
                    {" "}（{keys.length} 个键 · {ln === "hft"
                      ? "写 flow_learn_params.json，下一拍生效"
                      : "写 runtime_tuning，60s 内生效"}）
                  </span>
                </div>
                {keys.length === 0 ? (
                  <div className="text-[11px] text-muted-foreground">未登记（结构上只能 hold）</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="text-fg-muted">
                        <tr>
                          <th className="text-left">旋钮</th>
                          <th className="text-right">现值</th>
                          <th className="text-right">允许区间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {keys.map((k) => {
                          const [lo, hi] = wl[k];
                          const v = cur[k];
                          return (
                            <tr key={k} className="border-t border-border/60">
                              <td className="py-1 font-mono text-[11px]">{k}</td>
                              <td className="text-right tabular-nums">
                                {v === null || v === undefined ? "—" : fmtNum(Number(v), 2)}
                              </td>
                              <td className="text-right tabular-nums text-muted-foreground">
                                {fmtNum(lo, 2)} ~ {fmtNum(hi, 2)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </SectionCard>

      {/* 时间轴 */}
      <SectionCard
        title="决策时间轴"
        description="点任意一行展开：模型的理由、它引用了哪张卡片、证据摘要、以及该车道的统计关结论"
        action={
          <span className="text-[11px] text-muted-foreground">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin inline" /> : null}
            {data?.as_of ? ` 截止 ${fmtTime(data.as_of)}` : ""}
          </span>
        }
      >
        {(data?.journal || []).length === 0 ? (
          <div className="text-xs text-muted-foreground">无记录</div>
        ) : (
          <div className="space-y-1.5">
            {(data?.journal || []).map((j, i) => (
              <JournalRow
                key={`${j.ts_iso || i}-${i}`}
                item={j}
                open={open === i}
                onToggle={() => setOpen(open === i ? null : i)}
                cardById={cardById}
                onOpenCard={setCardDrawer}
              />
            ))}
          </div>
        )}
      </SectionCard>

      {/* 待判 / 已判 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard
          title={`待判（${(data?.pending_verdicts || []).length}）`}
          description="已落地但还没到改后判定时间窗的改动"
          action={<Activity className="w-3.5 h-3.5 text-primary" />}
        >
          {(data?.pending_verdicts || []).length === 0 ? (
            <div className="text-xs text-muted-foreground">
              {data?.pending_present === false
                ? "无待判队列文件（说明最近没有落地过改动）"
                : "队列为空：没有未决的改动"}
            </div>
          ) : (
            <div className="space-y-1.5">
              {(data?.pending_verdicts || []).map((p, i) => (
                <div key={`${p.param}-${i}`} className="rounded border border-border/60 px-2 py-1.5 text-xs">
                  <span className="font-mono">{p.param}</span>
                  {" "}{fmtNum(p.old ?? null, 4)} → <b>{fmtNum(p.new ?? null, 4)}</b>
                  <span className="text-muted-foreground">
                    {" "}· 判据 {p.verdict_metric || "—"}
                    {p.verdict_at ? ` · 到点 ${fmtTime(new Date(p.verdict_at * 1000))}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard
          title={`已判（${(data?.verdict_history || []).length}）`}
          description="改后 30 分钟内 mean_y < 改前 × 0.8 就写回旧值（self_tuner_verdict 每 15 分钟跑）"
          action={<ShieldCheck className="w-3.5 h-3.5 text-primary" />}
        >
          {(data?.verdict_history || []).length === 0 ? (
            <div className="text-xs text-muted-foreground">还没有裁决记录</div>
          ) : (
            <div className="space-y-1.5">
              {(data?.verdict_history || []).slice(0, 8).map((v, i) => (
                <div key={`${v.param}-${v.ts}-${i}`} className="rounded border border-border/60 px-2 py-1.5 text-xs">
                  <span className="font-mono">{v.param}</span>
                  {" "}{fmtNum(v.old ?? null, 4)} → {fmtNum(v.new ?? null, 4)}
                  <Badge
                    variant="outline"
                    className={cn("ml-2 text-[9px]",
                      v.verdict === "rollback" || v.rolled_back
                        ? "border-loss/40 text-loss" : "border-profit/40 text-profit")}
                  >
                    {v.rolled_back ? "已回滚" : (v.verdict || "keep")}
                  </Badge>
                  <span className="text-muted-foreground">
                    {" "}改前 n={v.before?.n ?? "—"} v={fmtNum(v.before?.v ?? null, 3)}
                    {" → "}改后 n={v.after?.n ?? "—"} v={fmtNum(v.after?.v ?? null, 3)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* 判据速查 */}
      <SectionCard title="这套闭环的规则（后端原文）" action={<Info className="w-3.5 h-3.5 text-primary" />}>
        <ul className="text-xs text-muted-foreground space-y-1">
          {Object.entries(data?.rules || {}).map(([k, v]) => (
            <li key={k}><span className="font-mono text-[11px]">{k}</span>：{v}</li>
          ))}
        </ul>
      </SectionCard>

      {/* 卡片抽屉 */}
      {cardDrawer ? (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={() => setCardDrawer(null)}>
          <div
            className="w-full max-w-xl h-full overflow-y-auto bg-background border-l border-border p-4 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-2">
              <FileText className="w-4 h-4 mt-0.5 text-primary" />
              <div className="flex-1">
                <div className="font-medium">{cardDrawer.title || cardDrawer.id}</div>
                <div className="text-[11px] text-muted-foreground font-mono">{cardDrawer.id}</div>
              </div>
              <Button variant="outline" size="sm" onClick={() => setCardDrawer(null)}>关闭</Button>
            </div>
            <div className="text-xs space-y-2">
              <div><b>说法</b>：{cardDrawer.claim || "—"}</div>
              <div className="text-loss"><b>何时不适用</b>：{cardDrawer.not_when || "—"}</div>
              {cardDrawer.verified_in_repo ? (
                <div className="text-profit"><b>本仓对照</b>：{cardDrawer.verified_in_repo}</div>
              ) : null}
              <div className="text-muted-foreground"><b>来源</b>：{cardDrawer.source || "—"}</div>
              {cardDrawer.source_url ? (
                <a className="text-cyan-400 underline inline-flex items-center gap-1"
                   href={cardDrawer.source_url} target="_blank" rel="noreferrer">
                  打开出处 <ExternalLink className="w-3 h-3" />
                </a>
              ) : null}
              <div className="text-[10px] text-muted-foreground">
                车道：{(cardDrawer.lanes || ["all"]).map((l) => LANE_LABEL[l] || l).join(" / ")}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function JournalRow({
  item, open, onToggle, cardById, onOpenCard,
}: {
  item: AdvisorJournalItem;
  open: boolean;
  onToggle: () => void;
  cardById: Map<string, KnowledgeCard>;
  onOpenCard: (c: KnowledgeCard) => void;
}) {
  const st = decisionStyle(item.decision);
  const cited = item.cited || [];
  const changed = item.param && item.new !== null && item.new !== undefined;
  return (
    <div className={cn("rounded border", st.border)}>
      <button onClick={onToggle} data-testid="advisor-journal-row"
        className="w-full text-left px-2.5 py-2 flex items-center gap-2 flex-wrap">
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        <span className="text-[11px] text-muted-foreground tabular-nums w-[86px]">
          {item.ts_iso ? fmtTime(item.ts_iso) : "—"}
        </span>
        <Badge variant="outline" className={cn("text-[9px]", st.cls)}>{st.label}</Badge>
        <span className="text-[10px] text-muted-foreground">{LANE_LABEL[item.lane || ""] || item.lane || "—"}</span>
        {changed ? (
          <span className="text-[11px] font-mono">
            {item.param} {fmtNum(item.old ?? null, 4)} → <b>{fmtNum(item.new ?? null, 4)}</b>
          </span>
        ) : null}
        <span className="text-[11px] text-muted-foreground flex-1 truncate">{item.why || st.hint}</span>
        <span className="text-[10px] shrink-0">
          {cited.length > 0 ? (
            <span className="text-cyan-400">引用 {cited.length} 张</span>
          ) : (
            <span className="text-muted-foreground">未引用</span>
          )}
        </span>
      </button>
      {open ? (
        <div className="px-3 pb-2 space-y-2 text-[11px]">
          <div><b>理由</b>：{item.why || "—"}</div>
          <div>
            <b>引用的知识条目</b>：
            {cited.length === 0 ? (
              <span className="text-loss"> 无 ⇒ 本轮作废（规则：没有编号不许改参数）</span>
            ) : (
              <span className="inline-flex flex-wrap gap-1 ml-1">
                {cited.map((cid) => {
                  const c = cardById.get(cid);
                  return (
                    <button key={cid}
                      onClick={() => c && onOpenCard(c)}
                      className={cn("rounded border px-1.5 py-0.5 font-mono text-[10px]",
                        c ? "border-cyan-400/40 text-cyan-300 hover:bg-cyan-400/10"
                          : "border-amber-400/40 text-amber-300")}
                      title={c ? c.title : "该编号不在卡片文件里（可能是索引与文件不一致）"}
                    >
                      {cid}
                    </button>
                  );
                })}
              </span>
            )}
          </div>
          {item.kb ? (
            <div className="text-muted-foreground">
              <b>检索</b>：降级 {String(item.kb.degraded ?? "—")}
              {" · 卡片 "}{item.kb.cards ?? "—"}
              {" · 本仓教训 "}{item.kb.lessons ?? "—"}
              {item.kb.reason ? ` · ${item.kb.reason}` : ""}
            </div>
          ) : null}
          {item.gate ? (
            <div className={item.gate.passed ? "text-profit" : "text-amber-300"}>
              <b>统计关</b>（{item.gate.name || "—"}）：{item.gate.passed ? "通过" : "未通过"} · {item.gate.why || "—"}
            </div>
          ) : null}
          {item.evidence_digest && Object.keys(item.evidence_digest).length > 0 ? (
            <details>
              <summary className="cursor-pointer text-muted-foreground">证据摘要</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] text-muted-foreground">
                {JSON.stringify(item.evidence_digest, null, 2)}
              </pre>
            </details>
          ) : null}
          {item.raw_llm ? (
            <details>
              <summary className="cursor-pointer text-muted-foreground">模型原始输出（截断）</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[10px] text-muted-foreground">
                {item.raw_llm}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default LaneAdvisorPanel;
