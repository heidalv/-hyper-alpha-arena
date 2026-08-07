import { apiRequest } from './client'

export interface TradingPairInfo {
  symbol: string
  status: 'verified' | 'unverified'
}

export interface TradingPairsResponse {
  symbols: string[]
  symbols_detail: TradingPairInfo[]
  builtin: string[]
  exchange_symbols: string[]
  exchange: string
}

export const getTradingPairs = () =>
  apiRequest<TradingPairsResponse>('/config/trading-pairs')
