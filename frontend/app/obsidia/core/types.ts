/**
 * Obsidia 核心类型
 * ----------------
 * 插件化前端的公共契约：导航项 / 分组 / Ribbon / 命令 / 事件。
 */

import type { ComponentType } from 'react'

/** lucide-react 图标（或任何接受 size/className 的组件） */
export type ObsidiaIcon = ComponentType<{ size?: number; className?: string; style?: React.CSSProperties }>

/** 一个可导航到的页面（page 即现有 hash 路由的 key） */
export interface ObsidiaNavItem {
  page: string
  label: string
  icon: ObsidiaIcon
}

/** 侧栏里的一组导航 */
export interface ObsidiaNavGroup {
  id: string
  label: string
  items: ObsidiaNavItem[]
  /** 默认是否展开 */
  defaultOpen?: boolean
}

/** 左侧 Ribbon 图标（快捷入口） */
export interface ObsidiaRibbonItem {
  id: string
  page: string
  label: string
  icon: ObsidiaIcon
  /** obsidian=知识库视图 / trading=交易 / bottom=底部动作 */
  zone?: 'obsidian' | 'trading' | 'bottom'
}

/** 命令面板中的一条命令 */
export interface ObsidiaCommand {
  id: string
  title: string
  hint?: string
  icon?: ObsidiaIcon
  keywords?: string
  run: () => void
}

/** 一个插件的声明（当前采用轻量注册表：贡献导航/Ribbon/命令，页面渲染仍复用 keepAlive） */
export interface ObsidiaPlugin {
  id: string
  name: string
  navGroups?: ObsidiaNavGroup[]
  ribbon?: ObsidiaRibbonItem[]
  commands?: ObsidiaCommand[]
}

/** EventBus 事件表 */
export interface ObsidiaEventMap {
  /** 请求在阅读区打开某个 vault 笔记 */
  'vault:open': { path: string }
  /** 请求导航到某个页面 */
  'nav:go': { page: string }
  /** 切换命令面板 */
  'command-palette:toggle': void
}
