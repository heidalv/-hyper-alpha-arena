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
  Lightbulb,
  TrendingUp,
  AlertTriangle,
  CheckCircle,
  RefreshCw,
  Target,
  BarChart3,
  Brain,
  ArrowRight,
  Zap,
  Shield,
  Clock,
} from 'lucide-react'
import {
  LearningInsight,
  LearningRecommendation,
  LearningReport,
} from '@/lib/types/analytics'
import {
  getLearningInsights,
  getLearningRecommendations,
  getLearningReport,
  triggerLearningAnalysis,
} from '@/lib/api'

interface LearningInsightsProps {
  accountId?: number
  tradingMode?: string
}

const INSIGHT_TYPE_ICONS: Record<string, React.ReactNode> = {
  factor_performance: <BarChart3 className="h-4 w-4" />,
  market_regime: <TrendingUp className="h-4 w-4" />,
  entry_pattern: <Target className="h-4 w-4" />,
  exit_pattern: <TrendingUp className="h-4 w-4" />,
  risk_pattern: <Shield className="h-4 w-4" />,
  timing_pattern: <Clock className="h-4 w-4" />,
}

const PRIORITY_COLORS: Record<string, string> = {
  high: 'bg-red-100 text-red-700 border-red-200',
  medium: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  low: 'bg-green-100 text-green-700 border-green-200',
}

export default function LearningInsights({
  accountId,
  tradingMode = 'mainnet',
}: LearningInsightsProps) {
  useTranslation()
  const [insights, setInsights] = useState<LearningInsight[]>([])
  const [recommendations, setRecommendations] = useState<LearningRecommendation[]>([])
  const [report, setReport] = useState<LearningReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [activeTab, setActiveTab] = useState('insights')
  const [insightTypeFilter, setInsightTypeFilter] = useState('all')
  const [priorityFilter, setPriorityFilter] = useState('all')

  useEffect(() => {
    loadData()
  }, [insightTypeFilter, priorityFilter, accountId, tradingMode])

  const loadData = async () => {
    setLoading(true)
    try {
      const [insightsData, recommendationsData, reportData] = await Promise.all([
        getLearningInsights({
          account_id: accountId,
          trading_mode: tradingMode,
          insight_type: insightTypeFilter !== 'all' ? insightTypeFilter : undefined,
        }),
        getLearningRecommendations({
          account_id: accountId,
          trading_mode: tradingMode,
          priority: priorityFilter !== 'all' ? priorityFilter : undefined,
        }),
        getLearningReport({
          account_id: accountId,
          trading_mode: tradingMode,
        }),
      ])
      setInsights(insightsData.insights)
      setRecommendations(recommendationsData.recommendations)
      setReport(reportData)
    } catch (error) {
      console.error('Failed to load learning data:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleAnalyze = async () => {
    setAnalyzing(true)
    try {
      await triggerLearningAnalysis({
        account_id: accountId,
        trading_mode: tradingMode,
      })
      await loadData()
    } catch (error) {
      console.error('Failed to trigger learning analysis:', error)
    } finally {
      setAnalyzing(false)
    }
  }

  const getConfidenceColor = (confidence: number): string => {
    if (confidence >= 0.7) return 'text-green-500'
    if (confidence >= 0.5) return 'text-yellow-500'
    return 'text-orange-500'
  }

  const getConfidenceWidth = (confidence: number): string => {
    return `${confidence * 100}%`
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
          <h2 className="text-3xl font-bold">学习洞察</h2>
          <p className="text-base text-muted-foreground mt-1">
            基于您的交易历史的 AI 智能分析和建议
          </p>
        </div>
        <div className="flex items-center gap-4">
          <Button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="gap-2 text-base px-6 py-3 rounded-xl"
          >
            {analyzing ? (
              <RefreshCw className="h-5 w-5 animate-spin" />
            ) : (
              <Brain className="h-5 w-5" />
            )}
            {analyzing ? '分析中...' : '运行分析'}
          </Button>
          <Button onClick={loadData} variant="outline" size="lg">
            <RefreshCw className="h-5 w-5" />
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      {report && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card className="hover-lift">
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="p-3 rounded-xl bg-blue-100">
                  <Lightbulb className="h-6 w-6 text-blue-600" />
                </div>
                <div>
                  <p className="text-base text-muted-foreground">洞察</p>
                  <p className="text-3xl font-bold">{report.insights_count}</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card className="hover-lift">
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="p-3 rounded-xl bg-green-100">
                  <CheckCircle className="h-6 w-6 text-green-600" />
                </div>
                <div>
                  <p className="text-base text-muted-foreground">建议</p>
                  <p className="text-3xl font-bold">{report.recommendations_count}</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card className="hover-lift">
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="p-3 rounded-xl bg-purple-100">
                  <Zap className="h-6 w-6 text-purple-600" />
                </div>
                <div>
                  <p className="text-base text-muted-foreground">最高置信度</p>
                  <p className="text-3xl font-bold">
                    {report.top_insights.length > 0
                      ? `${(Math.max(...report.top_insights.map((i) => i.confidence)) * 100).toFixed(0)}%`
                      : '-'}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card className="hover-lift">
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="p-3 rounded-xl bg-orange-100">
                  <AlertTriangle className="h-6 w-6 text-orange-600" />
                </div>
                <div>
                  <p className="text-base text-muted-foreground">高优先级</p>
                  <p className="text-3xl font-bold">
                    {recommendations.filter((r) => r.priority === 'high').length}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="text-base">
          <TabsTrigger value="insights" className="text-base px-4 py-2">洞察</TabsTrigger>
          <TabsTrigger value="recommendations" className="text-base px-4 py-2">建议</TabsTrigger>
          <TabsTrigger value="summary" className="text-base px-4 py-2">摘要</TabsTrigger>
        </TabsList>

        <TabsContent value="insights" className="space-y-4">
          <div className="flex items-center gap-4 mb-4">
            <Select value={insightTypeFilter} onValueChange={setInsightTypeFilter}>
              <SelectTrigger className="w-48">
                <SelectValue placeholder="洞察类型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">所有类型</SelectItem>
                <SelectItem value="factor_performance">因子表现</SelectItem>
                <SelectItem value="market_regime">市场状态</SelectItem>
                <SelectItem value="entry_pattern">入场模式</SelectItem>
                <SelectItem value="exit_pattern">出场模式</SelectItem>
                <SelectItem value="risk_pattern">风险模式</SelectItem>
                <SelectItem value="timing_pattern">时机模式</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {insights.length === 0 ? (
            <Card>
              <CardContent className="pt-6 text-center">
                <Brain className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                <p className="text-muted-foreground">
                  暂无洞察。运行分析以从您的交易数据中生成洞察。
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {insights.map((insight, idx) => (
                <Card key={idx} className="overflow-hidden">
                  <CardContent className="pt-6">
                    <div className="flex items-start gap-4">
                      <div className="p-2 rounded-lg bg-blue-100 shrink-0">
                        {INSIGHT_TYPE_ICONS[insight.insight_type] || (
                          <Lightbulb className="h-4 w-4 text-blue-600" />
                        )}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center justify-between mb-2">
                          <h3 className="font-semibold">{insight.title}</h3>
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-muted-foreground">
                              {insight.supporting_trades} 笔交易
                            </span>
                            <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${
                                  insight.confidence >= 0.7
                                    ? 'bg-green-500'
                                    : insight.confidence >= 0.5
                                    ? 'bg-yellow-500'
                                    : 'bg-orange-500'
                                }`}
                                style={{ width: getConfidenceWidth(insight.confidence) }}
                              />
                            </div>
                            <span
                              className={`text-sm font-medium ${getConfidenceColor(
                                insight.confidence
                              )}`}
                            >
                              {(insight.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                        </div>
                        <p className="text-sm text-muted-foreground mb-3">
                          {insight.description}
                        </p>
                        {insight.evidence.length > 0 && (
                          <div className="mb-3 p-3 rounded bg-muted">
                            <p className="text-xs font-medium mb-2">证据:</p>
                            <ul className="space-y-1">
                              {insight.evidence.slice(0, 3).map((evidence, eIdx) => (
                                <li
                                  key={eIdx}
                                  className="text-sm text-muted-foreground flex items-start gap-2"
                                >
                                  <span className="text-blue-500">•</span>
                                  {evidence}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        <div className="flex items-start gap-2 p-3 rounded bg-green-50 border border-green-200">
                          <Lightbulb className="h-4 w-4 text-green-600 shrink-0 mt-0.5" />
                          <div>
                            <p className="text-sm font-medium text-green-800">建议</p>
                            <p className="text-sm text-green-700">{insight.recommendation}</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="recommendations" className="space-y-4">
          <div className="flex items-center gap-4 mb-4">
            <Select value={priorityFilter} onValueChange={setPriorityFilter}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="优先级" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">所有优先级</SelectItem>
                <SelectItem value="high">高</SelectItem>
                <SelectItem value="medium">中</SelectItem>
                <SelectItem value="low">低</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {recommendations.length === 0 ? (
            <Card>
              <CardContent className="pt-6 text-center">
                <CheckCircle className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
                <p className="text-muted-foreground">
                  暂无建议。运行分析以生成可操作的建议。
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {recommendations.map((rec, idx) => (
                <Card key={idx}>
                  <CardContent className="pt-6">
                    <div className="flex items-start gap-4">
                      <div
                        className={`px-3 py-1 rounded-full text-xs font-medium border ${PRIORITY_COLORS[rec.priority]}`}
                      >
                        {rec.priority.toUpperCase()}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center justify-between mb-2">
                          <h3 className="font-semibold">{rec.category}</h3>
                          <span className="text-sm text-muted-foreground">{rec.action}</span>
                        </div>
                        <p className="text-sm text-muted-foreground mb-3">
                          {rec.rationale}
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                          <div className="p-3 rounded bg-blue-50">
                            <p className="font-medium text-blue-800">预期影响</p>
                            <p className="text-blue-700">{rec.expected_impact}</p>
                          </div>
                          <div className="p-3 rounded bg-gray-100">
                            <p className="font-medium">实施方案</p>
                            <p className="text-muted-foreground">{rec.implementation}</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="summary" className="space-y-4">
          {report && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>热门洞察</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {report.top_insights.slice(0, 5).map((insight, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between p-3 rounded-lg bg-muted"
                      >
                        <div className="flex items-center gap-3">
                          <span className="font-bold text-lg w-6">{idx + 1}</span>
                          <div>
                            <p className="font-medium">{insight.title}</p>
                            <p className="text-sm text-muted-foreground">
                              {insight.recommendation}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-muted-foreground">
                            {insight.supporting_trades} 笔交易
                          </span>
                          <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 rounded-full"
                              style={{ width: `${insight.confidence * 100}%` }}
                            />
                          </div>
                          <span className="text-sm font-medium">
                            {(insight.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Card>
                  <CardHeader>
                    <CardTitle>因子表现</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {Object.entries(report.factor_performance_summary)
                        .slice(0, 5)
                        .map(([factor, data]) => (
                          <div
                            key={factor}
                            className="flex items-center justify-between p-2 rounded bg-muted"
                          >
                            <span className="font-medium">{factor}</span>
                            <div className="flex items-center gap-4 text-sm">
                              <span className="text-muted-foreground">
                                {data.sample_count} 样本
                              </span>
                              <span className="text-green-500">
                                +{data.avg_positive.toFixed(3)}
                              </span>
                              <span className="text-red-500">
                                {data.avg_negative.toFixed(3)}
                              </span>
                            </div>
                          </div>
                        ))}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle>市场状态表现</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {Object.entries(report.regime_performance_summary)
                        .slice(0, 5)
                        .map(([regime, data]) => (
                          <div
                            key={regime}
                            className="flex items-center justify-between p-2 rounded bg-muted"
                          >
                            <span className="font-medium capitalize">{regime}</span>
                            <div className="flex items-center gap-4 text-sm">
                              <span className="text-muted-foreground">
                                {data.trades} 笔交易
                              </span>
                              <span
                                className={
                                  data.win_rate >= 0.5 ? 'text-green-500' : 'text-red-500'
                                }
                              >
                                {(data.win_rate * 100).toFixed(0)}% WR
                              </span>
                              <span
                                className={
                                  data.avg_pnl >= 0 ? 'text-green-500' : 'text-red-500'
                                }
                              >
                                {data.avg_pnl >= 0 ? '+' : ''}
                                {data.avg_pnl.toFixed(2)}%
                              </span>
                            </div>
                          </div>
                        ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
