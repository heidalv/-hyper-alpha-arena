/**
 * AccountModeSelector — 多选「账户 x 交易所 x 模式」组合
 *
 * 数据源：/api/account/list（沿用现有账户体系，不新增账户模型）。
 * 每个账户按其已启用的能力生成可选组合：
 *   - trading_mode === 'paper'   -> { exchange: 'paper', trading_mode: 'paper' }
 *   - hyperliquid_enabled        -> { exchange: 'hyperliquid', trading_mode: hyperliquid_environment }
 * （backend 聚合器当前只支持这两类；binance 等其它交易所留待聚合器扩展后再开放选择，避免选了却拿不到数据）
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, X, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { apiRequest } from '@/lib/api'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuCheckboxItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { AccountSelection, TradingModeKind } from './types'

interface RawAccount {
  id: number
  name: string
  trading_mode: string
  hyperliquid_environment: string | null
  hyperliquid_enabled: boolean
  selected_exchange: string
}

interface AccountOption {
  key: string
  account_id: number
  account_name: string
  exchange: string
  trading_mode: TradingModeKind
}

const MODE_LABEL: Record<string, string> = { paper: '模拟', testnet: '测试网', mainnet: '实盘' }

function buildOptions(accounts: RawAccount[]): AccountOption[] {
  const options: AccountOption[] = []
  for (const acc of accounts) {
    if (acc.trading_mode === 'paper') {
      options.push({
        key: `${acc.id}:paper:paper`,
        account_id: acc.id,
        account_name: acc.name,
        exchange: 'paper',
        trading_mode: 'paper',
      })
    }
    if (acc.hyperliquid_enabled) {
      const env = (acc.hyperliquid_environment === 'testnet' ? 'testnet' : 'mainnet') as TradingModeKind
      options.push({
        key: `${acc.id}:hyperliquid:${env}`,
        account_id: acc.id,
        account_name: acc.name,
        exchange: 'hyperliquid',
        trading_mode: env,
      })
    }
  }
  return options
}

interface AccountModeSelectorProps {
  value: AccountSelection[]
  onChange: (next: AccountSelection[]) => void
}

export default function AccountModeSelector({ value, onChange }: AccountModeSelectorProps) {
  const [options, setOptions] = useState<AccountOption[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    (async () => {
      setLoading(true)
      try {
        const res = await apiRequest('/account/list')
        const accounts: RawAccount[] = await res.json()
        setOptions(buildOptions(accounts))
      } catch (err) {
        console.warn('[AccountModeSelector] 账户列表加载失败:', err)
      } finally {
        setLoading(false)
      }
    })()
  }, [])

  const selectedKeys = useMemo(
    () => new Set(value.map((s) => `${s.account_id}:${s.exchange}:${s.trading_mode}`)),
    [value],
  )

  const toggle = (opt: AccountOption, checked: boolean) => {
    if (checked) {
      const next: AccountSelection = {
        account_id: opt.account_id,
        exchange: opt.exchange,
        trading_mode: opt.trading_mode,
        label: `${opt.account_name} · ${opt.exchange === 'hyperliquid' ? 'Hyperliquid' : '模拟'} · ${MODE_LABEL[opt.trading_mode]}`,
      }
      onChange([...value, next])
    } else {
      onChange(value.filter((s) => `${s.account_id}:${s.exchange}:${s.trading_mode}` !== opt.key))
    }
  }

  const remove = (key: string) => {
    onChange(value.filter((s) => `${s.account_id}:${s.exchange}:${s.trading_mode}` !== key))
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-8 gap-1.5">
            <Users className="h-3.5 w-3.5" />
            选择账户
            {value.length > 0 && (
              <Badge variant="secondary" className="h-4 text-[10px] px-1">
                {value.length}
              </Badge>
            )}
            <ChevronDown className="h-3 w-3 opacity-60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72 max-h-96 overflow-y-auto">
          <DropdownMenuLabel>账户 x 交易所 x 模式</DropdownMenuLabel>
          <DropdownMenuSeparator />
          {loading && <div className="px-2 py-3 text-xs text-muted-foreground">加载中…</div>}
          {!loading && options.length === 0 && (
            <div className="px-2 py-3 text-xs text-muted-foreground">暂无可用账户</div>
          )}
          {options.map((opt) => (
            <DropdownMenuCheckboxItem
              key={opt.key}
              checked={selectedKeys.has(opt.key)}
              onCheckedChange={(checked) => toggle(opt, checked === true)}
              onSelect={(e) => e.preventDefault()}
            >
              <span className="truncate">{opt.account_name}</span>
              <Badge
                variant="outline"
                className={cn(
                  'ml-auto text-[9px] h-4 px-1',
                  opt.trading_mode === 'mainnet' && 'border-emerald-500/40 text-emerald-400',
                  opt.trading_mode === 'testnet' && 'border-amber-500/40 text-amber-400',
                  opt.trading_mode === 'paper' && 'border-sky-500/40 text-sky-400',
                )}
              >
                {opt.exchange === 'hyperliquid' ? 'HL' : '模拟'} · {MODE_LABEL[opt.trading_mode]}
              </Badge>
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <div className="flex items-center gap-1.5 flex-wrap">
        {value.map((s) => (
          <Badge
            key={`${s.account_id}:${s.exchange}:${s.trading_mode}`}
            variant="secondary"
            className="h-6 gap-1 pr-1 text-[11px]"
          >
            {s.label}
            <button
              onClick={() => remove(`${s.account_id}:${s.exchange}:${s.trading_mode}`)}
              className="hover:text-red-400 ml-0.5"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          </Badge>
        ))}
      </div>
    </div>
  )
}
