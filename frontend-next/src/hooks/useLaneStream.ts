/**
 * useLaneStream — 车道实时通道（优先 /ws，断线自动降级为轮询）
 *
 * 设计 §5：后端拟新增 `lane_update` / `quote_update` / `fill_update` / `breaker_update`
 * 事件（复用现有 `/ws` + `ws_broadcast_hub`）。若后端尚未推送这些事件，
 * 本 hook 只做轮询（配合 `useLanes()`），并暴露 `mode: "ws" | "polling"`，不伪造数据。
 *
 * 判定规则：
 *  - 收到任一 lane 相关事件（lane_update/quote_update/fill_update/breaker_update）→ mode="ws"
 *  - 否则（含 ws 已连接但无 lane 事件 / ws 断线）→ mode="polling"
 *    （页面据此渲染「轮询中」标识，并依赖 usePolling 兜底更新）
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { getWs } from "@/lib/ws";

export type LaneStreamMode = "ws" | "polling";

/** 车道实时事件载荷（与后端约定对齐；字段为可选，由调用方按需取用） */
export interface LaneStreamEvent {
  lane_id?: string;
  symbol?: string;
  edge_bp?: number;
  pnl_today?: number;
  inventory?: number;
  mode?: string;
  bid?: number;
  ask?: number;
  w?: number;
  side?: string;
  px?: number;
  qty?: number;
  spread_bp?: number;
  breaker?: string;
  state?: string;
  ts?: string;
  [key: string]: unknown;
}

const LANE_EVENT_TYPES = new Set(["lane_update", "quote_update", "fill_update", "breaker_update"]);

export interface UseLaneStreamResult {
  /** 当前通道模式 */
  mode: LaneStreamMode;
  /** ws 是否已连接 */
  connected: boolean;
  /** 最近一次收到的 lane 相关事件（用作现场增量更新） */
  lastEvent: LaneStreamEvent | null;
}

/**
 * @param onEvent 可选回调：收到 lane 相关事件时触发（页面可据此局部 patch 数据）
 */
export function useLaneStream(onEvent?: (evt: LaneStreamEvent) => void): UseLaneStreamResult {
  const [mode, setMode] = useState<LaneStreamMode>("polling");
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<LaneStreamEvent | null>(null);
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);
  const seenLaneEventRef = useRef(false);
  // 用于对 ws.connected 的周期性探测（ws 管理器是单例，跨组件共享）
  const statusTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const ws = getWs();
    const unsub = ws.subscribe((data) => {
      if (!data || typeof data !== "object") return;
      const type = data.type || data.action || "";
      if (LANE_EVENT_TYPES.has(type)) {
        seenLaneEventRef.current = true;
        const evt = data as LaneStreamEvent;
        setLastEvent(evt);
        try {
          onEventRef.current?.(evt);
        } catch {
          /* ignore */
        }
      }
    });

    // 周期性探测连接状态 + 是否收到过 lane 事件，决定 mode
    const poll = () => {
      const isConnected = ws.connected;
      setConnected(isConnected);
      setMode(seenLaneEventRef.current ? "ws" : "polling");
    };
    poll();
    statusTimer.current = setInterval(poll, 2000);

    return () => {
      unsub();
      if (statusTimer.current) {
        clearInterval(statusTimer.current);
        statusTimer.current = null;
      }
    };
  }, []);

  return { mode, connected, lastEvent };
}
