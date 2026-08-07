/**
 * AI策略详情页 (V3 - Tab导航版)
 * 
 * Tab结构：
 * 1. 概览 - 市场环境 + 动态风控 + 策略表现
 * 2. 自主监控 - 三周期分析状态 + AI决策日志
 * 3. 持仓追踪 - 实时PnL + 动态TP/SL + 手动干预
 * 4. 回测优化 - 自主回测-评估-AI修改-再回测闭环
 * 5. 自学习 - 策略记忆 + 提示词进化 + 表现热力图
 * 6. 配置 - 所有策略配置参数
 */

import { useState, useEffect }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { 
  Play, Pause, RefreshCw, Edit, ArrowLeft, Shield, 
  TrendingUp, TrendingDown, Activity, AlertTriangle, 
  Brain, Target, Loader2, BarChart3, Zap, Eye, Radio,
  Settings, Crosshair, GraduationCap, LineChart
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import StrategyMonitorPanel from './StrategyMonitorPanel';
import PositionTrackerPanel from './PositionTrackerPanel';
import BacktestOptimizePanel from './BacktestOptimizePanel';
import LearningDashboard from './LearningDashboard';

type TabId = 'overview' | 'monitor' | 'positions' | 'backtest' | 'learning' | 'config';

const TABS: { id: TabId; label: string; icon: React.ReactNode; activeOnly?: boolean }[] = [
  { id: 'overview', label: '概览', icon: <BarChart3 className="w-4 h-4" /> },
  { id: 'monitor', label: '自主监控', icon: <Radio className="w-4 h-4" />, activeOnly: true },
  { id: 'positions', label: '持仓追踪', icon: <Crosshair className="w-4 h-4" />, activeOnly: true },
  { id: 'backtest', label: '回测优化', icon: <Target className="w-4 h-4" /> },
  { id: 'learning', label: '自学习', icon: <GraduationCap className="w-4 h-4" /> },
  { id: 'config', label: '配置', icon: <Settings className="w-4 h-4" /> },
];

interface AIStrategy {
  id: number;
  strategy_id: string;
  name: string;
  description?: string;
  status: string;
  account_id: number;
  master_prompt_template_id: number;
  prompt_version: number;
  prompt_variables: any;
  signal_pool_ids: number[];
  trigger_mode: string;
  trigger_interval?: number;
  enabled_factors: string[];
  factor_weights: any;
  // 交易对
  target_symbols?: string[];
  primary_symbol?: string;
  timeframe?: string;
  // 杠杆
  max_leverage?: number;
  default_leverage?: number;
  leverage_mode?: string;
  // 自主模式
  auto_mode?: string;
  // 风控
  max_position_size: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  max_daily_loss: number;
  auto_execute: boolean;
  require_confirmation: boolean;
  min_confidence: number;
  learning_enabled: boolean;
  optimization_target: string;
  training_frequency: string;
  created_at: string;
  activated_at?: string;
  last_executed_at?: string;
}

interface StrategyMemory {
  strategy_id: string;
  total_trades: number;
  win_rate: number;
  avg_profit: number;
  avg_loss: number;
  sharpe_ratio: number;
  max_drawdown: number;
  performance_by_regime?: Record<string, { trades: number; wins: number; total_pnl: number }>;
  successful_patterns?: any[];
  failed_patterns?: any[];
  key_lessons?: any[];
  updated_at?: string;
}

interface MarketEnvironment {
  symbol: string;
  macro: {
    market_cycle: string;
    cycle_confidence: number;
    risk_budget_pct: number;
  };
  micro: {
    volatility_regime: string;
    volatility_value: number;
    trend_direction: string;
    trend_strength: number;
    liquidity_score: number;
  };
  adapted_params: {
    sl_multiplier: number;
    tp_multiplier: number;
    position_scale: number;
    entry_threshold: number;
  };
  guidance: string;
  // 数据溯源
  data_source?: string;
  price_source?: string;  // realtime / kline_fresh / kline_stale
  kline_count?: number;
  kline_age_hours?: number;
  current_price?: number;
  atr_value?: number;
  analysis_time?: string;
  price_stale_warning?: string;
}

interface DynamicRisk {
  symbol: string;
  side: string;
  stop_loss: {
    type: string;
    pct: number;
    price?: number;
    atr_multiple: number;
  };
  take_profit_levels: { pct: number; close_ratio: number }[];
  trailing_stop: {
    enabled: boolean;
    activation_pct: number;
    distance_pct: number;
  };
  time_stop: {
    enabled: boolean;
    hours: number;
  };
  position_size_pct: number;
  market_env_summary: {
    cycle: string;
    volatility: string;
    trend: string;
  };
}

// 市场周期标签样式
const cycleStyles: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  bull: { label: '牛市', color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400', icon: <TrendingUp className="w-3 h-3" /> },
  bear: { label: '熊市', color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400', icon: <TrendingDown className="w-3 h-3" /> },
  sideways: { label: '震荡', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', icon: <Activity className="w-3 h-3" /> },
  unknown: { label: '数据不足', color: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400', icon: <AlertTriangle className="w-3 h-3" /> },
};

const volStyles: Record<string, { label: string; color: string }> = {
  low: { label: '低波动', color: 'text-blue-600 dark:text-blue-400' },
  normal: { label: '正常', color: 'text-gray-600 dark:text-gray-400' },
  high: { label: '高波动', color: 'text-orange-600 dark:text-orange-400' },
  extreme: { label: '极端', color: 'text-red-600 dark:text-red-400' },
};

export default function AiStrategyDetail({ strategyId }: { strategyId: string }) {
  const [strategy, setStrategy] = useState<AIStrategy | null>(null);
  const [memory, setMemory] = useState<StrategyMemory | null>(null);
  const [marketEnv, setMarketEnv] = useState<MarketEnvironment | null>(null);
  const [dynamicRisk, setDynamicRisk] = useState<DynamicRisk | null>(null);
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [loadingEnv, setLoadingEnv] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  useEffect(() => {
    loadAll();
  }, [strategyId]);

  const loadAll = async () => {
    setLoading(true);
    await Promise.all([loadStrategy(), loadMemory()]);
    setLoading(false);
  };

  const loadStrategy = async () => {
    try {
      const response = await fetch(`/api/ai-strategies/${strategyId}`);
      if (!response.ok) throw new Error('Failed to load');
      const data = await response.json();
      setStrategy(data);
      // 加载市场环境
      loadMarketEnv(data);
    } catch (error) {
      console.error('Load error:', error);
    }
  };

  const loadMemory = async () => {
    try {
      const response = await fetch(`/api/ai-strategies/${strategyId}/memory`);
      if (!response.ok) throw new Error('Failed to load memory');
      const data = await response.json();
      setMemory(data);
    } catch (error) {
      console.error('Load memory error:', error);
    }
  };

  const loadMarketEnv = async (strat?: AIStrategy) => {
    setLoadingEnv(true);
    const sym = strat?.primary_symbol || strategy?.primary_symbol || 'BTC';
    try {
      const envRes = await fetch(`/api/ai-strategies/${strategyId}/market-environment?symbol=${sym}`);
      if (envRes.ok) {
        setMarketEnv(await envRes.json());
      }
      
      const riskRes = await fetch(`/api/ai-strategies/${strategyId}/dynamic-risk?symbol=${sym}&side=buy`);
      if (riskRes.ok) {
        setDynamicRisk(await riskRes.json());
      }
    } catch (error) {
      console.error('Load market env error:', error);
    } finally {
      setLoadingEnv(false);
    }
  };

  const handleActivate = async () => {
    try {
      const response = await fetch(`/api/ai-strategies/${strategyId}/activate`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已激活');
      loadStrategy();
    } catch (error) {
      toast.error('激活失败');
    }
  };

  const handlePause = async () => {
    try {
      const response = await fetch(`/api/ai-strategies/${strategyId}/pause`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已暂停');
      loadStrategy();
    } catch (error) {
      toast.error('暂停失败');
    }
  };

  const handleExecute = async () => {
    setExecuting(true);
    const loadingToast = toast.loading('策略正在执行，AI 正在分析市场并生成决策...');
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);
      
      const response = await fetch(`/api/ai-strategies/${strategyId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger_reason: '手动触发' }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      
      const result = await response.json();
      if (result.success) {
        toast.success(`执行成功，产生 ${result.decisions?.length || 0} 个决策`, { id: loadingToast });
        // 刷新记忆和市场环境
        loadMemory();
        loadMarketEnv();
      } else {
        toast.error(`执行失败: ${result.error_message || '未知错误'}`, { id: loadingToast });
      }
    } catch (error: any) {
      if (error.name === 'AbortError') {
        toast.error('执行超时，请稍后查看结果', { id: loadingToast });
      } else {
        toast.error('执行失败', { id: loadingToast });
      }
    } finally {
      setExecuting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (!strategy) {
    return (
      <div className="container mx-auto p-6">
        <p className="text-gray-500">策略不存在</p>
      </div>
    );
  }

  const cycleInfo = cycleStyles[marketEnv?.macro?.market_cycle || 'unknown'] || cycleStyles.unknown;
  const volInfo = volStyles[marketEnv?.micro?.volatility_regime || 'normal'] || volStyles.normal;

  const visibleTabs = TABS.filter(t => !t.activeOnly || strategy.status === 'active');

  return (
    <div className="container mx-auto p-6 space-y-4">
      {/* ===== 头部 ===== */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => window.history.back()}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">{strategy.name}</h1>
              <Badge className={strategy.status === 'active' ? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400' : ''}>
                {strategy.status === 'active' && (
                  <span className="relative flex h-2 w-2 mr-1.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                  </span>
                )}
                {strategy.status === 'active' ? '运行中' : strategy.status === 'paused' ? '已暂停' : strategy.status}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">{strategy.description}</p>
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              {strategy.target_symbols && strategy.target_symbols.length > 0 && (
                strategy.target_symbols.map(sym => (
                  <span key={sym} className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    sym === strategy.primary_symbol
                      ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400'
                      : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'
                  }`}>
                    {sym === strategy.primary_symbol ? `★${sym}/USDT` : `${sym}/USDT`}
                  </span>
                ))
              )}
              {strategy.timeframe && (() => {
                const tf = strategy.timeframe;
                const periodLabel = ['1m','3m','5m'].includes(tf) ? '超短线'
                  : ['15m'].includes(tf) ? '短线'
                  : ['30m','1h','4h','1d'].includes(tf) ? '长线' : '';
                const periodColor = ['1m','3m','5m','15m'].includes(tf)
                  ? 'bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400'
                  : 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400';
                return (
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${periodColor}`}>
                    {periodLabel ? `${tf} ${periodLabel}` : tf}
                  </span>
                );
              })()}
              {(strategy.default_leverage || strategy.max_leverage) && (
                <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-400">
                  {strategy.default_leverage || 1}x{strategy.leverage_mode === 'isolated' ? '逐仓' : '全仓'}
                  {strategy.max_leverage ? ` (≤${strategy.max_leverage}x)` : ''}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex gap-2">
          {strategy.status === 'active' ? (
            <Button variant="outline" size="sm" onClick={handlePause}>
              <Pause className="w-4 h-4 mr-1.5" />暂停
            </Button>
          ) : (
            <Button size="sm" onClick={handleActivate}>
              <Play className="w-4 h-4 mr-1.5" />激活
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={handleExecute} disabled={executing}>
            {executing ? (
              <><Loader2 className="w-4 h-4 mr-1.5 animate-spin" />执行中...</>
            ) : (
              <><Zap className="w-4 h-4 mr-1.5" />执行</>
            )}
          </Button>
        </div>
      </div>

      {/* ===== Tab 导航栏 ===== */}
      <div className="border-b dark:border-gray-800">
        <nav className="flex gap-0.5 -mb-px overflow-x-auto">
          {visibleTabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-muted-foreground hover:text-foreground hover:border-gray-300'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* ===== Tab 内容 ===== */}

      {/* 概览 Tab */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* 市场环境分析卡片 */}
      {marketEnv && (
        <Card className="border-l-4 border-l-blue-500">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Activity className="w-5 h-5 text-blue-500" />
              市场环境分析
              {loadingEnv && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
              <Button size="sm" variant="ghost" onClick={() => loadMarketEnv()} className="ml-auto">
                <RefreshCw className="w-3 h-3" />
              </Button>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
              {/* 宏观周期 */}
              <div className="text-center">
                <div className="text-xs text-gray-500 mb-1">市场周期</div>
                <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-sm font-medium ${cycleInfo.color}`}>
                  {cycleInfo.icon} {cycleInfo.label}
                </span>
                <div className="text-xs text-gray-400 mt-1">
                  置信度 {(marketEnv.macro.cycle_confidence * 100).toFixed(0)}%
                </div>
              </div>
              
              {/* 波动率 */}
              <div className="text-center">
                <div className="text-xs text-gray-500 mb-1">波动率</div>
                <div className={`text-lg font-bold ${volInfo.color}`}>
                  {volInfo.label}
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  {(marketEnv.micro.volatility_value * 100).toFixed(2)}%
                </div>
              </div>
              
              {/* 趋势 */}
              <div className="text-center">
                <div className="text-xs text-gray-500 mb-1">趋势方向</div>
                <div className={`text-lg font-bold ${
                  marketEnv.micro.trend_direction === 'bullish' ? 'text-green-600' :
                  marketEnv.micro.trend_direction === 'bearish' ? 'text-red-600' : 'text-gray-600'
                }`}>
                  {marketEnv.micro.trend_direction === 'bullish' ? '看多' :
                   marketEnv.micro.trend_direction === 'bearish' ? '看空' : '中性'}
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  强度 {(marketEnv.micro.trend_strength * 100).toFixed(0)}%
                </div>
              </div>

              {/* 风险预算 */}
              <div className="text-center">
                <div className="text-xs text-gray-500 mb-1">风险预算</div>
                <div className="text-lg font-bold">
                  {(marketEnv.macro.risk_budget_pct * 100).toFixed(0)}%
                </div>
                <div className="text-xs text-gray-400 mt-1">宏观约束</div>
              </div>

              {/* 流动性 */}
              <div className="text-center">
                <div className="text-xs text-gray-500 mb-1">流动性</div>
                <div className={`text-lg font-bold ${
                  marketEnv.micro.liquidity_score > 0.7 ? 'text-green-600' :
                  marketEnv.micro.liquidity_score > 0.3 ? 'text-yellow-600' : 'text-red-600'
                }`}>
                  {(marketEnv.micro.liquidity_score * 100).toFixed(0)}%
                </div>
              </div>
            </div>

            {/* 价格过期警告 */}
            {marketEnv.price_stale_warning && (
              <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-lg p-3 text-sm text-red-700 dark:text-red-300">
                <span className="font-medium">⚠️ 数据警告：</span>{marketEnv.price_stale_warning}
              </div>
            )}

            {/* AI 指导建议 */}
            {marketEnv.guidance && (
              <div className="bg-blue-50 dark:bg-blue-950/30 rounded-lg p-3 text-sm text-blue-800 dark:text-blue-300">
                <span className="font-medium">AI 建议：</span>{marketEnv.guidance}
              </div>
            )}

            {/* 数据溯源 */}
            <div className="flex items-center gap-4 text-[10px] text-gray-400 dark:text-gray-500 mt-3 pt-2 border-t flex-wrap">
              <span className="flex items-center gap-1">
                {marketEnv.price_source === 'realtime' ? (
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                ) : marketEnv.price_source === 'kline_fresh' ? (
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400" />
                ) : marketEnv.price_source === 'kline_stale' ? (
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-400" />
                ) : (
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
                )}
                价格: {
                  marketEnv.price_source === 'realtime' ? '交易所实时' :
                  marketEnv.price_source === 'kline_fresh' ? 'K线(较新)' :
                  marketEnv.price_source === 'kline_stale' ? 'K线(过期⚠️)' :
                  '未知来源'
                }
              </span>
              {(marketEnv.kline_count ?? 0) > 0 && (
                <span>基于 {marketEnv.kline_count} 条K线</span>
              )}
              {(marketEnv.current_price ?? 0) > 0 && (
                <span className={marketEnv.price_source === 'kline_stale' ? 'text-red-500 line-through' : 'font-medium'}>
                  {marketEnv.symbol || 'BTC'} ${marketEnv.current_price?.toLocaleString()}
                </span>
              )}
              {(marketEnv.kline_age_hours ?? 0) > 0 && (
                <span className={(marketEnv.kline_age_hours ?? 0) > 24 ? 'text-red-400' : ''}>
                  K线 {(marketEnv.kline_age_hours ?? 0) < 1
                    ? `${Math.round((marketEnv.kline_age_hours ?? 0) * 60)}分钟前`
                    : `${Math.round(marketEnv.kline_age_hours ?? 0)}小时前`
                  }
                </span>
              )}
              {marketEnv.analysis_time && (
                <span className="ml-auto">更新: {marketEnv.analysis_time}</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ===== V2 新增：动态止盈止损面板 ===== */}
      {dynamicRisk && (
        <Card className="border-l-4 border-l-orange-500">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Shield className="w-5 h-5 text-orange-500" />
              动态风险管理
              <span className="text-xs font-normal text-gray-500 ml-2">
                (基于ATR + 市场环境实时计算)
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {/* 止损 */}
              <div className="bg-red-50 dark:bg-red-950/20 rounded-lg p-3">
                <div className="text-xs text-red-600 dark:text-red-400 mb-1 font-medium">动态止损</div>
                <div className="text-2xl font-bold text-red-700 dark:text-red-400">
                  {(dynamicRisk.stop_loss.pct * 100).toFixed(2)}%
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  类型: {dynamicRisk.stop_loss.type === 'atr_based' ? 'ATR' : '固定'}
                  {dynamicRisk.stop_loss.type === 'atr_based' && ` ×${dynamicRisk.stop_loss.atr_multiple}`}
                </div>
              </div>

              {/* 分批止盈 */}
              <div className="bg-green-50 dark:bg-green-950/20 rounded-lg p-3">
                <div className="text-xs text-green-600 dark:text-green-400 mb-1 font-medium">分批止盈</div>
                <div className="space-y-1">
                  {dynamicRisk.take_profit_levels.map((level, i) => (
                    <div key={i} className="flex justify-between text-sm">
                      <span className="text-gray-600 dark:text-gray-400">TP{i + 1}</span>
                      <span className="font-medium text-green-700 dark:text-green-400">
                        +{(level.pct * 100).toFixed(1)}% → 平{(level.close_ratio * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 移动止损 */}
              <div className="bg-purple-50 dark:bg-purple-950/20 rounded-lg p-3">
                <div className="text-xs text-purple-600 dark:text-purple-400 mb-1 font-medium">移动止损</div>
                <div className="text-lg font-bold text-purple-700 dark:text-purple-400">
                  {dynamicRisk.trailing_stop.enabled ? '已激活' : '未激活'}
                </div>
                {dynamicRisk.trailing_stop.enabled && (
                  <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                    <div>盈利 {(dynamicRisk.trailing_stop.activation_pct * 100).toFixed(1)}% 后启动</div>
                    <div>追踪距离 {(dynamicRisk.trailing_stop.distance_pct * 100).toFixed(2)}%</div>
                  </div>
                )}
              </div>

              {/* 仓位建议 */}
              <div className="bg-blue-50 dark:bg-blue-950/20 rounded-lg p-3">
                <div className="text-xs text-blue-600 dark:text-blue-400 mb-1 font-medium">建议仓位</div>
                <div className="text-2xl font-bold text-blue-700 dark:text-blue-400">
                  {(dynamicRisk.position_size_pct * 100).toFixed(1)}%
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {dynamicRisk.time_stop.enabled && `超时: ${dynamicRisk.time_stop.hours}h`}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 性能指标 - 概览 Tab */}
      {memory && memory.total_trades > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="w-5 h-5" />
              策略表现
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* 基础指标 */}
            <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
              <div>
                <div className="text-sm text-gray-500">总交易</div>
                <div className="text-2xl font-bold">{memory.total_trades}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500">胜率</div>
                <div className={`text-2xl font-bold ${memory.win_rate >= 0.5 ? 'text-green-600' : 'text-red-600'}`}>
                  {(memory.win_rate * 100).toFixed(1)}%
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-500">平均盈利</div>
                <div className="text-2xl font-bold text-green-600">
                  +{(memory.avg_profit * 100).toFixed(2)}%
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-500">平均亏损</div>
                <div className="text-2xl font-bold text-red-600">
                  {(memory.avg_loss * 100).toFixed(2)}%
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-500">夏普比率</div>
                <div className={`text-2xl font-bold ${memory.sharpe_ratio >= 1 ? 'text-green-600' : ''}`}>
                  {memory.sharpe_ratio.toFixed(2)}
                </div>
              </div>
              <div>
                <div className="text-sm text-gray-500">最大回撤</div>
                <div className="text-2xl font-bold text-red-600">
                  {(memory.max_drawdown * 100).toFixed(2)}%
                </div>
              </div>
            </div>

            {/* V2 新增：按市场状态分类表现 */}
            {memory.performance_by_regime && Object.keys(memory.performance_by_regime).length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-1">
                  <Brain className="w-4 h-4" /> 按市场状态分类表现
                </h4>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {Object.entries(memory.performance_by_regime).map(([regime, perf]) => {
                    const wr = perf.trades > 0 ? perf.wins / perf.trades : 0;
                    return (
                      <div key={regime} className="border rounded-lg p-3 dark:border-gray-700">
                        <div className="text-xs font-medium text-gray-500 mb-1">{regime}</div>
                        <div className="flex justify-between items-center">
                          <span className="text-sm">{perf.trades} 笔</span>
                          <span className={`text-sm font-medium ${wr >= 0.5 ? 'text-green-600' : 'text-red-600'}`}>
                            WR {(wr * 100).toFixed(0)}%
                          </span>
                        </div>
                        <div className={`text-xs mt-1 ${perf.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          PnL: {perf.total_pnl >= 0 ? '+' : ''}{perf.total_pnl.toFixed(2)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* V2 新增：关键教训 */}
            {memory.key_lessons && memory.key_lessons.length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3 flex items-center gap-1">
                  <AlertTriangle className="w-4 h-4 text-yellow-500" /> 最近亏损教训
                </h4>
                <div className="space-y-2">
                  {memory.key_lessons.slice(0, 3).map((lesson: any, i: number) => (
                    <div key={i} className="flex items-center gap-3 text-sm bg-yellow-50 dark:bg-yellow-950/20 rounded p-2">
                      <span className="text-gray-600 dark:text-gray-400 font-mono">{lesson.symbol}</span>
                      <Badge variant="outline" className="text-xs">
                        {lesson.side === 'buy' ? '做多' : '做空'}
                      </Badge>
                      <span className="text-red-600">{(lesson.pnl_pct * 100).toFixed(2)}%</span>
                      <span className="text-gray-400">|</span>
                      <span className="text-gray-500">{lesson.market_regime}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
        </div>
      )}

      {/* ===== 自主监控 Tab ===== */}
      {activeTab === 'monitor' && strategy.status === 'active' && (
        <StrategyMonitorPanel strategyId={strategy.strategy_id} />
      )}

      {/* ===== 持仓追踪 Tab ===== */}
      {activeTab === 'positions' && strategy.status === 'active' && (
        <PositionTrackerPanel strategyId={strategy.strategy_id} />
      )}

      {/* ===== 回测优化 Tab ===== */}
      {activeTab === 'backtest' && (
        <BacktestOptimizePanel strategyId={strategy.strategy_id} />
      )}

      {/* ===== 自学习 Tab ===== */}
      {activeTab === 'learning' && (
        <LearningDashboard strategyId={strategy.strategy_id} />
      )}

      {/* ===== 配置 Tab ===== */}
      {activeTab === 'config' && (
        <div className="space-y-6">
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>基本配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-500">策略ID:</span>
              <span className="font-mono text-sm">{strategy.strategy_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">账户ID:</span>
              <span>{strategy.account_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">提示词模板:</span>
              <span>{strategy.master_prompt_template_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">提示词版本:</span>
              <span>{strategy.prompt_version}</span>
            </div>
          </CardContent>
        </Card>

        {/* 触发配置 */}
        <Card>
          <CardHeader>
            <CardTitle>触发配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-500">触发模式:</span>
              <span>{strategy.trigger_mode}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">信号池:</span>
              <span>{strategy.signal_pool_ids?.length || 0} 个</span>
            </div>
            {strategy.trigger_interval && (
              <div className="flex justify-between">
                <span className="text-gray-500">触发间隔:</span>
                <span>{strategy.trigger_interval}秒</span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 风险配置（显示静态配置 + 动态覆盖提示） */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              风险配置
              {dynamicRisk && (
                <Badge variant="outline" className="text-xs font-normal text-blue-600">
                  动态覆盖中
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-500">基础仓位上限:</span>
              <span>{(strategy.max_position_size * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">基础止损:</span>
              <span className="text-red-600">{(strategy.stop_loss_pct * 100).toFixed(1)}% <span className="text-xs text-gray-400">(档案值)</span></span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">基础止盈:</span>
              <span className="text-green-600">{(strategy.take_profit_pct * 100).toFixed(1)}% <span className="text-xs text-gray-400">(档案值)</span></span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">最大日损:</span>
              <span>{(strategy.max_daily_loss * 100).toFixed(0)}%</span>
            </div>
            {dynamicRisk && (
              <div className="text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30 rounded p-2 mt-2">
                实际交易时将根据ATR和市场环境动态覆盖以上参数
              </div>
            )}
          </CardContent>
        </Card>

        {/* 执行配置 */}
        <Card>
          <CardHeader>
            <CardTitle>执行 & 学习</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-500">自动执行:</span>
              <span className={strategy.auto_execute ? 'text-green-600 font-medium' : ''}>
                {strategy.auto_execute ? '已开启' : '关闭'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">需要确认:</span>
              <span>{strategy.require_confirmation ? '是' : '否'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">最小置信度:</span>
              <span>{(strategy.min_confidence * 100).toFixed(0)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">学习优化:</span>
              <span className={strategy.learning_enabled ? 'text-green-600 font-medium' : ''}>
                {strategy.learning_enabled ? '启用' : '禁用'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">优化目标:</span>
              <span>{strategy.optimization_target}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">训练频率:</span>
              <span>{strategy.training_frequency}</span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 因子权重 */}
      {strategy.enabled_factors && strategy.enabled_factors.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>因子配置</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {strategy.enabled_factors.map((factor) => (
                <div key={factor} className="flex items-center justify-between">
                  <span>{factor}</span>
                  <div className="flex items-center gap-2">
                    <div className="w-32 h-2 bg-gray-200 rounded">
                      <div
                        className="h-full bg-blue-500 rounded"
                        style={{ width: `${(strategy.factor_weights?.[factor] || 0) * 100}%` }}
                      />
                    </div>
                    <span className="text-sm text-gray-500 w-12 text-right">
                      {((strategy.factor_weights?.[factor] || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
        </div>
      )}
    </div>
  );
}
