/**
 * 交易矩阵仪表盘前端客户端 — 对接 /api/dashboard/*
 */

import { apiRequest } from './api'
import type {
  AccountOverview,
  AccountSelection,
  DashboardLayoutDTO,
  WidgetInstance,
} from '@/components/dashboard-pro/types'

export async function fetchAccountsOverview(
  selections: AccountSelection[],
): Promise<AccountOverview[]> {
  if (selections.length === 0) return []
  const res = await apiRequest('/dashboard/overview', {
    method: 'POST',
    body: JSON.stringify({
      selections: selections.map((s) => ({
        account_id: s.account_id,
        exchange: s.exchange,
        trading_mode: s.trading_mode,
      })),
    }),
  })
  const data = await res.json()
  return data.accounts ?? []
}

export async function listLayouts(): Promise<DashboardLayoutDTO[]> {
  const res = await apiRequest('/dashboard/layouts')
  const data = await res.json()
  return data.layouts ?? []
}

export async function getActiveLayout(): Promise<DashboardLayoutDTO | null> {
  const res = await apiRequest('/dashboard/layouts/active')
  const data = await res.json()
  return data.layout ?? null
}

export async function createLayout(params: {
  name: string
  widgets: WidgetInstance[]
  selected_accounts: AccountSelection[]
  activate?: boolean
}): Promise<DashboardLayoutDTO> {
  const res = await apiRequest('/dashboard/layouts', {
    method: 'POST',
    body: JSON.stringify(params),
  })
  const data = await res.json()
  return data.layout
}

export async function updateLayout(
  id: number,
  params: Partial<{
    name: string
    widgets: WidgetInstance[]
    selected_accounts: AccountSelection[]
  }>,
): Promise<DashboardLayoutDTO> {
  const res = await apiRequest(`/dashboard/layouts/${id}`, {
    method: 'PUT',
    body: JSON.stringify(params),
  })
  const data = await res.json()
  return data.layout
}

export async function deleteLayout(id: number): Promise<void> {
  await apiRequest(`/dashboard/layouts/${id}`, { method: 'DELETE' })
}

export async function activateLayout(id: number): Promise<DashboardLayoutDTO> {
  const res = await apiRequest(`/dashboard/layouts/${id}/activate`, { method: 'POST' })
  const data = await res.json()
  return data.layout
}
