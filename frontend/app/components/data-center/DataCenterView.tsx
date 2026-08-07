import { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import {
  Database, Play, Square, RefreshCw, CheckCircle2, XCircle,
  Clock, Loader2, ArrowDownToLine, BarChart3, AlertTriangle,
  Plus, Download
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { apiRequest } from '@/lib/api';
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs';

interface SubTask {
  symbol: string;
  period: string;
  status: string;
  progress: number;
  total_expected: number;
  collected: number;
  existing_in_db: number;
  error: string;
}

interface SyncProgress {
  status: string;
  exchange: string;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  current_task: string;
  overall_progress: number;
  started_at: string | null;
  estimated_remaining_seconds: number;
  total_records_synced: number;
  error: string;
  sub_tasks: SubTask[];
}

interface DataSummaryItem {
  symbol: string;
  period: string;
  record_count: number;
  earliest: string | null;
  latest: string | null;
  days_covered: number;
}

interface DataSummary {
  exchange: string;
  total_records: number;
  data: DataSummaryItem[];
}

interface MarketDataMetricItem {
  name: string;
  count: number;
  success: number;
  failed: number;
  success_rate: number | null;
  avg_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
  last_ms: number | null;
  last_ok: boolean | null;
  last_error: string | null;
  last_at: string | null;
}

interface MarketDataMetrics {
  started_at: string;
  generated_at: string;
  total_count: number;
  total_failed: number;
  overall_success_rate: number | null;
  metrics: Record<string, MarketDataMetricItem>;
}

interface ExchangeProfile {
  exchange: string;
  records: number;
  symbols: number;
  periods: number;
  freshness_seconds: number | null;
  raw_events: number;
  raw_symbols: number;
  raw_freshness_seconds: number | null;
  status: string;
  shadow_compare?: {
    overall_match_rate: number | null;
    checks: Array<{
      symbol: string;
      timeframe: string;
      status: string;
      compared: number;
      matched: number;
      match_rate: number | null;
      mismatch_count: number;
    }>;
  };
}

interface ExchangeProfilesResponse {
  generated_at: string;
  profiles: ExchangeProfile[];
  queue: {
    enabled: boolean;
    running: boolean;
    queue_size: number;
    total_tasks: number;
    counts: Record<string, number>;
  };
  raw_summary: {
    total: number;
    groups: Array<Record<string, any>>;
  };
}

const PERIOD_LABELS: Record<string, string> = {
  '1d': '日线', '4h': '4小时', '1h': '1小时', '30m': '30分钟',
  '15m': '15分钟', '5m': '5分钟', '1m': '1分钟',
};

const DEFAULT_PERIODS = ['1d', '4h', '1h', '30m', '15m', '5m', '1m'];

export default function DataCenterView() {
  const { symbols: configuredPairs } = useTradingPairs()
  const activeSymbols = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  const [syncProgress, setSyncProgress] = useState<SyncProgress | null>(null);
  const [dataSummary, setDataSummary] = useState<DataSummary | null>(null);
  const [marketDataMetrics, setMarketDataMetrics] = useState<MarketDataMetrics | null>(null);
  const [exchangeProfiles, setExchangeProfiles] = useState<ExchangeProfilesResponse | null>(null);
  const [metricsUnavailable, setMetricsUnavailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);

  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [selectedPeriods, setSelectedPeriods] = useState<string[]>(DEFAULT_PERIODS);
  const [syncDays, setSyncDays] = useState(365);
  const [customSymbol, setCustomSymbol] = useState('');

  const [quickSyncing, setQuickSyncing] = useState<Record<string, boolean>>({});
  const [symbolDataStatus, setSymbolDataStatus] = useState<Record<string, { has_data: boolean; sufficient: boolean; coverage: number }>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProgress = useCallback(async () => {
    try {
      const res = await apiRequest('/klines/history-sync/progress');
      const data = await res.json();
      setSyncProgress(data);
      return data;
    } catch (e) {
      console.error('Load progress failed:', e);
      return null;
    }
  }, []);

  const loadSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const res = await apiRequest('/klines/history-sync/data-summary');
      const data = await res.json();
      setDataSummary(data);
    } catch (e) {
      console.error('Load summary failed:', e);
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const loadMetrics = useCallback(async () => {
    try {
      const res = await apiRequest('/klines/metrics');
      if (!res.ok) {
        setMetricsUnavailable(true);
        return;
      }
      const data = await res.json();
      setMarketDataMetrics(data);
      setMetricsUnavailable(false);
    } catch (e) {
      console.error('Load market data metrics failed:', e);
      setMetricsUnavailable(true);
    }
  }, []);

  const loadExchangeProfiles = useCallback(async () => {
    try {
      const res = await apiRequest('/market-data-v2/exchange-profiles');
      if (!res.ok) return;
      const data = await res.json();
      setExchangeProfiles(data);
    } catch (e) {
      console.error('Load exchange profiles failed:', e);
    }
  }, []);

  useEffect(() => {
    if (activeSymbols.length > 0 && selectedSymbols.length === 0) {
      setSelectedSymbols(activeSymbols)
    }
  }, [activeSymbols])

  useEffect(() => {
    loadProgress();
    loadSummary();
    loadMetrics();
    loadExchangeProfiles();
    selectedSymbols.forEach(s => checkSymbolData(s));
  }, []);

  useEffect(() => {
    if (syncProgress?.status === 'running' || syncProgress?.status === 'stopping') {
      if (!pollRef.current) {
        pollRef.current = setInterval(loadProgress, 10000);
      }
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [syncProgress?.status, loadProgress]);

  const startSync = async () => {
    if (selectedSymbols.length === 0) {
      toast.error('请至少选择一个交易对');
      return;
    }
    setLoading(true);
    try {
      const res = await apiRequest('/klines/history-sync/start', {
        method: 'POST',
        body: JSON.stringify({
          symbols: selectedSymbols,
          periods: selectedPeriods,
          days: syncDays,
        }),
      });
      const data = await res.json();
      if (data.error) {
        toast.error(data.error, { duration: 6000 });
        return;
      }
      toast.success(`同步已启动: ${data.pending_tasks}个任务, ${data.skipped_tasks}个已跳过`);
      await loadProgress();
    } catch (e: any) {
      toast.error(`启动失败: ${e.message || e}`, { duration: 6000 });
    } finally {
      setLoading(false);
    }
  };

  const stopSync = async () => {
    try {
      await apiRequest('/klines/history-sync/stop', { method: 'POST' });
      toast.success('正在停止同步...');
      await loadProgress();
    } catch (e: any) {
      toast.error(`停止失败: ${e.message || e}`);
    }
  };

  const toggleSymbol = (s: string) => {
    setSelectedSymbols(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    );
  };

  const togglePeriod = (p: string) => {
    setSelectedPeriods(prev =>
      prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]
    );
  };

  const checkSymbolData = useCallback(async (sym: string) => {
    try {
      const res = await apiRequest(`/klines/history-sync/check-symbol?symbol=${encodeURIComponent(sym)}`);
      const data = await res.json();
      setSymbolDataStatus(prev => ({
        ...prev,
        [sym]: { has_data: data.has_data, sufficient: data.sufficient, coverage: data.overall_coverage },
      }));
      return data;
    } catch {
      return null;
    }
  }, []);

  const quickSyncSymbol = useCallback(async (sym: string) => {
    setQuickSyncing(prev => ({ ...prev, [sym]: true }));
    const toastId = toast.loading(`正在同步 ${sym} 历史数据...`);
    try {
      const res = await apiRequest('/klines/history-sync/quick-sync', {
        method: 'POST',
        body: JSON.stringify({ symbol: sym, days: syncDays }),
      });
      const data = await res.json();
      toast.dismiss(toastId);
      toast.success(`${sym} 同步完成: +${data.total_collected?.toLocaleString() || 0} 条`, { duration: 4000 });
      setSymbolDataStatus(prev => ({ ...prev, [sym]: { has_data: true, sufficient: true, coverage: 100 } }));
      loadSummary();
    } catch (e: any) {
      toast.dismiss(toastId);
      toast.error(`${sym} 同步失败: ${e.message || e}`, { duration: 5000 });
    } finally {
      setQuickSyncing(prev => ({ ...prev, [sym]: false }));
    }
  }, [syncDays, loadSummary]);

  const addCustomSymbol = async () => {
    const sym = customSymbol.trim().toUpperCase();
    if (!sym) return;
    if (!/^[A-Z0-9]{1,10}$/.test(sym)) {
      toast.error('交易对名称只允许字母和数字，长度1~10');
      return;
    }
    if (selectedSymbols.includes(sym)) {
      toast.error(`${sym} 已在列表中`);
      return;
    }

    setSelectedSymbols(prev => [...prev, sym]);
    setCustomSymbol('');

    const status = await checkSymbolData(sym);
    if (status && !status.sufficient) {
      toast(`${sym} 缺少历史数据，自动开始同步...`, { icon: '📡', duration: 3000 });
      quickSyncSymbol(sym);
    } else if (status?.sufficient) {
      toast.success(`${sym} 历史数据充足 (覆盖率 ${status.overall_coverage}%)`);
    }
  };

  const isRunning = syncProgress?.status === 'running' || syncProgress?.status === 'stopping';

  const formatTime = (seconds: number) => {
    if (seconds <= 0) return '--';
    if (seconds < 60) return `${seconds}秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
    return `${Math.floor(seconds / 3600)}时${Math.floor((seconds % 3600) / 60)}分`;
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'running': return <Badge className="bg-blue-500 text-white animate-pulse">同步中</Badge>;
      case 'completed': return <Badge className="bg-green-500 text-white">已完成</Badge>;
      case 'failed': return <Badge className="bg-red-500 text-white">失败</Badge>;
      case 'paused': return <Badge className="bg-yellow-500 text-white">已暂停</Badge>;
      case 'stopping': return <Badge className="bg-yellow-500 text-white animate-pulse">停止中</Badge>;
      case 'skipped': return <Badge variant="outline">已跳过</Badge>;
      case 'pending': return <Badge variant="outline" className="text-muted-foreground">等待中</Badge>;
      default: return <Badge variant="outline">{status}</Badge>;
    }
  };

  // 按 symbol 分组 summary
  const groupedSummary = dataSummary?.data.reduce<Record<string, DataSummaryItem[]>>((acc, item) => {
    (acc[item.symbol] = acc[item.symbol] || []).push(item);
    return acc;
  }, {}) || {};
  const metricItems = Object.values(marketDataMetrics?.metrics || {});
  const slowestMetric = metricItems
    .filter(item => item.p95_ms !== null)
    .sort((a, b) => (b.p95_ms || 0) - (a.p95_ms || 0))[0];
  const latestFailedMetric = metricItems.find(item => item.last_ok === false);
  const formatFreshness = (seconds: number | null | undefined) => {
    if (seconds === null || seconds === undefined) return '--';
    if (seconds < 60) return `${seconds}秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}时`;
    return `${Math.floor(seconds / 86400)}天`;
  };

  return (
    <div className="space-y-6 p-4 max-w-7xl mx-auto">
      {/* 标题 */}
      <div className="flex items-center gap-3">
        <Database className="h-7 w-7 text-blue-600" />
        <div>
          <h1 className="text-2xl font-bold">数据中心</h1>
          <p className="text-sm text-muted-foreground">
            管理历史K线数据，支持策略回测和AI分析
          </p>
        </div>
      </div>

      {/* 吞吐指标 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5" />
              市场数据吞吐指标
            </CardTitle>
            <Button onClick={loadMetrics} variant="ghost" size="sm">
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {metricsUnavailable ? (
            <div className="text-sm text-muted-foreground">
              指标接口暂不可用。通常是后端还没重启加载新代码，重启后会显示实时吞吐。
            </div>
          ) : !marketDataMetrics || metricItems.length === 0 ? (
            <div className="text-sm text-muted-foreground">
              暂无指标。访问 K线页面或刷新数据中心后，这里会开始累积调用耗时和成功率。
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="rounded-lg bg-blue-50 dark:bg-blue-950/30 p-3">
                  <p className="text-lg font-bold text-blue-600">{marketDataMetrics.total_count}</p>
                  <p className="text-xs text-muted-foreground">总调用</p>
                </div>
                <div className="rounded-lg bg-green-50 dark:bg-green-950/30 p-3">
                  <p className="text-lg font-bold text-green-600">
                    {marketDataMetrics.overall_success_rate === null
                      ? '--'
                      : `${(marketDataMetrics.overall_success_rate * 100).toFixed(1)}%`}
                  </p>
                  <p className="text-xs text-muted-foreground">成功率</p>
                </div>
                <div className="rounded-lg bg-purple-50 dark:bg-purple-950/30 p-3">
                  <p className="text-lg font-bold text-purple-600">{slowestMetric?.p95_ms ?? '--'}ms</p>
                  <p className="text-xs text-muted-foreground">最高 P95</p>
                </div>
                <div className="rounded-lg bg-red-50 dark:bg-red-950/30 p-3">
                  <p className="text-lg font-bold text-red-600">{marketDataMetrics.total_failed}</p>
                  <p className="text-xs text-muted-foreground">失败次数</p>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b">
                    <tr>
                      <th className="text-left py-2 pr-3 font-medium">链路</th>
                      <th className="text-right py-2 px-3 font-medium">次数</th>
                      <th className="text-right py-2 px-3 font-medium">成功率</th>
                      <th className="text-right py-2 px-3 font-medium">平均</th>
                      <th className="text-right py-2 px-3 font-medium">P95</th>
                      <th className="text-right py-2 pl-3 font-medium">最近</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metricItems.map(item => (
                      <tr key={item.name} className="border-b border-border/50">
                        <td className="py-2 pr-3 font-mono text-xs">{item.name}</td>
                        <td className="py-2 px-3 text-right">{item.count}</td>
                        <td className="py-2 px-3 text-right">
                          {item.success_rate === null ? '--' : `${(item.success_rate * 100).toFixed(1)}%`}
                        </td>
                        <td className="py-2 px-3 text-right">{item.avg_ms ?? '--'}ms</td>
                        <td className="py-2 px-3 text-right">{item.p95_ms ?? '--'}ms</td>
                        <td className="py-2 pl-3 text-right">
                          <span className={item.last_ok === false ? 'text-red-500' : 'text-muted-foreground'}>
                            {item.last_ms ?? '--'}ms
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {latestFailedMetric?.last_error && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-600">
                  最近错误：{latestFailedMetric.name} - {latestFailedMetric.last_error}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 多交易所数据画像 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              多交易所数据画像
            </CardTitle>
            <Button onClick={loadExchangeProfiles} variant="ghost" size="sm">
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {!exchangeProfiles ? (
            <div className="text-sm text-muted-foreground">正在等待数据画像...</div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="rounded-lg bg-blue-50 dark:bg-blue-950/30 p-3">
                  <p className="text-lg font-bold text-blue-600">{exchangeProfiles.profiles.length}</p>
                  <p className="text-xs text-muted-foreground">交易所</p>
                </div>
                <div className="rounded-lg bg-green-50 dark:bg-green-950/30 p-3">
                  <p className="text-lg font-bold text-green-600">
                    {exchangeProfiles.profiles.reduce((sum, p) => sum + p.records, 0).toLocaleString()}
                  </p>
                  <p className="text-xs text-muted-foreground">旧链路 K线</p>
                </div>
                <div className="rounded-lg bg-purple-50 dark:bg-purple-950/30 p-3">
                  <p className="text-lg font-bold text-purple-600">{exchangeProfiles.raw_summary.total.toLocaleString()}</p>
                  <p className="text-xs text-muted-foreground">Raw Events</p>
                </div>
                <div className="rounded-lg bg-orange-50 dark:bg-orange-950/30 p-3">
                  <p className="text-lg font-bold text-orange-600">
                    {exchangeProfiles.queue.queue_size}/{exchangeProfiles.queue.total_tasks}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    V2队列 {exchangeProfiles.queue.enabled ? '已开' : '关闭'}
                  </p>
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b">
                    <tr>
                      <th className="text-left py-2 pr-3 font-medium">交易所</th>
                      <th className="text-right py-2 px-3 font-medium">K线记录</th>
                      <th className="text-right py-2 px-3 font-medium">交易对</th>
                      <th className="text-right py-2 px-3 font-medium">周期</th>
                      <th className="text-right py-2 px-3 font-medium">数据延迟</th>
                      <th className="text-right py-2 px-3 font-medium">Raw</th>
                      <th className="text-right py-2 px-3 font-medium">一致率</th>
                      <th className="text-right py-2 pl-3 font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exchangeProfiles.profiles.map(profile => (
                      <tr key={profile.exchange} className="border-b border-border/50">
                        <td className="py-2 pr-3 font-medium">{profile.exchange}</td>
                        <td className="py-2 px-3 text-right">{profile.records.toLocaleString()}</td>
                        <td className="py-2 px-3 text-right">{profile.symbols}</td>
                        <td className="py-2 px-3 text-right">{profile.periods}</td>
                        <td className="py-2 px-3 text-right">{formatFreshness(profile.freshness_seconds)}</td>
                        <td className="py-2 px-3 text-right">{profile.raw_events.toLocaleString()}</td>
                        <td className="py-2 px-3 text-right">
                          {profile.shadow_compare?.overall_match_rate === null || profile.shadow_compare?.overall_match_rate === undefined
                            ? '--'
                            : `${(profile.shadow_compare.overall_match_rate * 100).toFixed(1)}%`}
                        </td>
                        <td className="py-2 pl-3 text-right">
                          <Badge variant={profile.status === 'healthy' ? 'default' : 'outline'}>
                            {profile.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="text-xs text-muted-foreground">
                V2 旁路默认关闭，不会替换旧 K线页面和 FullAuto。打开后 raw event 会先进入旁路表，再用一致率接口和旧 K线对比。
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 同步控制面板 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ArrowDownToLine className="h-5 w-5" />
            历史数据同步
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 交易对选择 */}
          <div>
            <label className="text-sm font-medium mb-2 block">交易对</label>
            <div className="flex flex-wrap gap-2 items-center">
              {[...new Set([...activeSymbols, ...selectedSymbols])].map(s => {
                const status = symbolDataStatus[s];
                const syncing = quickSyncing[s];
                return (
                  <div key={s} className="relative group">
                    <button
                      onClick={() => toggleSymbol(s)}
                      disabled={isRunning}
                      className={`px-3 py-1.5 text-sm rounded-full border transition-all ${
                        selectedSymbols.includes(s)
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'bg-background border-border text-muted-foreground hover:border-blue-400'
                      } ${isRunning ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      {syncing && <Loader2 className="h-3 w-3 animate-spin inline mr-1" />}
                      {s}
                      {status && !syncing && (
                        status.sufficient
                          ? <CheckCircle2 className="h-3 w-3 inline ml-1 text-green-300" />
                          : <AlertTriangle className="h-3 w-3 inline ml-1 text-yellow-300" />
                      )}
                    </button>
                    {/* 数据不足时显示快速同步按钮 */}
                    {status && !status.sufficient && !syncing && !isRunning && selectedSymbols.includes(s) && (
                      <button
                        onClick={(e) => { e.stopPropagation(); quickSyncSymbol(s); }}
                        className="absolute -top-1 -right-1 bg-orange-500 text-white rounded-full w-4 h-4 flex items-center justify-center text-[10px] hover:bg-orange-600 transition-colors"
                        title={`${s} 数据不足(${status.coverage}%)，点击同步`}
                      >
                        <Download className="h-2.5 w-2.5" />
                      </button>
                    )}
                  </div>
                );
              })}
              <div className="flex items-center gap-1">
                <input
                  value={customSymbol}
                  onChange={e => setCustomSymbol(e.target.value.toUpperCase())}
                  onKeyDown={e => e.key === 'Enter' && addCustomSymbol()}
                  placeholder="添加新交易对..."
                  disabled={isRunning}
                  className="w-32 px-2 py-1.5 text-sm rounded-lg border bg-background"
                />
                <Button size="sm" variant="ghost" onClick={addCustomSymbol} disabled={isRunning || !customSymbol.trim()}>
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground mt-1.5">
              添加新交易对时自动检查并同步历史数据 &nbsp;|&nbsp;
              <CheckCircle2 className="h-3 w-3 inline text-green-500" /> 数据充足 &nbsp;
              <AlertTriangle className="h-3 w-3 inline text-yellow-500" /> 数据不足
            </p>
          </div>

          {/* 周期选择 */}
          <div>
            <label className="text-sm font-medium mb-2 block">数据周期</label>
            <div className="flex flex-wrap gap-2">
              {Object.entries(PERIOD_LABELS).map(([p, label]) => (
                <button
                  key={p}
                  onClick={() => togglePeriod(p)}
                  disabled={isRunning}
                  className={`px-3 py-1.5 text-sm rounded-full border transition-all ${
                    selectedPeriods.includes(p)
                      ? 'bg-purple-600 text-white border-purple-600'
                      : 'bg-background border-border text-muted-foreground hover:border-purple-400'
                  } ${isRunning ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 天数选择 */}
          <div className="flex items-center gap-4">
            <label className="text-sm font-medium">同步范围</label>
            <div className="flex gap-2">
              {[30, 90, 180, 365].map(d => (
                <button
                  key={d}
                  onClick={() => setSyncDays(d)}
                  disabled={isRunning}
                  className={`px-3 py-1.5 text-sm rounded-lg border transition-all ${
                    syncDays === d
                      ? 'bg-green-600 text-white border-green-600'
                      : 'bg-background border-border text-muted-foreground hover:border-green-400'
                  }`}
                >
                  {d === 365 ? '1年' : `${d}天`}
                </button>
              ))}
            </div>
            <span className="text-xs text-muted-foreground">
              预计 {selectedSymbols.length * selectedPeriods.length} 个子任务
            </span>
          </div>

          {/* 操作按钮 */}
          <div className="flex gap-3 pt-2">
            {!isRunning ? (
              <Button onClick={startSync} disabled={loading} className="gap-2">
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                开始同步
              </Button>
            ) : (
              <Button onClick={stopSync} variant="destructive" className="gap-2">
                <Square className="h-4 w-4" />
                停止同步
              </Button>
            )}
            <Button onClick={() => { loadProgress(); loadSummary(); loadMetrics(); loadExchangeProfiles(); }} variant="outline" className="gap-2">
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 同步进度 */}
      {syncProgress && syncProgress.status !== 'idle' && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                {isRunning && <Loader2 className="h-5 w-5 animate-spin text-blue-500" />}
                同步进度
              </CardTitle>
              {getStatusBadge(syncProgress.status)}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 总体进度条 */}
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span>
                  {syncProgress.current_task && (
                    <span className="text-blue-600 font-medium">
                      正在同步: {syncProgress.current_task}
                    </span>
                  )}
                </span>
                <span className="text-muted-foreground">
                  {syncProgress.completed_tasks}/{syncProgress.total_tasks} 
                  {syncProgress.failed_tasks > 0 && (
                    <span className="text-red-500 ml-1">({syncProgress.failed_tasks}失败)</span>
                  )}
                </span>
              </div>
              <div className="w-full bg-muted rounded-full h-3 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-blue-600 rounded-full transition-all duration-500"
                  style={{ width: `${syncProgress.overall_progress}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground mt-1">
                <span>{syncProgress.overall_progress.toFixed(1)}%</span>
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  预计剩余: {formatTime(syncProgress.estimated_remaining_seconds)}
                </span>
              </div>
            </div>

            {/* 统计数据 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-blue-50 dark:bg-blue-950/30 rounded-lg p-3 text-center">
                <p className="text-lg font-bold text-blue-600">{syncProgress.exchange}</p>
                <p className="text-xs text-muted-foreground">交易所</p>
              </div>
              <div className="bg-green-50 dark:bg-green-950/30 rounded-lg p-3 text-center">
                <p className="text-lg font-bold text-green-600">
                  {syncProgress.total_records_synced.toLocaleString()}
                </p>
                <p className="text-xs text-muted-foreground">已同步记录</p>
              </div>
              <div className="bg-purple-50 dark:bg-purple-950/30 rounded-lg p-3 text-center">
                <p className="text-lg font-bold text-purple-600">{syncProgress.completed_tasks}</p>
                <p className="text-xs text-muted-foreground">完成任务</p>
              </div>
              <div className="bg-orange-50 dark:bg-orange-950/30 rounded-lg p-3 text-center">
                <p className="text-lg font-bold text-orange-600">
                  {syncProgress.started_at 
                    ? `${((Date.now() - new Date(syncProgress.started_at).getTime()) / 60000).toFixed(0)}分`
                    : '--'}
                </p>
                <p className="text-xs text-muted-foreground">已运行</p>
              </div>
            </div>

            {/* 子任务列表 */}
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-background border-b">
                  <tr>
                    <th className="text-left py-2 px-2 font-medium">交易对</th>
                    <th className="text-left py-2 px-2 font-medium">周期</th>
                    <th className="text-left py-2 px-2 font-medium">状态</th>
                    <th className="text-right py-2 px-2 font-medium">进度</th>
                    <th className="text-right py-2 px-2 font-medium">已采集</th>
                  </tr>
                </thead>
                <tbody>
                  {syncProgress.sub_tasks.map((st, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
                      <td className="py-1.5 px-2 font-medium">{st.symbol}</td>
                      <td className="py-1.5 px-2">{PERIOD_LABELS[st.period] || st.period}</td>
                      <td className="py-1.5 px-2">
                        {st.status === 'running' && <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500 inline mr-1" />}
                        {st.status === 'completed' && <CheckCircle2 className="h-3.5 w-3.5 text-green-500 inline mr-1" />}
                        {st.status === 'failed' && <XCircle className="h-3.5 w-3.5 text-red-500 inline mr-1" />}
                        {st.status === 'skipped' && <span className="text-muted-foreground">跳过(已有)</span>}
                        {st.status === 'pending' && <span className="text-muted-foreground">等待中</span>}
                        {st.error && (
                          <span className="text-red-500 text-xs ml-1" title={st.error}>
                            <AlertTriangle className="h-3 w-3 inline" />
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 px-2 text-right">
                        {st.status === 'running' && (
                          <div className="w-16 bg-muted rounded-full h-1.5 inline-block ml-auto">
                            <div
                              className="h-full bg-blue-500 rounded-full"
                              style={{ width: `${st.progress}%` }}
                            />
                          </div>
                        )}
                        {st.status !== 'running' && `${st.progress.toFixed(0)}%`}
                      </td>
                      <td className="py-1.5 px-2 text-right text-muted-foreground">
                        {st.collected.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 数据库概览 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5" />
              数据库概览
              {dataSummary && (
                <Badge variant="outline" className="ml-2">
                  {dataSummary.exchange} | {dataSummary.total_records.toLocaleString()} 条
                </Badge>
              )}
            </CardTitle>
            <Button onClick={loadSummary} variant="ghost" size="sm" disabled={loadingSummary}>
              {loadingSummary ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {!dataSummary || dataSummary.data.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Database className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p>暂无K线数据</p>
              <p className="text-sm">点击「开始同步」拉取历史数据</p>
            </div>
          ) : (
            <div className="space-y-4">
              {Object.entries(groupedSummary).map(([symbol, items]) => (
                <div key={symbol} className="border rounded-lg p-3">
                  <h3 className="font-semibold text-sm mb-2 flex items-center gap-2">
                    {symbol}
                    <Badge variant="outline" className="text-xs">
                      {items.reduce((s, i) => s + i.record_count, 0).toLocaleString()} 条
                    </Badge>
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
                    {items.map(item => (
                      <div
                        key={`${item.symbol}-${item.period}`}
                        className="bg-muted/50 rounded-md px-3 py-2 text-xs"
                      >
                        <div className="flex justify-between items-center mb-1">
                          <span className="font-medium">{PERIOD_LABELS[item.period] || item.period}</span>
                          <span className="text-muted-foreground">{item.record_count.toLocaleString()}</span>
                        </div>
                        <div className="text-muted-foreground">
                          {item.days_covered > 0 ? (
                            <>
                              <span>{item.days_covered}天</span>
                              <span className="mx-1">|</span>
                              <span>{(() => {
                                try {
                                  return new Date(item.earliest + 'Z').toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
                                } catch { return item.earliest?.split(' ')[0]; }
                              })()}</span>
                              <span>~</span>
                              <span>{(() => {
                                try {
                                  return new Date(item.latest + 'Z').toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
                                } catch { return item.latest?.split(' ')[0]; }
                              })()}</span>
                            </>
                          ) : (
                            <span>无数据</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
