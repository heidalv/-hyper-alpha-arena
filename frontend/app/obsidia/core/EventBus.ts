/**
 * EventBus —— 极简类型化发布订阅
 * ------------------------------
 * 供外壳、侧栏、视图之间解耦通信（如"文件树点某文件 → 阅读区打开"）。
 */

import type { ObsidiaEventMap } from './types'

type Handler<T> = (payload: T) => void

export class EventBus {
  private handlers: Map<string, Set<Handler<any>>> = new Map()

  on<K extends keyof ObsidiaEventMap>(event: K, handler: Handler<ObsidiaEventMap[K]>): () => void {
    const key = event as string
    if (!this.handlers.has(key)) this.handlers.set(key, new Set())
    this.handlers.get(key)!.add(handler)
    return () => this.off(event, handler)
  }

  off<K extends keyof ObsidiaEventMap>(event: K, handler: Handler<ObsidiaEventMap[K]>): void {
    this.handlers.get(event as string)?.delete(handler)
  }

  emit<K extends keyof ObsidiaEventMap>(event: K, payload: ObsidiaEventMap[K]): void {
    this.handlers.get(event as string)?.forEach((h) => {
      try {
        h(payload)
      } catch (err) {
        console.error(`[Obsidia EventBus] handler error for "${String(event)}":`, err)
      }
    })
  }
}

/** 全局单例 */
export const eventBus = new EventBus()
