/**
 * useWebSocket — 订阅 WebSocket 消息 + 断线自动降级到 HTTP 轮询
 */
"use client";

import { useEffect, useRef } from "react";
import { getWs } from "@/lib/ws";
import { useMarketStore } from "@/lib/stores/market";
import { useTradingStore } from "@/lib/stores/trading";
import { api } from "@/lib/api";

// 降级轮询间隔
const FALLBACK_POLL_INTERVAL = 15_000;

export function useWebSocket(enabled: boolean = true) {
  const setPrice = useMarketStore((s) => s.setPrice);
  const setWsConnected = useMarketStore((s) => s.setWsConnected);
  const setPositions = useTradingStore((s) => s.setPositions);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled) {
      setWsConnected(false);
      return;
    }
    const ws = getWs();

    // WebSocket 消息处理
    const unsub = ws.subscribe((data) => {
      if (!data || typeof data !== "object") return;

      const msgType = data.type || data.action || "";

      switch (msgType) {
        case "price_update":
        case "ticker":
          if (data.symbol && data.price) {
            setPrice(data.symbol, data.price, data.change_24h);
          }
          break;

        case "positions":
        case "position_update":
          if (Array.isArray(data.positions)) {
            setPositions(data.positions);
          }
          break;

        case "get_snapshot":
          if (data.prices) {
            Object.entries(data.prices).forEach(([sym, info]: [string, any]) => {
              if (info?.price) setPrice(sym, info.price);
            });
          }
          break;
      }
    });

    // 连接状态监控 + 降级轮询
    const statusTimer = setInterval(() => {
      const connected = ws.connected;
      setWsConnected(connected);

      // 断线时启动降级轮询
      if (!connected && !pollTimerRef.current) {
        console.log("[WS] 断线，启动 HTTP 降级轮询");
        pollTimerRef.current = setInterval(async () => {
          try {
            // 轮询账户概览
            const overview = await api.getDashboard();
            if (overview?.account) {
              // 更新余额
              useTradingStore.getState().setBalance(
                overview.account.current_cash,
                overview.portfolio?.total_assets || overview.account.current_cash
              );
            }
          } catch {}
        }, FALLBACK_POLL_INTERVAL);
      }

      // 恢复连接时停止降级轮询
      if (connected && pollTimerRef.current) {
        console.log("[WS] 恢复连接，停止降级轮询");
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    }, 3000);

    return () => {
      unsub();
      clearInterval(statusTimer);
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [enabled, setPrice, setWsConnected, setPositions]);
}
