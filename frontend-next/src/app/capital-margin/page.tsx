"use client";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  PieChart, RefreshCw, Loader2, Activity, Shield,
  Brain, Database, Gauge, Layers,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

type CapitalMargin = {
  kpi?: Record<string, any>;
  bucket_weights?: Record<string, number>;
  arb_scorecard?: { kpi?: Record<string, any>; gate?: Record<string, any> } | null;
  agents?: Array<Record<string, any>>;
  model_consistency?: any;
  quota?: Record<string, any>;
  e5?: Array<Record<string, any>>;
  f4?: { passed?: boolean; live_allowed?: boolean } | null;
  paid_data_summary?: Record<string, number>;
  freshness?: Array<{ name: string; exists: boolean; age_h: number | null; stale: boolean }>;
  notes?: string[];
};

function fmt(v: any, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  return n.toFixed(digits);
}

function pct(v: any): string {
  if (v === null || v === undefined) return "—";
  return `${(Number(v) * 100).toFixed(1)}%`;
}

export default function CapitalMarginPage() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: ["capital-margin"],
    queryFn: () => apiRequest<CapitalMargin>("/dashboard/capital-margin?days=14"),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const kpi = data?.kpi || {};
  const buckets = data?.bucket_weights || {};
  const freeze = kpi.promotion_freeze;
  const staleN = (data?.freshness || []).filter((f) => f.stale || !f.exists).length;

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<PieChart className="w-4 h-4" />}
        title="资本与边际"
        subtitle="三桶分配 · 策略贡献 · Agent 可信度 · 数据新鲜度 · 配额"
        refreshHint="60s"
        breadcrumb={[{ label: "系统" }, { label: "资本与边际" }]}
        badge={
          <span className="chip-capsule">
            <span className={cn(
              "w-1.5 h-1.5 rounded-full",
              freeze?.frozen ? "bg-loss" : "bg-profit",
            )} />
            {freeze?.frozen ? "晋升冻结中" : "分配正常"}
          </span>
        }
        actions={
          <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          </Button>
        }
      />

      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> 加载中…
        </div>
      )}

      {error && (
        <Card className="glass p-4 text-sm text-loss">
          加载失败：{(error as Error).message || String(error)}
        </Card>
      )}

      {data && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Kpi label="趋势桶" value={pct(buckets.trend)} icon={Layers} />
            <Kpi label="现金流桶" value={pct(buckets.cashflow)} icon={Activity} />
            <Kpi label="研究桶" value={pct(buckets.research)} icon={Brain} />
            <Kpi
              label="可信度缩放"
              value={fmt(kpi.credibility_scale, 2)}
              icon={Gauge}
              hint="Timing Agent"
            />
            <Kpi label="套利 PnL" value={fmt(kpi.arb_pnl)} icon={PieChart} />
            <Kpi label="套利年化" value={pct(kpi.arb_ann)} icon={Activity} />
            <Kpi label="套利回撤" value={fmt(kpi.arb_mdd)} icon={Shield} />
            <Kpi
              label="数据陈旧源"
              value={String(staleN)}
              icon={Database}
              color={staleN > 0 ? "warning" : "profit"}
            />
          </div>

          <div className="grid md:grid-cols-2 gap-3">
            <Card className="glass p-4 space-y-3">
              <Head icon={Brain} title="Agent 可信度" hint="近 14 天" />
              {(data.agents || []).length === 0 ? (
                <Empty text="尚无已评分预测" />
              ) : (
                <table className="data-table text-sm">
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th className="text-right">已评</th>
                      <th className="text-right">均分</th>
                      <th className="text-right">Brier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.agents || []).slice(0, 12).map((a, i) => (
                      <tr key={i}>
                        <td>{a.agent || a.agent_id || "—"}</td>
                        <td className="text-right">{a.n_scored ?? a.n ?? "—"}</td>
                        <td className="text-right">{fmt(a.avg_score)}</td>
                        <td className="text-right">{fmt(a.avg_brier)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card className="glass p-4 space-y-3">
              <Head icon={Activity} title="E5 事件策略" hint="影子 KPI" />
              {(data.e5 || []).length === 0 ? (
                <Empty text="无 E5 策略注册" />
              ) : (
                <table className="data-table text-sm">
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th className="text-right">N</th>
                      <th className="text-right">净下界 bp</th>
                      <th>门</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.e5 || []).map((e) => (
                      <tr key={e.strategy}>
                        <td className="font-mono text-xs">{e.strategy}</td>
                        <td className="text-right">{e.n_scored ?? "—"}</td>
                        <td className="text-right">{fmt(e.net_lower_bp)}</td>
                        <td>
                          <span className={cn(
                            "text-xs",
                            e.promotion_ready ? "text-profit" : "text-muted-foreground",
                          )}>
                            {e.promotion_ready ? "可晋" : "观察"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <Card className="glass p-4 space-y-2">
              <Head icon={Gauge} title="模型配额" />
              {!data.quota ? (
                <Empty text="配额不可用" />
              ) : (
                <pre className="text-xs text-muted-foreground overflow-auto max-h-40">
                  {JSON.stringify(data.quota, null, 2)}
                </pre>
              )}
            </Card>
            <Card className="glass p-4 space-y-2">
              <Head icon={Shield} title="F4 / 付费决策" />
              <div className="text-sm space-y-1">
                <div>F4 过门：{data.f4?.passed ? "是" : data.f4 ? "否" : "—"}</div>
                <div>Aster Live 允许：{data.f4?.live_allowed ? "是" : "否"}</div>
                <div>付费 BUY/HOLD/SKIP：{" "}
                  {data.paid_data_summary
                    ? `${data.paid_data_summary.BUY ?? 0}/${data.paid_data_summary.HOLD ?? 0}/${data.paid_data_summary.SKIP ?? 0}`
                    : "—"}
                </div>
              </div>
            </Card>
            <Card className="glass p-4 space-y-2">
              <Head icon={Database} title="数据新鲜度" />
              <ul className="text-xs space-y-1 max-h-40 overflow-auto">
                {(data.freshness || []).map((f) => (
                  <li key={f.name} className="flex justify-between gap-2">
                    <span>{f.name}</span>
                    <span className={cn(f.stale || !f.exists ? "text-warning" : "text-muted-foreground")}>
                      {!f.exists ? "缺失" : f.age_h == null ? "?" : `${f.age_h}h`}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          {(data.notes || []).length > 0 && (
            <Card className="glass p-3 text-xs text-muted-foreground">
              {(data.notes || []).join(" · ")}
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Kpi({
  label, value, icon: Icon, color, hint,
}: {
  label: string; value: string;
  icon: React.ComponentType<{ className?: string }>;
  color?: "profit" | "warning" | "loss";
  hint?: string;
}) {
  return (
    <Card className="glass p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        <Icon className="w-3.5 h-3.5 text-muted-foreground" />
      </div>
      <div className={cn(
        "text-lg font-semibold tabular-nums",
        color === "profit" && "text-profit",
        color === "warning" && "text-warning",
        color === "loss" && "text-loss",
      )}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-muted-foreground mt-0.5">{hint}</div>}
    </Card>
  );
}

function Head({
  icon: Icon, title, hint,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string; hint?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Icon className="w-4 h-4 text-muted-foreground" />
      <span className="font-medium text-sm">{title}</span>
      {hint && <span className="text-xs text-muted-foreground ml-auto">{hint}</span>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="text-sm text-muted-foreground py-4 text-center">{text}</div>;
}
