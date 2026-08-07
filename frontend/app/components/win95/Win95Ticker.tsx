/**
 * Win95Ticker — Real-time market price ticker bar
 * 动态读取用户配置的交易对（不硬编码），跟随配置变化实时更新
 * 支持点击交易对跳转到K线图页面
 */
import { useState, useEffect, useRef } from 'react'
import { useTradingPairs } from '@/hooks/useTradingPairs'

interface TickerData {
  symbol: string
  price: number
  change24h: number
}

interface Win95TickerProps {
  /** 点击交易对时触发，用于切换页面 */
  onNavigate?: (page: string) => void
}

export default function Win95Ticker({ onNavigate }: Win95TickerProps) {
  const [prices, setPrices] = useState<TickerData[]>([])
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 动态获取用户配置的交易对
  const { symbols } = useTradingPairs()

  useEffect(() => {
    // 没有配置交易对时不发请求
    if (!symbols || symbols.length === 0) return

    const fetchPrices = async () => {
      try {
        const symList = symbols.join(',')
        const resp = await fetch(
          `/api/market/prices?market=hyperliquid&symbols=${symList}&_t=${Date.now()}`,
          { cache: 'no-store' }
        )
        if (resp.ok) {
          const data = await resp.json()
          if (Array.isArray(data)) {
            // 按配置顺序排序
            const priceMap = new Map(data.map((d: any) => [
              (d.symbol || d.coin || '').toUpperCase(),
              { price: d.price || d.mark_price || 0, change24h: d.percentage24h || d.change24h || 0 }
            ]))
            setPrices(
              symbols
                .map(sym => ({
                  symbol: sym,
                  price: priceMap.get(sym)?.price ?? 0,
                  change24h: priceMap.get(sym)?.change24h ?? 0,
                }))
                .filter(t => t.price > 0)
            )
          }
        }
      } catch {
        // silent fail
      }
    }

    fetchPrices()
    // 顶部行情条全局展示，固定 3s 轮询（不依赖页面 keep-alive 上下文）
    intervalRef.current = setInterval(fetchPrices, 3000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [symbols])

  const fmt = (n: number) => {
    if (n >= 1000) return '$' + n.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    if (n >= 1) return '$' + n.toFixed(2)
    return '$' + n.toFixed(4)
  }

  /** 点击交易对：派发自定义事件通知 KlinesView 切换交易对，并跳转到 K线图页面 */
  const handleTickerClick = (symbol: string) => {
    window.dispatchEvent(new CustomEvent('klines:navigate', { detail: { symbol } }))
    onNavigate?.('klines')
  }

  return (
    <div className="w95-ticker">
      {prices.length > 0 ? (
        prices.map((t) => (
          <div
            key={t.symbol}
            className="w95-ticker-item w95-ticker-item--clickable"
            title={`点击查看 ${t.symbol} K线图`}
            onClick={() => handleTickerClick(t.symbol)}
          >
            <span
              className="w95-led"
              style={{
                background: t.change24h > 0 ? '#00FF00' : t.change24h < 0 ? '#FF0000' : '#FFFF00',
                borderColor: t.change24h > 0 ? '#008000' : t.change24h < 0 ? '#800000' : '#808000',
              }}
            />
            <b>{t.symbol}</b>
            <span style={{ color: t.change24h >= 0 ? '#008000' : '#FF0000' }}>
              {fmt(t.price)}
            </span>
            <span style={{ color: t.change24h >= 0 ? '#008000' : '#FF0000', fontSize: '12px' }}>
              {t.change24h >= 0 ? '▲' : '▼'}
              {t.change24h >= 0 ? '+' : ''}
              {t.change24h.toFixed(2)}%
            </span>
          </div>
        ))
      ) : (
        <span style={{ color: '#808080', fontStyle: 'italic' }}>加载行情中...</span>
      )}
      <div style={{ marginLeft: 'auto', color: '#808080', display: 'flex', alignItems: 'center', gap: 4 }}>
        <span className="w95-blink">●</span> LIVE
      </div>

      <style>{`
        .w95-ticker {
          display: flex;
          gap: 10px;
          padding: 0 8px;
          background: #FAFAFA;
          border: 1px solid #D0D0D0;
          font-family: 'Consolas', 'Courier New', monospace;
          font-size: 13px;
          line-height: 1;
          flex-shrink: 0;
          margin: 2px 4px;
          overflow-x: auto;
          overflow-y: hidden;
          height: 28px;
          min-height: 28px;
          align-items: center;
          scrollbar-width: none;
        }
        .w95-ticker::-webkit-scrollbar {
          display: none;
        }
        .w95-ticker-item {
          display: flex;
          gap: 4px;
          align-items: center;
          white-space: nowrap;
          height: 22px;
          padding: 0 4px;
          border: 1px solid transparent;
        }
        .w95-ticker-item--clickable {
          cursor: pointer;
        }
        .w95-ticker-item--clickable:hover {
          background: #000080;
          color: #FFFFFF;
          border: 1px solid #C0C0C0;
        }
        .w95-ticker-item--clickable:hover b,
        .w95-ticker-item--clickable:hover span {
          color: #FFFFFF !important;
        }
        .w95-ticker-item--clickable:active {
          background: #000060;
          border: 1px inset #808080;
        }
        .w95-led {
          width: 8px;
          height: 8px;
          border-radius: 50% !important;
          display: inline-block;
          border: 1px solid;
          flex-shrink: 0;
        }
        @keyframes w95blink { 50% { opacity: 0; } }
        .w95-blink { animation: w95blink 1s step-end infinite; }
      `}</style>
    </div>
  )
}
