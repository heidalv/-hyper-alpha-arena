import StrategyOverview from '@/components/dashboard/StrategyOverview'
import type { WidgetProps } from '../types'

/** 直接复用既有全自动策略概览组件（数据自取，不依赖 overview 聚合）。 */
export default function StrategyOverviewWidget(_props: WidgetProps) {
  return <StrategyOverview className="h-full" />
}
