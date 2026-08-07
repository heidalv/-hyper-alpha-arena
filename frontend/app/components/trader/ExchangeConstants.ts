/**
 * Exchange-related constants shared across trader components.
 */

export const EXCHANGE_OPTIONS = [
  { id: 'hyperliquid', name: 'Hyperliquid', color: '#4C6EF5', desc: '去中心化永续合约' },
  { id: 'binance', name: 'Binance', color: '#F0B90B', desc: '全球最大交易所' },
  { id: 'bybit', name: 'Bybit', color: '#F7A600', desc: '衍生品交易平台' },
  { id: 'okx', name: 'OKX', color: '#A0A0A0', desc: 'Web3 综合平台' },
  { id: 'gateio', name: 'Gate.io', color: '#2354E6', desc: '全球数字资产交易' },
  { id: 'asterdex', name: 'Asterdex', color: '#A855F7', desc: '去中心化衍生品' },
] as const

export const EXCHANGE_NAMES: Record<string, string> = Object.fromEntries(
  EXCHANGE_OPTIONS.map(e => [e.id, e.name])
)
