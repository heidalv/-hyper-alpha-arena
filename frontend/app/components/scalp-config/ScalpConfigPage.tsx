/**
 * 短线策略配置页 — 全参数可视化调节 + 实时EV模拟器
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { RefreshCw, Save, Loader2 } from 'lucide-react';
import {
  fetchScalpConfig, updateScalpConfig, fetchPresets, fetchCurrentPreset,
  saveCustomPreset, deleteCustomPreset, calcEV,
  type ScalpConfig, type ParamDef, type GroupDef, type ScalpStats, type EVResult, type Preset,
} from '@/lib/scalpConfigApi';

export default function ScalpConfigPage() {
  const [config, setConfig] = useState<ScalpConfig | null>(null);
  const [paramDefs, setParamDefs] = useState<Record<string, ParamDef>>({});
  const [groups, setGroups] = useState<Record<string, GroupDef>>({});
  const [stats, setStats] = useState<ScalpStats | null>(null);
  const [, setServerEV] = useState<EVResult | null>(null);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pWinAssume, setPWinAssume] = useState(0.55);
  const [currentPresetKey, setCurrentPresetKey] = useState<string>('custom');
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [presetName, setPresetName] = useState('');
  const [presetDesc, setPresetDesc] = useState('');

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const [cfgResp, presResp, curResp] = await Promise.all([
        fetchScalpConfig(), fetchPresets(), fetchCurrentPreset()
      ]);
      setConfig(cfgResp.config);
      setParamDefs(cfgResp.param_defs);
      setGroups(cfgResp.groups);
      setStats(cfgResp.stats);
      setServerEV(cfgResp.ev);
      setPresets(presResp);
      setCurrentPresetKey(curResp.preset_key);
      setDirty(false);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // 实时EV计算（基于本地config + 胜率假设）
  const liveEV = useMemo(() => {
    if (!config) return null;
    return calcEV(
      config.tp_pct, config.sl_pct, pWinAssume,
      config.ev_tp_realization, config.leverage, config.position_pct, 3,
    );
  }, [config, pWinAssume]);

  // 按 group 分组参数（必须在 early return 之前，保持 hooks 数量一致）
  const groupedParams = useMemo(() => {
    const result: Record<string, string[]> = {};
    for (const [key, def] of Object.entries(paramDefs)) {
      const g = def.group;
      if (!result[g]) result[g] = [];
      result[g].push(key);
    }
    return result;
  }, [paramDefs]);

  const updateParam = (key: string, value: number | boolean) => {
    setConfig(prev => prev ? { ...prev, [key]: value } : prev);
    setDirty(true);
    setCurrentPresetKey('custom');
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    setError(null);
    try {
      const updates: Record<string, any> = {};
      for (const [k, v] of Object.entries(config)) {
        updates[k] = v;
      }
      const resp = await updateScalpConfig(updates);
      if (resp.success) {
        setConfig(resp.config);
        setServerEV(resp.ev);
        setDirty(false);
        // 保存后重新检测匹配的预设
        try {
          const cur = await fetchCurrentPreset();
          setCurrentPresetKey(cur.preset_key);
        } catch {}
      } else {
        setError(resp.errors?.join('; ') || '保存失败');
      }
    } catch (e: any) {
      setError(e.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const applyPreset = (presetKey: string) => {
    const preset = presets[presetKey];
    if (!preset || !config) return;
    setConfig(prev => prev ? { ...prev, ...preset.params } : prev);
    setDirty(true);
    setCurrentPresetKey(presetKey);
  };

  const handleSaveCustomPreset = async () => {
    if (!config || !presetName.trim()) return;
    try {
      const resp = await saveCustomPreset(presetName.trim(), config, presetDesc.trim());
      if (resp.success) {
        // 刷新预设列表
        const presResp = await fetchPresets();
        setPresets(presResp);
        setCurrentPresetKey(resp.key);
        setShowSaveDialog(false);
        setPresetName('');
        setPresetDesc('');
      }
    } catch (e: any) {
      setError(e.message || '保存预设失败');
    }
  };

  const handleDeleteCustomPreset = async (key: string) => {
    try {
      await deleteCustomPreset(key);
      const presResp = await fetchPresets();
      setPresets(presResp);
    } catch (e: any) {
      setError(e.message || '删除失败');
    }
  };

  if (loading || !config) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>;
  }

  const sortedGroups = Object.entries(groups).sort((a, b) => a[1].order - b[1].order);

  return (
    <div className="flex flex-col h-full overflow-auto p-4 space-y-4 max-w-5xl mx-auto">
      {/* 标题栏 */}
      <div className="flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-bold flex items-center gap-2">⚙️ 短线策略配置</h2>
          {/* 当前预设标识 */}
          <Badge
            variant={currentPresetKey === 'custom' ? 'secondary' : 'default'}
            className={`text-xs ${currentPresetKey !== 'custom' && currentPresetKey !== 'balanced' ? 'bg-orange-500' : ''} ${currentPresetKey === 'balanced' ? 'bg-green-500' : ''}`}
          >
            {presets[currentPresetKey]?.name || '自定义'}
          </Badge>
          {dirty && <Badge variant="destructive" className="text-xs">未保存</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setShowSaveDialog(true)} disabled={!config}>
            保存为预设
          </Button>
          <Button variant="outline" size="sm" onClick={loadData} disabled={saving}>
            <RefreshCw className="w-3.5 h-3.5 mr-1" />刷新
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!dirty || saving}>
            {saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
            保存并应用
          </Button>
        </div>
      </div>

      {/* 保存自定义预设对话框 */}
      {showSaveDialog && (
        <Card className="border-primary/30">
          <CardContent className="pt-4 space-y-3">
            <h3 className="text-sm font-medium">💾 保存当前配置为自定义预设</h3>
            <div className="flex gap-2 items-center">
              <input
                type="text"
                placeholder="预设名称（如：我的窄止盈v2）"
                value={presetName}
                onChange={e => setPresetName(e.target.value)}
                className="flex-1 px-3 py-1.5 text-sm rounded border bg-background"
              />
              <input
                type="text"
                placeholder="描述（可选）"
                value={presetDesc}
                onChange={e => setPresetDesc(e.target.value)}
                className="flex-1 px-3 py-1.5 text-sm rounded border bg-background"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={() => { setShowSaveDialog(false); setPresetName(''); setPresetDesc(''); }}>取消</Button>
              <Button size="sm" onClick={handleSaveCustomPreset} disabled={!presetName.trim()}>保存预设</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 预设方案（内置 + 自定义） */}
      <div className="space-y-2 flex-shrink-0">
        <div className="text-xs text-muted-foreground">点击应用预设方案：</div>
        <div className="flex gap-2 flex-wrap">
          {Object.entries(presets).map(([key, p]) => {
            const isActive = currentPresetKey === key;
            const isCustom = (p as any).is_custom;
            return (
              <div key={key} className={`flex items-center gap-0.5 rounded-lg border ${isActive ? 'border-primary bg-primary/10' : 'border-border'}`}>
                <button
                  onClick={() => applyPreset(key)}
                  className={`px-3 py-1.5 text-sm rounded-l-lg transition-colors ${isActive ? 'font-bold' : 'hover:bg-muted'}`}
                >
                  {isActive ? '✓ ' : ''}{p.name}
                  <span className="text-xs text-muted-foreground ml-2 hidden md:inline">{p.description}</span>
                </button>
                {isCustom && (
                  <button
                    onClick={() => handleDeleteCustomPreset(key)}
                    className="px-1.5 py-1.5 text-xs text-red-500 hover:bg-red-500/10 rounded-r-lg"
                    title="删除"
                  >✕</button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {error && <div className="text-sm text-red-500 bg-red-500/10 p-2 rounded">⚠️ {error}</div>}

      {/* 实时EV模拟器 */}
      {liveEV && config && (
        <EVSimulator ev={liveEV} pWinAssume={pWinAssume} setPWinAssume={setPWinAssume} stats={stats} config={config} />
      )}

      {/* 参数分组 */}
      {config && sortedGroups.map(([groupKey, groupDef]) => {
        const keys = groupedParams[groupKey] || [];
        if (keys.length === 0) return null;
        return (
          <Card key={groupKey}>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">{groupDef.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {keys.map(key => (
                <ParamRow
                  key={key}
                  paramKey={key}
                  def={paramDefs[key]}
                  value={config[key]}
                  onChange={(v) => updateParam(key, v)}
                  leverage={config.leverage}
                />
              ))}
            </CardContent>
          </Card>
        );
      })}

      <div className="h-8" />
    </div>
  );
}

// ════════════════════════════════════════
// EV 模拟器组件
// ════════════════════════════════════════
function EVSimulator({ ev, pWinAssume, setPWinAssume, stats, config }: {
  ev: EVResult; pWinAssume: number; setPWinAssume: (v: number) => void;
  stats: ScalpStats | null; config: ScalpConfig;
}) {
  const isPositive = ev.ev_pct > 0;
  const beatsBreakeven = pWinAssume > ev.breakeven_win;
  const lev = config.leverage;
  const posPct = config.position_pct;
  const tpPrice = config.tp_pct;
  const slPrice = config.sl_pct;
  // 杠杆放大后的保证金盈亏
  const tpMargin = tpPrice * lev;
  const slMargin = slPrice * lev;
  const evMargin = ev.ev_pct * lev;
  const costMargin = ev.round_trip_cost * lev;

  return (
    <Card className={`${isPositive ? 'border-green-500/30' : 'border-red-500/30'}`}>
      <CardContent className="pt-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium">📊 实时预期效果模拟器</span>
          {stats && (
            <span className="text-xs text-muted-foreground">
              7天实测: {stats.trades}笔 胜率{(stats.win_rate * 100).toFixed(0)}% 盈亏比{stats.profit_factor}
            </span>
          )}
        </div>

        {/* 杠杆放大对照表 */}
        <div className="mb-3 p-3 bg-muted/50 rounded-lg">
          <div className="text-xs text-muted-foreground mb-2">杠杆放大对照（{lev}x 杠杆，仓位占权益 {(posPct * 100).toFixed(0)}%）</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <div className="flex flex-col">
              <span className="text-muted-foreground">止盈 TP</span>
              <span><span className="text-muted-foreground">{(tpPrice * 100).toFixed(2)}%价格</span> → <strong className="text-green-500">{(tpMargin * 100).toFixed(1)}%保证金</strong></span>
            </div>
            <div className="flex flex-col">
              <span className="text-muted-foreground">止损 SL</span>
              <span><span className="text-muted-foreground">{(slPrice * 100).toFixed(2)}%价格</span> → <strong className="text-red-500">-{(slMargin * 100).toFixed(1)}%保证金</strong></span>
            </div>
            <div className="flex flex-col">
              <span className="text-muted-foreground">EV/笔</span>
              <span><span className="text-muted-foreground">{(ev.ev_pct * 100).toFixed(3)}%价格</span> → <strong className={evMargin > 0 ? 'text-green-500' : 'text-red-500'}>{evMargin > 0 ? '+' : ''}{(evMargin * 100).toFixed(2)}%保证金</strong></span>
            </div>
            <div className="flex flex-col">
              <span className="text-muted-foreground">往返手续费</span>
              <span><span className="text-muted-foreground">{(ev.round_trip_cost * 100).toFixed(2)}%价格</span> → <strong className="text-orange-500">-{(costMargin * 100).toFixed(2)}%保证金</strong></span>
            </div>
          </div>
        </div>

        {/* 胜率假设滑块 */}
        <div className="flex items-center gap-2 mb-3">
          <span className="text-xs text-muted-foreground w-20">胜率假设:</span>
          <input
            type="range" min={0.30} max={0.80} step={0.01}
            value={pWinAssume}
            onChange={e => setPWinAssume(parseFloat(e.target.value))}
            className="flex-1"
          />
          <span className="text-sm font-bold w-12 text-right">{(pWinAssume * 100).toFixed(0)}%</span>
        </div>

        {/* 指标网格 — 保证金口径 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricBox label="EV/笔(保证金)" value={`${evMargin > 0 ? '+' : ''}${(evMargin * 100).toFixed(2)}%`} positive={ev.ev_pct > 0} />
          <MetricBox label="盈亏比(RR)" value={ev.rr.toFixed(2)} positive={ev.rr >= 1.0} />
          <MetricBox label="日化(保证金)" value={`${(ev.daily_return * 100).toFixed(2)}%`} positive={ev.daily_return > 0} />
          <MetricBox label="月化(保证金)" value={`${(ev.monthly_return * 100).toFixed(1)}%`} positive={ev.monthly_return > 0} />
        </div>

        {/* 提示 */}
        <div className="mt-3 text-xs space-y-1">
          <div className="text-muted-foreground">
            盈亏平衡胜率: <strong className={beatsBreakeven ? 'text-green-500' : 'text-red-500'}>{(ev.breakeven_win * 100).toFixed(1)}%</strong>
            {' '}{beatsBreakeven ? '✅ 当前假设胜率超过平衡点' : '⚠️ 当前假设胜率不足以盈利'}
          </div>
          <div className="text-muted-foreground">
            手续费占盈利比: {(ev.fee_ratio * 100).toFixed(1)}% | 往返成本: {(ev.round_trip_cost * 100).toFixed(2)}%价格 = {(costMargin * 100).toFixed(2)}%保证金
          </div>
          <div className="text-muted-foreground">
            💡 日化 = EV/笔 × {lev}x杠杆 × {(posPct * 100).toFixed(0)}%仓位 × 每日笔数（价格%已乘杠杆转保证金%）
          </div>
          {stats && stats.win_rate < ev.breakeven_win && (
            <div className="text-red-500 font-medium">
              ⚠️ 实测胜率({(stats.win_rate * 100).toFixed(1)}%)低于平衡胜率({(ev.breakeven_win * 100).toFixed(1)}%)，当前配置预计亏损
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function MetricBox({ label, value, positive }: { label: string; value: string; positive: boolean }) {
  return (
    <div className={`p-2 rounded-lg ${positive ? 'bg-green-500/10' : 'bg-red-500/10'}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-lg font-bold ${positive ? 'text-green-500' : 'text-red-500'}`}>{value}</div>
    </div>
  );
}

// ════════════════════════════════════════
// 参数行组件
// ════════════════════════════════════════
function ParamRow({ paramKey, def, value, onChange, leverage }: {
  paramKey: string;
  def: ParamDef;
  value: number | boolean;
  onChange: (v: number | boolean) => void;
  leverage: number;
}) {
  if (def.type === 'bool') {
    return (
      <div className="flex items-center justify-between py-1">
        <div>
          <span className="text-sm">{def.label}</span>
          <span className="text-xs text-muted-foreground ml-2">({def.env})</span>
        </div>
        <button
          onClick={() => onChange(!value)}
          className={`relative w-11 h-6 rounded-full transition-colors ${value ? 'bg-primary' : 'bg-muted'}`}
        >
          <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${value ? 'left-5' : 'left-0.5'}`} />
        </button>
      </div>
    );
  }

  const numValue = value as number;
  const displayValue = def.unit === '%' ? (numValue * 100).toFixed(2) : numValue.toString();
  const isInDeathZone = paramKey === 'sl_pct' && numValue >= 0.02 && numValue <= 0.04;
  // 杠杆放大后的保证金盈亏（仅 TP/SL 相关参数显示）
  const showMarginNote = (paramKey === 'tp_pct' || paramKey === 'sl_pct' || paramKey === 'max_tp_pct' || paramKey === 'max_sl_pct') && def.unit === '%' && leverage > 1;
  const marginPct = numValue * leverage;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm">{def.label}</span>
          <span className="text-xs text-muted-foreground ml-1">({def.env})</span>
        </div>
        <span className={`text-sm font-bold ${isInDeathZone ? 'text-red-500' : ''}`}>
          {displayValue}{def.unit === '%' ? '%' : def.unit}
          {isInDeathZone && ' ⚠️'}
        </span>
        {showMarginNote && (
          <span className={`text-xs ml-2 ${paramKey.includes('sl') ? 'text-red-500/70' : 'text-green-500/70'}`}>
            = {paramKey.includes('sl') ? '-' : ''}{(marginPct * 100).toFixed(1)}%保证金 @{leverage}x
          </span>
        )}
      </div>
      <input
        type="range"
        min={def.min}
        max={def.max}
        step={def.type === 'int' ? 1 : (def.max - def.min) / 100}
        value={numValue}
        onChange={e => onChange(parseFloat(e.target.value))}
        className={`w-full ${isInDeathZone ? 'accent-red-500' : ''}`}
      />
      {isInDeathZone && (
        <div className="text-xs text-red-500">⚠️ SL 2-4% 是插针死亡区间（实测胜率18%），建议调到 1.5% 以下</div>
      )}
      {paramKey === 'tp_pct' && numValue > 0.03 && (
        <div className="text-xs text-orange-500">⚠️ TP &gt; 3% 实测胜率骤降（≥4%仅33%），建议 1.5-2%</div>
      )}
    </div>
  );
}
