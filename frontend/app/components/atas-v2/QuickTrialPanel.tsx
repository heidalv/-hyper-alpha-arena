/**
 * 快速试单 + 学习激活 — AI 策略中心专用面板
 *
 * 仪表盘 · 总开关 · 预设 · 参数配置表
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import {
  Zap,
  Brain,
  Gauge,
  Target,
  RefreshCw,
  Rocket,
  Scale,
  Activity,
  BookOpen,
  CheckCircle2,
  Timer,
  Loader2,
  Shield,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';
import { MetricCard } from '../ui/metric-card';
import {
  applyFastTrialPreset,
  getFastTrialConfig,
  patchFastTrialConfig,
  GEAR_LABELS,
  PRESET_ACCENT,
  type FastTrialParam,
  type FastTrialPreset,
  type FastTrialState,
} from '@/lib/fastTrialApi';

const PRESET_ICONS: Record<string, typeof Rocket> = {
  rocket: Rocket,
  brain: Brain,
  zap: Zap,
  target: Target,
  scale: Scale,
  shield: Shield,
};

function PresetCard({
  preset,
  active,
  saving,
  onApply,
}: {
  preset: FastTrialPreset;
  active: boolean;
  saving: boolean;
  onApply: (id: string) => void;
}) {
  const Icon = PRESET_ICONS[preset.icon || 'rocket'] || Rocket;
  const accent = PRESET_ACCENT[preset.accent || 'slate'] || PRESET_ACCENT.slate;

  return (
    <button
      type="button"
      disabled={saving}
      onClick={() => onApply(preset.id)}
      className={[
        'relative text-left rounded-xl border p-4 transition-all',
        accent,
        active ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : '',
        saving ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      {active && (
        <span className="absolute top-3 right-3 flex items-center gap-1 text-[10px] text-emerald-500 font-medium">
          <CheckCircle2 className="w-3 h-3" />
          当前
        </span>
      )}
      <div className="flex items-start gap-3 pr-12">
        <div className="p-2 rounded-lg bg-background/60 border border-border/50">
          <Icon className="w-4 h-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{preset.label}</p>
          <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{preset.desc}</p>
        </div>
      </div>
      {preset.highlights && preset.highlights.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {preset.highlights.map((h) => (
            <span
              key={h}
              className="text-[10px] px-2 py-0.5 rounded-full bg-background/70 border border-border/60 text-muted-foreground"
            >
              {h}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

function BoolSwitch({
  param,
  saving,
  onToggle,
}: {
  param: FastTrialParam;
  saving: boolean;
  onToggle: (key: string, value: boolean) => void;
}) {
  const checked = Boolean(param.effective);
  return (
    <div className="flex items-center justify-between gap-4 py-2.5 border-b border-border/50 last:border-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{param.label}</p>
        {param.desc && (
          <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{param.desc}</p>
        )}
        {param.overridden && (
          <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600">
            已热改
          </span>
        )}
      </div>
      <Switch
        checked={checked}
        disabled={saving}
        onCheckedChange={(v) => onToggle(param.key, v)}
      />
    </div>
  );
}

function ParamRow({
  param,
  saving,
  draft,
  onDraft,
  onSave,
}: {
  param: FastTrialParam;
  saving: boolean;
  draft: string;
  onDraft: (key: string, val: string) => void;
  onSave: (key: string, directValue?: unknown) => void;
}) {
  if (param.type === 'bool') return null;

  if (param.type === 'gear') {
    const opts = param.options || [];
    return (
      <tr className="border-b border-border/40 hover:bg-muted/30">
        <td className="py-2.5 px-3 text-sm">{param.label}</td>
        <td className="py-2.5 px-3">
          <div className="flex flex-wrap gap-1">
            {opts.map((g) => (
              <Button
                key={g}
                size="sm"
                variant={param.effective === g ? 'default' : 'outline'}
                className="h-7 text-xs px-2"
                disabled={saving}
                onClick={() => onSave(param.key, g)}
              >
                {GEAR_LABELS[g] || g}
              </Button>
            ))}
          </div>
        </td>
        <td className="py-2.5 px-3 text-xs text-muted-foreground max-w-[200px]">
          {param.desc || '—'}
        </td>
        <td className="py-2.5 px-3 text-center">
          {param.overridden ? (
            <span className="text-[10px] text-amber-600">热改</span>
          ) : (
            <span className="text-[10px] text-muted-foreground">默认</span>
          )}
        </td>
      </tr>
    );
  }

  return (
    <tr className="border-b border-border/40 hover:bg-muted/30">
      <td className="py-2.5 px-3 text-sm">{param.label}</td>
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <input
            type="number"
            step={param.type === 'float' ? '0.01' : '1'}
            min={param.min}
            max={param.max}
            value={draft}
            onChange={(e) => onDraft(param.key, e.target.value)}
            className="w-24 h-8 px-2 text-sm rounded border bg-background"
            disabled={saving}
          />
          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={saving || draft === String(param.effective)}
            onClick={() => onSave(param.key)}
          >
            应用
          </Button>
        </div>
      </td>
      <td className="py-2.5 px-3 text-xs text-muted-foreground">
        {param.min != null && param.max != null
          ? `范围 ${param.min}–${param.max}`
          : param.desc || '—'}
      </td>
      <td className="py-2.5 px-3 text-center">
        {param.overridden ? (
          <span className="text-[10px] text-amber-600">热改</span>
        ) : (
          <span className="text-[10px] text-muted-foreground">默认</span>
        )}
      </td>
    </tr>
  );
}

export default function QuickTrialPanel() {
  const [state, setState] = useState<FastTrialState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setLoadError(null);
      const data = await getFastTrialConfig();
      setState(data);
      const next: Record<string, string> = {};
      for (const g of data.schema.groups) {
        for (const p of g.params) {
          if (p.type !== 'bool' && p.type !== 'gear') {
            next[p.key] = String(p.effective ?? '');
          }
        }
      }
      setDrafts(next);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载配置失败';
      setLoadError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  const patch = async (patches: Record<string, unknown>) => {
    setSaving(true);
    try {
      const next = await patchFastTrialConfig(patches);
      setState(next);
      toast.success('配置已更新');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const toggleBool = (key: string, value: boolean) => patch({ [key]: value });

  const saveParam = async (key: string, directValue?: unknown) => {
    const val = directValue ?? drafts[key];
    if (val === undefined) return;
    const spec = state?.schema.groups.flatMap((g) => g.params).find((p) => p.key === key);
    if (!spec) return;
    let parsed: unknown = val;
    if (spec.type === 'int') parsed = parseInt(String(val), 10);
    else if (spec.type === 'float') parsed = parseFloat(String(val));
    await patch({ [key]: parsed });
  };

  const applyPreset = async (presetId: string) => {
    setSaving(true);
    try {
      const next = await applyFastTrialPreset(presetId);
      setState(next);
      const label = next.presets?.find((p) => p.id === presetId)?.label || presetId;
      toast.success(`已应用「${label}」预设`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '预设应用失败');
    } finally {
      setSaving(false);
    }
  };

  if (loading && !state) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">正在加载快速试单配置…</p>
      </div>
    );
  }

  if (loadError && !state) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4 mx-6">
        <p className="text-sm text-red-400">加载失败：{loadError}</p>
        <p className="text-xs text-muted-foreground text-center max-w-md">
          通常是后端未启动或刚改代码需重启。请确认 http://127.0.0.1:8000 正常后再试。
        </p>
        <Button size="sm" onClick={() => { setLoading(true); load(); }}>
          <RefreshCw className="w-3.5 h-3.5 mr-1" /> 重试
        </Button>
      </div>
    );
  }

  const dash = state?.dashboard || {};
  const pace = dash.pace;
  const mlto = dash.mlto;
  const bus = dash.learning_bus;
  const scalp = dash.scalp;
  const tierTick = dash.tier_tick;
  const iv = tierTick?.intervals_sec || {};

  const allBoolParams =
    state?.schema.groups.flatMap((g) => g.params.filter((p) => p.type === 'bool')) || [];

  return (
    <div className="mx-6 my-4 space-y-5 pb-8 max-w-5xl">
      {/* 说明条 */}
      <div className="px-4 py-3 rounded-lg border border-violet-500/25 bg-violet-500/5 text-sm">
        <span className="font-medium text-violet-300">模拟盘快速试单</span>
        <span className="text-muted-foreground ml-2">
          三周期 Tick 分离：协调器心跳（学习/巡检）快、中线/长线 AI 各自独立节奏；短线 ScalpRouter 独立扫描。快速试单加速的是学习与开单频率，不是把 LLM 都压到 30 秒。
        </span>
      </div>

      {/* 预设方案 */}
      <Card>
        <CardHeader className="py-3 flex flex-row items-center justify-between gap-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Rocket className="w-4 h-4 text-violet-400" />
            一键预设
          </CardTitle>
          <Button size="sm" variant="ghost" disabled={saving} onClick={load} className="gap-1.5 h-8">
            <RefreshCw className={`w-3.5 h-3.5 ${saving ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-xs text-muted-foreground mb-3">
            选预设会整包覆盖下方参数（三周期 Tick、门控、短线、学习）。手动改单项后「当前预设」标记会清除。
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(state?.presets || []).map((p) => (
              <PresetCard
                key={p.id}
                preset={p}
                active={state?.active_preset === p.id}
                saving={saving}
                onApply={applyPreset}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 仪表盘 — 三周期 Tick */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          icon={Gauge}
          title="协调器心跳"
          value={iv.coordinator ?? pace?.tick_seconds ?? 0}
          suffix="s"
          decimals={0}
          subtitle={
            tierTick?.scheduler_enabled
              ? '轻量 tick · 不含 LLM'
              : '分层调度已关闭'
          }
        />
        <MetricCard
          icon={Target}
          title="中线 AI"
          value={iv.mid ?? 120}
          suffix="s"
          decimals={0}
          subtitle={
            tierTick?.live?.due_now?.includes('mid')
              ? '本轮到期'
              : `距下次 ${tierTick?.live?.until_due_sec?.mid ?? '—'}s`
          }
        />
        <MetricCard
          icon={Activity}
          title="长线 AI"
          value={iv.long ?? 240}
          suffix="s"
          decimals={0}
          subtitle={
            tierTick?.live?.due_now?.includes('long')
              ? '本轮到期'
              : `距下次 ${tierTick?.live?.until_due_sec?.long ?? '—'}s`
          }
        />
        <MetricCard
          icon={Timer}
          title="短线因子"
          value={iv.short ?? scalp?.confirm_threshold ?? 45}
          suffix="s"
          decimals={0}
          subtitle="ScalpRouter 独立循环"
        />
      </div>

      {/* 仪表盘 — 全局 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          icon={Gauge}
          title="截流档"
          value={pace?.tick_seconds ?? 0}
          suffix="s"
          decimals={0}
          subtitle={GEAR_LABELS[pace?.gear || ''] || pace?.gear}
        />
        <MetricCard
          icon={Target}
          title="可开单研判"
          value={mlto?.can_open ?? 0}
          decimals={0}
          subtitle={`共 ${mlto?.thesis_total ?? 0} 条 · LLM ${mlto?.with_llm_summary ?? 0}`}
        />
        <MetricCard
          icon={BookOpen}
          title="距下次复盘"
          value={bus?.next_review_in ?? 0}
          decimals={0}
          suffix=" 笔"
          subtitle={`已处理 ${bus?.trade_count_total ?? 0} 笔`}
        />
        <MetricCard
          icon={Activity}
          title="运行会话"
          value={dash.sessions_running ?? 0}
          decimals={0}
          subtitle={
            dash.learning_loop?.registered
              ? dash.learning_loop?.paused
                ? '学习环已暂停'
                : '学习环运行中'
              : '学习环未注册'
          }
        />
      </div>

      {/* 仪表盘 — 短线 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          icon={Timer}
          title="短线探索门槛"
          value={scalp?.confirm_threshold ?? 35}
          decimals={0}
          subtitle={`直通 ≥ ${scalp?.execute_threshold ?? 45}`}
        />
        <MetricCard
          icon={Zap}
          title="开仓冷却"
          value={scalp?.open_cooldown_sec ?? 300}
          suffix="s"
          decimals={0}
          subtitle="同币两次开仓最小间隔"
        />
        <MetricCard
          icon={Timer}
          title="平仓后再开"
          value={Math.round((scalp?.reentry_cooldown_sec ?? 14400) / 60)}
          suffix=" min"
          decimals={0}
          subtitle="短线 tier 平仓冷却"
        />
        <MetricCard
          icon={Activity}
          title="独立调度"
          value={scalp?.independent_scheduler ? 1 : 0}
          decimals={0}
          subtitle={scalp?.independent_scheduler ? 'ScalpRouter 运行中' : '已关闭'}
        />
      </div>

      {/* 总开关卡片 */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            控制开关
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {allBoolParams.map((p) => (
            <BoolSwitch
              key={p.key}
              param={p}
              saving={saving}
              onToggle={toggleBool}
            />
          ))}
        </CardContent>
      </Card>

      {/* 参数配置表（按分组） */}
      {state?.schema.groups
        .filter((g) => g.id !== 'master')
        .map((group) => {
          const tableParams = group.params.filter((p) => p.type !== 'bool');
          if (!tableParams.length) return null;
          return (
            <Card key={group.id}>
              <CardHeader className="py-3">
                <CardTitle className="text-sm flex items-center gap-2">
                  {group.id === 'tier_tick' && <Zap className="w-4 h-4" />}
                  {group.id === 'pace' && <Gauge className="w-4 h-4" />}
                  {group.id === 'open_gate' && <Target className="w-4 h-4" />}
                  {group.id === 'scalp' && <Timer className="w-4 h-4" />}
                  {group.id === 'learning' && <Brain className="w-4 h-4" />}
                  {group.label}
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0 overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-xs text-muted-foreground border-b">
                      <th className="py-2 px-3 font-medium">参数</th>
                      <th className="py-2 px-3 font-medium">当前值</th>
                      <th className="py-2 px-3 font-medium">说明</th>
                      <th className="py-2 px-3 font-medium text-center w-16">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tableParams.map((p) => (
                      <ParamRow
                        key={p.key}
                        param={p}
                        saving={saving}
                        draft={drafts[p.key] ?? String(p.effective ?? '')}
                        onDraft={(k, v) => setDrafts((d) => ({ ...d, [k]: v }))}
                        onSave={saveParam}
                      />
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          );
        })}

      {/* 当前有效值摘要 */}
      {state?.effective && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm text-muted-foreground">当前生效参数快照</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <pre className="text-[11px] leading-relaxed overflow-x-auto rounded-md bg-muted/40 p-3 text-muted-foreground">
              {JSON.stringify(state.effective, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
