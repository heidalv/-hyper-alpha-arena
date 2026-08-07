/**
 * 决策日志 — 读 GAP 闭环 DecisionSnapshot v2 API
 */
import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { History, TrendingUp, TrendingDown, CheckCircle, XCircle, Clock } from 'lucide-react'

interface GapDecision {
  id: number
  symbol: string
  tier: string
  action: string
  confidence: number
  source_lane: string | null
  proposal_id: string | null
  code_reason: string | null
  evaluate_verdict: { allowed?: boolean; reason?: string; layer?: string } | null
  executed: boolean | null
  execution_channel: string | null
  reasoning: string
  timestamp: string | null
}

interface DecisionLogProps {
  symbol: string
  sessionId?: string
}

const GAP_API = '/api/gap-closure'

export function DecisionLog({ symbol, sessionId }: DecisionLogProps) {
  const [decisions, setDecisions] = useState<GapDecision[]>([])
  const [isLoading, setIsLoading] = useState(false)

  useEffect(() => {
    const sym = symbol.toUpperCase()
    const fetchDecisions = async () => {
      setIsLoading(true)
      try {
        const response = await fetch(
          `${GAP_API}/decisions/recent?symbol=${encodeURIComponent(sym)}&limit=20`
        )
        if (response.ok) {
          const data = await response.json()
          setDecisions(data.decisions || [])
        }
      } catch (e) {
        console.error('获取决策快照失败:', e)
      } finally {
        setIsLoading(false)
      }
    }

    fetchDecisions()
    const interval = setInterval(fetchDecisions, 8000)
    return () => clearInterval(interval)
  }, [symbol, sessionId])

  const getActionIcon = (action: string) => {
    if (action === 'buy') return <TrendingUp className="h-4 w-4 text-green-500" />
    if (action === 'sell') return <TrendingDown className="h-4 w-4 text-red-500" />
    return <Clock className="h-4 w-4 text-muted-foreground" />
  }

  const laneLabel = (lane: string | null) => {
    if (!lane) return '未知'
    if (lane.includes('scalp')) return '短线'
    if (lane.includes('swing') || lane === 'mid') return '中线'
    if (lane.includes('trend') || lane === 'long') return '长线'
    if (lane === 'master') return '总控'
    return lane
  }

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <History className="h-4 w-4" />
          决策日志（TCP 快照）
          <Badge variant="outline" className="text-xs ml-auto">{symbol}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[280px]">
          {isLoading && decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">加载中...</p>
          ) : decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">暂无决策记录</p>
          ) : (
            <div className="space-y-2">
              {decisions.map((d) => {
                const allowed = d.evaluate_verdict?.allowed !== false
                const codeReason = d.code_reason || d.evaluate_verdict?.reason || ''
                return (
                  <div
                    key={`${d.id}-${d.proposal_id || d.timestamp}`}
                    className="border rounded-lg p-2 text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5">
                        {getActionIcon(d.action)}
                        <span className="font-medium uppercase">{d.action}</span>
                        <Badge variant="secondary" className="text-[10px]">
                          {laneLabel(d.source_lane)}
                        </Badge>
                        <Badge variant="outline" className="text-[10px]">
                          {d.tier}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-1">
                        {d.executed ? (
                          <CheckCircle className="h-3.5 w-3.5 text-green-500" />
                        ) : allowed ? (
                          <Clock className="h-3.5 w-3.5 text-yellow-500" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5 text-red-500" />
                        )}
                        <span className="text-muted-foreground">{d.confidence?.toFixed?.(0) ?? d.confidence}%</span>
                      </div>
                    </div>
                    {codeReason && (
                      <p className="text-[10px] text-amber-700 dark:text-amber-400 truncate" title={codeReason}>
                        代码原因={codeReason.slice(0, 120)}
                      </p>
                    )}
                    {d.reasoning && (
                      <p className="text-muted-foreground line-clamp-2">{d.reasoning}</p>
                    )}
                    <p className="text-[10px] text-muted-foreground">
                      {d.timestamp ? new Date(d.timestamp).toLocaleString('zh-CN') : ''}
                      {d.proposal_id ? ` · ${d.proposal_id.slice(0, 8)}` : ''}
                    </p>
                  </div>
                )
              })}
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

export default DecisionLog
