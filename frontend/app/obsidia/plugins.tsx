/**
 * 默认插件集
 * ----------
 * 把知识库（真实 vault）与全部交易页按业务分组注册进 Obsidia 注册表。
 * 页面 key 与现有 hash 路由 / keepAlive 完全一致（保持零改动挂载）。
 */

import {
  Network,
  Workflow,
  BookText,
  BarChart3,
  Coins,
  Brain,
  TestTube,
  LineChart,
  CandlestickChart,
  Server,
  ArrowRightLeft,
  Sparkles,
  FlaskConical,
  BookOpen,
  Globe,
  Settings,
  ShieldAlert,
  PieChart,
  FileText,
  HelpCircle,
  Archive,
  Layers,
} from 'lucide-react'

import { registry } from './core/PluginRegistry'
import type { ObsidiaPlugin } from './core/types'

/** 新增的 Obsidian 原生视图页面 key */
export const VIEW_AGENT_EVOLUTION = 'agent-evolution'
export const VIEW_VAULT_GRAPH = 'vault-graph'
export const VIEW_VAULT_CANVAS = 'vault-canvas'
export const VIEW_VAULT_EXPLORER = 'vault-explorer'

export const OBSIDIA_VIEW_TITLES: Record<string, string> = {
  [VIEW_AGENT_EVOLUTION]: 'Agent 进化中心',
  [VIEW_VAULT_GRAPH]: '关系图谱',
  [VIEW_VAULT_CANVAS]: 'Canvas',
  [VIEW_VAULT_EXPLORER]: '知识库',
}

// ── 知识库插件（真实 vault 可视化）──
const knowledgePlugin: ObsidiaPlugin = {
  id: 'obsidia-knowledge',
  name: '知识库',
  navGroups: [
    {
      id: 'knowledge',
      label: '知识库 · Agent',
      defaultOpen: true,
      items: [
        { page: VIEW_AGENT_EVOLUTION, label: '进化中心', icon: Sparkles },
        { page: VIEW_VAULT_GRAPH, label: '关系图谱', icon: Network },
        { page: VIEW_VAULT_CANVAS, label: 'Canvas 进化图', icon: Workflow },
        { page: VIEW_VAULT_EXPLORER, label: '笔记浏览', icon: BookText },
      ],
    },
  ],
  ribbon: [
    { id: 'rb-evo', page: VIEW_AGENT_EVOLUTION, label: 'Agent 进化中心', icon: Sparkles, zone: 'obsidian' },
    { id: 'rb-graph', page: VIEW_VAULT_GRAPH, label: '关系图谱', icon: Network, zone: 'obsidian' },
    { id: 'rb-canvas', page: VIEW_VAULT_CANVAS, label: 'Canvas', icon: Workflow, zone: 'obsidian' },
    { id: 'rb-notes', page: VIEW_VAULT_EXPLORER, label: '知识库', icon: BookText, zone: 'obsidian' },
  ],
}

// ── 交易主插件 ──
const tradingPlugin: ObsidiaPlugin = {
  id: 'trading-core',
  name: '交易',
  navGroups: [
    {
      id: 'main',
      label: '主要功能',
      defaultOpen: true,
      items: [
        { page: 'comprehensive', label: '仪表盘', icon: BarChart3 },
        { page: 'atas-v2', label: 'AI 策略', icon: Brain },
        { page: 'paper-trading', label: '模拟交易', icon: TestTube },
        { page: 'klines', label: 'K 线图表', icon: LineChart },
        { page: 'llm-billing', label: 'LLM 计费', icon: Coins },
      ],
    },
    {
      id: 'trade-tools',
      label: '交易工具',
      items: [
        { page: 'hyperliquid', label: 'Hyperliquid 交易', icon: CandlestickChart },
      ],
    },
    {
      id: 'exchange-arb',
      label: '交易所 & 套利',
      items: [
        { page: 'exchange-hub', label: '交易所枢纽', icon: Server },
        { page: 'arbitrage-hub', label: '套利中心', icon: ArrowRightLeft },
      ],
    },
  ],
  ribbon: [
    { id: 'rb-dash', page: 'comprehensive', label: '仪表盘', icon: BarChart3, zone: 'trading' },
    { id: 'rb-atas', page: 'atas-v2', label: 'AI 策略', icon: Brain, zone: 'trading' },
    { id: 'rb-kline', page: 'klines', label: 'K 线图表', icon: LineChart, zone: 'trading' },
    { id: 'rb-exhub', page: 'exchange-hub', label: '交易所枢纽', icon: Server, zone: 'trading' },
  ],
}

// ── AI / 因子 / 分析 / 系统 插件 ──
const intelligencePlugin: ObsidiaPlugin = {
  id: 'intelligence',
  name: '智能',
  navGroups: [
    {
      id: 'strategy-config',
      label: '策略配置',
      defaultOpen: true,
      items: [
        { page: 'scalp-config', label: '短线策略配置', icon: Settings },
        { page: 'mid-config', label: '中线策略配置', icon: Layers },
        { page: 'mid-prompt', label: '中线提示词', icon: BookOpen },
        { page: 'long-config', label: '长线策略配置', icon: Layers },
        { page: 'long-prompt', label: '长线提示词', icon: BookOpen },
      ],
    },
    {
      id: 'market-data',
      label: '市场数据',
      items: [
        { page: 'market-intelligence', label: '全市场数据中台', icon: Globe },
        { page: 'unified-factor', label: '因子系统', icon: FlaskConical },
        { page: 'intelligent-learning', label: '进化中枢', icon: Sparkles },
      ],
    },
    {
      id: 'analysis',
      label: '分析',
      items: [
        { page: 'analytics', label: '数据分析', icon: PieChart },
        { page: 'risk', label: '风控监控', icon: ShieldAlert },
      ],
    },
    {
      id: 'system',
      label: '系统管理',
      items: [
        { page: 'settings', label: '设置', icon: Settings },
        { page: 'system-logs', label: '系统日志', icon: FileText },
        { page: 'user-guide', label: '使用指南', icon: HelpCircle },
      ],
    },
    {
      id: 'archived',
      label: '📦 已封存',
      defaultOpen: false,
      items: [
        { page: 'prompt-management', label: '提示词管理(旧)', icon: Archive },
        { page: 'hypothesis', label: '策略假设引擎', icon: Archive },
        { page: 'attribution', label: '归因分析', icon: Archive },
        { page: 'data-center', label: '数据中心', icon: Archive },
        { page: 'data-quality', label: '数据质量', icon: Archive },
        { page: 'modern-signals', label: '信号系统', icon: Archive },
        { page: 'smart-signal-generator', label: '智能信号生成', icon: Archive },
        { page: 'atas-console', label: 'ATAS 控制台', icon: Archive },
        { page: 'market-scanner', label: '市场扫描器', icon: Archive },
        { page: 'fee-monitor', label: '费率监控', icon: Archive },
        { page: 'exchange-config', label: '交易所配置(旧)', icon: Archive },
        { page: 'trader-management', label: 'AI 交易员', icon: Archive },
        { page: VIEW_AGENT_EVOLUTION, label: '进化中心(旧)', icon: Archive },
        { page: VIEW_VAULT_GRAPH, label: '关系图谱', icon: Archive },
        { page: VIEW_VAULT_CANVAS, label: 'Canvas 进化图', icon: Archive },
        { page: VIEW_VAULT_EXPLORER, label: '笔记浏览', icon: Archive },
      ],
    },
  ],
  ribbon: [
    { id: 'rb-learn', page: 'intelligent-learning', label: '进化中枢', icon: Sparkles, zone: 'trading' },
  ],
}

let installed = false

/** 幂等安装默认插件 */
export function installDefaultPlugins(): void {
  if (installed) return
  registry.registerAll([knowledgePlugin, tradingPlugin, intelligencePlugin])
  installed = true
}
