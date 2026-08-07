import { ArrowRight } from 'lucide-react'
import CollapsibleHelpPanel from './CollapsibleHelpPanel'

/**
 * 套利启动配置 — 四层职责说明（避免多入口重复配置的困惑）
 */
export default function ArbitrageConfigMap({
  defaultOpen = false,
  embedded = false,
}: {
  defaultOpen?: boolean
  embedded?: boolean
}) {
  const rows = [
    {
      layer: '① 资金层',
      what: '套利 Paper 账户 + 各交易所分账',
      where: '套利中心 → 模拟账户',
      note: '与 AI 策略模拟盘完全分开，专用资金池',
    },
    {
      layer: '② 交易员层',
      what: '开启专用套利 · 授权策略 · 双模型 · 绑定 Paper',
      where: '套利中心 → 交易员套利',
      note: '证明该交易员是套利用的；S8 需策略模型+执行模型分开',
    },
    {
      layer: '③ 绑定层',
      what: 'Paper 账户 ↔ 专用套利交易员 显式关联',
      where: '启动配置 Step2 或 模拟账户页',
      note: '两处入口写同一关系，任选其一完成即可',
    },
    {
      layer: '④ 运行层',
      what: '选本次运行的策略子集 → 检查 → 启动/停止',
      where: '套利中心 → 启动配置',
      note: '所选策略必须是 ② 中已授权策略的子集',
    },
  ]

  const body = (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-muted-foreground">
        系统分四层，不要混用「AI 策略全自动」或「Rebate 全局 Config API」来启动套利 Paper——那些是别的入口。
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border/60">
              <th className="py-2 pr-3 font-medium">层级</th>
              <th className="py-2 pr-3 font-medium">配置内容</th>
              <th className="py-2 pr-3 font-medium">在哪里操作</th>
              <th className="py-2 font-medium">要点</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.layer} className="border-b border-border/40 align-top">
                <td className="py-2.5 pr-3 font-medium whitespace-nowrap">{row.layer}</td>
                <td className="py-2.5 pr-3">{row.what}</td>
                <td className="py-2.5 pr-3 text-blue-700 dark:text-blue-300 whitespace-nowrap">{row.where}</td>
                <td className="py-2.5 text-muted-foreground">{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground pt-1">
        <span className="font-medium text-foreground">推荐顺序：</span>
        <span>模拟账户建账分账</span>
        <ArrowRight className="w-3 h-3" />
        <span>专用套利配交易员</span>
        <ArrowRight className="w-3 h-3" />
        <span>启动配置绑定</span>
        <ArrowRight className="w-3 h-3" />
        <span>选策略 → 检查 → 启动</span>
      </div>
    </div>
  )

  if (embedded) return body

  return (
    <CollapsibleHelpPanel
      title="配置逻辑一览（谁管什么）"
      summary="资金层 → 交易员层 → 绑定层 → 运行层，四层分工。"
      defaultOpen={defaultOpen}
    >
      {body}
    </CollapsibleHelpPanel>
  )
}
