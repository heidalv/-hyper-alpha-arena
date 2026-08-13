"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiRequest } from "@/lib/api";
import { OpsHeader, OpsKpiStrip } from "@/components/ops/OpsHeader";
import { OpsChainProgress } from "@/components/ops/OpsChainProgress";
import { OpsHeartbeatMatrix } from "@/components/ops/OpsHeartbeatMatrix";
import { OpsFunnel } from "@/components/ops/OpsFunnel";
import { OpsFactorPool, type PoolView } from "@/components/ops/OpsFactorPool";
import { OpsPairs } from "@/components/ops/OpsPairs";
import { OpsTraining } from "@/components/ops/OpsTraining";
import { OpsErrors } from "@/components/ops/OpsErrors";
import { OpsMiningBoost } from "@/components/ops/OpsMiningBoost";
import { OpsTpSlTrain } from "@/components/ops/OpsTpSlTrain";
import { cn } from "@/lib/utils";
import "./ops.css";

function scrollToErrors() {
  const el = document.getElementById("ops-errors");
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function OpsDashboard() {
  const searchParams = useSearchParams();
  const [pulse, setPulse] = useState<any>(null);
  const [hb, setHb] = useState<any>(null);
  const [funnel, setFunnel] = useState<any>(null);
  const [pool, setPool] = useState<any>(null);
  const [poolView, setPoolView] = useState<PoolView>("tradable");
  const [poolAutoSwitched, setPoolAutoSwitched] = useState(false);
  const [cands, setCands] = useState<any>(null);
  const [binds, setBinds] = useState<any>(null);
  const [train, setTrain] = useState<any>(null);
  const [errors, setErrors] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [ago, setAgo] = useState(0);
  const [clock, setClock] = useState("");
  const [flash, setFlash] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const refreshInflight = useRef(false);

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3200);
  };

  const refresh = useCallback(async () => {
    // 上一次还没回来就跳过，避免 8 路请求叠成雪崩把页面卡死
    if (refreshInflight.current) return;
    refreshInflight.current = true;
    setLoading(true);
    try {
      const opts = { timeout: 10000 };
      const [p, h, f, po, ca, bi, tr, er] = await Promise.all([
        apiRequest("/ops/pipeline", opts),
        apiRequest("/ops/heartbeats", opts),
        apiRequest("/ops/evolution-funnel?days=7", opts),
        apiRequest(`/ops/factor-pool?view=${poolView}&limit=80`, opts),
        apiRequest("/ops/candidates?limit=40", opts),
        apiRequest("/ops/bindings?limit=80", opts),
        apiRequest("/ops/training", opts),
        apiRequest("/ops/errors?limit=80", opts),
      ]);
      setPulse(p);
      setHb(h);
      setFunnel(f);
      setPool(po);
      // 可交易为空但隔离有数：首次自动切到隔离，避免「池始终为空」误判
      if (
        !poolAutoSwitched &&
        poolView === "tradable" &&
        Array.isArray(po?.items) &&
        po.items.length === 0 &&
        Number(po?.counts?.quarantine || 0) > 0
      ) {
        setPoolAutoSwitched(true);
        setPoolView("quarantine");
      }
      setCands(ca);
      setBinds(bi);
      setTrain(tr);
      setErrors(er);
      setLastRefresh(new Date());
      setFlash(true);
      window.setTimeout(() => setFlash(false), 220);
    } catch (e: any) {
      showToast(`刷新失败：${e?.message || e}`);
    } finally {
      setLoading(false);
      refreshInflight.current = false;
    }
  }, [poolView, poolAutoSwitched]);

  useEffect(() => {
    void refresh();
    // 15s：降低与后端重负载叠峰；失败/慢时靠 in-flight 跳过
    const id = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const tick = () => {
      setClock(
        new Date().toLocaleTimeString("zh-CN", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }),
      );
      if (lastRefresh) {
        setAgo(Math.floor((Date.now() - lastRefresh.getTime()) / 1000));
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [lastRefresh]);

  // 兼容 /ops#ops-errors 与旧 ?tab=errors
  useEffect(() => {
    const go = () => {
      const tab = (searchParams.get("tab") || "").toLowerCase();
      const hash = window.location.hash;
      if (tab === "errors" || hash === "#ops-errors") {
        scrollToErrors();
      }
    };
    const t = window.setTimeout(go, 350);
    window.addEventListener("hashchange", go);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("hashchange", go);
    };
  }, [searchParams, errors]);

  const alertCount = useMemo(() => {
    const c = errors?.counts || {};
    return Number(c.P0 || 0) + Number(c.P1 || 0);
  }, [errors]);

  const kpiItems = useMemo(() => {
    const p = pulse?.pulse || {};
    const hbOk = p.heartbeat_ok;
    const hbTotal = p.heartbeat_total;
    let hbTone: "ok" | "lag" | "down" | "" = "";
    if (hbTotal != null && hbOk != null) {
      if (hbOk === hbTotal && hbTotal > 0) hbTone = "ok";
      else if (hbOk === 0 && hbTotal > 0) hbTone = "down";
      else if (hbOk < hbTotal) hbTone = "lag";
    }
    return [
      {
        label: "心跳正常",
        value:
          hbOk != null && hbTotal != null ? `${hbOk}/${hbTotal}` : "—",
        tone: hbTone,
      },
      { label: "可交易因子", value: p.tradable_factors ?? "—", tone: "info" as const },
      {
        label: "固定币",
        // 窄 KPI 格：只显示数量 + 前 2 币，完整列表放 title，避免大字号裁切成乱码
        value: Array.isArray(p.fixed_symbols) && p.fixed_symbols.length
          ? (p.fixed_symbols.length <= 2
              ? p.fixed_symbols.join("/")
              : `${p.fixed_symbols.length} · ${p.fixed_symbols.slice(0, 2).join("/")}+`)
          : (p.fixed_symbol_count ?? "—"),
        title: Array.isArray(p.fixed_symbols) && p.fixed_symbols.length
          ? p.fixed_symbols.join(" / ")
          : undefined,
        dense: true,
        tone: "ok" as const,
      },
      {
        label: "全表pass",
        value: p.candidates_pass ?? "—",
        tone: "ok" as const,
      },
      {
        label: "AI待扫",
        value: p.ai_pending_scan ?? "—",
        tone: Number(p.ai_pending_scan) > 0 ? ("lag" as const) : ("ok" as const),
      },
      {
        label: "绑定 running",
        value: p.bindings_running ?? "—",
        tone: Number(p.bindings_running) > 0 ? ("lag" as const) : ("" as const),
      },
      {
        label: "车道",
        value: p.lane_trading_enabled ? "开仓" : "干跑",
        tone: p.lane_trading_enabled ? ("lag" as const) : ("ok" as const),
      },
      {
        label: "元标签",
        value: p.meta_usable ? "可用" : "未达标",
        tone: p.meta_usable ? ("ok" as const) : ("lag" as const),
      },
    ];
  }, [pulse]);

  const pauseBinding = async (id: number) => {
    if (!confirm(`确认暂停绑定 #${id}？`)) return;
    try {
      await apiRequest(`/ops/bindings/${id}/pause`, { method: "POST" });
      showToast(`已暂停绑定 #${id}`);
      void refresh();
    } catch (e: any) {
      showToast(`暂停失败：${e?.message || e}`);
    }
  };

  const enableCand = async (id: number) => {
    if (!confirm(`确认启用候选 #${id}？（将生成 running 绑定）`)) return;
    try {
      await apiRequest(`/ops/candidates/${id}/enable`, { method: "POST" });
      showToast(`已启用候选 #${id}`);
      void refresh();
    } catch (e: any) {
      showToast(`启用失败：${e?.message || e}`);
    }
  };

  return (
    <div className={cn("ops-scope", flash && "ops-flash")}>
      <link
        rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap"
      />

      <div className="ops-sticky-top">
        <OpsHeader
          ago={ago}
          alertCount={alertCount}
          loading={loading}
          clock={clock}
          onRefresh={() => void refresh()}
        />
        <OpsKpiStrip items={kpiItems} />
      </div>

      <div className="ops-dash">
        <OpsMiningBoost />
        <OpsTpSlTrain />
        <OpsChainProgress
          fixedPool={pulse?.fixed_pool || train?.fixed_pool}
          aiScan={pulse?.ai_scan || train?.ai_scan}
        />
        <OpsHeartbeatMatrix items={hb?.items || []} />
        <OpsFunnel
          counts={funnel?.counts || {}}
          rejects={funnel?.rejects || []}
          onActionClick={(action, n) =>
            showToast(`动作 ${action}：近 7 天 ${n} 次`)
          }
        />
        <OpsTraining
          report={train?.report}
          laneEnabled={binds?.lane?.PAIR_BINDING_LANE_ENABLED}
          cbEnabled={binds?.circuit_breaker?.SCALP_CIRCUIT_BREAKER_ENABLED}
          laneNote={binds?.lane?.note}
        />
        <OpsFactorPool
          view={poolView}
          callout={pool?.callout}
          items={pool?.items || []}
          counts={pool?.counts}
          quarantineReasons={pool?.quarantine_reasons}
          onViewChange={setPoolView}
        />
        <OpsPairs
          candidates={cands?.items || []}
          passCount={cands?.pass_count ?? 0}
          passCountGlobal={cands?.pass_count_global}
          bySymbol={cands?.by_symbol}
          callout={cands?.callout}
          bindings={binds?.items || []}
          laneEnabled={binds?.lane?.PAIR_BINDING_LANE_ENABLED}
          laneNote={binds?.lane?.note}
          aiSymbols={binds?.ai_symbols}
          onEnable={(id) => void enableCand(id)}
          onPause={(id) => void pauseBinding(id)}
        />
        <OpsErrors items={errors?.items || []} counts={errors?.counts} />
      </div>

      {toast ? <div className="ops-toast">{toast}</div> : null}
    </div>
  );
}

export default function OpsPage() {
  return (
    <Suspense
      fallback={
        <div className="ops-scope" style={{ padding: 24 }}>
          <div className="ops-muted">加载因子运维台…</div>
        </div>
      }
    >
      <OpsDashboard />
    </Suspense>
  );
}
