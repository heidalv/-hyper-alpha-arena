/**
 * 中长线策略配置页 — 中线/长线共用，通过 tier prop 区分
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { RefreshCw, Save, Loader2 } from 'lucide-react';
import {
  fetchStrategyConfig, updateStrategyConfig, fetchStrategyPresets,
  type ParamDef, type GroupDef, type StrategyStats, type Preset, type Tier,
} from '@/lib/strategyConfigApi';

export default function StrategyConfigPage({ tier }: { tier: Tier }) {
  const title = tier === 'mid' ? '📊 中线策略配置' : '📈 长线策略配置';
  const [config, setConfig] = useState<Record<string, number | boolean> | null>(null);
  const [paramDefs, setParamDefs] = useState<Record<string, ParamDef>>({});
  const [groups, setGroups] = useState<Record<string, GroupDef>>({});
  const [stats, setStats] = useState<StrategyStats | null>(null);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [activePreset, setActivePreset] = useState<string>('');

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const [cfgResp, presResp] = await Promise.all([
        fetchStrategyConfig(tier), fetchStrategyPresets(tier)
      ]);
      setConfig(cfgResp.config);
      setParamDefs(cfgResp.param_defs);
      setGroups(cfgResp.groups);
      setStats(cfgResp.stats);
      setPresets(presResp);
      setDirty(false);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [tier]);

  useEffect(() => { setLoading(true); loadData(); }, [loadData]);

  const updateParam = (key: string, value: number | boolean) => {
    setConfig(prev => prev ? { ...prev, [key]: value } : prev);
    setDirty(true);
    setActivePreset('');
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const resp = await updateStrategyConfig(tier, config as Record<string, any>);
      if (resp.success) {
        setConfig(resp.config);
        setDirty(false);
      }
    } catch (e: any) {
      setError(e.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const applyPreset = (key: string) => {
    const preset = presets[key];
    if (!preset || !config) return;
    setConfig(prev => prev ? { ...prev, ...preset.params } : prev);
    setDirty(true);
    setActivePreset(key);
  };

  // 按 group 分组
  const groupedParams = useMemo(() => {
    const result: Record<string, string[]> = {};
    for (const [key, def] of Object.entries(paramDefs)) {
      const g = def.group;
      if (!result[g]) result[g] = [];
      result[g].push(key);
    }
    return result;
  }, [paramDefs]);

  const sortedGroups = useMemo(() =>
    Object.entries(groups).sort((a, b) => a[1].order - b[1].order)
  , [groups]);

  if (loading || !config) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex flex-col h-full overflow-auto p-4 space-y-4 max-w-5xl mx-auto">
      {/* 标题栏 */}
      <div className="flex items-center justify-between flex-shrink-0">
        <h2 className="text-xl font-bold">{title}</h2>
        <div className="flex items-center gap-2">
          {dirty && <Badge variant="destructive" className="text-xs">未保存</Badge>}
          <Button variant="outline" size="sm" onClick={loadData} disabled={saving}>
            <RefreshCw className="w-3.5 h-3.5 mr-1" />刷新
          </Button>
          <Button size="sm" onClick={handleSave} disabled={!dirty || saving}>
            {saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
            保存并应用
          </Button>
        </div>
      </div>

      {/* 预设方案 */}
      <div className="flex gap-2 flex-wrap flex-shrink-0">
        {Object.entries(presets).map(([key, p]) => (
          <Button
            key={key}
            variant={activePreset === key ? 'default' : 'outline'}
            size="sm"
            onClick={() => applyPreset(key)}
          >
            {activePreset === key ? '✓ ' : ''}{p.name}
            <span className="text-xs text-muted-foreground ml-2">{p.description}</span>
          </Button>
        ))}
      </div>

      {error && <div className="text-sm text-red-500 bg-red-500/10 p-2 rounded">⚠️ {error}</div>}

      {/* 实测统计 */}
      {stats && stats.trades > 0 && (
        <Card>
          <CardContent className="pt-4">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <StatBox label="近7天" value={`${stats.trades}笔`} />
              <StatBox label="胜率" value={`${(stats.win_rate * 100).toFixed(0)}%`} positive={stats.win_rate >= 0.5} />
              <StatBox label="净PnL" value={`${stats.net_pnl > 0 ? '+' : ''}${stats.net_pnl.toFixed(1)}`} positive={stats.net_pnl > 0} />
              <StatBox label="盈亏比" value={stats.profit_factor.toFixed(2)} positive={stats.profit_factor >= 1.0} />
              <StatBox label="平均持仓" value={`${stats.avg_hold_hours}h`} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* 长线分批止盈可视化 */}
      {tier === 'long' && config.staged_tp_enabled && (
        <Card className="border-primary/20">
          <CardContent className="pt-4">
            <div className="text-sm font-medium mb-2">📊 分批止盈路线图</div>
            <div className="flex items-center gap-2 text-xs flex-wrap">
              <span className="px-2 py-1 bg-muted rounded">持仓</span>
              <span>→</span>
              <span className="px-2 py-1 bg-green-500/10 text-green-500 rounded">+{(config.tp1_trigger as number * 100).toFixed(0)}% 减{(config.tp1_reduce as number * 100).toFixed(0)}%</span>
              <span>→</span>
              <span className="px-2 py-1 bg-green-500/10 text-green-500 rounded">+{(config.tp2_trigger as number * 100).toFixed(0)}% 减{(config.tp2_reduce as number * 100).toFixed(0)}%</span>
              <span>→</span>
              <span className="px-2 py-1 bg-green-500/10 text-green-500 rounded">+{(config.tp3_trigger as number * 100).toFixed(0)}% 减{(config.tp3_reduce as number * 100).toFixed(0)}%</span>
              <span>→</span>
              <span className="px-2 py-1 bg-blue-500/10 text-blue-500 rounded">ATR trailing ({config.trailing_atr_mult}x)</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 参数分组 */}
      {sortedGroups.map(([groupKey, groupDef]) => {
        const keys = groupedParams[groupKey];
        if (!keys || keys.length === 0) return null;
        return (
          <Card key={groupKey}>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">{groupDef.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {keys.map(key => (
                <ParamRow
                  key={key}
                  def={paramDefs[key]}
                  value={config[key]}
                  onChange={(v) => updateParam(key, v)}
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

function StatBox({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="p-2 rounded-lg bg-muted">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-sm font-bold ${positive === undefined ? '' : positive ? 'text-green-500' : 'text-red-500'}`}>{value}</div>
    </div>
  );
}

function ParamRow({ def, value, onChange }: {
  def: ParamDef;
  value: number | boolean;
  onChange: (v: number | boolean) => void;
}) {
  if (def.type === 'bool') {
    return (
      <div className="flex items-center justify-between py-1">
        <span className="text-sm">{def.label}</span>
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
  const displayValue = def.unit === '%' ? (numValue * 100).toFixed(1) : numValue.toString();
  const isDanger = def.label.includes('SL') && numValue >= 0.04 && numValue <= 0.06 && def.label.includes('止损');

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-sm">{def.label}</span>
        <span className={`text-sm font-bold ${isDanger ? 'text-orange-500' : ''}`}>
          {displayValue}{def.unit === '%' ? '%' : def.unit ? ` ${def.unit}` : ''}
        </span>
      </div>
      <input
        type="range"
        min={def.min}
        max={def.max}
        step={def.type === 'int' ? 1 : (def.max - def.min) / 100}
        value={numValue}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full"
      />
    </div>
  );
}
