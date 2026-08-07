import React, { useState } from 'react'
import TouchButton from '@/components/ui/TouchButton'
import BottomSheet from '@/components/ui/BottomSheet'
import { apiRequest } from '@/api/client'

interface OrderPanelProps {
  symbol: string
  accountId: number
  onOrderPlaced: () => void
}

export default function OrderPanel({ symbol, accountId, onOrderPlaced }: OrderPanelProps) {
  const [side, setSide] = useState<'long' | 'short'>('long')
  const [leverage, setLeverage] = useState(5)
  const [quantity, setQuantity] = useState('')
  const [tpPrice, setTpPrice] = useState('')
  const [slPrice, setSlPrice] = useState('')
  const [acting, setActing] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const margin = quantity ? (parseFloat(quantity) / leverage).toFixed(2) : '0.00'
  const fee = quantity ? (parseFloat(quantity) * 0.001).toFixed(2) : '0.00'

  const quickPcts = [25, 50, 75, 100]

  const handleSubmit = async () => {
    if (!quantity || parseFloat(quantity) <= 0) return
    setActing(true)
    try {
      await apiRequest('/paper/place-order', {
        method: 'POST',
        body: JSON.stringify({
          account_id: accountId,
          symbol,
          side,
          size: parseFloat(quantity),
          leverage,
          tp_price: tpPrice ? parseFloat(tpPrice) : undefined,
          sl_price: slPrice ? parseFloat(slPrice) : undefined,
        }),
      })
      setQuantity('')
      setTpPrice('')
      setSlPrice('')
      setShowConfirm(false)
      onOrderPlaced()
    } catch (e: any) {
      alert(e.message || '下单失败')
    } finally {
      setActing(false)
    }
  }

  return (
    <div className="card space-y-3">
      <p className="text-xs text-terminal-muted font-semibold">下单</p>

      {/* Side */}
      <div className="flex gap-2">
        {(['long', 'short'] as const).map(s => (
          <button
            key={s}
            onClick={() => setSide(s)}
            className={`flex-1 py-2.5 rounded text-sm font-bold transition-colors ${
              side === s
                ? s === 'long' ? 'bg-terminal-profit text-white' : 'bg-terminal-loss text-white'
                : 'bg-terminal-bg border border-terminal-border text-terminal-muted'
            }`}
          >
            {s === 'long' ? '做多 Long' : '做空 Short'}
          </button>
        ))}
      </div>

      {/* Leverage */}
      <div>
        <label className="text-xs text-terminal-muted block mb-1">杠杆: {leverage}x</label>
        <input
          type="range"
          min="1"
          max="20"
          value={leverage}
          onChange={(e) => setLeverage(parseInt(e.target.value))}
          className="w-full h-1.5 bg-terminal-bg rounded-full appearance-none cursor-pointer accent-terminal-primary"
        />
        <div className="flex justify-between text-[10px] text-terminal-muted mt-0.5">
          <span>1x</span><span>10x</span><span>20x</span>
        </div>
      </div>

      {/* Quantity */}
      <div>
        <label className="text-xs text-terminal-muted block mb-1">数量 (USDT)</label>
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          placeholder="0.00"
          className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-primary"
        />
        <div className="flex gap-1 mt-1">
          {quickPcts.map(pct => (
            <button
              key={pct}
              onClick={() => {/* Quick fill would need balance context */}}
              className="flex-1 py-1 rounded text-[10px] bg-terminal-bg border border-terminal-border text-terminal-muted active:opacity-70"
            >
              {pct}%
            </button>
          ))}
        </div>
      </div>

      {/* TP/SL */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-xs text-terminal-muted block mb-1">止盈 TP</label>
          <input
            type="number"
            value={tpPrice}
            onChange={(e) => setTpPrice(e.target.value)}
            placeholder="选填"
            className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-xs text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-profit"
          />
        </div>
        <div>
          <label className="text-xs text-terminal-muted block mb-1">止损 SL</label>
          <input
            type="number"
            value={slPrice}
            onChange={(e) => setSlPrice(e.target.value)}
            placeholder="选填"
            className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-xs text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-loss"
          />
        </div>
      </div>

      {/* Info */}
      <div className="flex justify-between text-xs">
        <span className="text-terminal-muted">保证金: <span className="text-terminal-text">${margin}</span></span>
        <span className="text-terminal-muted">手续费: <span className="text-terminal-text">${fee}</span></span>
      </div>

      {/* Place Order */}
      <TouchButton
        variant={side === 'long' ? 'success' : 'danger'}
        fullWidth
        disabled={!quantity || parseFloat(quantity) <= 0}
        onClick={() => setShowConfirm(true)}
      >
        {side === 'long' ? '买入做多' : '卖出做空'} {symbol}
      </TouchButton>

      {/* Confirm BottomSheet */}
      <BottomSheet open={showConfirm} onClose={() => setShowConfirm(false)} title="确认下单">
        <div className="space-y-4">
          <div className="text-center">
            <p className="text-lg font-bold">{symbol}</p>
            <p className={`text-sm font-semibold ${side === 'long' ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              {side === 'long' ? '做多' : '做空'} · {leverage}x
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div><span className="text-xs text-terminal-muted">数量</span><p className="font-mono">{quantity} USDT</p></div>
            <div><span className="text-xs text-terminal-muted">保证金</span><p className="font-mono">${margin}</p></div>
            {tpPrice && <div><span className="text-xs text-terminal-muted">止盈</span><p className="font-mono text-terminal-profit">${tpPrice}</p></div>}
            {slPrice && <div><span className="text-xs text-terminal-muted">止损</span><p className="font-mono text-terminal-loss">${slPrice}</p></div>}
          </div>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setShowConfirm(false)}>取消</TouchButton>
            <TouchButton variant={side === 'long' ? 'success' : 'danger'} fullWidth loading={acting} onClick={handleSubmit}>
              确认下单
            </TouchButton>
          </div>
        </div>
      </BottomSheet>
    </div>
  )
}
