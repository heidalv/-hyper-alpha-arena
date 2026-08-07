import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Star,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  CheckCircle,
  Lightbulb,
  Target,
  Shield,
  Clock,
} from 'lucide-react'
import { formatCurrency, formatPercent } from '@/lib/priceFormat'
import {
  TradeReview,
  ReviewSummary,
  ReviewDimensionType,
} from '@/lib/types/analytics'
import { getTradeReviews, triggerTradeReview } from '@/lib/api'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

interface TradeReviewListProps {
  accountId?: number
  tradingMode?: string
}

const DIMENSION_ICONS: Record<ReviewDimensionType, React.ReactNode> = {
  entry_quality: <Target className="h-4 w-4" />,
  exit_quality: <TrendingUp className="h-4 w-4" />,
  risk_management: <Shield className="h-4 w-4" />,
  market_regime: <TrendingUp className="h-4 w-4" />,
  timing: <Clock className="h-4 w-4" />,
  position_sizing: <Target className="h-4 w-4" />,
  emotion_control: <Lightbulb className="h-4 w-4" />,
  discipline: <CheckCircle className="h-4 w-4" />,
}

const DIMENSION_LABELS: Record<ReviewDimensionType, string> = {
  entry_quality: '入场质量',
  exit_quality: '出场质量',
  risk_management: '风险管理',
  market_regime: '市场状态',
  timing: '时机把握',
  position_sizing: '仓位控制',
  emotion_control: '情绪控制',
  discipline: '纪律执行',
}

export default function TradeReviewList({
  accountId,
  tradingMode = 'mainnet',
}: TradeReviewListProps) {
  useTranslation()
  const { symbols: configuredPairs } = useTradingPairs()
  const TRADING_SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS
  const [reviews, setReviews] = useState<TradeReview[]>([])
  const [summary, setSummary] = useState<ReviewSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedReview, setSelectedReview] = useState<TradeReview | null>(null)
  const [expandedReviews, setExpandedReviews] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('all')
  const [symbolFilter, setSymbolFilter] = useState('all')

  useEffect(() => {
    loadData()
  }, [filter, symbolFilter, accountId, tradingMode])

  const loadData = async () => {
    setLoading(true)
    try {
      const params: any = {
        account_id: accountId,
        trading_mode: tradingMode,
      }
      if (filter !== 'all') {
        params.status = filter
      }
      if (symbolFilter !== 'all') {
        params.symbol = symbolFilter
      }

      const data = await getTradeReviews(params)
      setReviews(data.reviews)
      setSummary(data.summary)
    } catch (error) {
      console.error('Failed to load trade reviews:', error)
    } finally {
      setLoading(false)
    }
  }

  const toggleExpanded = (tradeId: string) => {
    setExpandedReviews((prev) => {
      const next = new Set(prev)
      if (next.has(tradeId)) {
        next.delete(tradeId)
      } else {
        next.add(tradeId)
      }
      return next
    })
  }

  const getScoreColor = (score: number): string => {
    if (score >= 8.5) return 'text-green-500'
    if (score >= 7) return 'text-blue-500'
    if (score >= 5) return 'text-yellow-500'
    if (score >= 3) return 'text-orange-500'
    return 'text-red-500'
  }

  const getScoreBg = (score: number): string => {
    if (score >= 8.5) return 'bg-green-500'
    if (score >= 7) return 'bg-blue-500'
    if (score >= 5) return 'bg-yellow-500'
    if (score >= 3) return 'bg-orange-500'
    return 'bg-red-500'
  }

  const formatDate = (dateStr: string): string => {
    return new Date(dateStr).toLocaleString()
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">交易复盘</h2>
          <p className="text-muted-foreground">
            复盘和分析你的交易决策
          </p>
        </div>
        <div className="flex items-center gap-4">
          <Select value={symbolFilter} onValueChange={setSymbolFilter}>
            <SelectTrigger className="w-32">
              <SelectValue placeholder="币种" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">所有币种</SelectItem>
              {TRADING_SYMBOLS.slice(0, 10).map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">所有复盘</SelectItem>
              <SelectItem value="completed">已完成</SelectItem>
              <SelectItem value="flagged">已标记</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={loadData} variant="outline" size="icon">
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">总复盘数</p>
              <p className="text-2xl font-bold">{summary.total_reviews}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">平均分</p>
              <p className={`text-2xl font-bold ${getScoreColor(summary.avg_overall_score)}`}>
                {summary.avg_overall_score.toFixed(1)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">优秀</p>
              <p className="text-2xl font-bold text-green-500">
                {summary.score_distribution?.excellent || 0}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">待改进</p>
              <p className="text-2xl font-bold text-orange-500">
                {(summary.score_distribution?.acceptable || 0) + (summary.score_distribution?.poor || 0)}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-xs text-muted-foreground">总盈亏</p>
              <p
                className={`text-2xl font-bold ${
                  summary.total_pnl >= 0 ? 'text-green-500' : 'text-red-500'
                }`}
              >
                {formatCurrency(summary.total_pnl)}
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Review List */}
      <div className="space-y-4">
        {reviews.length === 0 ? (
          <Card>
            <CardContent className="pt-6 text-center">
              <p className="text-muted-foreground">暂无交易复盘记录</p>
            </CardContent>
          </Card>
        ) : (
          reviews.map((review) => (
            <Card key={review.trade_id} className="overflow-hidden">
              <div
                className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50"
                onClick={() => toggleExpanded(review.trade_id)}
              >
                <div className="flex items-center gap-4">
                  <div
                    className={`w-12 h-12 rounded-full flex items-center justify-center text-white font-bold ${getScoreBg(
                      review.overall_score
                    )}`}
                  >
                    {review.overall_score.toFixed(1)}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold">{review.symbol}</span>
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          review.side === 'long'
                            ? 'bg-green-100 text-green-700'
                            : 'bg-red-100 text-red-700'
                        }`}
                      >
                        {review.side.toUpperCase()}
                      </span>
                      <span className="text-sm text-muted-foreground">
                        {formatDate(review.exit_time)}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      Entry: {review.entry_price} → Exit: {review.exit_price}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <p
                      className={`text-lg font-bold ${
                        review.pnl >= 0 ? 'text-green-500' : 'text-red-500'
                      }`}
                    >
                      {review.pnl >= 0 ? '+' : ''}
                      {formatCurrency(review.pnl)} ({review.pnl_pct >= 0 ? '+' : ''}
                      {review.pnl_pct.toFixed(2)}%)
                    </p>
                  </div>
                  {expandedReviews.has(review.trade_id) ? (
                    <ChevronUp className="h-5 w-5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-5 w-5 text-muted-foreground" />
                  )}
                </div>
              </div>

              {expandedReviews.has(review.trade_id) && (
                <div className="border-t bg-muted/30 p-4">
                  {/* Conclusion */}
                  <div className="mb-4 p-3 rounded-lg bg-blue-50 border border-blue-200">
                    <p className="font-medium text-blue-800">{review.conclusion}</p>
                  </div>

                  {/* Dimension Scores */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                    {Object.entries(review.dimensions).map(([key, dim]) => (
                      <div key={key} className="p-3 rounded-lg bg-muted">
                        <div className="flex items-center gap-2 mb-2">
                          {DIMENSION_ICONS[key as ReviewDimensionType]}
                          <span className="text-sm font-medium">
                            {DIMENSION_LABELS[key as ReviewDimensionType]}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className={`text-xl font-bold ${getScoreColor(dim.score)}`}>
                            {dim.score.toFixed(1)}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            ({dim.weight.toFixed(0)}%)
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Issues */}
                  {Object.values(review.dimensions).some((d) => d.issues.length > 0) && (
                    <div className="mb-4">
                      <h4 className="font-medium mb-2 flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-orange-500" />
                        发现问题
                      </h4>
                      <div className="space-y-1">
                        {Object.values(review.dimensions)
                          .flatMap((d) => d.issues)
                          .slice(0, 5)
                          .map((issue, idx) => (
                            <p key={idx} className="text-sm text-orange-600 flex items-start gap-2">
                              <span>•</span>
                              {issue}
                            </p>
                          ))}
                      </div>
                    </div>
                  )}

                  {/* Suggestions */}
                  {Object.values(review.dimensions).some((d) => d.suggestions.length > 0) && (
                    <div className="mb-4">
                      <h4 className="font-medium mb-2 flex items-center gap-2">
                        <Lightbulb className="h-4 w-4 text-blue-500" />
                        改进建议
                      </h4>
                      <div className="space-y-1">
                        {Object.values(review.dimensions)
                          .flatMap((d) => d.suggestions)
                          .slice(0, 5)
                          .map((suggestion, idx) => (
                            <p key={idx} className="text-sm text-blue-600 flex items-start gap-2">
                              <span>•</span>
                              {suggestion}
                            </p>
                          ))}
                      </div>
                    </div>
                  )}

                  {/* Lessons Learned */}
                  {review.lessons_learned.length > 0 && (
                    <div className="mb-4">
                      <h4 className="font-medium mb-2 flex items-center gap-2">
                        <CheckCircle className="h-4 w-4 text-green-500" />
                        经验教训
                      </h4>
                      <ul className="space-y-1">
                        {review.lessons_learned.slice(0, 3).map((lesson, idx) => (
                          <li key={idx} className="text-sm text-green-600 flex items-start gap-2">
                            <span>✓</span>
                            {lesson}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* AI Info */}
                  {review.ai_confidence && (
                    <div className="text-sm text-muted-foreground pt-2 border-t">
                      <p>AI 置信度: {(review.ai_confidence * 100).toFixed(0)}%</p>
                      {review.ai_reasoning && (
                        <p className="mt-1">分析理由: {review.ai_reasoning}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
          ))
        )}
      </div>

      {/* Review Detail Dialog */}
      <Dialog open={!!selectedReview} onOpenChange={() => setSelectedReview(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>交易复盘详情</DialogTitle>
            <DialogDescription>
              {selectedReview && `${selectedReview.symbol} - ${selectedReview.side.toUpperCase()}`}
            </DialogDescription>
          </DialogHeader>
          {selectedReview && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 rounded-lg bg-muted">
                  <p className="text-sm text-muted-foreground">综合评分</p>
                  <p className={`text-3xl font-bold ${getScoreColor(selectedReview.overall_score)}`}>
                    {selectedReview.overall_score.toFixed(1)}/10
                  </p>
                </div>
                <div className="p-4 rounded-lg bg-muted">
                  <p className="text-sm text-muted-foreground">盈亏</p>
                  <p
                    className={`text-3xl font-bold ${
                      selectedReview.pnl >= 0 ? 'text-green-500' : 'text-red-500'
                    }`}
                  >
                    {formatCurrency(selectedReview.pnl)}
                  </p>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
