import { useState, useEffect, useCallback }from 'react'
import { cn, fmtTime, fmtDateTime } from '@/lib/utils';
import {
  Brain, RefreshCw, TrendingUp, TrendingDown, Minus,
  ChevronDown, ChevronRight, Clock, Target, Shield,
  BarChart3, Filter, AlertTriangle
} from 'lucide-react';

interface DecisionEntry {
  id: number;
  symbol: string;
  operation: string;
  confidence: number;
  reasoning: string;
  target_portion: number;
  total_balance: number;
  order_id?: string;
  decision_time: string;
  trigger_mode?: string;
  executed?: string;
  account_name?: string;
  prompt_template_name?: string;
  leverage?: number;
  stop_loss_price?: number;
  take_profit_price?: number;
  tier?: string | null;
  agent_source?: string | null;
}

// 周期（短/长）显示配置 —— 中线已合并到长线，mid 仅做向后兼容映射
const TIER_CONFIG: Record<string, { label: string; color: string }> = {
  short: { label: '短线', color: 'text-sky-500 bg-sky-500/10 border border-sky-500/20' },
  long: { label: '长线', color: 'text-amber-600 bg-amber-500/10 border border-amber-500/20' },
};

const OP_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode; bg: string }> = {
  buy: { label: '买入', color: 'text-green-500', icon: <TrendingUp className="w-4 h-4" />, bg: 'bg-green-500/10 border-green-500/20' },
  sell: { label: '卖出', color: 'text-red-500', icon: <TrendingDown className="w-4 h-4" />, bg: 'bg-red-500/10 border-red-500/20' },
  hold: { label: '观望', color: 'text-muted-foreground', icon: <Minus className="w-4 h-4" />, bg: 'bg-muted/50 border-border' },
  close: { label: '平仓', color: 'text-orange-500', icon: <AlertTriangle className="w-4 h-4" />, bg: 'bg-orange-500/10 border-orange-500/20' },
  reduce: { label: '减仓', color: 'text-amber-500', icon: <TrendingDown className="w-4 h-4" />, bg: 'bg-amber-500/10 border-amber-500/20' },
  pyramid: { label: '加仓', color: 'text-emerald-500', icon: <TrendingUp className="w-4 h-4" />, bg: 'bg-emerald-500/10 border-emerald-500/20' },
  dca: { label: '补仓', color: 'text-blue-500', icon: <TrendingUp className="w-4 h-4" />, bg: 'bg-blue-500/10 border-blue-500/20' },
};

export default function AIDecisionHistory() {
  const [decisions, setDecisions] = useState<DecisionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filter, setFilter] = useState<'all' | 'buy' | 'sell' | 'hold' | 'close'>('all');
  // 周期筛选：短线刷屏会把长线挤掉，按 tier 服务端筛选才能看到长线分析
  const [tierFilter, setTierFilter] = useState<'all' | 'short' | 'long'>('all');

  const fetchDecisions = useCallback(async () => {
    setLoading(true);
    try {
      const tierQ = tierFilter === 'all' ? '' : `&tier=${tierFilter}`;
      const res = await fetch(`/api/arena/model-chat?limit=30${tierQ}`);
      if (res.ok) {
        const data = await res.json();
        setDecisions(data.entries || []);
      }
    } catch (e) {
      console.error('[AIDecisionHistory] fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, [tierFilter]);

  useEffect(() => { fetchDecisions(); }, [fetchDecisions]);

  const filtered = filter === 'all'
    ? decisions
    : decisions.filter(d => d.operation === filter);

  const stats = {
    total: decisions.length,
    buy: decisions.filter(d => d.operation === 'buy').length,
    sell: decisions.filter(d => d.operation === 'sell').length,
    hold: decisions.filter(d => d.operation === 'hold').length,
    close: decisions.filter(d => d.operation === 'close').length,
    reduce: decisions.filter(d => d.operation === 'reduce').length,
    pyramid: decisions.filter(d => d.operation === 'pyramid').length,
    dca: decisions.filter(d => d.operation === 'dca').length,
    activeCount: decisions.filter(d => !['hold'].includes(d.operation)).length,
    avgConfidence: decisions.length > 0
      ? decisions.reduce((s, d) => s + (d.confidence || 0), 0) / decisions.length
      : 0,
  };

  const activePct = stats.total > 0 ? ((stats.activeCount / stats.total) * 100) : 0;

  return (
    <div className="p-6 space-y-6">
      {/* 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <StatCard label="总决策数" value={stats.total} sub="近100条" />
        <StatCard label="买入" value={stats.buy} color="text-green-500"
          sub={stats.total > 0 ? `${((stats.buy / stats.total) * 100).toFixed(0)}%` : '0%'} />
        <StatCard label="卖出" value={stats.sell} color="text-red-500"
          sub={stats.total > 0 ? `${((stats.sell / stats.total) * 100).toFixed(0)}%` : '0%'} />
        <StatCard label="平仓" value={stats.close} color="text-orange-500"
          sub={stats.total > 0 ? `${((stats.close / stats.total) * 100).toFixed(0)}%` : '0%'} />
        <StatCard label="观望" value={stats.hold} color="text-yellow-500"
          sub={stats.total > 0 ? `${((stats.hold / stats.total) * 100).toFixed(0)}%` : '0%'} />
        <StatCard label="平均置信度" value={`${(stats.avgConfidence * 100).toFixed(0)}%`}
          color={stats.avgConfidence >= 0.7 ? 'text-green-500' : 'text-yellow-500'} />
      </div>

      {/* 周期筛选（短/长）——服务端按 tier 拉取，避免短线刷屏淹没长线 */}
      <div className="flex items-center gap-1 bg-muted rounded-lg p-1 w-fit">
        {(['all', 'short', 'long'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTierFilter(t)}
            className={cn(
              'px-3 py-1.5 rounded-md text-xs font-medium transition-all',
              tierFilter === t
                ? 'bg-background shadow text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t === 'all' ? '全周期' : t === 'short' ? '短线' : '长线'}
          </button>
        ))}
      </div>

      {/* 筛选 + 刷新 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 bg-muted rounded-lg p-1">
          {(['all', 'buy', 'sell', 'hold', 'close'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'px-3 py-1.5 rounded-md text-xs font-medium transition-all',
                filter === f
                  ? 'bg-background shadow text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              {f === 'all' ? '全部' : f === 'buy' ? '买入' : f === 'sell' ? '卖出' : f === 'hold' ? '观望' : '平仓'}              {f !== 'all' && ` (${f === 'buy' ? stats.buy : f === 'sell' ? stats.sell : f === 'hold' ? stats.hold : stats.close})`}
            </button>
          ))}
        </div>
        <button onClick={fetchDecisions} disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-secondary hover:bg-secondary/80 text-secondary-foreground rounded-lg">
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {/* 决策列表 */}
      <div className="space-y-2">
        {loading && decisions.length === 0 && (
          <div className="text-center py-16 text-muted-foreground">
            <Brain className="w-8 h-8 mx-auto mb-3 animate-pulse opacity-40" />
            <p className="text-sm">加载决策记录...</p>
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="text-center py-16 text-muted-foreground">
            <Brain className="w-8 h-8 mx-auto mb-3 opacity-30" />
            <p className="text-sm">暂无 {filter !== 'all' ? `${filter === 'buy' ? '买入' : filter === 'sell' ? '卖出' : '观望'}` : ''} 决策记录</p>
          </div>
        )}

        {filtered.map(d => {
          const op = OP_CONFIG[d.operation] || OP_CONFIG.hold;
          const expanded = expandedId === d.id;
          return (
            <div
              key={d.id}
              className={cn('rounded-xl border p-3 transition-all cursor-pointer', op.bg, 'hover:shadow-sm')}
              onClick={() => setExpandedId(expanded ? null : d.id)}
            >
              {/* 主行 */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={cn('p-1.5 rounded-lg', op.color)}>
                    {op.icon}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold text-sm">{d.symbol}</span>
                      {d.tier && TIER_CONFIG[d.tier] && (
                        <span className={cn('text-xs font-medium px-1.5 py-0.5 rounded', TIER_CONFIG[d.tier].color)}>
                          {TIER_CONFIG[d.tier].label}
                        </span>
                      )}
                      <span className={cn('text-xs font-medium px-1.5 py-0.5 rounded', op.color, 'bg-background/50')}>
                        {op.label}
                      </span>
                      {d.executed === 'true' && (
                        <span className="text-xs text-green-500 bg-green-500/10 px-1.5 py-0.5 rounded">已执行</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5 line-clamp-1 max-w-md">
                      {d.reasoning || '无推理记录'}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-4 text-xs text-right">
                  <div>
                    <div className="text-muted-foreground">置信度</div>
                    <div className={cn('font-mono font-medium',
                      (d.confidence || 0) >= 0.7 ? 'text-green-500' : (d.confidence || 0) >= 0.5 ? 'text-yellow-500' : 'text-red-500'
                    )}>
                      {((d.confidence || 0) * 100).toFixed(0)}%
                    </div>
                  </div>
                  <div>
                    <div className="text-muted-foreground">仓位</div>
                    <div className="font-mono">{((d.target_portion || 0) * 100).toFixed(0)}%</div>
                  </div>
                  <div className="text-muted-foreground/60">
                    {d.decision_time ? fmtTime(d.decision_time) : '--'}
                  </div>
                  {expanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                </div>
              </div>

              {/* 展开详情 */}
              {expanded && (
                <div className="mt-3 pt-3 border-t border-border/50 space-y-2 text-xs">
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                    <DetailItem icon={<Clock />} label="时间" value={d.decision_time ? fmtDateTime(d.decision_time) : '-'} />
                    <DetailItem icon={<Target />} label="止盈" value={d.take_profit_price ? `$${Number(d.take_profit_price).toLocaleString()}` : '未设置'} />
                    <DetailItem icon={<Shield />} label="止损" value={d.stop_loss_price ? `$${Number(d.stop_loss_price).toLocaleString()}` : '未设置'} />
                    <DetailItem icon={<BarChart3 />} label="杠杆" value={d.leverage ? `${d.leverage}x` : '-'} />
                    <DetailItem icon={<Target />} label="触发" value={d.trigger_mode || '定时'} />
                  </div>
                  {d.reasoning && (
                    <div className="bg-muted/30 rounded-lg p-3 mt-2">
                      <div className="text-muted-foreground mb-1 font-medium">AI 推理</div>
                      <div className="text-foreground/80 whitespace-pre-line leading-relaxed">{d.reasoning}</div>
                    </div>
                  )}
                  <div className="flex items-center gap-4 text-muted-foreground/60 pt-1">
                    {d.account_name && <span>账户: {d.account_name}</span>}
                    {d.prompt_template_name && <span>模板: {d.prompt_template_name}</span>}
                    <span>ID: {d.id}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatCard({ label, value, color, sub }: {
  label: string; value: string | number; color?: string; sub?: string
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className={cn('text-xl font-bold font-mono', color)}>{value}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
      {sub && <div className="text-xs text-muted-foreground/60 mt-0.5">{sub}</div>}
    </div>
  );
}

function DetailItem({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground/50 w-4 h-4">{icon}</span>
      <span className="text-muted-foreground">{label}:</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}
