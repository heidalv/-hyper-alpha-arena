/**
 * 账户快照共享 Context
 * 总览仪表板与 AI 策略中心共用同一 snapshot 数据源，避免重复请求
 */
import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';

const API_BASE = '/api/atas/v2';

export interface Portfolio {
  account_id: number;
  total_value: number;
  capital: number;
  positions: Record<string, unknown>;
  active_strategies: number;
  unrealized_pnl: number;
  daily_pnl: number;
  current_drawdown?: number;
  peak_equity?: number;
  cash_ratio?: number;
  data_source?: string;
  error?: string;
}

export interface SnapshotData {
  portfolio: Portfolio | null;
  health_score?: unknown;
  risk_alerts?: unknown[];
  metrics?: unknown;
  snapshot_id?: string;
  timestamp?: number;
}

interface AccountSnapshotContextValue {
  /** 当前账户 ID */
  accountId: number | null;
  /** 是否为真实账户（已连接交易所） */
  isRealAccount: boolean;
  /** 快照数据（与总览仪表板同源） */
  snapshot: SnapshotData | null;
  /** 是否正在加载 */
  loading: boolean;
  /** 手动刷新 */
  refresh: () => Promise<void>;
}

const AccountSnapshotContext = createContext<AccountSnapshotContextValue | null>(null);

function isRealAccount(acc: { hyperliquid_environment?: string; binance_enabled?: unknown }): boolean {
  const hl = acc.hyperliquid_environment;
  const binance = acc.binance_enabled === true || acc.binance_enabled === 'true';
  return (hl === 'testnet' || hl === 'mainnet') || !!binance;
}

interface AccountSnapshotProviderProps {
  accountId: number | null;
  accounts: Array<{ id: number; name?: string; hyperliquid_environment?: string; binance_enabled?: unknown }>;
  children: React.ReactNode;
}

export function AccountSnapshotProvider({ accountId, accounts, children }: AccountSnapshotProviderProps) {
  const [snapshot, setSnapshot] = useState<SnapshotData | null>(null);
  const [loading, setLoading] = useState(false);
  const isFetchingRef = useRef(false);

  const selectedAccount = accounts.find((a) => a.id === accountId);
  const isRealAccountFlag = selectedAccount ? isRealAccount(selectedAccount) : false;

  const fetchSnapshot = useCallback(async () => {
    if (!accountId) {
      setSnapshot(null);
      return;
    }
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/account/${accountId}/snapshot`);
      if (res.ok) {
        const data = await res.json();
        setSnapshot(data.snapshot || null);
      } else {
        setSnapshot(null);
      }
    } catch {
      setSnapshot(null);
    } finally {
      isFetchingRef.current = false;
      setLoading(false);
    }
  }, [accountId, isRealAccountFlag]);

  useEffect(() => {
    if (!accountId) {
      setSnapshot(null);
      return;
    }
    fetchSnapshot();
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        fetchSnapshot();
      }
    }, 60000);
    return () => clearInterval(interval);
  }, [accountId, isRealAccountFlag, fetchSnapshot]);

  const value: AccountSnapshotContextValue = {
    accountId,
    isRealAccount: isRealAccountFlag,
    snapshot,
    loading,
    refresh: fetchSnapshot,
  };

  return (
    <AccountSnapshotContext.Provider value={value}>
      {children}
    </AccountSnapshotContext.Provider>
  );
}

export function useAccountSnapshot() {
  const ctx = useContext(AccountSnapshotContext);
  return ctx;
}
