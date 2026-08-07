/**
 * AI策略中心 - 主页面 (V2 增强版)
 * 
 * V2 新增功能：
 * - 顶部：全局市场环境仪表盘（宏观周期 + 波动率 + 趋势 + AI建议）
 * - 策略卡片增强：显示策略记忆（胜率/夏普）、动态风险参数
 * - 执行时显示 loading + toast 反馈
 * - 状态筛选 + 一键刷新市场环境
 */

import { useState, useEffect, useCallback, useRef }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Play, Pause, Edit, Trash2, Plus, RefreshCw, Loader2,
  TrendingUp, TrendingDown, Activity, AlertTriangle,
  Shield, Brain, Zap, BarChart3, Target, Eye,
  ChevronRight, ArrowRight, Crosshair, Rocket, Eraser,
  Archive, RotateCcw
} from 'lucide-react';
import AiStrategyWizard from './AiStrategyWizard';
import AiStrategyDetail from './AiStrategyDetail';
import AutoLaunchPanel from './AutoLaunchPanel';
import { toast } from 'react-hot-toast';
import { useAccountSnapshot } from '@/contexts/AccountSnapshotContext';
import { fmtDateTime } from '@/lib/utils';

interface AIStrategy {
  id: number;
  strategy_id: string;
  name: string;
  description?: string;
  status: 'draft' | 'active' | 'paused' | 'terminated' | 'archived';
  account_id: number;
  master_prompt_template_id: number;
  prompt_version: number;
  signal_pool_ids: number[];
  trigger_mode: string;
  auto_execute: boolean;
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
  min_confidence: number;
  learning_enabled: boolean;
  created_at: string;
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
  key_lessons?: any[];
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
  kline_count?: number;
  current_price?: number;
  atr_value?: number;
  analysis_time?: string;
}

const API_BASE = '/api/ai-strategies';

// 市场周期样式
const cycleConfig: Record<string, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
  bull: { label: '牛市', color: 'text-green-700 dark:text-green-400', bg: 'bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800', icon: <TrendingUp className="w-5 h-5 text-green-500" /> },
  bear: { label: '熊市', color: 'text-red-700 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800', icon: <TrendingDown className="w-5 h-5 text-red-500" /> },
  sideways: { label: '震荡', color: 'text-yellow-700 dark:text-yellow-400', bg: 'bg-yellow-50 dark:bg-yellow-950/30 border-yellow-200 dark:border-yellow-800', icon: <Activity className="w-5 h-5 text-yellow-500" /> },
  unknown: { label: '数据不足', color: 'text-gray-600 dark:text-gray-400', bg: 'bg-gray-50 dark:bg-gray-900 border-gray-200 dark:border-gray-700', icon: <AlertTriangle className="w-5 h-5 text-gray-400" /> },
  transition: { label: '转换期', color: 'text-purple-700 dark:text-purple-400', bg: 'bg-purple-50 dark:bg-purple-950/30 border-purple-200 dark:border-purple-800', icon: <Activity className="w-5 h-5 text-purple-500" /> },
};

const volConfig: Record<string, { label: string; color: string }> = {
  low: { label: '低波动', color: 'text-blue-600 dark:text-blue-400' },
  normal: { label: '正常波动', color: 'text-gray-600 dark:text-gray-400' },
  high: { label: '高波动', color: 'text-orange-600 dark:text-orange-400' },
  extreme: { label: '极端波动', color: 'text-red-600 dark:text-red-400' },
};

interface AiStrategyListProps {
  onSwitchTab?: (tab: string) => void;
  /** 从全自动面板跳转时传入的策略ID，自动打开详情 */
  openStrategyId?: string | null;
  /** 用户关闭详情时回调 */
  onStrategyClosed?: () => void;
}

export default function AiStrategyList({ onSwitchTab, openStrategyId, onStrategyClosed }: AiStrategyListProps = {}) {
  const [strategies, setStrategies] = useState<AIStrategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<{ status?: string }>({});
  const [showWizard, setShowWizard] = useState(false);
  const [showAutoLaunch, setShowAutoLaunch] = useState(false);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(null);
  
  // V2 新增状态
  const [marketEnv, setMarketEnv] = useState<MarketEnvironment | null>(null);
  const [loadingEnv, setLoadingEnv] = useState(false);
  const [memoryMap, setMemoryMap] = useState<Record<string, StrategyMemory>>({});
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [autonomousStatus, setAutonomousStatus] = useState<Record<string, any>>({});
  const [multiTF, setMultiTF] = useState<Record<string, any> | null>(null);
  const [loadingMTF, setLoadingMTF] = useState(false);
  
  // Paper 模式余额
  const [paperBalance, setPaperBalance] = useState<any>(null);
  const [paperAccounts, setPaperAccounts] = useState<any[]>([]);
  const [cleaning, setCleaning] = useState(false);

  // 真实账户数据：复用总览仪表板的 snapshot Context
  const snapshotCtx = useAccountSnapshot();

  const initialLoadDone = useRef(false);

  const loadStrategies = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const params = new URLSearchParams();
      if (filter.status) params.append('status', filter.status);

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);

      const response = await fetch(`${API_BASE}?${params.toString()}`, {
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      setStrategies(data);
      
      if (data.length > 0 && !silent) {
        loadAllMemories(data);
        loadAutonomousStatus(data);
      }
    } catch (error) {
      console.error('Load strategies error:', error);
      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          toast.error('请求超时，请检查后端服务是否启动');
        } else {
          toast.error(`加载失败: ${error.message}`);
        }
      }
      setStrategies([]);
    } finally {
      setLoading(false);
    }
  };

  // V2: 批量加载策略记忆
  const loadAllMemories = async (strats: AIStrategy[]) => {
    const newMap: Record<string, StrategyMemory> = {};
    await Promise.allSettled(
      strats.map(async (s) => {
        try {
          const res = await fetch(`${API_BASE}/${s.strategy_id}/memory`);
          if (res.ok) {
            const data = await res.json();
            if (data.total_trades > 0) {
              newMap[s.strategy_id] = data;
            }
          }
        } catch { /* ignore */ }
      })
    );
    setMemoryMap(newMap);
  };

  const loadAutonomousStatus = async (strats: AIStrategy[]) => {
    const activeStrats = strats.filter(s => s.status === 'active');
    if (activeStrats.length === 0) return;
    const newMap: Record<string, any> = {};
    await Promise.allSettled(
      activeStrats.map(async (s) => {
        try {
          const res = await fetch(`${API_BASE}/autonomous/${s.strategy_id}/status`);
          if (res.ok) {
            newMap[s.strategy_id] = await res.json();
          }
        } catch { /* ignore */ }
      })
    );
    setAutonomousStatus(newMap);
  };

  const loadMultiTimeframe = useCallback(async () => {
    setLoadingMTF(true);
    try {
      const res = await fetch(`${API_BASE}/global/multi-timeframe?symbol=BTC`, { signal: AbortSignal.timeout(20000) });
      if (res.ok) {
        const data = await res.json();
        setMultiTF(data.timeframes || null);
      }
    } catch (e) {
      console.error('Load multi-timeframe error:', e);
    } finally {
      setLoadingMTF(false);
    }
  }, []);

  const strategiesRef = useRef(strategies);
  strategiesRef.current = strategies;

  const loadMarketEnv = useCallback(async () => {
    setLoadingEnv(true);
    try {
      const strats = strategiesRef.current;
      const activeStrategy = strats.find(s => s.status === 'active') || strats[0];
      
      let url: string;
      if (activeStrategy) {
        const sym = activeStrategy.primary_symbol || 'BTC';
        url = `${API_BASE}/${activeStrategy.strategy_id}/market-environment?symbol=${sym}`;
      } else {
        url = `${API_BASE}/global/market-environment?symbol=BTC`;
      }
      
      const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
      if (res.ok) {
        setMarketEnv(await res.json());
      }
    } catch (error) {
      console.error('Load market env error:', error);
    } finally {
      setLoadingEnv(false);
    }
  }, []);

  useEffect(() => {
    loadStrategies();
  }, [filter]);

  const pollCount = useRef(0);
  useEffect(() => {
    pollCount.current = 0;
    const timer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      pollCount.current += 1;
      // Every 4th poll (~60s) do a full refresh with memory/autonomous
      if (pollCount.current % 4 === 0) {
        loadStrategies(false);
      } else {
        loadStrategies(true);
      }
    }, 15000);
    return () => clearInterval(timer);
  }, [filter]);

  // 从全自动面板跳转：打开指定策略详情
  useEffect(() => {
    if (openStrategyId) {
      setSelectedStrategyId(openStrategyId);
    }
  }, [openStrategyId]);

  useEffect(() => {
    if (strategies.length > 0 && !initialLoadDone.current) {
      initialLoadDone.current = true;
      if (!marketEnv && !loadingEnv) loadMarketEnv();
      if (!multiTF && !loadingMTF) loadMultiTimeframe();
      (async () => {
        try {
          const accRes = await fetch('/api/account/list', { signal: AbortSignal.timeout(3000) });
          if (!accRes.ok) return;
          const raw = await accRes.json();
          const allAccs = Array.isArray(raw) ? raw : (raw?.accounts || []);
          const papers = allAccs.filter((a: any) => a.trading_mode === 'paper');
          setPaperAccounts(papers);
          if (papers.length > 0) {
            const balRes = await fetch(`/api/paper/balance/${papers[0].id}`, { signal: AbortSignal.timeout(3000) });
            if (balRes.ok) setPaperBalance(await balRes.json());
          }
        } catch {}
      })();
    }
  }, [strategies.length]);

  // 激活策略
  const handleActivate = async (strategyId: string) => {
    try {
      const response = await fetch(`${API_BASE}/${strategyId}/activate`, { method: 'POST' });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已激活');
      loadStrategies();
    } catch { toast.error('激活失败'); }
  };

  // 暂停策略
  const handlePause = async (strategyId: string) => {
    try {
      const response = await fetch(`${API_BASE}/${strategyId}/pause`, { method: 'POST' });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已暂停');
      loadStrategies();
    } catch { toast.error('暂停失败'); }
  };

  // V2: 手动执行（带 loading 状态和 toast）
  const handleExecute = async (strategyId: string) => {
    setExecutingId(strategyId);
    const loadingToast = toast.loading('AI 正在分析市场环境并执行策略决策...');
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);

      const response = await fetch(`${API_BASE}/${strategyId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger_reason: '手动触发', force: false }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      const result = await response.json();
      if (result.success) {
        toast.success(
          `执行成功！产生 ${result.decisions?.length || 0} 个决策`,
          { id: loadingToast }
        );
        // 刷新记忆数据
        loadAllMemories(strategies);
      } else {
        toast.error(`执行失败: ${result.error_message || '未知错误'}`, { id: loadingToast });
      }
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        toast.error('执行超时（2分钟），请稍后查看结果', { id: loadingToast });
      } else {
        toast.error('执行失败', { id: loadingToast });
      }
    } finally {
      setExecutingId(null);
    }
  };

  // 删除策略
  const handleDelete = async (strategyId: string) => {
    if (!confirm('确定要删除这个策略吗？此操作不可恢复。')) return;
    try {
      const response = await fetch(`${API_BASE}/${strategyId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已删除');
      loadStrategies();
    } catch { toast.error('删除失败'); }
  };

  // 归档策略
  const handleArchive = async (strategyId: string) => {
    try {
      const response = await fetch(`${API_BASE}/${strategyId}/archive?reason=manual`, { method: 'POST' });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已归档');
      loadStrategies();
    } catch { toast.error('归档失败'); }
  };

  // 恢复策略（归档/终止 → 激活）
  const handleResume = async (strategyId: string) => {
    try {
      const response = await fetch(`${API_BASE}/${strategyId}/resume`, { method: 'POST' });
      if (!response.ok) throw new Error('Failed');
      toast.success('策略已恢复激活');
      loadStrategies();
    } catch { toast.error('恢复失败'); }
  };

  // 批量清理已停止会话的残留策略
  const handleCleanupStale = async () => {
    const pausedCount = strategies.filter(s => s.status === 'paused').length;
    if (pausedCount === 0) {
      toast('没有需要清理的策略', { icon: 'ℹ️' });
      return;
    }
    if (!confirm(`检测到 ${pausedCount} 个已暂停的策略，确定要清理所有已停止会话的残留策略吗？\n\n此操作将删除所有不再关联到运行中会话的策略。`)) return;
    setCleaning(true);
    try {
      const response = await fetch('/api/full-auto/cleanup-stale-strategies', { method: 'POST' });
      if (!response.ok) throw new Error('Failed');
      const result = await response.json();
      toast.success(`清理完成：删除了 ${result.deleted_strategies} 个残留策略`);
      loadStrategies();
    } catch {
      toast.error('清理失败');
    } finally {
      setCleaning(false);
    }
  };

  // 状态徽章
  const getStatusBadge = (status: string) => {
    const map: Record<string, { label: string; className: string }> = {
      draft: { label: '草稿', className: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300' },
      active: { label: '运行中', className: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400' },
      paused: { label: '已暂停', className: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400' },
      terminated: { label: '已终止', className: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400' },
      archived: { label: '已归档', className: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400' },
    };
    const config = map[status] || map.draft;
    return <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${config.className}`}>{config.label}</span>;
  };

  // ===== 子视图切换 =====
  if (showWizard) {
    return (
      <div className="p-6">
        <Button 
          variant="outline" 
          onClick={() => { setShowWizard(false); loadStrategies(); }}
          className="mb-4"
        >
          ← 返回策略列表
        </Button>
        <AiStrategyWizard />
      </div>
    );
  }

  if (selectedStrategyId) {
    return (
      <div className="p-6">
        <Button 
          variant="outline" 
          onClick={() => {
            setSelectedStrategyId(null);
            onStrategyClosed?.();
            loadStrategies();
          }}
          className="mb-4"
        >
          ← 返回策略列表
        </Button>
        <AiStrategyDetail strategyId={selectedStrategyId} />
      </div>
    );
  }

  // ===== 加载中：骨架屏 =====
  if (loading && strategies.length === 0) {
    return (
      <div className="p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <div className="h-7 w-48 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
            <div className="h-4 w-72 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
          </div>
          <div className="flex gap-2">
            <div className="h-8 w-20 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
            <div className="h-8 w-24 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
          </div>
        </div>
        <div className="h-32 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="border rounded-lg p-4 space-y-3">
              <div className="h-5 w-32 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
              <div className="h-3 w-48 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
              <div className="grid grid-cols-4 gap-2">
                {[1,2,3,4].map(j => <div key={j} className="h-10 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />)}
              </div>
              <div className="h-8 bg-gray-100 dark:bg-gray-800 rounded animate-pulse" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ===== 主界面 =====
  const cycle = cycleConfig[marketEnv?.macro?.market_cycle || 'unknown'] || cycleConfig.unknown;
  const vol = volConfig[marketEnv?.micro?.volatility_regime || 'normal'] || volConfig.normal;
  const activeCount = strategies.filter(s => s.status === 'active').length;
  const totalTrades = Object.values(memoryMap).reduce((sum, m) => sum + m.total_trades, 0);
  const avgWinRate = Object.values(memoryMap).length > 0
    ? Object.values(memoryMap).reduce((sum, m) => sum + m.win_rate, 0) / Object.values(memoryMap).length
    : 0;

  return (
    <div className="p-6 space-y-6">
      {/* ===== 顶部区域：标题 + 操作 ===== */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Brain className="w-7 h-7 text-blue-600" />
            AI 策略中心
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            AI 驱动的量化交易策略管理 · 动态风险控制 · 策略学习与优化
          </p>
        </div>
        <div className="flex gap-2">
          {strategies.filter(s => s.status === 'paused').length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleCleanupStale}
              disabled={cleaning}
              className="text-red-600 border-red-200 hover:bg-red-50 dark:text-red-400 dark:border-red-800 dark:hover:bg-red-950/30"
            >
              {cleaning ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Eraser className="w-4 h-4 mr-1" />}
              清理残留策略 ({strategies.filter(s => s.status === 'paused').length})
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => { loadStrategies(); loadMarketEnv(); loadMultiTimeframe(); }}>
            <RefreshCw className="w-4 h-4 mr-1" />
            刷新
          </Button>
          <Button variant="outline" size="sm" onClick={() => setShowWizard(true)}>
            <Plus className="w-4 h-4 mr-1" />
            手动创建
          </Button>
          <Button
            className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700"
            onClick={() => setShowAutoLaunch(!showAutoLaunch)}
          >
            <Rocket className="w-4 h-4 mr-1.5" />
            一键启动AI交易
          </Button>
        </div>
      </div>

      {/* ===== 一键启动面板 ===== */}
      {showAutoLaunch && (
        <AutoLaunchPanel
          onLaunched={(sid) => {
            loadStrategies();
            setTimeout(() => setSelectedStrategyId(sid), 1500);
          }}
          onClose={() => setShowAutoLaunch(false)}
          onSwitchTab={onSwitchTab}
        />
      )}

      {/* ===== 资金概览：模拟 + 真实账户 ===== */}
      <div className="flex flex-wrap gap-4">
      {/* 模拟账户 */}
      {paperBalance && (
        <Card className="flex-1 min-w-[320px] border-l-4 border-l-amber-500 bg-gradient-to-r from-amber-50/50 to-orange-50/50 dark:from-amber-950/20 dark:to-orange-950/20">
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center">
                    <Shield className="w-4 h-4 text-white" />
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-bold">模拟账户</span>
                      <Badge variant="outline" className="text-[9px] bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/40 dark:text-amber-300 dark:border-amber-700">
                        PAPER
                      </Badge>
                    </div>
                    <span className="text-[10px] text-muted-foreground">虚拟资金 · 零风险</span>
                  </div>
                </div>

                <div className="h-8 w-px bg-border" />

                <div className="flex gap-6 text-sm">
                  <div>
                    <span className="text-[10px] text-muted-foreground block">总权益</span>
                    <span className="font-bold text-blue-700 dark:text-blue-400">
                      ${paperBalance.total_equity?.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-muted-foreground block">可用余额</span>
                    <span className="font-bold text-green-700 dark:text-green-400">
                      ${paperBalance.available_balance?.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-muted-foreground block">冻结保证金</span>
                    <span className="font-bold">
                      ${paperBalance.frozen_margin?.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-muted-foreground block">未实现盈亏</span>
                    <span className={`font-bold ${(paperBalance.unrealized_pnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {(paperBalance.unrealized_pnl ?? 0) >= 0 ? '+' : '-'}${Math.abs(paperBalance.unrealized_pnl ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-muted-foreground block">已实现盈亏</span>
                    <span className={`font-bold ${(paperBalance.realized_pnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {(paperBalance.realized_pnl ?? 0) >= 0 ? '+' : '-'}${Math.abs(paperBalance.realized_pnl ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-muted-foreground block">收益率</span>
                    <span className={`font-bold ${(paperBalance.return_pct ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {(paperBalance.return_pct ?? 0) >= 0 ? '+' : ''}{(paperBalance.return_pct ?? 0).toFixed(2)}%
                    </span>
                  </div>
                </div>
              </div>

              {onSwitchTab && (
                <Button
                  variant="outline"
                  size="sm"
                  className="border-amber-300 text-amber-700 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-950"
                  onClick={() => onSwitchTab('paper-trading')}
                >
                  查看详情
                  <ChevronRight className="w-3.5 h-3.5 ml-1" />
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 真实账户：复用总览仪表板 snapshot 数据 */}
      {snapshotCtx?.isRealAccount && snapshotCtx?.snapshot?.portfolio && (
        <Card className="flex-1 min-w-[320px] border-l-4 border-l-emerald-500 bg-gradient-to-r from-emerald-50/50 to-teal-50/50 dark:from-emerald-950/20 dark:to-teal-950/20">
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center">
                    <Zap className="w-4 h-4 text-white" />
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-bold">真实账户</span>
                      <Badge variant="outline" className="text-[9px] bg-emerald-100 text-emerald-700 border-emerald-300 dark:bg-emerald-900/40 dark:text-emerald-300 dark:border-emerald-700">
                        LIVE
                      </Badge>
                    </div>
                    <span className="text-[10px] text-muted-foreground">交易所实盘资金</span>
                  </div>
                </div>

                <div className="h-8 w-px bg-border" />

                <div className="flex flex-wrap gap-6 text-sm">
                  {(() => {
                    const p = snapshotCtx.snapshot!.portfolio as { total_value?: number; capital?: number };
                    const total = Number(p?.total_value ?? p?.capital ?? 0);
                    const capital = Number(p?.capital ?? 0);
                    return (
                      <div key={snapshotCtx.accountId ?? 0} className="flex flex-col gap-1">
                        <span className="text-[10px] text-muted-foreground">当前账户</span>
                        <div className="flex items-baseline gap-2">
                          <span className="font-bold text-emerald-700 dark:text-emerald-400">
                            ${total.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                          </span>
                          <span className="text-[10px] text-muted-foreground">
                            可用 ${capital.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                          </span>
                        </div>
                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
      </div>

      {/* ===== 多周期趋势仪表盘 ===== */}
      <Card className="border-l-4 border-l-blue-500 overflow-hidden">
        <CardContent className="p-0">
          {/* 双周期趋势行 —— 中周期已合并到长线 */}
          <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x dark:divide-gray-700">
            {([
              { key: 'short', label: '短线', sub: '15m · 7天', icon: <Zap className="w-4 h-4" /> },
              { key: 'long',  label: '长线', sub: '1d · 365天', icon: <BarChart3 className="w-4 h-4" /> },
            ] as const).map(tf => {
              const d = multiTF?.[tf.key];
              const trend = d?.trend_direction || 'neutral';
              const strength = d?.trend_strength || 0;
              const vol = d?.volatility_regime || 'normal';
              const signal = d?.signal_strength || 0;
              const trendColor = trend === 'bullish' ? 'text-green-600 dark:text-green-400' : trend === 'bearish' ? 'text-red-600 dark:text-red-400' : 'text-gray-500';
              const trendIcon = trend === 'bullish' ? '↗' : trend === 'bearish' ? '↘' : '→';
              const trendText = trend === 'bullish' ? '看多' : trend === 'bearish' ? '看空' : '中性';
              const volLabel = vol === 'low' ? '低' : vol === 'high' ? '高' : vol === 'extreme' ? '极端' : '正常';
              const volColor = vol === 'low' ? 'text-blue-500' : vol === 'high' ? 'text-orange-500' : vol === 'extreme' ? 'text-red-500' : 'text-gray-500';

              const signalPct = Math.abs(signal) * 100;
              const signalBarColor = signal > 0 ? 'bg-green-500' : signal < 0 ? 'bg-red-500' : 'bg-gray-300';

              return (
                <div key={tf.key} className="p-4 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground">{tf.icon}</span>
                      <span className="text-sm font-bold">{tf.label}</span>
                      <span className="text-[10px] text-muted-foreground">{tf.sub}</span>
                    </div>
                    {loadingMTF && !multiTF && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
                  </div>

                  {d ? (
                    <>
                      <div className="flex items-baseline gap-2">
                        <span className={`text-2xl font-black ${trendColor}`}>{trendIcon} {trendText}</span>
                        <span className="text-xs text-muted-foreground">强度 {(strength * 100).toFixed(0)}%</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-muted-foreground">波动率</span>
                        <span className={`font-semibold ${volColor}`}>{volLabel}</span>
                        <span className="text-muted-foreground ml-auto">信号</span>
                        <div className="w-16 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${signalBarColor}`} style={{ width: `${Math.max(signalPct, 5)}%` }} />
                        </div>
                        <span className={`font-mono text-[10px] ${signal > 0 ? 'text-green-600' : signal < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                          {signal > 0 ? '+' : ''}{signal.toFixed(2)}
                        </span>
                      </div>
                      {tf.key === 'long' && d.market_cycle && d.market_cycle !== 'unknown' && (
                        <div className={`text-xs font-medium px-2 py-0.5 rounded inline-block ${
                          d.market_cycle === 'bull' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' :
                          d.market_cycle === 'bear' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' :
                          d.market_cycle === 'sideways' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' :
                          'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400'
                        }`}>
                          宏观周期: {d.market_cycle === 'bull' ? '牛市' : d.market_cycle === 'bear' ? '熊市' : d.market_cycle === 'sideways' ? '震荡' : '转换期'}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                      {loadingMTF ? <><Loader2 className="w-3 h-3 animate-spin" /> 分析中...</> : '暂无数据'}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* 底部：策略总览 + 实时价格 */}
          <div className="px-4 py-2.5 border-t dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 flex items-center justify-between text-xs">
            <div className="flex items-center gap-4">
              <span className="text-muted-foreground">活跃策略</span>
              <span className="font-bold text-green-600">{activeCount}<span className="font-normal text-muted-foreground">/{strategies.length}</span></span>
              <span className="text-muted-foreground">总交易</span>
              <span className="font-bold">{totalTrades}</span>
              <span className="text-muted-foreground">平均胜率</span>
              <span className={`font-bold ${avgWinRate >= 0.5 ? 'text-green-600' : avgWinRate > 0 ? 'text-yellow-600' : 'text-muted-foreground'}`}>
                {avgWinRate > 0 ? `${(avgWinRate * 100).toFixed(1)}%` : '--'}
              </span>
            </div>
            <div className="flex items-center gap-3 text-muted-foreground">
              {multiTF?.short?.current_price > 0 && (
                <span className="flex items-center gap-1.5">
                  {multiTF.short.price_source === 'realtime' ? (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" title="实时价格" />
                  ) : multiTF.short.price_source === 'kline_stale' ? (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-400" title="价格可能过期" />
                  ) : (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
                  )}
                  BTC <span className={`font-mono font-bold ${
                    multiTF.short.price_source === 'kline_stale' ? 'text-red-500 line-through' : 'text-foreground'
                  }`}>${multiTF.short.current_price.toLocaleString()}</span>
                  {multiTF.short.price_source === 'kline_stale' && (
                    <span className="text-[9px] text-red-500">⚠过期</span>
                  )}
                </span>
              )}
              {multiTF?.short?.kline_count > 0 && (
                <span>{multiTF.short.kline_count}条K线</span>
              )}
            </div>
          </div>

          {/* AI 建议 */}
          {marketEnv?.guidance && (
            <div className="px-4 py-2.5 bg-blue-50/80 dark:bg-blue-950/20 border-t dark:border-gray-700 text-xs text-blue-700 dark:text-blue-400 flex items-start gap-2">
              <Brain className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span><strong>AI 建议：</strong>{marketEnv.guidance}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ===== 筛选器 ===== */}
      <div className="flex items-center gap-3">
        <select
          className="px-3 py-1.5 text-sm border rounded-lg bg-white dark:bg-gray-800 dark:border-gray-700"
          value={filter.status || ''}
          onChange={(e) => setFilter({ ...filter, status: e.target.value || undefined })}
        >
          <option value="">所有状态</option>
          <option value="draft">草稿</option>
          <option value="active">运行中</option>
          <option value="paused">已暂停</option>
          <option value="terminated">已终止</option>
          <option value="archived">已归档</option>
        </select>
        <span className="text-sm text-gray-400">
          共 {strategies.length} 个策略
        </span>
      </div>

      {/* ===== 策略卡片列表 ===== */}
      {strategies.length === 0 ? (
        <div className="space-y-4">
          {!showAutoLaunch && (
            <Card className="border-dashed">
              <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                <div className="w-16 h-16 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mb-4">
                  <Rocket className="w-8 h-8 text-white" />
                </div>
                <h3 className="text-lg font-semibold mb-2">开始 AI 自主交易</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-6 max-w-md">
                  只需选择账户和交易对，AI 自动分析市场、选择最优策略风格和周期、
                  生成策略并开始自主交易。
                </p>
                <div className="flex gap-3">
                  <Button
                    size="lg"
                    className="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700"
                    onClick={() => setShowAutoLaunch(true)}
                  >
                    <Rocket className="w-5 h-5 mr-2" />
                    一键启动
                  </Button>
                  <Button variant="outline" onClick={() => setShowWizard(true)}>
                    <Plus className="w-4 h-4 mr-1.5" />
                    手动创建
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {strategies.map((strategy) => {
            const mem = memoryMap[strategy.strategy_id];
            const isExecuting = executingId === strategy.strategy_id;
            const autoStatus = autonomousStatus[strategy.strategy_id];

            return (
              <Card 
                key={strategy.id} 
                className={`group hover:shadow-lg transition-all cursor-pointer border ${
                  strategy.status === 'active' ? 'border-green-200 dark:border-green-800/50' : ''
                }`}
                onClick={() => setSelectedStrategyId(strategy.strategy_id)}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <CardTitle className="text-base truncate flex items-center gap-2">
                        {strategy.name}
                        {strategy.auto_execute && (
                          <Zap className="w-3.5 h-3.5 text-yellow-500 flex-shrink-0" title="自动执行" />
                        )}
                        {strategy.status === 'active' && (
                          <span className="relative flex h-2 w-2" title="自主分析运行中">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                          </span>
                        )}
                      </CardTitle>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">
                        {strategy.description || '无描述'}
                      </p>
                      {/* 交易对 + 周期 + 杠杆 信息栏 */}
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {strategy.target_symbols && strategy.target_symbols.length > 0 && (
                          strategy.target_symbols.map(sym => (
                            <span
                              key={sym}
                              className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                sym === strategy.primary_symbol
                                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400'
                                  : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'
                              }`}
                            >
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
                      {/* 自主运行状态标签 */}
                      {strategy.status === 'active' && autoStatus && (
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          {autoStatus.running && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
                              <span className="relative flex h-1.5 w-1.5">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
                              </span>
                              自主分析中
                            </span>
                          )}
                          {autoStatus.tracked_positions > 0 && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-50 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400">
                              <Crosshair className="w-2.5 h-2.5" />
                              追踪 {autoStatus.tracked_positions} 仓
                            </span>
                          )}
                          {autoStatus.optimizing && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                              <Target className="w-2.5 h-2.5" />
                              回测优化中
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="flex-shrink-0 ml-2">
                      {getStatusBadge(strategy.status)}
                    </div>
                  </div>
                </CardHeader>

                <CardContent className="pt-0 space-y-3">
                  {/* V2: 策略记忆 - 核心指标 */}
                  {mem && mem.total_trades > 0 ? (
                    <div className="grid grid-cols-4 gap-2 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/50">
                      <div className="text-center">
                        <div className="text-[10px] text-gray-500">交易</div>
                        <div className="text-sm font-bold">{mem.total_trades}</div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-gray-500">胜率</div>
                        <div className={`text-sm font-bold ${mem.win_rate >= 0.5 ? 'text-green-600' : 'text-red-600'}`}>
                          {(mem.win_rate * 100).toFixed(1)}%
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-gray-500">夏普</div>
                        <div className={`text-sm font-bold ${mem.sharpe_ratio >= 1 ? 'text-green-600' : ''}`}>
                          {mem.sharpe_ratio.toFixed(2)}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-gray-500">回撤</div>
                        <div className="text-sm font-bold text-red-600">
                          {(mem.max_drawdown * 100).toFixed(1)}%
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-2 text-xs text-gray-400 bg-gray-50 dark:bg-gray-800/50 rounded-lg">
                      暂无交易记录
                    </div>
                  )}

                  {/* 策略基础信息 */}
                  <div className="text-xs space-y-1.5 text-gray-500 dark:text-gray-400">
                    <div className="flex justify-between">
                      <span>触发模式</span>
                      <span className="font-medium text-gray-700 dark:text-gray-300">{strategy.trigger_mode}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>风控</span>
                      <span className="font-medium text-gray-700 dark:text-gray-300">
                        SL {(strategy.stop_loss_pct * 100).toFixed(0)}% / TP {(strategy.take_profit_pct * 100).toFixed(0)}%
                        {marketEnv && (
                          <span className="text-blue-500 ml-1" title="动态覆盖中">⚡</span>
                        )}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>置信度</span>
                      <span className="font-medium text-gray-700 dark:text-gray-300">
                        ≥{(strategy.min_confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                    {strategy.last_executed_at && (
                      <div className="flex justify-between">
                        <span>最后执行</span>
                        <span className="font-medium text-gray-700 dark:text-gray-300 text-[11px]">
                          {fmtDateTime(strategy.last_executed_at)}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* 操作按钮 */}
                  <div className="flex gap-1.5 pt-2 border-t dark:border-gray-700" onClick={(e) => e.stopPropagation()}>
                    {strategy.status === 'archived' || strategy.status === 'terminated' ? (
                      <Button
                        size="sm"
                        variant="default"
                        onClick={() => handleResume(strategy.strategy_id)}
                        className="flex-1 h-8 text-xs"
                      >
                        <RotateCcw className="w-3 h-3 mr-1" />
                        恢复激活
                      </Button>
                    ) : strategy.status === 'draft' || strategy.status === 'paused' ? (
                      <Button
                        size="sm"
                        variant="default"
                        onClick={() => handleActivate(strategy.strategy_id)}
                        className="flex-1 h-8 text-xs"
                      >
                        <Play className="w-3 h-3 mr-1" />
                        激活
                      </Button>
                    ) : (
                      <>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handlePause(strategy.strategy_id)}
                          className="flex-1 h-8 text-xs"
                        >
                          <Pause className="w-3 h-3 mr-1" />
                          暂停
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleArchive(strategy.strategy_id)}
                          className="flex-1 h-8 text-xs"
                        >
                          <Archive className="w-3 h-3 mr-1" />
                          归档
                        </Button>
                      </>
                    )}

                    {strategy.status === 'active' && (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleExecute(strategy.strategy_id)}
                        disabled={isExecuting}
                        className="flex-1 h-8 text-xs"
                      >
                        {isExecuting ? (
                          <><Loader2 className="w-3 h-3 mr-1 animate-spin" />执行中</>
                        ) : (
                          <><Zap className="w-3 h-3 mr-1" />执行</>
                        )}
                      </Button>
                    )}

                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setSelectedStrategyId(strategy.strategy_id)}
                      className="h-8 px-2"
                      title="查看详情"
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </Button>

                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(strategy.strategy_id)}
                      className="h-8 px-2"
                      title="删除"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-red-500" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
