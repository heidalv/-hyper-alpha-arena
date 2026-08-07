/**
 * WidgetRegistry — 交易矩阵仪表盘组件库元数据表
 *
 * 供"添加组件"面板渲染选项，以及 GridCanvas 按 widget.type 解析出实际渲染组件。
 */
import { type ComponentType } from 'react'
import {
  Wallet,
  TrendingUp,
  Percent,
  Layers,
  Table2,
  LineChart as LineChartIcon,
  Users,
  Brain,
  MessageSquareText,
  Activity,
} from 'lucide-react'
import type { WidgetProps } from './types'

import {
  EquityCardWidget,
  PnlCardWidget,
  WinRateCardWidget,
  ActivePositionsCardWidget,
} from './widgets/StatCards'
import PositionsTableWidget from './widgets/PositionsTableWidget'
import StrategyOverviewWidget from './widgets/StrategyOverviewWidget'
import RecentDecisionsWidget from './widgets/RecentDecisionsWidget'
import AccountCompareWidget from './widgets/AccountCompareWidget'
import AssetCurveChart from './widgets/AssetCurveChart'
import IndicatorChartWidget from './widgets/IndicatorChartWidget'

export interface WidgetDef {
  type: string
  label: string
  description: string
  icon: ComponentType<{ className?: string }>
  defaultSize: { w: number; h: number }
  minSize?: { w: number; h: number }
  component: ComponentType<WidgetProps>
}

export const WIDGET_REGISTRY: WidgetDef[] = [
  {
    type: 'equity_card',
    label: '总权益',
    description: '所选账户权益合计',
    icon: Wallet,
    defaultSize: { w: 3, h: 3 },
    minSize: { w: 2, h: 2 },
    component: EquityCardWidget,
  },
  {
    type: 'pnl_card',
    label: '总盈亏',
    description: '所选账户盈亏合计',
    icon: TrendingUp,
    defaultSize: { w: 3, h: 3 },
    minSize: { w: 2, h: 2 },
    component: PnlCardWidget,
  },
  {
    type: 'winrate_card',
    label: '胜率',
    description: '加权平均胜率',
    icon: Percent,
    defaultSize: { w: 3, h: 3 },
    minSize: { w: 2, h: 2 },
    component: WinRateCardWidget,
  },
  {
    type: 'positions_card',
    label: '持仓中',
    description: '活跃持仓数量',
    icon: Layers,
    defaultSize: { w: 3, h: 3 },
    minSize: { w: 2, h: 2 },
    component: ActivePositionsCardWidget,
  },
  {
    type: 'account_compare',
    label: '账户对比',
    description: '多账户 x 交易所 x 模式并排对比',
    icon: Users,
    defaultSize: { w: 12, h: 5 },
    minSize: { w: 4, h: 3 },
    component: AccountCompareWidget,
  },
  {
    type: 'asset_curve',
    label: '权益曲线',
    description: '多账户权益曲线叠加（实时累积）',
    icon: LineChartIcon,
    defaultSize: { w: 8, h: 7 },
    minSize: { w: 4, h: 4 },
    component: AssetCurveChart,
  },
  {
    type: 'positions_table',
    label: '持仓明细',
    description: '所有已选账户的持仓列表',
    icon: Table2,
    defaultSize: { w: 6, h: 7 },
    minSize: { w: 3, h: 3 },
    component: PositionsTableWidget,
  },
  {
    type: 'indicator_chart',
    label: '指标图表',
    description: '接入因子库，自选指标叠加走势',
    icon: Activity,
    defaultSize: { w: 6, h: 7 },
    minSize: { w: 4, h: 4 },
    component: IndicatorChartWidget,
  },
  {
    type: 'strategy_overview',
    label: '策略表现',
    description: '全自动策略运行概览',
    icon: Brain,
    defaultSize: { w: 4, h: 6 },
    minSize: { w: 3, h: 3 },
    component: StrategyOverviewWidget,
  },
  {
    type: 'recent_decisions',
    label: '最近 AI 决策',
    description: '模型最新交易决策流',
    icon: MessageSquareText,
    defaultSize: { w: 4, h: 6 },
    minSize: { w: 3, h: 3 },
    component: RecentDecisionsWidget,
  },
]

export const WIDGET_MAP: Record<string, WidgetDef> = Object.fromEntries(
  WIDGET_REGISTRY.map((w) => [w.type, w]),
)

export function getWidgetDef(type: string): WidgetDef | undefined {
  return WIDGET_MAP[type]
}

/** 新建 widget 实例时用于分配默认布局位置的简单堆叠算法（从左到右、超出后换行）。 */
export function nextWidgetPosition(existing: { x: number; y: number; w: number; h: number }[]): {
  x: number
  y: number
} {
  if (existing.length === 0) return { x: 0, y: 0 }
  const maxY = Math.max(...existing.map((w) => w.y + w.h))
  return { x: 0, y: maxY }
}
