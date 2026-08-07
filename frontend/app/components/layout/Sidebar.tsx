/**
 * Sidebar — 侧边栏导航
 * 合并原 Win95MenuBar 所有功能，分 4 组展示
 * 注意：使用 <div> 而非 <button> 以避免 Win95 CSS !important 覆盖
 * HMR: 修改本文件保存后应自动刷新侧边栏（无需重启 Vite）
 */

import {
  BarChart3,
  Brain,
  FlaskConical,
  ShieldAlert,
  LineChart,
  Activity,
  Bot,
  Database,
  Sparkles,
  BookOpen,
  Gauge,
  Target,
  TrendingDown,
  Search,
  HelpCircle,
  Server,
  Zap,
  DollarSign,
  ArrowRightLeft,
  FileText,
  Settings,
  TestTube,
  Coins,
} from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import ThemeToggle from '@/components/layout/ThemeToggle'

interface NavItem {
  label: string
  page: string
  icon: any
}

interface SidebarProps {
  currentPage?: string
  onPageChange?: (page: string) => void
  collapsed?: boolean
  onToggleCollapse?: () => void
}

// ── 6 组导航（重新归类） ──

const mainNav: NavItem[] = [
  { label: '仪表盘', page: 'comprehensive', icon: BarChart3 },
  { label: 'LLM 计费统计', page: 'llm-billing', icon: Coins },
  { label: 'AI 策略', page: 'atas-v2', icon: Brain },
  { label: '模拟交易', page: 'paper-trading', icon: TestTube },
  { label: 'K线图表', page: 'klines', icon: LineChart },
]

const tradingNav: NavItem[] = [
  { label: '信号系统', page: 'modern-signals', icon: Activity },
  { label: '市场扫描器', page: 'market-scanner', icon: Search },
  { label: '智能信号生成', page: 'smart-signal-generator', icon: Zap },
]

const exchangeArbNav: NavItem[] = [
  { label: '交易所枢纽', page: 'exchange-hub', icon: Server },
  { label: '套利中心', page: 'arbitrage-hub', icon: ArrowRightLeft },
  { label: '交易所配置', page: 'exchange-config', icon: Gauge },
  { label: 'AI 交易员', page: 'trader-management', icon: Bot },
  { label: '费率监控', page: 'fee-monitor', icon: DollarSign },
]

const aiNav: NavItem[] = [
  { label: 'Hermes 进化', page: 'hermes-evolution', icon: Bot },
  { label: '智能学习中心', page: 'intelligent-learning', icon: Sparkles },
  { label: '因子系统', page: 'unified-factor', icon: FlaskConical },
  { label: '提示词管理', page: 'prompt-management', icon: BookOpen },
  { label: '策略假设引擎', page: 'hypothesis', icon: Target },
  { label: '归因分析', page: 'attribution', icon: TrendingDown },
  { label: '数据中心', page: 'data-center', icon: Database },
  { label: '风控监控', page: 'risk', icon: ShieldAlert },
]

const analysisNav: NavItem[] = [
  { label: '数据分析', page: 'analytics', icon: BarChart3 },
  { label: '数据质量', page: 'data-quality', icon: Database },
]

const systemNav: NavItem[] = [
  { label: '系统日志', page: 'system-logs', icon: FileText },
  { label: '使用指南', page: 'user-guide', icon: HelpCircle },
  { label: '设置', page: 'settings', icon: Settings },
]

export default function Sidebar({
  currentPage = 'comprehensive',
  onPageChange,
  collapsed = false,
  onToggleCollapse,
}: SidebarProps) {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'

  const colors = {
    bg: isDark ? '#111827' : '#FFFFFF',
    border: isDark ? '#374151' : '#D0D0D0',
    groupLabel: isDark ? '#9CA3AF' : '#808080',
    text: isDark ? '#E5E7EB' : '#333',
    textActive: isDark ? '#C4B5FD' : '#000080',
    itemBgActive: isDark ? '#1F2937' : '#D0D0D0',
    itemBgHover: isDark ? '#1F2937' : '#E8E8E8',
    logo: isDark ? '#A78BFA' : '#000080',
  }

  const renderGroup = (title: string, items: NavItem[]) => (
    <div>
      {!collapsed && (
        <div style={{ padding: '4px 12px', fontSize: '11px', color: colors.groupLabel, fontWeight: 600, letterSpacing: '0.5px', marginBottom: '2px' }}>
          {title}
        </div>
      )}
      <div>
        {items.map((item) => {
          const isActive = currentPage === item.page ||
            (currentPage === 'comprehensive' && item.page === 'comprehensive') ||
            (currentPage === 'signal-management' && item.page === 'modern-signals')

          const Icon = item.icon

          return (
            <div
              key={item.page}
              onClick={() => onPageChange?.(item.page)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: collapsed ? '8px 0' : '6px 12px',
                justifyContent: collapsed ? 'center' : 'flex-start',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: isActive ? 700 : 400,
                color: isActive ? colors.textActive : colors.text,
                backgroundColor: isActive ? colors.itemBgActive : 'transparent',
                borderLeft: isActive ? `3px solid ${colors.textActive}` : '3px solid transparent',
                transition: 'background-color 0.15s, color 0.15s',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLDivElement).style.backgroundColor = colors.itemBgHover
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLDivElement).style.backgroundColor = 'transparent'
                }
              }}
            >
              <Icon style={{ width: '16px', height: '16px', flexShrink: 0 }} />
              {!collapsed && <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.label}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )

  return (
    <aside
      style={{
        position: 'fixed',
        left: 0,
        top: 0,
        bottom: 0,
        width: collapsed ? '48px' : '160px',
        zIndex: 40,
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: colors.bg,
        borderRight: `1px solid ${colors.border}`,
        transition: 'width 0.2s',
        overflow: 'hidden',
      }}
    >
      {/* Logo / Toggle */}
      <div
        onClick={onToggleCollapse}
        style={{
          height: '32px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed ? 'center' : 'space-between',
          padding: collapsed ? '0' : '0 12px',
          borderBottom: `1px solid ${colors.border}`,
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 700,
          color: colors.logo,
          userSelect: 'none',
        }}
      >
        {!collapsed && <span>Alpha Arena</span>}
        <span style={{ fontSize: '11px', color: '#808080' }}>
          {collapsed ? '▶' : '◀'}
        </span>
      </div>

      {/* Navigation groups */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '8px 0' }}>
        {renderGroup('主要功能', mainNav)}
        <div style={{ height: '8px' }} />
        {renderGroup('交易工具', tradingNav)}
        <div style={{ height: '8px' }} />
        {renderGroup('交易所 & 套利', exchangeArbNav)}
        <div style={{ height: '8px' }} />
        {renderGroup('AI & 因子', aiNav)}
        <div style={{ height: '8px' }} />
        {renderGroup('分析', analysisNav)}
        <div style={{ height: '8px' }} />
        {renderGroup('系统', systemNav)}
      </div>

      <ThemeToggle collapsed={collapsed} isDark={isDark} />
    </aside>
  )
}
