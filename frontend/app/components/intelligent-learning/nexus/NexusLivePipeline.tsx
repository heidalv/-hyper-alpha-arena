/**
 * NexusLivePipeline — 进化中枢实时管线（WebSocket + 血缘图 + 时序图表）
 *
 * - 通过 learningCoreApi.subscribeLearningEvents 订阅内核 EvolutionEnvelope 血缘事件；
 * - 实时事件流 + 各阶段计数柱状图（recharts）；
 * - 点击某条链路，用 @xyflow 渲染"假设→验证→进化→学习→部署"血缘流程图。
 */

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ReactFlow, Background, Controls, MarkerType,
  type Node, type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import {
  subscribeLearningEvents, getRecentLineages, getLineage, getLearningOverview,
  type EvolutionEnvelope, type LineageSummary, type EvolutionStage,
} from '@/lib/learningCoreApi'
import { SectionCard, StatCard, RefreshButton } from '../IlcUi'
import { cn } from '@/lib/utils'

const STAGE_COLOR: Record<EvolutionStage, string> = {
  hypothesis: '#7aa2f7',
  validate: '#7dcfff',
  evolve: '#bb9af7',
  learn: '#9ece6a',
  rl_decide: '#e0af68',
  deploy: '#73daca',
  observe: '#565f89',
  feedback: '#f7768e',
}

const STAGE_LABEL: Record<string, string> = {
  hypothesis: '假设', validate: '验证', evolve: '进化', learn: '学习',
  rl_decide: 'RL决策', deploy: '部署', observe: '观测', feedback: '反馈',
}

const STAGE_ORDER: EvolutionStage[] = [
  'hypothesis', 'validate', 'evolve', 'learn', 'rl_decide', 'deploy', 'observe', 'feedback',
]

export function NexusLivePipeline() {
  const [events, setEvents] = useState<EvolutionEnvelope[]>([])
  const [lineages, setLineages] = useState<LineageSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [lineageNodes, setLineageNodes] = useState<EvolutionEnvelope[]>([])
  const [connected, setConnected] = useState(false)
  const [stats, setStats] = useState<Record<string, number>>({})
  const flashRef = useRef<HTMLDivElement | null>(null)

  const refreshLineages = useCallback(async () => {
    try {
      const [ls, ov] = await Promise.all([getRecentLineages(30), getLearningOverview()])
      setLineages(ls)
      setStats((ov?.core?.ledger?.by_stage as Record<string, number>) || {})
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    refreshLineages()
    const unsub = subscribeLearningEvents((env) => {
      setConnected(true)
      setEvents((prev) => [env, ...prev].slice(0, 120))
      setStats((prev) => ({ ...prev, [env.stage]: (prev[env.stage] || 0) + 1 }))
      if (flashRef.current) {
        flashRef.current.classList.remove('nexus-flash')
        void flashRef.current.offsetWidth
        flashRef.current.classList.add('nexus-flash')
      }
    })
    const timer = setInterval(refreshLineages, 20000)
    return () => { unsub(); clearInterval(timer) }
  }, [refreshLineages])

  const openLineage = useCallback(async (id: string) => {
    setSelected(id)
    try { setLineageNodes(await getLineage(id)) } catch { setLineageNodes([]) }
  }, [])

  const chartData = useMemo(
    () => STAGE_ORDER.map((s) => ({ stage: STAGE_LABEL[s], key: s, count: stats[s] || 0 })),
    [stats],
  )

  // 血缘图布局：按 created_at 顺序水平排布
  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = []
    const es: Edge[] = []
    lineageNodes.forEach((n, i) => {
      ns.push({
        id: n.envelope_id,
        position: { x: i * 210, y: (i % 2) * 90 },
        data: {
          label: `${STAGE_LABEL[n.stage] || n.stage}\n${n.source}${n.symbol ? ' · ' + n.symbol : ''}`,
        },
        style: {
          background: STAGE_COLOR[n.stage] || '#565f89',
          color: '#1a1b26', border: 'none', borderRadius: 10,
          fontSize: 11, width: 180, padding: 8, whiteSpace: 'pre-line',
        },
      })
      if (n.parent_id) {
        es.push({
          id: `${n.parent_id}-${n.envelope_id}`,
          source: n.parent_id, target: n.envelope_id,
          animated: true, markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: '#7aa2f7' },
        })
      }
    })
    return { nodes: ns, edges: es }
  }, [lineageNodes])

  return (
    <div className="space-y-4">
      <style>{`
        @keyframes nexusFlash { 0% { box-shadow: 0 0 0 0 rgba(122,162,247,0.6);} 100% { box-shadow: 0 0 0 8px rgba(122,162,247,0);} }
        .nexus-flash { animation: nexusFlash 0.6s ease-out; }
      `}</style>

      <motion.div
        className="grid grid-cols-2 md:grid-cols-4 gap-3"
        initial="hidden"
        animate="show"
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      >
        {[
          <div ref={flashRef} className="rounded-md" key="conn">
            <StatCard label="实时连接" value={connected ? '● 已连接' : '○ 待连接'} tone={connected ? 'good' : 'warn'} />
          </div>,
          <StatCard key="ev" label="事件流" value={events.length} hint="最近窗口" />,
          <StatCard key="ln" label="链路数" value={lineages.length} hint="最近血缘" />,
          <StatCard key="tot" label="总事件" value={Object.values(stats).reduce((a, b) => a + b, 0)} hint="账本累计" />,
        ].map((card, i) => (
          <motion.div
            key={i}
            variants={{ hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0 } }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            {card}
          </motion.div>
        ))}
      </motion.div>

      <SectionCard title="各阶段事件分布" description="EvolutionEnvelope 按阶段聚合（实时累加）">
        <div style={{ width: '100%', height: 180 }}>
          <ResponsiveContainer>
            <BarChart data={chartData}>
              <XAxis dataKey="stage" tick={{ fontSize: 11, fill: '#a9b1d6' }} />
              <YAxis tick={{ fontSize: 11, fill: '#a9b1d6' }} allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#1a1b26', border: '1px solid #414868', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {chartData.map((d) => (
                  <Cell key={d.key} fill={STAGE_COLOR[d.key as EvolutionStage] || '#565f89'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </SectionCard>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="实时事件流" description="内核血缘事件（WebSocket 推送）"
          action={<RefreshButton onClick={refreshLineages} loading={false} />}>
          <div className="space-y-1.5 max-h-[360px] overflow-y-auto">
            {events.length === 0 && <p className="text-sm text-muted-foreground">等待事件推送…（触发假设/回测/RL 决策后出现）</p>}
            <AnimatePresence initial={false}>
              {events.map((e) => (
                <motion.button key={e.envelope_id}
                  layout
                  initial={{ opacity: 0, height: 0, y: -6 }}
                  animate={{ opacity: 1, height: 'auto', y: 0 }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  whileTap={{ scale: 0.985 }}
                  onClick={() => openLineage(e.lineage_id)}
                  className="w-full flex items-center gap-2 text-left rounded-md border px-2.5 py-1.5 text-xs hover:bg-muted/40 overflow-hidden">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0"
                    style={{ background: (STAGE_COLOR[e.stage] || '#565f89') + '33', color: STAGE_COLOR[e.stage] }}>
                    {STAGE_LABEL[e.stage] || e.stage}
                  </span>
                  <span className="font-mono text-muted-foreground truncate">{e.source}</span>
                  {e.symbol && <span className="text-muted-foreground shrink-0">· {e.symbol}</span>}
                  <span className="ml-auto text-muted-foreground shrink-0">{e.status}</span>
                </motion.button>
              ))}
            </AnimatePresence>
          </div>
        </SectionCard>

        <SectionCard title="血缘回放" description={selected ? `链路 ${selected}` : '点击左侧事件查看完整血缘链路'}>
          <div style={{ width: '100%', height: 360 }} className="rounded-md border bg-[#16161e]">
            {lineageNodes.length > 0 ? (
              <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }}>
                <Background color="#414868" gap={16} />
                <Controls showInteractive={false} />
              </ReactFlow>
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
                暂无血缘数据
              </div>
            )}
          </div>
          {lineages.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {lineages.slice(0, 12).map((l) => (
                <motion.button key={l.lineage_id} onClick={() => openLineage(l.lineage_id)}
                  whileHover={{ scale: 1.06 }} whileTap={{ scale: 0.94 }}
                  className={cn(
                    'text-[11px] rounded border px-2 py-0.5 hover:bg-muted/40 font-mono text-muted-foreground',
                    selected === l.lineage_id && 'border-primary/60 bg-primary/10 text-foreground',
                  )}>
                  {l.latest_stage ? (STAGE_LABEL[l.latest_stage] || l.latest_stage) : ''} · {l.node_count}
                </motion.button>
              ))}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  )
}

export default NexusLivePipeline
