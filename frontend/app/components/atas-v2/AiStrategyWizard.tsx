/**
 * AI策略统一创建向导
 * 
 * 全新的联动式创建流程：
 * 1. 策略需求描述（用户用自然语言描述需求）
 * 2. AI生成策略框架（AI理解需求并生成策略大纲）
 * 3. AI生成信号定义（AI根据策略自动生成所需信号）
 * 4. 自动创建信号池（系统自动组织信号到信号池）
 * 5. 风险与执行配置（用户调整参数）
 * 6. 预览与确认（展示完整配置，一键创建）
 * 
 * 核心改进：
 * - 从用户需求出发，AI统一生成所有组件
 * - 策略、信号、信号池自动关联
 * - 保证数据流和逻辑的一致性
 * - 接入真实LLM调用，而非硬编码模板
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ChevronLeft, ChevronRight, Check, Plus, Trash2, Save, Loader2, Activity, Shield, TrendingUp, TrendingDown, Sparkles, AlertTriangle, Download, CheckCircle2 } from 'lucide-react';
import { toast } from 'react-hot-toast';
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs';

// V2: 市场环境预览数据结构
interface MarketPreview {
  market_env: {
    market_cycle: string;
    cycle_confidence: number;
    risk_budget_pct: number;
    volatility_regime: string;
    volatility_value: number;
    trend_direction: string;
    trend_strength: number;
    liquidity_score: number;
    // 数据溯源
    data_source?: string;
    kline_count?: number;
    current_price?: number;
    atr_value?: number;
    analysis_time?: string;
  };
  base_params: { stop_loss_pct: number; take_profit_pct: number; max_position_size: number };
  dynamic_params: {
    stop_loss_pct: number;
    stop_loss_type: string;
    tp_levels: { pct: number; close_ratio: number }[];
    trailing_stop: { enabled: boolean; activation_pct: number; distance_pct: number };
    time_stop_hours: number;
    position_size_pct: number;
  };
  adapted_multipliers: {
    sl_multiplier: number;
    tp_multiplier: number;
    position_scale: number;
    entry_threshold: number;
  };
  guidance: string;
}

interface WizardData {
  // 第1步：用户需求
  user_requirement: string;
  account_id: number;
  trading_style: string;
  target_symbols: string[];     // 用户选择的交易对列表，如 ["BTC", "ETH"]
  primary_symbol: string;       // 主要交易标的
  timeframe: string;            // 交易时间周期
  
  // 第2步：AI生成的策略框架
  strategy_name: string;
  strategy_description: string;
  strategy_logic: string;
  entry_conditions: string[];
  exit_conditions: string[];
  // 生成来源追踪
  generation_source: 'ai' | 'template' | '';
  generation_detail: string;
  market_data_used: boolean;
  confidence_note: string;
  
  // 第3步：AI生成的信号定义
  generated_signals: Array<{
    name: string;
    description: string;
    signal_type: string;
    calculation_logic: string;
    parameters: Record<string, any>;
  }>;
  
  // 第4步：自动创建的信号池
  signal_pool: {
    name: string;
    logic: string;
    signals: string[];
    weights?: Record<string, number>;
  };
  
  // 第5步：风险与执行配置（V2增强）
  risk_config: {
    max_position_size: number;
    stop_loss_pct: number;
    take_profit_pct: number;
    max_daily_loss: number;
    // V2 新增：动态风控模式
    dynamic_sl_enabled: boolean;    // 启用基于ATR的动态止损
    trailing_stop_enabled: boolean; // 启用移动止损
    time_stop_enabled: boolean;     // 启用时间止损
    time_stop_hours: number;        // 时间止损小时数
  };
  execution_config: {
    auto_execute: boolean;
    require_confirmation: boolean;
    min_confidence: number;
  };
  
  // 第6步
  complete_config?: any;
}

// ===== 内置交易风格定义 =====
interface TradingStyleOption {
  key: string;
  label: string;
  category: string; // 分组显示
}

const BUILT_IN_STYLES: TradingStyleOption[] = [
  // —— 趋势类 ——
  { key: 'trend', label: '趋势跟踪（Trend Following）', category: '趋势类' },
  { key: 'momentum', label: '动量交易（Momentum）', category: '趋势类' },
  { key: 'breakout', label: '突破交易（Breakout）', category: '趋势类' },
  { key: 'turtle', label: '海龟交易法（Turtle Trading）', category: '趋势类' },
  // —— 震荡类 ——
  { key: 'mean_reversion', label: '均值回归（Mean Reversion）', category: '震荡类' },
  { key: 'range', label: '区间震荡（Range Trading）', category: '震荡类' },
  { key: 'grid', label: '网格交易（Grid Trading）', category: '震荡类' },
  { key: 'scalping', label: '高频剥头皮（Scalping）', category: '震荡类' },
  // —— 策略类 ——
  { key: 'swing', label: '波段交易（Swing Trading）', category: '策略类' },
  { key: 'dca', label: '定投策略（DCA）', category: '策略类' },
  { key: 'martingale', label: '马丁格尔（Martingale）', category: '策略类' },
  // —— 套利类 ——
  { key: 'funding_rate', label: '资金费率套利（Funding Rate）', category: '套利类' },
  { key: 'arbitrage', label: '跨所套利（Arbitrage）', category: '套利类' },
];

const STRATEGY_TEMPLATES: Record<string, string> = {
  trend: `我想创建一个 BTC/USDT 趋势跟踪策略，15分钟周期。

【入场条件 - 做多】
1. EMA20 上穿 EMA50，确认短期趋势向上
2. MACD 柱状图由负转正（金叉确认）
3. RSI(14) 在 40-70 区间（避免追高）
4. 成交量大于20周期均量的1.2倍（量价配合）

【入场条件 - 做空】
1. EMA20 下穿 EMA50，确认短期趋势向下
2. MACD 柱状图由正转负（死叉确认）
3. RSI(14) 在 30-60 区间（避免追空）

【出场条件】
1. 止损：入场价的 3%
2. 止盈：入场价的 6%（盈亏比 2:1）
3. 移动止损：盈利超过 4% 后启用 1.5% 追踪止损
4. EMA20 反向穿越 EMA50 时强制平仓

【仓位管理】
每次开仓使用总资金的 20%，最大同时持仓 1 个，单日最大亏损 5% 停止交易。`,

  mean_reversion: `我想创建一个 BTC/USDT 均值回归策略，5分钟周期，适合震荡行情。

【核心指标】
1. 布林带：周期20，标准差2.0
2. RSI(14) 超买超卖判定
3. ATR(14) 用于动态止损计算

【入场条件 - 做多（超卖回归）】
1. 价格触及或跌破布林带下轨
2. RSI(14) < 30 进入超卖区
3. 出现看涨K线形态（锤子线/吞没）作为反转信号
4. 成交量出现放量（> 20周期均量 * 1.3）

【入场条件 - 做空（超买回归）】
1. 价格触及或突破布林带上轨
2. RSI(14) > 70 进入超买区
3. 出现看跌K线形态（射击之星/乌云盖顶）

【出场条件】
1. 止盈：价格回归至布林带中轨（EMA20）
2. 止损：1.5 倍 ATR（动态止损）
3. RSI 回到 40-60 中性区间平仓
4. 持仓超过 30 根K线未达止盈则强制平仓（防止横盘消耗）

【风险控制】
单次仓位 15%，止损不超过总资金的 2%，布林带开口过大（> 3%）时暂停交易（趋势行情不适用均值回归）。`,

  breakout: `我想创建一个 BTC/USDT 突破交易策略，15分钟周期。

【关键位识别】
1. 支撑位/阻力位：前 4 小时的最高价和最低价
2. 布林带上下轨作为动态突破参考
3. 前日的高点和低点作为关键水平位

【入场条件 - 向上突破做多】
1. 价格收盘突破阻力位（实体突破，非影线假突破）
2. 突破时成交量 > 20周期均量的 1.5 倍（量能确认）
3. MACD 在零轴上方或即将金叉（趋势支持）
4. 突破前价格在阻力位下方盘整至少 8 根K线（蓄力充分）

【入场条件 - 向下突破做空】
1. 价格收盘跌破支撑位
2. 突破时成交量放大
3. MACD 在零轴下方或即将死叉

【出场条件】
1. 初始止损：突破位回撤 1.5%（假突破保护）
2. 止盈目标：突破幅度的 2 倍（按盘整区间宽度计算）
3. 追踪止损：盈利 > 2% 后启用 1% 追踪止损锁定利润
4. 若突破后 6 根K线内未能持续走强，视为假突破平仓

【过滤条件】
波动率过低时（ATR < 近期均值的 0.5 倍）不开仓，避免无效突破。单次仓位 20%，每日最多交易 3 次。`,

  grid: `我想创建一个 BTC/USDT 网格交易策略，适合横盘震荡行情。

【网格参数设计】
1. 价格区间：基于近 24 小时最高价和最低价自动计算
2. 网格数量：10 层（每层等间距）
3. 每格资金：总资金的 8%（预留 20% 作为安全垫）
4. 网格间距：根据 ATR(24H) 动态调整，通常为价格的 0.3%-0.5%

【交易逻辑】
1. 价格每下跌一格，在该格位买入一份
2. 价格每上涨一格，卖出对应格位的持仓
3. 每次成交赚取一格的价差利润

【入场条件】
1. 布林带带宽 < 3%（确认横盘状态）
2. ADX(14) < 25（无明显趋势）
3. RSI(14) 在 35-65 区间（中性区域）

【风险控制】
1. 最大持仓：总资金的 80%（满格持仓上限）
2. 整体止损：当浮亏达到总资金 10% 时全部平仓离场
3. 趋势保护：若价格连续突破 3 格未回调，暂停新开仓（可能变趋势）
4. 网格重置：当价格突破区间上下限 2% 时，重新计算网格区间

【适用场景】
震荡市使用，当 ADX > 30 或布林带开口 > 4% 时自动暂停网格，避免趋势行情中逆势加仓。`,

  momentum: `我想创建一个 BTC/USDT 动量交易策略，15分钟周期。

【核心理念】
跟随市场动量方向，强者恒强。不预测顶底，只做动能最强的方向。

【入场条件 - 做多】
1. RSI(14) 在 55-80 区间（动量向上但未极端超买）
2. 价格突破 20 周期最高价
3. 成交量 > 20周期均量的 1.8 倍（动量爆发）
4. OBV（能量潮）趋势向上

【入场条件 - 做空】
1. RSI(14) 在 20-45 区间（动量向下）
2. 价格跌破 20 周期最低价
3. 成交量放大确认

【出场条件】
1. 止损：2.5% 固定止损
2. 止盈：5%（盈亏比 2:1）
3. RSI 进入超买区(>80)或超卖区(<20) 时逐步减仓
4. 追踪止损：盈利超过 3% 后启用 1.5% 追踪

【仓位管理】
单次仓位 20%，最大同时持仓 2 个，动量衰减时（成交量连续 3 根K线下降）禁止新开仓。`,

  turtle: `我想创建一个 BTC/USDT 海龟交易策略，1小时周期。

【核心规则 - 唐奇安通道突破】
1. 入场通道：20周期最高价/最低价
2. 离场通道：10周期最低价/最高价
3. ATR(20) 用于仓位计算和止损

【入场条件 - 做多】
1. 价格突破 20周期最高价（通道上轨）
2. 前一次突破信号未产生盈利（只在前次失败后入场）
3. 成交量确认（> 20周期均量 * 1.3）

【入场条件 - 做空】
1. 价格跌破 20周期最低价（通道下轨）
2. 同样需要前次失败过滤

【出场条件】
1. 做多离场：价格跌破 10周期最低价
2. 做空离场：价格突破 10周期最高价
3. 止损：2 倍 ATR(20)

【仓位管理 - 金字塔加仓】
初始仓位：总资金 / (2 * ATR * 合约乘数)。每突破 0.5 倍 ATR 加仓一次，最多加 3 次。所有仓位总风险不超总资金 10%。`,

  range: `我想创建一个 BTC/USDT 区间震荡策略，5分钟周期。

【区间识别】
1. 布林带带宽 < 2.5%（横盘确认）
2. ADX(14) < 20（无趋势）
3. 取近 50 根K线的最高价和最低价作为区间上下沿

【入场条件 - 做多】
1. 价格触及区间下沿（下沿附近 0.5% 范围）
2. RSI(14) < 35
3. 随机指标 KDJ 的 K 值 < 20
4. 出现反转K线形态（单根大阳/锤子线）

【入场条件 - 做空】
1. 价格触及区间上沿（上沿附近 0.5% 范围）
2. RSI(14) > 65
3. KDJ 的 K 值 > 80

【出场条件】
1. 止盈：价格回到区间中位
2. 止损：突破区间上下沿 1.5%（真正突破→离场）
3. 持仓超过 20 根K线强制平仓

【风险控制】
单次仓位 15%，ADX 突破 25 时暂停策略（可能开始趋势），每日最多交易 5 次。`,

  scalping: `我想创建一个 BTC/USDT 高频剥头皮策略，1分钟周期。

【核心理念】
快进快出，赚取微小价差，胜率优先，每笔盈利目标 0.3%-0.8%。

【入场条件 - 做多】
1. EMA5 上穿 EMA13（超短期动量）
2. VWAP 支撑（价格在 VWAP 上方）
3. 买盘深度 > 卖盘深度 * 1.2（订单流偏多）
4. 1分钟成交量 > 均量 1.5 倍

【入场条件 - 做空】
1. EMA5 下穿 EMA13
2. 价格在 VWAP 下方
3. 卖盘深度 > 买盘深度 * 1.2

【出场条件】
1. 止盈：0.5%（快速收割）
2. 止损：0.3%（严格止损，盈亏比约 1.6:1）
3. 持仓超过 5 根K线无论盈亏均离场
4. EMA 反向交叉立即平仓

【风险控制】
单次仓位 10%，每日最多交易 20 次，连续亏损 3 次暂停 10 分钟，单日最大亏损 3% 停止交易。
ℹ️ 注意：剥头皮对手续费敏感，请确保使用低手续费账户。`,

  swing: `我想创建一个 BTC/USDT 波段交易策略，4小时周期。

【核心理念】
捕捉中期波段（持仓 1-7 天），结合多时间周期分析确认方向。

【多周期分析】
1. 日线级别：确定主趋势方向（EMA50 与 EMA200 关系）
2. 4小时级别：寻找入场时机
3. 只顺大周期方向交易

【入场条件 - 做多】
1. 日线 EMA50 > EMA200（多头市）
2. 4H 价格回调至 EMA20 附近获得支撑
3. 4H RSI(14) 在 40-55 区间（回调位）
4. 出现看涨形态（锤子线/看涨吞没/双底）

【入场条件 - 做空】
1. 日线 EMA50 < EMA200（空头市）
2. 4H 价格反弹至 EMA20 附近受阻
3. 出现看跌形态

【出场条件】
1. 止损：入场价的 4%
2. 止盈：入场价的 10-15%（盈亏比 3:1）
3. 追踪止损：盈利 > 6% 后启用 3% 追踪止损
4. 日线趋势反转时强制平仓

【仓位管理】
单次仓位 25%，最大同时持仓 2 个，单日最大亏损 8% 停止交易。`,

  dca: `我想创建一个 BTC/USDT 智能定投策略，日线周期。

【核心理念】
定期定额买入 + 智能加仓（下跌越多买越多），降低平均成本，适合长期稳健策略。

【定投规则】
1. 基础定投：每天固定时间买入总资金的 2%
2. 智能加仓：
   - 跌 5%：买入 3%（加倍）
   - 跌 10%：买入 5%（加倍）
   - 跌 20%：买入 8%（大幅加仓）

【入场条件】
1. 每日固定时间执行基础定投
2. 智能加仓条件：价格相对 30 日均线下跌超过阈值
3. 恐慌贪婪指数 < 30 时触发加仓（如可获取）

【出场/止盈条件】
1. 总持仓盈利 > 30%：卖出 30% 持仓收回成本
2. 总持仓盈利 > 50%：卖出 50% 持仓锁定利润
3. RSI(日线) > 85：极端超买清仓

【风险控制】
最大总投入不超过账户总资金的 80%，预留 20% 现金应对极端行情。不使用杠杆。`,

  martingale: `我想创建一个 BTC/USDT 改良型马丁格尔策略，15分钟周期。

【核心理念】
亏损后加倍加仓，利用一次盈利覆盖前面所有亏损。改良版限制最大加仓次数控制风险。

【加仓规则】
1. 第1单：总资金的 5%
2. 第1次亏损加仓：10%（加倍）
3. 第2次亏损加仓：20%（再加倍）
4. 第3次亏损加仓：25%（最后一次）
5. 最多加仓 3 次（总计 4 单，防止无限加仓爆仓）

【入场条件】
1. EMA20 > EMA50（只顺趋势方向开第一单）
2. RSI(14) 在 40-60 中性区间
3. 加仓触发：价格继续向不利方向移动 1.5%

【出场条件】
1. 止盈：平均持仓成本 + 2%（覆盖所有亏损 + 利润）
2. 最终止损：4单全部开出后价格再跌 3%，全部清仓认亏
3. 趋势反转（EMA20 反穿 EMA50）时全部平仓

【风险警告】
ℹ️ 马丁格尔策略风险较高，建议小资金测试。最大总投入限制在 60%，防止单边行情爆仓。`,

  funding_rate: `我想创建一个 BTC/USDT 资金费率套利策略。

【核心理念】
利用永续合约的资金费率机制套利。当资金费率极端偏离时，收取资金费而非下注方向。

【监控指标】
1. 当前资金费率（每 8 小时结算一次）
2. 预测资金费率
3. 持仓量偏移（多空比）
4. 市场情绪指标

【入场条件 - 做空收费率】
1. 资金费率 > +0.1%（异常高，多头付费）
2. 预测资金费率同样偏高
3. 在资金费结算前 30 分钟开空单

【入场条件 - 做多收费率】
1. 资金费率 < -0.1%（异常低，空头付费）
2. 预测资金费率同样偏低
3. 在资金费结算前 30 分钟开多单

【出场条件】
1. 资金费结算完成后立即平仓
2. 止损：价格波动超过 1%（空场波动损失 > 资金费收入）
3. 资金费率回归正常范围（|rate| < 0.03%）取消开仓

【风险控制】
单次仓位 30%，低杠杆（2-3x），严格在结算后平仓，避免持仓过夜承受方向性风险。`,

  arbitrage: `我想创建一个跨交易所套利策略。

【核心理念】
监控同一币种在不同交易所的价差，当价差超过交易成本时执行套利。

【监控指标】
1. 主所 A 价格（实时 BTC/USDT）
2. 主所 B 价格（实时 BTC/USDT）
3. 价差百分比 = |价格A - 价格B| / 平均价 * 100
4. 两所的买一卖一价差（盘口深度）

【入场条件】
1. 价差百分比 > 0.5%（覆盖双边手续费）
2. 两交易所盘口深度足够（避免滑点）
3. 价差持续超过 3 秒（避免瞬间报价错误）
4. 在价格低的所买入，在价格高的所卖出

【出场条件】
1. 价差收窄至 < 0.1%（利润已经实现）
2. 超时：开仓后 5 分钟内价差未收窄则平仓
3. 任一所的价格反向超过 0.3%

【风险控制】
单次仓位 20%，必须同时在两所开仓对冲，避免单边暂露。考虑提币延迟和网络风险。`,
};

const INITIAL_DATA: WizardData = {
  user_requirement: '',
  account_id: 0,
  trading_style: 'trend',
  target_symbols: ['BTC'],
  primary_symbol: 'BTC',
  timeframe: '15m',
  strategy_name: '',
  strategy_description: '',
  strategy_logic: '',
  entry_conditions: [],
  exit_conditions: [],
  generation_source: '',
  generation_detail: '',
  market_data_used: false,
  confidence_note: '',
  generated_signals: [],
  signal_pool: {
    name: '',
    logic: 'AND',
    signals: [],
  },
  risk_config: {
    max_position_size: 0.2,
    stop_loss_pct: 0.05,
    take_profit_pct: 0.10,
    max_daily_loss: 0.10,
    dynamic_sl_enabled: true,
    trailing_stop_enabled: true,
    time_stop_enabled: true,
    time_stop_hours: 72,
  },
  execution_config: {
    auto_execute: false,
    require_confirmation: true,
    min_confidence: 0.6,
  },
};

// V2: 市场周期/波动率显示配置
const cycleLabels: Record<string, { text: string; color: string }> = {
  bull: { text: '牛市', color: 'text-green-600' },
  bear: { text: '熊市', color: 'text-red-600' },
  sideways: { text: '震荡', color: 'text-yellow-600' },
  unknown: { text: '数据不足', color: 'text-gray-500' },
  transition: { text: '转换期', color: 'text-purple-600' },
};
const volLabels: Record<string, { text: string; color: string }> = {
  low: { text: '低', color: 'text-blue-600' },
  normal: { text: '正常', color: 'text-gray-600' },
  high: { text: '高', color: 'text-orange-600' },
  extreme: { text: '极端', color: 'text-red-600' },
};

export default function AiStrategyWizard() {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>(INITIAL_DATA);
  const [submitting, setSubmitting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [customStyles, setCustomStyles] = useState<Array<{ id: number; key: string; name: string; description: string; template: string }>>([]);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [customName, setCustomName] = useState('');
  const [customDesc, setCustomDesc] = useState('');
  const [customTemplate, setCustomTemplate] = useState('');
  const [savingCustom, setSavingCustom] = useState(false);
  
  // V2 新增状态
  const [marketPreview, setMarketPreview] = useState<MarketPreview | null>(null);
  const [loadingMarket, setLoadingMarket] = useState(false);
  
  // 交易对选择状态
  const [availableSymbols, setAvailableSymbols] = useState<string[]>([]);
  const [loadingSymbols, setLoadingSymbols] = useState(false);
  const [customSymbolInput, setCustomSymbolInput] = useState('');
  const [symbolDataMap, setSymbolDataMap] = useState<Record<string, { sufficient: boolean; coverage: number; syncing?: boolean }>>({});

  const totalSteps = 6;

  const { symbols: configuredPairs } = useTradingPairs();
  const POPULAR_SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS;
  
  // 时间周期选项
  const TIMEFRAME_OPTIONS = [
    { value: '1m', label: '1分钟' },
    { value: '5m', label: '5分钟' },
    { value: '15m', label: '15分钟' },
    { value: '30m', label: '30分钟' },
    { value: '1h', label: '1小时' },
    { value: '4h', label: '4小时' },
    { value: '1d', label: '日线' },
  ];

  // 加载账户列表 + 自定义风格 + 可用交易对
  useEffect(() => {
    fetch('/api/account/list', { signal: AbortSignal.timeout(2000) })
      .then(res => res.ok ? res.json() : [])
      .then(d => setAccounts(d))
      .catch(() => setAccounts([]));
    loadCustomStyles();
    loadAvailableSymbols();
  }, []);
  
  // 加载可用交易对列表（从 Hyperliquid 获取）
  const loadAvailableSymbols = async () => {
    setLoadingSymbols(true);
    try {
      const res = await fetch('/api/hyperliquid/symbols/available', { signal: AbortSignal.timeout(5000) });
      if (res.ok) {
        const data = await res.json();
        // data 格式：{ symbols: [{symbol: "BTC", name: "Bitcoin"}, ...] } 或 ["BTC", "ETH"]
        let symbols: string[] = [];
        if (Array.isArray(data)) {
          symbols = data.map((s: any) => typeof s === 'string' ? s : s.symbol);
        } else if (data?.symbols) {
          symbols = data.symbols.map((s: any) => typeof s === 'string' ? s : s.symbol);
        }
        if (symbols.length > 0) {
          setAvailableSymbols(symbols);
          return;
        }
      }
    } catch (e) {
      console.warn('Failed to load symbols from API, using defaults');
    }
    // fallback：使用常用列表
    setAvailableSymbols(POPULAR_SYMBOLS);
    setLoadingSymbols(false);
  };

  const checkAndSyncSymbol = useCallback(async (sym: string) => {
    try {
      const res = await fetch(`/api/klines/history-sync/check-symbol?symbol=${encodeURIComponent(sym)}`, { signal: AbortSignal.timeout(10000) });
      if (!res.ok) return;
      const info = await res.json();
      setSymbolDataMap(prev => ({ ...prev, [sym]: { sufficient: info.sufficient, coverage: info.overall_coverage } }));

      if (!info.sufficient) {
        setSymbolDataMap(prev => ({ ...prev, [sym]: { ...prev[sym], syncing: true } }));
        toast(`${sym} 缺少历史数据，自动同步中...`, { icon: '📡', duration: 3000 });
        try {
          const syncRes = await fetch('/api/klines/history-sync/quick-sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, days: 365 }),
          });
          if (syncRes.ok) {
            const result = await syncRes.json();
            toast.success(`${sym} 历史数据同步完成: +${result.total_collected?.toLocaleString() || 0} 条`);
            setSymbolDataMap(prev => ({ ...prev, [sym]: { sufficient: true, coverage: 100, syncing: false } }));
          } else {
            setSymbolDataMap(prev => ({ ...prev, [sym]: { ...prev[sym], syncing: false } }));
          }
        } catch {
          setSymbolDataMap(prev => ({ ...prev, [sym]: { ...prev[sym], syncing: false } }));
        }
      }
    } catch {
      // ignore
    }
  }, []);

  // 添加自定义交易对（支持逗号/空格/回车分隔批量添加）
  const handleAddCustomSymbol = () => {
    const raw = customSymbolInput.trim();
    if (!raw) return;
    
    const parts = raw.split(/[,，\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    const existing = new Set(data.target_symbols);
    const added: string[] = [];
    
    for (const sym of parts) {
      if (/^[A-Z0-9]{2,10}$/.test(sym) && !existing.has(sym)) {
        existing.add(sym);
        added.push(sym);
      }
    }
    
    if (added.length > 0) {
      const newSymbols = [...data.target_symbols, ...added];
      const updates: Partial<WizardData> = { target_symbols: newSymbols };
      if (data.target_symbols.length === 0) {
        updates.primary_symbol = newSymbols[0];
      }
      updateData(updates);
      toast.success(`已添加: ${added.join(', ')}`);
      added.forEach(sym => checkAndSyncSymbol(sym));
    } else if (parts.length > 0) {
      toast.error('交易对已存在或格式不正确（需2~10位字母数字）');
    }
    
    setCustomSymbolInput('');
  };

  const loadCustomStyles = () => {
    fetch('/api/ai-strategies/trading-styles/custom', { signal: AbortSignal.timeout(3000) })
      .then(res => res.ok ? res.json() : [])
      .then(d => setCustomStyles(d))
      .catch(() => setCustomStyles([]));
  };

  // V2: 加载市场环境预览（选择账户后 + 进入步骤5时触发）
  const loadMarketPreview = async (accountId?: number) => {
    const aid = accountId || data.account_id;
    if (!aid) return;
    setLoadingMarket(true);
    try {
      const params = new URLSearchParams({
        account_id: aid.toString(),
        symbol: data.primary_symbol || 'BTC',
        stop_loss_pct: data.risk_config.stop_loss_pct.toString(),
        take_profit_pct: data.risk_config.take_profit_pct.toString(),
        max_position_size: data.risk_config.max_position_size.toString(),
      });
      const res = await fetch(`/api/ai-strategies/wizard/market-preview?${params}`, {
        signal: AbortSignal.timeout(10000),
      });
      if (res.ok) {
        setMarketPreview(await res.json());
      }
    } catch (e) {
      console.error('Market preview load error:', e);
    } finally {
      setLoadingMarket(false);
    }
  };

  const handleSaveCustomStyle = async () => {
    if (!customName.trim()) { toast.error('请输入风格名称'); return; }
    setSavingCustom(true);
    try {
      const res = await fetch('/api/ai-strategies/trading-styles/custom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: customName, description: customDesc, template: customTemplate }),
      });
      if (!res.ok) throw new Error('Failed');
      const created = await res.json();
      setCustomStyles(prev => [...prev, created]);
      // 自动选中新创建的风格
      updateData({ trading_style: created.key });
      if (created.template) updateData({ user_requirement: created.template });
      setShowCustomForm(false);
      setCustomName(''); setCustomDesc(''); setCustomTemplate('');
      toast.success('自定义风格保存成功！');
    } catch { toast.error('保存失败，请重试'); }
    finally { setSavingCustom(false); }
  };

  const handleDeleteCustomStyle = async (id: number) => {
    if (!confirm('确定删除这个自定义风格？')) return;
    await fetch(`/api/ai-strategies/trading-styles/custom/${id}`, { method: 'DELETE' });
    setCustomStyles(prev => prev.filter(s => s.id !== id));
    if (customStyles.find(s => s.id === id)?.key === data.trading_style) {
      updateData({ trading_style: 'trend' });
    }
    toast.success('已删除');
  };

  // AI生成策略框架（真实LLM调用，带超时和进度提示）
  const generateStrategyFramework = async () => {
    if (!data.user_requirement.trim()) {
      toast.error('请先输入策略需求');
      return;
    }
    if (!data.account_id) {
      toast.error('请先选择交易账户');
      return;
    }

    setGenerating(true);
    const loadingToast = toast.loading('AI 正在分析市场数据并生成策略框架，预计需要 10-30 秒...');
    
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000); // 120秒超时
      
      const response = await fetch('/api/ai-strategies/generate-framework', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_requirement: data.user_requirement,
          trading_style: data.trading_style,
          account_id: data.account_id,
          target_symbols: data.target_symbols,
          primary_symbol: data.primary_symbol,
          timeframe: data.timeframe,
        }),
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        throw new Error(errorData?.detail || `AI生成失败 (${response.status})`);
      }

      const result = await response.json();
      updateData({
        strategy_name: result.strategy_name,
        strategy_description: result.strategy_description,
        strategy_logic: result.strategy_logic,
        entry_conditions: result.entry_conditions,
        exit_conditions: result.exit_conditions,
        generation_source: result.generation_source || 'template',
        generation_detail: result.generation_detail || '',
        market_data_used: result.market_data_used || false,
        confidence_note: result.confidence_note || '',
      });
      
      if (result.generation_source === 'ai') {
        toast.success(`AI 策略生成成功（${result.generation_detail}）`, { id: loadingToast });
      } else {
        toast.error(`AI 不可用，已使用预设模板。原因: ${result.generation_detail || '未知'}`, { id: loadingToast, duration: 8000 });
      }
    } catch (error: any) {
      console.error('Generate framework error:', error);
      if (error.name === 'AbortError') {
        toast.error('AI 生成超时，请稍后重试', { id: loadingToast });
      } else {
        toast.error(error.message || 'AI生成失败，请重试', { id: loadingToast });
      }
    } finally {
      setGenerating(false);
    }
  };

  // AI生成信号定义（真实LLM调用）
  const generateSignals = async () => {
    if (!data.strategy_logic) {
      toast.error('请先生成策略框架');
      return;
    }
    
    setGenerating(true);
    const loadingToast = toast.loading('AI 正在根据策略逻辑生成信号定义，预计需要 10-30 秒...');
    
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);
      
      const response = await fetch('/api/ai-strategies/generate-signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_logic: data.strategy_logic,
          entry_conditions: data.entry_conditions,
          exit_conditions: data.exit_conditions,
        }),
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        throw new Error(errorData?.detail || `AI生成失败 (${response.status})`);
      }

      const result = await response.json();
      
      // 自动将所有生成的信号加入信号池
      if (result.signals?.length > 0) {
        const signalNames = result.signals.map((s: any) => s.name);
        updateData({
          generated_signals: result.signals,
          signal_pool: {
            ...data.signal_pool,
            name: data.signal_pool.name || `${data.strategy_name}_信号池`,
            signals: signalNames,
            logic: result.signal_pool_logic || 'AND',
          }
        });
      } else {
        updateData({ generated_signals: result.signals || [] });
      }
      
      toast.success(`成功生成 ${result.signals?.length || 0} 个信号！`, { id: loadingToast });
    } catch (error: any) {
      console.error('Generate signals error:', error);
      if (error.name === 'AbortError') {
        toast.error('AI 生成超时，请稍后重试', { id: loadingToast });
      } else {
        toast.error(error.message || 'AI信号生成失败，请重试', { id: loadingToast });
      }
    } finally {
      setGenerating(false);
    }
  };

  const updateData = (updates: Partial<WizardData>) => {
    setData(prev => ({ ...prev, ...updates }));
  };

  const handleNext = () => {
    // 步骤校验
    if (step === 1) {
      if (!data.user_requirement.trim()) {
        toast.error('请先输入策略需求描述');
        return;
      }
      if (!data.account_id) {
        toast.error('请先选择交易账户');
        return;
      }
      if (data.target_symbols.length === 0) {
        toast.error('请至少选择一个交易对');
        return;
      }
    }
    if (step < totalSteps) {
      const nextStep = step + 1;
      setStep(nextStep);
      // V2: 进入风控步骤时刷新市场预览
      if (nextStep === 5) {
        loadMarketPreview();
      }
    }
  };

  const handlePrev = () => {
    if (step > 1) {
      setStep(step - 1);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    const loadingToast = toast.loading('正在创建策略系统...');
    try {
      // 统一创建：策略 + 信号 + 信号池
      const response = await fetch('/api/ai-strategies/create-complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        throw new Error(errorData?.detail || '创建失败');
      }

      const result = await response.json();
      toast.success(`策略系统创建成功！策略ID: ${result.strategy_id}`, { id: loadingToast });
      window.location.href = '/atas/ai-strategies';
    } catch (error: any) {
      console.error('Create error:', error);
      toast.error(error.message || '创建失败，请检查输入', { id: loadingToast });
    } finally {
      setSubmitting(false);
    }
  };

  const renderStep = () => {
    switch (step) {
      // 第1步：用户需求描述
      case 1:
        return (
          <div className="space-y-4">
            <div className="bg-blue-50 p-4 rounded-md border border-blue-200">
              <h3 className="font-semibold text-blue-900 mb-2">AI将帮您创建完整的策略系统</h3>
              <p className="text-sm text-blue-700">请用自然语言描述您的交易需求，AI将自动生成策略、信号和信号池</p>
            </div>
            
            <div>
              <Label>选择交易账户 *</Label>
              <select
                className="w-full px-4 py-2 border rounded-md"
                value={data.account_id || ''}
                onChange={(e) => {
                  const id = parseInt(e.target.value) || 0;
                  updateData({ account_id: id });
                  if (id) loadMarketPreview(id);
                }}
              >
                <option value="">请选择账户</option>
                {accounts.map(acc => (
                  <option key={acc.id} value={acc.id}>
                    {acc.name} ({acc.account_type || '未知'})
                  </option>
                ))}
              </select>
              {accounts.length === 0 && (
                <p className="text-xs text-gray-500 mt-1">正在加载账户列表...</p>
              )}
            </div>

            {/* 交易对选择 */}
            <div>
              <Label className="flex items-center gap-2">
                交易对选择 *
                <span className="text-xs font-normal text-gray-400">
                  已选 {data.target_symbols.length} 个
                  {data.target_symbols.length > 1 && `，主交易对: ${data.primary_symbol}`}
                </span>
              </Label>
              <div className="mt-2 border rounded-lg p-3 bg-white dark:bg-gray-900 space-y-3">
                {/* 自定义输入框 */}
                <div className="flex gap-2">
                  <Input
                    placeholder="输入交易对名称，如 BTC、ETH、DOGE …"
                    className="flex-1 text-sm"
                    value={customSymbolInput}
                    onChange={(e) => setCustomSymbolInput(e.target.value.toUpperCase())}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleAddCustomSymbol();
                      }
                    }}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleAddCustomSymbol}
                    disabled={!customSymbolInput.trim()}
                  >
                    <Plus className="w-3.5 h-3.5 mr-1" />
                    添加
                  </Button>
                </div>

                {/* 已选交易对标签 */}
                <div className="flex flex-wrap gap-1.5 min-h-[32px]">
                  {data.target_symbols.length === 0 && (
                    <span className="text-xs text-gray-400 py-1">请至少选择一个交易对</span>
                  )}
                  {data.target_symbols.map(sym => (
                    <span
                      key={sym}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium transition-all cursor-pointer
                        ${sym === data.primary_symbol 
                          ? 'bg-blue-100 text-blue-800 border-2 border-blue-400 dark:bg-blue-900/40 dark:text-blue-300' 
                          : 'bg-gray-100 text-gray-700 border border-gray-300 dark:bg-gray-800 dark:text-gray-300 hover:bg-gray-200'
                        }`}
                      onClick={() => updateData({ primary_symbol: sym })}
                      title={sym === data.primary_symbol ? '主交易对（点击其他币种可切换）' : '点击设为主交易对'}
                    >
                      {sym === data.primary_symbol && <span className="text-[10px]">★</span>}
                      {symbolDataMap[sym]?.syncing && <Loader2 className="h-3 w-3 animate-spin" />}
                      {sym}
                      {symbolDataMap[sym] && !symbolDataMap[sym].syncing && (
                        symbolDataMap[sym].sufficient 
                          ? <CheckCircle2 className="h-2.5 w-2.5 text-green-500" />
                          : <AlertTriangle className="h-2.5 w-2.5 text-yellow-500" title={`数据覆盖率 ${symbolDataMap[sym].coverage}%`} />
                      )}
                      <button
                        className="ml-0.5 text-gray-400 hover:text-red-500"
                        onClick={(e) => {
                          e.stopPropagation();
                          const newSymbols = data.target_symbols.filter(s => s !== sym);
                          const updates: Partial<WizardData> = { target_symbols: newSymbols };
                          if (sym === data.primary_symbol && newSymbols.length > 0) {
                            updates.primary_symbol = newSymbols[0];
                          }
                          updateData(updates);
                        }}
                        title="移除"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
                
                {/* 快速选择常用交易对 */}
                <div>
                  <div className="text-[10px] text-gray-400 mb-1.5">快速选择：</div>
                  <div className="flex flex-wrap gap-1.5">
                    {(availableSymbols.length > 0 ? availableSymbols : POPULAR_SYMBOLS).slice(0, 24).map(sym => {
                      const isSelected = data.target_symbols.includes(sym);
                      return (
                        <button
                          key={sym}
                          type="button"
                          className={`px-2 py-0.5 text-xs rounded border transition-all
                            ${isSelected 
                              ? 'bg-blue-50 border-blue-300 text-blue-700 dark:bg-blue-950 dark:border-blue-600 dark:text-blue-400' 
                              : 'bg-white border-gray-200 text-gray-600 hover:border-blue-300 hover:text-blue-600 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-400'
                            }`}
                          onClick={() => {
                            if (isSelected) {
                              const newSymbols = data.target_symbols.filter(s => s !== sym);
                              const updates: Partial<WizardData> = { target_symbols: newSymbols };
                              if (sym === data.primary_symbol && newSymbols.length > 0) {
                                updates.primary_symbol = newSymbols[0];
                              }
                              updateData(updates);
                            } else {
                              const newSymbols = [...data.target_symbols, sym];
                              const updates: Partial<WizardData> = { target_symbols: newSymbols };
                              if (data.target_symbols.length === 0) {
                                updates.primary_symbol = sym;
                              }
                              updateData(updates);
                              checkAndSyncSymbol(sym);
                            }
                          }}
                        >
                          {symbolDataMap[sym]?.syncing && <Loader2 className="h-3 w-3 animate-spin inline mr-0.5" />}
                          {isSelected ? '✓ ' : ''}{sym}
                          {symbolDataMap[sym] && !symbolDataMap[sym].syncing && (
                            symbolDataMap[sym].sufficient 
                              ? <CheckCircle2 className="h-3 w-3 inline ml-0.5 text-green-500" />
                              : <AlertTriangle className="h-3 w-3 inline ml-0.5 text-yellow-500" />
                          )}
                        </button>
                      );
                    })}
                    {loadingSymbols && <Loader2 className="w-3 h-3 animate-spin text-gray-400" />}
                  </div>
                </div>
                <p className="text-[10px] text-gray-400">
                  输入框支持自定义交易对 · 点击快速选择添加常用币种 · ★ 标记主交易对 · 点击已选标签切换主交易对
                </p>
              </div>
            </div>
            
            {/* 时间周期选择 */}
            <div>
              <Label>交易时间周期</Label>
              <select
                className="w-full px-4 py-2 border rounded-md"
                value={data.timeframe}
                onChange={(e) => updateData({ timeframe: e.target.value })}
              >
                {TIMEFRAME_OPTIONS.map(tf => (
                  <option key={tf.value} value={tf.value}>{tf.label}</option>
                ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">策略分析和信号检测使用的K线周期</p>
            </div>

            {/* V2: 市场环境实时面板 */}
            {data.account_id > 0 && (
              <div className="border rounded-lg overflow-hidden">
                <div className="bg-slate-50 dark:bg-slate-800 px-4 py-2 flex items-center gap-2 border-b">
                  <Activity className="w-4 h-4 text-blue-500" />
                  <span className="text-sm font-medium">当前市场环境</span>
                  {loadingMarket && <Loader2 className="w-3 h-3 animate-spin text-gray-400 ml-auto" />}
                </div>
                {marketPreview ? (
                  <div className="p-4 space-y-3">
                    <div className="grid grid-cols-4 gap-3 text-center text-sm">
                      <div>
                        <div className="text-xs text-gray-500">市场周期</div>
                        <div className={`font-bold flex items-center justify-center gap-1 ${(cycleLabels[marketPreview.market_env.market_cycle] || cycleLabels.unknown).color}`}>
                          {loadingMarket && (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          )}
                          {(cycleLabels[marketPreview.market_env.market_cycle] || cycleLabels.unknown).text}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">波动率</div>
                        <div className={`font-bold ${(volLabels[marketPreview.market_env.volatility_regime] || volLabels.normal).color}`}>
                          {(volLabels[marketPreview.market_env.volatility_regime] || volLabels.normal).text}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">趋势</div>
                        <div className={`font-bold ${
                          marketPreview.market_env.trend_direction === 'bullish' ? 'text-green-600' :
                          marketPreview.market_env.trend_direction === 'bearish' ? 'text-red-600' : 'text-gray-600'
                        }`}>
                          {marketPreview.market_env.trend_direction === 'bullish' ? '看多' :
                           marketPreview.market_env.trend_direction === 'bearish' ? '看空' : '中性'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">风险预算</div>
                        <div className="font-bold">{(marketPreview.market_env.risk_budget_pct * 100).toFixed(0)}%</div>
                      </div>
                    </div>
                    {marketPreview.guidance && (
                      <div className="text-xs text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30 rounded p-2">
                        <strong>AI 建议：</strong>{marketPreview.guidance}
                      </div>
                    )}
                    {/* 数据溯源信息 */}
                    <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500 border-t pt-2 mt-1">
                      <span className="flex items-center gap-1">
                        {marketPreview.market_env.data_source === 'default' ? (
                          <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
                        ) : (
                          <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400" />
                        )}
                        数据来源: {
                          marketPreview.market_env.data_source === 'market_adaptor' ? '市场适配器' :
                          marketPreview.market_env.data_source === 'kline_analysis' ? 'K线实时分析' :
                          marketPreview.market_env.data_source === 'market_data_analyzer' ? '市场分析器' :
                          '默认值'
                        }
                      </span>
                      {(marketPreview.market_env.kline_count ?? 0) > 0 && (
                        <span>K线: {marketPreview.market_env.kline_count}条</span>
                      )}
                      {(marketPreview.market_env.current_price ?? 0) > 0 && (
                        <span>BTC: ${marketPreview.market_env.current_price?.toLocaleString()}</span>
                      )}
                      {marketPreview.market_env.analysis_time && (
                        <span className="ml-auto">{marketPreview.market_env.analysis_time}</span>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="p-4 text-xs text-gray-400 text-center flex items-center justify-center gap-2">
                    {loadingMarket && <Loader2 className="w-4 h-4 animate-spin text-blue-400" />}
                    {loadingMarket ? '正在分析市场环境...' : '选择账户后自动分析'}
                  </div>
                )}
              </div>
            )}

            <div>
              <Label>交易风格 *</Label>
              <select
                className="w-full px-4 py-2 border rounded-md"
                value={data.trading_style}
                onChange={(e) => {
                  const style = e.target.value;
                  if (style === '__custom_new__') {
                    setShowCustomForm(true);
                    return;
                  }
                  updateData({ trading_style: style });
                  // 内置模板
                  const tpl = STRATEGY_TEMPLATES[style];
                  if (tpl) { updateData({ user_requirement: tpl }); return; }
                  // 自定义风格模板
                  const cs = customStyles.find(s => s.key === style);
                  if (cs?.template) updateData({ user_requirement: cs.template });
                }}
              >
                {/* 分组显示内置风格 */}
                {['趋势类', '震荡类', '策略类', '套利类'].map(cat => (
                  <optgroup key={cat} label={cat}>
                    {BUILT_IN_STYLES.filter(s => s.category === cat).map(s => (
                      <option key={s.key} value={s.key}>{s.label}</option>
                    ))}
                  </optgroup>
                ))}
                {/* 自定义风格 */}
                {customStyles.length > 0 && (
                  <optgroup label="我的自定义风格">
                    {customStyles.map(s => (
                      <option key={s.key} value={s.key}>{s.name}</option>
                    ))}
                  </optgroup>
                )}
                <option value="__custom_new__">+ 创建自定义风格...</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">选择风格后会自动填充对应策略模板，您可以继续编辑调整</p>
              {/* 自定义风格管理 */}
              {customStyles.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {customStyles.map(s => (
                    <span key={s.id} className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-50 text-blue-700 text-xs rounded border border-blue-200">
                      {s.name}
                      <button onClick={() => handleDeleteCustomStyle(s.id)} className="text-red-400 hover:text-red-600" title="删除">
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* 自定义风格创建弹窗 */}
            {showCustomForm && (
              <div className="border-2 border-blue-300 rounded-md p-4 bg-blue-50 space-y-3">
                <h4 className="font-semibold text-blue-900">+ 创建自定义交易风格</h4>
                <div>
                  <Label>风格名称 *</Label>
                  <Input value={customName} onChange={e => setCustomName(e.target.value)} placeholder="例如：RSI能量潮策略" />
                </div>
                <div>
                  <Label>风格简介</Label>
                  <Input value={customDesc} onChange={e => setCustomDesc(e.target.value)} placeholder="简要描述这个风格的特点" />
                </div>
                <div>
                  <Label>策略需求模板（可选）</Label>
                  <Textarea value={customTemplate} onChange={e => setCustomTemplate(e.target.value)} rows={5} className="font-mono text-sm" placeholder="当选择此风格时自动填充的策略描述模板..." />
                </div>
                <div className="flex gap-2">
                  <Button onClick={handleSaveCustomStyle} disabled={savingCustom} size="sm">
                    <Save className="w-4 h-4 mr-1" />
                    {savingCustom ? '保存中...' : '保存风格'}
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setShowCustomForm(false)}>取消</Button>
                </div>
              </div>
            )}

            <div>
              <Label>策略需求描述 *</Label>
              <Textarea
                value={data.user_requirement}
                onChange={(e) => updateData({ user_requirement: e.target.value })}
                placeholder={"例如：\n我想创建一个 BTC 趋势跟踪策略，使用 EMA 交叉和 MACD 确认趋势。\n当 EMA20 上穿 EMA50 且 MACD 金叉时做多，反之做空。\n每次交易使用 20% 资金，止损 5%，止盈 10%。"}
                rows={6}
                className="font-mono text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                提示：请尽量详细地描述您的交易逻辑、入场条件、出场条件和风险控制
              </p>
            </div>

          </div>
        );

      // 第2步：AI生成的策略框架
      case 2:
        return (
          <div className="space-y-4">
            {/* 顶部状态栏：根据生成来源显示不同样式 */}
            {data.strategy_name && data.generation_source === 'template' ? (
              <div className="bg-amber-50 p-4 rounded-md border border-amber-300">
                <div className="flex items-center gap-2 mb-2">
                  <AlertTriangle className="w-5 h-5 text-amber-600" />
                  <h3 className="font-semibold text-amber-900">预设模板生成（非AI）</h3>
                </div>
                <p className="text-sm text-amber-700 mb-2">
                  {data.confidence_note || '当前策略由预设规则模板生成，未通过AI分析。建议检查AI配置后重新生成。'}
                </p>
                <Button
                  size="sm"
                  onClick={() => {
                    updateData({ strategy_name: '', strategy_description: '', strategy_logic: '', entry_conditions: [], exit_conditions: [], generation_source: '', generation_detail: '' });
                    setTimeout(() => generateStrategyFramework(), 100);
                  }}
                  disabled={generating}
                  className="bg-amber-600 hover:bg-amber-700"
                >
                  {generating ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1" />}
                  重新用 AI 生成
                </Button>
              </div>
            ) : data.strategy_name && data.generation_source === 'ai' ? (
              <div className="bg-green-50 p-4 rounded-md border border-green-200">
                <div className="flex items-center gap-2 mb-1">
                  <Sparkles className="w-5 h-5 text-green-600" />
                  <h3 className="font-semibold text-green-900">AI 智能生成</h3>
                  <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full">{data.generation_detail}</span>
                  {data.market_data_used && (
                    <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">已结合实时数据</span>
                  )}
                </div>
                <p className="text-sm text-green-700">{data.confidence_note || 'AI 已根据您的需求和市场数据生成策略，可自由编辑调整'}</p>
              </div>
            ) : (
              <div className="bg-gray-50 p-4 rounded-md border border-gray-200">
                <h3 className="font-semibold text-gray-900 mb-2">策略框架生成</h3>
                <p className="text-sm text-gray-600">
                  点击下方按钮，AI 将分析历史市场数据并生成定制化策略框架
                </p>
              </div>
            )}

            {!data.strategy_name ? (
              <div className="text-center py-8">
                {data.user_requirement.trim() ? (
                  <>
                    <Button 
                      onClick={generateStrategyFramework}
                      disabled={generating}
                      className="px-6 py-3"
                    >
                      {generating ? (
                        <>
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                          AI 正在分析市场数据并生成策略...
                        </>
                      ) : (
                        <>
                          <Sparkles className="w-4 h-4 mr-2" />
                          点击让 AI 生成策略框架
                        </>
                      )}
                    </Button>
                    <p className="text-xs text-gray-500 mt-2">
                      AI 将分析近30天历史K线数据 + 市场状态，结合您的需求生成策略
                    </p>
                  </>
                ) : (
                  <div className="space-y-3">
                    <p className="text-sm text-amber-700">请先回到第1步填写策略需求描述</p>
                    <Button variant="outline" onClick={() => setStep(1)}>
                      返回第1步
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <>
                <div>
                  <Label>策略名称</Label>
                  <Input
                    value={data.strategy_name}
                    onChange={(e) => updateData({ strategy_name: e.target.value })}
                    placeholder="AI生成的策略名称"
                  />
                </div>

                <div>
                  <Label>策略描述</Label>
                  <Textarea
                    value={data.strategy_description}
                    onChange={(e) => updateData({ strategy_description: e.target.value })}
                    rows={3}
                  />
                </div>

                <div>
                  <Label>策略逻辑</Label>
                  <Textarea
                    value={data.strategy_logic}
                    onChange={(e) => updateData({ strategy_logic: e.target.value })}
                    rows={6}
                    className="font-mono text-sm"
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <Label>入场条件</Label>
                    <div className="border rounded-md p-3 bg-gray-50 space-y-1">
                      {data.entry_conditions.map((cond, idx) => (
                        <div key={idx} className="text-sm flex items-start gap-1.5">
                          <span className="text-green-600 font-medium shrink-0">{idx + 1}.</span>
                          <span>{cond}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <Label>出场条件</Label>
                    <div className="border rounded-md p-3 bg-gray-50 space-y-1">
                      {data.exit_conditions.map((cond, idx) => (
                        <div key={idx} className="text-sm flex items-start gap-1.5">
                          <span className="text-red-600 font-medium shrink-0">{idx + 1}.</span>
                          <span>{cond}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="flex gap-2 items-center">
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => {
                      updateData({ strategy_name: '', strategy_description: '', strategy_logic: '', entry_conditions: [], exit_conditions: [], generation_source: '', generation_detail: '' });
                      setTimeout(() => generateStrategyFramework(), 100);
                    }}
                    disabled={generating}
                  >
                    {generating ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1" />}
                    重新生成
                  </Button>
                  {data.generation_source === 'ai' && (
                    <span className="text-xs text-gray-400">对结果不满意？可重新生成获得不同策略方案</span>
                  )}
                </div>
              </>
            )}
          </div>
        );

      // 第3步：AI生成信号定义
      case 3:
        return (
          <div className="space-y-4">
            <div className="bg-purple-50 p-4 rounded-md border border-purple-200">
              <h3 className="font-semibold text-purple-900 mb-2">信号定义生成</h3>
              <p className="text-sm text-purple-700">根据策略逻辑，AI 将生成匹配的交易信号</p>
            </div>

            {data.generated_signals.length === 0 ? (
              <div className="text-center py-8">
                <Button 
                  onClick={generateSignals}
                  disabled={generating || !data.strategy_logic}
                  className="px-6 py-3"
                >
                  {generating ? (
                    <>
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                      AI 正在生成信号...
                    </>
                  ) : (
                    '点击让 AI 生成信号'
                  )}
                </Button>
                <p className="text-xs text-gray-500 mt-2">
                  AI 将根据策略逻辑和入出场条件自动设计技术指标信号
                </p>
              </div>
            ) : (
              <>
                <div className="space-y-3">
                  <Label>生成的信号（{data.generated_signals.length} 个）</Label>
                  {data.generated_signals.map((signal, idx) => (
                    <div key={idx} className="border rounded-md p-4 bg-white">
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex-1">
                          <h4 className="font-semibold text-sm">{signal.name}</h4>
                          <p className="text-xs text-gray-600 mt-1">{signal.description}</p>
                        </div>
                        <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded">
                          {signal.signal_type}
                        </span>
                      </div>
                      <div className="text-xs text-gray-500 mt-2">
                        <strong>计算逻辑：</strong> {signal.calculation_logic}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        <strong>参数：</strong> {JSON.stringify(signal.parameters)}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="flex gap-2">
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => {
                      updateData({ generated_signals: [] });
                      setTimeout(() => generateSignals(), 100);
                    }}
                    disabled={generating}
                  >
                    {generating ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : null}
                    重新生成
                  </Button>
                </div>
              </>
            )}
          </div>
        );

      // 第4步：信号池配置
      case 4:
        return (
          <div className="space-y-4">
            <div className="bg-orange-50 p-4 rounded-md border border-orange-200">
              <h3 className="font-semibold text-orange-900 mb-2">信号池配置</h3>
              <p className="text-sm text-orange-700">将生成的信号组织成信号池，定义信号如何组合触发交易</p>
            </div>

            <div>
              <Label>信号池名称</Label>
              <Input
                value={data.signal_pool.name}
                onChange={(e) => updateData({ 
                  signal_pool: { ...data.signal_pool, name: e.target.value }
                })}
                placeholder="例如：BTC趋势信号池"
              />
            </div>

            <div>
              <Label>组合逻辑</Label>
              <select
                className="w-full px-4 py-2 border rounded-md"
                value={data.signal_pool.logic}
                onChange={(e) => updateData({ 
                  signal_pool: { ...data.signal_pool, logic: e.target.value }
                })}
              >
                <option value="AND">所有信号都满足（AND）</option>
                <option value="OR">任一信号满足（OR）</option>
                <option value="WEIGHTED">加权组合（WEIGHTED）</option>
                <option value="THRESHOLD">阈值模式（THRESHOLD）</option>
              </select>
            </div>

            <div>
              <Label>包含的信号</Label>
              <div className="border rounded-md p-3 bg-gray-50">
                {data.generated_signals.map((signal, idx) => (
                  <div key={idx} className="flex items-center gap-2 mb-2">
                    <input
                      type="checkbox"
                      id={`signal_${idx}`}
                      checked={data.signal_pool.signals.includes(signal.name)}
                      onChange={(e) => {
                        const signals = e.target.checked
                          ? [...data.signal_pool.signals, signal.name]
                          : data.signal_pool.signals.filter(s => s !== signal.name);
                        updateData({ signal_pool: { ...data.signal_pool, signals } });
                      }}
                      className="w-4 h-4"
                    />
                    <Label htmlFor={`signal_${idx}`} className="cursor-pointer">
                      {signal.name} ({signal.signal_type})
                    </Label>
                  </div>
                ))}
              </div>
            </div>

            {data.signal_pool.logic === 'WEIGHTED' && (
              <div>
                <Label>信号权重配置</Label>
                <div className="space-y-2">
                  {data.signal_pool.signals.map(signalName => (
                    <div key={signalName} className="flex items-center gap-2">
                      <span className="text-sm w-32 truncate">{signalName}:</span>
                      <Input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={data.signal_pool.weights?.[signalName] || 0.5}
                        onChange={(e) => {
                          const weights = { ...data.signal_pool.weights, [signalName]: parseFloat(e.target.value) };
                          updateData({ signal_pool: { ...data.signal_pool, weights } });
                        }}
                        className="w-24"
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );

      // 第5步：风险与执行配置（V2 增强 - 动态风控）
      case 5: {
        const mp = marketPreview;
        const dp = mp?.dynamic_params;
        const cyc = mp ? (cycleLabels[mp.market_env.market_cycle] || cycleLabels.unknown) : null;
        const vol = mp ? (volLabels[mp.market_env.volatility_regime] || volLabels.normal) : null;

        return (
          <div className="space-y-4">
            <div className="bg-red-50 dark:bg-red-950/20 p-4 rounded-md border border-red-200 dark:border-red-800">
              <h3 className="font-semibold text-red-900 dark:text-red-400 mb-2 flex items-center gap-2">
                <Shield className="w-5 h-5" />
                风险与执行配置
              </h3>
              <p className="text-sm text-red-700 dark:text-red-400/80">
                设置基础风控参数。系统将根据市场环境<strong>动态调整</strong>实际止盈止损值。
              </p>
            </div>

            {/* V2: 动态风控预览面板 */}
            {mp && (
              <div className="border rounded-lg overflow-hidden border-blue-200 dark:border-blue-800">
                <div className="bg-blue-50 dark:bg-blue-950/30 px-4 py-2 flex items-center justify-between border-b">
                  <div className="flex items-center gap-2 text-sm font-medium text-blue-800 dark:text-blue-400">
                    <Activity className="w-4 h-4" />
                    动态风控预览
                    <span className="text-xs font-normal text-blue-600/70">基于当前市场环境实时计算</span>
                  </div>
                  <button
                    onClick={() => loadMarketPreview()}
                    className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
                  >
                    {loadingMarket ? <Loader2 className="w-3 h-3 animate-spin" /> : '刷新'}
                  </button>
                </div>
                <div className="p-4 space-y-3">
                  {/* 市场状态行 */}
                  <div className="flex items-center gap-4 text-xs">
                    <span className="text-gray-500">当前市场：</span>
                    <span className={`font-bold flex items-center gap-1 ${cyc?.color}`}>
                      {loadingMarket && (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      )}
                      {cyc?.text}
                    </span>
                    <span className="text-gray-300">|</span>
                    <span className="text-gray-500">波动率：</span>
                    <span className={`font-bold ${vol?.color}`}>{vol?.text}</span>
                    <span className="text-gray-300">|</span>
                    <span className="text-gray-500">趋势：</span>
                    <span className={`font-bold ${
                      mp.market_env.trend_direction === 'bullish' ? 'text-green-600' :
                      mp.market_env.trend_direction === 'bearish' ? 'text-red-600' : 'text-gray-600'
                    }`}>
                      {mp.market_env.trend_direction === 'bullish' ? '看多' :
                       mp.market_env.trend_direction === 'bearish' ? '看空' : '中性'}
                    </span>
                  </div>

                  {/* 基础 vs 动态 对比表 */}
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div className="text-xs text-gray-500 font-medium">参数</div>
                    <div className="text-xs text-gray-500 font-medium text-center">你设的基础值</div>
                    <div className="text-xs text-blue-600 font-medium text-center">动态调整后 ⚡</div>

                    <div className="text-gray-700 dark:text-gray-300">止损</div>
                    <div className="text-center">{(data.risk_config.stop_loss_pct * 100).toFixed(1)}%</div>
                    <div className={`text-center font-bold ${
                      dp && dp.stop_loss_pct !== data.risk_config.stop_loss_pct ? 'text-blue-600' : ''
                    }`}>
                      {dp ? `${(dp.stop_loss_pct * 100).toFixed(1)}%` : '--'}
                      {dp && dp.stop_loss_type === 'atr_based' && (
                        <span className="text-[10px] text-gray-400 ml-1">(ATR)</span>
                      )}
                    </div>

                    <div className="text-gray-700 dark:text-gray-300">止盈(TP1)</div>
                    <div className="text-center">{(data.risk_config.take_profit_pct * 100).toFixed(1)}%</div>
                    <div className="text-center font-bold text-blue-600">
                      {dp?.tp_levels?.[0] ? `${(dp.tp_levels[0].pct * 100).toFixed(1)}%→平${(dp.tp_levels[0].close_ratio * 100).toFixed(0)}%` : '--'}
                    </div>

                    <div className="text-gray-700 dark:text-gray-300">仓位</div>
                    <div className="text-center">{(data.risk_config.max_position_size * 100).toFixed(0)}%</div>
                    <div className={`text-center font-bold ${
                      dp && dp.position_size_pct < data.risk_config.max_position_size ? 'text-orange-600' :
                      dp && dp.position_size_pct > data.risk_config.max_position_size ? 'text-green-600' : ''
                    }`}>
                      {dp ? `${(dp.position_size_pct * 100).toFixed(1)}%` : '--'}
                    </div>
                  </div>

                  {/* 分批止盈详情 */}
                  {dp?.tp_levels && dp.tp_levels.length > 0 && (
                    <div className="bg-green-50 dark:bg-green-950/20 rounded p-2 text-xs">
                      <span className="font-medium text-green-700 dark:text-green-400">分批止盈：</span>
                      {dp.tp_levels.map((lv: any, i: number) => (
                        <span key={i} className="ml-2">
                          TP{i+1}: +{(lv.pct * 100).toFixed(1)}% → 平{(lv.close_ratio * 100).toFixed(0)}%
                        </span>
                      ))}
                    </div>
                  )}

                  {mp.guidance && (
                    <div className="text-xs text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30 rounded p-2">
                      <strong>AI：</strong>{mp.guidance}
                    </div>
                  )}
                  {/* 数据溯源 */}
                  <div className="flex items-center gap-3 text-[10px] text-gray-400 dark:text-gray-500 border-t pt-2">
                    <span className="flex items-center gap-1">
                      {mp.market_env.data_source === 'default' ? (
                        <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
                      ) : (
                        <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400" />
                      )}
                      {mp.market_env.data_source === 'market_adaptor' ? '市场适配器' :
                       mp.market_env.data_source === 'kline_analysis' ? 'K线实时分析' :
                       mp.market_env.data_source === 'market_data_analyzer' ? '市场分析器' : '默认值'}
                    </span>
                    {(mp.market_env.kline_count ?? 0) > 0 && (
                      <span>{mp.market_env.kline_count}条K线</span>
                    )}
                    {(mp.market_env.current_price ?? 0) > 0 && (
                      <span>BTC ${mp.market_env.current_price?.toLocaleString()}</span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* 基础风控参数 */}
            <div className="border-t pt-4">
              <h4 className="font-semibold mb-1">基础风控参数</h4>
              <p className="text-xs text-gray-500 mb-3">这些是基础值，实际交易时会被市场环境动态调整</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>最大仓位比例</Label>
                  <Input
                    type="number" step="0.01" min="0.01" max="1"
                    value={data.risk_config.max_position_size}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, max_position_size: parseFloat(e.target.value) || 0.2 }
                    })}
                  />
                  <p className="text-xs text-gray-500 mt-1">{(data.risk_config.max_position_size * 100).toFixed(0)}% 资金</p>
                </div>
                <div>
                  <Label>基础止损</Label>
                  <Input
                    type="number" step="0.01" min="0.01" max="0.5"
                    value={data.risk_config.stop_loss_pct}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, stop_loss_pct: parseFloat(e.target.value) || 0.05 }
                    })}
                  />
                  <p className="text-xs text-gray-500 mt-1">{(data.risk_config.stop_loss_pct * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <Label>基础止盈</Label>
                  <Input
                    type="number" step="0.01" min="0.01" max="1"
                    value={data.risk_config.take_profit_pct}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, take_profit_pct: parseFloat(e.target.value) || 0.10 }
                    })}
                  />
                  <p className="text-xs text-gray-500 mt-1">{(data.risk_config.take_profit_pct * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <Label>最大日损失</Label>
                  <Input
                    type="number" step="0.01" min="0.01" max="0.5"
                    value={data.risk_config.max_daily_loss}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, max_daily_loss: parseFloat(e.target.value) || 0.10 }
                    })}
                  />
                  <p className="text-xs text-gray-500 mt-1">{(data.risk_config.max_daily_loss * 100).toFixed(0)}%</p>
                </div>
              </div>
            </div>

            {/* V2: 动态风控开关 */}
            <div className="border-t pt-4">
              <h4 className="font-semibold mb-1">智能风控功能</h4>
              <p className="text-xs text-gray-500 mb-3">开启后，系统将根据 ATR 和市场状态自动优化止盈止损</p>
              <div className="space-y-3">
                <label className="flex items-start gap-3 p-3 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors">
                  <input
                    type="checkbox"
                    checked={data.risk_config.dynamic_sl_enabled}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, dynamic_sl_enabled: e.target.checked }
                    })}
                    className="w-4 h-4 mt-0.5"
                  />
                  <div>
                    <div className="text-sm font-medium">动态止损（基于 ATR）</div>
                    <div className="text-xs text-gray-500">根据市场真实波动幅度计算止损，而非固定百分比。高波动时自动放宽止损防止被洗，低波动时收紧止损保护利润。</div>
                  </div>
                </label>

                <label className="flex items-start gap-3 p-3 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors">
                  <input
                    type="checkbox"
                    checked={data.risk_config.trailing_stop_enabled}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, trailing_stop_enabled: e.target.checked }
                    })}
                    className="w-4 h-4 mt-0.5"
                  />
                  <div>
                    <div className="text-sm font-medium">移动止损（追踪止盈）</div>
                    <div className="text-xs text-gray-500">盈利达到一定比例后激活，自动追踪最高价/最低价，锁定已有利润。行情反转时自动平仓。</div>
                  </div>
                </label>

                <label className="flex items-start gap-3 p-3 border rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors">
                  <input
                    type="checkbox"
                    checked={data.risk_config.time_stop_enabled}
                    onChange={(e) => updateData({ 
                      risk_config: { ...data.risk_config, time_stop_enabled: e.target.checked }
                    })}
                    className="w-4 h-4 mt-0.5"
                  />
                  <div>
                    <div className="text-sm font-medium">时间止损</div>
                    <div className="text-xs text-gray-500">
                      持仓超过设定时间未达目标时自动平仓，避免资金长期占用。
                    </div>
                    {data.risk_config.time_stop_enabled && (
                      <div className="mt-2 flex items-center gap-2">
                        <Input
                          type="number" min="1" max="720"
                          value={data.risk_config.time_stop_hours}
                          onChange={(e) => updateData({
                            risk_config: { ...data.risk_config, time_stop_hours: parseInt(e.target.value) || 72 }
                          })}
                          className="w-20 h-7 text-sm"
                        />
                        <span className="text-xs text-gray-500">小时</span>
                      </div>
                    )}
                  </div>
                </label>
              </div>
            </div>

            {/* 执行配置 */}
            <div className="border-t pt-4">
              <h4 className="font-semibold mb-3">执行配置</h4>
              <div className="space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={data.execution_config.auto_execute}
                    onChange={(e) => updateData({ 
                      execution_config: { ...data.execution_config, auto_execute: e.target.checked }
                    })}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">自动执行交易（无需人工确认）</span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={data.execution_config.require_confirmation}
                    onChange={(e) => updateData({ 
                      execution_config: { ...data.execution_config, require_confirmation: e.target.checked }
                    })}
                    className="w-4 h-4"
                  />
                  <span className="text-sm">需要人工确认（推荐）</span>
                </label>

                <div>
                  <Label>最小置信度</Label>
                  <Input
                    type="number" step="0.05" min="0.1" max="1"
                    value={data.execution_config.min_confidence}
                    onChange={(e) => updateData({ 
                      execution_config: { ...data.execution_config, min_confidence: parseFloat(e.target.value) || 0.6 }
                    })}
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    置信度 &ge; {(data.execution_config.min_confidence * 100).toFixed(0)}% 才触发交易
                    {mp && mp.adapted_multipliers.entry_threshold > data.execution_config.min_confidence && (
                      <span className="text-blue-600 ml-1">
                        (市场环境可能提高至 {(mp.adapted_multipliers.entry_threshold * 100).toFixed(0)}%)
                      </span>
                    )}
                  </p>
                </div>
              </div>
            </div>
          </div>
        );
      }

      // 第6步：预览与确认（V2 增强）
      case 6: {
        const mp6 = marketPreview;
        return (
          <div className="space-y-4">
            <div className="bg-indigo-50 dark:bg-indigo-950/20 p-4 rounded-md border border-indigo-200 dark:border-indigo-800">
              <h3 className="font-semibold text-indigo-900 dark:text-indigo-400 mb-2">预览完整配置</h3>
              <p className="text-sm text-indigo-700 dark:text-indigo-400/80">请仔细检查以下配置，确认无误后点击创建</p>
            </div>

            <div className="space-y-3">
              {/* 策略基本信息 */}
              <div className="border rounded-md p-4 bg-white dark:bg-gray-800/50">
                <h4 className="font-semibold mb-2 text-sm">策略信息</h4>
                <div className="text-sm space-y-1">
                  <p><strong>名称：</strong>{data.strategy_name}</p>
                  <p><strong>描述：</strong>{data.strategy_description}</p>
                  <p><strong>账户ID：</strong>{data.account_id}</p>
                  <p><strong>交易风格：</strong>{data.trading_style}</p>
                  <p>
                    <strong>交易对：</strong>
                    {data.target_symbols.map(sym => (
                      <span key={sym} className={`inline-block px-1.5 py-0.5 rounded text-xs mr-1 ${
                        sym === data.primary_symbol ? 'bg-blue-100 text-blue-700 font-bold' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {sym === data.primary_symbol ? `★${sym}` : sym}
                      </span>
                    ))}
                  </p>
                  <p><strong>时间周期：</strong>{data.timeframe}</p>
                </div>
              </div>

              {/* 信号 + 信号池 */}
              <div className="border rounded-md p-4 bg-white dark:bg-gray-800/50">
                <h4 className="font-semibold mb-2 text-sm">信号系统（{data.generated_signals.length} 个信号）</h4>
                <div className="text-sm space-y-1">
                  {data.generated_signals.map((sig, idx) => (
                    <p key={idx} className="text-gray-700 dark:text-gray-300">{idx + 1}. {sig.name} ({sig.signal_type})</p>
                  ))}
                  <div className="border-t mt-2 pt-2 text-xs text-gray-500">
                    信号池: <strong>{data.signal_pool.name}</strong> · 逻辑: <strong>{data.signal_pool.logic}</strong>
                  </div>
                </div>
              </div>

              {/* V2: 风控配置（基础 + 动态） */}
              <div className="border rounded-md p-4 bg-white dark:bg-gray-800/50">
                <h4 className="font-semibold mb-2 text-sm flex items-center gap-2">
                  <Shield className="w-4 h-4 text-orange-500" />
                  风险管理
                </h4>
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">基础仓位</span>
                    <span>{(data.risk_config.max_position_size * 100).toFixed(0)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">每日限额</span>
                    <span>{(data.risk_config.max_daily_loss * 100).toFixed(0)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">基础止损</span>
                    <span className="text-red-600">{(data.risk_config.stop_loss_pct * 100).toFixed(1)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">基础止盈</span>
                    <span className="text-green-600">{(data.risk_config.take_profit_pct * 100).toFixed(1)}%</span>
                  </div>
                </div>

                {/* 智能风控功能汇总 */}
                <div className="mt-3 pt-3 border-t space-y-1.5">
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`w-2 h-2 rounded-full ${data.risk_config.dynamic_sl_enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                    <span className={data.risk_config.dynamic_sl_enabled ? '' : 'text-gray-400'}>
                      动态止损（ATR）{data.risk_config.dynamic_sl_enabled ? '✓ 已开启' : '✗ 未开启'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`w-2 h-2 rounded-full ${data.risk_config.trailing_stop_enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                    <span className={data.risk_config.trailing_stop_enabled ? '' : 'text-gray-400'}>
                      移动止损{data.risk_config.trailing_stop_enabled ? '✓ 已开启' : '✗ 未开启'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`w-2 h-2 rounded-full ${data.risk_config.time_stop_enabled ? 'bg-green-500' : 'bg-gray-300'}`} />
                    <span className={data.risk_config.time_stop_enabled ? '' : 'text-gray-400'}>
                      时间止损{data.risk_config.time_stop_enabled ? `✓ ${data.risk_config.time_stop_hours}小时` : '✗ 未开启'}
                    </span>
                  </div>
                </div>

                {/* 动态预览 */}
                {mp6?.dynamic_params && (
                  <div className="mt-3 p-2.5 rounded bg-blue-50 dark:bg-blue-950/20 text-xs space-y-1">
                    <div className="font-medium text-blue-700 dark:text-blue-400">⚡ 当前市场环境下的实际参数：</div>
                    <div className="grid grid-cols-2 gap-1 text-blue-800/80 dark:text-blue-300/80">
                      <span>实际止损: {(mp6.dynamic_params.stop_loss_pct * 100).toFixed(1)}%</span>
                      <span>实际仓位: {(mp6.dynamic_params.position_size_pct * 100).toFixed(1)}%</span>
                      {mp6.dynamic_params.tp_levels?.[0] && (
                        <span>第1级止盈: +{(mp6.dynamic_params.tp_levels[0].pct * 100).toFixed(1)}%</span>
                      )}
                      <span>时间止损: {mp6.dynamic_params.time_stop_hours}h</span>
                    </div>
                  </div>
                )}
              </div>

              {/* 执行配置 */}
              <div className="border rounded-md p-4 bg-white dark:bg-gray-800/50">
                <h4 className="font-semibold mb-2 text-sm">执行配置</h4>
                <div className="text-sm space-y-1">
                  <p><strong>自动执行：</strong>{data.execution_config.auto_execute ? '✓ 是' : '✗ 否'}</p>
                  <p><strong>需要确认：</strong>{data.execution_config.require_confirmation ? '✓ 是' : '✗ 否'}</p>
                  <p><strong>最小置信度：</strong>{(data.execution_config.min_confidence * 100).toFixed(0)}%</p>
                </div>
              </div>
            </div>

            <div className="bg-green-50 dark:bg-green-950/20 p-3 rounded-md border border-green-200 dark:border-green-800">
              <p className="text-sm text-green-800 dark:text-green-400">
                <strong>创建后系统将：</strong>
                ① 创建 {data.generated_signals.length} 个信号 + 1 个信号池 + 1 个 AI 策略
                → ② 自动关联所有组件
                → ③ 策略运行时动态调整风控参数
                → ④ 每次交易后学习并优化策略
              </p>
            </div>
          </div>
        );
      }

      default:
        return null;
    }
  };

  return (
    <div className="container mx-auto p-6 max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>创建 AI 策略 - 第 {step}/{totalSteps} 步</CardTitle>
          <div className="flex gap-2 mt-4">
            {Array.from({ length: totalSteps }).map((_, i) => (
              <div
                key={i}
                className={`h-2 flex-1 rounded ${
                  i + 1 <= step ? 'bg-blue-500' : 'bg-gray-200'
                }`}
              />
            ))}
          </div>
        </CardHeader>

        <CardContent className="min-h-[300px]">
          {renderStep()}
        </CardContent>

        <CardFooter className="flex justify-between">
          <Button
            variant="outline"
            onClick={handlePrev}
            disabled={step === 1}
          >
            <ChevronLeft className="w-4 h-4 mr-2" />
            上一步
          </Button>

          {step < totalSteps ? (
            <Button onClick={handleNext}>
              下一步
              <ChevronRight className="w-4 h-4 ml-2" />
            </Button>
          ) : (
            <Button onClick={handleSubmit} disabled={submitting}>
              {submitting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  创建中...
                </>
              ) : (
                <>
                  <Check className="w-4 h-4 mr-2" />
                  完成创建
                </>
              )}
            </Button>
          )}
        </CardFooter>
      </Card>
    </div>
  );
}
