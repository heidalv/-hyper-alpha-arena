/**
 * 轻量力导向布局（无第三方依赖）
 * 斥力(所有点对) + 弹簧(边) + 向心力，迭代若干轮得到稳定坐标。
 * 节点规模控制在数百以内即可流畅。
 */

export interface FLNode { id: string; x: number; y: number; vx?: number; vy?: number }
export interface FLEdge { source: string; target: string }

export function computeLayout(
  nodeIds: string[],
  edges: FLEdge[],
  opts: { iterations?: number; width?: number; height?: number; repulsion?: number; spring?: number; springLen?: number } = {},
): Map<string, { x: number; y: number }> {
  const iterations = opts.iterations ?? 160
  const width = opts.width ?? 1200
  const height = opts.height ?? 800
  const repulsion = opts.repulsion ?? 9000
  const spring = opts.spring ?? 0.02
  const springLen = opts.springLen ?? 90

  const cx = width / 2
  const cy = height / 2

  // 初始化：环形分布，避免完全重叠
  const nodes: FLNode[] = nodeIds.map((id, i) => {
    const angle = (i / Math.max(1, nodeIds.length)) * Math.PI * 2
    const r = 40 + Math.random() * Math.min(width, height) * 0.4
    return { id, x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r, vx: 0, vy: 0 }
  })
  const index = new Map(nodes.map((n) => [n.id, n]))

  const validEdges = edges.filter((e) => index.has(e.source) && index.has(e.target))

  for (let iter = 0; iter < iterations; iter++) {
    const cooling = 1 - iter / iterations
    // 斥力
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]
      let fx = 0
      let fy = 0
      for (let j = 0; j < nodes.length; j++) {
        if (i === j) continue
        const b = nodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let dist2 = dx * dx + dy * dy
        if (dist2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; dist2 = 0.01 }
        const force = repulsion / dist2
        const dist = Math.sqrt(dist2)
        fx += (dx / dist) * force
        fy += (dy / dist) * force
      }
      // 向心力
      fx += (cx - a.x) * 0.008
      fy += (cy - a.y) * 0.008
      a.vx = fx
      a.vy = fy
    }
    // 弹簧
    for (const e of validEdges) {
      const a = index.get(e.source)!
      const b = index.get(e.target)!
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
      const f = spring * (dist - springLen)
      const fx = (dx / dist) * f
      const fy = (dy / dist) * f
      a.vx! += fx; a.vy! += fy
      b.vx! -= fx; b.vy! -= fy
    }
    // 位移（限速 + 冷却）
    const maxStep = 30 * cooling + 2
    for (const n of nodes) {
      let vx = n.vx! * 0.85
      let vy = n.vy! * 0.85
      const sp = Math.sqrt(vx * vx + vy * vy)
      if (sp > maxStep) { vx = (vx / sp) * maxStep; vy = (vy / sp) * maxStep }
      n.x += vx
      n.y += vy
    }
  }

  const result = new Map<string, { x: number; y: number }>()
  nodes.forEach((n) => result.set(n.id, { x: n.x, y: n.y }))
  return result
}
