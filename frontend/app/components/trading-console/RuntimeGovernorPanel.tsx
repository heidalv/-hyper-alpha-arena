/**
 * RuntimeGovernor — 运行时门槛审批面板
 */
import { useCallback, useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Settings2, Check, X } from 'lucide-react'

const GAP_API = '/api/gap-closure'

interface PendingPatch {
  patch_id: string
  keys: Record<string, unknown>
  reason: string
  status: string
}

export function RuntimeGovernorPanel() {
  const [pending, setPending] = useState<PendingPatch[]>([])
  const [tuning, setTuning] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [pRes, tRes] = await Promise.all([
        fetch(`${GAP_API}/runtime/pending`),
        fetch(`${GAP_API}/runtime/tuning`),
      ])
      if (pRes.ok) {
        const p = await pRes.json()
        setPending(p.pending || [])
      }
      if (tRes.ok) {
        const t = await tRes.json()
        setTuning(t.tuning || {})
      }
    } catch (e) {
      console.error('RuntimeGovernor 加载失败', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000)
    return () => clearInterval(id)
  }, [refresh])

  const approve = async (patchId: string) => {
    await fetch(`${GAP_API}/runtime/approve/${patchId}`, { method: 'POST' })
    refresh()
  }

  const reject = async (patchId: string) => {
    await fetch(`${GAP_API}/runtime/reject/${patchId}`, { method: 'POST' })
    refresh()
  }

  const tuningKeys = ['max_daily_trades', 'scalp_min_confidence', 'min_risk_reward']

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Settings2 className="h-4 w-4" />
          运行时门槛（Governor）
          <Badge variant="outline" className="ml-auto text-xs">
            {pending.length} 待审批
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2 text-xs">
          {tuningKeys.map((k) => {
            const v = tuning[k]
            const display =
              v && typeof v === 'object' && 'value' in (v as object)
                ? String((v as { value: unknown }).value)
                : String(v ?? '—')
            return (
              <div key={k} className="border rounded p-2">
                <div className="text-muted-foreground truncate">{k}</div>
                <div className="font-mono font-medium">{display}</div>
              </div>
            )
          })}
        </div>
        <ScrollArea className="h-[160px]">
          {loading && pending.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4">加载中...</p>
          ) : pending.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4">无待审批 patch</p>
          ) : (
            <div className="space-y-2">
              {pending.map((p) => (
                <div key={p.patch_id} className="border rounded p-2 text-xs">
                  <div className="font-medium">{p.patch_id}</div>
                  <div className="text-muted-foreground">{p.reason || '无说明'}</div>
                  <pre className="text-[10px] mt-1 overflow-x-auto">
                    {JSON.stringify(p.keys, null, 0)}
                  </pre>
                  <div className="flex gap-2 mt-2">
                    <Button size="sm" variant="default" className="h-7 text-xs" onClick={() => approve(p.patch_id)}>
                      <Check className="h-3 w-3 mr-1" /> 批准
                    </Button>
                    <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => reject(p.patch_id)}>
                      <X className="h-3 w-3 mr-1" /> 拒绝
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

export default RuntimeGovernorPanel
