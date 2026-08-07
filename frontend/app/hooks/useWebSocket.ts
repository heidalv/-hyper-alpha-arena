/**
 * @deprecated 请使用 main.tsx 中的全局 WebSocket 单例代替此 Hook。
 * 此 Hook 会为每个使用它的组件创建独立的 WebSocket 连接，造成重复连接。
 * 保留此文件仅为兼容可能的间接引用，新代码请勿使用。
 *
 * WebSocket Hook for Real-time Updates
 * Manages WebSocket connection and message handling
 *
 * PERFORMANCE OPTIMIZATION:
 * - Uses useRef to store onMessage callback to prevent reconnection on every render
 * - Only connects once per component mount
 * - Properly cleans up connection on unmount
 */

import { useEffect, useRef } from 'react';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';

export function useWebSocket(onMessage: (message: any) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();
  const onMessageRef = useRef(onMessage);

  // Keep onMessage ref updated without triggering reconnection
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let mounted = true;

    const connect = () => {
      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;

        ws.onopen = () => {
          console.log('[WebSocket] Connected');

          // Send account info if needed
          const accountId = localStorage.getItem('account_id');
          if (accountId) {
            ws.send(JSON.stringify({
              type: 'register',
              account_id: parseInt(accountId)
            }));
          }
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            // Use ref to always call latest onMessage without reconnection
            onMessageRef.current(data);
          } catch (err) {
            console.error('[WebSocket] Failed to parse message:', err);
          }
        };

        ws.onerror = (error) => {
          console.error('[WebSocket] Error:', error);
        };

        ws.onclose = (event) => {
          console.log('[WebSocket] Disconnected, code:', event.code);
          wsRef.current = null;

          // Attempt reconnection after 3 seconds if not intentionally closed
          if (mounted && event.code !== 1000) {
            reconnectTimeoutRef.current = setTimeout(() => {
              console.log('[WebSocket] Reconnecting...');
              connect();
            }, 3000);
          }
        };
      } catch (err) {
        console.error('[WebSocket] Connection failed:', err);
      }
    };

    connect();

    // Cleanup on unmount
    return () => {
      mounted = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        // Close with code 1000 (normal closure)
        wsRef.current.close(1000, 'Component unmounted');
        wsRef.current = null;
      }
    };
  }, []); // Empty deps - only connect once per mount

  return wsRef.current;
}
