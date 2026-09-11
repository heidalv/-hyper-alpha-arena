"use client";

/**
 * 套利中心 · 配置
 *
 * 设计 §3.6：
 *  - 费率表（GET /api/trading/config/fees，Aster maker 0% / taker 0.04%，含「重新拉取」）；
 *  - 车道参数编辑器（GET/PATCH /api/trading/config/lanes/{id}，editable_keys 白名单 + 范围校验 + 恢复默认，保存走 confirmDialog）；
 *  - 数据源健康（GET /api/trading/config/datasources：盘口/成交/资金费/现货数据年龄与断流标记，stale=true 醒目提示）。
 */
import { Suspense, useCallback, useMemo, useState } from "react";
import { Settings2, Percent, SlidersHorizontal, Activity, AlertTriangle } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import { ageFromAsOf, tradingApi, type DataSourceEntry, type DataSourceKind } from "@/lib/trading-api";
import { PageShell, DataState, FeeTable, LaneParamEditor } from "@/components/arbitrage";
import { useConfigFees, useLanes, useLaneConfig, useConfigDataSources } from "@/hooks/useLaneData";
import { useLaneStream } from "@/hooks/useLaneStream";

function isStale(asOf?: string | null): boolean {
  const age = ageFromAsOf(asOf);
  return age != null && age > 90_000;
}

const SOURCE_LABEL: Record<DataSourceKind, string> = {
  orderbook: "盘口",
  trades: "成交",
  funding: "资金费",
  spot: "现货",
};

/** 把 age_sec（秒）格式化为可读，如 "22d3h" / "12s" */
function fmtAge(sec: number | null): string {
  if (sec == null) return "无数据";
  if (sec < 0) return "刚刚";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

function fmtTs(ms: number | null): string {
  if (ms == null) return "—";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function ConfigPage() {
  return (
    <Suspense fallback={<PageShell title="套利中心 · 配置" icon={<Settings2 className="h-4 w-4" />} breadcrumb={[{ label: "套利中心" }, { label: "配置" }]} />}>
      <ConfigInner />
    </Suspense>
  );
}

function ConfigInner() {
  const fees = useConfigFees();
  const lanes = useLanes();
  const datasources = useConfigDataSources();
  const stream = useLaneStream();

  const laneOptions = useMemo(() => {
    return (lanes.data?.items ?? []).map((l) => l.lane_id);
  }, [lanes.data]);

  const [selectedLane, setSelectedLane] = useState<string>("");
  const effectiveLane = selectedLane || laneOptions[0] || "";
  const config = useLaneConfig(effectiveLane);

  const [saveBusy, setSaveBusy] = useState(false);

  const onSave = useCallback(async (params: Record<string, number>) => {
    setSaveBusy(true);
    try {
      await tradingApi.updateLaneConfig(effectiveLane, params);
      config.refresh();
      lanes.refresh();
    } finally {
      setSaveBusy(false);
    }
  }, [effectiveLane, config, lanes]);

  return (
    <PageShell
      title="套利中心 · 配置"
      subtitle="费率 / 参数 / 数据源"
      icon={<Settings2 className="h-4 w-4" />}
      mode={stream.mode}
      asOf={fees.data?.as_of ?? datasources.data?.as_of ?? lanes.data?.as_of ?? null}
      onRefresh={() => { fees.refresh(); lanes.refresh(); config.refresh(); datasources.refresh(); }}
      refreshing={fees.loading && !fees.data}
      breadcrumb={[{ label: "套利中心" }, { label: "配置" }]}
    >
      {/* 费率表 */}
      <Card className="glass p-4">
        <BlockTitle icon={<Percent className="h-3.5 w-3.5" />} title="费率表（每所 maker/taker）" />
        <DataState
          loading={fees.loading}
          error={fees.error}
          hasData={!!fees.data}
          onRetry={fees.refresh}
          stale={isStale(fees.data?.as_of)}
        >
          <FeeTable
            items={fees.data?.items ?? []}
            refreshing={fees.loading}
            onRefresh={fees.refresh}
          />
        </DataState>
      </Card>

      {/* 车道参数编辑器 */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <BlockTitle icon={<SlidersHorizontal className="h-3.5 w-3.5" />} title="车道参数编辑器" />
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>车道</span>
            <select
              value={effectiveLane}
              onChange={(e) => setSelectedLane(e.target.value)}
              disabled={!laneOptions.length}
              className="rounded-md border border-border/40 bg-muted/20 px-2 py-1 text-xs text-foreground outline-none focus:border-cyan-400/40 disabled:opacity-50"
            >
              {laneOptions.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
          </label>
        </div>
        <DataState
          loading={config.loading}
          error={config.error}
          hasData={!!config.data}
          onRetry={config.refresh}
          stale={isStale(config.data?.as_of)}
          empty={!laneOptions.length}
          emptyHint="暂无车道（可调用 /api/trading/lanes/seed 初始化）"
        >
          <LaneParamEditor
            key={effectiveLane}
            config={config.data}
            busy={saveBusy}
            onSave={onSave}
          />
        </DataState>
      </Card>

      {/* 数据源健康（GET /api/trading/config/datasources） */}
      <Card className="glass p-4">
        <BlockTitle icon={<Activity className="h-3.5 w-3.5" />} title="数据源健康" />
        <DataState
          loading={datasources.loading}
          error={datasources.error}
          hasData={!!datasources.data}
          onRetry={datasources.refresh}
          stale={isStale(datasources.data?.as_of)}
          empty={!datasources.data || datasources.data.items.length === 0}
          emptyHint="暂无数据源健康信息"
        >
          {datasources.data && datasources.data.stale_count > 0 && (
            <div className="mb-2 flex items-start gap-2 rounded-lg border border-loss/30 bg-loss/10 px-3 py-2.5 text-xs text-loss">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
              <span>{datasources.data.stale_count} 个数据源断流（年龄超过 10 分钟）——需要一眼看见，请处理。</span>
            </div>
          )}
          <div className="overflow-x-auto rounded-xl border border-border/40">
            <table className="data-table">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left">来源</th>
                  <th className="text-left">场所</th>
                  <th className="text-right">行数</th>
                  <th className="text-right">最近写入</th>
                  <th className="text-right">数据年龄</th>
                  <th className="text-center">状态</th>
                </tr>
              </thead>
              <tbody>
                {(datasources.data?.items ?? []).map((d: DataSourceEntry, i) => (
                  <tr key={`${d.source}-${d.exchange ?? i}`} className={cn("border-b border-border/20", d.stale && "bg-loss/5")}>
                    <td className="font-medium">{SOURCE_LABEL[d.source] ?? d.source}</td>
                    <td className="text-muted-foreground">{d.exchange ?? "—"}</td>
                    <td className="text-right font-mono tabular-nums">{fmtNum(d.rows, 0)}</td>
                    <td className="text-right font-mono tabular-nums text-muted-foreground">{fmtTs(d.last_ts)}</td>
                    <td className={cn("text-right font-mono tabular-nums", d.stale ? "text-loss font-semibold" : "text-foreground")}>
                      {fmtAge(d.age_sec)}
                    </td>
                    <td className="text-center">
                      {d.stale ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-loss/30 bg-loss/10 px-1.5 py-0.5 text-[11px] text-loss">
                          <AlertTriangle className="h-3 w-3" /> 断流
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full border border-profit/25 bg-profit/10 px-1.5 py-0.5 text-[11px] text-profit">正常</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-2 text-[11px] text-muted-foreground">
            数据来源：GET /api/trading/config/datasources（盘口/成交/资金费/现货）；年龄超过 600s 视为断流，stale=true 会醒目标红。
          </div>
        </DataState>
      </Card>
    </PageShell>
  );
}

function BlockTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
      <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
        {icon}
      </span>
      {title}
    </h2>
  );
}
