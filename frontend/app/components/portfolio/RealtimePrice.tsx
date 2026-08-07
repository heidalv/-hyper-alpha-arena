/**
 * RealtimePrice Component
 *
 * PERFORMANCE OPTIMIZATION:
 * - Removed HTTP polling to eliminate duplicate data fetching
 * - Now relies entirely on WebSocket for real-time updates
 * - Single HTTP fetch on mount only if price is not available via WebSocket
 */
import React, { useEffect, useState, useRef } from 'react'
import FlipNumber from './FlipNumber'

interface RealtimePriceProps {
  symbol: string
  wsRef?: React.MutableRefObject<WebSocket | null>
  className?: string
}

export default function RealtimePrice({ symbol, wsRef, className = "" }: RealtimePriceProps) {
  const [price, setPrice] = useState<number | null>(null)
  const [priceChange, setPriceChange] = useState<'up' | 'down' | null>(null)
  const hasReceivedWsData = useRef(false)
  const priceChangeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // WebSocket listener for real-time price updates
    if (wsRef?.current) {
      const handleMessage = (event: MessageEvent) => {
        try {
          const message = JSON.parse(event.data)
          // Handle price updates from WebSocket
          if (message?.type === 'price_update' && message.symbol === symbol) {
            const newPrice = Number(message.price)
            if (!isNaN(newPrice)) {
              hasReceivedWsData.current = true
              setPrice(prevPrice => {
                // Set price change direction for animation
                if (prevPrice !== null && prevPrice !== newPrice) {
                  setPriceChange(newPrice > prevPrice ? 'up' : newPrice < prevPrice ? 'down' : null)
                  // Clear the change indicator after animation
                  if (priceChangeTimeoutRef.current) clearTimeout(priceChangeTimeoutRef.current)
                  priceChangeTimeoutRef.current = setTimeout(() => setPriceChange(null), 1000)
                }
                return newPrice
              })
            }
          }
        } catch {
          // Ignore non-JSON messages
        }
      }

      const ws = wsRef.current
      ws.addEventListener('message', handleMessage)

      return () => {
        ws.removeEventListener('message', handleMessage)
        if (priceChangeTimeoutRef.current) clearTimeout(priceChangeTimeoutRef.current)
      }
    }
  }, [wsRef, symbol])

  useEffect(() => {
    // PERFORMANCE: Single HTTP fetch on mount only if WebSocket hasn't provided data yet
    // Removed the 5-second polling interval to reduce server load
    const fetchPriceOnce = async () => {
      // Only fetch if we haven't received WebSocket data yet
      if (hasReceivedWsData.current) {
        return
      }

      try {
        const response = await fetch(`/api/market/price/${symbol}`)
        if (response.ok) {
          const data = await response.json()
          const newPrice = data.price
          if (newPrice && !isNaN(newPrice) && !hasReceivedWsData.current) {
            setPrice(prevPrice => {
              // Only set if still no WebSocket data
              if (prevPrice === null) {
                return newPrice
              }
              return prevPrice
            })
          }
        }
      } catch (error) {
        console.error(`Error fetching price for ${symbol}:`, error)
      }
    }

    // Fetch once on mount (or if no WebSocket)
    fetchPriceOnce()

    // PERFORMANCE: No interval polling - rely on WebSocket for updates
    // This eliminates duplicate data fetching and reduces server load
  }, [symbol])

  if (price === null) {
    return (
      <div className={`text-xs text-muted-foreground ${className}`}>
        --
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      <FlipNumber
        value={price}
        prefix="$"
        decimals={2}
        className={`text-xs font-medium transition-colors duration-300 ${
          priceChange === 'up'
            ? 'text-green-500'
            : priceChange === 'down'
            ? 'text-red-500'
            : 'text-muted-foreground'
        }`}
      />
      {priceChange && (
        <span className={`text-xs transition-opacity duration-1000 ${
          priceChange === 'up' ? 'text-green-500' : 'text-red-500'
        }`}>
          {priceChange === 'up' ? '↗' : '↘'}
        </span>
      )}
    </div>
  )
}