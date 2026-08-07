/**
 * BacktestContext - 全局回测任务状态管理
 * 支持跨页面显示回测进度和结果
 */

import { createContext, useContext, useState, useCallback, ReactNode } from 'react'
import { toast } from 'react-hot-toast'

// 回测任务状态接口
export interface BacktestTask {
  id: string
  type: 'signal' | 'pool'
  targetId: number
  targetName: string
  symbol: string
  days: number
  status: 'running' | 'completed' | 'error'
  progress: number
  result?: any
  error?: string
  startTime: number
  endTime?: number
  isNew?: boolean
}

// 回测配置接口
export interface BacktestConfig {
  type: 'signal' | 'pool'
  id: number
  symbol: string
  days: number
  targetName?: string
}

// Context 值接口
interface BacktestContextValue {
  // 状态
  tasks: BacktestTask[]
  showResult: BacktestTask | null
  floatingMinimized: boolean
  
  // 操作
  startBacktest: (config: BacktestConfig) => void
  removeTask: (taskId: string) => void
  viewResult: (task: BacktestTask) => void
  closeResult: () => void
  setFloatingMinimized: (minimized: boolean) => void
  clearAllTasks: () => void
}

const BacktestContext = createContext<BacktestContextValue | null>(null)

const API_BASE = '/api/signals'

export function BacktestProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<BacktestTask[]>([])
  const [showResult, setShowResult] = useState<BacktestTask | null>(null)
  const [floatingMinimized, setFloatingMinimized] = useState(false)

  // 启动回测任务
  const startBacktest = useCallback((config: BacktestConfig) => {
    const taskId = `bt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    const newTask: BacktestTask = {
      id: taskId,
      type: config.type,
      targetId: config.id,
      targetName: config.targetName || `${config.type === 'pool' ? '信号池' : '信号'} #${config.id}`,
      symbol: config.symbol,
      days: config.days,
      status: 'running',
      progress: 0,
      startTime: Date.now()
    }

    setTasks(prev => [...prev, newTask])
    setFloatingMinimized(false)

    // 后台异步执行回测
    runBacktestInBackground(taskId, config)
  }, [])

  // 后台运行回测
  const runBacktestInBackground = async (
    taskId: string,
    config: BacktestConfig
  ) => {
    // 模拟进度更新
    const progressInterval = setInterval(() => {
      setTasks(prev => prev.map(task => {
        if (task.id === taskId && task.status === 'running') {
          const newProgress = Math.min(task.progress + Math.random() * 15, 90)
          return { ...task, progress: newProgress }
        }
        return task
      }))
    }, 500)

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

      clearInterval(progressInterval)

      if (!res.ok) throw new Error('Backtest failed')
      const result = await res.json()

      // 更新任务为完成状态
      setTasks(prev => prev.map(task => {
        if (task.id === taskId) {
          return {
            ...task,
            status: 'completed',
            progress: 100,
            result,
            endTime: Date.now(),
            isNew: true
          }
        }
        return task
      }))

      // 3秒后取消新完成标记
      setTimeout(() => {
        setTasks(prev => prev.map(task => {
          if (task.id === taskId) {
            return { ...task, isNew: false }
          }
          return task
        }))
      }, 3000)

    } catch (error) {
      clearInterval(progressInterval)
      console.error('Backtest error:', error)

      setTasks(prev => prev.map(task => {
        if (task.id === taskId) {
          return {
            ...task,
            status: 'error',
            progress: 0,
            error: '回测失败，请稍后重试',
            endTime: Date.now()
          }
        }
        return task
      }))

      toast.error('回测失败')
    }
  }

  // 移除任务
  const removeTask = useCallback((taskId: string) => {
    setTasks(prev => prev.filter(task => task.id !== taskId))
    if (showResult?.id === taskId) {
      setShowResult(null)
    }
  }, [showResult])

  // 查看结果
  const viewResult = useCallback((task: BacktestTask) => {
    if (task.status === 'completed' && task.result) {
      setShowResult(task)
    }
  }, [])

  // 关闭结果
  const closeResult = useCallback(() => {
    setShowResult(null)
  }, [])

  // 清除所有任务
  const clearAllTasks = useCallback(() => {
    setTasks([])
    setShowResult(null)
  }, [])

  const value: BacktestContextValue = {
    tasks,
    showResult,
    floatingMinimized,
    startBacktest,
    removeTask,
    viewResult,
    closeResult,
    setFloatingMinimized,
    clearAllTasks
  }

  return (
    <BacktestContext.Provider value={value}>
      {children}
    </BacktestContext.Provider>
  )
}

// Hook 用于访问回测上下文
export function useBacktest() {
  const context = useContext(BacktestContext)
  if (!context) {
    throw new Error('useBacktest must be used within a BacktestProvider')
  }
  return context
}
