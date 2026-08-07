import { useState, useEffect, useCallback } from 'react'
import type { Position } from '../api/types'
import { getPositions } from '../api/trading'

interface UsePositionsReturn {
  positions: Position[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
}

export function usePositions(accountId: number): UsePositionsReturn {
  const [positions, setPositions] = useState<Position[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchPositions = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getPositions(accountId)
      // getPositions 返回 { positions: Position[] }，需解包；兼容直接返回数组
      setPositions(Array.isArray(data) ? data : data.positions || [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch positions')
    } finally {
      setLoading(false)
    }
  }, [accountId])

  useEffect(() => {
    fetchPositions()
  }, [fetchPositions])

  return { positions, loading, error, refresh: fetchPositions }
}
