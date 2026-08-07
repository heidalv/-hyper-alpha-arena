/**
 * ATAS V2 策略中心 - 完整管理面板
 * 集成回测引擎、风险管理、系统监控等所有新功能
 * 【已整合】使用 AccountSnapshotContext 共享数据，与 AI 策略中心共用同一数据源
 */
import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { 
  Activity, TrendingUp, Shield, Heart, AlertTriangle, 
  Zap, CheckCircle2, XCircle, Info 
} from 'lucide-react';
import { formatPrice, formatSize } from '@/lib/priceFormat';
import { fmtDateTime } from '@/lib/utils';
import { useAccountSnapshot } from '@/contexts/AccountSnapshotContext';

interface Portfolio {
  account_id: number;
  total_value: number;
  capital: number;
  positions: Record<string, any>;
  active_strategies: number;
  unrealized_pnl: number;
  daily_pnl: number;
  current_drawdown: number;
  peak_equity?: number;
  cash_ratio: number;
  data_source?: string;
  error?: string;
}

interface HealthScore {
  overall: number;
  performance: number;
  risk: number;
  stability: number;
  liquidity: number;
}

interface RiskAlert {
  level: string;
  category: string;
  message: string;
  timestamp: string;
}

interface ATASV2DashboardProps {
  accountId: number;
  accountName?: string;
}

export default function ATASV2Dashboard({ accountId, accountName }: ATASV2DashboardProps) {
  const snapshotCtx = useAccountSnapshot();
  const [activeTab, setActiveTab] = useState('overview');

  // 使用共享 Context 数据（与 AI 策略中心同源）
  const portfolio = snapshotCtx?.accountId === accountId ? (snapshotCtx.snapshot?.portfolio as Portfolio | null) ?? null : null;
  const healthScore = snapshotCtx?.accountId === accountId ? (snapshotCtx.snapshot?.health_score as HealthScore | null) ?? null : null;
  const riskAlerts = snapshotCtx?.accountId === accountId ? (snapshotCtx.snapshot?.risk_alerts as RiskAlert[]) ?? [] : [];
  const metrics = snapshotCtx?.accountId === accountId ? snapshotCtx.snapshot?.metrics ?? null : null;
  const initialLoading = snapshotCtx?.loading ?? true;
  const handleManualRefresh = () => snapshotCtx?.refresh?.();

  // 健康度颜色
  const getHealthColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 60) return 'text-yellow-600';
    return 'text-red-600';
  };

  // 健康度图标
  const getHealthIcon = (score: number) => {
    if (score >= 80) return <CheckCircle2 className="h-5 w-5 text-green-600" />;
    if (score >= 60) return <AlertTriangle className="h-5 w-5 text-yellow-600" />;
    return <XCircle className="h-5 w-5 text-red-600" />;
  };

  // 首次加载时显示全屏 loading
  if (initialLoading && !portfolio) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <Activity className="h-8 w-8 animate-spin mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">加载ATAS V2数据...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Zap className="h-6 w-6 text-blue-600" />
            ATAS V2 策略中心
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {accountName && `账户: ${accountName} `}
            (ID: {accountId})
          </p>
        </div>
        <div className="flex items-center gap-3">
          {initialLoading && (
            <Activity className="h-4 w-4 animate-spin text-muted-foreground" />
          )}
          {portfolio && snapshotCtx?.snapshot?.timestamp && !initialLoading && (
            <span className="text-xs text-muted-foreground">
              更新于 {new Date((snapshotCtx.snapshot.timestamp as number) * 1000).toLocaleTimeString()}
            </span>
          )}
          <Button onClick={handleManualRefresh} variant="outline" size="sm" disabled={initialLoading}>
            <Activity className={`h-4 w-4 mr-2 ${initialLoading ? 'animate-spin' : ''}`} />
            {initialLoading ? '刷新中...' : '刷新'}
          </Button>
        </div>
      </div>

      {/* 概览卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 总资产 */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">总资产</p>
                <p className="text-2xl font-bold">
                  ${portfolio?.total_value?.toFixed(2) || '0.00'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  现金: ${portfolio?.capital?.toFixed(2) || '0.00'}
                </p>
              </div>
              <TrendingUp className="h-8 w-8 text-blue-600" />
            </div>
          </CardContent>
        </Card>

        {/* 健康度 */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">账户健康度</p>
                <p className={`text-2xl font-bold ${getHealthColor(healthScore?.overall || 0)}`}>
                  {healthScore?.overall?.toFixed(0) || '0'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  综合评分
                </p>
              </div>
              {getHealthIcon(healthScore?.overall || 0)}
            </div>
          </CardContent>
        </Card>

        {/* 风险等级 */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">风险监控</p>
                <p className="text-2xl font-bold">
                  {riskAlerts.length}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  活跃预警
                </p>
              </div>
              <Shield className={`h-8 w-8 ${riskAlerts.length > 0 ? 'text-red-600' : 'text-green-600'}`} />
            </div>
          </CardContent>
        </Card>

        {/* 系统状态 */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">系统健康</p>
                <p className="text-2xl font-bold">
                  {metrics?.system_health || '正常'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  CPU: {metrics?.cpu_usage?.toFixed(1) || '0'}%
                </p>
              </div>
              <Heart className="h-8 w-8 text-green-600" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* 详细信息标签页 */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="health">健康度</TabsTrigger>
          <TabsTrigger value="risk">风险监控</TabsTrigger>
          <TabsTrigger value="metrics">系统指标</TabsTrigger>
        </TabsList>

        {/* 概览标签 */}
        <TabsContent value="overview" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>投资组合概览</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">总价值</p>
                  <p className="text-lg font-semibold">${portfolio?.total_value?.toFixed(2) || '0.00'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">可用资金</p>
                  <p className="text-lg font-semibold">${portfolio?.capital?.toFixed(2) || '0.00'}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">今日盈亏</p>
                  <p className={`text-lg font-semibold ${(portfolio?.daily_pnl || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {(portfolio?.daily_pnl || 0) >= 0 ? '+' : ''}${portfolio?.daily_pnl?.toFixed(2) || '0.00'}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">未实现盈亏</p>
                  <p className={`text-lg font-semibold ${(portfolio?.unrealized_pnl || 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {(portfolio?.unrealized_pnl || 0) >= 0 ? '+' : ''}${portfolio?.unrealized_pnl?.toFixed(2) || '0.00'}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">当前回撤</p>
                  <p className={`text-lg font-semibold ${(portfolio?.current_drawdown || 0) > 0.05 ? 'text-red-600' : (portfolio?.current_drawdown || 0) > 0.02 ? 'text-yellow-600' : 'text-green-600'}`}>
                    {((portfolio?.current_drawdown || 0) * 100).toFixed(2)}%
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">现金比例</p>
                  <p className="text-lg font-semibold">{((portfolio?.cash_ratio || 0) * 100).toFixed(1)}%</p>
                </div>
              </div>

              {portfolio?.positions && Object.keys(portfolio.positions).length > 0 && (
                <div className="mt-6">
                  <h4 className="text-sm font-semibold mb-3">持仓明细</h4>
                  <div className="space-y-2">
                    {Object.entries(portfolio.positions).map(([symbol, pos]: [string, any]) => {
                      const baseSymbol = (symbol || '').replace(/\/?USDT$/i, '') || symbol;
                      return (
                      <div key={symbol} className="flex items-center justify-between p-3 border rounded-lg">
                        <div>
                          <p className="font-semibold">{symbol}</p>
                          <p className="text-xs text-muted-foreground">
                            数量: {formatSize(pos.quantity, baseSymbol)}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="font-semibold">${formatPrice(pos.current_price, baseSymbol)}</p>
                          <p className={`text-xs ${pos.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {pos.unrealized_pnl >= 0 ? '+' : ''}{pos.unrealized_pnl?.toFixed(2) || '0.00'}
                          </p>
                        </div>
                      </div>
                    );})}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 健康度标签 */}
        <TabsContent value="health" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>账户健康度评分</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-center justify-between p-4 border rounded-lg">
                  <div className="flex items-center gap-3">
                    <div className={`text-3xl font-bold ${getHealthColor(healthScore?.overall || 0)}`}>
                      {healthScore?.overall?.toFixed(0) || '0'}
                    </div>
                    <div>
                      <p className="font-semibold">综合评分</p>
                      <p className="text-xs text-muted-foreground">整体健康状况</p>
                    </div>
                  </div>
                  {getHealthIcon(healthScore?.overall || 0)}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="p-4 border rounded-lg">
                    <p className="text-sm text-muted-foreground">表现得分</p>
                    <p className="text-2xl font-bold text-blue-600">{healthScore?.performance?.toFixed(0) || '0'}</p>
                  </div>
                  <div className="p-4 border rounded-lg">
                    <p className="text-sm text-muted-foreground">风险得分</p>
                    <p className="text-2xl font-bold text-green-600">{healthScore?.risk?.toFixed(0) || '0'}</p>
                  </div>
                  <div className="p-4 border rounded-lg">
                    <p className="text-sm text-muted-foreground">稳定得分</p>
                    <p className="text-2xl font-bold text-purple-600">{healthScore?.stability?.toFixed(0) || '0'}</p>
                  </div>
                  <div className="p-4 border rounded-lg">
                    <p className="text-sm text-muted-foreground">流动得分</p>
                    <p className="text-2xl font-bold text-orange-600">{healthScore?.liquidity?.toFixed(0) || '0'}</p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 风险监控标签 */}
        <TabsContent value="risk" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>风险预警</span>
                <Badge variant={riskAlerts.length > 0 ? 'destructive' : 'default'}>
                  {riskAlerts.length} 个预警
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {riskAlerts.length === 0 ? (
                <div className="text-center py-8">
                  <CheckCircle2 className="h-12 w-12 text-green-600 mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground">暂无风险预警</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {riskAlerts.map((alert, index) => (
                    <div key={index} className={`p-4 border-l-4 rounded-lg ${
                      alert.level === 'critical' ? 'border-red-600 bg-red-50' :
                      alert.level === 'warning' ? 'border-yellow-600 bg-yellow-50' :
                      'border-blue-600 bg-blue-50'
                    }`}>
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <Badge variant={alert.level === 'critical' ? 'destructive' : 'default'}>
                              {alert.level.toUpperCase()}
                            </Badge>
                            <span className="text-xs text-muted-foreground">{alert.category}</span>
                          </div>
                          <p className="text-sm font-medium">{alert.message}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            {fmtDateTime(alert.timestamp)}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 系统指标标签 */}
        <TabsContent value="metrics" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>系统监控指标</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">CPU使用率</p>
                  <p className="text-2xl font-bold">{metrics?.cpu_usage?.toFixed(1) || '0'}%</p>
                </div>
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">内存使用率</p>
                  <p className="text-2xl font-bold">{metrics?.memory_usage?.toFixed(1) || '0'}%</p>
                </div>
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">磁盘使用率</p>
                  <p className="text-2xl font-bold">{metrics?.disk_usage?.toFixed(1) || '0'}%</p>
                </div>
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">活跃策略</p>
                  <p className="text-2xl font-bold">{metrics?.active_strategies || '0'}</p>
                </div>
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">持仓总数</p>
                  <p className="text-2xl font-bold">{metrics?.total_positions || '0'}</p>
                </div>
                <div className="p-4 border rounded-lg">
                  <p className="text-sm text-muted-foreground">今日盈亏</p>
                  <p className={`text-2xl font-bold ${metrics?.daily_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {metrics?.daily_pnl >= 0 ? '+' : ''}${metrics?.daily_pnl?.toFixed(2) || '0.00'}
                  </p>
                </div>
              </div>

              <div className="mt-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                <div className="flex items-start gap-3">
                  <Info className="h-5 w-5 text-blue-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-semibold text-blue-900 dark:text-blue-100">系统状态: {metrics?.system_health || '正常'}</p>
                    <p className="text-xs text-blue-700 dark:text-blue-300 mt-1">
                      更新时间: {(() => {
                        const m = metrics as { timestamp?: string | number } | null | undefined;
                        const ts = m?.timestamp;
                        if (ts == null || ts === '') return '--';
                        if (typeof ts === 'number') return new Date(ts).toLocaleString();
                        return fmtDateTime(ts);
                      })()}
                    </p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
