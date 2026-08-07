import { useState, useEffect, useCallback } from 'react'
import { getTradingPairs } from '@/api/config'

export function useTradingPairs() {
  const [symbols, setSymbols] = useState<string[]>([])
  const [exchangeSymbols, setExchangeSymbols] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getTradingPairs()
      setSymbols(data.symbols || [])
      setExchangeSymbols(data.exchange_symbols || [])
      setError(null)
    } catch (e: any) {
      setError(e.message || '加载交易对失败')
      console.error('[useTradingPairs]', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch() }, [fetch])

  return { symbols, exchangeSymbols, loading, error, refetch: fetch }
}
