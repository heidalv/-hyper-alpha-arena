/**
 * MetaStrategyView — 元学习策略选择可视化 (F3-3)
 * 展示市场状态 → 策略 Nature 自适应映射及推荐权重
 * API: 使用现有 market regime + strategy 数据
 */
import { useState, useEffect, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { BarChart3, Cpu, RefreshCw, TrendingUp, Zap } from 'lucide-react';

interface StrategyRanking {
  strategy_id: string;
  template_id: string;
  symbol: string;
  tier: string;
  trade_nature: string;
  regime_score: number;
  historical_score: number;
  composite_score: number;
  weight: number;
  recommendation: string;
}

interface MetaSelection {
  market_regime: string;
  regime_confidence: number;
  volatility_ratio: number;
  selected_strategies: StrategyRanking[];
  paused_strategies: string[];
  summary: string;
  selected_at: string;
}

// Static preference matrix matching backend REGIME_NATURE_PREFERENCE
const REGIME_NATURE_MATRIX: Record<string, Record<string, number>> = {
  trending_up: { trend_follow: 1.3, swing: 0.8, position: 1.0, intraday: 0.6, scalp: 0.4 },
  trending_down: { trend_follow: 1.2, swing: 0.7, position: 0.8, intraday: 0.7, scalp: 0.5 },
  ranging: { swing: 1.0, scalp: 0.5, intraday: 0.5, trend_follow: 0.3, position: 0.3 },
  crash: { trend_follow: 0.1, swing: 0.1, position: 0.0, intraday: 0.1, scalp: 0.0 },
  high_volatility: { intraday: 1.0, scalp: 1.2, swing: 0.5, trend_follow: 0.5, position: 0.3 },
  low_volatility: { trend_follow: 1.2, position: 1.3, swing: 0.8, intraday: 0.4, scalp: 0.3 },
  unknown: { trend_follow: 0.6, swing: 0.6, position: 0.6, intraday: 0.5, scalp: 0.5 },
};

const REC_COLORS: Record<string, string> = {
  strong_buy: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  buy: 'bg-green-500/10 text-green-400 border-green-500/30',
  hold: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
  pause: 'bg-red-500/10 text-red-400 border-red-500/30',
};

const NATURE_COLORS: Record<string, string> = {
  trend_follow: 'bg-blue-500/10 text-blue-400',
  swing: 'bg-purple-500/10 text-purple-400',
  position: 'bg-green-500/10 text-green-400',
  intraday: 'bg-orange-500/10 text-orange-400',
  scalp: 'bg-pink-500/10 text-pink-400',
};

export default function MetaStrategyView() {
  const [currentRegime, setCurrentRegime] = useState<string>('unknown');
  const [regimeConf, setRegimeConf] = useState(0.5);
  const [volRatio, setVolRatio] = useState(1.0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRegime = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/evolution/regime-analysis');
      if (res.ok) {
        const data = await res.json();
        setCurrentRegime(data.current_regime || 'unknown');
        setRegimeConf(data.regime_confidence || 0.5);
      }
      // Also fetch volatility context
      try {
        const perfRes = await fetch('/api/analytics/performance/summary');
        if (perfRes.ok) {
          const perfData = await perfRes.json();
          const vol = perfData?.risk?.volatility || 0;
          setVolRatio(Math.max(0.3, Math.min(3.0, vol / 15 + 1)));
        }
      } catch {}
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRegime(); }, []);

  // Compute strategy preferences for current regime
  const preferences = useMemo(() => {
    return REGIME_NATURE_MATRIX[currentRegime] || REGIME_NATURE_MATRIX.unknown;
  }, [currentRegime]);

  // Sort natures by preference weight
  const sortedNatures = useMemo(() => {
    return Object.entries(preferences)
      .sort(([, a], [, b]) => b - a)
      .map(([nature, weight]) => {
        let rec = 'pause';
        if (weight >= 0.8) rec = 'strong_buy';
        else if (weight >= 0.4) rec = 'buy';
        else if (weight >= 0.2) rec = 'hold';
        return { nature, weight, recommendation: rec };
      });
  }, [preferences]);

  const regimeStyles: Record<string, string> = {
    trending_up: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
    trending_down: 'bg-red-500/10 text-red-400 border-red-500/30',
    ranging: 'bg-gray-500/10 text-gray-400 border-gray-500/30',
    crash: 'bg-red-700/10 text-red-600 border-red-700/30',
    high_volatility: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
    low_volatility: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
    unknown: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
  };

  return (
    <Card className="border-gray-800 bg-gray-900/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Cpu className="h-4 w-4 text-cyan-400" />
            Meta-Learning 策略选择 (F3-3)
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={fetchRegime} disabled={loading} className="h-7 w-7 p-0">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <div className="text-red-400 text-xs">{error}</div>}
        {loading && <div className="text-gray-500 text-xs text-center py-4">加载中...</div>}

        {/* Current Regime Badge */}
        <div className="flex items-center gap-3">
          <div className="text-xs text-gray-500">当前市场:</div>
          <Badge variant="outline" className={`text-sm px-3 py-1 ${regimeStyles[currentRegime] || regimeStyles.unknown}`}>
            {currentRegime.replace(/_/g, ' ').toUpperCase()}
          </Badge>
          <div className="text-xs text-gray-500">
            conf: {(regimeConf * 100).toFixed(0)}% | vol: {volRatio.toFixed(1)}x
          </div>
        </div>

        {/* Nature Preference Matrix */}
        <div>
          <div className="text-xs text-gray-500 mb-2 flex items-center gap-1">
            <TrendingUp className="h-3 w-3" /> 策略类型推荐权重
          </div>
          <div className="space-y-2">
            {sortedNatures.map(({ nature, weight, recommendation }) => (
              <div key={nature} className="flex items-center gap-2">
                <Badge variant="outline" className={`text-xs w-24 justify-center ${NATURE_COLORS[nature] || ''}`}>
                  {nature}
                </Badge>
                <div className="flex-1 bg-gray-800 rounded-full h-2.5">
                  <div
                    className={`h-2.5 rounded-full transition-all ${
                      weight >= 1.0 ? 'bg-emerald-500' :
                      weight >= 0.6 ? 'bg-green-500' :
                      weight >= 0.3 ? 'bg-yellow-500' :
                      weight > 0 ? 'bg-red-500' : 'bg-gray-700'
                    }`}
                    style={{ width: `${Math.min(100, (weight / 1.3) * 100)}%` }}
                  />
                </div>
                <span className="text-xs text-gray-400 w-10 text-right font-mono">
                  {weight.toFixed(1)}x
                </span>
                <Badge variant="outline" className={`text-xs ${REC_COLORS[recommendation]}`}>
                  {recommendation === 'strong_buy' ? '强烈推荐' :
                   recommendation === 'buy' ? '推荐' :
                   recommendation === 'hold' ? '观望' : '暂停'}
                </Badge>
              </div>
            ))}
          </div>
        </div>

        {/* Strategy Selection Summary */}
        <div className="bg-gray-800/30 rounded p-3">
          <div className="text-xs text-gray-500 mb-1 flex items-center gap-1">
            <Zap className="h-3 w-3 text-yellow-400" /> 自适应规则
          </div>
          <div className="space-y-1 text-xs text-gray-400">
            {currentRegime === 'crash' && (
              <p className="text-red-400">⚠ CRASH 状态 — 所有策略暂停交易（权重≈0）</p>
            )}
            {currentRegime === 'trending_up' && (
              <p className="text-emerald-400">↗ 趋势上行 — trend_follow 权重 1.3x，swing 正常</p>
            )}
            {currentRegime === 'ranging' && (
              <p className="text-yellow-400">↔ 震荡市场 — swing 权重正常，trend_follow 降权</p>
            )}
            {currentRegime === 'high_volatility' && (
              <p className="text-orange-400">⚡ 高波动 — intraday/scalp 优先，降低持仓周期</p>
            )}
            {currentRegime === 'low_volatility' && (
              <p className="text-blue-400">🔵 低波动 — position/trend_follow 优先，适合趋势持仓</p>
            )}
            <p>综合评分 = 0.5 × 市场匹配 + 0.5 × 历史表现</p>
            <p>权重 &lt; 0.3 的策略自动暂停</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
