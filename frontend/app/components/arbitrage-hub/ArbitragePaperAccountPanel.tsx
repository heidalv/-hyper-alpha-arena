import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown, ChevronUp, Plus, RefreshCw, Settings2, Sparkles, Wallet, Save, X,
  Pencil, RotateCcw, Trash2,
} from 'lucide-react'
import { toast } from 'react-hot-toast'
import { cn } from '@/lib/utils'
import {
  type ArbitragePaperAccount,
  type ArbitragePaperDashboard,
  type ArbitragePaperPreset,
  applyArbitragePaperPreset,
  createArbitragePaperAccount,
  deleteArbitragePaperAccount,
  getArbitragePaperAccounts,
  getArbitragePaperDashboard,
  getArbitragePaperPresets,
  resetArbitragePaperAccount,
  updateArbitragePaperAccount,
  updateArbitragePaperBalances,
} from '@/lib/arbitrageApi'
import ArbitragePaperDashboard from './ArbitragePaperDashboard'
import ExchangeAllocationGrid, { EXCHANGE_LABELS, EXCHANGE_ORDER } from './ExchangeAllocationGrid'
import ArbitrageTraderBinding from './ArbitrageTraderBinding'
import ArbitrageSetupGuide from './ArbitrageSetupGuide'

function balancesToAmounts(account: ArbitragePaperAccount | null): Record<string, number> {
  const result: Record<string, number> = {}
  for (const exchange of EXCHANGE_ORDER) {
    result[exchange] = Number(account?.exchange_balances?.[exchange]?.allocated_usd || 0)
  }
  return result
}

function emptyDraft(): Record<string, number> {
  return EXCHANGE_ORDER.reduce<Record<string, number>>((acc, exchange) => {
    acc[exchange] = 0
    return acc
  }, {})
}

export default function ArbitragePaperAccountPanel() {
  const [accounts, setAccounts] = useState<ArbitragePaperAccount[]>([])
  const [presets, setPresets] = useState<ArbitragePaperPreset[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [dashboard, setDashboard] = useState<ArbitragePaperDashboard | null>(null)
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)

  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const [showAdjustPanel, setShowAdjustPanel] = useState(false)
  const [showManageDialog, setShowManageDialog] = useState(false)
  const [manageMode, setManageMode] = useState<'edit' | 'reset' | 'delete'>('edit')
  const [newEquity, setNewEquity] = useState(300)
  const [presetId, setPresetId] = useState('small_300u_standard')
  const [manageName, setManageName] = useState('')
  const [manageRisk, setManageRisk] = useState('balanced')
  const [resetEquity, setResetEquity] = useState(300)
  const [resetPresetId, setResetPresetId] = useState('small_300u_standard')
  const [draft, setDraft] = useState<Record<string, number>>(() => emptyDraft())
  const draftDirtyRef = useRef(false)

  const syncDraftFromAccount = useCallback((account: ArbitragePaperAccount | null) => {
    setDraft(balancesToAmounts(account))
    draftDirtyRef.current = false
  }, [])

  const handleDraftChange = useCallback((next: Record<string, number>) => {
    draftDirtyRef.current = true
    setDraft(next)
  }, [])

  const selected = useMemo(
    () => accounts.find(a => a.id === selectedId) || accounts[0] || null,
    [accounts, selectedId],
  )

  const concentrateDraftToExchange = useCallback((exchange: string, reservePct = 0) => {
    const total = Object.values(draft).reduce((sum, v) => sum + Number(v || 0), 0)
      || Number(selected?.total_equity || 0)
    if (total <= 0) {
      toast.error('请先设置有效的总资金')
      return
    }
    const reserveAmount = reservePct > 0 ? Math.round(total * reservePct * 100) / 100 : 0
    const mainAmount = Math.max(total - reserveAmount, 0)
    const next = emptyDraft()
    for (const ex of EXCHANGE_ORDER) {
      if (ex === exchange) next[ex] = mainAmount
      else if (ex === 'reserve') next[ex] = reserveAmount
      else next[ex] = 0
    }
    handleDraftChange(next)
  }, [draft, handleDraftChange, selected?.total_equity])

  const loadAccounts = useCallback(async () => {
    const [accs, prs] = await Promise.all([getArbitragePaperAccounts(), getArbitragePaperPresets()])
    setAccounts(accs)
    setPresets(prs)
    setSelectedId(prev => {
      if (prev && accs.some(a => a.id === prev)) return prev
      return accs[0]?.id ?? null
    })
    return accs
  }, [])

  const loadDashboard = useCallback(async (accountId: number, opts?: { syncDraft?: boolean }) => {
    const data = await getArbitragePaperDashboard(accountId)
    setDashboard(data)
    const shouldSyncDraft = opts?.syncDraft ?? !draftDirtyRef.current
    if (data?.account && shouldSyncDraft) {
      syncDraftFromAccount(data.account)
    }
    return data
  }, [syncDraftFromAccount])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const accs = await loadAccounts()
      const id = selectedId && accs.some(a => a.id === selectedId) ? selectedId : accs[0]?.id
      if (id) await loadDashboard(id)
      else setDashboard(null)
    } finally {
      setLoading(false)
    }
  }, [loadAccounts, loadDashboard, selectedId])

  useEffect(() => {
    refresh()
  }, [])

  useEffect(() => {
    if (!selectedId) return
    loadDashboard(selectedId, { syncDraft: !showAdjustPanel && !draftDirtyRef.current })
  }, [selectedId, loadDashboard, showAdjustPanel])

  useEffect(() => {
    if (!selectedId || showAdjustPanel) return
    const timer = setInterval(() => {
      loadDashboard(selectedId, { syncDraft: !draftDirtyRef.current }).catch(() => {})
    }, 15_000)
    return () => clearInterval(timer)
  }, [selectedId, loadDashboard, showAdjustPanel])

  useEffect(() => {
    if (!showAdjustPanel || !selected) return
    syncDraftFromAccount(selected)
  // 仅在打开调整面板时加载配额，避免轮询/刷新覆盖正在编辑的内容
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAdjustPanel])

  const handleCreate = async () => {
    setCreating(true)
    try {
      const res = await createArbitragePaperAccount({
        name: `套利 Paper ${newEquity}U`,
        total_equity: newEquity,
        preset_id: presetId,
      })
      if (!res.success || !res.account) {
        toast.error(res.error || '创建失败')
        return
      }
      toast.success('套利 Paper 账户已创建')
      setShowCreateDialog(false)
      setShowAdjustPanel(false)
      await loadAccounts()
      setSelectedId(res.account.id)
      await loadDashboard(res.account.id)
    } finally {
      setCreating(false)
    }
  }

  const handleSave = async () => {
    if (!selected) return
    setSaving(true)
    try {
      const payload = EXCHANGE_ORDER.reduce<Record<string, number>>((acc, ex) => {
        acc[ex] = Number(draft[ex] || 0)
        return acc
      }, {})
      const res = await updateArbitragePaperBalances(selected.id, payload)
      if (!res.success) {
        toast.error(res.error || '保存失败')
        return
      }
      toast.success('分账户配额已保存')
      draftDirtyRef.current = false
      setShowAdjustPanel(false)
      await refresh()
    } finally {
      setSaving(false)
    }
  }

  const handleApplyPreset = async () => {
    if (!selected) return
    setSaving(true)
    try {
      const res = await applyArbitragePaperPreset(selected.id, presetId, selected.total_equity)
      if (!res.success || !res.account) {
        toast.error(res.error || '套用失败')
        return
      }
      toast.success('已套用科学配额')
      if (res.account) syncDraftFromAccount(res.account)
      draftDirtyRef.current = false
      await refresh()
    } finally {
      setSaving(false)
    }
  }

  const openManageDialog = (mode: 'edit' | 'reset' | 'delete') => {
    if (!selected) return
    setManageMode(mode)
    setManageName(selected.name)
    setManageRisk(selected.risk_profile || 'balanced')
    setResetEquity(Number(selected.total_equity || newEquity || 300))
    setResetPresetId(selected.allocation_preset || presetId || 'small_300u_standard')
    setShowManageDialog(true)
  }

  const handleManageConfirm = async () => {
    if (!selected) return
    setSaving(true)
    try {
      if (manageMode === 'edit') {
        const res = await updateArbitragePaperAccount(selected.id, {
          name: manageName,
          risk_profile: manageRisk,
        })
        if (!res.success || !res.account) {
          toast.error(res.error || '修改失败')
          return
        }
        toast.success('账户信息已修改')
        setShowManageDialog(false)
        await refresh()
        return
      }
      if (manageMode === 'reset') {
        const res = await resetArbitragePaperAccount(selected.id, {
          total_equity: resetEquity,
          preset_id: resetPresetId,
          clear_ledger: true,
        })
        if (!res.success || !res.account) {
          toast.error(res.error || '重置失败')
          return
        }
        toast.success('账户已重置')
        setShowManageDialog(false)
        await refresh()
        return
      }
      const res = await deleteArbitragePaperAccount(selected.id)
      if (!res.success) {
        toast.error(res.error || '删除失败')
        return
      }
      toast.success('账户已删除')
      setShowManageDialog(false)
      const accs = await loadAccounts()
      const nextId = accs[0]?.id ?? null
      setSelectedId(nextId)
      if (nextId) await loadDashboard(nextId)
      else setDashboard(null)
    } finally {
      setSaving(false)
    }
  }

  if (!loading && accounts.length === 0) {
    return (
      <div className="max-w-lg mx-auto">
        <div className="rounded-xl border-2 border-dashed border-blue-300 dark:border-blue-700 bg-card p-6">
          <div className="text-center mb-5">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center mb-3">
              <Wallet className="w-7 h-7 text-white" />
            </div>
            <h2 className="text-xl font-bold">创建套利 Paper 账户</h2>
            <p className="text-sm text-muted-foreground mt-1">
              独立于 AI 策略模拟盘，专用于 S1–S8 积分套利验证
            </p>
          </div>
          <div className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">初始总资金 (USDT)</label>
              <div className="flex gap-2 mt-1.5">
                {[300, 500, 1000].map(amt => (
                  <button
                    key={amt}
                    type="button"
                    onClick={() => setNewEquity(amt)}
                    className={cn(
                      'flex-1 py-1.5 rounded text-xs font-medium border',
                      newEquity === amt
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'border-border hover:bg-muted',
                    )}
                  >
                    {amt}U
                  </button>
                ))}
              </div>
              <input
                type="number"
                min={1}
                value={newEquity}
                onChange={e => setNewEquity(Number(e.target.value || 0))}
                className="w-full mt-2 rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">配额模板</label>
              <select
                value={presetId}
                onChange={e => setPresetId(e.target.value)}
                className="w-full mt-1.5 rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {presets.map(p => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
              </select>
            </div>
            <button
              type="button"
              onClick={handleCreate}
              disabled={creating}
              className="w-full rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white py-2.5 text-sm font-medium"
            >
              {creating ? '创建中...' : '创建并开始使用'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <ArbitrageSetupGuide variant="paper" />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Wallet className="w-5 h-5 text-blue-500" /> 套利模拟账户
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            各交易所资金 · 积分 · 仓位 · 流水（独立体系，不混用 AI 策略 Paper）
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setShowAdjustPanel(v => !v)}
            className="px-3 py-2 rounded-lg border border-border bg-card hover:bg-muted text-sm flex items-center gap-1.5"
          >
            <Settings2 className="w-4 h-4" />
            调整配额
            {showAdjustPanel ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
          <button
            type="button"
            onClick={() => openManageDialog('edit')}
            disabled={!selected}
            className="px-3 py-2 rounded-lg border border-border bg-card hover:bg-muted disabled:opacity-50 text-sm flex items-center gap-1.5"
          >
            <Pencil className="w-4 h-4" /> 修改
          </button>
          <button
            type="button"
            onClick={() => openManageDialog('reset')}
            disabled={!selected}
            className="px-3 py-2 rounded-lg border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/15 text-amber-700 dark:text-amber-300 disabled:opacity-50 text-sm flex items-center gap-1.5"
          >
            <RotateCcw className="w-4 h-4" /> 重置
          </button>
          <button
            type="button"
            onClick={() => openManageDialog('delete')}
            disabled={!selected}
            className="px-3 py-2 rounded-lg border border-red-500/40 bg-red-500/10 hover:bg-red-500/15 text-red-700 dark:text-red-300 disabled:opacity-50 text-sm flex items-center gap-1.5"
          >
            <Trash2 className="w-4 h-4" /> 删除
          </button>
          <button
            type="button"
            onClick={() => setShowCreateDialog(true)}
            className="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm flex items-center gap-1.5"
          >
            <Plus className="w-4 h-4" /> 添加账户
          </button>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="px-3 py-2 rounded-lg bg-secondary hover:bg-secondary/80 text-sm flex items-center gap-1.5"
          >
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} /> 刷新
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        <span className="text-xs font-semibold text-muted-foreground shrink-0">账户:</span>
        {accounts.map(a => (
          <button
            key={a.id}
            type="button"
            onClick={() => setSelectedId(a.id)}
            className={cn(
              'shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors',
              a.id === selectedId
                ? 'bg-blue-500/15 border-blue-500/40 text-blue-700 dark:text-blue-300'
                : 'bg-card border-border hover:bg-muted',
            )}
          >
            {a.name}
            {a.status === 'running' && (
              <span className="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-green-500 align-middle" />
            )}
          </button>
        ))}
      </div>

      {selected && (
        <ArbitrageTraderBinding account={selected} onUpdated={refresh} />
      )}

      {showAdjustPanel && selected && (
        <div className="rounded-xl border border-border bg-card p-4 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="font-medium text-sm">调整「{selected.name}」交易所配额</div>
              <p className="text-xs text-muted-foreground mt-1">
                编辑期间不会自动刷新覆盖；改完点「保存手动金额」。可一键集中到单一交易所。
              </p>
            </div>
            <button type="button" onClick={() => {
              draftDirtyRef.current = false
              syncDraftFromAccount(selected)
              setShowAdjustPanel(false)
            }} className="text-muted-foreground hover:text-foreground">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => concentrateDraftToExchange('asterdex')}
              className="px-2.5 py-1.5 rounded-lg border border-border bg-background hover:bg-muted text-xs"
            >
              全部 → {EXCHANGE_LABELS.asterdex}
            </button>
            <button
              type="button"
              onClick={() => concentrateDraftToExchange('hyperliquid')}
              className="px-2.5 py-1.5 rounded-lg border border-border bg-background hover:bg-muted text-xs"
            >
              全部 → {EXCHANGE_LABELS.hyperliquid}
            </button>
            <button
              type="button"
              onClick={() => concentrateDraftToExchange('binance')}
              className="px-2.5 py-1.5 rounded-lg border border-border bg-background hover:bg-muted text-xs"
            >
              全部 → {EXCHANGE_LABELS.binance}
            </button>
            <button
              type="button"
              onClick={() => concentrateDraftToExchange('asterdex', 0.1)}
              className="px-2.5 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 hover:bg-amber-500/15 text-xs text-amber-800 dark:text-amber-200"
            >
              Asterdex 90% + Reserve 10%
            </button>
          </div>
          <ExchangeAllocationGrid balances={draft} editable onChange={handleDraftChange} />
          <div className="text-xs text-muted-foreground">
            当前合计 ${Object.values(draft).reduce((s, v) => s + Number(v || 0), 0).toFixed(2)}
            {selected.status === 'running' && (
              <span className="ml-2 text-amber-600 dark:text-amber-400">运行中无法改配额，请先停止验证</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              value={presetId}
              onChange={e => setPresetId(e.target.value)}
              className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
            >
              {presets.map(p => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
            </select>
            <button
              type="button"
              onClick={handleApplyPreset}
              disabled={saving}
              className="px-3 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm flex items-center gap-2"
            >
              <Sparkles className="w-4 h-4" /> 套用科学配额
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || selected.status === 'running'}
              className="px-3 py-2 rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white text-sm flex items-center gap-2"
            >
              <Save className="w-4 h-4" /> 保存手动金额
            </button>
          </div>
        </div>
      )}

      {dashboard ? (
        <ArbitragePaperDashboard data={dashboard} />
      ) : (
        <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
          {loading ? '加载账户详情...' : '无法加载账户详情'}
        </div>
      )}

      {showCreateDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">添加套利 Paper 账户</h3>
              <button type="button" onClick={() => setShowCreateDialog(false)}><X className="w-4 h-4" /></button>
            </div>
            <div className="space-y-3">
              <input
                type="number"
                min={1}
                value={newEquity}
                onChange={e => setNewEquity(Number(e.target.value || 0))}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                placeholder="总资金 USDT"
              />
              <select
                value={presetId}
                onChange={e => setPresetId(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {presets.map(p => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
              </select>
              <button
                type="button"
                onClick={handleCreate}
                disabled={creating}
                className="w-full rounded-lg bg-blue-600 hover:bg-blue-700 text-white py-2 text-sm"
              >
                {creating ? '创建中...' : '确认创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showManageDialog && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">
                {manageMode === 'edit' ? '修改套利模拟账户' : manageMode === 'reset' ? '重置套利模拟账户' : '删除套利模拟账户'}
              </h3>
              <button type="button" onClick={() => setShowManageDialog(false)}><X className="w-4 h-4" /></button>
            </div>

            {manageMode === 'edit' && (
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">账户名称</label>
                  <input
                    value={manageName}
                    onChange={e => setManageName(e.target.value)}
                    className="w-full mt-1.5 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="例如：S8 Rh 专用 Paper"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">风险档位</label>
                  <select
                    value={manageRisk}
                    onChange={e => setManageRisk(e.target.value)}
                    className="w-full mt-1.5 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  >
                    <option value="conservative">保守</option>
                    <option value="balanced">均衡</option>
                    <option value="aggressive">进取</option>
                  </select>
                </div>
              </div>
            )}

            {manageMode === 'reset' && (
              <div className="space-y-3">
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">
                  重置会清空该账户当前流水、盈亏统计和分账户余额，然后按新本金和模板重新分配。正在运行时需要先停止验证。
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">重置后总资金 (USDT)</label>
                  <input
                    type="number"
                    min={1}
                    value={resetEquity}
                    onChange={e => setResetEquity(Number(e.target.value || 0))}
                    className="w-full mt-1.5 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">配额模板</label>
                  <select
                    value={resetPresetId}
                    onChange={e => setResetPresetId(e.target.value)}
                    className="w-full mt-1.5 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  >
                    {presets.map(p => <option key={p.preset_id} value={p.preset_id}>{p.name}</option>)}
                  </select>
                </div>
              </div>
            )}

            {manageMode === 'delete' && (
              <div className="space-y-3">
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-300">
                  确认删除「{selected.name}」？这会删除该账户的分账户余额和资金流水，并解除交易员绑定。正在运行时需要先停止验证。
                </div>
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowManageDialog(false)}
                className="px-3 py-2 rounded-lg border border-border bg-card hover:bg-muted text-sm"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleManageConfirm}
                disabled={saving}
                className={cn(
                  'px-3 py-2 rounded-lg text-white text-sm disabled:opacity-50',
                  manageMode === 'delete'
                    ? 'bg-red-600 hover:bg-red-700'
                    : manageMode === 'reset'
                      ? 'bg-amber-600 hover:bg-amber-700'
                      : 'bg-blue-600 hover:bg-blue-700',
                )}
              >
                {saving ? '处理中...' : manageMode === 'edit' ? '保存修改' : manageMode === 'reset' ? '确认重置' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
