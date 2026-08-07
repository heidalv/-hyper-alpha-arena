import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Loader2, Target, AlertCircle, TrendingUp, TrendingDown, X, Ban } from 'lucide-react'

interface PositionWithPlan {
  symbol: string
  side: 'long' | 'short'
  amount: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
  close_plan: {
    decision_id: number | null
    take_profit_price: number | null
    stop_loss_price: number | null
    tp_order_id: string | null
    sl_order_id: string | null
    tp_triggered: boolean
    sl_triggered: boolean
  } | null
  position_id: string
}

interface AISuggestion {
  id: number
  decision_time: string
  operation: string
  symbol: string | null
  target_portion: number
  reason: string
  take_profit_price: number | null
  stop_loss_price: number | null
}

interface AITradingPanelProps {
  accountId: number
  refreshKey?: number
}

export default function AITradingPanel({ accountId, refreshKey }: AITradingPanelProps) {
  const [loading, setLoading] = useState(true)
  const [positions, setPositions] = useState<PositionWithPlan[]>([])
  const [autoExecutedDecisions, setAutoExecutedDecisions] = useState<AISuggestion[]>([])
  const [closingPositionId, setClosingPositionId] = useState<string | null>(null)
  const [closingAll, setClosingAll] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchData = async (showLoading = true) => {
    try {
      if (showLoading) {
        setLoading(true)
      }
      setMessage(null)

      // 并行获取持仓和自动开单记录
      const [positionsRes, autoExecutedRes] = await Promise.all([
        fetch(`/api/ai-trading/accounts/${accountId}/positions-with-plans`),
        // 🔥 修复：显示所有已执行的决策（buy/sell），不过滤signal_trigger_id
        fetch(`/api/ai-trading/accounts/${accountId}/suggestions?limit=10&executed_only=true`)
      ])

      if (!positionsRes.ok || !autoExecutedRes.ok) {
        throw new Error('Failed to fetch data')
      }

      const positionsData = await positionsRes.json()
      const autoExecutedData = await autoExecutedRes.json()

      setPositions(positionsData)
      setAutoExecutedDecisions(autoExecutedData)
    } catch (error) {
      console.error('Error fetching data:', error)
      setMessage({ type: 'error', text: '加载数据失败' })
    } finally {
      if (showLoading) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    fetchData(true)
  }, [accountId])

  useEffect(() => {
    if (refreshKey > 0) {
      fetchData(false)
    }
  }, [refreshKey])

  const closePosition = async (symbol: string, positionId: string) => {
    if (!confirm(`确定要平仓 ${symbol} (ID: ${positionId}) 吗？`)) return

    try {
      setClosingPositionId(positionId)
      setMessage(null)

      const response = await fetch(`/api/ai-trading/accounts/${accountId}/close-position`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, position_id: positionId, reason: '手动平仓' })
      })

      // 🔥 检查响应状态
      if (!response.ok) {
        const errorText = await response.text()
        console.error('Close position failed:', response.status, errorText)
        setMessage({ type: 'error', text: `平仓失败 (${response.status}): ${errorText}` })
        // 不移除持仓，让用户重试
        return
      }

      const result = await response.json()

      if (result.status === 'success') {
        setMessage({ type: 'success', text: `${symbol} 平仓成功！盈亏: $${result.realized_pnl?.toFixed(2) || '0.00'}` })
        // 🔥 立即从前端移除该持仓
        setPositions(prev => prev.filter(p => p.position_id !== positionId))
      } else {
        setMessage({ type: 'error', text: result.detail || result.message || '平仓失败' })
        // 平仓失败不移除显示，让用户知道失败
      }
    } catch (error) {
      console.error('Error closing position:', error)
      setMessage({ type: 'error', text: '平仓失败，请稍后重试' })
      // 异常时也不移除显示
    } finally {
      setClosingPositionId(null)
    }
  }

  const closeAllPositions = async () => {
    if (positions.length === 0) return

    if (!confirm(`确定要平仓所有 ${positions.length} 个持仓吗？`)) return

    try {
      setClosingAll(true)
      setMessage(null)

      const response = await fetch(`/api/ai-trading/accounts/${accountId}/close-all-positions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: '一键平仓所有' })
      })

      // 🔥 检查响应状态
      if (!response.ok) {
        const errorText = await response.text()
        console.error('Close all positions failed:', response.status, errorText)
        setMessage({ type: 'error', text: `批量平仓失败 (${response.status}): ${errorText}` })
        return
      }

      const result = await response.json()

      if (result.status === 'success' || result.status === 'partial_success') {
        const successCount = result.closed_count || 0
        const successPnl = result.total_pnl?.toFixed(2) || '0.00'
        const errorInfo = result.errors ? ` (${result.errors.length} 个失败)` : ''

        setMessage({
          type: result.status === 'success' ? 'success' : 'error',
          text: `已平仓 ${successCount} 个持仓，总盈亏: $${successPnl}${errorInfo}`
        })

        // 🔥 只清空持仓列表如果全部成功
        if (result.status === 'success') {
          setPositions([])
        }
      } else {
        setMessage({ type: 'error', text: result.detail || result.message || '批量平仓失败' })
      }
    } catch (error) {
      console.error('Error closing all positions:', error)
      setMessage({ type: 'error', text: '批量平仓失败，请稍后重试' })
    } finally {
      setClosingAll(false)
    }
  }

  const syncPositions = async () => {
    try {
      setSyncing(true)
      setMessage(null)

      const response = await fetch(`/api/ai-trading/accounts/${accountId}/sync-positions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })

      if (!response.ok) {
        const errorText = await response.text()
        console.error('Sync positions failed:', response.status, errorText)
        setMessage({ type: 'error', text: `同步失败 (${response.status}): ${errorText}` })
        return
      }

      const result = await response.json()

      if (result.status === 'success') {
        setMessage({
          type: 'success',
          text: `同步完成！已更新 ${result.synced_count} 个持仓状态`
        })
        // 刷新数据
        setTimeout(() => fetchData(false), 500)
      } else {
        setMessage({ type: 'error', text: result.message || '同步失败' })
      }
    } catch (error) {
      console.error('Error syncing positions:', error)
      setMessage({ type: 'error', text: '同步失败，请稍后重试' })
    } finally {
      setSyncing(false)
    }
  }

  const formatOperation = (op: string) => {
    switch (op.toLowerCase()) {
      case 'buy': return { text: '做多', icon: TrendingUp, color: 'text-green-500' }
      case 'sell': return { text: '做空', icon: TrendingDown, color: 'text-red-500' }
      default: return { text: op, icon: null, color: 'text-gray-500' }
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Target className="w-5 h-5" />
            AI自动开单
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="w-5 h-5" />
          AI自动开单
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 消息提示 */}
        {message && (
          <div className={`flex items-center gap-2 p-3 rounded-lg ${
            message.type === 'success' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
          }`}>
            {message.type === 'success' ? (
              <AlertCircle className="w-5 h-5" />
            ) : (
              <AlertCircle className="w-5 h-5" />
            )}
            <span className="text-sm">{message.text}</span>
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto"
              onClick={() => setMessage(null)}
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        )}

        {/* 持仓明细和自动开单明细 - 并排显示 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 当前持仓 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold flex items-center gap-2">
                <Badge variant="secondary">📦</Badge>
                当前持仓 {positions.length > 0 && `(${positions.length})`}
              </h3>
              <div className="flex items-center gap-2">
                {/* 同步持仓按钮 */}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={syncPositions}
                  disabled={syncing}
                  className="flex items-center gap-1"
                  title="同步持仓状态（删除已在交易所平掉的仓位）"
                >
                  {syncing ? (
                    <>
                      <Loader2 className="w-3 h-3 animate-spin" />
                      同步中...
                    </>
                  ) : (
                    <>
                      <Target className="w-3 h-3" />
                      同步持仓
                    </>
                  )}
                </Button>

                {/* 一键平仓全部按钮 */}
                {positions.length > 0 && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={closeAllPositions}
                    disabled={closingAll}
                    className="flex items-center gap-1"
                  >
                    {closingAll ? (
                      <>
                        <Loader2 className="w-3 h-3 animate-spin" />
                        平仓中...
                      </>
                    ) : (
                      <>
                        <Ban className="w-3 h-3" />
                        一键平仓全部
                      </>
                    )}
                  </Button>
                )}
              </div>
            </div>

            {positions.length === 0 ? (
              <div className="text-center py-6 text-muted-foreground text-sm">
                当前无持仓
              </div>
            ) : (
              <div className="space-y-3">
                {positions.map((position) => {
                  const decisionId = position.position_id?.replace('decision_', '') || 'unknown'

                  return (
                    <div
                      key={position.position_id}
                      className="border rounded-lg p-4 space-y-2"
                    >
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{position.symbol}</span>
                            <Badge variant={position.side === 'long' ? 'default' : 'destructive'}>
                              {position.side === 'long' ? '多头' : '空头'}
                            </Badge>
                            <span className="text-xs text-muted-foreground">
                              ID: {decisionId}
                            </span>
                          </div>
                          <div className="text-sm text-muted-foreground">
                            数量: {position.amount} @ ${position.entry_price.toLocaleString()}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            当前价: ${position.current_price?.toLocaleString() || position.entry_price.toLocaleString()}
                          </div>
                        </div>

                        <div className="text-right space-y-1">
                          <div className={`text-lg font-semibold ${
                            position.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {position.unrealized_pnl >= 0 ? '+' : ''}${position.unrealized_pnl.toFixed(2)}
                          </div>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => closePosition(position.symbol, position.position_id)}
                            disabled={closingPositionId === position.position_id}
                          >
                            {closingPositionId === position.position_id ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              '平仓'
                            )}
                          </Button>
                        </div>
                      </div>

                      {/* 平仓计划 */}
                      {position.close_plan && (
                        <div className="pt-2 border-t text-xs space-y-1">
                          <div className="text-muted-foreground">平仓计划:</div>
                          <div className="flex gap-4">
                            {position.close_plan.take_profit_price && (
                              <div className={`flex items-center gap-1 ${
                                position.close_plan.tp_triggered ? 'text-green-600 line-through' : 'text-green-600'
                              }`}>
                                <Target className="w-3 h-3" />
                                TP: ${position.close_plan.take_profit_price.toLocaleString()}
                                {position.close_plan.tp_triggered && <span>(已触发)</span>}
                              </div>
                            )}
                            {position.close_plan.stop_loss_price && (
                              <div className={`flex items-center gap-1 ${
                                position.close_plan.sl_triggered ? 'text-red-600 line-through' : 'text-red-600'
                              }`}>
                                <AlertCircle className="w-3 h-3" />
                                SL: ${position.close_plan.stop_loss_price.toLocaleString()}
                                {position.close_plan.sl_triggered && <span>(已触发)</span>}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* 自动开单明细 */}
          <div className="space-y-3">
            <h3 className="font-semibold flex items-center gap-2">
              <Badge variant="secondary">🤖</Badge>
              自动开单明细 {autoExecutedDecisions.length > 0 && `(${autoExecutedDecisions.length})`}
            </h3>

            {autoExecutedDecisions.length === 0 ? (
              <div className="text-center py-6 text-muted-foreground text-sm">
                暂无自动开单记录
              </div>
            ) : (
              <div className="space-y-3 max-h-96 overflow-y-auto">
                {autoExecutedDecisions.map((decision) => {
                  const operation = formatOperation(decision.operation)
                  const OperationIcon = operation.icon
                  const decisionTime = new Date(decision.decision_time)

                  return (
                    <div
                      key={decision.id}
                      className="border rounded-lg p-3 space-y-2 bg-blue-50/50"
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-2">
                          {OperationIcon && <OperationIcon className={`w-3 h-3 ${operation.color}`} />}
                          <span className="font-medium text-sm">{decision.symbol || '未指定'}</span>
                          <Badge variant="outline" className={`${operation.color} text-xs py-0`}>
                            {operation.text}
                          </Badge>
                          <Badge variant="default" className="bg-blue-600 text-xs py-0">
                            自动
                          </Badge>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {decisionTime.toLocaleString('zh-CN', {
                            month: '2-digit',
                            day: '2-digit',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </div>
                      </div>

                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-muted-foreground">
                          比例: {(decision.target_portion * 100).toFixed(1)}%
                        </span>
                        {(decision.take_profit_price || decision.stop_loss_price) && (
                          <div className="flex gap-3">
                            {decision.take_profit_price && (
                              <div className="flex items-center gap-1 text-green-600">
                                <Target className="w-3 h-3" />
                                TP: ${decision.take_profit_price.toLocaleString()}
                              </div>
                            )}
                            {decision.stop_loss_price && (
                              <div className="flex items-center gap-1 text-red-600">
                                <AlertCircle className="w-3 h-3" />
                                SL: ${decision.stop_loss_price.toLocaleString()}
                              </div>
                            )}
                          </div>
                        )}
                      </div>

                      <p className="text-xs text-muted-foreground line-clamp-2">{decision.reason}</p>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
