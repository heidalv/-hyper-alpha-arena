import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import type { ArbitrageStartValidation } from '@/lib/arbitrageApi'

const MODE_LABEL: Record<string, string> = {
  hedge: '双所对冲',
  directional: '方向合约',
  maker_roundtrip: 'Maker 开平',
  volume_program: '刷量/活动',
  monitor_only: '仅监控',
}

const DIRECTION_LABEL: Record<string, string> = {
  fixed_hedge: '固定对冲腿',
  fixed_roundtrip: '固定开平',
  ai_signal: 'AI 信号定多空',
  funding_rate: '资金费率定多空',
  volume_target: 'VIP 成交量目标',
  campaign_rules: '活动规则',
  none: '无',
}

export default function StartReadinessChecklist({
  validation,
}: {
  validation: ArbitrageStartValidation | null
}) {
  if (!validation) {
    return (
      <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        选择账户和策略后，点击「启动前检查」。每种策略运行方式不同：对冲、Maker 刷积分、方向合约、活动/VIP 等；未接入引擎的策略会被直接拦截。
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="font-semibold">启动前检查</div>
        <div className={validation.passed ? 'text-green-600' : 'text-red-600'}>
          {validation.passed ? '通过' : '未通过'}
        </div>
      </div>

      {validation.strategy_runtime && validation.strategy_runtime.length > 0 && (
        <div className="rounded-lg border border-border/60 overflow-hidden">
          <div className="px-3 py-2 bg-muted/40 text-xs font-semibold">所选策略如何运行</div>
          <div className="divide-y divide-border/50">
            {validation.strategy_runtime.map(row => (
              <div key={row.strategy_id} className="px-3 py-2.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold">{row.strategy_id} · {row.name}</span>
                  <span className={row.passed ? 'text-green-600' : 'text-red-600'}>
                    {row.passed ? '可运行' : '不可运行'}
                  </span>
                </div>
                <div className="text-muted-foreground mt-1">
                  {MODE_LABEL[row.execution_mode] || row.execution_mode}
                  {' · '}
                  决策: {DIRECTION_LABEL[row.direction_rule] || row.direction_rule}
                  {' · '}
                  {row.summary}
                </div>
                {!row.passed && row.message && (
                  <div className="text-red-600 mt-1">{row.message}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {validation.checks.map(check => (
          <div key={check.name} className="flex items-start gap-2 rounded-lg bg-muted/30 p-3 text-sm">
            {check.passed ? (
              <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5 shrink-0" />
            ) : (
              <XCircle className="w-4 h-4 text-red-600 mt-0.5 shrink-0" />
            )}
            <div>
              <div className="font-medium">{check.name}</div>
              <div className="text-muted-foreground">{check.message}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          S8（Stage6 Maker 优先）由<strong className="font-semibold">策略分析模型</strong>选币定方向、
          <strong className="font-semibold">执行规划模型</strong>定仓位；S3 Maker 开平；
          S1/S5 已下线；S6 已关闭；S2/S4 未接入自动执行；S7 仅监控。
          须在本页<strong className="font-semibold">显式绑定</strong>已在「AI 交易员 → 专用套利」开启专用套利、
          并配置双模型的交易员。
        </span>
      </div>
    </div>
  )
}
