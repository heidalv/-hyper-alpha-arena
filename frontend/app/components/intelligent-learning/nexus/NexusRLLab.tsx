/**
 * NexusRLLab — RL 交易决策实验室（影子先行）
 *
 * 展示 RL agent 状态/策略统计/replay buffer；支持离线训练与影子决策（绝不下单）。
 */

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { SectionCard, StatCard, RefreshButton, InfoBanner } from '../IlcUi'
import { apiRequest } from '@/lib/api'

interface RLStatus {
  shadow?: {
    enabled?: boolean; shadow_only?: boolean; live_allowed?: boolean
    policy?: { trained_steps?: number; n_features?: number; n_actions?: number }
    actions?: Record<string, string>
  }
  replay?: { total?: number; by_action?: Record<string, number>; avg_reward?: number }
}

const ACTION_LABEL: Record<string, string> = { '0': '持仓', '1': '开多', '2': '开空', '3': '平仓' }

export function NexusRLLab() {
  const [status, setStatus] = useState<RLStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [symbol, setSymbol] = useState('BTC')
  const [decision, setDecision] = useState<any>(null)
  const [training, setTraining] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiRequest('/learning/rl/status')
      setStatus(await res.json())
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t) }, [load])

  const train = async () => {
    setTraining(true)
    try {
      const res = await apiRequest('/learning/rl/train', { method: 'POST', body: JSON.stringify({ batch_size: 256, epochs: 5 }) })
      const r = await res.json()
      alert(r.ok ? `训练完成：步数 ${r.trained_steps}，样本 ${r.samples_seen}，平均TD ${r.mean_abs_td}` : `训练失败：${r.error}`)
      load()
    } catch (e: any) { alert(e.message || '训练失败') } finally { setTraining(false) }
  }

  const decide = async () => {
    try {
      const res = await apiRequest('/learning/rl/decide', { method: 'POST', body: JSON.stringify({ symbol, timeframe: '1h', position: 0 }) })
      setDecision(await res.json())
    } catch (e: any) { alert(e.message || '决策失败') }
  }

  const sh = status?.shadow
  const rp = status?.replay

  return (
    <div className="space-y-4">
      <InfoBanner title="安全说明" variant="warn">
        RL agent 默认<strong>仅影子模式</strong>，与现有管线并行输出决策，<strong>绝不接管下单</strong>；
        接管实盘需关闭 RL_SHADOW_ONLY + Governor 审批 + paper 验证达标。
      </InfoBanner>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="RL 开关" value={sh?.enabled ? '启用' : '关闭'} tone={sh?.enabled ? 'good' : 'default'} />
        <StatCard label="影子模式" value={sh?.shadow_only ? '是' : '否'} tone={sh?.shadow_only ? 'good' : 'warn'} />
        <StatCard label="实盘接管" value={sh?.live_allowed ? '允许' : '禁止'} tone={sh?.live_allowed ? 'warn' : 'good'} />
        <StatCard label="训练步数" value={sh?.policy?.trained_steps ?? 0} hint={`${sh?.policy?.n_features ?? 0} 特征`} />
      </div>

      <SectionCard title="经验回放缓冲区" description="来自回测折算 + 交易 outcome 的转移样本"
        action={<RefreshButton onClick={load} loading={loading} />}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="样本总数" value={rp?.total ?? 0} />
          <StatCard label="平均奖励" value={(rp?.avg_reward ?? 0).toFixed(5)} tone={(rp?.avg_reward ?? 0) >= 0 ? 'good' : 'bad'} />
          <div className="col-span-2 rounded-md border p-3">
            <div className="text-xs text-muted-foreground mb-1">动作分布</div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(rp?.by_action || {}).map(([a, c]) => (
                <Badge key={a} variant="outline">{ACTION_LABEL[a] || a}: {c as number}</Badge>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-3">
          <Button size="sm" onClick={train} disabled={training}>{training ? '训练中…' : '离线训练策略'}</Button>
        </div>
      </SectionCard>

      <SectionCard title="影子决策" description="用当前策略对某标的产出决策（不执行）">
        <div className="flex items-center gap-2">
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-28 rounded-md border bg-transparent px-2 py-1 text-sm" placeholder="BTC" />
          <Button size="sm" variant="outline" onClick={decide}>产出影子决策</Button>
        </div>
        {decision && (
          <div className="mt-3 rounded-md border p-3 text-sm">
            {decision.enabled === false ? (
              <span className="text-muted-foreground">RL 未启用：{decision.reason}</span>
            ) : decision.error ? (
              <span className="text-red-400">{decision.error}</span>
            ) : (
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Badge>{decision.action_name}</Badge>
                  <span className="text-muted-foreground">置信度 {(decision.confidence * 100).toFixed(1)}%</span>
                  <span className="ml-auto text-xs text-muted-foreground">executed: {String(decision.executed)}</span>
                </div>
                <div className="text-xs text-muted-foreground font-mono">Q: [{(decision.q_values || []).join(', ')}]</div>
              </div>
            )}
          </div>
        )}
      </SectionCard>
    </div>
  )
}

export default NexusRLLab
