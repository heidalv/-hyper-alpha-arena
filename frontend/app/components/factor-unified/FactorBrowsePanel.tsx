/**
 * FactorBrowsePanel — 因子浏览器
 *
 * 浏览本地已注册因子和云端因子的分类列表，查看详情和计算代码。
 */
import { useState, useEffect, useCallback }from 'react'
import { apiRequest } from '@/lib/api'

interface LocalFactor {
  name: string
  value: number
  normalized: number
  category: string
}

interface CloudFactor {
  id: number
  factor_id: string
  name: string
  display_name: string
  category: string
  subcategory: string
  status: string
  localized: boolean
  source_repo: string
  downloaded_at: string
  localized_at: string
  error_message: string
}

interface Props {
  symbol: string
}

const STATUS_COLORS: Record<string, string> = {
  downloaded: '#60a5fa',
  validated: '#34d399',
  localized: '#a78bfa',
  active: '#22c55e',
  error: '#ef4444',
}

const STATUS_LABELS: Record<string, string> = {
  downloaded: '已下载',
  validated: '已验证',
  localized: '已本地化',
  active: '已激活',
  error: '错误',
}

export default function FactorBrowsePanel({ symbol }: Props) {
  const [tab, setTab] = useState<'local' | 'cloud'>('local')
  const [localFactors, setLocalFactors] = useState<LocalFactor[]>([])
  const [cloudFactors, setCloudFactors] = useState<CloudFactor[]>([])
  const [selectedFactor, setSelectedFactor] = useState<string | null>(null)
  const [factorDetail, setFactorDetail] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const fetchLocalFactors = useCallback(async () => {
    try {
      const resp = await apiRequest(`/factors/values/${symbol}?include_new=true`)
      const data = await resp.json()
      setLocalFactors(data.factors || [])
    } catch {
      setLocalFactors([])
    }
  }, [symbol])

  const fetchCloudFactors = useCallback(async () => {
    try {
      const resp = await apiRequest('/factors/cloud')
      const data = await resp.json()
      setCloudFactors(data.factors || [])
    } catch {
      setCloudFactors([])
    }
  }, [])

  const fetchFactorDetail = useCallback(async (factorId: string) => {
    setLoading(true)
    try {
      const resp = await apiRequest(`/factors/cloud/${factorId}`)
      const data = await resp.json()
      setFactorDetail(data)
      setSelectedFactor(factorId)
    } catch {
      setFactorDetail(null)
    } finally {
      setLoading(false)
    }
  }, [])

  const localizeFactor = useCallback(async (factorId: string) => {
    try {
      const resp = await apiRequest(`/factors/cloud/${factorId}/localize`, {
        method: 'POST',
      })
      const data = await resp.json()
      if (data.status === 'success') {
        fetchCloudFactors()
        if (selectedFactor === factorId) {
          fetchFactorDetail(factorId)
        }
      }
    } catch (e: any) {
      alert('本地化失败: ' + (e.message || '未知错误'))
    }
  }, [fetchCloudFactors, fetchFactorDetail, selectedFactor])

  useEffect(() => {
    fetchLocalFactors()
  }, [fetchLocalFactors])

  useEffect(() => {
    if (tab === 'cloud') fetchCloudFactors()
  }, [tab, fetchCloudFactors])

  // 本地因子按类别分组
  const grouped = localFactors.reduce<Record<string, LocalFactor[]>>((acc, f) => {
    const cat = f.category || 'other'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(f)
    return acc
  }, {})

  return (
    <div className="space-y-4">
      {/* 本地/云端切换 */}
      <div className="flex gap-2">
        <button
          onClick={() => setTab('local')}
          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            tab === 'local'
              ? 'bg-blue-600 text-white'
              : 'bg-muted text-muted-foreground hover:bg-muted/80'
          }`}
        >
          本地因子 ({localFactors.length})
        </button>
        <button
          onClick={() => setTab('cloud')}
          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            tab === 'cloud'
              ? 'bg-blue-600 text-white'
              : 'bg-muted text-muted-foreground hover:bg-muted/80'
          }`}
        >
          云端因子 ({cloudFactors.length})
        </button>
      </div>

      {tab === 'local' && (
        <div className="grid grid-cols-2 gap-3">
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat} className="rounded-lg border bg-card">
              <div className="px-3 py-2 border-b bg-muted/30 text-xs font-medium">
                {cat} ({items.length})
              </div>
              <div className="p-2 space-y-1">
                {items.map(f => (
                  <div key={f.name} className="flex items-center justify-between py-1 px-2 rounded hover:bg-muted/50 text-xs">
                    <span className="font-mono truncate">{f.name}</span>
                    <span className="tabular-nums text-muted-foreground">{f.value.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'cloud' && (
        <div className="grid grid-cols-3 gap-3">
          {/* 因子列表 */}
          <div className="col-span-1 rounded-lg border bg-card max-h-[600px] overflow-auto">
            <div className="px-3 py-2 border-b bg-muted/30 text-xs font-medium sticky top-0 bg-background">
              云端因子
            </div>
            <div className="divide-y">
              {cloudFactors.length === 0 && (
                <div className="text-center py-8 text-xs text-muted-foreground">
                  暂无云端因子，请先配置同步源
                </div>
              )}
              {cloudFactors.map(f => (
                <button
                  key={f.factor_id}
                  onClick={() => fetchFactorDetail(f.factor_id)}
                  className={`w-full text-left px-3 py-2 hover:bg-muted/50 transition-colors ${
                    selectedFactor === f.factor_id ? 'bg-blue-500/10' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono truncate">{f.factor_id}</span>
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded"
                      style={{
                        color: STATUS_COLORS[f.status] || '#94a3b8',
                        background: `${STATUS_COLORS[f.status] || '#94a3b8'}15`,
                      }}
                    >
                      {STATUS_LABELS[f.status] || f.status}
                    </span>
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">{f.name}</div>
                </button>
              ))}
            </div>
          </div>

          {/* 因子详情 */}
          <div className="col-span-2 rounded-lg border bg-card p-4">
            {loading && <div className="text-center py-8 text-xs text-muted-foreground">加载中...</div>}
            {!loading && !factorDetail && (
              <div className="text-center py-8 text-xs text-muted-foreground">
                选择一个云端因子查看详情
              </div>
            )}
            {!loading && factorDetail && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">{factorDetail.display_name || factorDetail.name}</h3>
                  {factorDetail.status !== 'active' && (
                    <button
                      onClick={() => localizeFactor(factorDetail.factor_id)}
                      className="px-3 py-1 rounded bg-green-600 text-white text-xs hover:bg-green-700"
                    >
                      本地化
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <span className="text-muted-foreground">分类:</span>{' '}
                    {factorDetail.category}/{factorDetail.subcategory}
                  </div>
                  <div>
                    <span className="text-muted-foreground">版本:</span> {factorDetail.version || '--'}
                  </div>
                  <div>
                    <span className="text-muted-foreground">来源:</span>{' '}
                    <span className="truncate">{factorDetail.source_repo}</span>
                  </div>
                </div>
                {factorDetail.description && (
                  <div className="text-xs text-muted-foreground bg-muted/30 rounded p-2">
                    {factorDetail.description}
                  </div>
                )}
                {factorDetail.error_message && (
                  <div className="text-xs text-red-500 bg-red-500/10 rounded p-2">
                    {factorDetail.error_message}
                  </div>
                )}
                <div>
                  <div className="text-xs font-medium mb-1">计算代码</div>
                  <pre className="text-[10px] font-mono bg-zinc-900 text-zinc-300 rounded p-3 overflow-auto max-h-[300px]">
                    {factorDetail.calculation_code || '// 无代码'}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
