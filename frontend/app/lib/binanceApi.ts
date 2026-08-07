/**
 * Binance API service module
 */

import { apiRequest } from './api';
import type {
  BinanceConfig,
  BinanceBalance,
  BinancePositionsResponse,
  BinanceSetupRequest,
  BinanceOrderRequest,
  BinanceOrderResult,
  BinanceApiResponse,
} from './types/binance';

const BINANCE_API_BASE = '/binance';

/**
 * Configuration Management
 */
export async function setupBinanceAccount(
  accountId: number,
  config: BinanceSetupRequest
): Promise<BinanceApiResponse> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/setup`,
    {
      method: 'POST',
      body: JSON.stringify(config),
    }
  );
  return response.json();
}

export async function getBinanceConfig(
  accountId: number
): Promise<BinanceConfig> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/config`
  );
  return response.json();
}

export async function enableBinanceTrading(
  accountId: number
): Promise<BinanceApiResponse> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/enable`,
    {
      method: 'POST',
    }
  );
  return response.json();
}

export async function disableBinanceTrading(
  accountId: number
): Promise<BinanceApiResponse> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/disable`,
    {
      method: 'POST',
    }
  );
  return response.json();
}

export async function deleteBinanceConfig(
  accountId: number
): Promise<BinanceApiResponse> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/config`,
    {
      method: 'DELETE',
    }
  );
  return response.json();
}

/**
 * Account Data
 */
export async function getBinanceBalance(
  accountId: number
): Promise<BinanceBalance> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/balance`
  );
  return response.json();
}

export async function getBinancePositions(
  accountId: number,
  forceRefresh: boolean = false // 添加 forceRefresh 参数
): Promise<BinancePositionsResponse> {
  const url = `${BINANCE_API_BASE}/accounts/${accountId}/positions${forceRefresh ? '?force_refresh=true' : ''}`;
  const response = await apiRequest(url);
  return response.json();
}

/**
 * Trading Operations
 */
export async function placeBinanceOrder(
  accountId: number,
  order: BinanceOrderRequest
): Promise<BinanceOrderResult> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/orders`,
    {
      method: 'POST',
      body: JSON.stringify(order),
    }
  );
  return response.json();
}

/**
 * Close a position (futures only)
 */
export async function closeBinancePosition(
  accountId: number,
  symbol: string
): Promise<BinanceOrderResult> {
  const response = await apiRequest(
    `${BINANCE_API_BASE}/accounts/${accountId}/close-position?symbol=${encodeURIComponent(symbol)}`,
    {
      method: 'POST',
    }
  );
  return response.json();
}

/**
 * AI Strategy Status
 */
export interface StrategyStatus {
  account_id: number
  account_name: string
  enabled: boolean
  running: boolean
  trigger_interval: number
  signal_pool_id: number | null
  last_trigger_at: string | null
  next_trigger_in: number | null
  manager_running: boolean
}

export async function getAccountStrategyStatus(
  accountId: number
): Promise<StrategyStatus | null> {
  try {
    const response = await apiRequest(
      `/accounts/${accountId}/strategy/status`
    );
    return response.json();
  } catch {
    // 404 = account has no strategy configured, not an error
    return null;
  }
}

/**
 * AI Decision History
 */
export interface AIDecisionEntry {
  id: number
  account_id: number
  account_name: string
  model: string
  decision_time: string
  operation: string
  symbol: string | null
  reason: string
  prev_portion: number
  target_portion: number
  total_balance: number
  executed: string
  order_id: number | null
  hyperliquid_environment: string | null
  wallet_address: string | null
  prompt_snapshot: string | null
  reasoning_snapshot: string | null
  decision_snapshot: any | null
  // 三周期独立分析
  short_bias?: string | null
  short_confidence?: number | null
  mid_bias?: string | null
  mid_confidence?: number | null
  long_bias?: string | null
  long_confidence?: number | null
}

export interface AIDecisionHistoryResponse {
  generated_at: string
  entries: AIDecisionEntry[]
}

export async function getAIDecisionHistory(
  accountId: number,
  limit: number = 20
): Promise<AIDecisionHistoryResponse> {
  const response = await apiRequest(
    `/arena/model-chat?account_id=${accountId}&limit=${limit}`
  );
  return response.json();
}
