/**
 * ATAS 高级交易系统 - 统一策略控制台 v4.0
 * 
 * 一体化策略生成与执行平台：
 * - 端到端策略生成流程
 * - 层次化策略整合 (中长期规划 + 短期执行)
 * - 统一数据流管理
 * - 一体化执行监控
 * - 流程化操作体验
 */

'use client'

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Input } from '@/components/ui/input'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'
import { DecisionLog } from './DecisionLog'
import { RuntimeGovernorPanel } from './RuntimeGovernorPanel'
import {
  Activity,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Target,
  BarChart3,
  Zap,
  Brain,
  Signal,
  Shield,
  Minus,
  Layers,
  GitBranch,
  Play,
  Pause,
  Check,
  ChevronRight,
  ChevronLeft,
  Settings,
  PlusCircle,
  Sparkles,
  Gauge,
  Clock,
} from 'lucide-react'

const API_BASE = '/api/atas'

// ==================== 类型定义 ====================

interface StrategyConfig {
  name: string
  description: string
  symbols: string[]
  horizon: string
  risk_profile: string
  max_position_pct: number
  max_total_exposure: number
  max_daily_loss_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  enabled_signal_pools: number[]
  min_signal_strength: number
  factor_weights: Record<string, number>
  auto_execute: boolean
  require_confirmation: boolean
  max_leverage: number
}

interface StrategyPlan {
  market_cycle: string
  cycle_confidence: number
  position_bias: string
  key_support: number
  key_resistance: number
  tactical_action: string
  tactical_confidence: number
  entry_timing: string
  suggested_entry: number
  suggested_stop_loss: number
  suggested_take_profit: number
  signal_consensus: string
  signal_strength: number
  factor_score: number
  risk_score: number
  volatility_level: string
  recommended_actions: any[]
  active_signals: any[]
  key_factors: Record<string, number>
}

interface StrategyExecution {
  is_active: boolean
  trades_today: number
  pnl_today: number
  pnl_total: number
  win_rate: number
}

interface Strategy {
  strategy_id: string
  config: StrategyConfig
  plan: StrategyPlan
  execution: StrategyExecution
  phase: string
  created_at: string
  updated_at: string
  activated_at?: string
  account_id?: number
  environment: string
}

// ==================== 辅助组件 ====================

const PhaseIndicator = ({ phase }: { phase: string }) => {
  const getConfig = () => {
    switch (phase) {
      case 'draft': return { color: 'bg-gray-500', label: '草稿' }
      case 'analyzing': return { color: 'bg-blue-500 animate-pulse', label: '分析中' }
      case 'ready': return { color: 'bg-green-500', label: '就绪' }
      case 'active': return { color: 'bg-emerald-500 animate-pulse', label: '执行中' }
      case 'paused': return { color: 'bg-yellow-500', label: '已暂停' }
      case 'completed': return { color: 'bg-purple-500', label: '已完成' }
      case 'cancelled': return { color: 'bg-red-500', label: '已取消' }
      default: return { color: 'bg-gray-500', label: '未知' }
    }
  }
  
  const config = getConfig()
  
  return (
    <div className="flex items-center gap-2">
      <div className={`w-2 h-2 rounded-full ${config.color}`} />
      <span className="text-sm font-medium">{config.label}</span>
    </div>
  )
}

const RiskProfileBadge = ({ profile }: { profile: string }) => {
  const config = {
    conservative: { color: 'bg-blue-500/20 text-blue-500 border-blue-500/30', label: '保守' },
    moderate: { color: 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30', label: '稳健' },
    aggressive: { color: 'bg-red-500/20 text-red-500 border-red-500/30', label: '激进' },
  }[profile] || { color: 'bg-gray-500/20 text-gray-500', label: '未知' }
  
  return <Badge variant="outline" className={config.color}>{config.label}</Badge>
}

const ActionBadge = ({ action }: { action: string }) => {
  const config = {
    enter_long: { color: 'bg-green-500', label: '做多入场' },
    enter_short: { color: 'bg-red-500', label: '做空入场' },
    hold: { color: 'bg-blue-500', label: '持仓等待' },
    wait: { color: 'bg-gray-500', label: '观望' },
    exit: { color: 'bg-orange-500', label: '退出' },
    BUY: { color: 'bg-green-500', label: '买入' },
    SELL: { color: 'bg-red-500', label: '卖出' },
    HOLD: { color: 'bg-gray-500', label: '持仓' },
  }[action] || { color: 'bg-gray-500', label: action }
  
  return <Badge className={`${config.color} text-white`}>{config.label}</Badge>
}

const ConsensusBadge = ({ consensus, strength }: { consensus: string; strength: number }) => {
  const config = {
    bullish: { icon: TrendingUp, color: 'text-green-500', bg: 'bg-green-500/10', label: '看涨' },
    bearish: { icon: TrendingDown, color: 'text-red-500', bg: 'bg-red-500/10', label: '看跌' },
    neutral: { icon: Minus, color: 'text-gray-500', bg: 'bg-gray-500/10', label: '中性' },
  }[consensus] || { icon: Minus, color: 'text-gray-500', bg: 'bg-gray-500/10', label: '未知' }
  
  const Icon = config.icon
  
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg ${config.bg}`}>
      <Icon className={`w-4 h-4 ${config.color}`} />
      <span className={`font-medium ${config.color}`}>{config.label}</span>
      <span className="text-sm text-muted-foreground">({(strength * 100).toFixed(0)}%)</span>
    </div>
  )
}

const RiskMeter = ({ score }: { score: number }) => {
  const getColor = () => {
    if (score > 70) return 'text-red-500'
    if (score > 50) return 'text-orange-500'
    if (score > 30) return 'text-yellow-500'
    return 'text-green-500'
  }
  
  return (
    <div className="flex items-center gap-3">
      <Gauge className={`w-5 h-5 ${getColor()}`} />
      <div className="flex-1">
        <Progress value={score} className="h-2" />
      </div>
      <span className={`font-bold ${getColor()}`}>{score.toFixed(0)}</span>
    </div>
  )
}

// ==================== 策略向导组件 ====================

interface WizardStepProps {
  config: StrategyConfig
  setConfig: (config: StrategyConfig) => void
  onNext: () => void
  onBack?: () => void
  symbols?: string[]
}

const WizardStep1 = ({ config, setConfig, onNext, symbols = FALLBACK_TRADING_PAIRS }: WizardStepProps) => (
  <div className="space-y-6">
    <div>
      <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Target className="w-5 h-5 text-primary" />
        基本信息
      </h3>
      <div className="space-y-4">
        <div>
          <Label>策略名称</Label>
          <Input
            value={config.name}
            onChange={(e) => setConfig({ ...config, name: e.target.value })}
            placeholder="输入策略名称"
          />
        </div>
        <div>
          <Label>策略描述</Label>
          <Input
            value={config.description}
            onChange={(e) => setConfig({ ...config, description: e.target.value })}
            placeholder="简述策略目标"
          />
        </div>
        <div>
          <Label>交易标的</Label>
          <div className="flex flex-wrap gap-2 mt-2">
            {symbols.slice(0, 6).map((sym) => {
              const isSelected = config.symbols.includes(sym)
              return (
                <button
                  key={sym}
                  onClick={() => {
                    const newSymbols = isSelected
                      ? config.symbols.filter((s) => s !== sym)
                      : [...config.symbols, sym]
                    setConfig({ ...config, symbols: newSymbols })
                  }}
                  className={`inline-flex items-center justify-center gap-2 whitespace-nowrap text-xs font-medium transition-all h-8 px-3 rounded-md ${
                    isSelected
                      ? 'bg-blue-600 text-white shadow-md hover:bg-blue-700 border-2 border-blue-600 font-bold'
                      : 'border border-gray-300 bg-white hover:bg-gray-100 text-gray-900'
                  }`}
                >
                  {sym}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
    <div className="flex justify-end">
      <Button onClick={onNext} disabled={!config.name || config.symbols.length === 0}>
        下一步 <ChevronRight className="w-4 h-4 ml-1" />
      </Button>
    </div>
  </div>
)

const WizardStep2 = ({ config, setConfig, onNext, onBack }: WizardStepProps) => (
  <div className="space-y-6">
    <div>
      <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Clock className="w-5 h-5 text-primary" />
        时间跨度与风险偏好
      </h3>
      <div className="space-y-4">
        <div>
          <Label>策略周期</Label>
          <div className="grid grid-cols-2 gap-2 mt-2">
            {[
              { value: 'intraday', label: '日内', desc: '1-24小时' },
              { value: 'swing', label: '波段', desc: '1-7天' },
              { value: 'position', label: '中期', desc: '1周-1月' },
              { value: 'long_term', label: '长期', desc: '1月以上' },
            ].map((opt) => (
              <Button
                key={opt.value}
                variant={config.horizon === opt.value ? 'default' : 'outline'}
                className="h-auto flex-col py-3"
                onClick={() => setConfig({ ...config, horizon: opt.value })}
              >
                <span className="font-semibold">{opt.label}</span>
                <span className="text-xs opacity-70">{opt.desc}</span>
              </Button>
            ))}
          </div>
        </div>
        <Separator />
        <div>
          <Label>风险偏好</Label>
          <div className="grid grid-cols-3 gap-2 mt-2">
            {[
              { value: 'conservative', label: '保守', desc: '低风险低收益' },
              { value: 'moderate', label: '稳健', desc: '平衡风险收益' },
              { value: 'aggressive', label: '激进', desc: '高风险高收益' },
            ].map((opt) => (
              <Button
                key={opt.value}
                variant={config.risk_profile === opt.value ? 'default' : 'outline'}
                className="h-auto flex-col py-3"
                onClick={() => setConfig({ ...config, risk_profile: opt.value })}
              >
                <span className="font-semibold">{opt.label}</span>
                <span className="text-xs opacity-70">{opt.desc}</span>
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
    <div className="flex justify-between">
      <Button variant="outline" onClick={onBack}>
        <ChevronLeft className="w-4 h-4 mr-1" /> 上一步
      </Button>
      <Button onClick={onNext}>
        下一步 <ChevronRight className="w-4 h-4 ml-1" />
      </Button>
    </div>
  </div>
)

const WizardStep3 = ({ config, setConfig, onNext, onBack }: WizardStepProps) => (
  <div className="space-y-6">
    <div>
      <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Shield className="w-5 h-5 text-primary" />
        风险控制参数
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label>单仓最大占比 (%)</Label>
          <Input
            type="number"
            value={config.max_position_pct}
            onChange={(e) => setConfig({ ...config, max_position_pct: Number(e.target.value) })}
            min={5}
            max={50}
          />
        </div>
        <div>
          <Label>总敞口上限 (%)</Label>
          <Input
            type="number"
            value={config.max_total_exposure}
            onChange={(e) => setConfig({ ...config, max_total_exposure: Number(e.target.value) })}
            min={20}
            max={100}
          />
        </div>
        <div>
          <Label>日最大亏损 (%)</Label>
          <Input
            type="number"
            value={config.max_daily_loss_pct}
            onChange={(e) => setConfig({ ...config, max_daily_loss_pct: Number(e.target.value) })}
            min={1}
            max={20}
          />
        </div>
        <div>
          <Label>最大杠杆</Label>
          <Input
            type="number"
            value={config.max_leverage}
            onChange={(e) => setConfig({ ...config, max_leverage: Number(e.target.value) })}
            min={1}
            max={20}
          />
        </div>
        <div>
          <Label>止损百分比 (%)</Label>
          <Input
            type="number"
            value={config.stop_loss_pct}
            onChange={(e) => setConfig({ ...config, stop_loss_pct: Number(e.target.value) })}
            min={1}
            max={20}
          />
        </div>
        <div>
          <Label>止盈百分比 (%)</Label>
          <Input
            type="number"
            value={config.take_profit_pct}
            onChange={(e) => setConfig({ ...config, take_profit_pct: Number(e.target.value) })}
            min={1}
            max={50}
          />
        </div>
      </div>
    </div>
    <div className="flex justify-between">
      <Button variant="outline" onClick={onBack}>
        <ChevronLeft className="w-4 h-4 mr-1" /> 上一步
      </Button>
      <Button onClick={onNext}>
        下一步 <ChevronRight className="w-4 h-4 ml-1" />
      </Button>
    </div>
  </div>
)

const WizardStep4 = ({ config, setConfig, onNext, onBack }: WizardStepProps) => (
  <div className="space-y-6">
    <div>
      <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Settings className="w-5 h-5 text-primary" />
        执行设置
      </h3>
      <div className="space-y-4">
        <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
          <div>
            <Label className="text-base">自动执行</Label>
            <p className="text-sm text-muted-foreground">策略信号触发后自动下单</p>
          </div>
          <Switch
            checked={config.auto_execute}
            onCheckedChange={(checked) => setConfig({ ...config, auto_execute: checked })}
          />
        </div>
        <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
          <div>
            <Label className="text-base">需要确认</Label>
            <p className="text-sm text-muted-foreground">执行前需要手动确认</p>
          </div>
          <Switch
            checked={config.require_confirmation}
            onCheckedChange={(checked) => setConfig({ ...config, require_confirmation: checked })}
          />
        </div>
        <div>
          <Label>最小信号强度</Label>
          <div className="flex items-center gap-4 mt-2">
            <Input
              type="range"
              min={0.3}
              max={0.9}
              step={0.1}
              value={config.min_signal_strength}
              onChange={(e) => setConfig({ ...config, min_signal_strength: Number(e.target.value) })}
              className="flex-1"
            />
            <span className="w-16 text-right font-mono">
              {(config.min_signal_strength * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      </div>
    </div>
    <Separator />
    <div>
      <h4 className="font-semibold mb-3">配置预览</h4>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div className="flex justify-between p-2 bg-muted rounded">
          <span className="text-muted-foreground">策略名称</span>
          <span className="font-medium">{config.name}</span>
        </div>
        <div className="flex justify-between p-2 bg-muted rounded">
          <span className="text-muted-foreground">交易标的</span>
          <span className="font-medium">{config.symbols.join(', ')}</span>
        </div>
        <div className="flex justify-between p-2 bg-muted rounded">
          <span className="text-muted-foreground">风险偏好</span>
          <RiskProfileBadge profile={config.risk_profile} />
        </div>
        <div className="flex justify-between p-2 bg-muted rounded">
          <span className="text-muted-foreground">最大杠杆</span>
          <span className="font-medium">{config.max_leverage}x</span>
        </div>
      </div>
    </div>
    <div className="flex justify-between">
      <Button variant="outline" onClick={onBack}>
        <ChevronLeft className="w-4 h-4 mr-1" /> 上一步
      </Button>
      <Button onClick={onNext} className="bg-gradient-to-r from-blue-500 to-purple-500">
        <Sparkles className="w-4 h-4 mr-1" /> 生成策略
      </Button>
    </div>
  </div>
)

// ==================== 主组件 ====================

export function UnifiedATASConsole() {
  // 统一交易对配置
  const { symbols: configuredPairs } = useTradingPairs()
  const SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  // 视图状态
  const [view, setView] = useState<'list' | 'wizard' | 'detail'>('list')
  const [wizardStep, setWizardStep] = useState(1)

  // 策略状态
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [activeStrategy, setActiveStrategy] = useState<Strategy | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy | null>(null)

  // 配置状态
  const [newConfig, setNewConfig] = useState<StrategyConfig>({
    name: '',
    description: '',
    symbols: SYMBOLS.slice(0, 2),
    horizon: 'swing',
    risk_profile: 'moderate',
    max_position_pct: 25,
    max_total_exposure: 80,
    max_daily_loss_pct: 5,
    stop_loss_pct: 3,
    take_profit_pct: 6,
    enabled_signal_pools: [],
    min_signal_strength: 0.6,
    factor_weights: {},
    auto_execute: false,
    require_confirmation: true,
    max_leverage: 20,
  })
  
  // UI状态
  const [isLoading, setIsLoading] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  
  // ==================== API 调用 ====================
  
  const fetchStrategies = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/strategies`)
      if (response.ok) {
        const data = await response.json()
        setStrategies(data.strategies || [])
      }
    } catch (e) {
      console.error('获取策略列表失败:', e)
    }
  }, [])
  
  const fetchActiveStrategy = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/strategies/active`)
      if (response.ok) {
        const data = await response.json()
        if (data.has_active && data.strategy) {
          setActiveStrategy(data.strategy)
        } else {
          setActiveStrategy(null)
        }
      }
    } catch (e) {
      console.error('获取活跃策略失败:', e)
    }
  }, [])
  
  const createStrategy = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/strategies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      })
      
      const data = await response.json()
      
      if (data.success && data.strategy) {
        setSelectedStrategy(data.strategy)
        setView('detail')
        await fetchStrategies()
        // 自动生成计划
        await generatePlan(data.strategy.strategy_id)
      } else {
        setError(data.error || '创建策略失败')
      }
    } catch (e: any) {
      setError(e.message || '创建策略失败')
    } finally {
      setIsLoading(false)
    }
  }
  
  const generatePlan = async (strategyId: string) => {
    setIsGenerating(true)
    try {
      const response = await fetch(`${API_BASE}/strategies/${strategyId}/generate`, {
        method: 'POST',
      })
      
      const data = await response.json()
      
      if (data.success && data.strategy) {
        setSelectedStrategy(data.strategy)
        await fetchStrategies()
      }
    } catch (e) {
      console.error('生成策略计划失败:', e)
    } finally {
      setIsGenerating(false)
    }
  }
  
  const activateStrategy = async (strategyId: string) => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/strategies/${strategyId}/activate`, {
        method: 'POST',
      })
      
      const data = await response.json()
      
      if (data.success && data.strategy) {
        setSelectedStrategy(data.strategy)
        setActiveStrategy(data.strategy)
        await fetchStrategies()
      }
    } catch (e) {
      console.error('激活策略失败:', e)
    } finally {
      setIsLoading(false)
    }
  }
  
  const pauseStrategy = async (strategyId: string) => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/strategies/${strategyId}/pause`, {
        method: 'POST',
      })
      
      const data = await response.json()
      
      if (data.success && data.strategy) {
        setSelectedStrategy(data.strategy)
        if (activeStrategy?.strategy_id === strategyId) {
          setActiveStrategy(null)
        }
        await fetchStrategies()
      }
    } catch (e) {
      console.error('暂停策略失败:', e)
    } finally {
      setIsLoading(false)
    }
  }
  
  // ==================== Effects ====================
  
  useEffect(() => {
    fetchStrategies()
    fetchActiveStrategy()
  }, [fetchStrategies, fetchActiveStrategy])
  
  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(() => {
      fetchActiveStrategy()
    }, 30000)
    return () => clearInterval(interval)
  }, [autoRefresh, fetchActiveStrategy])
  
  // ==================== 渲染 ====================
  
  const renderWizard = () => (
    <Card className="max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-primary" />
          创建新策略
        </CardTitle>
        <CardDescription>
          通过向导配置您的交易策略
        </CardDescription>
        <div className="flex items-center gap-2 mt-4">
          {[1, 2, 3, 4].map((step) => (
            <div key={step} className="flex items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                  wizardStep >= step
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted text-muted-foreground'
                }`}
              >
                {wizardStep > step ? <Check className="w-4 h-4" /> : step}
              </div>
              {step < 4 && (
                <div
                  className={`w-12 h-1 ${
                    wizardStep > step ? 'bg-primary' : 'bg-muted'
                  }`}
                />
              )}
            </div>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {wizardStep === 1 && (
          <WizardStep1
            config={newConfig}
            setConfig={setNewConfig}
            onNext={() => setWizardStep(2)}
            symbols={SYMBOLS}
          />
        )}
        {wizardStep === 2 && (
          <WizardStep2
            config={newConfig}
            setConfig={setNewConfig}
            onNext={() => setWizardStep(3)}
            onBack={() => setWizardStep(1)}
          />
        )}
        {wizardStep === 3 && (
          <WizardStep3
            config={newConfig}
            setConfig={setNewConfig}
            onNext={() => setWizardStep(4)}
            onBack={() => setWizardStep(2)}
          />
        )}
        {wizardStep === 4 && (
          <WizardStep4
            config={newConfig}
            setConfig={setNewConfig}
            onNext={createStrategy}
            onBack={() => setWizardStep(3)}
          />
        )}
        {error && (
          <div className="mt-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-500 text-sm">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  )
  
  const renderStrategyDetail = () => {
    if (!selectedStrategy) return null
    
    const { config, plan, execution, phase } = selectedStrategy
    
    return (
      <div className="space-y-4">
        {/* 策略头部 */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-2xl font-bold flex items-center gap-3">
                  {config.name}
                  <PhaseIndicator phase={phase} />
                </h2>
                <p className="text-muted-foreground mt-1">{config.description || '无描述'}</p>
                <div className="flex items-center gap-4 mt-3">
                  <div className="flex items-center gap-1">
                    {config.symbols.map((sym) => (
                      <Badge key={sym} variant="outline">{sym}</Badge>
                    ))}
                  </div>
                  <RiskProfileBadge profile={config.risk_profile} />
                </div>
              </div>
              <div className="flex gap-2">
                {phase === 'ready' && (
                  <Button onClick={() => activateStrategy(selectedStrategy.strategy_id)} disabled={isLoading}>
                    <Play className="w-4 h-4 mr-1" /> 激活
                  </Button>
                )}
                {phase === 'active' && (
                  <Button variant="destructive" onClick={() => pauseStrategy(selectedStrategy.strategy_id)} disabled={isLoading}>
                    <Pause className="w-4 h-4 mr-1" /> 暂停
                  </Button>
                )}
                {phase === 'draft' && (
                  <Button onClick={() => generatePlan(selectedStrategy.strategy_id)} disabled={isGenerating}>
                    {isGenerating ? (
                      <RefreshCw className="w-4 h-4 mr-1 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4 mr-1" />
                    )}
                    生成计划
                  </Button>
                )}
                <Button variant="outline" onClick={() => {
                  setSelectedStrategy(null)
                  setView('list')
                }}>
                  返回列表
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
        
        {/* 策略详情 Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="overview">概览</TabsTrigger>
            <TabsTrigger value="plan">策略计划</TabsTrigger>
            <TabsTrigger value="signals">信号与因子</TabsTrigger>
            <TabsTrigger value="execution">执行监控</TabsTrigger>
          </TabsList>
          
          <ScrollArea className="h-[calc(100vh-380px)]">
            {/* 概览 Tab */}
            <TabsContent value="overview" className="space-y-4 mt-4">
              <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                {/* 市场周期 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <TrendingUp className="w-4 h-4" />
                      市场周期
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold capitalize">
                      {plan.market_cycle === 'bull_trend' ? '上涨趋势' :
                       plan.market_cycle === 'bear_trend' ? '下跌趋势' :
                       plan.market_cycle === 'high_volatility' ? '高波动' :
                       plan.market_cycle === 'low_volatility' ? '低波动' :
                       plan.market_cycle === 'accumulation' ? '吸筹' :
                       plan.market_cycle === 'distribution' ? '派发' : plan.market_cycle}
                    </div>
                    <div className="text-sm text-muted-foreground mt-1">
                      置信度: {(plan.cycle_confidence * 100).toFixed(0)}%
                    </div>
                  </CardContent>
                </Card>
                
                {/* 战术建议 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Zap className="w-4 h-4" />
                      战术建议
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ActionBadge action={plan.tactical_action} />
                    <div className="text-sm text-muted-foreground mt-2">
                      置信度: {(plan.tactical_confidence * 100).toFixed(0)}%
                    </div>
                  </CardContent>
                </Card>
                
                {/* 信号共识 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Signal className="w-4 h-4" />
                      信号共识
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ConsensusBadge consensus={plan.signal_consensus} strength={plan.signal_strength} />
                  </CardContent>
                </Card>
                
                {/* 风险评分 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Shield className="w-4 h-4" />
                      风险评分
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <RiskMeter score={plan.risk_score} />
                    <div className="text-sm text-muted-foreground mt-1">
                      波动水平: {plan.volatility_level}
                    </div>
                  </CardContent>
                </Card>
                
                {/* 关键价位 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <BarChart3 className="w-4 h-4" />
                      关键价位
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex justify-between">
                      <div>
                        <div className="text-xs text-muted-foreground">支撑</div>
                        <div className="text-lg font-bold text-green-500">
                          ${plan.key_support?.toLocaleString() || 'N/A'}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-xs text-muted-foreground">阻力</div>
                        <div className="text-lg font-bold text-red-500">
                          ${plan.key_resistance?.toLocaleString() || 'N/A'}
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                
                {/* 因子评分 */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Brain className="w-4 h-4" />
                      因子评分
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold">
                      {plan.factor_score.toFixed(1)}
                    </div>
                    <Progress value={plan.factor_score} className="h-2 mt-2" />
                  </CardContent>
                </Card>
              </div>
              
              {/* 推荐动作 */}
              {plan.recommended_actions.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Sparkles className="w-5 h-5 text-primary" />
                      推荐动作
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {plan.recommended_actions.map((action, idx) => (
                        <div key={idx} className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
                          <div className="flex items-center gap-4">
                            <ActionBadge action={action.action} />
                            <div>
                              <div className="font-semibold">{action.symbol}</div>
                              <div className="text-sm text-muted-foreground">{action.reason}</div>
                            </div>
                          </div>
                          <div className="text-right">
                            {action.entry_price && (
                              <div className="text-sm">
                                入场: ${action.entry_price.toLocaleString()}
                              </div>
                            )}
                            <div className="text-xs text-muted-foreground">
                              置信度: {(action.confidence * 100).toFixed(0)}%
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
            </TabsContent>
            
            {/* 策略计划 Tab */}
            <TabsContent value="plan" className="space-y-4 mt-4">
              <div className="grid md:grid-cols-2 gap-4">
                {/* 中长期规划 */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Layers className="w-5 h-5 text-blue-500" />
                      中长期规划
                    </CardTitle>
                    <CardDescription>基于宏观周期的市场分析</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="p-4 bg-muted/50 rounded-lg">
                      <div className="text-sm text-muted-foreground mb-1">市场周期</div>
                      <div className="text-xl font-bold capitalize">{plan.market_cycle}</div>
                      <Progress value={plan.cycle_confidence * 100} className="h-2 mt-2" />
                      <div className="text-xs text-muted-foreground mt-1">
                        置信度: {(plan.cycle_confidence * 100).toFixed(0)}%
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="p-3 bg-green-500/10 rounded-lg">
                        <div className="text-sm text-muted-foreground">关键支撑</div>
                        <div className="text-lg font-bold text-green-500">
                          ${plan.key_support?.toLocaleString() || 'N/A'}
                        </div>
                      </div>
                      <div className="p-3 bg-red-500/10 rounded-lg">
                        <div className="text-sm text-muted-foreground">关键阻力</div>
                        <div className="text-lg font-bold text-red-500">
                          ${plan.key_resistance?.toLocaleString() || 'N/A'}
                        </div>
                      </div>
                    </div>
                    <Separator />
                    <div className="space-y-2">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">仓位偏向</span>
                        <span className="font-medium capitalize">{plan.position_bias}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">最大仓位</span>
                        <span className="font-medium">{config.max_position_pct}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">最大杠杆</span>
                        <span className="font-medium">{config.max_leverage}x</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
                
                {/* 短期战术 */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Zap className="w-5 h-5 text-yellow-500" />
                      短期战术
                    </CardTitle>
                    <CardDescription>日内交易的战术决策</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="p-4 bg-muted/50 rounded-lg">
                      <div className="text-sm text-muted-foreground mb-2">当前建议</div>
                      <div className="flex items-center gap-2">
                        <ActionBadge action={plan.tactical_action} />
                        <span className="text-sm">
                          置信度: {(plan.tactical_confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="p-3 bg-muted rounded-lg">
                        <div className="text-sm text-muted-foreground">入场时机</div>
                        <div className="font-semibold capitalize">{plan.entry_timing}</div>
                      </div>
                      <div className="p-3 bg-muted rounded-lg">
                        <div className="text-sm text-muted-foreground">波动水平</div>
                        <div className="font-semibold capitalize">{plan.volatility_level}</div>
                      </div>
                    </div>
                    <Separator />
                    <div className="space-y-2">
                      <div className="flex justify-between p-2 bg-muted/50 rounded">
                        <span className="text-muted-foreground">建议入场</span>
                        <span className="font-medium">${plan.suggested_entry?.toLocaleString() || 'N/A'}</span>
                      </div>
                      <div className="flex justify-between p-2 bg-red-500/10 rounded">
                        <span className="text-red-500">止损价</span>
                        <span className="font-medium">${plan.suggested_stop_loss?.toLocaleString() || 'N/A'}</span>
                      </div>
                      <div className="flex justify-between p-2 bg-green-500/10 rounded">
                        <span className="text-green-500">止盈价</span>
                        <span className="font-medium">${plan.suggested_take_profit?.toLocaleString() || 'N/A'}</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
            
            {/* 信号与因子 Tab */}
            <TabsContent value="signals" className="space-y-4 mt-4">
              <div className="grid md:grid-cols-2 gap-4">
                {/* 信号共识 */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Signal className="w-5 h-5" />
                      信号系统
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex items-center justify-center p-4">
                      <ConsensusBadge consensus={plan.signal_consensus} strength={plan.signal_strength} />
                    </div>
                    <Separator />
                    <div className="space-y-2">
                      {plan.active_signals.length > 0 ? (
                        plan.active_signals.slice(0, 5).map((signal, idx) => (
                          <div key={idx} className="flex items-center justify-between p-2 bg-muted/50 rounded">
                            <span className="font-medium">{signal.name || signal.signal_name}</span>
                            <Badge variant="outline">{signal.direction || 'neutral'}</Badge>
                          </div>
                        ))
                      ) : (
                        <div className="text-center text-muted-foreground py-4">
                          暂无活跃信号
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
                
                {/* 关键因子 */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Brain className="w-5 h-5" />
                      因子分析
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
                      <span className="text-muted-foreground">综合评分</span>
                      <span className="text-2xl font-bold">{plan.factor_score.toFixed(1)}</span>
                    </div>
                    <Separator />
                    <div className="space-y-2">
                      {Object.entries(plan.key_factors).slice(0, 8).map(([name, value]) => (
                        <div key={name} className="flex items-center justify-between p-2 bg-muted/30 rounded">
                          <span className="text-sm">{name}</span>
                          <span className="font-mono text-sm">{typeof value === 'number' ? value.toFixed(4) : value}</span>
                        </div>
                      ))}
                      {Object.keys(plan.key_factors).length === 0 && (
                        <div className="text-center text-muted-foreground py-4">
                          暂无因子数据
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
            
            {/* 执行监控 Tab */}
            <TabsContent value="execution" className="space-y-4 mt-4">
              <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">状态</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className={`text-2xl font-bold ${execution.is_active ? 'text-green-500' : 'text-gray-500'}`}>
                      {execution.is_active ? '运行中' : '未运行'}
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">今日交易</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold">{execution.trades_today}</div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">今日盈亏</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className={`text-2xl font-bold ${execution.pnl_today >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                      ${execution.pnl_today.toFixed(2)}
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">胜率</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold">{(execution.win_rate * 100).toFixed(1)}%</div>
                  </CardContent>
                </Card>
              </div>
              
              <div className="grid md:grid-cols-2 gap-4">
                <DecisionLog symbol={SYMBOLS[0] || 'BTC'} />
                <RuntimeGovernorPanel />
              </div>
              
              <Card>
                <CardHeader>
                  <CardTitle>执行日志</CardTitle>
                  <CardDescription>逐笔事件已迁移至 TCP 决策快照（上方面板）</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="text-center text-muted-foreground py-4 text-sm">
                    完整审计链见 API /api/gap-closure/audit/chain
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </ScrollArea>
        </Tabs>
      </div>
    )
  }
  
  const renderStrategyList = () => (
    <div className="space-y-4">
      {/* 活跃策略卡片 */}
      {activeStrategy && (
        <Card className="border-primary/50 bg-primary/5">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="w-5 h-5 text-primary animate-pulse" />
                  当前活跃策略
                </CardTitle>
                <CardDescription>{activeStrategy.config.name}</CardDescription>
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  setSelectedStrategy(activeStrategy)
                  setView('detail')
                }}
              >
                查看详情
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-sm text-muted-foreground">市场周期</div>
                <div className="font-semibold capitalize">{activeStrategy.plan.market_cycle}</div>
              </div>
              <div>
                <div className="text-sm text-muted-foreground">战术建议</div>
                <ActionBadge action={activeStrategy.plan.tactical_action} />
              </div>
              <div>
                <div className="text-sm text-muted-foreground">信号共识</div>
                <div className="capitalize">{activeStrategy.plan.signal_consensus}</div>
              </div>
              <div>
                <div className="text-sm text-muted-foreground">风险评分</div>
                <div className="font-semibold">{activeStrategy.plan.risk_score.toFixed(0)}</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
      
      {/* 策略列表 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>策略列表</CardTitle>
              <CardDescription>管理您的交易策略</CardDescription>
            </div>
            <Button onClick={() => {
              setNewConfig({
                name: '',
                description: '',
                symbols: SYMBOLS.slice(0, 2),
                horizon: 'swing',
                risk_profile: 'moderate',
                max_position_pct: 25,
                max_total_exposure: 80,
                max_daily_loss_pct: 5,
                stop_loss_pct: 3,
                take_profit_pct: 6,
                enabled_signal_pools: [],
                min_signal_strength: 0.6,
                factor_weights: {},
                auto_execute: false,
                require_confirmation: true,
                max_leverage: 20,
              })
              setWizardStep(1)
              setView('wizard')
            }}>
              <PlusCircle className="w-4 h-4 mr-1" /> 创建策略
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {strategies.length > 0 ? (
            <div className="space-y-3">
              {strategies.map((strategy) => (
                <div
                  key={strategy.strategy_id}
                  className="flex items-center justify-between p-4 bg-muted/50 rounded-lg hover:bg-muted/70 cursor-pointer transition-colors"
                  onClick={() => {
                    setSelectedStrategy(strategy)
                    setView('detail')
                  }}
                >
                  <div className="flex items-center gap-4">
                    <PhaseIndicator phase={strategy.phase} />
                    <div>
                      <div className="font-semibold">{strategy.config.name}</div>
                      <div className="text-sm text-muted-foreground">
                        {strategy.config.symbols.join(', ')} • {strategy.config.horizon}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <RiskProfileBadge profile={strategy.config.risk_profile} />
                    <div className="text-sm text-muted-foreground">
                      {new Date(strategy.updated_at).toLocaleDateString()}
                    </div>
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12">
              <Sparkles className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">还没有策略</h3>
              <p className="text-muted-foreground mb-4">创建您的第一个交易策略开始自动化交易</p>
              <Button onClick={() => {
                setWizardStep(1)
                setView('wizard')
              }}>
                <PlusCircle className="w-4 h-4 mr-1" /> 创建策略
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
  
  return (
    <div className="p-6 min-h-screen bg-background">
      {/* 页面头部 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <GitBranch className="w-6 h-6 text-primary" />
            ATAS 策略中心
          </h1>
          <p className="text-muted-foreground">统一策略生成与执行平台</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch
              id="auto-refresh"
              checked={autoRefresh}
              onCheckedChange={setAutoRefresh}
            />
            <Label htmlFor="auto-refresh" className="text-sm">自动刷新</Label>
          </div>
          <Button variant="outline" size="sm" onClick={() => {
            fetchStrategies()
            fetchActiveStrategy()
          }}>
            <RefreshCw className="w-4 h-4 mr-1" /> 刷新
          </Button>
        </div>
      </div>
      
      {/* 主内容 */}
      {view === 'list' && renderStrategyList()}
      {view === 'wizard' && renderWizard()}
      {view === 'detail' && renderStrategyDetail()}
      
      {/* 加载遮罩 */}
      {isLoading && (
        <div className="fixed inset-0 bg-background/50 flex items-center justify-center z-50">
          <div className="flex items-center gap-3 p-4 bg-card rounded-lg shadow-lg">
            <RefreshCw className="w-5 h-5 animate-spin" />
            <span>处理中...</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default UnifiedATASConsole
