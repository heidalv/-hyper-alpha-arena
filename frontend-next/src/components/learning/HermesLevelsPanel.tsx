"use client";

/**
 * Hermes 四级面板（[2026-10-03 用户需求] 「补齐功能显示」）
 *
 * 背景：契约体检发现后端有 **18 个带真实数据的接口从未上屏**，其中 Hermes 家族最集中：
 *   /api/hermes/{dashboard,maturity,architecture,genesis,patterns,block-patterns,health,schedule,wisdom}
 * 实测数据（2026-10-03）：智慧 232 条 / 模式 14 / L3 提案 967（286 待审、0 裁决）/ 
 * L4 候选 468（345 孵化、0 验证、0 晋升、123 失败）/ 成熟度 75。
 *
 * 本面板把这些能力全部呈现，并按「闭环是否真的走通」着色：
 *   · L3 验收：pending>0 且 accepted+rejected==0 ⇒ ❌ 无人裁决
 *   · L4 晋升：validated==0 且 promoted==0 ⇒ ❌ 未走通
 *   · L2 实验：running_ab_tests==0 ⇒ ⚠️ 版本只增不验
 */
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, CheckCircle2, XCircle, Boxes, GitBranch, Sparkles, Server, Loader2,
  ListTree, ShieldAlert, Activity, FlaskConical,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import { SectionCard, RefreshButton } from "@/components/operations/IlcUi";
import { Badge } from "@/components/ui/badge";

const BACKEND = getBackendUrl().replace(/\/$/, "");
const num = (v: unknown): number => (Number.isFinite(Number(v)) ? Number(v) : 0);

function LevelCard({
  title, subtitle, icon: Icon, badge, badgeTone, rows,
}: {
  title: string; subtitle: string; icon: React.ComponentType<{ className?: string }>;
  badge: string; badgeTone: "ok" | "warn" | "bad";
  rows: { label: string; value: string; suffix?: string }[];
}) {
  const tone = {
    ok: { cls: "border-profit/40", chip: "bg-profit/15 text-profit", Icon: CheckCircle2 },
    warn: { cls: "border-amber-400/40", chip: "bg-amber-400/15 text-amber-300", Icon: AlertTriangle },
    bad: { cls: "border-loss/40", chip: "bg-loss/15 text-loss", Icon: XCircle },
  }[badgeTone];
  const ChipIcon = tone.Icon;
  return (
    <div className={cn("glass rounded-lg border p-3 space-y-2", tone.cls)}>
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium">{title}</span>
        <span className={cn("ml-auto text-[10px] px-1.5 py-0.5 rounded flex items-center gap-1", tone.chip)}>
          <ChipIcon className="w-3 h-3" />{badge}
        </span>
      </div>
      <div className="text-[11px] text-muted-foreground">{subtitle}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] text-muted-foreground">{r.label}</span>
            <span className="text-xs font-mono tabular-nums">
              {r.value}{r.suffix ? <span className="text-[10px] text-muted-foreground ml-0.5">{r.suffix}</span> : null}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function HermesLevelsPanel() {
  const [data, setData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  /** [2026-10-03] 批量裁决预览（dry_run）：先看清将要通过哪些提案，再决定是否执行 */
  const [preview, setPreview] = useState<any[] | null>(null);
  /** 驱动停摆天数（load() 中计算后写入） */
  const [staleDaysState, setStaleDaysState] = useState(-1);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    const paths = [
      "dashboard", "maturity", "architecture?status=pending", "genesis", "patterns",
      "block-patterns", "health", "schedule", "wisdom", "task-log", "genesis/incubation",
    ];
    const rs = await Promise.allSettled(
      paths.map((p) => fetch(`${BACKEND}/api/hermes/${p}`).then((r) => r.json())),
    );
    const next: Record<string, any> = {};
    rs.forEach((r, i) => { if (r.status === "fulfilled") next[paths[i]] = r.value; });
    setData(next);
    // 停摆天数：在 setState 阶段算（避免渲染期 Date.now() 触发 react-hooks/purity）
    const log = (next["task-log"]?.tasks ?? []) as any[];
    const lastMs = log.reduce((mx: number, t: any) => {
      const v = Date.parse(String(t?.last_finished_at || ""));
      return Number.isFinite(v) && v > mx ? v : mx;
    }, 0);
    setStaleDaysState(lastMs ? (Date.now() - lastMs) / 86400000 : -1);
    const failed = rs.filter((x) => x.status === "rejected").length;
    if (failed) setErr(`${failed}/${paths.length} 个 Hermes 接口请求失败`);
    setLoading(false);
  }, []);

  /** [2026-10-03 补齐] L3 裁决：接后端已有的 accept / reject / auto-accept-pending / reconcile。 */
  const decide = useCallback(async (id: number, action: "accept" | "reject") => {
    setActing(`${action}-${id}`);
    setMsg(null);
    try {
      const r = await fetch(`${BACKEND}/api/hermes/architecture/${id}/${action}`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      setMsg({ ok: r.ok, text: `提案 #${id} ${action === "accept" ? "通过" : "驳回"}：${JSON.stringify(j).slice(0, 120)}` });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: String(e).slice(0, 140) });
    } finally {
      setActing(null);
    }
  }, [load]);

  const batch = useCallback(async (path: string, label: string) => {
    setActing(label);
    setMsg(null);
    try {
      const r = await fetch(`${BACKEND}${path}`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      setMsg({ ok: r.ok, text: `${label}：${JSON.stringify(j).slice(0, 160)}` });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: String(e).slice(0, 140) });
    } finally {
      setActing(null);
    }
  }, [load]);

  /** [2026-10-03] 批量裁决「预览」：dry_run 不写库，返回将要通过的提案清单。 */
  const previewBatch = useCallback(async () => {
    setActing("预览批量裁决");
    setMsg(null);
    setPreview(null);
    try {
      const r = await fetch(`${BACKEND}/api/hermes/architecture/auto-accept-pending?limit=20&dry_run=true`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      setPreview(j?.preview ?? []);
      setMsg({
        ok: r.ok,
        text: `预览：将裁决 ${j?.would_accept ?? 0} 条（剩余待审 ${j?.remaining_pending ?? "?"}；`
          + `${j?.auto_accept_enabled ? "自动裁决已启用" : "自动裁决未启用 — 这只是预览，不会执行"}）`,
      });
    } catch (e) {
      setMsg({ ok: false, text: String(e).slice(0, 140) });
    } finally {
      setActing(null);
    }
  }, []);
  // [2026-10-03] 首屏加载改为宏任务触发：load() 开头会同步 setLoading(true)，react-hooks/set-state-in-effect 不允许在 effect 体内同步 setState。
  useEffect(() => {
    const t = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const dash = data.dashboard ?? {};
  const l1 = dash.l1_wisdom ?? {};
  const l2 = dash.l2_prompt ?? {};
  const l3 = dash.l3_architecture ?? {};
  const l4 = dash.l4_genesis ?? {};
  const maturity = data.maturity ?? dash.maturity ?? {};
  const hHealth = data.health ?? {};
  const arch = data.architecture ?? {};
  const gen = data.genesis ?? {};
  const patterns: any[] = data.patterns?.patterns ?? [];
  const blocks = data["block-patterns"]?.stats ?? data["block-patterns"] ?? {};
  const wisdom: any[] = data.wisdom?.records ?? [];
  const schedule = data.schedule?.tasks ?? data.schedule ?? {};
  const scheduleTasks: any[] = Array.isArray(schedule)
    ? schedule
    : Object.entries(schedule).map(([k, v]) => ({ job_id: k, ...(typeof v === "object" ? (v as object) : { value: v }) }));
  const liveJobs: string[] = data.schedule?.live_jobs ?? [];
  const pendingProps: any[] = data["architecture?status=pending"]?.proposals ?? [];
  const taskLog: any[] = data["task-log"]?.tasks ?? [];
  const incub = data["genesis/incubation"] ?? {};
  /** 停摆天数（在 load() 里计算，避免渲染期 Date.now() 触发 react-hooks/purity） */
  const staleDays = staleDaysState;

  const l3Verified = num(l3.accepted) + num(l3.rejected) + num(l3.implemented);
  const l3PendingPct = num(l3.total) > 0 ? (num(l3.pending) / num(l3.total)) * 100 : 0;
  const l4Done = num(l4.validated) + num(l4.promoted_live);

  if (loading) {
    return <div className="flex justify-center py-16"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="space-y-4">
      {/* [2026-10-03 补齐] 驱动存活横幅：此前 Hermes 停摆 48 天，界面上完全看不出来 */}
      <div className={cn(
        "rounded-lg border px-3 py-2 text-xs flex flex-wrap items-center gap-2",
        liveJobs.length > 0 ? "border-profit/40 bg-profit/10 text-profit" : "border-loss/40 bg-loss/10 text-loss",
      )}>
        {liveJobs.length > 0 ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
        <span className="font-medium">
          {liveJobs.length > 0
            ? `驱动在跑：后端 APScheduler 已注册 ${liveJobs.join(" / ")}`
            : "无驱动注册：链路不会自动推进（检查 HERMES_SCHEDULER_ENABLED）"}
        </span>
        {staleDays >= 0 && (
          <span className="text-muted-foreground">
            · task_run_log 最近一次运行 {staleDays < 1 ? "今天" : `${staleDays.toFixed(1)} 天前`}
            {staleDays > 2 ? "（2026-08-17 opencode_scheduler 被删后曾长期停摆）" : ""}
          </span>
        )}
        <span className="text-muted-foreground ml-auto">L2/L3 的 LLM 任务默认关闭（HERMES_L2_ENABLED / HERMES_L3_ENABLED）</span>
      </div>

      <div className="glass rounded-lg p-4 flex flex-wrap items-center gap-3">
        <Sparkles className="w-4 h-4 text-primary" />
        <span className="text-sm font-medium">Hermes 进化内核 · 四级通道</span>
        <Badge variant="secondary" className="text-xs">
          成熟度 {num(maturity.maturity_score)}
        </Badge>
        <span className="text-xs text-muted-foreground">
          DB {hHealth.db_ok ? "✅" : "❌"} · LLM sidecar {hHealth.sidecar_ok ? "✅" : "❌（L2/L3/L4 依赖它）"}
        </span>
        <span className="text-xs text-muted-foreground ml-auto">数据源 /api/hermes/*（9 个接口）</span>
        <RefreshButton onClick={() => void load()} loading={loading} />
      </div>

      {err && (
        <div className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5" />{err}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <LevelCard
          title="L1 · 智慧库" icon={Boxes}
          subtitle="参数级成功率 → 智慧记录（/api/hermes/wisdom）"
          badge={num(l1.total_records) > 0 ? "在跑" : "无数据"}
          badgeTone={num(l1.total_records) > 0 ? "ok" : "bad"}
          rows={[
            { label: "智慧记录", value: String(num(l1.total_records)), suffix: "条" },
            { label: "参数模式", value: String(num(l1.patterns)), suffix: "个" },
            { label: "成熟度分", value: String(num(maturity.l1_wisdom)) },
          ]}
        />
        <LevelCard
          title="L2 · 提示词" icon={GitBranch}
          subtitle="磁盘 .md ↔ DB active 版本（/api/hermes/prompts/diff）"
          badge={num(l2.running_ab_tests) > 0 ? "在跑" : "只增不验"}
          badgeTone={num(l2.running_ab_tests) > 0 ? "ok" : "warn"}
          rows={[
            { label: "active 版本", value: String(num(l2.active_versions)), suffix: "个" },
            { label: "A/B 实验", value: String(num(l2.running_ab_tests)), suffix: "个" },
            { label: "成熟度分", value: String(num(maturity.l2_prompt)) },
          ]}
        />
        <LevelCard
          title="L3 · 架构提案" icon={ListTree}
          subtitle="提案 → 裁决 → 实施（/api/hermes/architecture）"
          // [2026-10-03 自我纠正] accept_proposal 直接落 implemented（实测 pending 286→285、
          // implemented 681→682），accepted/rejected 计数恒为 0 是状态词汇设计 ⇒ 不能据此判"断链"。
          // 用 pending 占比判定积压。
          badge={l3PendingPct > 25 ? `待审积压 ${l3PendingPct.toFixed(0)}%` : "在跑"}
          badgeTone={l3PendingPct > 40 ? "bad" : l3PendingPct > 25 ? "warn" : "ok"}
          rows={[
            { label: "提案总数", value: String(num(l3.total)) },
            { label: "待审 pending", value: String(num(l3.pending)) },
            { label: "已实施", value: String(num(l3.implemented)) },
            { label: "accepted/rejected", value: `${num(l3.accepted)} / ${num(l3.rejected)}`, suffix: "（词汇未用）" },
            { label: "成熟度分", value: String(num(maturity.l3_architecture)) },
            { label: "接口返回", value: String((arch.proposals ?? []).length), suffix: "条" },
          ]}
        />
        <LevelCard
          title="L4 · 起源候选" icon={FlaskConical}
          subtitle="候选 → 孵化 → 验证 → 上实盘（/api/hermes/genesis）"
          badge={l4Done === 0 && num(l4.total) > 0 ? "晋升闭环断" : "在跑"}
          badgeTone={l4Done === 0 && num(l4.total) > 0 ? "bad" : "ok"}
          rows={[
            { label: "候选总数", value: String(num(l4.total)) },
            { label: "孵化中", value: String(num(l4.incubating)) },
            { label: "已验证", value: String(num(l4.validated)) },
            { label: "已晋升实盘", value: String(num(l4.promoted_live)) },
            { label: "失败", value: String(num(l4.failed)) },
            { label: "成熟度分", value: String(num(maturity.l4_genesis)) },
          ]}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <SectionCard title={`参数模式库（${patterns.length} 条）`} action={<Boxes className="w-3.5 h-3.5 text-primary" />}>
          <div className="space-y-1 max-h-[260px] overflow-y-auto">
            {patterns.length === 0 ? (
              <div className="text-xs text-muted-foreground">暂无模式</div>
            ) : patterns.slice(0, 20).map((p, i) => (
              <div key={i} className="text-[11px] flex items-center gap-2 border-b border-border/30 py-1">
                <span className="font-mono text-cyan-300 truncate max-w-[46%]" title={String(p.param_key ?? "")}>
                  {String(p.param_key ?? "—")}
                </span>
                <span className="text-muted-foreground">{String(p.market_condition ?? "")}</span>
                <span className={cn("px-1 rounded text-[10px]",
                  String(p.outcome) === "improved" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                  {String(p.outcome ?? "—")}
                </span>
                <span className="ml-auto tabular-nums" title="平均 PnL 影响">
                  {p.avg_pnl_impact != null ? `${Number(p.avg_pnl_impact) >= 0 ? "+" : ""}${Number(p.avg_pnl_impact).toFixed(1)}` : "—"}
                </span>
                <span className="text-muted-foreground tabular-nums" title="样本数">n={String(p.sample_count ?? "—")}</span>
                <span className="text-muted-foreground tabular-nums" title="平均置信度">
                  conf={p.confidence_avg != null ? Number(p.confidence_avg).toFixed(2) : "—"}
                </span>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard
          title={`决策拦截模式${blocks.from_snapshots?.totals ? `（${blocks.from_snapshots.window_days} 天 ${num(blocks.from_snapshots.totals.decisions)} 次决策 · 执行率 ${(num(blocks.from_snapshots.totals.exec_rate) * 100).toFixed(1)}%）` : ""}`}
          action={<ShieldAlert className="w-3.5 h-3.5 text-primary" />}
        >
          {!blocks.from_snapshots ? (
            <div className="text-xs text-muted-foreground">暂无数据</div>
          ) : (
            <div className="space-y-3">
              {/* 按车道：谁在开单、谁一次没开 */}
              <div className="overflow-x-auto">
                <table className="data-table w-full text-[11px]">
                  <thead>
                    <tr className="text-muted-foreground border-b border-border">
                      <th className="text-left py-1 px-2">车道</th>
                      <th className="text-left py-1 px-2">周期</th>
                      <th className="text-right py-1 px-2">决策</th>
                      <th className="text-right py-1 px-2">执行</th>
                      <th className="text-right py-1 px-2">执行率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(blocks.from_snapshots.by_lane ?? []).map((l: any, i: number) => {
                      const rate = num(l.exec_rate) * 100;
                      return (
                        <tr key={i} className="border-b border-border/30">
                          <td className="py-1 px-2 font-mono">{String(l.lane)}</td>
                          <td className="py-1 px-2">{String(l.tier)}</td>
                          <td className="py-1 px-2 text-right tabular-nums">{num(l.decisions)}</td>
                          <td className="py-1 px-2 text-right tabular-nums">{num(l.executed)}</td>
                          <td className={cn("py-1 px-2 text-right tabular-nums font-medium",
                            rate === 0 ? "text-loss" : rate < 5 ? "text-amber-300" : "text-profit")}>
                            {rate.toFixed(1)}%
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* 理由分类：为什么被拦 */}
              <div className="space-y-1">
                <div className="text-[11px] text-muted-foreground">
                  拦截理由分类（对 {num(blocks.from_snapshots.classified_rows)} 条真实 AI 理由做关键词归类；每类附一条原样摘录）
                </div>
                {(blocks.from_snapshots.reason_categories ?? []).map((c: any, i: number) => (
                  <div key={i} className="rounded border border-border/40 px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-medium">{String(c.category)}</span>
                      <span className="ml-auto text-[11px] tabular-nums text-cyan-300">×{num(c.count)}</span>
                    </div>
                    {c.example && (
                      <div className="text-[10px] text-muted-foreground mt-0.5 truncate" title={String(c.example)}>
                        {String(c.example)}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* 拦截层级（结构化字段） */}
              {(blocks.from_snapshots.by_layer ?? []).length > 0 && (
                <div className="text-[11px] text-muted-foreground">
                  拦截层级：{(blocks.from_snapshots.by_layer ?? []).slice(0, 5)
                    .map((l: any) => `${l.layer}${l.rule ? `/${l.rule}` : ""}×${num(l.count)}`).join(" · ")}
                  <span className="block mt-0.5">
                    注：decision_snapshots.gate_blocks_json 全表为空、evaluate_verdict_json.reason 是空串 ⇒
                    层级字段只能给出 master；理由只有 ai_reasoning 自由文本可用（上游未结构化记录拦截原因，属待补齐项）。
                  </span>
                </div>
              )}
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard title={`智慧记录（共 ${num(data.wisdom?.total)} 条，显示前 ${wisdom.length}）`} action={<Sparkles className="w-3.5 h-3.5 text-primary" />}>
        <div className="overflow-x-auto">
          <table className="data-table w-full text-[11px]">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-1.5 px-2">ID</th>
                <th className="text-left py-1.5 px-2">参数</th>
                <th className="text-left py-1.5 px-2">方向</th>
                <th className="text-left py-1.5 px-2">市况</th>
                <th className="text-left py-1.5 px-2">结果</th>
                <th className="text-left py-1.5 px-2">焦点</th>
              </tr>
            </thead>
            <tbody>
              {wisdom.slice(0, 15).map((w, i) => (
                <tr key={i} className="border-b border-border/30">
                  <td className="py-1 px-2 font-mono">{String(w.id ?? "—")}</td>
                  <td className="py-1 px-2 font-mono text-cyan-300">{String(w.param_key ?? "—")}</td>
                  <td className="py-1 px-2">{String(w.param_direction || "—")}</td>
                  <td className="py-1 px-2">{String(w.market_condition || "—")}</td>
                  <td className={cn("py-1 px-2",
                    String(w.outcome) === "win" ? "text-profit"
                      : String(w.outcome) === "loss" ? "text-loss" : "text-muted-foreground")}>
                    {String(w.outcome ?? "—")}
                  </td>
                  <td className="py-1 px-2 text-muted-foreground">{String(w.focus || "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="调度注册表（/api/hermes/schedule）" action={<Server className="w-3.5 h-3.5 text-primary" />}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {Object.entries(schedule).length === 0 ? (
            <div className="text-xs text-muted-foreground col-span-full">无调度数据</div>
          ) : Object.entries(schedule).slice(0, 12).map(([k, v]) => (
            <div key={k} className="rounded border border-border/50 px-2 py-1.5 flex items-center gap-2">
              <Activity className="w-3 h-3 text-primary shrink-0" />
              <span className="text-[11px] font-mono truncate">{k}</span>
              <span className="ml-auto text-[11px] text-muted-foreground truncate max-w-[45%]">
                {typeof v === "object" ? JSON.stringify(v).slice(0, 50) : String(v)}
              </span>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* [2026-10-03 补齐] L3 验收闭环：此前 286 条 pending、accepted+rejected=0（提案无人裁决）。
          后端接口一直存在（accept / reject / auto-accept-pending / reconcile-implemented），只是前端没接。 */}
      <SectionCard
        title={`L3 提案裁决（待审 ${num(l3.pending)} 条 · 当前列出 ${pendingProps.length} 条）`}
        action={
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => void previewBatch()}
              disabled={acting !== null}
              data-testid="preview-batch-decide"
              className="text-[11px] px-2 py-1 rounded border border-cyan-400/40 text-cyan-300 hover:bg-cyan-400/10 disabled:opacity-50"
            >
              {acting === "预览批量裁决" ? "预览中…" : "预览批量裁决"}
            </button>
            <button
              onClick={() => void batch("/api/hermes/architecture/auto-accept-pending?limit=20", "批量自动裁决 20 条")}
              disabled={acting !== null}
              className="text-[11px] px-2 py-1 rounded border border-amber-400/40 text-amber-300 hover:bg-amber-400/10 disabled:opacity-50"
            >
              {acting === "批量自动裁决 20 条" ? "执行中…" : "批量自动裁决 20 条"}
            </button>
            <button
              onClick={() => void batch("/api/hermes/architecture/reconcile-implemented?limit=100", "对账已实施")}
              disabled={acting !== null}
              className="text-[11px] px-2 py-1 rounded border border-border text-muted-foreground hover:bg-muted/30 disabled:opacity-50"
            >
              {acting === "对账已实施" ? "执行中…" : "对账已实施"}
            </button>
          </div>
        }
      >
        {msg && (
          <div className={cn("mb-2 rounded border px-2 py-1.5 text-[11px] break-all",
            msg.ok ? "border-profit/40 bg-profit/10 text-profit" : "border-loss/40 bg-loss/10 text-loss")}>
            {msg.text}
          </div>
        )}
        {preview && preview.length > 0 && (
          <div className="mb-2 rounded border border-cyan-400/30 bg-cyan-400/5 px-2 py-1.5">
            <div className="text-[11px] text-cyan-300 mb-1">
              预览清单（{preview.length} 条，均**未执行**）——批量自动裁决会把这些直接置为 implemented：
            </div>
            <div className="space-y-0.5 max-h-[160px] overflow-y-auto">
              {preview.map((p: any) => (
                <div key={String(p.id)} className="text-[10px] flex items-center gap-2">
                  <span className="font-mono text-muted-foreground">#{String(p.id)}</span>
                  <span className="px-1 rounded bg-muted/40">{String(p.category ?? "")}</span>
                  <span className="text-muted-foreground">feas={String(p.feasibility ?? "?")}</span>
                  <span className="truncate">{String(p.title ?? "")}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="space-y-1.5 max-h-[320px] overflow-y-auto">
          {pendingProps.length === 0 ? (
            <div className="text-xs text-muted-foreground">
              没有 pending 提案（或接口未返回）。若上方显示待审 &gt; 0 而这里为空，请检查 /api/hermes/architecture?status=pending。
            </div>
          ) : pendingProps.slice(0, 12).map((p) => (
            <div key={p.id} className="rounded border border-border/50 px-2 py-2 space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-muted-foreground">#{p.id}</span>
                <span className="text-[11px] px-1.5 py-0.5 rounded bg-muted/40">{String(p.category || "—")}</span>
                {p.feasibility != null && (
                  <span className="text-[10px] text-muted-foreground">可行性 {String(p.feasibility)}</span>
                )}
                <span className="ml-auto flex gap-1">
                  <button
                    onClick={() => void decide(p.id, "accept")}
                    disabled={acting !== null}
                    data-testid={`accept-${p.id}`}
                    className="text-[11px] px-2 py-0.5 rounded border border-profit/40 text-profit hover:bg-profit/10 disabled:opacity-50"
                  >
                    {acting === `accept-${p.id}` ? "…" : "通过"}
                  </button>
                  <button
                    onClick={() => void decide(p.id, "reject")}
                    disabled={acting !== null}
                    data-testid={`reject-${p.id}`}
                    className="text-[11px] px-2 py-0.5 rounded border border-loss/40 text-loss hover:bg-loss/10 disabled:opacity-50"
                  >
                    {acting === `reject-${p.id}` ? "…" : "驳回"}
                  </button>
                </span>
              </div>
              <div className="text-[11px] font-medium">{String(p.title || "")}</div>
              <div className="text-[10px] text-muted-foreground line-clamp-2">{String(p.description || "").slice(0, 260)}</div>
            </div>
          ))}
        </div>
      </SectionCard>

      {/* [2026-10-03 补齐] L4 独立孵化通道：345 个候选此前只有 12 个策略存在、且挂在用户账户 14 上、
          没有执行者 ⇒ 永远 0 笔。这里给它一条独立通道（专用账户 + 专用会话），并如实显示阻塞原因。 */}
      <SectionCard
        title={`L4 孵化通道（候选 ${num(incub.bound ?? incub.incubating_candidates)} 条孵化中 · 绑定策略 ${num(incub.strategies_bound)} · 活跃 ${num(incub.strategies_active)}）`}
        action={
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => void batch("/api/hermes/genesis/incubation/repair?limit=12", "修复/改绑策略")}
              disabled={acting !== null}
              className="text-[11px] px-2 py-1 rounded border border-border text-muted-foreground hover:bg-muted/30 disabled:opacity-50"
            >
              {acting === "修复/改绑策略" ? "执行中…" : "修复/改绑策略"}
            </button>
            <button
              onClick={() => void batch("/api/hermes/genesis/incubation/start?limit=12", "启动孵化会话")}
              disabled={acting !== null || !!incub.session_running}
              className="text-[11px] px-2 py-1 rounded border border-profit/40 text-profit hover:bg-profit/10 disabled:opacity-50"
            >
              {acting === "启动孵化会话" ? "启动中…" : "启动孵化会话"}
            </button>
            <button
              onClick={() => void batch("/api/hermes/genesis/incubation/stop", "停止孵化会话")}
              disabled={acting !== null || !incub.session_running}
              className="text-[11px] px-2 py-1 rounded border border-loss/40 text-loss hover:bg-loss/10 disabled:opacity-50"
            >
              {acting === "停止孵化会话" ? "停止中…" : "停止孵化会话"}
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
          <div className="rounded border border-border/50 px-2 py-1.5">
            <div className="text-muted-foreground">孵化账户</div>
            <div className="font-mono">
              {incub.incubator_account_id ? `#${incub.incubator_account_id} ${incub.incubator_account_name ?? ""}` : "未创建"}
            </div>
          </div>
          <div className="rounded border border-border/50 px-2 py-1.5">
            <div className="text-muted-foreground">孵化会话</div>
            <div className={cn("font-mono", incub.session_running ? "text-profit" : "text-muted-foreground")}>
              {incub.session_id ? `${incub.session_id} · ${incub.session_status}` : "无"}
            </div>
          </div>
          <div className="rounded border border-border/50 px-2 py-1.5">
            <div className="text-muted-foreground">总开关</div>
            <div className={cn("font-mono", incub.enabled ? "text-profit" : "text-amber-300")}>
              {incub.enabled ? "已开" : "未开（HERMES_L4_INCUBATION_ENABLED）"}
            </div>
          </div>
          <div className="rounded border border-border/50 px-2 py-1.5">
            <div className="text-muted-foreground">达标门槛</div>
            <div className="font-mono">≥{num(incub.min_paper_trades)} 笔 / 胜率≥45% / 均盈≥$1</div>
          </div>
        </div>
        {(incub.blocking ?? []).length > 0 && (
          <div className="mt-2 rounded border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 text-[11px] text-amber-300 space-y-0.5">
            {(incub.blocking as string[]).map((b, i) => (
              <div key={i}>· {b}</div>
            ))}
          </div>
        )}
        <div className="mt-2 text-[11px] text-muted-foreground">
          说明：孵化交易跑在**专用纸面账户**上，不占用你的账户；没有这条通道时，候选即使挂着
          「孵化中」也永远攒不到 30 笔（实测 345 条候选的 paper_trades 全为 0）。
          {incub.hint ? ` ${incub.hint}` : ""}
        </div>
      </SectionCard>

      {/* [2026-10-03 补齐] 任务运行表：让"驱动停摆"这件事可观测（此前只能进 SQLite 看） */}
      <SectionCard title={`任务运行记录 task_run_log（${taskLog.length} 条，按最近运行排序）`} action={<Activity className="w-3.5 h-3.5 text-primary" />}>
        <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
          <table className="data-table w-full text-[11px]">
            <thead>
              <tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-1.5 px-2">任务</th>
                <th className="text-left py-1.5 px-2">状态</th>
                <th className="text-left py-1.5 px-2">最后运行</th>
                <th className="text-right py-1.5 px-2">累计次数</th>
                <th className="text-left py-1.5 px-2">错误</th>
              </tr>
            </thead>
            <tbody>
              {taskLog.slice(0, 25).map((t, i) => {
                const live = liveJobs.includes(String(t.job_id));
                return (
                  <tr key={i} className="border-b border-border/30">
                    <td className="py-1 px-2 font-mono">
                      {live && <span className="inline-block w-1.5 h-1.5 rounded-full bg-profit mr-1" title="本次已注册驱动" />}
                      {String(t.job_id)}
                    </td>
                    <td className={cn("py-1 px-2", String(t.last_status) === "ok" ? "text-profit" : "text-loss")}>
                      {String(t.last_status ?? "—")}
                    </td>
                    <td className="py-1 px-2 tabular-nums">{String(t.last_finished_at || "—").slice(0, 19).replace("T", " ")}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{String(t.run_count ?? "—")}</td>
                    <td className="py-1 px-2 text-muted-foreground">{String(t.last_error || "").slice(0, 60)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="mt-2 text-[11px] text-muted-foreground">
          L4 校验门槛：paper 交易 ≥ 30 笔、胜率 ≥ 45%、单笔均盈 ≥ $1、孵化 ≥ 3 天
          —— 当前 {num(l4.incubating)} 个孵化候选的 paper_trades 全为 0，
          即 <span className="text-loss">L4 依赖纸面交易在跑</span>；没有运行中的会话/策略时它无法推进。
        </div>
      </SectionCard>
    </div>
  );
}

export default HermesLevelsPanel;

