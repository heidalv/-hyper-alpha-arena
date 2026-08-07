/**
 * Exchange selection and management types
 */

export type ExchangeId = 'hyperliquid' | 'binance' | 'bybit' | 'okx' | 'gateio' | 'asterdex';

export interface ExchangeInfo {
  id: ExchangeId;
  name: string;
  displayName: string;
  selectable: boolean;
  selected: boolean;
  apiSupported: boolean;
  comingSoon: boolean;
  logo: string;
  description: string;
  features: string[];
  referralLink: string;
  buttonText: string;
  buttonVariant: 'default' | 'outline';
  proTip?: string;
}

export interface ExchangeSelection {
  current: ExchangeId;
  available: ExchangeId[];
}

export interface ExchangeContextType {
  currentExchange: ExchangeId;
  exchanges: ExchangeInfo[];
  selectExchange: (exchangeId: ExchangeId) => void;
  isLoading: boolean;
}

export const DEFAULT_EXCHANGE: ExchangeId = 'asterdex';

export const EXCHANGE_DISPLAY_NAMES: Record<ExchangeId, string> = {
  hyperliquid: 'Hyperliquid',
  binance: 'Binance',
  bybit: 'Bybit',
  okx: 'OKX',
  gateio: 'Gate.io',
  asterdex: 'Asterdex',
};

export const EXCHANGE_STATUS_COLORS: Record<ExchangeId, string> = {
  hyperliquid: '🟢',
  binance: '🟡',
  bybit: '🟡',
  okx: '🟡',
  gateio: '🟡',
  asterdex: '🟡',
};
