import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

export const EXCHANGE_LABELS: Record<string, string> = {
  asterdex: 'Asterdex',
  hyperliquid: 'HL',
  binance: 'Binance',
  okx: 'OKX',
  bybit: 'Bybit',
  gateio: 'Gate.io',
  reserve: 'Reserve',
}

export const EXCHANGE_ORDER = ['asterdex', 'hyperliquid', 'binance', 'okx', 'bybit', 'gateio', 'reserve']

function normalizeBalances(balances: Record<string, number>): Record<string, number> {
  const next: Record<string, number> = {}
  for (const exchange of EXCHANGE_ORDER) {
    next[exchange] = Math.max(Number(balances[exchange] || 0), 0)
  }
  return next
}

function AllocationInput({
  exchange,
  amount,
  onCommit,
}: {
  exchange: string
  amount: number
  onCommit: (exchange: string, value: number) => void
}) {
  const [text, setText] = useState(String(amount))
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (!focused) {
      setText(String(amount))
    }
  }, [amount, focused])

  const commit = () => {
    const trimmed = text.trim()
    const n = trimmed === '' ? 0 : Number(trimmed)
    const safe = Number.isFinite(n) ? Math.max(n, 0) : 0
    setText(String(safe))
    onCommit(exchange, safe)
  }

  return (
    <input
      value={text}
      type="number"
      min={0}
      step="1"
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false)
        commit()
      }}
      onChange={e => setText(e.target.value)}
      onKeyDown={e => {
        if (e.key === 'Enter') {
          e.currentTarget.blur()
        }
      }}
      className="mt-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
    />
  )
}

export default function ExchangeAllocationGrid({
  balances,
  editable = false,
  onChange,
}: {
  balances: Record<string, number>
  editable?: boolean
  onChange?: (next: Record<string, number>) => void
}) {
  const normalized = normalizeBalances(balances)
  const total = Object.values(normalized).reduce((sum, v) => sum + Number(v || 0), 0)

  const setAmount = (exchange: string, value: number) => {
    onChange?.({ ...normalized, [exchange]: value })
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
      {EXCHANGE_ORDER.map(exchange => {
        const amount = Number(normalized[exchange] || 0)
        const pct = total > 0 ? amount / total : 0
        return (
          <div
            key={exchange}
            className={cn(
              'rounded-xl border border-border bg-card p-4',
              exchange === 'reserve' && 'border-amber-500/30 bg-amber-500/5',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="font-medium">{EXCHANGE_LABELS[exchange] || exchange}</div>
              <div className="text-xs text-muted-foreground">{Math.round(pct * 100)}%</div>
            </div>
            {editable ? (
              <AllocationInput exchange={exchange} amount={amount} onCommit={setAmount} />
            ) : (
              <div className="mt-3 text-2xl font-bold">${amount.toFixed(2)}</div>
            )}
            <div className="mt-3 h-2 rounded-full bg-muted overflow-hidden">
              <div
                className={cn('h-full rounded-full', exchange === 'reserve' ? 'bg-amber-500' : 'bg-blue-500')}
                style={{ width: `${Math.min(pct * 100, 100)}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
