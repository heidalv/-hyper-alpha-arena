/**
 * KpiTile — 套利中心通用 KPI 小卡片（从 ArbitrageHubPage 拆出共享）
 */
import { cn } from '@/lib/utils'

export default function KpiTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: string
  sub: string
  tone: 'blue' | 'purple' | 'green' | 'amber' | 'red'
}) {
  const toneClass = {
    blue: 'border-blue-500/20 bg-blue-500/5 text-blue-600 dark:text-blue-400',
    purple: 'border-purple-500/20 bg-purple-500/5 text-purple-600 dark:text-purple-400',
    green: 'border-green-500/20 bg-green-500/5 text-green-600 dark:text-green-400',
    amber: 'border-amber-500/20 bg-amber-500/5 text-amber-600 dark:text-amber-400',
    red: 'border-red-500/20 bg-red-500/5 text-red-600 dark:text-red-400',
  }[tone]

  return (
    <div className={cn('rounded-xl border p-3', toneClass)}>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-lg font-bold mt-0.5">{value}</div>
      <div className="text-[11px] text-muted-foreground truncate">{sub}</div>
    </div>
  )
}
