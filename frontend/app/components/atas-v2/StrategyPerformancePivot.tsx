/**
 * StrategyPerformancePivot — 策略性能四维切面 (F2-5)
 * symbol x tier x trade_nature x market_regime 交叉分析
 * API: GET /api/analytics/strategy-performance-pivot
 */
import { useState, useEffect, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { BarChart3, Filter, RefreshCw, Search, TrendingDown, TrendingUp } from 'lucide-react';

interface PivotRow {
  symbol: string;
  tier: string;
  trade_nature: string;
  market_regime: string;
  trades: number;
  win_rate: number;
  avg_pnl: number;
  sharpe_est: number;
  total_pnl: number;
  best_trade: number;
  worst_trade: number;
}

interface PivotResponse {
  status: string;
  period_days: number;
  account_id: number | null;
  pivot: PivotRow[];
  count: number;
}

const TIER_COLORS: Record<string, string> = {
  short: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  mid: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
  long: 'bg-green-500/10 text-green-400 border-green-500/30',
};

const REGIME_COLORS: Record<string, string> = {
  trending_up: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  trending_down: 'bg-red-500/10 text-red-400 border-red-500/30',
  ranging: 'bg-gray-500/10 text-gray-400 border-gray-500/30',
  crash: 'bg-red-700/10 text-red-600 border-red-700/30',
  high_volatility: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  low_volatility: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  unknown: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
};

export default function StrategyPerformancePivot() {
  const [data, setData] = useState<PivotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);
  const [accountId, setAccountId] = useState<number | undefined>();
  const [filterSymbol, setFilterSymbol] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [filterNature, setFilterNature] = useState('all');
  const [sortKey, setSortKey] = useState<keyof PivotRow>('total_pnl');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ days: String(days) });
      if (accountId) params.set('account_id', String(accountId));
      const res = await fetch(`/api/analytics/strategy-performance-pivot?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (e: any) {
      setError(e.message || 'Failed to load pivot data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, [days, accountId]);

  const filtered = useMemo(() => {
    if (!data?.pivot) return [];
    return data.pivot
      .filter(row => !filterSymbol || row.symbol.toLowerCase().includes(filterSymbol.toLowerCase()))
      .filter(row => filterTier === 'all' || row.tier === filterTier)
      .filter(row => filterNature === 'all' || row.trade_nature === filterNature)
      .sort((a, b) => {
        const va = a[sortKey] ?? 0;
        const vb = b[sortKey] ?? 0;
        return sortDir === 'desc' ? (vb as number) - (va as number) : (va as number) - (vb as number);
      });
  }, [data, filterSymbol, filterTier, filterNature, sortKey, sortDir]);

  const uniqueTiers = useMemo(() => [...new Set((data?.pivot || []).map(r => r.tier))], [data]);
  const uniqueNatures = useMemo(() => [...new Set((data?.pivot || []).map(r => r.trade_nature))], [data]);

  const handleSort = (key: keyof PivotRow) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const SortIcon = ({ col }: { col: keyof PivotRow }) => {
    if (sortKey !== col) return <span className="text-gray-600 ml-1">⇅</span>;
    return sortDir === 'desc' ? <span className="text-blue-400 ml-1">↓</span> : <span className="text-blue-400 ml-1">↑</span>;
  };

  return (
    <Card className="border-gray-800 bg-gray-900/50">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-blue-400" />
            策略性能四维切面 (F2-5)
            {data && <Badge variant="outline" className="text-xs">{data.count} 条记录</Badge>}
          </CardTitle>
          <div className="flex items-center gap-2">
            <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
              <SelectTrigger className="w-24 h-7 text-xs bg-gray-800 border-gray-700">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">7 天</SelectItem>
                <SelectItem value="30">30 天</SelectItem>
                <SelectItem value="90">90 天</SelectItem>
                <SelectItem value="365">1 年</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="ghost" size="sm" onClick={fetchData} disabled={loading} className="h-7 w-7 p-0">
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-2 mt-2 flex-wrap">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-gray-500" />
            <Input
              placeholder="Symbol..."
              value={filterSymbol}
              onChange={(e) => setFilterSymbol(e.target.value)}
              className="w-28 h-7 pl-7 text-xs bg-gray-800 border-gray-700"
            />
          </div>
          <Select value={filterTier} onValueChange={setFilterTier}>
            <SelectTrigger className="w-24 h-7 text-xs bg-gray-800 border-gray-700">
              <Filter className="h-3 w-3 mr-1" /> Tier
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部 Tier</SelectItem>
              {uniqueTiers.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={filterNature} onValueChange={setFilterNature}>
            <SelectTrigger className="w-28 h-7 text-xs bg-gray-800 border-gray-700">
              <Filter className="h-3 w-3 mr-1" /> Nature
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部 Nature</SelectItem>
              {uniqueNatures.map(n => <SelectItem key={n} value={n}>{n}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {loading && !data && (
          <div className="flex items-center justify-center py-12 text-gray-500 text-sm">
            <RefreshCw className="h-4 w-4 animate-spin mr-2" /> 加载中...
          </div>
        )}
        {error && (
          <div className="flex items-center justify-center py-8 text-red-400 text-sm">
            {error}
            <Button variant="ghost" size="sm" onClick={fetchData} className="ml-2 h-6 text-xs">重试</Button>
          </div>
        )}
        {data && filtered.length === 0 && (
          <div className="flex items-center justify-center py-8 text-gray-500 text-sm">
            无匹配数据
          </div>
        )}
        {data && filtered.length > 0 && (
          <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-gray-900 z-10">
                <tr className="border-b border-gray-800">
                  <th className="text-left p-2 text-gray-400 font-medium">Symbol</th>
                  <th className="text-left p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('tier')}>Tier <SortIcon col="tier" /></th>
                  <th className="text-left p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('trade_nature')}>Nature <SortIcon col="trade_nature" /></th>
                  <th className="text-left p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('market_regime')}>Regime <SortIcon col="market_regime" /></th>
                  <th className="text-right p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('trades')}>Trades <SortIcon col="trades" /></th>
                  <th className="text-right p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('win_rate')}>WR <SortIcon col="win_rate" /></th>
                  <th className="text-right p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('avg_pnl')}>Avg PnL <SortIcon col="avg_pnl" /></th>
                  <th className="text-right p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('sharpe_est')}>Sharpe <SortIcon col="sharpe_est" /></th>
                  <th className="text-right p-2 text-gray-400 font-medium cursor-pointer hover:text-white" onClick={() => handleSort('total_pnl')}>Total PnL <SortIcon col="total_pnl" /></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                    <td className="p-2 font-medium text-white">{row.symbol}</td>
                    <td className="p-2">
                      <Badge variant="outline" className={`text-xs ${TIER_COLORS[row.tier] || 'bg-gray-500/10 text-gray-400'}`}>
                        {row.tier}
                      </Badge>
                    </td>
                    <td className="p-2 text-gray-300">{row.trade_nature}</td>
                    <td className="p-2">
                      <Badge variant="outline" className={`text-xs ${REGIME_COLORS[row.market_regime] || ''}`}>
                        {row.market_regime}
                      </Badge>
                    </td>
                    <td className="p-2 text-right text-gray-300">{row.trades}</td>
                    <td className="p-2 text-right">
                      <span className={row.win_rate >= 0.45 ? 'text-green-400' : row.win_rate >= 0.30 ? 'text-yellow-400' : 'text-red-400'}>
                        {(row.win_rate * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="p-2 text-right">
                      <span className={row.avg_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                        ${row.avg_pnl.toFixed(2)}
                      </span>
                    </td>
                    <td className="p-2 text-right">
                      <span className={row.sharpe_est >= 0.5 ? 'text-green-400' : row.sharpe_est >= 0 ? 'text-yellow-400' : 'text-red-400'}>
                        {row.sharpe_est.toFixed(2)}
                      </span>
                    </td>
                    <td className="p-2 text-right font-medium">
                      <span className={row.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                        {row.total_pnl >= 0 ? <TrendingUp className="inline h-3 w-3 mr-1" /> : <TrendingDown className="inline h-3 w-3 mr-1" />}
                        ${row.total_pnl.toFixed(2)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
