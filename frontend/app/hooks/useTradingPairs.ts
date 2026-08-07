/**
 * 统一交易对配置 Hook
 *
 * 从后端 /api/config/trading-pairs 获取用户配置的常用交易对，
 * 所有需要展示"可选交易对"的模块都应使用此 hook，禁止本地写死 fallback 列表。
 */

import { useState, useEffect, useCallback, useRef } from 'react';

/** @deprecated 请使用 useTradingPairs().symbols；不再提供写死列表 */
export const FALLBACK_TRADING_PAIRS: string[] = [];

export interface SymbolDetail {
  symbol: string;
  status: 'verified' | 'unverified';
}

interface TradingPairsData {
  symbols: string[];
  symbolsDetail: SymbolDetail[];
  builtin: string[];
  exchangeSymbols: string[];
}

interface UseTradingPairsReturn extends TradingPairsData {
  loading: boolean;
  save: (symbols: string[]) => Promise<boolean>;
  add: (symbol: string) => Promise<boolean>;
  remove: (symbol: string) => Promise<boolean>;
  reload: () => Promise<void>;
}

const EMPTY_DATA: TradingPairsData = {
  symbols: [], symbolsDetail: [], builtin: [], exchangeSymbols: [],
};

let globalCache: TradingPairsData | null = null;
let globalPromise: Promise<TradingPairsData> | null = null;
const listeners = new Set<() => void>();

function notifyAll() {
  listeners.forEach(fn => fn());
}

function parseResponse(raw: any): TradingPairsData {
  return {
    symbols: raw.symbols ?? [],
    symbolsDetail: (raw.symbols_detail ?? []).map((d: any) => ({
      symbol: d.symbol, status: d.status ?? 'unverified',
    })),
    builtin: raw.builtin ?? [],
    exchangeSymbols: raw.exchange_symbols ?? [],
  };
}

async function fetchPairs(): Promise<TradingPairsData> {
  const res = await fetch('/api/config/trading-pairs', { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return parseResponse(await res.json());
}

async function loadOnce(): Promise<TradingPairsData> {
  if (globalCache) return globalCache;
  if (!globalPromise) {
    globalPromise = fetchPairs()
      .then(data => {
        globalCache = data;
        globalPromise = null;
        return data;
      })
      .catch(err => {
        globalPromise = null;
        console.warn('[useTradingPairs] 加载全局交易对失败:', err);
        throw err;
      });
  }
  return globalPromise;
}

async function savePairs(symbols: string[]): Promise<TradingPairsData> {
  const res = await fetch('/api/config/trading-pairs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbols }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = parseResponse(await res.json());
  globalCache = data;
  notifyAll();
  return data;
}

export function useTradingPairs(): UseTradingPairsReturn {
  const [data, setData] = useState<TradingPairsData>(globalCache ?? EMPTY_DATA);
  const [loading, setLoading] = useState(!globalCache);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    const syncFromGlobal = () => {
      if (globalCache && mountedRef.current) setData(globalCache);
    };
    listeners.add(syncFromGlobal);

    if (!globalCache) {
      setLoading(true);
      loadOnce()
        .then(d => {
          if (mountedRef.current) {
            setData(d);
            setLoading(false);
          }
        })
        .catch(() => {
          if (mountedRef.current) setLoading(false);
        });
    }

    return () => {
      mountedRef.current = false;
      listeners.delete(syncFromGlobal);
    };
  }, []);

  const save = useCallback(async (symbols: string[]) => {
    try {
      const cleaned = [...new Set(symbols.map(s => s.trim().toUpperCase()).filter(Boolean))];
      if (globalCache) {
        globalCache = { ...globalCache, symbols: cleaned };
        notifyAll();
      }
      await savePairs(cleaned);
      return true;
    } catch {
      return false;
    }
  }, []);

  const add = useCallback(async (symbol: string) => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return false;
    const current = globalCache?.symbols ?? [];
    if (current.includes(sym)) return true;
    return save([...current, sym]);
  }, [save]);

  const remove = useCallback(async (symbol: string) => {
    const sym = symbol.trim().toUpperCase();
    const current = globalCache?.symbols ?? [];
    if (!current.includes(sym)) return true;
    const next = current.filter(s => s !== sym);
    if (next.length === 0) return false;
    return save(next);
  }, [save]);

  const reload = useCallback(async () => {
    setLoading(true);
    globalCache = null;
    globalPromise = null;
    try {
      const d = await loadOnce();
      if (mountedRef.current) {
        setData(d);
        setLoading(false);
      }
      notifyAll();
    } catch {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  return { ...data, loading, save, add, remove, reload };
}

export async function getTradingPairs(): Promise<string[]> {
  const data = await loadOnce();
  return data.symbols;
}
