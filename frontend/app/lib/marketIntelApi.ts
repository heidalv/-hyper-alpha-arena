/**
 * 全市场数据中台 API 客户端
 */
import { apiRequest } from './api';

export interface VenueData {
  available: boolean;
  best_bid?: number | null;
  best_ask?: number | null;
  bid_volume?: number | null;
  ask_volume?: number | null;
  open_interest?: number | null;
  funding_rate?: number | null;
  price?: number | null;
  source?: string;
  reason?: string;
}

export interface SymbolOverview {
  orderbook: {
    available: boolean;
    active_venues: number;
    total_venues: number;
    global_imbalance: number | null;
    best_bid: number | null;
    best_ask: number | null;
    cross_venue_spread: number | null;
    venues: Record<string, VenueData>;
  };
  market: {
    available: boolean;
    active_venues: number;
    total_venues: number;
    total_oi: number | null;
    funding_rates: Record<string, number | null>;
    funding_arbitrage: number | null;
    oi_by_exchange: Record<string, number | null>;
    venues: Record<string, VenueData>;
  };
  derivatives: {
    available: boolean;
    funding_rate: number | null;
    signal: string | null;
    signal_strength: number | null;
    liquidation_long: number | null;
    liquidation_short: number | null;
    long_short_ratio: number | null;
    data_sources: string;
  };
  whale: {
    available: boolean;
    direction: number | null;
    total_usd: number | null;
    confidence: number | null;
    whale_count?: number | null;
    net_usd?: number | null;
    active_venues?: number;
    venues?: Record<string, { available: boolean; whale_buy_usd?: number; whale_sell_usd?: number; count?: number; largest_usd?: number }>;
  };
}

export interface OverviewResponse {
  symbols: Record<string, SymbolOverview>;
  fetched_at: number;
}

export interface DataHealth {
  orderbook_venues?: Record<string, { fail_count: number; healthy: boolean }>;
  market_venues?: Record<string, { fail_count: number; healthy: boolean }>;
  btc_readiness?: {
    price_ok: boolean;
    klines_ok: boolean;
    indicators_ok: boolean;
    derivatives_ok: boolean;
    missing: string[];
    warnings: string[];
  };
  overall_score: number;
  fetched_at: number;
}

export interface SourcesConfig {
  venues: Record<string, { name: string; api_key_configured: boolean; public_api: boolean }>;
  aggregate_sources: Record<string, { name: string; api_key_configured: boolean }>;
  venue_health?: Record<string, { fail_count: number; healthy: boolean }>;
  fetched_at: number;
}

/** 交易对来源标记 */
export type SymbolSource = 'user' | 'active' | 'auto';

export interface WatchlistSymbol {
  symbol: string;
  sources: SymbolSource[];
}

export interface WatchlistResponse {
  symbols: string[];
  details: WatchlistSymbol[];
  counts: { user: number; active: number; auto: number; total: number };
  fetched_at: number;
}

export async function fetchOverview(symbols: string[]): Promise<OverviewResponse> {
  const resp = await apiRequest(`/market-intel/overview?symbols=${symbols.join(',')}`);
  return resp.json();
}

export async function fetchOrderbook(symbol: string, depth = 20): Promise<any> {
  const resp = await apiRequest(`/market-intel/orderbook/${symbol}?depth=${depth}`);
  return resp.json();
}

export async function fetchDataHealth(): Promise<DataHealth> {
  const resp = await apiRequest('/market-intel/data-health');
  return resp.json();
}

export async function fetchSourcesConfig(): Promise<SourcesConfig> {
  const resp = await apiRequest('/market-intel/sources-config');
  return resp.json();
}

export async function updateSourcesConfig(config: Record<string, any>): Promise<any> {
  const resp = await apiRequest('/market-intel/sources-config', {
    method: 'PUT',
    body: JSON.stringify(config),
  });
  return resp.json();
}

/** 聚合交易对监控列表：用户配置 + AI运行中 + 自动选币 */
export async function fetchWatchlist(): Promise<WatchlistResponse> {
  const resp = await apiRequest('/market-intel/watchlist');
  return resp.json();
}
