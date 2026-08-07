import React, { useState, useCallback, useRef } from 'react'

interface PullToRefreshProps {
  onRefresh: () => Promise<void>
  children: React.ReactNode
}

export const PullToRefresh: React.FC<PullToRefreshProps> = ({ onRefresh, children }) => {
  const [pulling, setPulling] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [pullDistance, setPullDistance] = useState(0)
  const startYRef = useRef(0)
  const threshold = 80

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    const scrollTop = (e.target as HTMLElement).closest('.scrollable')?.scrollTop ?? 0
    if (scrollTop === 0) {
      startYRef.current = e.touches[0].clientY
    }
  }, [])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const dy = e.touches[0].clientY - startYRef.current
    if (dy > 0) {
      setPulling(true)
      setPullDistance(Math.min(dy, 160))
    }
  }, [])

  const handleTouchEnd = useCallback(async () => {
    if (pullDistance >= threshold && !refreshing) {
      setRefreshing(true)
      try {
        await onRefresh()
      } finally {
        setRefreshing(false)
      }
    }
    setPulling(false)
    setPullDistance(0)
  }, [pullDistance, refreshing, onRefresh])

  return (
    <div
      className="scrollable"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {/* Pull indicator */}
      {(pulling || refreshing) && (
        <div
          className="flex items-center justify-center text-muted text-sm"
          style={{ height: pullDistance || (refreshing ? 40 : 0) }}
        >
          {refreshing ? (
            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <span>{pullDistance >= threshold ? '松开刷新' : '下拉刷新'}</span>
          )}
        </div>
      )}
      {children}
    </div>
  )
}
