import { apiRequest } from './client'
import type { Account } from './types'

export interface AccountListItem {
  id: number
  name: string
  account_type: string
  trading_mode: string
  is_active: string
  initial_capital?: number
  current_cash?: number
  model?: string
  base_url?: string
  auto_trading_enabled?: string
  llm_config_id?: number
}

export const listAccounts = (tradingMode?: string) =>
  apiRequest<AccountListItem[]>(`/account/list${tradingMode ? `?trading_mode=${tradingMode}` : ''}`)

export const createAccount = (data: {
  name: string
  account_type?: string
  trading_mode?: string
  initial_capital?: number
  model?: string
  llm_config_id?: number
  auto_trading_enabled?: boolean
}) =>
  apiRequest<Account>(`/account/`, {
    method: 'POST',
    body: JSON.stringify(data),
  })

export const getAccountDetail = (accountId: number) =>
  apiRequest<any>(`/account/${accountId}/overview`)

export const deleteAccount = (accountId: number) =>
  apiRequest<{ message: string }>(`/account/${accountId}`, { method: 'DELETE' })
