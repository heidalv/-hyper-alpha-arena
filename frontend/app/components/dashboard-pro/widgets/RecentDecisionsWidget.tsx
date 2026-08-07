import RecentDecisions from '@/components/dashboard/RecentDecisions'
import type { WidgetProps } from '../types'

/** 直接复用既有最近 AI 决策组件（内部按全局 TradingModeContext 取数）。 */
export default function RecentDecisionsWidget(_props: WidgetProps) {
  return <RecentDecisions className="h-full" />
}
