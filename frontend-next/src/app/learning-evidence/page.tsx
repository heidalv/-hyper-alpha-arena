"use client";

/**
 * 学习链证据页（/learning-evidence）
 *
 * [2026-10-04 工作流①②③] 把三条链路的产出集中到一处，**样本不足时明确显示"待积累 N/门槛"**，
 * 而不是一片空白或假数字。数据源全部是**只读**接口：
 *   · GET /api/learning/calibration/forward  → ① 前向收益分桶校准表（含贝叶斯收缩）
 *   · GET /api/learning/shadow/report        → ②③ 影子 vs 真实管线（vol_target / rl）
 *
 * 设计原则（沿用本会话的教训）：
 *   · 比率一律带 n；样本 < 门槛时**不显示任何比率**（防"看着有报表、其实没数据"）
 *   · pending 指标（爆仓/回撤/夏普对比）显式列出，不假装能算
 *   · 时间口径：决策表为本地 naive、行情表为 UTC epoch —— 页面上标注数据截止时间
 */

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
// 路径以既有页面为准（仓库用 operations/IlcUi 提供 SectionCard；Button 文件名为小写）
import { SectionCard } from "@/components/operations/IlcUi";
import { Button } from "@/components/ui/button";

const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

type Bucket = {
  bucket: string;
  n: number;
  win_rate_raw: number;
  win_rate: number;
  avg_ret: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
};

type Calibration = {
  labeled: number;
  skipped: number;
  horizon_days: number;
  buckets: Bucket[];
  by_lane?: Record<string, { n: number; win_rate: number; buckets: Bucket[] }>;
};

type ShadowBlock = {
  workflow: string;
  n: number;
  min_samples: number;
  status: string;
  hint?: string;
  pending?: string[];
  actions_seen?: string[];
  scale_median?: number | null;
  sigma_annual_median?: number | null;
  theoretical_notional_median?: number | null;
  actual_notional_median?: number | null;
  over_budget_ratio?: number;
};

type ShadowReport = { vol_target: ShadowBlock; rl: ShadowBlock };

function pct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

export default function LearningEvidencePage() {
  const [calib, setCalib] = useState<Calibration | null>(null);
  const [shadow, setShadow] = useState<ShadowReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<string>("");

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const [a, b] = await Promise.all([
        fetch(`${API}/api/learning/calibration/forward?days=12&horizon_days=7`).then((r) => r.json()),
        fetch(`${API}/api/learning/shadow/report`).then((r) => r.json()),
      ]);
      setCalib(a as Calibration);
      setShadow(b as ShadowReport);
      setLoadedAt(new Date().toLocaleTimeString("zh-CN"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const bucketRows = calib?.buckets ?? [];

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        title="学习链证据"
        subtitle="①前向收益校准 ②波动率影子 ③RL 影子 —— 只读报表；样本不足时只显示门槛与进度，不产出比率"
        actions={
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={busy}>
            {busy ? "刷新中…" : "刷新"}
          </Button>
        }
      />

      {err ? (
        <SectionCard title="加载失败" action={<span className="text-xs text-loss">{err}</span>}>
          <div className="text-sm text-fg-muted">确认后端 :8000 在运行，且已重启加载新接口。</div>
        </SectionCard>
      ) : null}

      <SectionCard
        title="① 决策前向收益分桶（7 天窗口）"
        action={<span className="text-xs text-fg-muted">已标注 {calib?.labeled ?? "—"} 条 · 跳过 {calib?.skipped ?? "—"} · {loadedAt}</span>}
      >
        {bucketRows.length === 0 ? (
          <div className="text-sm text-warning">
            暂无可标注样本（需决策时间早于 7 天）。当前快照保留期约 8 天 ⇒ 可标注窗口很窄；
            已建独立标注表 `decision_labels`（只增不删）以摆脱该限制，样本将随决策累积。
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-fg-muted">
                <tr>
                  <th className="text-left">置信分桶</th>
                  <th className="text-right">n</th>
                  <th className="text-right">实测胜率</th>
                  <th className="text-right">收缩胜率</th>
                  <th className="text-right">平均收益</th>
                  <th className="text-right">期望值</th>
                </tr>
              </thead>
              <tbody>
                {bucketRows.map((b) => (
                  <tr key={b.bucket} className="border-t border-border">
                    <td className="py-1">{b.bucket}</td>
                    <td className="text-right">{b.n}</td>
                    <td className="text-right">{pct(b.win_rate_raw)}</td>
                    <td className="text-right">{pct(b.win_rate)}</td>
                    <td className="text-right">{pct(b.avg_ret, 2)}</td>
                    <td className={`text-right ${b.expectancy >= 0 ? "text-profit" : "text-loss"}`}>
                      {pct(b.expectancy, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 text-xs text-fg-muted">
              注：收缩胜率 = 贝叶斯收缩（Beta 先验，n0=20）；实测数据曾显示**置信度与胜率反相关**，
              故本表不作单调假设。
            </div>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="② 目标波动率影子（5x 语境）"
        action={<span className="text-xs text-fg-muted">{shadow?.vol_target?.status ?? "—"}</span>}
      >
        <ShadowBody blk={shadow?.vol_target} />
      </SectionCard>

      <SectionCard
        title="③ RL 影子 vs 真实管线"
        action={<span className="text-xs text-fg-muted">{shadow?.rl?.status ?? "—"}</span>}
      >
        <ShadowBody blk={shadow?.rl} />
      </SectionCard>
    </div>
  );
}

function ShadowBody({ blk }: { blk?: ShadowBlock }) {
  if (!blk) return <div className="text-sm text-fg-muted">加载中…</div>;
  const insufficient = blk.status === "insufficient_samples";
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap gap-4">
        <span>
          样本 <b>{blk.n}</b> / 门槛 {blk.min_samples}
        </span>
        {blk.scale_median !== undefined ? <span>scale 中位数 {blk.scale_median ?? "—"}</span> : null}
        {blk.sigma_annual_median !== undefined ? (
          <span>σ̂ 中位数 {pct(blk.sigma_annual_median)}</span>
        ) : null}
        {blk.over_budget_ratio !== undefined ? (
          <span>超风险预算比例 {pct(blk.over_budget_ratio)}</span>
        ) : null}
      </div>
      {insufficient ? (
        <div className="text-warning">{blk.hint ?? "样本不足，暂不产出任何比率"}</div>
      ) : null}
      {blk.actions_seen?.length ? (
        <div className="text-fg-muted">已见动作：{blk.actions_seen.join(" / ")}</div>
      ) : null}
      {blk.pending?.length ? (
        <div className="text-xs text-fg-muted">待数据：{blk.pending.join("；")}</div>
      ) : null}
    </div>
  );
}
