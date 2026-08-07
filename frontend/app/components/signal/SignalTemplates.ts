/**
 * 信号模板库 - 预置主流交易策略模板
 * Signal Templates Library - Pre-built mainstream trading strategy templates
 */

export interface SignalCondition {
  metric: string
  operator: string
  threshold: number
  time_window: string
  description?: string
}

export interface SignalTemplate {
  id: string
  name: string
  nameEn: string
  category: 'trend' | 'momentum' | 'volume_price' | 'volatility'
  description: string
  descriptionEn: string
  direction: 'long' | 'short' | 'both'
  signals: SignalCondition[]
  logic: 'AND' | 'OR'
  riskLevel: 'low' | 'medium' | 'high'
  timeframe: string
  tags: string[]
}

export interface TemplateCategory {
  id: string
  name: string
  nameEn: string
  icon: string
  description: string
  descriptionEn: string
}

// 模板分类定义
export const TEMPLATE_CATEGORIES: TemplateCategory[] = [
  {
    id: 'trend',
    name: '趋势跟踪',
    nameEn: 'Trend Following',
    icon: 'TrendingUp',
    description: '识别和跟随市场主要趋势方向',
    descriptionEn: 'Identify and follow main market trend direction'
  },
  {
    id: 'momentum',
    name: '动量指标',
    nameEn: 'Momentum',
    icon: 'Zap',
    description: '捕捉价格动量变化和超买超卖信号',
    descriptionEn: 'Capture momentum changes and overbought/oversold signals'
  },
  {
    id: 'volume_price',
    name: '量价分析',
    nameEn: 'Volume & Price',
    icon: 'BarChart3',
    description: '分析成交量和订单流异动',
    descriptionEn: 'Analyze volume and order flow anomalies'
  },
  {
    id: 'volatility',
    name: '波动率',
    nameEn: 'Volatility',
    icon: 'Activity',
    description: '基于市场波动率的突破和收缩策略',
    descriptionEn: 'Breakout and contraction strategies based on volatility'
  }
]

// 预置信号模板
export const SIGNAL_TEMPLATES: SignalTemplate[] = [
  // ==================== 趋势跟踪类 ====================
  {
    id: 'ema_cross_long',
    name: '均线金叉做多',
    nameEn: 'EMA Golden Cross Long',
    category: 'trend',
    description: '快速EMA上穿慢速EMA，确认上升趋势形成',
    descriptionEn: 'Fast EMA crosses above slow EMA, confirming uptrend formation',
    direction: 'long',
    signals: [
      { metric: 'ema_cross', operator: 'equals', threshold: 1, time_window: '1h', description: 'EMA金叉' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['均线', '趋势', '做多']
  },
  {
    id: 'ema_cross_short',
    name: '均线死叉做空',
    nameEn: 'EMA Death Cross Short',
    category: 'trend',
    description: '快速EMA下穿慢速EMA，确认下降趋势形成',
    descriptionEn: 'Fast EMA crosses below slow EMA, confirming downtrend formation',
    direction: 'short',
    signals: [
      { metric: 'ema_cross', operator: 'equals', threshold: -1, time_window: '1h', description: 'EMA死叉' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['均线', '趋势', '做空']
  },
  {
    id: 'trend_strength_long',
    name: '强势趋势确认',
    nameEn: 'Strong Trend Confirmation',
    category: 'trend',
    description: '价格站上均线且趋势强度指标确认',
    descriptionEn: 'Price above MA with trend strength confirmation',
    direction: 'long',
    signals: [
      { metric: 'price_above_ema', operator: 'equals', threshold: 1, time_window: '1h', description: '价格在EMA上方' },
      { metric: 'adx', operator: 'greater_than', threshold: 25, time_window: '1h', description: 'ADX>25趋势强劲' }
    ],
    logic: 'AND',
    riskLevel: 'low',
    timeframe: '1h',
    tags: ['趋势确认', 'ADX', '低风险']
  },
  {
    id: 'multi_tf_trend',
    name: '多周期趋势共振',
    nameEn: 'Multi-Timeframe Trend Alignment',
    category: 'trend',
    description: '多个时间周期趋势方向一致，高确定性入场',
    descriptionEn: 'Trend alignment across multiple timeframes for high-probability entry',
    direction: 'both',
    signals: [
      { metric: 'trend_1h', operator: 'equals', threshold: 1, time_window: '1h', description: '1H趋势向上' },
      { metric: 'trend_4h', operator: 'equals', threshold: 1, time_window: '4h', description: '4H趋势向上' }
    ],
    logic: 'AND',
    riskLevel: 'low',
    timeframe: '4h',
    tags: ['多周期', '高确定性', '低风险']
  },

  // ==================== 动量类 ====================
  {
    id: 'rsi_oversold_long',
    name: 'RSI超卖反弹',
    nameEn: 'RSI Oversold Bounce',
    category: 'momentum',
    description: 'RSI进入超卖区域后回升，捕捉反弹机会',
    descriptionEn: 'RSI enters oversold zone and recovers, capturing bounce opportunity',
    direction: 'long',
    signals: [
      { metric: 'rsi', operator: 'less_than', threshold: 30, time_window: '1h', description: 'RSI<30超卖' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['RSI', '超卖', '反转']
  },
  {
    id: 'rsi_overbought_short',
    name: 'RSI超买做空',
    nameEn: 'RSI Overbought Short',
    category: 'momentum',
    description: 'RSI进入超买区域，预期回调做空',
    descriptionEn: 'RSI enters overbought zone, expecting pullback for short',
    direction: 'short',
    signals: [
      { metric: 'rsi', operator: 'greater_than', threshold: 70, time_window: '1h', description: 'RSI>70超买' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['RSI', '超买', '反转']
  },
  {
    id: 'macd_golden_cross',
    name: 'MACD金叉做多',
    nameEn: 'MACD Golden Cross Long',
    category: 'momentum',
    description: 'MACD快线上穿慢线，动量转多',
    descriptionEn: 'MACD fast line crosses above slow line, momentum turns bullish',
    direction: 'long',
    signals: [
      { metric: 'macd_cross', operator: 'equals', threshold: 1, time_window: '1h', description: 'MACD金叉' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['MACD', '金叉', '动量']
  },
  {
    id: 'macd_death_cross',
    name: 'MACD死叉做空',
    nameEn: 'MACD Death Cross Short',
    category: 'momentum',
    description: 'MACD快线下穿慢线，动量转空',
    descriptionEn: 'MACD fast line crosses below slow line, momentum turns bearish',
    direction: 'short',
    signals: [
      { metric: 'macd_cross', operator: 'equals', threshold: -1, time_window: '1h', description: 'MACD死叉' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['MACD', '死叉', '动量']
  },

  // ==================== 量价类 ====================
  {
    id: 'taker_buy_surge',
    name: '主买量激增',
    nameEn: 'Taker Buy Surge',
    category: 'volume_price',
    description: '主动买入量显著大于卖出量，买方力量强劲',
    descriptionEn: 'Taker buy volume significantly exceeds sell volume, strong buying pressure',
    direction: 'long',
    signals: [
      { metric: 'taker_buy_ratio', operator: 'greater_than', threshold: 0.55, time_window: '5m', description: '主买比例>55%' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['成交量', '主买', '短线']
  },
  {
    id: 'taker_sell_surge',
    name: '主卖量激增',
    nameEn: 'Taker Sell Surge',
    category: 'volume_price',
    description: '主动卖出量显著大于买入量，卖方力量强劲',
    descriptionEn: 'Taker sell volume significantly exceeds buy volume, strong selling pressure',
    direction: 'short',
    signals: [
      { metric: 'taker_buy_ratio', operator: 'less_than', threshold: -0.55, time_window: '5m', description: '主买比例<-55%' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['成交量', '主卖', '短线']
  },
  {
    id: 'cvd_bullish',
    name: 'CVD买压信号',
    nameEn: 'CVD Bullish Signal',
    category: 'volume_price',
    description: '累计成交量差显示持续买方压力',
    descriptionEn: 'Cumulative Volume Delta shows sustained buying pressure',
    direction: 'long',
    signals: [
      { metric: 'cvd', operator: 'greater_than', threshold: 5000000, time_window: '1h', description: 'CVD>5M' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['CVD', '买压', '资金流']
  },
  {
    id: 'cvd_bearish',
    name: 'CVD卖压信号',
    nameEn: 'CVD Bearish Signal',
    category: 'volume_price',
    description: '累计成交量差显示持续卖方压力',
    descriptionEn: 'Cumulative Volume Delta shows sustained selling pressure',
    direction: 'short',
    signals: [
      { metric: 'cvd', operator: 'less_than', threshold: -5000000, time_window: '1h', description: 'CVD<-5M' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '1h',
    tags: ['CVD', '卖压', '资金流']
  },
  {
    id: 'order_imbalance_long',
    name: '订单簿失衡做多',
    nameEn: 'Order Book Imbalance Long',
    category: 'volume_price',
    description: '订单簿买方深度显著大于卖方',
    descriptionEn: 'Order book bid depth significantly exceeds ask depth',
    direction: 'long',
    signals: [
      { metric: 'order_imbalance', operator: 'greater_than', threshold: 0.6, time_window: '5m', description: '失衡>0.6' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['订单簿', '深度', '短线']
  },
  {
    id: 'order_imbalance_short',
    name: '订单簿失衡做空',
    nameEn: 'Order Book Imbalance Short',
    category: 'volume_price',
    description: '订单簿卖方深度显著大于买方',
    descriptionEn: 'Order book ask depth significantly exceeds bid depth',
    direction: 'short',
    signals: [
      { metric: 'order_imbalance', operator: 'less_than', threshold: -0.6, time_window: '5m', description: '失衡<-0.6' }
    ],
    logic: 'AND',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['订单簿', '深度', '短线']
  },
  {
    id: 'oi_surge',
    name: 'OI激增信号',
    nameEn: 'Open Interest Surge',
    category: 'volume_price',
    description: '未平仓合约量显著增加，新资金入场',
    descriptionEn: 'Significant increase in open interest, new money entering',
    direction: 'both',
    signals: [
      { metric: 'oi_delta_percent', operator: 'greater_than', threshold: 2, time_window: '15m', description: 'OI变化>2%' }
    ],
    logic: 'AND',
    riskLevel: 'high',
    timeframe: '15m',
    tags: ['OI', '资金流', '高风险']
  },

  // ==================== 波动率类 ====================
  {
    id: 'bollinger_upper_breakout',
    name: '布林带上轨突破',
    nameEn: 'Bollinger Upper Breakout',
    category: 'volatility',
    description: '价格突破布林带上轨，强势突破信号',
    descriptionEn: 'Price breaks above upper Bollinger Band, strong breakout signal',
    direction: 'long',
    signals: [
      { metric: 'bb_position', operator: 'greater_than', threshold: 1, time_window: '1h', description: '突破上轨' }
    ],
    logic: 'AND',
    riskLevel: 'high',
    timeframe: '1h',
    tags: ['布林带', '突破', '高风险']
  },
  {
    id: 'bollinger_lower_breakout',
    name: '布林带下轨突破',
    nameEn: 'Bollinger Lower Breakout',
    category: 'volatility',
    description: '价格跌破布林带下轨，强势下跌信号',
    descriptionEn: 'Price breaks below lower Bollinger Band, strong breakdown signal',
    direction: 'short',
    signals: [
      { metric: 'bb_position', operator: 'less_than', threshold: -1, time_window: '1h', description: '突破下轨' }
    ],
    logic: 'AND',
    riskLevel: 'high',
    timeframe: '1h',
    tags: ['布林带', '突破', '高风险']
  },
  {
    id: 'atr_expansion',
    name: 'ATR波动扩张',
    nameEn: 'ATR Volatility Expansion',
    category: 'volatility',
    description: 'ATR快速扩张，市场进入高波动期',
    descriptionEn: 'ATR expanding rapidly, market entering high volatility period',
    direction: 'both',
    signals: [
      { metric: 'atr_ratio', operator: 'greater_than', threshold: 1.5, time_window: '1h', description: 'ATR扩张>1.5x' }
    ],
    logic: 'AND',
    riskLevel: 'high',
    timeframe: '1h',
    tags: ['ATR', '波动率', '高风险']
  },

  // ==================== 组合策略 ====================
  {
    id: 'combo_bullish_momentum',
    name: '多头动量组合',
    nameEn: 'Bullish Momentum Combo',
    category: 'momentum',
    description: '多个动量指标同时确认看多，高概率入场',
    descriptionEn: 'Multiple momentum indicators confirm bullish, high-probability entry',
    direction: 'long',
    signals: [
      { metric: 'order_imbalance', operator: 'greater_than', threshold: 0.5, time_window: '5m', description: '订单失衡>0.5' },
      { metric: 'taker_buy_ratio', operator: 'greater_than', threshold: 0.5, time_window: '5m', description: '主买>50%' },
      { metric: 'cvd', operator: 'greater_than', threshold: 3000000, time_window: '1h', description: 'CVD>3M' }
    ],
    logic: 'OR',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['组合', '多指标', '高概率']
  },
  {
    id: 'combo_bearish_momentum',
    name: '空头动量组合',
    nameEn: 'Bearish Momentum Combo',
    category: 'momentum',
    description: '多个动量指标同时确认看空，高概率入场',
    descriptionEn: 'Multiple momentum indicators confirm bearish, high-probability entry',
    direction: 'short',
    signals: [
      { metric: 'order_imbalance', operator: 'less_than', threshold: -0.5, time_window: '5m', description: '订单失衡<-0.5' },
      { metric: 'taker_buy_ratio', operator: 'less_than', threshold: -0.5, time_window: '5m', description: '主卖>50%' },
      { metric: 'cvd', operator: 'less_than', threshold: -3000000, time_window: '1h', description: 'CVD<-3M' }
    ],
    logic: 'OR',
    riskLevel: 'medium',
    timeframe: '5m',
    tags: ['组合', '多指标', '高概率']
  }
]

// 按分类获取模板
export function getTemplatesByCategory(category: string): SignalTemplate[] {
  return SIGNAL_TEMPLATES.filter(t => t.category === category)
}

// 按方向获取模板
export function getTemplatesByDirection(direction: 'long' | 'short' | 'both'): SignalTemplate[] {
  return SIGNAL_TEMPLATES.filter(t => t.direction === direction || t.direction === 'both')
}

// 按风险等级获取模板
export function getTemplatesByRisk(riskLevel: 'low' | 'medium' | 'high'): SignalTemplate[] {
  return SIGNAL_TEMPLATES.filter(t => t.riskLevel === riskLevel)
}

// 搜索模板
export function searchTemplates(query: string): SignalTemplate[] {
  const lowerQuery = query.toLowerCase()
  return SIGNAL_TEMPLATES.filter(t =>
    t.name.toLowerCase().includes(lowerQuery) ||
    t.nameEn.toLowerCase().includes(lowerQuery) ||
    t.description.toLowerCase().includes(lowerQuery) ||
    t.tags.some(tag => tag.toLowerCase().includes(lowerQuery))
  )
}

// 可用指标列表
export const AVAILABLE_METRICS = [
  { id: 'order_imbalance', name: '订单失衡', nameEn: 'Order Imbalance', range: '-1 to 1' },
  { id: 'taker_buy_ratio', name: '主买比例', nameEn: 'Taker Buy Ratio', range: '-1 to 1' },
  { id: 'cvd', name: '累计成交量差', nameEn: 'CVD', range: 'number' },
  { id: 'oi_delta_percent', name: 'OI变化率', nameEn: 'OI Delta %', range: 'percent' },
  { id: 'funding_rate', name: '资金费率', nameEn: 'Funding Rate', range: 'percent' },
  { id: 'depth_ratio', name: '深度比率', nameEn: 'Depth Ratio', range: '0 to inf' },
  { id: 'rsi', name: 'RSI', nameEn: 'RSI', range: '0 to 100' },
  { id: 'macd_cross', name: 'MACD交叉', nameEn: 'MACD Cross', range: '-1, 0, 1' },
  { id: 'ema_cross', name: 'EMA交叉', nameEn: 'EMA Cross', range: '-1, 0, 1' },
  { id: 'bb_position', name: '布林带位置', nameEn: 'BB Position', range: '-2 to 2' },
  { id: 'atr_ratio', name: 'ATR比率', nameEn: 'ATR Ratio', range: '0 to inf' },
  { id: 'adx', name: 'ADX', nameEn: 'ADX', range: '0 to 100' },
  { id: 'price_above_ema', name: '价格在EMA上方', nameEn: 'Price Above EMA', range: '-1, 0, 1' },
  { id: 'trend_1h', name: '1H趋势', nameEn: '1H Trend', range: '-1, 0, 1' },
  { id: 'trend_4h', name: '4H趋势', nameEn: '4H Trend', range: '-1, 0, 1' }
]

// 可用运算符
export const AVAILABLE_OPERATORS = [
  { id: 'greater_than', name: '大于', nameEn: 'Greater Than', symbol: '>' },
  { id: 'less_than', name: '小于', nameEn: 'Less Than', symbol: '<' },
  { id: 'greater_than_or_equal', name: '大于等于', nameEn: 'Greater Than or Equal', symbol: '>=' },
  { id: 'less_than_or_equal', name: '小于等于', nameEn: 'Less Than or Equal', symbol: '<=' },
  { id: 'equals', name: '等于', nameEn: 'Equals', symbol: '=' },
  { id: 'abs_greater_than', name: '绝对值大于', nameEn: 'Abs Greater Than', symbol: '|x|>' }
]

// 可用时间窗口
export const AVAILABLE_TIME_WINDOWS = [
  { id: '1m', name: '1分钟', nameEn: '1 Minute' },
  { id: '3m', name: '3分钟', nameEn: '3 Minutes' },
  { id: '5m', name: '5分钟', nameEn: '5 Minutes' },
  { id: '15m', name: '15分钟', nameEn: '15 Minutes' },
  { id: '30m', name: '30分钟', nameEn: '30 Minutes' },
  { id: '1h', name: '1小时', nameEn: '1 Hour' },
  { id: '2h', name: '2小时', nameEn: '2 Hours' },
  { id: '4h', name: '4小时', nameEn: '4 Hours' }
]
