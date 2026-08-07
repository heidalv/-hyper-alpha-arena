import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Loader2, Play, X, CheckCircle, AlertCircle, TrendingUp, TrendingDown, Target } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface AISuggestion {
  id: number
  decision_time: string
  operation: string
  symbol: string | null
  target_portion: number
  reason: string
  take_profit_price: number | null
  stop_loss_price: number | null
  executed: boolean
  can_execute: boolean
}

interface AIOrderPanelProps {
  accountId: number
  refreshKey?: number
}

export default function AIOrderPanel({ accountId, refreshKey }: AIOrderPanelProps) {
  useTranslation()
  const [loading, setLoading] = useState(true)
  const [suggestions, setSuggestions] = useState<AISuggestion[]>([])
  const [executedDecisions, setExecutedDecisions] = useState<AISuggestion[]>([])
  const [executingId, setExecutingId] = useState<number | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const fetchData = async (showLoading = true) => {
    try {
      if (showLoading) {
        setLoading(true)
      }
      setMessage(null)

      // 并行获取建议和已执行决策
      const [suggestionsRes, executedRes] = await Promise.all([
        fetch(`/api/ai-trading/accounts/${accountId}/suggestions?limit=5&executed_only=false`),
        fetch(`/api/ai-trading/accounts/${accountId}/suggestions?limit=10&executed_only=true`)
      ])

      if (!suggestionsRes.ok || !executedRes.ok) {
        throw new Error('Failed to fetch data')
      }

      const suggestionsData = await suggestionsRes.json()
      const executedData = await executedRes.json()

      setSuggestions(suggestionsData)
      setExecutedDecisions(executedData)
    } catch (error) {
      console.error('Error fetching data:', error)
      setMessage({ type: 'error', text: '加载数据失败' })
    } finally {
      if (showLoading) {
        setLoading(false)
      }
    }
  }

  // 初次加载时显示loading，后续refresh不显示loading避免闪烁
  useEffect(() => {
    fetchData(true)
  }, [accountId])

  // 监听refreshKey变化，静默刷新（不显示loading）
  useEffect(() => {
    if (refreshKey > 0) {
      fetchData(false)
    }
  }, [refreshKey])

  const executeSuggestion = async (suggestionId: number) => {
    try {
      setExecutingId(suggestionId)
      setMessage(null)

      const response = await fetch(`/api/ai-trading/accounts/${accountId}/execute-suggestion`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suggestion_id: suggestionId })
      })

      const result = await response.json()

      if (result.status === 'success') {
        setMessage({ type: 'success', text: '订单执行成功！' })
        // 刷新数据
        setTimeout(() => fetchData(), 1000)
      } else {
        setMessage({ type: 'error', text: result.message || '执行失败' })
      }
    } catch (error) {
      console.error('Error executing suggestion:', error)
      setMessage({ type: 'error', text: '执行失败，请稍后重试' })
    } finally {
      setExecutingId(null)
    }
  }

  const closePosition = async (symbol: string) => {
    if (!confirm(`确定要平仓 ${symbol} 吗？`)) return

    try {
      setClosingSymbol(symbol)
      setMessage(null)

      const response = await fetch(`/api/ai-trading/accounts/${accountId}/close-position`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, reason: '手动平仓' })
      })

      const result = await response.json()

      if (result.status === 'success') {
        setMessage({ type: 'success', text: `${symbol} 平仓成功！盈亏: $${result.realized_pnl.toFixed(2)}` })
        // 刷新数据
        setTimeout(() => fetchData(), 1000)
      } else {
        setMessage({ type: 'error', text: result.detail || '平仓失败' })
      }
    } catch (error) {
      console.error('Error closing position:', error)
      setMessage({ type: 'error', text: '平仓失败，请稍后重试' })
    } finally {
      setClosingSymbol(null)
    }
  }

  const formatOperation = (op: string) => {
    switch (op.toLowerCase()) {
      case 'buy': return { text: '做多', icon: TrendingUp, color: 'text-green-500' }
      case 'sell': return { text: '做空', icon: TrendingDown, color: 'text-red-500' }
      case 'close': return { text: '平仓', icon: X, color: 'text-gray-500' }
      default: return { text: op, icon: null, color: 'text-gray-500' }
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Target className="w-5 h-5" />
            AI策略开单
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
          AI策略开单
        </CardTitle>
        <CardDescription>查看AI建议并执行交易</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 消息提示 */}
        {message && (
          <div className={`flex items-center gap-2 p-3 rounded-lg ${
            message.type === 'success' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
          }`}>
            {message.type === 'success' ? (
              <CheckCircle className="w-5 h-5" />
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

        {/* AI建议 */}
        <div className="space-y-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Badge variant="secondary">💡</Badge>
            AI建议 {suggestions.length > 0 && `(${suggestions.length})`}
          </h3>

          {suggestions.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm">
              暂无新的AI建议
            </div>
          ) : (
            <div className="space-y-3">
              {suggestions.map((suggestion) => {
                const operation = formatOperation(suggestion.operation)
                const OperationIcon = operation.icon

                return (
                  <div
                    key={suggestion.id}
                    className="border rounded-lg p-4 space-y-2 hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-2">
                        {OperationIcon && <OperationIcon className={`w-4 h-4 ${operation.color}`} />}
                        <span className="font-medium">{suggestion.symbol || '未指定'}</span>
                        <Badge variant="outline" className={operation.color}>
                          {operation.text}
                        </Badge>
                        <span className="text-sm text-muted-foreground">
                          {(suggestion.target_portion * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="flex gap-2">
                        {suggestion.can_execute && (
                          <Button
                            size="sm"
                            onClick={() => executeSuggestion(suggestion.id)}
                            disabled={executingId === suggestion.id}
                          >
                            {executingId === suggestion.id ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <>
                                <Play className="w-4 h-4 mr-1" />
                                执行
                              </>
                            )}
                          </Button>
                        )}
                      </div>
                    </div>

                    <p className="text-sm text-muted-foreground">{suggestion.reason}</p>

                    {(suggestion.take_profit_price || suggestion.stop_loss_price) && (
                      <div className="flex gap-4 text-xs">
                        {suggestion.take_profit_price && (
                          <div className="flex items-center gap-1 text-green-600">
                            <Target className="w-3 h-3" />
                            止盈: ${suggestion.take_profit_price.toLocaleString()}
                          </div>
                        )}
                        {suggestion.stop_loss_price && (
                          <div className="flex items-center gap-1 text-red-600">
                            <AlertCircle className="w-3 h-3" />
                            止损: ${suggestion.stop_loss_price.toLocaleString()}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* 已完成交易 */}
        <div className="space-y-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Badge variant="secondary">✅</Badge>
            已完成交易 {executedDecisions.length > 0 && `(${executedDecisions.length})`}
          </h3>

          {executedDecisions.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm">
              暂无已执行交易
            </div>
          ) : (
            <div className="space-y-3">
              {executedDecisions.map((decision) => {
                const operation = formatOperation(decision.operation)
                const OperationIcon = operation.icon
                const decisionTime = new Date(decision.decision_time)

                return (
                  <div
                    key={decision.id}
                    className="border rounded-lg p-4 space-y-2 bg-green-50/50"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-2">
                        {OperationIcon && <OperationIcon className={`w-4 h-4 ${operation.color}`} />}
                        <span className="font-medium">{decision.symbol || '未指定'}</span>
                        <Badge variant="outline" className={operation.color}>
                          {operation.text}
                        </Badge>
                        <Badge variant="default" className="bg-green-600">
                          已执行
                        </Badge>
                        <span className="text-sm text-muted-foreground">
                          {(decision.target_portion * 100).toFixed(1)}%
                        </span>
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

                    <p className="text-sm text-muted-foreground">{decision.reason}</p>

                    {(decision.take_profit_price || decision.stop_loss_price) && (
                      <div className="flex gap-4 text-xs">
                        {decision.take_profit_price && (
                          <div className="flex items-center gap-1 text-green-600">
                            <Target className="w-3 h-3" />
                            止盈: ${decision.take_profit_price.toLocaleString()}
                          </div>
                        )}
                        {decision.stop_loss_price && (
                          <div className="flex items-center gap-1 text-red-600">
                            <AlertCircle className="w-3 h-3" />
                            止损: ${decision.stop_loss_price.toLocaleString()}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
