import { useCallback, useEffect, useState } from 'react'
import { Link2, Unlink, User } from 'lucide-react'
import { toast } from 'react-hot-toast'
import {
  type ArbitragePaperAccount,
  type BindableArbitrageTrader,
  bindArbitragePaperTrader,
  getBindableArbitrageTraders,
  unbindArbitragePaperTrader,
} from '@/lib/arbitrageApi'

export default function ArbitrageTraderBinding({
  account,
  onUpdated,
}: {
  account: ArbitragePaperAccount | null
  onUpdated?: () => void
}) {
  const [traders, setTraders] = useState<BindableArbitrageTrader[]>([])
  const [selectedTraderId, setSelectedTraderId] = useState<number | ''>('')
  const [loading, setLoading] = useState(false)
  const [binding, setBinding] = useState(false)

  const load = useCallback(async () => {
    if (!account) return
    setLoading(true)
    try {
      const rows = await getBindableArbitrageTraders(account.id)
      setTraders(rows)
      if (account.owner_account_id) {
        setSelectedTraderId(account.owner_account_id)
      }
    } finally {
      setLoading(false)
    }
  }, [account])

  useEffect(() => {
    load()
  }, [load])

  if (!account) return null

  const bound = account.trader_profile || (account.owner_account_id ? {
    account_name: account.owner_account_name || `交易员#${account.owner_account_id}`,
    enabled_strategies: [],
  } : null)

  const handleBind = async () => {
    if (!selectedTraderId) {
      toast.error('请选择要绑定的专用套利交易员')
      return
    }
    setBinding(true)
    try {
      const res = await bindArbitragePaperTrader(account.id, Number(selectedTraderId))
      if (!res.success) {
        toast.error(res.error || '绑定失败')
        return
      }
      toast.success('已绑定专用套利交易员')
      onUpdated?.()
      await load()
    } finally {
      setBinding(false)
    }
  }

  const handleUnbind = async () => {
    setBinding(true)
    try {
      const res = await unbindArbitragePaperTrader(account.id)
      if (!res.success) {
        toast.error(res.error || '解绑失败')
        return
      }
      toast.success('已解绑交易员')
      setSelectedTraderId('')
      onUpdated?.()
      await load()
    } finally {
      setBinding(false)
    }
  }

  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 space-y-3">
      <div className="flex items-start gap-2">
        <User className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
        <div>
          <div className="font-semibold text-sm">绑定专用套利交易员</div>
          <p className="text-xs text-muted-foreground mt-1">
            须先在「AI 交易员」勾选「可用于积分套利」，并在「套利中心 → 交易员套利」配好双模型与策略。
          </p>
        </div>
      </div>

      {bound ? (
        <div className="rounded-lg border border-green-500/30 bg-green-500/10 px-3 py-2 text-sm">
          <div className="font-medium text-green-700 dark:text-green-300">
            已绑定：{account.trader_profile?.account_name || account.owner_account_name}
          </div>
          {account.trader_profile?.enabled_strategies?.length ? (
            <div className="text-xs text-muted-foreground mt-1">
              授权策略：{account.trader_profile.enabled_strategies.join(' / ')}
            </div>
          ) : null}
          <button
            type="button"
            onClick={handleUnbind}
            disabled={binding}
            className="mt-2 text-xs inline-flex items-center gap-1 text-red-600 hover:underline disabled:opacity-50"
          >
            <Unlink className="w-3 h-3" /> 解绑
          </button>
        </div>
      ) : (
        <>
          <select
            value={selectedTraderId}
            onChange={e => setSelectedTraderId(e.target.value ? Number(e.target.value) : '')}
            disabled={loading || binding}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm"
          >
            <option value="">
              {loading ? '加载可绑定交易员...' : traders.length ? '选择专用套利交易员' : '暂无符合条件的交易员'}
            </option>
            {traders.filter(t => t.available).map(t => (
              <option key={t.trader_account_id} value={t.trader_account_id}>
                {t.trader_name} · 策略 {t.enabled_strategies.join('/')} · 分析#{t.strategy_llm_config_id}/执行#{t.execution_llm_config_id}
              </option>
            ))}
          </select>
          {traders.length === 0 && !loading && (
            <p className="text-xs text-muted-foreground">
              请先到「AI 交易员」勾选「可用于积分套利」，再到「交易员套利」Tab 配好双模型并保存。
            </p>
          )}
          <button
            type="button"
            onClick={handleBind}
            disabled={binding || !selectedTraderId}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white px-3 py-2 text-sm"
          >
            <Link2 className="w-4 h-4" /> 确认绑定
          </button>
        </>
      )}
    </div>
  )
}
