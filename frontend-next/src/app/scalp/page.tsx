"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Settings, Save, Loader2 } from "lucide-react";
import { useScalpConfig, useScalpPresets, useUpdateScalpConfig } from "@/hooks/useTradingData";
import { useState, useMemo } from "react";
import { cn } from "@/lib/utils";

// EV 计算函数
function calcEV(tp: number, sl: number, pWin: number, tpReal = 0.55, lev = 10, posPct = 0.30, trades = 3) {
  const cost = 0.0021;
  const ev = pWin * tp * tpReal - (1 - pWin) * sl - cost;
  const daily = ev * lev * posPct * trades;
  return {
    ev, daily, monthly: daily * 30,
    rr: sl > 0 ? tp / sl : 0,
    breakeven: (sl + cost) / (tp * tpReal + sl),
    costMargin: cost * lev,
  };
}

export default function ScalpPage() {
  const { data, isLoading } = useScalpConfig();
  const { data: presets } = useScalpPresets();
  const updateMutation = useUpdateScalpConfig();

  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pWin, setPWin] = useState(0.55);

  // 初始化 config
  if (data && !config) {
    setConfig(data.config);
  }

  const updateParam = (key: string, value: number | boolean) => {
    setConfig((prev) => prev ? { ...prev, [key]: value } : prev);
    setDirty(true);
  };

  const handleSave = async () => {
    if (!config) return;
    await updateMutation.mutateAsync(config);
    setDirty(false);
  };

  const ev = useMemo(() => {
    if (!config) return null;
    return calcEV(config.tp_pct, config.sl_pct, pWin, config.ev_tp_realization, config.leverage, config.position_pct);
  }, [config, pWin]);

  if (isLoading || !config || !data) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;
  }

  const params = data.param_defs;
  const groups = data.groups;
  const stats = data.stats;
  const lev = config.leverage;

  return (
    <div className="p-4 space-y-4 max-w-4xl mx-auto">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold flex items-center gap-2"><Settings className="w-5 h-5 text-primary" />短线策略配置</h1>
        <div className="flex gap-2">
          {dirty && <Badge variant="destructive" className="text-xs">未保存</Badge>}
          <Button size="sm" onClick={handleSave} disabled={!dirty || updateMutation.isPending}>
            {updateMutation.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
            保存
          </Button>
        </div>
      </div>

      {/* 预设 */}
      {presets && (
        <div className="flex gap-2 flex-wrap">
          {Object.entries(presets).map(([key, p]: [string, any]) => (
            <Button key={key} variant="outline" size="sm" onClick={() => { setConfig({ ...config, ...p.params }); setDirty(true); }}>
              {p.name}<span className="text-xs text-muted-foreground ml-2">{p.description}</span>
            </Button>
          ))}
        </div>
      )}

      {/* EV模拟器 */}
      {ev && (
        <Card className={cn(ev.ev > 0 ? "border-profit/30" : "border-loss/30")}>
          <div className="p-4">
            <div className="text-sm font-medium mb-3">EV 模拟器 ({lev}x杠杆)</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <Metric label="EV/笔(保证金)" value={`${ev.ev > 0 ? "+" : ""}${(ev.ev * lev * 100).toFixed(2)}%`} positive={ev.ev > 0} />
              <Metric label="盈亏比" value={ev.rr.toFixed(2)} positive={ev.rr >= 1} />
              <Metric label="日化(保证金)" value={`${(ev.daily * 100).toFixed(2)}%`} positive={ev.daily > 0} />
              <Metric label="月化" value={`${(ev.monthly * 100).toFixed(1)}%`} positive={ev.monthly > 0} />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">胜率假设:</span>
              <input type="range" min={0.3} max={0.8} step={0.01} value={pWin} onChange={(e) => setPWin(parseFloat(e.target.value))} className="flex-1" />
              <span className="text-sm font-bold w-10 text-right">{(pWin * 100).toFixed(0)}%</span>
            </div>
            <div className="mt-2 text-xs text-muted-foreground">
              TP {((config.tp_pct as number) * 100).toFixed(1)}%价格 = {((config.tp_pct as number) * lev * 100).toFixed(0)}%保证金 |
              SL {((config.sl_pct as number) * 100).toFixed(1)}%价格 = {((config.sl_pct as number) * lev * 100).toFixed(0)}%保证金 |
              手续费 {(ev.costMargin * 100).toFixed(2)}%保证金
            </div>
          </div>
        </Card>
      )}

      {/* 参数 */}
      {Object.entries(groups).sort(([, a]: any, [, b]: any) => a.order - b.order).map(([gKey, gDef]: [string, any]) => {
        const keys = Object.entries(params).filter(([, d]: any) => d.group === gKey).map(([k]) => k);
        if (!keys.length) return null;
        return (
          <Card key={gKey} className="p-4">
            <h2 className="text-sm font-medium mb-3">{gDef.title}</h2>
            <div className="space-y-3">
              {keys.map((key) => {
                const def = params[key];
                if (!def) return null;
                if (def.type === "bool") {
                  return (
                    <div key={key} className="flex items-center justify-between">
                      <span className="text-sm">{def.label}</span>
                      <button onClick={() => updateParam(key, !config[key])} className={cn("relative w-11 h-6 rounded-full transition-colors", config[key] ? "bg-primary" : "bg-muted")}>
                        <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", config[key] ? "left-5" : "left-0.5")} />
                      </button>
                    </div>
                  );
                }
                const val = config[key] as number;
                const isTpSl = key === "tp_pct" || key === "sl_pct";
                const marginVal = isTpSl ? val * lev : 0;
                return (
                  <div key={key}>
                    <div className="flex justify-between text-sm mb-1">
                      <span>{def.label}</span>
                      <span className="font-bold tabular-nums">
                        {def.unit === "%" ? (val * 100).toFixed(2) + "%" : val}
                        {isTpSl && <span className="text-xs text-muted-foreground ml-1">= {(marginVal * 100).toFixed(0)}%保证金</span>}
                      </span>
                    </div>
                    <input type="range" min={def.min} max={def.max} step={(def.max - def.min) / 100} value={val} onChange={(e) => updateParam(key, parseFloat(e.target.value))} className="w-full" />
                  </div>
                );
              })}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

function Metric({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className={cn("p-2 rounded", positive === undefined ? "bg-muted/50" : positive ? "bg-profit/10" : "bg-loss/10")}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-sm font-bold tabular-nums", positive === undefined ? "" : positive ? "text-profit" : "text-loss")}>{value}</div>
    </div>
  );
}
