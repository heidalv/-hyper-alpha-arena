/**
 * FactorEvalPanel — 因子 IC 质量评估面板
 *
 * 展示因子 IC 均值、ICIR、衰减半衰期、评级等
 */
import { useState, useCallback }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  BarChart3, RefreshCw, TrendingUp, Award, Search,
  ArrowUpDown, ChevronDown,
} from 'lucide-react'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

interface FactorReport {
  factor_id: string
  ic_mean: number
  ic_std: number
  icir: number
  ic_positive_pct: number
  ic_decay_halflife: number
  turnover: number
  monotonicity: number
  tail_risk: number
  grade: string
  data_points: number
}

interface EvalData {
  symbol: string
  reports: FactorReport[]
  message?: string
  error?: string
}

const GRADE_COLORS: Record<string, string> = {
  A: 'bg-green-500/20 text-green-400 border-green-500/30',
  B: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  C: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  D: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  F: 'bg-red-500/20 text-red-400 border-red-500/30',
}

export default function FactorEvalPanel({ symbols: propSymbols }: { symbols?: string[] }) {
  const { symbols: configuredPairs } = useTradingPairs()
  const symbols = propSymbols && propSymbols.length > 0 ? propSymbols : (configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS)

  const [symbol, setSymbol] = useState('BTC')
  const [data, setData] = useState<EvalData | null>(null)
  const [loading, setLoading] = useState(false)
  const [sortField, setSortField] = useState<keyof FactorReport>('ic_mean')
  const [sortAsc, setSortAsc] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

  const fetchEval = useCallback(async (sym: string) => {
    setLoading(true)
    try {
      const res = await fetch(`/api/monitor/factor-eval/${sym}?top_n=30`)
      if (res.ok) setData(await res.json())
    } catch (e) {
      console.error('[FactorEval] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleSymbolChange = (sym: string) => {
    setSymbol(sym)
    fetchEval(sym)
  }

  const handleSort = (field: keyof FactorReport) => {
    if (sortField === field) {
      setSortAsc(!sortAsc)
    } else {
      setSortField(field)
      setSortAsc(false)
    }
  }

  const reports = data?.reports ?? []
  const sorted = [...reports].sort((a, b) => {
    const va = a[sortField] as number
    const vb = b[sortField] as number
    return sortAsc ? va - vb : vb - va
  })

  const gradeDistrib = reports.reduce<Record<string, number>>((acc, r) => {
    acc[r.grade] = (acc[r.grade] || 0) + 1
    return acc
  }, {})

  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-purple-400" />
          <h2 className="text-lg font-bold text-white">因子 IC 质量评估</h2>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1 flex-wrap">
            {symbols.map(s => (
              <button
                key={s}
                onClick={() => handleSymbolChange(s)}
                className={`px-2.5 py-1 text-xs rounded border transition ${
                  symbol === s
                    ? 'bg-purple-500/20 border-purple-500/50 text-purple-300'
                    : 'bg-slate-800/50 border-slate-700/30 text-slate-400 hover:border-slate-600'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchEval(symbol)}
            disabled={loading}
            className="text-xs"
          >
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
            分析
          </Button>
        </div>
      </div>

      {/* Summary */}
      {reports.length > 0 && (
        <div className="grid grid-cols-5 gap-3">
          {['A', 'B', 'C', 'D', 'F'].map(g => (
            <Card key={g} className="bg-[#1a1a2e] border-slate-700/50">
              <CardContent className="p-3 flex items-center justify-between">
                <span className={`px-2 py-0.5 rounded border text-xs font-bold ${GRADE_COLORS[g]}`}>
                  {g}
                </span>
                <span className="text-lg font-bold text-white">{gradeDistrib[g] || 0}</span>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Factor Table */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <Award className="w-4 h-4" />
            因子排名 — {symbol}
            {data?.message && <span className="text-xs text-slate-500 ml-2">({data.message})</span>}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4">
          {!data ? (
            <p className="text-xs text-slate-500 text-center py-8">选择交易对后点击「分析」开始评估</p>
          ) : reports.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-8">
              {data.error || data.message || '无因子数据'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-700/50">
                    <th className="text-left py-2 px-2">#</th>
                    <th className="text-left py-2 px-2">因子</th>
                    <th className="text-center py-2 px-2">评级</th>
                    <SortHeader field="ic_mean" label="IC均值" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <SortHeader field="icir" label="ICIR" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <SortHeader field="ic_positive_pct" label="IC>0%" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <SortHeader field="ic_decay_halflife" label="衰减(bars)" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <SortHeader field="turnover" label="换手率" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <SortHeader field="monotonicity" label="单调性" current={sortField} asc={sortAsc} onSort={handleSort} />
                    <th className="text-right py-2 px-2">数据点</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r, i) => (
                    <React.Fragment key={r.factor_id}>
                      <tr
                        className="border-b border-slate-800/50 hover:bg-slate-800/30 cursor-pointer"
                        onClick={() => setExpanded(expanded === r.factor_id ? null : r.factor_id)}
                      >
                        <td className="py-2 px-2 text-slate-500">{i + 1}</td>
                        <td className="py-2 px-2 font-medium text-slate-200 flex items-center gap-1">
                          {r.factor_id}
                          <ChevronDown className={`w-3 h-3 text-slate-600 transition ${expanded === r.factor_id ? 'rotate-180' : ''}`} />
                        </td>
                        <td className="py-2 px-2 text-center">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold ${GRADE_COLORS[r.grade]}`}>
                            {r.grade}
                          </span>
                        </td>
                        <td className="py-2 px-2 text-right font-mono">
                          <span className={r.ic_mean > 0 ? 'text-green-400' : 'text-red-400'}>
                            {(r.ic_mean * 100).toFixed(2)}%
                          </span>
                        </td>
                        <td className="py-2 px-2 text-right font-mono text-slate-300">{r.icir.toFixed(2)}</td>
                        <td className="py-2 px-2 text-right font-mono text-slate-300">
                          {(r.ic_positive_pct * 100).toFixed(0)}%
                        </td>
                        <td className="py-2 px-2 text-right font-mono text-slate-300">{r.ic_decay_halflife}</td>
                        <td className="py-2 px-2 text-right font-mono text-slate-300">{(r.turnover * 100).toFixed(1)}%</td>
                        <td className="py-2 px-2 text-right font-mono text-slate-300">{(r.monotonicity * 100).toFixed(0)}%</td>
                        <td className="py-2 px-2 text-right text-slate-400">{r.data_points}</td>
                      </tr>
                      {expanded === r.factor_id && (
                        <tr>
                          <td colSpan={10} className="px-4 py-3 bg-slate-800/20">
                            <div className="grid grid-cols-4 gap-4 text-xs">
                              <div>
                                <span className="text-slate-500">IC 标准差</span>
                                <p className="text-slate-200 font-mono">{(r.ic_std * 100).toFixed(3)}%</p>
                              </div>
                              <div>
                                <span className="text-slate-500">尾部风险</span>
                                <p className="text-slate-200 font-mono">{(r.tail_risk * 100).toFixed(3)}%</p>
                              </div>
                              <div>
                                <span className="text-slate-500">IC 半衰期</span>
                                <p className="text-slate-200 font-mono">{r.ic_decay_halflife} bars</p>
                              </div>
                              <div>
                                <span className="text-slate-500">单调性得分</span>
                                <p className="text-slate-200 font-mono">{(r.monotonicity * 100).toFixed(1)}%</p>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SortHeader({ field, label, current, asc, onSort }: {
  field: keyof FactorReport; label: string;
  current: keyof FactorReport; asc: boolean;
  onSort: (f: keyof FactorReport) => void
}) {
  return (
    <th
      className="text-right py-2 px-2 cursor-pointer hover:text-white transition select-none"
      onClick={() => onSort(field)}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {current === field && (
          <ArrowUpDown className={`w-3 h-3 ${asc ? 'rotate-180' : ''}`} />
        )}
      </span>
    </th>
  )
}
