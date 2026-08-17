"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

type TierRow = {
  tp_pct?: number;
  sl_pct?: number;
  oos_avg?: number;
  oos_n?: number;
  oos_win_rate?: number;
  source?: string;
  period?: string;
  shape?: string;
};

type Status = {
  enabled?: boolean;
  auto_train?: boolean;
  schedule?: string;
  exists?: boolean;
  updated_at?: string | null;
  age_hours?: number | null;
  by_tier?: Record<string, TierRow>;
  elapsed_sec?: number;
  ok?: boolean;
  path?: string;
  last_auto?: {
    at?: string | null;
    source?: string | null;
    ok?: boolean | null;
    skipped?: boolean | null;
    reason?: string | null;
  };
};

function pct(v?: number) {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

const TIER_LABEL: Record<string, string> = {
  short: "短线",
  mid: "波段",
  long: "长线",
};

const SHAPE_LABEL: Record<string, string> = {
  trend: "趋势",
  range: "震荡",
  breakout: "突破",
  low: "低波动",
  mid: "中波动",
  high: "高波动",
  "x-high": "极高波动",
};

function labelForKey(key: string) {
  if (!key.includes("|")) {
    return TIER_LABEL[key] || key;
  }
  const [tier, shape] = key.split("|", 2);
  const t = TIER_LABEL[tier] || tier;
  const s = SHAPE_LABEL[shape] || shape;
  return `${t} · ${s}`;
}

function Card({ k, row }: { k: string; row: TierRow }) {
  const isLongZeroTp = k === "long" || k.startsWith("long|");
  const tpBad = isLongZeroTp && !(row.tp_pct && row.tp_pct > 0);
  return (
    <div className="rounded border border-border/60 px-3 py-2 text-sm">
      <div className="font-medium mb-1">
        {labelForKey(k)}
        {row.period ? ` · ${row.period}` : ""}
      </div>
      <div className={tpBad ? "text-amber-600" : undefined}>
        止盈 {pct(row.tp_pct)}
        {tpBad ? "（缺趋势止盈）" : ""}
      </div>
      <div>止损 {pct(row.sl_pct)}</div>
      <div className="text-xs text-muted-foreground mt-1">
        样本外均收益 {pct(row.oos_avg)} · n={row.oos_n ?? "—"} · 胜率{" "}
        {row.oos_win_rate != null ? `${(row.oos_win_rate * 100).toFixed(0)}%` : "—"}
      </div>
    </div>
  );
}

export function OpsTpSlTrain() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const inflight = useRef(false);

  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const r = await apiRequest("/factors/tp-sl/status", { timeout: 8000 });
      setStatus(r);
    } catch {
      /* ignore poll errors */
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(id);
  }, [load]);

  async function setAuto(enabled: boolean) {
    setBusy("auto");
    setMsg(null);
    try {
      const r = await apiRequest("/factors/tp-sl/auto", {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      setMsg(r?.message || (enabled ? "已开启自动训练" : "已关闭自动训练"));
      await load();
    } catch (e: any) {
      setMsg(`自动开关失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  async function train() {
    setBusy("train");
    setMsg(null);
    try {
      const r = await apiRequest("/factors/tp-sl/train", { method: "POST" });
      setMsg(`已提交训练任务 ${r?.id || r?.job_id || ""}，完成后自动覆盖开仓 TP/SL`);
      await load();
    } catch (e: any) {
      setMsg(`训练触发失败: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  }

  const tiers = status?.by_tier || {};
  const auto = !!status?.auto_train;

  const { baseKeys, morphKeys, bandKeys } = useMemo(() => {
    const keys = Object.keys(tiers);
    const base = (["short", "mid", "long"] as const).filter((k) => k in tiers);
    const morph = keys
      .filter((k) => /\|(trend|range|breakout)$/.test(k))
      .sort();
    const band = keys
      .filter((k) => /\|(low|mid|high|x-high)$/.test(k))
      .sort();
    return { baseKeys: base as string[], morphKeys: morph, bandKeys: band };
  }, [tiers]);

  return (
    <section className="ops-panel">
      <div className="ops-panel-head">
        <span className="ops-panel-title">止盈止损 · 可训练</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={cn("ops-btn", auto && "ops-btn-active")}
            disabled={busy !== null}
            onClick={() => void setAuto(!auto)}
          >
            {busy === "auto" ? "切换中…" : auto ? "自动训练：开" : "自动训练：关"}
          </button>
          <button
            type="button"
            className="ops-btn"
            disabled={busy !== null}
            onClick={() => void train()}
          >
            {busy === "train" ? "提交中…" : "立即训练"}
          </button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground mb-3">
        开启自动后：每天凌晨 5 点网格搜最优止盈/止损（含长线趋势止盈），并按形态（趋势/震荡/突破）与波动带分桶；缺结果或超过 36
        小时会在启动后补训。开仓自动用学习结果。
      </p>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground mb-3">
        <span className={status?.enabled ? "text-profit" : "text-amber-600"}>
          {status?.enabled ? "开仓已用学习结果" : "开仓未用学习结果"}
        </span>
        <span>{status?.schedule || "每日 05:00"}</span>
        {status?.last_auto?.at ? (
          <span>
            上次自动：{status.last_auto.source || "—"}
            {status.last_auto.skipped
              ? `（跳过 ${status.last_auto.reason || ""}）`
              : status.last_auto.ok
                ? "（成功）"
                : "（失败）"}
          </span>
        ) : null}
      </div>
      {msg ? <p className="text-xs mb-2 text-foreground/80">{msg}</p> : null}
      {baseKeys.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          还没有训练结果。开着自动会很快补训，也可点「立即训练」。
        </p>
      ) : (
        <>
          <div className="text-xs text-muted-foreground mb-1">总档</div>
          <div className="grid gap-2 sm:grid-cols-3 mb-3">
            {baseKeys.map((k) => (
              <Card key={k} k={k} row={tiers[k]} />
            ))}
          </div>
          {morphKeys.length > 0 ? (
            <>
              <div className="text-xs text-muted-foreground mb-1">形态分桶（趋势 / 震荡 / 突破）</div>
              <div className="grid gap-2 sm:grid-cols-3 mb-3">
                {morphKeys.map((k) => (
                  <Card key={k} k={k} row={tiers[k]} />
                ))}
              </div>
            </>
          ) : (
            <p className="text-xs text-amber-600 mb-2">形态分桶尚未生成，请点「立即训练」。</p>
          )}
          {bandKeys.length > 0 ? (
            <>
              <div className="text-xs text-muted-foreground mb-1">波动带分桶</div>
              <div className="grid gap-2 sm:grid-cols-3">
                {bandKeys.map((k) => (
                  <Card key={k} k={k} row={tiers[k]} />
                ))}
              </div>
            </>
          ) : (
            <p className="text-xs text-amber-600">波动带分桶尚未生成，请点「立即训练」。</p>
          )}
        </>
      )}
      {status?.updated_at ? (
        <p className="text-xs text-muted-foreground mt-2">
          更新于 {status.updated_at}
          {status.age_hours != null ? ` · ${status.age_hours}h 前` : ""}
          {status.elapsed_sec != null ? ` · 耗时 ${status.elapsed_sec}s` : ""}
        </p>
      ) : null}
    </section>
  );
}
