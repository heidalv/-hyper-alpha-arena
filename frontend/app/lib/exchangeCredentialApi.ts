/**
 * Exchange Credential Management API
 */

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/exchange`;

export interface SupportedExchange {
  id: string
  name: string
  supports_spot: boolean
  supports_futures: boolean
  needs_passphrase: boolean
}

export interface ExchangeCredential {
  id: number
  account_id: number
  exchange: string
  label: string
  testnet: boolean
  enabled: boolean
  has_key: boolean
  has_secret: boolean
  has_passphrase: boolean
  created_at: string | null
}

export interface ExchangeStatus {
  exchange: string
  connected: boolean
  supports_spot?: boolean
  supports_futures?: boolean
  total_equity?: number | null
  available_balance?: number | null
  error?: string
}

export interface FundingRateComparison {
  symbol: string
  spread: number
  [exchange: string]: string | number | null | undefined
}

export interface CrossArbSpread {
  symbol: string
  exchange_a: string
  exchange_b: string
  price_a: number
  price_b: number
  spread_pct: number
  direction: string
}

export async function getSupportedExchanges(): Promise<SupportedExchange[]> {
  const res = await fetch(`${BASE}/supported`)
  if (!res.ok) return []
  return res.json()
}

export async function getCredentials(accountId = 0): Promise<ExchangeCredential[]> {
  const url = accountId > 0 ? `${BASE}/credentials?account_id=${accountId}` : `${BASE}/credentials`
  const res = await fetch(url)
  if (!res.ok) return []
  return res.json()
}

export async function saveCredential(data: {
  account_id?: number
  exchange: string
  label?: string
  api_key: string
  api_secret: string
  passphrase?: string
  testnet?: boolean
  enabled?: boolean
}): Promise<{ id: number; status: string }> {
  const res = await fetch(`${BASE}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      account_id: data.account_id ?? 1,
      exchange: data.exchange,
      label: data.label ?? '',
      api_key: data.api_key,
      api_secret: data.api_secret,
      passphrase: data.passphrase ?? '',
      testnet: data.testnet ?? true,
      enabled: data.enabled ?? true,
    }),
  })
  return res.json()
}

export async function deleteCredential(credId: number): Promise<void> {
  await fetch(`${BASE}/credentials/${credId}`, { method: 'DELETE' })
}

export async function testCredential(credId: number): Promise<ExchangeStatus> {
  const res = await fetch(`${BASE}/credentials/${credId}/test`, { method: 'POST' })
  return res.json()
}

export async function getExchangeStatuses(): Promise<ExchangeStatus[]> {
  const res = await fetch(`${BASE}/statuses`)
  if (!res.ok) return []
  return res.json()
}

export async function getCrossArbSpreads(symbols = 'BTC/USDT:USDT,ETH/USDT:USDT'): Promise<{
  spreads: CrossArbSpread[]
  timestamp: number
}> {
  const res = await fetch(`${BASE}/cross-arb/spreads?symbols=${encodeURIComponent(symbols)}`)
  if (!res.ok) return { spreads: [], timestamp: 0 }
  return res.json()
}

export async function getCrossArbFundingRates(symbols = ''): Promise<{
  exchanges: string[]
  comparison: FundingRateComparison[]
  timestamp: number
}> {
  const res = await fetch(`${BASE}/cross-arb/funding-rates?symbols=${encodeURIComponent(symbols)}`)
  if (!res.ok) return { exchanges: [], comparison: [], timestamp: 0 }
  return res.json()
}

export async function getCrossArbExposure(): Promise<{
  total_equity: number
  total_positions_notional: number
  exposure_pct: number
  exchanges: any[]
  is_safe: boolean
}> {
  const res = await fetch(`${BASE}/cross-arb/exposure`)
  if (!res.ok) return { total_equity: 0, total_positions_notional: 0, exposure_pct: 0, exchanges: [], is_safe: true }
  return res.json()
}
