import { useState, useEffect, useCallback } from 'react'
import { getSessions, getSessionStatus } from '@/api/fullauto'
import type { FullAutoSession } from '@/api/types'

export function useSession() {
  const [session, setSession] = useState<FullAutoSession | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const sessions = await getSessions()
      const active = sessions.find((s: FullAutoSession) =>
        ['running', 'defensive', 'paused'].includes(s.status)
      )
      if (active) {
        const detail = await getSessionStatus(active.session_id)
        setSession(detail)
      } else {
        setSession(null)
      }
    } catch (e) {
      console.error('[useSession] refresh failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Auto-refresh every 30s
  useEffect(() => {
    const iv = setInterval(refresh, 30000)
    return () => clearInterval(iv)
  }, [refresh])

  return { session, loading, refresh }
}
