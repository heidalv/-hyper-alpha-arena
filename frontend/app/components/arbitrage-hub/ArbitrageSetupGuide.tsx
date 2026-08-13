import React from 'react'
import { ArrowRight, Bot, Layers, Wallet } from 'lucide-react'
import CollapsibleHelpPanel from './CollapsibleHelpPanel'

type GuideVariant = 'full' | 'paper' | 'trader' | 'start'

const TITLES: Record<GuideVariant, string> = {
  paper: '套利模拟账户 · 它管什么',
  trader: 'AI 交易员 · 两套配置别混',
  start: '启动前 · 三处配置如何配合',
  full: '套利体系 · 模拟账户 + AI 交易员 + 启动配置',
}

const SUMMARIES: Record<GuideVariant, string> = {
  paper: '套利专用资金池，与 AI 策略 Paper 分开；只管钱、分账、绑定。',
  trader: '「AI 配置」管方向交易；「专用套利」管 S1–S8，资金和模型各走各的。',
  start: '模拟账户分账 → 专用套利配交易员 → 绑定 → 选策略启动。',
  full: '三套体系：方向交易 / 套利 Paper / 专用套利档案，勿混用。',
}

/**
 * 三套体系对照：AI 方向交易 / 套利 Paper 资金 / 专用套利交易员
 */
export default function ArbitrageSetupGuide({
  variant = 'full',
  defaultOpen = false,
  embedded = false,
}: {
  variant?: GuideVariant
  defaultOpen?: boolean
  embedded?: boolean
}) {
  const systems = [
    {
      icon: Bot,
      name: 'AI 交易员（方向交易）',
      where: 'AI 交易员管理 → AI 配置 / 钱包 / 风控',
      funds: '旧 AI Paper 资金池 或 实盘钱包',
      llm: '分析模型 + 执行模型（只配一次）',
      forWhat: 'BTC/ETH 等方向性 AI 策略、全自动会话',
      notFor: '不用于 S1–S8 积分套利 Paper 验证',
    },
    {
      icon: Wallet,
      name: '套利模拟账户',
      where: '套利中心 → 模拟账户',
      funds: '套利专用 Paper（独立总账 + 各交易所分账）',
      llm: '不涉及模型，只管钱',
      forWhat: '300U 分账、积分/仓位/流水、绑定交易员',
      notFor: '不是 AI 策略那个 Paper，不能混用',
    },
    {
      icon: Layers,
      name: '专用套利（交易员档案）',
      where: 'AI 交易员管理 → 专用套利',
      funds: '指向上面「套利模拟账户」',
      llm: '分析/执行模型统一用 deepseek-v4-flash',
      forWhat: '授权 S3/S8、证明是套利用交易员、绑定 Paper',
      notFor: '不是两套重复配置；模型请选 deepseek-v4-flash',
    },
  ]

  const flow = [
    '套利中心·模拟账户 → 创建 300U 并分账',
    'AI 交易员·专用套利 → 开启 + 选 Paper + 双模型 + 勾选策略',
    '模拟账户 或 启动配置 → 确认绑定交易员',
    '启动配置 → 选策略 → 检查 → 启动',
  ]

  const showSystems = variant === 'full' || variant === 'paper' || variant === 'trader'
  const showFlow = variant === 'full' || variant === 'start' || variant === 'paper' || variant === 'trader'

  const body = (
    <div className="space-y-4 text-sm">
      {variant === 'paper' && (
        <p className="text-xs text-muted-foreground">
          这里的账户是<strong className="text-foreground">套利专用资金池</strong>，和「AI 交易员 → AI 配置」里绑定的旧 Paper 不是同一个东西。
          只管钱、分账、绑定；策略和模型在「专用套利」里配。
        </p>
      )}

      {variant === 'trader' && (
        <div className="text-xs text-muted-foreground space-y-2">
          <p>
            <strong className="text-foreground">「AI 配置」Tab</strong>：方向性交易（选币、多空、自动交易）→ 用快速/深度模型 + 旧 AI Paper 或实盘。
          </p>
          <p>
            <strong className="text-foreground">「专用套利」Tab</strong>：S1–S8 积分套利 → 必须另开专用套利开关，绑定<strong className="text-foreground">套利中心·模拟账户</strong>；分析模型用<strong className="text-foreground">深度/reasoner（流式）</strong>，执行模型用<strong className="text-foreground">快速模型</strong>。
          </p>
          <p className="text-amber-700 dark:text-amber-300">
            同一个交易员可以同时有两套身份，但资金和模型各走各的，不要混选 Paper 账户类型。
          </p>
        </div>
      )}

      {showSystems && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          {systems.map(sys => {
            const Icon = sys.icon
            const dim =
              (variant === 'paper' && sys.name.includes('模拟')) ||
              (variant === 'trader' && sys.name.includes('专用套利')) ||
              variant === 'full'
            if (!dim && variant !== 'full') return null
            const highlight =
              (variant === 'paper' && sys.name.includes('模拟')) ||
              (variant === 'trader' && (sys.name.includes('专用套利') || sys.name.includes('AI 交易员')))
            return (
              <div
                key={sys.name}
                className={`rounded-lg border p-3 text-xs space-y-1.5 ${
                  highlight ? 'border-blue-500/40 bg-blue-500/5' : 'border-border/60 bg-card/50 opacity-90'
                }`}
              >
                <div className="font-medium flex items-center gap-1.5">
                  <Icon className="w-3.5 h-3.5" /> {sys.name}
                </div>
                <div><span className="text-muted-foreground">入口：</span>{sys.where}</div>
                <div><span className="text-muted-foreground">资金：</span>{sys.funds}</div>
                <div><span className="text-muted-foreground">模型：</span>{sys.llm}</div>
                <div><span className="text-muted-foreground">用途：</span>{sys.forWhat}</div>
                <div className="text-red-600/80 dark:text-red-400/80">✕ {sys.notFor}</div>
              </div>
            )
          })}
        </div>
      )}

      {showFlow && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground pt-1 border-t border-border/50">
          <span className="font-medium text-foreground mr-1">推荐顺序：</span>
          {flow.map((step, i) => (
            <React.Fragment key={step}>
              {i > 0 && <ArrowRight className="w-3 h-3 shrink-0" />}
              <span>{step}</span>
            </React.Fragment>
          ))}
        </div>
      )}

      {variant === 'paper' && (
        <div className="text-xs rounded-lg border border-amber-500/25 bg-amber-500/5 p-2.5 text-amber-800 dark:text-amber-200">
          本页负责：创建账户、调整各交易所配额、绑定交易员、看 Dashboard。
          启动 Paper 验证请去「启动配置」Tab。
        </div>
      )}

      {variant === 'trader' && (
        <div className="text-xs rounded-lg border border-amber-500/25 bg-amber-500/5 p-2.5 text-amber-800 dark:text-amber-200">
          保存专用套利后，若尚未绑定：到「套利中心 → 模拟账户」或「启动配置」点「确认绑定」。
          Paper 账户模式请选「套利专用 Paper 账户」，不要选旧 AI Paper 资金池（除非兼容旧 FullAuto）。
        </div>
      )}
    </div>
  )

  if (embedded) return body

  return (
    <CollapsibleHelpPanel
      title={TITLES[variant]}
      summary={SUMMARIES[variant]}
      defaultOpen={defaultOpen}
    >
      {body}
    </CollapsibleHelpPanel>
  )
}
