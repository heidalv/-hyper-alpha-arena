/**
 * 交易状态 Store — 持仓/订单/余额/会话
 * WebSocket 推送更新，组件订阅读取
 */
import { create } from "zustand";

interface Position {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  mark_price: number;
  quantity: number;
  leverage: number;
  unrealized_pnl: number;
  margin: number;
  trade_nature: string;
  status: string;
  opened_at: string;
}

interface TradingState {
  positions: Position[];
  balance: number;
  equity: number;
  sessionId: string | null;
  sessionStatus: string;
  lastUpdate: number;

  setPositions: (positions: Position[]) => void;
  updatePosition: (id: number, patch: Partial<Position>) => void;
  setBalance: (balance: number, equity: number) => void;
  setSession: (id: string, status: string) => void;
}

export const useTradingStore = create<TradingState>((set) => ({
  positions: [],
  balance: 0,
  equity: 0,
  sessionId: null,
  sessionStatus: "unknown",
  lastUpdate: 0,

  setPositions: (positions) => set({ positions, lastUpdate: Date.now() }),

  updatePosition: (id, patch) =>
    set((state) => ({
      positions: state.positions.map((p) =>
        p.id === id ? { ...p, ...patch } : p
      ),
      lastUpdate: Date.now(),
    })),

  setBalance: (balance, equity) => set({ balance, equity, lastUpdate: Date.now() }),

  setSession: (id, status) => set({ sessionId: id, sessionStatus: status }),
}));
