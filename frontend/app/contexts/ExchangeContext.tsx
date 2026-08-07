/**
 * Exchange selection context — dynamically loads configured exchanges
 */

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import {
  ExchangeId,
  ExchangeInfo,
  ExchangeContextType,
  EXCHANGE_DISPLAY_NAMES,
} from '@/lib/types/exchange';

const ExchangeContext = createContext<ExchangeContextType | undefined>(undefined);

interface ExchangeProviderProps {
  children: ReactNode;
}

const STORAGE_KEY = 'hyper-alpha-arena-selected-exchange';

const HL_INFO: ExchangeInfo = {
  id: 'hyperliquid',
  name: 'Hyperliquid',
  displayName: 'Hyperliquid',
  selectable: true,
  selected: true,
  apiSupported: true,
  comingSoon: false,
  logo: '/static/hyperliquid_logo.png',
  description: 'Decentralized perpetual futures exchange',
  features: ['No KYC Required', 'Low Fees', 'High Performance'],
  referralLink: 'https://app.hyperliquid.xyz/join/HYPERSVIP',
  buttonText: 'Open Futures',
  buttonVariant: 'default',
};

function makeExchangeInfo(id: ExchangeId, configured: boolean): ExchangeInfo {
  return {
    id,
    name: EXCHANGE_DISPLAY_NAMES[id] || id,
    displayName: EXCHANGE_DISPLAY_NAMES[id] || id,
    selectable: configured,
    selected: false,
    apiSupported: configured,
    comingSoon: !configured,
    logo: '',
    description: `${EXCHANGE_DISPLAY_NAMES[id] || id} exchange`,
    features: [],
    referralLink: '',
    buttonText: configured ? 'Connected' : 'Configure',
    buttonVariant: configured ? 'default' : 'outline',
  };
}

export function ExchangeProvider({ children }: ExchangeProviderProps) {
  const [currentExchange, setCurrentExchange] = useState<ExchangeId>('hyperliquid');
  const [exchanges, setExchanges] = useState<ExchangeInfo[]>([HL_INFO]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    loadExchanges();
  }, []);

  async function loadExchanges() {
    const list: ExchangeInfo[] = [HL_INFO];
    try {
      const res = await fetch('/api/exchange/credentials');
      if (res.ok) {
        const creds: { exchange: string; enabled: boolean }[] = await res.json();
        const configured = new Set(creds.filter(c => c.enabled).map(c => c.exchange));

        const others: ExchangeId[] = ['binance', 'bybit', 'okx', 'gateio', 'asterdex'];
        for (const id of others) {
          list.push(makeExchangeInfo(id, configured.has(id)));
        }
      }
    } catch {
      // API not available, only show HL
    }
    setExchanges(list);
  }

  const selectExchange = async (exchangeId: ExchangeId) => {
    if (exchangeId === currentExchange) return;
    const exchange = exchanges.find(ex => ex.id === exchangeId);
    if (!exchange?.selectable) return;

    setIsLoading(true);
    try {
      await fetch('/api/users/exchange-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_exchange: exchangeId }),
      });
      setCurrentExchange(exchangeId);
      localStorage.setItem(STORAGE_KEY, exchangeId);
    } catch {
      localStorage.setItem(STORAGE_KEY, exchangeId);
      setCurrentExchange(exchangeId);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <ExchangeContext.Provider value={{ currentExchange, exchanges, selectExchange, isLoading }}>
      {children}
    </ExchangeContext.Provider>
  );
}

export function useExchange(): ExchangeContextType {
  const context = useContext(ExchangeContext);
  if (context === undefined) {
    throw new Error('useExchange must be used within an ExchangeProvider');
  }
  return context;
}

export function useCurrentExchange(): ExchangeId {
  const { currentExchange } = useExchange();
  return currentExchange;
}

export function useCurrentExchangeInfo(): ExchangeInfo {
  const { currentExchange, exchanges } = useExchange();
  return exchanges.find(ex => ex.id === currentExchange) || exchanges[0];
}
