/**
 * ATAS 交易控制台 - 主页面 (v2.0 重构版)
 * 
 * 功能:
 * - 系统状态监控
 * - 市场概览
 * - 活跃信号
 * - AI 决策记录
 * - 配置管理
 */

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Activity,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Target,
  BarChart3,
  Zap,
  Brain,
  Wallet,
  Settings,
  Play,
  Square,
  Clock,
  Signal,
  Bot,
} from 'lucide-react'

// API 基础路径
const API_BASE = '/api/atas'

// ==================== 类型定义 ====================

interface ATASStatus {
  state: string
  is_running: boolean
  uptime_seconds: number
  statistics: {
    active_traders: number
    running_traders: number
    total_strategies: number
  }
  strategies: Array<{
    account_id: number
    enabled: boolean
    running: boolean
    trigger_interval: number
    signal_pool_id: number | null
    last_trigger_at: string | null
  }>
  last_update: string
}

interface MarketData {
  [symbol: string]: {
    symbol: string
    price: number
    price_available: boolean
    change_24h?: number
    volume_24h?: number
    error?: string
  }
}

interface SignalData {
  id: number
  signal_id: number
  signal_name: string
  symbol: string
  metric_value: number
  threshold: number
  operator: string
  triggered_at: string
}

interface DecisionData {
  id: number
  account_id: number
  symbol: string
  action: string
  quantity: number
  price: number
  reasoning: string
  executed: boolean
  created_at: string
}

interface ATASConfig {
  auto_refresh_enabled: boolean
  refresh_interval: number
  monitored_symbols: string[]
  risk_level: string
  max_position_percent: number
  stop_loss_percent: number
}

interface Statistics {
  today: {
    decisions: number
    executions: number
    signals: number
  }
}

// ==================== 主组件 ====================

export function TradingConsole() {
  // 连接状态
  const [isConnected, setIsConnected] = useState<boolean | null>(null)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  
  // 数据状态
  const [status, setStatus] = useState<ATASStatus | null>(null)
  const [marketData, setMarketData] = useState<MarketData>({})
  const [signals, setSignals] = useState<SignalData[]>([])
  const [decisions, setDecisions] = useState<DecisionData[]>([])
  const [statistics, setStatistics] = useState<Statistics | null>(null)
  const [config, setConfig] = useState<ATASConfig | null>(null)
  
  // UI 状态
  const [isLoading, setIsLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('overview')
  const [configSaving, setConfigSaving] = useState(false)

  // ==================== API 调用 ====================

  const checkHealth = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/health`)
      if (response.ok) {
        const data = await response.json()
        setIsConnected(data.available)
        setConnectionError(null)
        return data.available
      }
      setIsConnected(false)
      setConnectionError('服务器响应异常')
      return false
    } catch (e) {
      setIsConnected(false)
      setConnectionError('无法连接到服务器')
      return false
    }
  }, [])

  const fetchAllData = useCallback(async () => {
    if (!isConnected) return
    
    setIsLoading(true)
    try {
      const [statusRes, overviewRes, signalsRes, decisionsRes, statsRes, configRes] = await Promise.all([
        fetch(`${API_BASE}/status`),
        fetch(`${API_BASE}/overview`),
        fetch(`${API_BASE}/signals`),
        fetch(`${API_BASE}/decisions?limit=20`),
        fetch(`${API_BASE}/statistics`),
        fetch(`${API_BASE}/config`),
      ])

      if (statusRes.ok) {
        const data = await statusRes.json()
        setStatus(data)
      }
      if (overviewRes.ok) {
        const data = await overviewRes.json()
        setMarketData(data.data || {})
      }
      if (signalsRes.ok) {
        const data = await signalsRes.json()
        setSignals(data.signals || [])
      }
      if (decisionsRes.ok) {
        const data = await decisionsRes.json()
        setDecisions(data.decisions || [])
      }
      if (statsRes.ok) {
        const data = await statsRes.json()
        setStatistics(data)
      }
      if (configRes.ok) {
        const data = await configRes.json()
        setConfig(data.config || null)
      }
    } catch (e) {
      console.error('获取数据失败:', e)
    } finally {
      setIsLoading(false)
    }
  }, [isConnected])

  const saveConfig = useCallback(async (newConfig: Partial<ATASConfig>) => {
    setConfigSaving(true)
    try {
      const response = await fetch(`${API_BASE}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      })
      if (response.ok) {
        const data = await response.json()
        setConfig(data.config)
      }
    } catch (e) {
      console.error('保存配置失败:', e)
    } finally {
      setConfigSaving(false)
    }
  }, [])

  // ==================== 生命周期 ====================

  useEffect(() => {
    const init = async () => {
      const connected = await checkHealth()
      if (connected) {
        fetchAllData()
      }
    }
    init()
  }, [checkHealth, fetchAllData])

  // 自动刷新
  useEffect(() => {
    if (!isConnected || !config?.auto_refresh_enabled) return
    
    const interval = setInterval(() => {
      fetchAllData()
    }, (config?.refresh_interval || 60) * 1000)
    
    return () => clearInterval(interval)
  }, [isConnected, config?.auto_refresh_enabled, config?.refresh_interval, fetchAllData])

  // ==================== 渲染逻辑 ====================

  // 加载中
  if (isConnected === null) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8">
        <RefreshCw className="w-8 h-8 animate-spin text-muted-foreground mb-4" />
        <div className="text-muted-foreground">检查 ATAS 系统状态...</div>
      </div>
    )
  }

  // 连接失败
  if (isConnected === false) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8">
        <AlertTriangle className="w-12 h-12 text-amber-500 mb-4" />
        <h2 className="text-xl font-semibold mb-2">ATAS 系统暂不可用</h2>
        <p className="text-muted-foreground text-center max-w-md mb-4">
          {connectionError || '后端服务未启动或网络连接异常'}
        </p>
        <Button 
          variant="outline" 
          onClick={() => {
            setIsConnected(null)
            checkHealth()
          }}
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          重试连接
        </Button>
      </div>
    )
  }

  // 获取状态颜色
  const getStateColor = (state: string) => {
    switch (state) {
      case 'running': return 'text-green-500'
      case 'idle': return 'text-yellow-500'
      case 'error': return 'text-red-500'
      default: return 'text-muted-foreground'
    }
  }

  return (
    <div className="h-full flex flex-col gap-4 p-4 bg-background overflow-auto">
      {/* 顶部状态栏 */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">ATAS 控制台</h1>
          <Badge variant={status?.is_running ? 'default' : 'secondary'} className="gap-1">
            <Activity className={`w-3 h-3 ${status?.is_running ? 'animate-pulse' : ''}`} />
            {status?.state === 'running' ? '运行中' : status?.state === 'idle' ? '空闲' : '未知'}
          </Badge>
          <Badge variant="outline" className="gap-1">
            <Bot className="w-3 h-3" />
            {status?.statistics.active_traders || 0} 个活跃交易者
          </Badge>
        </div>
        
        <div className="flex items-center gap-4">
          {/* 自动刷新 */}
          <div className="flex items-center gap-2">
            <Switch
              id="auto-refresh"
              checked={config?.auto_refresh_enabled ?? true}
              onCheckedChange={(checked) => saveConfig({ auto_refresh_enabled: checked })}
            />
            <Label htmlFor="auto-refresh" className="text-sm">自动刷新</Label>
          </div>
          
          {/* 手动刷新 */}
          <Button
            variant="outline"
            size="sm"
            onClick={fetchAllData}
            disabled={isLoading}
          >
            <RefreshCw className={`w-4 h-4 mr-1 ${isLoading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">今日信号</p>
                <p className="text-2xl font-bold">{statistics?.today.signals || 0}</p>
              </div>
              <Zap className="w-8 h-8 text-yellow-500" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">今日决策</p>
                <p className="text-2xl font-bold">{statistics?.today.decisions || 0}</p>
              </div>
              <Brain className="w-8 h-8 text-purple-500" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">今日执行</p>
                <p className="text-2xl font-bold">{statistics?.today.executions || 0}</p>
              </div>
              <Target className="w-8 h-8 text-green-500" />
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">活跃策略</p>
                <p className="text-2xl font-bold">{status?.statistics.active_traders || 0}</p>
              </div>
              <Activity className="w-8 h-8 text-blue-500" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 主内容区 - Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="overview" className="gap-1">
            <BarChart3 className="w-4 h-4" />
            市场概览
          </TabsTrigger>
          <TabsTrigger value="signals" className="gap-1">
            <Signal className="w-4 h-4" />
            活跃信号
          </TabsTrigger>
          <TabsTrigger value="decisions" className="gap-1">
            <Brain className="w-4 h-4" />
            AI 决策
          </TabsTrigger>
          <TabsTrigger value="config" className="gap-1">
            <Settings className="w-4 h-4" />
            配置
          </TabsTrigger>
        </TabsList>

        {/* 市场概览 */}
        <TabsContent value="overview" className="flex-1">
          <Card>
            <CardHeader>
              <CardTitle>市场概览</CardTitle>
              <CardDescription>监控的交易对实时价格</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {Object.values(marketData).length > 0 ? (
                  Object.values(marketData).map((item) => (
                    <Card key={item.symbol} className="p-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="font-semibold text-lg">{item.symbol}</p>
                          <p className="text-2xl font-bold">
                            {item.price_available ? `$${item.price.toLocaleString()}` : '---'}
                          </p>
                        </div>
                        {item.price_available ? (
                          <CheckCircle className="w-6 h-6 text-green-500" />
                        ) : (
                          <XCircle className="w-6 h-6 text-red-500" />
                        )}
                      </div>
                    </Card>
                  ))
                ) : (
                  <div className="col-span-3 text-center text-muted-foreground py-8">
                    暂无市场数据
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* 策略状态 */}
          <Card className="mt-4">
            <CardHeader>
              <CardTitle>策略状态</CardTitle>
              <CardDescription>AI 交易者运行状态</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {status?.strategies.map((strategy) => (
                  <div key={strategy.account_id} className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                    <div className="flex items-center gap-3">
                      <Bot className={`w-5 h-5 ${strategy.enabled ? 'text-green-500' : 'text-muted-foreground'}`} />
                      <span>账户 #{strategy.account_id}</span>
                    </div>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <span>间隔: {strategy.trigger_interval}s</span>
                      <Badge variant={strategy.enabled ? 'default' : 'secondary'}>
                        {strategy.enabled ? '已启用' : '已禁用'}
                      </Badge>
                    </div>
                  </div>
                ))}
                {(!status?.strategies || status.strategies.length === 0) && (
                  <div className="text-center text-muted-foreground py-4">
                    暂无策略配置
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 活跃信号 */}
        <TabsContent value="signals" className="flex-1">
          <Card>
            <CardHeader>
              <CardTitle>活跃信号</CardTitle>
              <CardDescription>最近 1 小时内触发的信号</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {signals.map((signal) => (
                  <div key={signal.id} className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                    <div className="flex items-center gap-3">
                      <Zap className="w-5 h-5 text-yellow-500" />
                      <div>
                        <p className="font-medium">{signal.signal_name}</p>
                        <p className="text-sm text-muted-foreground">{signal.symbol}</p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-sm">
                        值: {signal.metric_value.toFixed(2)} {signal.operator} {signal.threshold}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {signal.triggered_at ? new Date(signal.triggered_at).toLocaleTimeString() : ''}
                      </p>
                    </div>
                  </div>
                ))}
                {signals.length === 0 && (
                  <div className="text-center text-muted-foreground py-8">
                    最近 1 小时无信号触发
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* AI 决策 */}
        <TabsContent value="decisions" className="flex-1">
          <Card>
            <CardHeader>
              <CardTitle>AI 决策记录</CardTitle>
              <CardDescription>最近的 AI 交易决策</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {decisions.map((decision) => (
                  <div key={decision.id} className="p-3 bg-muted/50 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={decision.action === 'BUY' ? 'default' : decision.action === 'SELL' ? 'destructive' : 'secondary'}>
                          {decision.action}
                        </Badge>
                        <span className="font-medium">{decision.symbol}</span>
                        <span className="text-sm text-muted-foreground">#{decision.account_id}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {decision.executed ? (
                          <Badge variant="outline" className="text-green-500">已执行</Badge>
                        ) : (
                          <Badge variant="outline" className="text-muted-foreground">未执行</Badge>
                        )}
                      </div>
                    </div>
                    <p className="text-sm text-muted-foreground line-clamp-2">
                      {decision.reasoning || '无推理说明'}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {decision.created_at ? new Date(decision.created_at).toLocaleString() : ''}
                    </p>
                  </div>
                ))}
                {decisions.length === 0 && (
                  <div className="text-center text-muted-foreground py-8">
                    暂无决策记录
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 配置 */}
        <TabsContent value="config" className="flex-1">
          <Card>
            <CardHeader>
              <CardTitle>系统配置</CardTitle>
              <CardDescription>调整 ATAS 系统参数</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {config && (
                <>
                  {/* 刷新间隔 */}
                  <div className="space-y-2">
                    <Label>刷新间隔 (秒)</Label>
                    <Input
                      type="number"
                      value={config.refresh_interval}
                      onChange={(e) => setConfig({ ...config, refresh_interval: parseInt(e.target.value) || 60 })}
                      min={10}
                      max={300}
                    />
                  </div>

                  {/* 风险等级 */}
                  <div className="space-y-2">
                    <Label>风险等级</Label>
                    <Select
                      value={config.risk_level}
                      onValueChange={(value) => setConfig({ ...config, risk_level: value })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="conservative">保守</SelectItem>
                        <SelectItem value="moderate">适中</SelectItem>
                        <SelectItem value="aggressive">激进</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* 最大仓位 */}
                  <div className="space-y-2">
                    <Label>最大仓位占比 (%)</Label>
                    <Input
                      type="number"
                      value={config.max_position_percent}
                      onChange={(e) => setConfig({ ...config, max_position_percent: parseFloat(e.target.value) || 20 })}
                      min={5}
                      max={50}
                    />
                  </div>

                  {/* 止损 */}
                  <div className="space-y-2">
                    <Label>止损百分比 (%)</Label>
                    <Input
                      type="number"
                      value={config.stop_loss_percent}
                      onChange={(e) => setConfig({ ...config, stop_loss_percent: parseFloat(e.target.value) || 5 })}
                      min={1}
                      max={20}
                    />
                  </div>

                  {/* 保存按钮 */}
                  <Button 
                    onClick={() => saveConfig(config)}
                    disabled={configSaving}
                    className="w-full"
                  >
                    {configSaving ? (
                      <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <CheckCircle className="w-4 h-4 mr-2" />
                    )}
                    保存配置
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

export default TradingConsole
