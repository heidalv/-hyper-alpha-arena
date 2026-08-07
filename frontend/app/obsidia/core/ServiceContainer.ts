/**
 * ServiceContainer —— Obsidia 的服务容器
 * --------------------------------------
 * 把项目里既有的能力（HTTP / WebSocket / toast / vault / 事件总线）
 * 收敛到一个对象，供插件与视图统一取用，避免各处零散 import。
 */

import { apiRequest } from '@/lib/api'
import { wsConnect, wsSend, wsSubscribe, wsIsOpen, wsGetSocket } from '@/lib/wsManager'
import { toast } from 'react-hot-toast'

import { eventBus, EventBus } from './EventBus'
import * as vaultApi from '../lib/vaultApi'

export interface ObsidiaServices {
  /** 统一 HTTP 请求（走 Vite 代理 /api） */
  api: typeof apiRequest
  /** WebSocket 管理器 */
  ws: {
    connect: typeof wsConnect
    send: typeof wsSend
    subscribe: typeof wsSubscribe
    isOpen: typeof wsIsOpen
    getSocket: typeof wsGetSocket
  }
  /** 轻提示 */
  toast: typeof toast
  /** vault 只读数据接口 */
  vault: typeof vaultApi
  /** 事件总线 */
  events: EventBus
}

export const services: ObsidiaServices = {
  api: apiRequest,
  ws: {
    connect: wsConnect,
    send: wsSend,
    subscribe: wsSubscribe,
    isOpen: wsIsOpen,
    getSocket: wsGetSocket,
  },
  toast,
  vault: vaultApi,
  events: eventBus,
}
