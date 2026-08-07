/**
 * ModernSignalManager - 现代化信号管理页面
 * Glassmorphism + Dark Mode 设计风格
 * 完整功能版本 - 支持 API 连接、模板库、CRUD 操作
 */

import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'react-hot-toast'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
// Checkbox replaced with native input
import {
  Activity,
  Plus,
  Trash2,
  Edit,
  Sparkles,
  Zap,
  Search,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  LayoutTemplate,
  RefreshCw,
  Copy,
  BarChart3,
  Target,
  Loader2,
} from 'lucide-react'

import {
  SIGNAL_TEMPLATES,
  TEMPLATE_CATEGORIES,
  AVAILABLE_METRICS,
  AVAILABLE_OPERATORS,
  AVAILABLE_TIME_WINDOWS,
  type SignalTemplate,
} from './SignalTemplates'
import AiSignalChatModal from './AiSignalChatModal'
import { TradingAccount, getAccounts } from '@/lib/api'
import { useBacktest } from '@/contexts/BacktestContext'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

// ==================== 类型定义 ====================

interface TriggerCondition {
  metric: string
  operator: string
  threshold: number
  time_window: string
}

interface SignalDefinition {
  id: number
  signal_name: string
  description: string | null
  trigger_condition: TriggerCondition
  enabled: boolean
  created_at: string
  updated_at: string
}

interface SignalPool {
  id: number
  pool_name: string
  signal_ids: number[]
  symbols: string[]
  enabled: boolean
  logic: 'OR' | 'AND' | 'WEIGHTED'
  weights?: Record<number, number>
  weight_threshold?: number
  created_at: string
}

interface PoolStats {
  total_triggers: number
  triggers_today: number
  trigger_distribution: Record<string, number>
  signal_stats: Array<{
    signal_id: number
    signal_name: string
    trigger_count: number
  }>
}

// 回测任务状态接口
interface BacktestTask {
  id: string  // 唯一ID
  type: 'signal' | 'pool'
  targetId: number
  targetName: string  // 信号或信号池名称
  symbol: string
  days: number
  status: 'running' | 'completed' | 'error'
  progress: number  // 0-100
  result?: any
  error?: string
  startTime: number
  endTime?: number
  isNew?: boolean  // 用于完成闪烁提醒
}

// ==================== API 函数 ====================

const API_BASE = '/api/signals'

async function fetchSignals(): Promise<{ signals: SignalDefinition[], pools: SignalPool[] }> {
  const res = await fetch(API_BASE)
  if (!res.ok) throw new Error('Failed to fetch signals')
  return res.json()
}

async function createSignal(data: {
  signal_name: string
  description?: string
  trigger_condition: TriggerCondition
  enabled: boolean
}): Promise<SignalDefinition> {
  const res = await fetch(`${API_BASE}/definitions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('Failed to create signal')
  return res.json()
}

async function updateSignal(id: number, data: Partial<{
  signal_name: string
  description: string
  trigger_condition: TriggerCondition
  enabled: boolean
}>): Promise<SignalDefinition> {
  const res = await fetch(`${API_BASE}/definitions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('Failed to update signal')
  return res.json()
}

async function deleteSignal(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/definitions/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete signal')
}

async function createPool(data: {
  pool_name: string
  signal_ids: number[]
  symbols: string[]
  enabled: boolean
  logic: string
}): Promise<SignalPool> {
  const res = await fetch(`${API_BASE}/pools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('Failed to create pool')
  return res.json()
}

async function updatePool(id: number, data: Partial<{
  pool_name: string
  signal_ids: number[]
  symbols: string[]
  enabled: boolean
  logic: string
}>): Promise<SignalPool> {
  const res = await fetch(`${API_BASE}/pools/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('Failed to update pool')
  return res.json()
}

async function deletePool(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/pools/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete pool')
}

async function createPoolFromTemplate(template: SignalTemplate, symbols: string[]): Promise<{
  success: boolean
  pool: SignalPool
  signals: SignalDefinition[]
}> {
  const res = await fetch(`${API_BASE}/create-pool-from-config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: template.name,
      symbols: symbols,
      description: template.description,
      logic: template.logic,
      signals: template.signals.map((s, i) => ({
        name: `${template.name}_${i + 1}`,
        metric: s.metric,
        operator: s.operator,
        threshold: s.threshold,
        time_window: s.time_window,
        description: s.description
      }))
    })
  })
  if (!res.ok) throw new Error('Failed to create pool from template')
  return res.json()
}

async function fetchPoolStats(poolId: number, days: number = 7): Promise<PoolStats> {
  const res = await fetch(`${API_BASE}/pools/${poolId}/stats?days=${days}`)
  if (!res.ok) {
    // 返回默认统计数据
    return {
      total_triggers: 0,
      triggers_today: 0,
      trigger_distribution: {},
      signal_stats: []
    }
  }
  return res.json()
}

// ==================== 主组件 ====================

export default function ModernSignalManager() {
  const { t, i18n } = useTranslation()
  const isZh = i18n.language?.startsWith('zh')

  // 统一交易对配置
  const { symbols: configuredPairs } = useTradingPairs()
  const SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  // 数据状态
  const [signals, setSignals] = useState<SignalDefinition[]>([])
  const [pools, setPools] = useState<SignalPool[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // UI 状态
  const [selectedPool, setSelectedPool] = useState<SignalPool | null>(null)
  const [selectedPoolStats, setSelectedPoolStats] = useState<PoolStats | null>(null)
  const [selectedSignal, setSelectedSignal] = useState<SignalDefinition | null>(null)  // 信号选择高亮
  const [searchQuery, setSearchQuery] = useState('')
  const [filterCategory, setFilterCategory] = useState<string>('all')

  // 对话框状态
  const [poolDialogOpen, setPoolDialogOpen] = useState(false)
  const [signalDialogOpen, setSignalDialogOpen] = useState(false)
  const [templateDialogOpen, setTemplateDialogOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<{ type: 'signal' | 'pool', id: number } | null>(null)

  // 表单状态
  const [editingPool, setEditingPool] = useState<SignalPool | null>(null)
  const [editingSignal, setEditingSignal] = useState<SignalDefinition | null>(null)
  const [poolForm, setPoolForm] = useState({
    pool_name: '',
    signal_ids: [] as number[],
    symbols: SYMBOLS.slice(0, 3) as string[],
    enabled: true,
    logic: 'OR' as 'OR' | 'AND' | 'WEIGHTED'
  })
  const [signalForm, setSignalForm] = useState({
    signal_name: '',
    description: '',
    metric: 'order_imbalance',
    operator: 'greater_than',
    threshold: 0.5,
    time_window: '1h',
    enabled: true
  })
  const [saving, setSaving] = useState(false)

  // 模板选择状态
  const [selectedTemplate, setSelectedTemplate] = useState<SignalTemplate | null>(null)
  const [templateSymbols, setTemplateSymbols] = useState<string[]>(SYMBOLS.slice(0, 3))

  // AI 信号生成状态
  const [aiDialogOpen, setAiDialogOpen] = useState(false)
  const [accounts, setAccounts] = useState<TradingAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)

  // 使用全局回测 Context
  const { startBacktest: globalStartBacktest } = useBacktest()

  // 保留旧状态用于其他地方兼容
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [backtestResult, setBacktestResult] = useState<any>(null)
  const [backtestTarget, setBacktestTarget] = useState<{ type: 'signal' | 'pool', id: number, symbol: string } | null>(null)

  // 回测参数配置对话框状态
  const [backtestConfigOpen, setBacktestConfigOpen] = useState(false)
  const [backtestConfig, setBacktestConfig] = useState({
    type: 'pool' as 'signal' | 'pool',
    id: 0,
    symbol: 'BTC',
    days: 7
  })

  // 可选交易对列表
  const availableSymbols = SYMBOLS
  // 可选时间范围
  const availableDays = [
    { value: 1, label: '1天' },
    { value: 3, label: '3天' },
    { value: 7, label: '7天' },
    { value: 14, label: '14天' },
    { value: 30, label: '30天' },
    { value: 90, label: '90天' }
  ]

  // 加载账户数据（用于 AI 生成）
  const loadAccounts = useCallback(async () => {
    setAccountsLoading(true)
    try {
      const data = await getAccounts()
      setAccounts(data)
    } catch (error) {
      console.error('Failed to load accounts:', error)
    } finally {
      setAccountsLoading(false)
    }
  }, [])

  // AI 创建信号处理
  const handleAiCreateSignal = async (config: any): Promise<boolean> => {
    try {
      const data = {
        signal_name: config.name,
        description: config.description,
        trigger_condition: config.trigger_condition,
        enabled: true
      }
      await createSignal(data)
      await loadData()
      toast.success(t('signals.signalCreated', '信号已创建'))
      return true
    } catch (error) {
      toast.error(t('signals.createError', '创建失败'))
      return false
    }
  }

  // 运行回测
  const runBacktest = async (config: any = {}) => {
    if (!backtestTarget) return
    
    setBacktestLoading(true)
    try {
      const url = backtestTarget.type === 'pool'
        ? `${API_BASE}/pools/${backtestTarget.id}/backtest-performance`
        : `${API_BASE}/backtest/${backtestTarget.id}/performance`
      
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: backtestTarget.symbol,
          days: 7,
          config: config.position_size_usd ? config : undefined
        })
      })
      
      if (!res.ok) throw new Error('Backtest failed')
      const result = await res.json()
      setBacktestResult(result)
    } catch (error) {
      console.error('Backtest error:', error)
      toast.error(t('signals.backtestError', '回测失败'))
      setBacktestResult({ error: '回测失败，请稍后重试' })
    } finally {
      setBacktestLoading(false)
    }
  }

  // 打开回测配置对话框（不直接执行）
  const openBacktest = (type: 'signal' | 'pool', id: number, defaultSymbol: string = 'BTC') => {
    setBacktestConfig({
      type,
      id,
      symbol: defaultSymbol,
      days: 7
    })
    setBacktestConfigOpen(true)
  }
  
  // 执行回测（从配置对话框触发 - 使用全局 Context）
  const executeBacktest = () => {
    setBacktestConfigOpen(false)
    
    // 获取目标名称
    let targetName = ''
    if (backtestConfig.type === 'pool') {
      const pool = pools.find(p => p.id === backtestConfig.id)
      targetName = pool?.pool_name || `信号池 #${backtestConfig.id}`
    } else {
      const signal = signals.find(s => s.id === backtestConfig.id)
      targetName = signal?.signal_name || `信号 #${backtestConfig.id}`
    }
    
    // 使用全局 Context 启动回测
    globalStartBacktest({
      type: backtestConfig.type,
      id: backtestConfig.id,
      symbol: backtestConfig.symbol,
      days: backtestConfig.days,
      targetName
    })
  }
  
  // 使用指定天数运行回测
  const runBacktestWithDays = async (config: { type: string, id: number, symbol: string, days: number }) => {
    setBacktestLoading(true)
    try {
      const url = config.type === 'pool'
        ? `${API_BASE}/pools/${config.id}/backtest-performance`
        : `${API_BASE}/backtest/${config.id}/performance`
      
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: config.symbol,
          days: config.days
        })
      })
      
      if (!res.ok) throw new Error('Backtest failed')
      const result = await res.json()
      setBacktestResult(result)
    } catch (error) {
      console.error('Backtest error:', error)
      toast.error(t('signals.backtestError', '回测失败'))
      setBacktestResult({ error: '回测失败，请稍后重试' })
    } finally {
      setBacktestLoading(false)
    }
  }

  // 使用目标运行回测
  const runBacktestWithTarget = async (target: { type: string, id: number, symbol: string }, config: any = {}) => {
    setBacktestLoading(true)
    try {
      const url = target.type === 'pool'
        ? `${API_BASE}/pools/${target.id}/backtest-performance`
        : `${API_BASE}/backtest/${target.id}/performance`
      
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: target.symbol,
          days: 7,
          config: config.position_size_usd ? config : undefined
        })
      })
      
      if (!res.ok) throw new Error('Backtest failed')
      const result = await res.json()
      setBacktestResult(result)
    } catch (error) {
      console.error('Backtest error:', error)
      toast.error(t('signals.backtestError', '回测失败'))
      setBacktestResult({ error: '回测失败，请稍后重试' })
    } finally {
      setBacktestLoading(false)
    }
  }

  // 关闭回测
  const closeBacktest = () => {
    setBacktestTarget(null)
    setBacktestResult(null)
  }

  // AI 创建信号池处理
  const handleAiCreatePool = async (config: any): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/create-pool-from-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      })
      if (!res.ok) throw new Error('Failed to create pool')
      await loadData()
      toast.success(t('signals.poolCreated', '信号池已创建'))
      return true
    } catch (error) {
      toast.error(t('signals.createError', '创建失败'))
      return false
    }
  }

  // AI 预览信号处理
  const handleAiPreviewSignal = (config: any) => {
    console.log('Preview signal:', config)
    toast.success(t('signals.previewReady', '预览已准备'))
  }

  // 加载数据
  const loadData = useCallback(async () => {
    try {
      const data = await fetchSignals()
      setSignals(data.signals)
      setPools(data.pools)
    } catch (error) {
      console.error('Failed to load signals:', error)
      toast.error(t('signals.loadError', '加载信号数据失败'))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [t])

  useEffect(() => {
    loadData()
  }, [loadData])

  // 加载选中池的统计数据
  useEffect(() => {
    if (selectedPool) {
      fetchPoolStats(selectedPool.id).then(setSelectedPoolStats).catch(() => setSelectedPoolStats(null))
    } else {
      setSelectedPoolStats(null)
    }
  }, [selectedPool])

  // 刷新数据
  const handleRefresh = async () => {
    setRefreshing(true)
    await loadData()
  }

  // 过滤信号池
  const filteredPools = pools.filter(pool =>
    pool.pool_name.toLowerCase().includes(searchQuery.toLowerCase())
  )

  // 获取池中的信号
  const getPoolSignals = (pool: SignalPool): SignalDefinition[] => {
    return pool.signal_ids.map(id => signals.find(s => s.id === id)).filter(Boolean) as SignalDefinition[]
  }

  // 统计数据
  const stats = {
    totalPools: pools.length,
    activePools: pools.filter(p => p.enabled).length,
    totalSignals: signals.length,
    activeSignals: signals.filter(s => s.enabled).length
  }

  // ==================== 信号池操作 ====================

  const openCreatePoolDialog = () => {
    setEditingPool(null)
    setPoolForm({
      pool_name: '',
      signal_ids: [],
      symbols: SYMBOLS.slice(0, 3),
      enabled: true,
      logic: 'OR'
    })
    setPoolDialogOpen(true)
  }

  const openEditPoolDialog = (pool: SignalPool) => {
    setEditingPool(pool)
    setPoolForm({
      pool_name: pool.pool_name,
      signal_ids: pool.signal_ids,
      symbols: pool.symbols,
      enabled: pool.enabled,
      logic: pool.logic
    })
    setPoolDialogOpen(true)
  }

  const handleSavePool = async () => {
    if (!poolForm.pool_name.trim()) {
      toast.error(t('signals.poolNameRequired', '请输入信号池名称'))
      return
    }
    setSaving(true)
    try {
      if (editingPool) {
        const updated = await updatePool(editingPool.id, poolForm)
        setPools(prev => prev.map(p => p.id === updated.id ? updated : p))
        if (selectedPool?.id === updated.id) {
          setSelectedPool(updated)
        }
        toast.success(t('signals.poolUpdated', '信号池已更新'))
      } else {
        const created = await createPool(poolForm)
        setPools(prev => [...prev, created])
        toast.success(t('signals.poolCreated', '信号池已创建'))
      }
      setPoolDialogOpen(false)
    } catch (error) {
      toast.error(t('signals.saveError', '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const handleTogglePool = async (pool: SignalPool, enabled: boolean) => {
    try {
      // 更新信号池状态
      const updated = await updatePool(pool.id, { enabled })
      
      // 更新本地信号池状态
      setPools(prev => prev.map(p => p.id === updated.id ? updated : p))
      if (selectedPool?.id === updated.id) {
        setSelectedPool(updated)
      }
      
      // 联动更新池内所有信号的状态
      if (pool.signal_ids && pool.signal_ids.length > 0) {
        const updatePromises = pool.signal_ids.map(async (signalId) => {
          try {
            return await updateSignal(signalId, { enabled })
          } catch (error) {
            console.error(`Failed to update signal ${signalId}:`, error)
            return null
          }
        })
        
        const updatedSignals = await Promise.all(updatePromises)
        
        // 更新本地信号状态
        setSignals(prev => prev.map(signal => {
          const updatedSignal = updatedSignals.find(s => s && s.id === signal.id)
          return updatedSignal || signal
        }))
        
        toast.success(
          enabled 
            ? t('signals.poolAndSignalsEnabled', '信号池及其信号已启用')
            : t('signals.poolAndSignalsDisabled', '信号池及其信号已禁用')
        )
      } else {
        // 信号池没有信号时也显示提示
        toast.success(
          enabled 
            ? t('signals.poolEnabled', '信号池已启用')
            : t('signals.poolDisabled', '信号池已禁用')
        )
      }
    } catch (error) {
      console.error('Toggle pool error:', error)
      toast.error(t('signals.toggleError', '切换状态失败'))
    }
  }

  const handleDeletePool = async () => {
    if (!deleteTarget || deleteTarget.type !== 'pool') return
    try {
      await deletePool(deleteTarget.id)
      setPools(prev => prev.filter(p => p.id !== deleteTarget.id))
      if (selectedPool?.id === deleteTarget.id) {
        setSelectedPool(null)
      }
      toast.success(t('signals.poolDeleted', '信号池已删除'))
    } catch (error) {
      toast.error(t('signals.deleteError', '删除失败'))
    } finally {
      setDeleteConfirmOpen(false)
      setDeleteTarget(null)
    }
  }

  // ==================== 信号操作 ====================

  const openCreateSignalDialog = () => {
    setEditingSignal(null)
    setSignalForm({
      signal_name: '',
      description: '',
      metric: 'order_imbalance',
      operator: 'greater_than',
      threshold: 0.5,
      time_window: '1h',
      enabled: true
    })
    setSignalDialogOpen(true)
  }

  const openEditSignalDialog = (signal: SignalDefinition) => {
    setEditingSignal(signal)
    setSignalForm({
      signal_name: signal.signal_name,
      description: signal.description || '',
      metric: signal.trigger_condition.metric,
      operator: signal.trigger_condition.operator,
      threshold: signal.trigger_condition.threshold,
      time_window: signal.trigger_condition.time_window,
      enabled: signal.enabled
    })
    setSignalDialogOpen(true)
  }

  const handleSaveSignal = async () => {
    if (!signalForm.signal_name.trim()) {
      toast.error(t('signals.signalNameRequired', '请输入信号名称'))
      return
    }
    setSaving(true)
    try {
      const data = {
        signal_name: signalForm.signal_name,
        description: signalForm.description || undefined,
        trigger_condition: {
          metric: signalForm.metric,
          operator: signalForm.operator,
          threshold: signalForm.threshold,
          time_window: signalForm.time_window
        },
        enabled: signalForm.enabled
      }
      if (editingSignal) {
        const updated = await updateSignal(editingSignal.id, data)
        setSignals(prev => prev.map(s => s.id === updated.id ? updated : s))
        toast.success(t('signals.signalUpdated', '信号已更新'))
      } else {
        const created = await createSignal(data)
        setSignals(prev => [...prev, created])
        toast.success(t('signals.signalCreated', '信号已创建'))
      }
      setSignalDialogOpen(false)
    } catch (error) {
      toast.error(t('signals.saveError', '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const handleToggleSignal = async (signal: SignalDefinition, enabled: boolean) => {
    try {
      const updated = await updateSignal(signal.id, { enabled })
      setSignals(prev => prev.map(s => s.id === updated.id ? updated : s))
    } catch (error) {
      toast.error(t('signals.toggleError', '切换状态失败'))
    }
  }

  const handleDeleteSignal = async () => {
    if (!deleteTarget || deleteTarget.type !== 'signal') return
    try {
      await deleteSignal(deleteTarget.id)
      setSignals(prev => prev.filter(s => s.id !== deleteTarget.id))
      toast.success(t('signals.signalDeleted', '信号已删除'))
    } catch (error) {
      toast.error(t('signals.deleteError', '删除失败'))
    } finally {
      setDeleteConfirmOpen(false)
      setDeleteTarget(null)
    }
  }

  // ==================== 模板操作 ====================

  const handleApplyTemplate = async () => {
    if (!selectedTemplate) return
    if (templateSymbols.length === 0) {
      toast.error(t('signals.selectSymbols', '请选择交易对'))
      return
    }
    setSaving(true)
    try {
      const result = await createPoolFromTemplate(selectedTemplate, templateSymbols)
      if (result.success) {
        await loadData() // 重新加载数据
        toast.success(t('signals.templateApplied', '模板已应用'))
        setTemplateDialogOpen(false)
        setSelectedTemplate(null)
      }
    } catch (error) {
      toast.error(t('signals.templateError', '应用模板失败'))
    } finally {
      setSaving(false)
    }
  }

  // ==================== 渲染 ====================

  if (loading) {
    return (
      <div className="w-full min-h-full flex items-center justify-center bg-gradient-to-br from-slate-50 via-white to-slate-100 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
          <span className="text-slate-500">{t('signals.loading', '加载中...')}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full min-h-full bg-gradient-to-br from-slate-50 via-white to-slate-100 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950 p-4 md:p-6 lg:p-8">
      {/* 页面头部 */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-3xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
              {t('signals.title', '信号管理系统')}
            </h1>
            <p className="text-slate-500 dark:text-slate-400 mt-2">
              {t('signals.subtitle', '配置和管理AI交易信号触发条件')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshing}
              className="bg-white dark:bg-slate-800/50"
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
              {t('signals.refresh', '刷新')}
            </Button>
            <Button
              variant="outline"
              onClick={() => setTemplateDialogOpen(true)}
              className="bg-white dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800/70"
            >
              <LayoutTemplate className="w-4 h-4 mr-2" />
              {t('signals.templates', '模板库')}
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                loadAccounts()
                setAiDialogOpen(true)
              }}
              className="bg-gradient-to-r from-blue-500 to-cyan-600 hover:from-blue-600 hover:to-cyan-700 text-white border-0"
            >
              <Sparkles className="w-4 h-4 mr-2" />
              {t('signals.aiGenerate', 'AI智能生成')}
            </Button>
            <Button
              variant="outline"
              onClick={openCreateSignalDialog}
              className="bg-white dark:bg-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-800/70"
            >
              <Plus className="w-4 h-4 mr-2" />
              {t('signals.newSignal', '新建信号')}
            </Button>
            <Button
              onClick={openCreatePoolDialog}
              className="bg-gradient-to-r from-purple-500 to-pink-600 hover:from-purple-600 hover:to-pink-700 text-white shadow-lg shadow-purple-500/50"
            >
              <Plus className="w-4 h-4 mr-2" />
              {t('signals.newPool', '新建信号池')}
            </Button>
          </div>
        </div>

        {/* 搜索和筛选 */}
        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500 dark:text-slate-400" />
            <input
              type="text"
              placeholder={t('signals.searchPlaceholder', '搜索信号池...')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2.5 rounded-lg bg-white dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50 text-slate-800 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-purple-500/50"
            />
          </div>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400 mb-1">{t('signals.stats.totalPools', '总信号池')}</p>
                <p className="text-2xl font-bold">{stats.totalPools}</p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500/20 to-purple-600/20 flex items-center justify-center">
                <Activity className="w-6 h-6 text-blue-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400 mb-1">{t('signals.stats.active', '活跃中')}</p>
                <p className="text-2xl font-bold text-emerald-400">{stats.activePools}</p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-emerald-500/20 to-emerald-600/20 flex items-center justify-center">
                <CheckCircle2 className="w-6 h-6 text-emerald-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400 mb-1">{t('signals.stats.totalSignals', '总信号数')}</p>
                <p className="text-2xl font-bold text-purple-400">{stats.totalSignals}</p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-purple-500/20 to-pink-600/20 flex items-center justify-center">
                <Zap className="w-6 h-6 text-purple-400" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-slate-400 mb-1">{t('signals.stats.activeSignals', '启用信号')}</p>
                <p className="text-2xl font-bold">{stats.activeSignals}</p>
              </div>
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-orange-500/20 to-yellow-600/20 flex items-center justify-center">
                <Target className="w-6 h-6 text-orange-400" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 主内容区 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 左侧：信号池列表 */}
        <div className="lg:col-span-1 space-y-4">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-purple-400" />
            {t('signals.poolList', '信号池列表')}
          </h2>

          {filteredPools.length === 0 ? (
            <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
              <CardContent className="p-6 text-center">
                <p className="text-slate-500">{t('signals.noPools', '暂无信号池')}</p>
                <Button
                  variant="outline"
                  className="mt-4"
                  onClick={() => setTemplateDialogOpen(true)}
                >
                  <LayoutTemplate className="w-4 h-4 mr-2" />
                  {t('signals.fromTemplate', '从模板创建')}
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="flex-1 overflow-y-auto min-h-0">
              <div className="space-y-4 pr-4">
                {filteredPools.map((pool) => (
                  <Card
                    key={pool.id}
                    className={`backdrop-blur-xl cursor-pointer transition-all ${
                      selectedPool?.id === pool.id
                        ? 'bg-gradient-to-br from-purple-500/20 to-pink-600/20 border-purple-500/50'
                        : 'bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50 hover:bg-slate-100 dark:hover:bg-slate-900/70'
                    }`}
                    onClick={() => setSelectedPool(pool)}
                  >
                    <CardContent className="p-6">
                      <div className="flex items-start justify-between mb-4">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-2">
                            <h3 className="text-lg font-bold">{pool.pool_name}</h3>
                            {pool.enabled && (
                              <span className="relative flex h-3 w-3">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mb-3 flex-wrap">
                            {pool.symbols.slice(0, 3).map((symbol) => (
                              <Badge key={symbol} variant="outline" className="border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 text-xs">
                                {symbol}
                              </Badge>
                            ))}
                            {pool.symbols.length > 3 && (
                              <Badge variant="outline" className="border-slate-300 dark:border-slate-600 text-slate-500 text-xs">
                                +{pool.symbols.length - 3}
                              </Badge>
                            )}
                            <Badge variant="outline" className={`text-xs ${
                              pool.logic === 'AND' ? 'border-blue-400 text-blue-400' :
                              pool.logic === 'WEIGHTED' ? 'border-orange-400 text-orange-400' :
                              'border-green-400 text-green-400'
                            }`}>
                              {pool.logic}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
                            <span className="flex items-center gap-1">
                              <Zap className="w-3 h-3" />
                              {pool.signal_ids.length} {t('signals.signals', '信号')}
                            </span>
                          </div>
                        </div>
                        <Switch
                          checked={pool.enabled}
                          onCheckedChange={(checked) => handleTogglePool(pool, checked)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 右侧：信号详情 */}
        <div className="lg:col-span-2">
          {selectedPool ? (
            <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
              <CardHeader className="border-b border-slate-200 dark:border-slate-800/50">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-xl">{selectedPool.pool_name}</CardTitle>
                    <div className="flex items-center gap-2 mt-2">
                      {selectedPool.symbols.map((symbol) => (
                        <Badge key={symbol} variant="secondary" className="text-xs">
                          {symbol}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openBacktest('pool', selectedPool.id, selectedPool.symbols[0] || 'BTC')}
                      className="bg-gradient-to-r from-emerald-500/20 to-teal-500/20 hover:from-emerald-500/30 hover:to-teal-500/30 border-emerald-500/50 text-emerald-400"
                    >
                      <BarChart3 className="w-4 h-4 mr-2" />
                      {t('signals.backtest', '回测分析')}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => openEditPoolDialog(selectedPool)}>
                      <Edit className="w-4 h-4 mr-2" />
                      {t('signals.edit', '编辑')}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
                      onClick={() => {
                        setDeleteTarget({ type: 'pool', id: selectedPool.id })
                        setDeleteConfirmOpen(true)
                      }}
                    >
                      <Trash2 className="w-4 h-4 mr-2" />
                      {t('signals.delete', '删除')}
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="p-6">
                {/* 统计信息 */}
                {selectedPoolStats && (
                  <div className="grid grid-cols-3 gap-4 mb-6">
                    <div className="p-4 rounded-xl bg-slate-100/50 dark:bg-slate-800/30">
                      <p className="text-xs text-slate-500 mb-1">{t('signals.stats.totalTriggers', '总触发次数')}</p>
                      <p className="text-xl font-bold">{selectedPoolStats.total_triggers}</p>
                    </div>
                    <div className="p-4 rounded-xl bg-slate-100/50 dark:bg-slate-800/30">
                      <p className="text-xs text-slate-500 mb-1">{t('signals.stats.todayTriggers', '今日触发')}</p>
                      <p className="text-xl font-bold text-purple-400">{selectedPoolStats.triggers_today}</p>
                    </div>
                    <div className="p-4 rounded-xl bg-slate-100/50 dark:bg-slate-800/30">
                      <p className="text-xs text-slate-500 mb-1">{t('signals.logic', '组合逻辑')}</p>
                      <p className="text-xl font-bold">{selectedPool.logic}</p>
                    </div>
                  </div>
                )}

                {/* 信号列表 */}
                <div className="space-y-4">
                  <h3 className="text-lg font-semibold flex items-center gap-2">
                    <Sparkles className="w-5 h-5 text-purple-400" />
                    {t('signals.signalDefinitions', '信号定义')}
                  </h3>

                  {getPoolSignals(selectedPool).length === 0 ? (
                    <div className="p-6 text-center text-slate-500">
                      {t('signals.noSignalsInPool', '该信号池暂无信号')}
                    </div>
                  ) : (
                    getPoolSignals(selectedPool).map((signal) => (
                      <div
                        key={signal.id}
                        onClick={() => setSelectedSignal(signal)}
                        className={`p-4 rounded-xl cursor-pointer transition-all border ${
                          selectedSignal?.id === signal.id
                            ? 'bg-gradient-to-br from-purple-500/20 to-pink-500/20 border-purple-500/50 shadow-lg shadow-purple-500/10'
                            : 'bg-slate-100/50 dark:bg-slate-800/30 border-slate-200 dark:border-slate-700/50 hover:bg-slate-200/50 dark:hover:bg-slate-800/50'
                        }`}
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-3 mb-2">
                              <h4 className="text-lg font-semibold">{signal.signal_name}</h4>
                              {signal.enabled ? (
                                <Badge className="bg-emerald-500/20 text-emerald-400 border-emerald-500/30">
                                  <CheckCircle2 className="w-3 h-3 mr-1" />
                                  {t('signals.enabled', '启用')}
                                </Badge>
                              ) : (
                                <Badge className="bg-slate-700/50 text-slate-400 border-slate-600/30">
                                  <XCircle className="w-3 h-3 mr-1" />
                                  {t('signals.disabled', '禁用')}
                                </Badge>
                              )}
                            </div>
                            {signal.description && (
                              <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">{signal.description}</p>
                            )}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                              <div>
                                <p className="text-slate-500 mb-1">{t('signals.metric', '指标')}</p>
                                <p className="font-medium">{signal.trigger_condition.metric}</p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">{t('signals.operator', '运算符')}</p>
                                <p className="font-medium">{signal.trigger_condition.operator}</p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">{t('signals.threshold', '阈值')}</p>
                                <p className="font-medium">{signal.trigger_condition.threshold}</p>
                              </div>
                              <div>
                                <p className="text-slate-500 mb-1">{t('signals.timeWindow', '时间窗口')}</p>
                                <p className="font-medium">{signal.trigger_condition.time_window}</p>
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                            <Button
                              variant="outline"
                              size="icon"
                              className="bg-gradient-to-r from-emerald-500/20 to-teal-500/20 hover:from-emerald-500/30 hover:to-teal-500/30 border-emerald-500/50 text-emerald-400 h-8 w-8"
                              onClick={() => openBacktest('signal', signal.id, selectedPool.symbols[0] || 'BTC')}
                              title={t('signals.backtest', '回测')}
                            >
                              <BarChart3 className="w-4 h-4" />
                            </Button>
                            <Button variant="ghost" size="icon" onClick={() => openEditSignalDialog(signal)} className="h-8 w-8">
                              <Edit className="w-4 h-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 h-8 w-8"
                              onClick={() => {
                                setDeleteTarget({ type: 'signal', id: signal.id })
                                setDeleteConfirmOpen(true)
                              }}
                              title={t('signals.delete', '删除')}
                            >
                              <Trash2 className="w-4 h-4" />
                            </Button>
                            <Switch
                              checked={signal.enabled}
                              onCheckedChange={(checked) => handleToggleSignal(signal, checked)}
                            />
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card className="backdrop-blur-xl bg-white/70 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800/50">
              <CardContent className="p-12 flex flex-col items-center justify-center text-center">
                <div className="w-20 h-20 rounded-full bg-slate-200 dark:bg-slate-800/50 flex items-center justify-center mb-4">
                  <Activity className="w-10 h-10 text-slate-400 dark:text-slate-600" />
                </div>
                <h3 className="text-xl font-semibold mb-2">{t('signals.selectPool', '选择信号池')}</h3>
                <p className="text-slate-500 dark:text-slate-400">
                  {t('signals.selectPoolHint', '从左侧列表中选择一个信号池查看详情')}
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* ==================== 信号池编辑对话框 ==================== */}
      <Dialog open={poolDialogOpen} onOpenChange={setPoolDialogOpen}>
        <DialogContent className="sm:max-w-[600px]">
          <DialogHeader>
            <DialogTitle>
              {editingPool ? t('signals.editPool', '编辑信号池') : t('signals.createPool', '创建信号池')}
            </DialogTitle>
            <DialogDescription>
              {t('signals.poolDialogDesc', '配置信号池名称、包含的信号和触发逻辑')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="pool_name">{t('signals.poolName', '信号池名称')}</Label>
              <Input
                id="pool_name"
                value={poolForm.pool_name}
                onChange={(e) => setPoolForm(prev => ({ ...prev, pool_name: e.target.value }))}
                placeholder={t('signals.poolNamePlaceholder', '输入信号池名称')}
              />
            </div>

            <div className="grid gap-2">
              <Label>{t('signals.selectSignals', '选择信号')}</Label>
              <ScrollArea className="h-40 rounded-md border p-4">
                {signals.length === 0 ? (
                  <p className="text-sm text-slate-500">{t('signals.noSignalsAvailable', '暂无可用信号')}</p>
                ) : (
                  <div className="space-y-2">
                    {signals.map((signal) => (
                      <div key={signal.id} className="flex items-center space-x-2">
                        <input
                          type="checkbox"
                          id={`signal-${signal.id}`}
                          checked={poolForm.signal_ids.includes(signal.id)}
                          onChange={(e) => {
                            const checked = e.target.checked
                            setPoolForm(prev => ({
                              ...prev,
                              signal_ids: checked
                                ? [...prev.signal_ids, signal.id]
                                : prev.signal_ids.filter(id => id !== signal.id)
                            }))
                          }}
                          className="w-4 h-4 rounded border-slate-300 text-purple-500 focus:ring-purple-500"
                        />
                        <label
                          htmlFor={`signal-${signal.id}`}
                          className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                        >
                          {signal.signal_name}
                          <span className="text-xs text-slate-500 ml-2">
                            ({signal.trigger_condition.metric} {signal.trigger_condition.operator} {signal.trigger_condition.threshold})
                          </span>
                        </label>
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            </div>

            <div className="grid gap-2">
              <Label>{t('signals.selectSymbols', '选择交易对')}</Label>
              <div className="flex flex-wrap gap-2">
                {SYMBOLS.slice(0, 6).map((symbol) => (
                  <Badge
                    key={symbol}
                    variant={poolForm.symbols.includes(symbol) ? 'default' : 'outline'}
                    className="cursor-pointer"
                    onClick={() => {
                      setPoolForm(prev => ({
                        ...prev,
                        symbols: prev.symbols.includes(symbol)
                          ? prev.symbols.filter(s => s !== symbol)
                          : [...prev.symbols, symbol]
                      }))
                    }}
                  >
                    {symbol}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="grid gap-2">
              <Label>{t('signals.logic', '组合逻辑')}</Label>
              <Select
                value={poolForm.logic}
                onValueChange={(v) => setPoolForm(prev => ({ ...prev, logic: v as 'OR' | 'AND' | 'WEIGHTED' }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="OR">{t('signals.orLogic', 'OR - 任意信号触发')}</SelectItem>
                  <SelectItem value="AND">{t('signals.andLogic', 'AND - 所有信号同时触发')}</SelectItem>
                  <SelectItem value="WEIGHTED">{t('signals.weightedLogic', 'WEIGHTED - 权重组合')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                id="pool_enabled"
                checked={poolForm.enabled}
                onCheckedChange={(checked) => setPoolForm(prev => ({ ...prev, enabled: checked }))}
              />
              <Label htmlFor="pool_enabled">{t('signals.enablePool', '启用信号池')}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPoolDialogOpen(false)} disabled={saving}>
              {t('signals.cancel', '取消')}
            </Button>
            <Button onClick={handleSavePool} disabled={saving}>
              {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
              {t('signals.save', '保存')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ==================== 信号编辑对话框 ==================== */}
      <Dialog open={signalDialogOpen} onOpenChange={setSignalDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle>
              {editingSignal ? t('signals.editSignal', '编辑信号') : t('signals.createSignal', '创建信号')}
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="signal_name">{t('signals.signalName', '信号名称')}</Label>
              <Input
                id="signal_name"
                value={signalForm.signal_name}
                onChange={(e) => setSignalForm(prev => ({ ...prev, signal_name: e.target.value }))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="description">{t('signals.description', '描述')}</Label>
              <Textarea
                id="description"
                value={signalForm.description}
                onChange={(e) => setSignalForm(prev => ({ ...prev, description: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>{t('signals.metric', '指标')}</Label>
                <Select
                  value={signalForm.metric}
                  onValueChange={(v) => setSignalForm(prev => ({ ...prev, metric: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AVAILABLE_METRICS.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {isZh ? m.name : m.nameEn}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>{t('signals.operator', '运算符')}</Label>
                <Select
                  value={signalForm.operator}
                  onValueChange={(v) => setSignalForm(prev => ({ ...prev, operator: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AVAILABLE_OPERATORS.map((o) => (
                      <SelectItem key={o.id} value={o.id}>
                        {o.symbol} {isZh ? o.name : o.nameEn}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>{t('signals.threshold', '阈值')}</Label>
                <Input
                  type="number"
                  step="any"
                  value={signalForm.threshold}
                  onChange={(e) => setSignalForm(prev => ({ ...prev, threshold: parseFloat(e.target.value) || 0 }))}
                />
              </div>
              <div className="grid gap-2">
                <Label>{t('signals.timeWindow', '时间窗口')}</Label>
                <Select
                  value={signalForm.time_window}
                  onValueChange={(v) => setSignalForm(prev => ({ ...prev, time_window: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AVAILABLE_TIME_WINDOWS.map((tw) => (
                      <SelectItem key={tw.id} value={tw.id}>
                        {isZh ? tw.name : tw.nameEn}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center space-x-2">
              <Switch
                id="signal_enabled"
                checked={signalForm.enabled}
                onCheckedChange={(checked) => setSignalForm(prev => ({ ...prev, enabled: checked }))}
              />
              <Label htmlFor="signal_enabled">{t('signals.enableSignal', '启用信号')}</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSignalDialogOpen(false)} disabled={saving}>
              {t('signals.cancel', '取消')}
            </Button>
            <Button onClick={handleSaveSignal} disabled={saving}>
              {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
              {t('signals.save', '保存')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ==================== 模板选择对话框 ==================== */}
      <Dialog open={templateDialogOpen} onOpenChange={setTemplateDialogOpen}>
        <DialogContent className="sm:max-w-[900px] max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <LayoutTemplate className="w-5 h-5 text-purple-400" />
              {t('signals.templateLibrary', '信号模板库')}
            </DialogTitle>
            <DialogDescription>
              {t('signals.templateDesc', '选择预置的交易策略模板快速创建信号池')}
            </DialogDescription>
          </DialogHeader>
          <Tabs defaultValue="trend" className="w-full">
            <TabsList className="grid w-full grid-cols-4">
              {TEMPLATE_CATEGORIES.map((cat) => (
                <TabsTrigger key={cat.id} value={cat.id}>
                  {isZh ? cat.name : cat.nameEn}
                </TabsTrigger>
              ))}
            </TabsList>
            {TEMPLATE_CATEGORIES.map((cat) => (
              <TabsContent key={cat.id} value={cat.id}>
                <div className="flex-1 overflow-y-auto pr-4 min-h-0">
                  <div className="grid grid-cols-2 gap-4">
                    {SIGNAL_TEMPLATES.filter(t => t.category === cat.id).map((template) => (
                      <Card
                        key={template.id}
                        className={`cursor-pointer transition-all ${
                          selectedTemplate?.id === template.id
                            ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20'
                            : 'hover:border-slate-400'
                        }`}
                        onClick={() => setSelectedTemplate(template)}
                      >
                        <CardContent className="p-4">
                          <div className="flex items-start justify-between mb-2">
                            <h4 className="font-semibold">{isZh ? template.name : template.nameEn}</h4>
                            <Badge variant="outline" className={`text-xs ${
                              template.direction === 'long' ? 'text-green-500 border-green-500' :
                              template.direction === 'short' ? 'text-red-500 border-red-500' :
                              'text-blue-500 border-blue-500'
                            }`}>
                              {template.direction === 'long' ? '做多' :
                               template.direction === 'short' ? '做空' : '双向'}
                            </Badge>
                          </div>
                          <p className="text-sm text-slate-500 mb-3">
                            {isZh ? template.description : template.descriptionEn}
                          </p>
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge variant="secondary" className="text-xs">
                              {template.signals.length} 信号
                            </Badge>
                            <Badge variant="secondary" className="text-xs">
                              {template.logic}
                            </Badge>
                            <Badge variant="secondary" className={`text-xs ${
                              template.riskLevel === 'low' ? 'bg-green-100 text-green-600' :
                              template.riskLevel === 'high' ? 'bg-red-100 text-red-600' :
                              'bg-yellow-100 text-yellow-600'
                            }`}>
                              {template.riskLevel === 'low' ? '低风险' :
                               template.riskLevel === 'high' ? '高风险' : '中风险'}
                            </Badge>
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                </div>
              </TabsContent>
            ))}
          </Tabs>

          {selectedTemplate && (
            <div className="border-t pt-4 mt-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-semibold">{isZh ? selectedTemplate.name : selectedTemplate.nameEn}</p>
                  <div className="flex items-center gap-2 mt-2">
                    <Label className="text-sm">{t('signals.applyToSymbols', '应用到交易对:')}</Label>
                    <div className="flex gap-1">
                      {SYMBOLS.slice(0, 4).map((symbol) => (
                        <Badge
                          key={symbol}
                          variant={templateSymbols.includes(symbol) ? 'default' : 'outline'}
                          className="cursor-pointer"
                          onClick={() => {
                            setTemplateSymbols(prev =>
                              prev.includes(symbol)
                                ? prev.filter(s => s !== symbol)
                                : [...prev, symbol]
                            )
                          }}
                        >
                          {symbol}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>
                <Button onClick={handleApplyTemplate} disabled={saving}>
                  {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Copy className="w-4 h-4 mr-2" />}
                  {t('signals.applyTemplate', '应用模板')}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ==================== 删除确认对话框 ==================== */}
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent className="sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-500">
              <AlertCircle className="w-5 h-5" />
              {t('signals.confirmDelete', '确认删除')}
            </DialogTitle>
            <DialogDescription>
              {deleteTarget?.type === 'pool'
                ? t('signals.deletePoolWarning', '确定要删除此信号池吗？此操作不可撤销。')
                : t('signals.deleteSignalWarning', '确定要删除此信号吗？此操作不可撤销。')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirmOpen(false)}>
              {t('signals.cancel', '取消')}
            </Button>
            <Button
              variant="destructive"
              onClick={deleteTarget?.type === 'pool' ? handleDeletePool : handleDeleteSignal}
            >
              {t('signals.confirmDeleteBtn', '确认删除')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ==================== AI 信号生成对话框 ==================== */}
      <AiSignalChatModal
        open={aiDialogOpen}
        onOpenChange={setAiDialogOpen}
        onCreateSignal={handleAiCreateSignal}
        onCreatePool={handleAiCreatePool}
        onPreviewSignal={handleAiPreviewSignal}
        accounts={accounts}
        accountsLoading={accountsLoading}
      />

      {/* ==================== 回测参数配置对话框 ==================== */}
      <Dialog open={backtestConfigOpen} onOpenChange={setBacktestConfigOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-emerald-400" />
              {t('signals.backtestConfig', '回测参数配置')}
            </DialogTitle>
            <DialogDescription>
              {backtestConfig.type === 'pool'
                ? t('signals.backtestPoolDesc', '配置信号池回测参数，选择交易对和时间范围')
                : t('signals.backtestSignalDesc', '配置单个信号回测参数，选择交易对和时间范围')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-6 py-4">
            {/* 目标类型提示 */}
            <div className="p-3 rounded-lg bg-slate-100 dark:bg-slate-800/50">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                {t('signals.backtestTarget', '回测目标')}:
                <span className="font-medium text-slate-700 dark:text-slate-300 ml-2">
                  {backtestConfig.type === 'pool'
                    ? `${t('signals.pool', '信号池')} #${backtestConfig.id}`
                    : `${t('signals.signal', '信号')} #${backtestConfig.id}`}
                </span>
              </p>
            </div>

            {/* 交易对选择 */}
            <div className="grid gap-2">
              <Label className="flex items-center gap-2">
                <Target className="w-4 h-4 text-purple-400" />
                {t('signals.selectSymbol', '选择交易对')}
              </Label>
              <div className="flex flex-wrap gap-2">
                {availableSymbols.map((symbol) => (
                  <Badge
                    key={symbol}
                    variant={backtestConfig.symbol === symbol ? 'default' : 'outline'}
                    className={`cursor-pointer transition-all ${
                      backtestConfig.symbol === symbol
                        ? 'bg-blue-600 hover:bg-blue-700 border-2 border-blue-600 text-white shadow-md dark:bg-blue-500 dark:hover:bg-blue-600'
                        : 'hover:bg-slate-100 dark:hover:bg-slate-800 hover:border-blue-300'
                    }`}
                    onClick={() => setBacktestConfig(prev => ({ ...prev, symbol }))}
                  >
                    {symbol}
                  </Badge>
                ))}
              </div>
            </div>

            {/* 时间范围选择 */}
            <div className="grid gap-2">
              <Label className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-blue-400" />
                {t('signals.selectTimeRange', '选择时间范围')}
              </Label>
              <div className="flex flex-wrap gap-2">
                {availableDays.map((option) => (
                  <Badge
                    key={option.value}
                    variant={backtestConfig.days === option.value ? 'default' : 'outline'}
                    className={`cursor-pointer transition-all ${
                      backtestConfig.days === option.value
                        ? 'bg-gradient-to-r from-blue-500 to-cyan-500 border-0'
                        : 'hover:bg-slate-100 dark:hover:bg-slate-800'
                    }`}
                    onClick={() => setBacktestConfig(prev => ({ ...prev, days: option.value }))}
                  >
                    {option.label}
                  </Badge>
                ))}
              </div>
            </div>

            {/* 配置摘要 */}
            <div className="p-4 rounded-xl bg-gradient-to-br from-emerald-500/10 to-teal-500/10 border border-emerald-500/20">
              <h4 className="text-sm font-semibold text-emerald-500 mb-2">
                {t('signals.configSummary', '配置摘要')}
              </h4>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-slate-500">{t('signals.symbol', '交易对')}: </span>
                  <span className="font-medium">{backtestConfig.symbol}USDT</span>
                </div>
                <div>
                  <span className="text-slate-500">{t('signals.days', '天数')}: </span>
                  <span className="font-medium">{backtestConfig.days} {t('signals.daysUnit', '天')}</span>
                </div>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBacktestConfigOpen(false)}>
              {t('signals.cancel', '取消')}
            </Button>
            <Button
              onClick={executeBacktest}
              className="bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white"
            >
              <BarChart3 className="w-4 h-4 mr-2" />
              {t('signals.startBacktest', '开始回测')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 回测悬浮窗口已移至全局组件 BacktestFloatingProgress */}
    </div>
  )
}
