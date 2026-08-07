/**
 * Binance type definitions for frontend
 */

export type BinanceMarketType = 'spot' | 'futures';

export interface BinanceConfig {
  configured: boolean;
  enabled: boolean;
  market_type: BinanceMarketType;  // snake_case to match backend
  marketType?: BinanceMarketType;  // camelCase alias for compatibility
  testnet: boolean;
  max_leverage: number;  // snake_case to match backend
  maxLeverage?: number;  // camelCase alias for compatibility
  api_key_fingerprint?: string | null;  // 用于去重同一币安账户
}

export interface BinanceBalance {
  total_balance: number;
  available_balance: number;
  margin_used?: number;
  frozen_balance?: number;
  currency: string;
  // 添加别名以兼容代码中的 total_equity
  total_equity?: number;
  roe_percent?: number;
}

export interface BinancePosition {
  symbol: string;
  side: string; // 'long' or 'short'
  size: number;
  entry_price: number;
  mark_price: number;
  liquidation_price: number;
  unrealized_pnl: number;
  roe_percent?: number; // ROE百分比 (未实现盈亏 / 保证金 * 100)
  leverage: number;
  margin: number;
  notional: number;
}

export interface BinancePositionsResponse {
  positions: BinancePosition[];
}

export interface BinanceSetupRequest {
  api_key?: string;  // 可选：更新时如果为空则保持原有密钥
  api_secret?: string;  // 可选：更新时如果为空则保持原有密钥
  market_type: BinanceMarketType;
  testnet: boolean;
  max_leverage?: number;
}

export interface BinanceOrderRequest {
  symbol: string;
  side: 'buy' | 'sell';
  amount: number;
  order_type: 'market' | 'limit';
  price?: number;
  leverage?: number;
  reduce_only?: boolean;
  // 阶段 3.2: 执行算法（MARKET/TWAP/POV/FUNDING_IS/SOR）
  algo?: string;
  algo_config?: Record<string, number>;
}

export interface BinanceOrderResult {
  status: 'success' | 'pending' | 'error';
  order_id?: string;
  symbol: string;
  side: string;
  amount: number;
  filled?: number;
  price?: number;
  error?: string;
  exchange: string;
  market_type: string;
}

export interface BinanceApiResponse {
  success: boolean;
  message: string;
  market_type?: BinanceMarketType;
  testnet?: boolean;
}
