import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import PerformanceDashboard from './PerformanceDashboard'
import FactorAnalysis from './FactorAnalysis'
import TradeReviewList from './TradeReviewList'
import LearningInsights from './LearningInsights'
import NetPerformancePanel from './NetPerformancePanel'
import AgentPerformancePanel from './AgentPerformancePanel'

interface AnalyticsPageProps {
  accountId?: number
}

export default function AnalyticsPage({ accountId }: AnalyticsPageProps) {
  const { t } = useTranslation()
  const [tradingMode, setTradingMode] = useState('mainnet')
  const [activeTab, setActiveTab] = useState('performance')

  return (
    <div className="container mx-auto py-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">{t('analytics.title')}</h1>
          <p className="text-muted-foreground">
            {t('analytics.subtitle')}
          </p>
        </div>
        <Select value={tradingMode} onValueChange={setTradingMode}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder={t('analytics.tradingMode')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="mainnet">{t('analytics.mainnet')}</SelectItem>
            <SelectItem value="testnet">{t('analytics.testnet')}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-6">
          <TabsTrigger value="performance">{t('analytics.performance')}</TabsTrigger>
          <TabsTrigger value="netPerformance">净值扣费</TabsTrigger>
          <TabsTrigger value="agentPerformance">Mid/Long Agent</TabsTrigger>
          <TabsTrigger value="factors">{t('analytics.factors')}</TabsTrigger>
          <TabsTrigger value="reviews">{t('analytics.reviews')}</TabsTrigger>
          <TabsTrigger value="learning">{t('analytics.learning')}</TabsTrigger>
        </TabsList>

        <TabsContent value="performance" className="mt-6">
          <PerformanceDashboard accountId={accountId} tradingMode={tradingMode} />
        </TabsContent>

        <TabsContent value="netPerformance" className="mt-6">
          <NetPerformancePanel />
        </TabsContent>

        <TabsContent value="agentPerformance" className="mt-6">
          <AgentPerformancePanel />
        </TabsContent>

        <TabsContent value="factors" className="mt-6">
          <FactorAnalysis accountId={accountId} tradingMode={tradingMode} />
        </TabsContent>

        <TabsContent value="reviews" className="mt-6">
          <TradeReviewList accountId={accountId} tradingMode={tradingMode} />
        </TabsContent>

        <TabsContent value="learning" className="mt-6">
          <LearningInsights accountId={accountId} tradingMode={tradingMode} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
