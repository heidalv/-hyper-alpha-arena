/**
 * 统一数据刷新频率配置
 * Unified Data Refresh Configuration for Frontend
 *
 * 所有前端组件的刷新间隔都应该从这个配置文件读取
 * 确保与后端配置保持一致
 */

export interface RefreshConfig {
  interval: number;        // 刷新间隔（毫秒）
  displayText: string;      // 显示文本
  description: string;      // 描述
}

// === 统一刷新配置 (单位: 毫秒) ===
export const REFRESH_CONFIGS: Record<string, RefreshConfig> = {
  // === 实时价格数据 (最快) ===
  price_ticker: {
    interval: 3000,         // 3秒
    displayText: '每3秒刷新',
    description: '实时价格刷新间隔'
  },

  // === 账户余额数据 (快速) ===
  binance_balance: {
    interval: 30000,        // 30秒
    displayText: '每30秒刷新',
    description: '币安余额刷新间隔'
  },
  hyperliquid_balance: {
    interval: 5000,         // 5秒
    displayText: '每5秒刷新',
    description: 'Hyperliquid余额刷新间隔'
  },

  // === 持仓数据 (快速) ===
  binance_positions: {
    interval: 30000,        // 30秒
    displayText: '每30秒刷新',
    description: '币安持仓刷新间隔'
  },
  hyperliquid_positions: {
    interval: 5000,         // 5秒
    displayText: '每5秒刷新',
    description: 'Hyperliquid持仓刷新间隔'
  },

  // === WebSocket快照推送 (中等) ===
  websocket_snapshot: {
    interval: 10000,        // 10秒
    displayText: '每10秒推送',
    description: 'WebSocket账户快照推送间隔'
  },

  // === 交易历史 (中等) ===
  recent_trades: {
    interval: 10000,        // 10秒
    displayText: '每10秒刷新',
    description: '最近交易历史刷新间隔'
  },
  action_summary: {
    interval: 15000,        // 15秒
    displayText: '每15秒刷新',
    description: '交易行为汇总刷新间隔'
  },

  // === AI决策和分析 (较慢) ===
  ai_decisions: {
    interval: 30000,        // 30秒
    displayText: '每30秒刷新',
    description: 'AI决策历史刷新间隔'
  },
  attribution_analysis: {
    interval: 30000,        // 30秒
    displayText: '每30秒刷新',
    description: '归因分析数据刷新间隔'
  },

  // === K线图表 (中等) ===
  kline_chart: {
    interval: 10000,        // 10秒
    displayText: '每10秒刷新',
    description: 'K线图表数据刷新间隔'
  },

  // === 市场流向数据 (中等) ===
  market_flow: {
    interval: 10000,        // 10秒
    displayText: '每10秒刷新',
    description: '市场流向指标刷新间隔'
  },
};

// === 数据延迟警告阈值 ===
export const DELAY_WARNING_THRESHOLD = 15000; // 15秒

// === Loading状态超时时间 ===
export const LOADING_TIMEOUT = 2000; // 2秒

// === 工具函数 ===

/**
 * 获取指定类型的刷新配置
 */
export function getRefreshConfig(type: string): RefreshConfig {
  const config = REFRESH_CONFIGS[type];
  if (!config) {
    console.warn(`Unknown refresh type: ${type}, using default 10s`);
    return {
      interval: 10000,
      displayText: '每10秒刷新',
      description: '默认刷新间隔'
    };
  }
  return config;
}

/**
 * 获取刷新间隔（毫秒）
 */
export function getRefreshInterval(type: string): number {
  return getRefreshConfig(type).interval;
}

/**
 * 获取刷新显示文本
 */
export function getRefreshDisplayText(type: string): string {
  return getRefreshConfig(type).displayText;
}

/**
 * 格式化最后更新时间
 */
export function formatLastUpdate(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp;

  if (diff < 1000) return '刚刚更新';
  if (diff < 60000) return `${Math.floor(diff / 1000)}秒前更新`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前更新`;
  return new Date(timestamp).toLocaleTimeString();
}

/**
 * 检查数据是否延迟
 */
export function isDataStale(lastUpdate: number, type?: string): boolean {
  const threshold = type ? getRefreshInterval(type) * 2 : DELAY_WARNING_THRESHOLD;
  return Date.now() - lastUpdate > threshold;
}

/**
 * 计算下次更新时间
 */
export function getNextUpdateTime(lastUpdate: number, type: string): number {
  return lastUpdate + getRefreshInterval(type);
}

/**
 * 获取刷新进度百分比（用于进度条）
 */
export function getRefreshProgress(lastUpdate: number, type: string): number {
  const interval = getRefreshInterval(type);
  const elapsed = Date.now() - lastUpdate;
  return Math.min(100, (elapsed / interval) * 100);
}
