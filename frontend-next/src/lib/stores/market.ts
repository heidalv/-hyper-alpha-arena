/**
 * 市场状态 Store — 实时价格/行情
 * 高频更新（价格每秒变化），用 Map 存储避免频繁重渲染
 */
import { create } from "zustand";

interface PriceData {
  price: number;
  change24h?: number;
  prevPrice: number;
  lastUpdate: number;
}

interface MarketState {
  prices: Record<string, PriceData>;
  wsConnected: boolean;

  setPrice: (symbol: string, price: number, change24h?: number) => void;
  setWsConnected: (connected: boolean) => void;
  getPrice: (symbol: string) => PriceData | undefined;
}

export const useMarketStore = create<MarketState>((set, get) => ({
  prices: {},
  wsConnected: false,

  setPrice: (symbol, price, change24h) =>
    set((state) => {
      const existing = state.prices[symbol];
      return {
        prices: {
          ...state.prices,
          [symbol]: {
            price,
            change24h: change24h ?? existing?.change24h,
            prevPrice: existing?.price ?? price,
            lastUpdate: Date.now(),
          },
        },
      };
    }),

  setWsConnected: (connected) => set({ wsConnected: connected }),

  getPrice: (symbol) => get().prices[symbol],
}));
