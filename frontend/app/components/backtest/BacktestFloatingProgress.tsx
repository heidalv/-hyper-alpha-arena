/**
 * BacktestFloatingProgress - 全局回测悬浮进度窗口组件
 * 始终显示在右下角，不受页面切换影响
 */

import { useTranslation } from 'react-i18next'
import { useBacktest, BacktestTask } from '@/contexts/BacktestContext'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  BarChart3,
  Bell,
  CheckCircle2,
  Loader2,
  Minimize2,
  X,
  XCircle,
} from 'lucide-react'

export default function BacktestFloatingProgress() {
  const { t } = useTranslation()
  const {
    tasks,
    showResult,
    floatingMinimized,
    removeTask,
    viewResult,
    closeResult,
    setFloatingMinimized,
    clearAllTasks
  } = useBacktest()

  // 如果没有任务，不显示
  if (tasks.length === 0) {
    return null
  }

  return (
    <>
      {/* 悬浮进度窗口 */}
      <div className="fixed bottom-4 right-4 z-[9999]">
        <div className={`transition-all duration-300 ${
          floatingMinimized ? 'w-14 h-14' : 'w-80'
        }`}>
          {floatingMinimized ? (
            /* 最小化状态 - 显示图标 */
            <button
              onClick={() => setFloatingMinimized(false)}
              className={`w-14 h-14 rounded-full shadow-lg flex items-center justify-center transition-all ${
                tasks.some(t => t.isNew)
                  ? 'bg-gradient-to-r from-emerald-500 to-teal-500 animate-pulse shadow-emerald-500/50'
                  : tasks.some(t => t.status === 'running')
                  ? 'bg-gradient-to-r from-blue-500 to-cyan-500 shadow-blue-500/30'
                  : 'bg-white dark:bg-slate-700 hover:bg-slate-100 dark:hover:bg-slate-600 border border-slate-200 dark:border-transparent'
              }`}
            >
              {tasks.some(t => t.status === 'running') ? (
                <Loader2 className="w-6 h-6 text-white animate-spin" />
              ) : tasks.some(t => t.isNew) ? (
                <Bell className="w-6 h-6 text-white animate-bounce" />
              ) : (
                <BarChart3 className="w-6 h-6 text-slate-600 dark:text-white" />
              )}
              {/* 任务数量角标 */}
              <span className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-red-500 text-white text-xs flex items-center justify-center font-bold">
                {tasks.length}
              </span>
            </button>
          ) : (
            /* 展开状态 - 显示任务列表 */
            <div className={`rounded-2xl shadow-2xl overflow-hidden backdrop-blur-xl border transition-all ${
              tasks.some(t => t.isNew)
                ? 'bg-gradient-to-br from-emerald-50 to-teal-50 dark:from-emerald-900/95 dark:to-teal-900/95 border-emerald-300 dark:border-emerald-500/50 shadow-emerald-500/20'
                : 'bg-white/95 dark:bg-slate-900/95 border-slate-200 dark:border-slate-700/50'
            }`}>
              {/* 标题栏 */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700/50">
                <div className="flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-emerald-500 dark:text-emerald-400" />
                  <span className="text-sm font-semibold text-slate-800 dark:text-white">
                    {t('signals.backtestTasks', '回测任务')}
                  </span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">({tasks.length})</span>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setFloatingMinimized(true)}
                    className="p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700/50 text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-white transition-colors"
                  >
                    <Minimize2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={clearAllTasks}
                    className="p-1.5 rounded-lg hover:bg-red-100 dark:hover:bg-red-500/20 text-slate-500 dark:text-slate-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* 任务列表 */}
              <div className="max-h-64 overflow-y-auto">
                {tasks.map((task) => (
                  <TaskItem
                    key={task.id}
                    task={task}
                    onView={() => viewResult(task)}
                    onRemove={() => removeTask(task.id)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 回测结果弹窗 */}
      <ResultDialog
        task={showResult}
        onClose={closeResult}
      />
    </>
  )
}

// 任务项组件
function TaskItem({ 
  task, 
  onView, 
  onRemove 
}: { 
  task: BacktestTask
  onView: () => void
  onRemove: () => void 
}) {
  const { t } = useTranslation()

  return (
    <div
      className={`px-4 py-3 border-b border-slate-200 dark:border-slate-800/50 last:border-b-0 transition-all cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/30 ${
        task.isNew ? 'animate-pulse bg-emerald-50 dark:bg-emerald-500/10' : ''
      }`}
      onClick={onView}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {task.status === 'running' && (
            <Loader2 className="w-4 h-4 text-blue-500 dark:text-blue-400 animate-spin flex-shrink-0" />
          )}
          {task.status === 'completed' && (
            <CheckCircle2 className={`w-4 h-4 flex-shrink-0 ${
              task.isNew ? 'text-emerald-500 dark:text-emerald-400 animate-bounce' : 'text-emerald-500 dark:text-emerald-400'
            }`} />
          )}
          {task.status === 'error' && (
            <XCircle className="w-4 h-4 text-red-500 dark:text-red-400 flex-shrink-0" />
          )}
          <span className="text-sm text-slate-800 dark:text-white truncate">{task.targetName}</span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onRemove()
          }}
          className="p-1 rounded hover:bg-slate-200 dark:hover:bg-slate-700/50 text-slate-400 dark:text-slate-500 hover:text-red-500 dark:hover:text-red-400 transition-colors flex-shrink-0"
        >
          <X className="w-3 h-3" />
        </button>
      </div>

      {/* 进度条 */}
      {task.status === 'running' && (
        <div className="mb-2">
          <div className="h-1.5 rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 to-cyan-400 transition-all duration-300"
              style={{ width: `${task.progress}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-1">
            <span className="text-xs text-slate-500">{Math.round(task.progress)}%</span>
            <span className="text-xs text-slate-500">{t('signals.running', '运行中...')}</span>
          </div>
        </div>
      )}

      {/* 完成信息 */}
      {task.status === 'completed' && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 dark:text-slate-400">
            {task.symbol}USDT · {task.days}{t('signals.daysUnit', '天')}
          </span>
          <span className={`font-medium ${
            task.isNew ? 'text-emerald-500 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'
          }`}>
            {task.isNew ? t('signals.justCompleted', '刚刚完成') : t('signals.clickToView', '点击查看')}
          </span>
        </div>
      )}

      {/* 错误信息 */}
      {task.status === 'error' && (
        <div className="text-xs text-red-500 dark:text-red-400">
          {task.error}
        </div>
      )}
    </div>
  )
}

// 结果对话框组件
function ResultDialog({ 
  task, 
  onClose 
}: { 
  task: BacktestTask | null
  onClose: () => void 
}) {
  const { t } = useTranslation()

  if (!task) return null

  // 从后端返回的 summary 对象中提取数据
  const summary = task.result?.summary
  const trades = task.result?.trades || []
  const hasNoTriggers = task.result?.message?.includes('No triggers') || task.result?.message?.includes('No combined triggers')

  return (
    <Dialog open={!!task} onOpenChange={() => onClose()}>
      <DialogContent className="sm:max-w-[900px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-emerald-500 dark:text-emerald-400" />
            {t('signals.backtestResult', '回测结果')} - {task.targetName}
          </DialogTitle>
          <DialogDescription>
            {task.symbol}USDT · {task.days}{t('signals.daysUnit', '天')} · 
            {t('signals.completedAt', '完成于')} {new Date(task.endTime || 0).toLocaleTimeString()}
            {task.result?.trigger_count !== undefined && (
              <span className="ml-2">· 触发 {task.result.trigger_count} 次</span>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* 回测结果内容 */}
        <div className="py-4">
          {task.result?.error ? (
            <div className="p-4 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 text-red-600 dark:text-red-400">
              {task.result.error}
            </div>
          ) : hasNoTriggers ? (
            <div className="p-6 rounded-xl bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/20 text-amber-700 dark:text-amber-400 text-center">
              <p className="text-lg font-medium mb-2">{t('signals.noTriggersFound', '未找到触发信号')}</p>
              <p className="text-sm opacity-80">
                {t('signals.noTriggersHint', '在选定的时间范围内，该信号未被触发。可能是阈值设置过于严格，或市场条件未达到触发条件。')}
              </p>
            </div>
          ) : !summary ? (
            <div className="p-6 rounded-xl bg-slate-100 dark:bg-slate-800/50 text-center">
              <p className="text-slate-500 dark:text-slate-400">
                {t('signals.noDataAvailable', '暂无回测数据')}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* 核心指标 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-4 rounded-xl bg-slate-100 dark:bg-slate-800/50">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t('signals.totalTrades', '总交易数')}</p>
                  <p className="text-xl font-bold text-slate-800 dark:text-white">{summary.total_trades || 0}</p>
                </div>
                <div className="p-4 rounded-xl bg-slate-100 dark:bg-slate-800/50">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t('signals.winRate', '胜率')}</p>
                  <p className={`text-xl font-bold ${
                    (summary.win_rate || 0) >= 50 ? 'text-emerald-600 dark:text-emerald-500' : 'text-red-600 dark:text-red-500'
                  }`}>
                    {(summary.win_rate || 0).toFixed(1)}%
                  </p>
                </div>
                <div className="p-4 rounded-xl bg-slate-100 dark:bg-slate-800/50">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t('signals.totalPnL', '总盈亏')}</p>
                  <p className={`text-xl font-bold ${
                    (summary.total_pnl_percent || 0) >= 0 ? 'text-emerald-600 dark:text-emerald-500' : 'text-red-600 dark:text-red-500'
                  }`}>
                    {(summary.total_pnl_percent || 0) >= 0 ? '+' : ''}
                    {(summary.total_pnl_percent || 0).toFixed(2)}%
                  </p>
                </div>
                <div className="p-4 rounded-xl bg-slate-100 dark:bg-slate-800/50">
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t('signals.maxDrawdown', '最大回撤')}</p>
                  <p className="text-xl font-bold text-orange-600 dark:text-orange-500">
                    {(summary.max_drawdown_percent || 0).toFixed(1)}%
                  </p>
                </div>
              </div>

              {/* 详细指标 */}
              <div className="p-4 rounded-xl bg-gradient-to-br from-slate-100 to-slate-50 dark:from-slate-800/50 dark:to-slate-900/50">
                <h4 className="text-sm font-semibold text-slate-800 dark:text-white mb-3">{t('signals.performanceDetails', '详细指标')}</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.avgProfit', '平均盈利')}: </span>
                    <span className="font-medium text-emerald-600 dark:text-emerald-500">
                      +{(summary.avg_win_percent || 0).toFixed(2)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.avgLoss', '平均亏损')}: </span>
                    <span className="font-medium text-red-600 dark:text-red-500">
                      {(summary.avg_loss_percent || 0).toFixed(2)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.profitFactor', '盈亏比')}: </span>
                    <span className="font-medium text-slate-800 dark:text-white">
                      {(summary.profit_factor || 0).toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.sharpeRatio', '夏普比率')}: </span>
                    <span className="font-medium text-slate-800 dark:text-white">
                      {(summary.sharpe_ratio || 0).toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.winTrades', '盈利次数')}: </span>
                    <span className="font-medium text-emerald-600 dark:text-emerald-500">
                      {summary.winning_trades || 0}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.lossTrades', '亏损次数')}: </span>
                    <span className="font-medium text-red-600 dark:text-red-500">
                      {summary.losing_trades || 0}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.avgHoldTime', '平均持仓')}: </span>
                    <span className="font-medium text-slate-800 dark:text-white">
                      {(summary.avg_hold_duration_min || 0).toFixed(0)} 分钟
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.bestTrade', '最佳交易')}: </span>
                    <span className="font-medium text-emerald-600 dark:text-emerald-500">
                      +{(summary.best_trade_pnl || 0).toFixed(2)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">{t('signals.worstTrade', '最差交易')}: </span>
                    <span className="font-medium text-red-600 dark:text-red-500">
                      {(summary.worst_trade_pnl || 0).toFixed(2)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* 交易记录 */}
              {trades.length > 0 && (
                <div className="p-4 rounded-xl bg-slate-50 dark:bg-slate-800/30">
                  <h4 className="text-sm font-semibold text-slate-800 dark:text-white mb-3">
                    {t('signals.tradeHistory', '交易记录')} ({trades.length}笔)
                  </h4>
                  <div className="max-h-48 overflow-y-auto space-y-2">
                    {trades.slice(0, 20).map((trade: any, idx: number) => (
                      <div key={idx} className="flex items-center justify-between p-2 rounded-lg bg-white dark:bg-slate-700/50 text-xs">
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                            trade.direction === 'long' 
                              ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400' 
                              : 'bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-400'
                          }`}>
                            {trade.direction === 'long' ? '做多' : '做空'}
                          </span>
                          <span className="text-slate-500 dark:text-slate-400">
                            {new Date(trade.entry_time).toLocaleDateString()}
                          </span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-slate-500 dark:text-slate-400">
                            {trade.entry_price?.toFixed(2)} → {trade.exit_price?.toFixed(2)}
                          </span>
                          <span className={`font-medium ${
                            trade.pnl_percent >= 0 
                              ? 'text-emerald-600 dark:text-emerald-400' 
                              : 'text-red-600 dark:text-red-400'
                          }`}>
                            {trade.pnl_percent >= 0 ? '+' : ''}{trade.pnl_percent?.toFixed(2)}%
                          </span>
                        </div>
                      </div>
                    ))}
                    {trades.length > 20 && (
                      <p className="text-center text-xs text-slate-400 py-2">
                        还有 {trades.length - 20} 笔交易...
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('signals.close', '关闭')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
