"use client";

/**
 * FactorOverviewPanel — 因子报告卡（雏形）
 *
 * v6 计划 9.1：admission_gate 判定徽章（通过/观察池/拒绝 + 门槛明细）、
 * 数据完整率、分层回测显著性 / 衰减。
 *
 * 数据源：GET /api/monitor/factor-eval/{symbol}
 *   reports[]: { factor_id, ic_mean, ic_std, icir, ic_positive_pct,
 *                ic_decay_halflife, turnover, monotonicity, tail_risk,
 *                grade, data_points }
 *
 * grade 语义（factor_evaluator）：
 *   A = |IC|>5% 且 ICIR>0.5；B = |IC|>3% 且 ICIR>0.3；
 *   C = |IC|>1.5%；D = |IC|>0.5%；F = 其余。
 * 徽章映射：A/B → 通过（绿）；C/D → 观察池（黄）；F → 拒绝（红）。
 * 数据完整率 = data_points / 评估窗口上限（API 拉取 limit=500）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

const MAX_EVAL_POINTS = 500;

interface FactorEvalReport {
  factor_id: string;
  ic_mean: number;
  ic_std: number;
  icir: number;
  ic_positive_pct: number;
  ic_decay_halflife: number | null;
  turnover: number;
  monotonicity: number;
  tail_risk: number;
  grade: string;
  data_points: number;
}

interface EvalResponse {
  symbol?: string;
  reports?: FactorEvalReport[];
  message?: string;
  error?: string;
}

type GateVerdict = "pass" | "watch" | "reject";

function gateVerdict(grade: string): { verdict: GateVerdict; label: string; color: string; bg: string; detail: string } {
  const g = (grade || "F").toUpperCase();
  if (g === "A" || g === "B") {
    return {
      verdict: "pass",
      label: "通过",
      color: "#22c55e",
      bg: "rgba(34,197,94,0.12)",
      detail: "|IC| ≥ 3% 且 ICIR ≥ 0.3（A 档还需 |IC|≥5%/ICIR≥0.5）",
    };
  }
  if (g === "C" || g === "D") {
    return {
      verdict: "watch",
      label: "观察池",
      color: "#eab308",
      bg: "rgba(234,179,8,0.12)",
      detail: "|IC| ≥ 0.5%（C 档 1.5% / D 档 0.5%），样本外待验证",
    };
  }
  return {
    verdict: "reject",
    label: "拒绝",
    color: "#ef4444",
    bg: "rgba(239,68,68,0.12)",
    detail: "|IC| < 0.5%，显著性不足",
  };
}

function fmt(n: number | null | undefined, digits = 3): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return n.toFixed(digits);
}

export default function FactorOverviewPanel({ symbol }: { symbol: string }) {
  const [reports, setReports] = useState<FactorEvalReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${getBackendUrl()}/api/monitor/factor-eval/${encodeURIComponent(symbol)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as EvalResponse;
      if (json.error) throw new Error(json.error);
      setReports(Array.isArray(json.reports) ? json.reports : []);
      setMessage(json.message ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const stats = useMemo(() => {
    const usable = reports.filter((r) => ["A", "B", "C"].includes(r.grade)).length;
    const avgIc =
      reports.length > 0
        ? reports.reduce((s, r) => s + Math.abs(r.ic_mean || 0), 0) / reports.length
        : 0;
    const avgIcir =
      reports.length > 0
        ? reports.reduce((s, r) => s + Math.abs(r.icir || 0), 0) / reports.length
        : 0;
    return { total: reports.length, usable, avgIc, avgIcir };
  }, [reports]);

  const chartData = useMemo(() => {
    return [...reports]
      .sort((a, b) => Math.abs(b.ic_mean || 0) - Math.abs(a.ic_mean || 0))
      .slice(0, 8)
      .map((r) => ({
        name: r.factor_id.length > 12 ? r.factor_id.slice(0, 11) + "…" : r.factor_id,
        ic: Math.abs(r.ic_mean || 0),
        grade: r.grade,
        full: r.factor_id,
      }));
  }, [reports]);

  return (
    <div className="space-y-4">
      {/* 报告卡统计条 */}
      <div className="grid grid-cols-4 gap-3">
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1">评估因子数</div>
          <div className="text-lg font-bold tabular-nums">{stats.total}</div>
          <div className="text-[10px] text-muted-foreground">
            {loading ? "加载中" : `${stats.usable} 个可用 (A/B/C)`}
          </div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1">平均 |IC|</div>
          <div className="text-lg font-bold tabular-nums">{fmt(stats.avgIc * 100)}%</div>
          <div className="text-[10px] text-muted-foreground">分层回测显著性</div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1">平均 |ICIR|</div>
          <div className="text-lg font-bold tabular-nums">{fmt(stats.avgIcir)}</div>
          <div className="text-[10px] text-muted-foreground">IC 稳定性</div>
        </Card>
        <Card className="p-3">
          <div className="text-[10px] text-muted-foreground mb-1">admission 通过率</div>
          <div className="text-lg font-bold tabular-nums">
            {stats.total > 0 ? `${Math.round((stats.usable / stats.total) * 100)}%` : "—"}
          </div>
          <div className="text-[10px] text-muted-foreground">过 Gate 的因子占比</div>
        </Card>
      </div>

      {/* IC 显著性条形图（分层回测显著性概览） */}
      {chartData.length > 0 && (
        <Card className="p-3">
          <div className="text-xs font-medium mb-2 flex items-center gap-1">
            <FlaskConical className="w-3.5 h-3.5 text-primary" />
            Top-{chartData.length} 因子 |IC| 显著性
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <XAxis dataKey="name" tick={{ fontSize: 9 }} interval={0} angle={-28} textAnchor="end" height={46} />
              <YAxis tick={{ fontSize: 9 }} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
              <Tooltip
                formatter={(v: any) => `${(Number(v) * 100).toFixed(2)}%`}
                labelFormatter={(l) => `因子 ${l}`}
                contentStyle={{ fontSize: 11 }}
              />
              <Bar dataKey="ic" radius={[3, 3, 0, 0]}>
                {chartData.map((d) => (
                  <Cell key={d.name} fill={d.grade === "F" ? "#ef4444" : d.grade === "D" ? "#eab308" : "#22c55e"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* 报告明细表 */}
      {loading && reports.length === 0 ? (
        <Card className="p-6 text-center text-xs text-muted-foreground">评估中...</Card>
      ) : error ? (
        <Card className="p-3 text-xs text-loss bg-loss/5 border-loss/30">加载失败：{error}</Card>
      ) : message ? (
        <Card className="p-3 text-xs text-muted-foreground">{message}</Card>
      ) : reports.length === 0 ? (
        <Card className="p-6 text-center text-xs text-muted-foreground">暂无评估报告</Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border/50 bg-muted/30 text-xs font-medium">
            因子报告卡（按 |IC| 降序）
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground border-b border-border/50">
                  <th className="px-4 py-2 font-medium">因子</th>
                  <th className="px-4 py-2 font-medium">admission 判定</th>
                  <th className="px-4 py-2 font-medium text-right">|IC|</th>
                  <th className="px-4 py-2 font-medium text-right">ICIR</th>
                  <th className="px-4 py-2 font-medium text-right">衰减半衰期</th>
                  <th className="px-4 py-2 font-medium">数据完整率</th>
                </tr>
              </thead>
              <tbody>
                {[...reports]
                  .sort((a, b) => Math.abs(b.ic_mean || 0) - Math.abs(a.ic_mean || 0))
                  .slice(0, 30)
                  .map((r) => {
                    const gate = gateVerdict(r.grade);
                    const completeness = Math.min(1, (r.data_points || 0) / MAX_EVAL_POINTS);
                    return (
                      <tr key={r.factor_id} className="border-b border-border/30">
                        <td className="px-4 py-2 font-mono text-xs max-w-[200px] truncate" title={r.factor_id}>
                          {r.factor_id}
                        </td>
                        <td className="px-4 py-2">
                          <Badge
                            variant="secondary"
                            title={`门槛明细：${gate.detail}`}
                            className="font-medium"
                            style={{ color: gate.color, backgroundColor: gate.bg }}
                          >
                            {r.grade} · {gate.label}
                          </Badge>
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">
                          <span className={cn("font-medium", Math.abs(r.ic_mean) >= 0.03 ? "text-profit" : Math.abs(r.ic_mean) >= 0.015 ? "text-warning" : "text-muted-foreground")}>
                            {fmt(r.ic_mean * 100)}%
                          </span>
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                          {fmt(r.icir)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                          {r.ic_decay_halflife != null ? `${r.ic_decay_halflife} bar` : "—"}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-2">
                            <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden">
                              <div
                                className={cn("h-full rounded-full", completeness >= 0.9 ? "bg-profit" : completeness >= 0.6 ? "bg-warning" : "bg-loss")}
                                style={{ width: `${Math.max(4, completeness * 100)}%` }}
                              />
                            </div>
                            <span className="text-[10px] text-muted-foreground tabular-nums">
                              {r.data_points}/{MAX_EVAL_POINTS}
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="flex justify-between items-center text-[10px] text-muted-foreground pt-1">
        <span>评估窗口：近 {MAX_EVAL_POINTS} 根 1h K 线（unified_data_pool 增强）</span>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading} className="h-6 text-xs">
          <RefreshCw className={cn("w-3 h-3 mr-1", loading && "animate-spin")} />
          {loading ? "评估中..." : "刷新"}
        </Button>
      </div>
    </div>
  );
}
