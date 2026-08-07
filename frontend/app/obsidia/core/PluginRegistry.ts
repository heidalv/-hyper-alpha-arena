/**
 * PluginRegistry —— 轻量插件注册表
 * --------------------------------
 * 插件通过 register() 贡献导航分组 / Ribbon 图标 / 命令。
 * 页面本体仍由 main.tsx 的 keepAlive 渲染（保留其 WS/全局状态），
 * 注册表只负责"目录与入口"，这样可在不重写渲染管线的前提下实现插件化。
 */

import type {
  ObsidiaPlugin,
  ObsidiaNavGroup,
  ObsidiaRibbonItem,
  ObsidiaCommand,
} from './types'

export class PluginRegistry {
  private plugins: Map<string, ObsidiaPlugin> = new Map()

  register(plugin: ObsidiaPlugin): void {
    if (this.plugins.has(plugin.id)) {
      console.warn(`[Obsidia] 插件重复注册: ${plugin.id}`)
      return
    }
    this.plugins.set(plugin.id, plugin)
  }

  registerAll(plugins: ObsidiaPlugin[]): void {
    plugins.forEach((p) => this.register(p))
  }

  getPlugins(): ObsidiaPlugin[] {
    return Array.from(this.plugins.values())
  }

  /** 聚合所有插件的导航分组（保序） */
  getNavGroups(): ObsidiaNavGroup[] {
    const groups: ObsidiaNavGroup[] = []
    for (const p of this.plugins.values()) {
      if (p.navGroups) groups.push(...p.navGroups)
    }
    return groups
  }

  /** 聚合所有 Ribbon 图标 */
  getRibbonItems(): ObsidiaRibbonItem[] {
    const items: ObsidiaRibbonItem[] = []
    for (const p of this.plugins.values()) {
      if (p.ribbon) items.push(...p.ribbon)
    }
    return items
  }

  /** 聚合所有命令 */
  getCommands(): ObsidiaCommand[] {
    const cmds: ObsidiaCommand[] = []
    for (const p of this.plugins.values()) {
      if (p.commands) cmds.push(...p.commands)
    }
    return cmds
  }

  /** page → label 映射（用于标签标题、命令面板等） */
  getPageTitles(): Record<string, string> {
    const map: Record<string, string> = {}
    for (const g of this.getNavGroups()) {
      for (const item of g.items) map[item.page] = item.label
    }
    return map
  }
}

/** 全局单例 */
export const registry = new PluginRegistry()
