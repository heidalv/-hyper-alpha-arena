"""
策略参数注册表 — 所有策略参数的唯一真相源 (Single Source of Truth)

整合原来分散在以下文件中的参数定义:
  - strategy_genome.py (GENOME_SCHEMA)
  - backtest_evolution_engine.py (DEFAULT_SIGNAL_PARAMS, SIGNAL_PARAM_RANGES, etc.)
  - live_pipeline_backtest_engine.py (DEFAULT_PIPELINE_PARAMS, PIPELINE_PARAM_RANGES)
  - strategy_evolver.py (PROMOTION_THRESHOLDS, DEFAULT_EVOLUTION_CONFIG)
  - deterministic_risk_gate.py (DEFAULT_RULES)
  - risk_control_service.py (RiskControlConfig)

向后兼容: 导出与原有常量同名的变量，旧代码无需立即修改。
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
#  参数规格定义
# ════════════════════════════════════════════════════════

@dataclass
class ParamSpec:
    """参数规格: 默认值 + 范围 + 变异权重 + 覆盖值"""
    default: float
    min: float
    max: float
    mutation_weight: float = 0.5
    description: str = ""


# ════════════════════════════════════════════════════════
#  1. 信号参数 (来自 backtest_evolution_engine.py)
# ════════════════════════════════════════════════════════

SIGNAL_PARAMS: Dict[str, ParamSpec] = {
    "ema_fast":            ParamSpec(8,    3,   20,   0.8, "快速EMA周期"),
    "ema_mid":             ParamSpec(21,   10,  40,   0.8, "中期EMA周期"),
    "ema_slow":            ParamSpec(55,   30,  200,  0.5, "慢速EMA周期"),
    "rsi_period":          ParamSpec(14,   7,   21,   0.6, "RSI周期"),
    "rsi_long_lo":         ParamSpec(25,   10,  45,   0.7, "做多RSI下限"),
    "rsi_long_hi":         ParamSpec(88,   65,  95,   0.7, "做多RSI上限"),
    "rsi_short_lo":        ParamSpec(12,   5,   35,   0.7, "做空RSI下限"),
    "rsi_short_hi":        ParamSpec(75,   55,  90,   0.7, "做空RSI上限"),
    "rsi_ob":              ParamSpec(80,   65,  90,   0.6, "RSI超买"),
    "rsi_os":              ParamSpec(20,   10,  35,   0.6, "RSI超卖"),
    "bb_period":           ParamSpec(18,   10,  40,   0.5, "布林带周期"),
    "bb_std":              ParamSpec(1.8,  1.0, 3.5,  0.5, "布林带标准差"),
    "bb_edge_pct":         ParamSpec(0.25, 0.08, 0.40, 0.4, "布林带边缘比例"),
    "macd_fast":           ParamSpec(12,   5,   20,   0.5, "MACD快线"),
    "macd_slow":           ParamSpec(26,   15,  40,   0.5, "MACD慢线"),
    "macd_signal":         ParamSpec(9,    5,   15,   0.4, "MACD信号线"),
    "breakout_lookback":   ParamSpec(20,   5,   30,   0.5, "突破回看周期"),
    "vol_surge_mult":      ParamSpec(1.5,  1.2, 3.0,  0.4, "放量倍数"),
    "vol_quiet_mult":      ParamSpec(0.8,  0.5, 1.5,  0.3, "缩量阈值"),
    "swing_pullback_lo":   ParamSpec(-0.05, -0.15, 0.02, 0.5, "波段回撤下限"),
    "swing_pullback_hi":   ParamSpec(-0.01, -0.001, 0.05, 0.5, "波段回撤上限"),
    "momentum_vol_mult":   ParamSpec(1.3,  1.1, 2.5,  0.4, "动量量能倍数"),
    "min_bars_between":    ParamSpec(3,    1,   5,    0.8, "最少间隔K线"),
}


# ════════════════════════════════════════════════════════
#  2. 策略类型专属默认参数
# ════════════════════════════════════════════════════════

CATEGORY_SIGNAL_DEFAULTS: Dict[str, Dict[str, float]] = {
    "trend": {
        "ema_fast": 5, "ema_mid": 14, "ema_slow": 40,
        "rsi_long_lo": 20, "rsi_long_hi": 88, "rsi_short_lo": 12, "rsi_short_hi": 80,
        "min_bars_between": 1,
    },
    "mean_reversion": {
        "rsi_ob": 72, "rsi_os": 28, "bb_period": 16, "bb_std": 1.6,
        "min_bars_between": 1,
    },
    "range": {
        "bb_period": 18, "bb_std": 1.8, "bb_edge_pct": 0.28,
        "rsi_long_hi": 88, "rsi_short_lo": 12,
        "min_bars_between": 1,
    },
    "breakout": {
        "breakout_lookback": 8, "vol_surge_mult": 1.1,
        "ema_fast": 5, "ema_mid": 14,
        "min_bars_between": 1,
    },
    "swing": {
        "ema_mid": 18, "ema_slow": 45,
        "swing_pullback_lo": -0.08, "swing_pullback_hi": 0.015,
        "rsi_long_lo": 22, "rsi_short_hi": 78,
        "min_bars_between": 1,
    },
    "momentum": {
        "ema_fast": 5, "ema_mid": 14,
        "rsi_long_hi": 88, "rsi_short_lo": 12,
        "momentum_vol_mult": 0.95,
        "min_bars_between": 1,
    },
}

CATEGORY_KEY_PARAMS: Dict[str, list] = {
    "trend":           ["ema_fast", "ema_mid", "ema_slow", "rsi_long_lo", "rsi_long_hi", "rsi_short_lo", "rsi_short_hi", "macd_fast", "macd_slow", "min_bars_between"],
    "mean_reversion":  ["rsi_ob", "rsi_os", "bb_period", "bb_std", "rsi_long_lo", "rsi_short_hi", "vol_quiet_mult", "min_bars_between"],
    "range":           ["bb_period", "bb_std", "bb_edge_pct", "rsi_long_hi", "rsi_short_lo", "min_bars_between"],
    "breakout":        ["breakout_lookback", "vol_surge_mult", "ema_fast", "ema_mid", "min_bars_between"],
    "swing":           ["swing_pullback_lo", "swing_pullback_hi", "ema_mid", "ema_slow", "rsi_long_lo", "rsi_short_hi", "min_bars_between"],
    "momentum":        ["ema_fast", "ema_mid", "rsi_long_hi", "rsi_short_lo", "momentum_vol_mult", "macd_fast", "macd_slow", "min_bars_between"],
}

# 按 tier 覆盖的信号参数范围
TIER_SIGNAL_PARAM_OVERRIDES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "short": {
        "ema_fast": (3, 12), "ema_mid": (8, 25), "ema_slow": (20, 60),
        "min_bars_between": (1, 3), "breakout_lookback": (3, 15),
        "rsi_long_lo": (15, 45), "rsi_long_hi": (70, 95),
    },
    "mid": {
        "ema_fast": (5, 18), "ema_mid": (12, 35), "ema_slow": (30, 120),
        "min_bars_between": (1, 5), "breakout_lookback": (5, 30),
    },
    "long": {
        "ema_fast": (8, 25), "ema_mid": (20, 60), "ema_slow": (50, 200),
        "min_bars_between": (2, 8), "breakout_lookback": (10, 50),
        "rsi_long_lo": (20, 50), "rsi_long_hi": (60, 90),
    },
}


# ════════════════════════════════════════════════════════
#  3. 管线参数 (来自 live_pipeline_backtest_engine.py)
# ════════════════════════════════════════════════════════

PIPELINE_PARAMS: Dict[str, ParamSpec] = {
    # 编排器 — 中期 RSI/MACD
    "mid_rsi_bull":            ParamSpec(55,  50,  70,  0.6, "中期RSI看多阈值"),
    "mid_rsi_bear":            ParamSpec(45,  30,  50,  0.6, "中期RSI看空阈值"),
    "mid_rsi_weak_bull":       ParamSpec(52,  48,  60,  0.5, "中期RSI弱看多"),
    "mid_rsi_weak_bear":       ParamSpec(48,  40,  52,  0.5, "中期RSI弱看空"),
    "mid_conf_strong":         ParamSpec(0.85, 0.5, 1.0, 0.5, "中期强置信度"),
    "mid_conf_weak":           ParamSpec(0.35, 0.15, 0.6, 0.5, "中期弱置信度"),
    "mid_conf_neutral":        ParamSpec(0.25, 0.1, 0.4,  0.4, "中期中性置信度"),
    # 编排器 — 情报融合
    "intel_fusion_min_conf":   ParamSpec(0.1,  0.05, 0.3, 0.5, "情报融合最小置信度"),
    "intel_fusion_neutral_boost": ParamSpec(0.3, 0.1, 0.5, 0.5, "中性时情报加成"),
    "intel_fusion_agree_boost":   ParamSpec(0.15, 0.05, 0.3, 0.4, "方向一致加成"),
    "intel_fusion_conflict_mult": ParamSpec(0.5,  0.2, 0.8,  0.4, "方向冲突衰减"),
    # 编排器 — 长期（恐贪）
    "long_fgi_extreme_fear":   ParamSpec(25,  15,  35,  0.5, "极度恐慌FGI阈值"),
    "long_fgi_extreme_greed":  ParamSpec(75,  65,  85,  0.5, "极度贪婪FGI阈值"),
    "long_fgi_fear":           ParamSpec(40,  30,  48,  0.5, "恐慌FGI阈值"),
    "long_fgi_greed":          ParamSpec(60,  52,  70,  0.5, "贪婪FGI阈值"),
    "long_intel_min_conf":     ParamSpec(15,  5,   30,  0.4, "长期情报最小置信度"),
    # 编排器 — 短期（鲸鱼）
    "short_whale_threshold":   ParamSpec(0.3,  0.1, 0.6,  0.5, "鲸鱼方向阈值"),
    # 编排器 — 最终决策
    "finalize_long_weight":    ParamSpec(0.25, 0.1, 0.4, 0.5, "长期最终权重"),
    "finalize_mid_weight":     ParamSpec(0.45, 0.3, 0.6, 0.5, "中期最终权重"),
    "finalize_short_weight":   ParamSpec(0.30, 0.1, 0.4, 0.5, "短期最终权重"),
    "finalize_min_conf":       ParamSpec(0.1,  0.05, 0.25, 0.5, "最终最小置信度"),
    "finalize_max_active_ratio": ParamSpec(0.6, 0.4, 0.8, 0.4, "活跃置信度比例"),
    "finalize_mid_fallback_conf": ParamSpec(0.15, 0.05, 0.3, 0.5, "中期回退置信度"),
    "finalize_long_fallback_conf": ParamSpec(0.2, 0.1, 0.4, 0.5, "长期回退置信度"),
    # 三维确认
    "confirmation_min_dims":   ParamSpec(2,    1,   3,   0.7, "最少确认维度"),
    # 情报权重
    "weight_funding":          ParamSpec(0.22, 0.05, 0.40, 0.7, "资金费率权重"),
    "weight_oi":               ParamSpec(0.22, 0.05, 0.40, 0.7, "持仓量权重"),
    "weight_liquidation":      ParamSpec(0.14, 0.05, 0.30, 0.6, "清算权重"),
    "weight_whale":            ParamSpec(0.10, 0.02, 0.25, 0.6, "鲸鱼权重"),
    "weight_news":             ParamSpec(0.08, 0.02, 0.20, 0.5, "新闻权重"),
    "weight_fear_greed":       ParamSpec(0.06, 0.02, 0.15, 0.5, "恐贪指数权重"),
    "weight_long_short":       ParamSpec(0.10, 0.02, 0.25, 0.6, "多空比权重"),
    "weight_top_trader":       ParamSpec(0.08, 0.02, 0.20, 0.5, "头部交易者权重"),
    "direction_threshold":     ParamSpec(0.15, 0.05, 0.30, 0.6, "方向阈值"),
    # 风控
    "stop_loss_pct":           ParamSpec(0.025, 0.01, 0.08, 0.9, "止损百分比"),
    "take_profit_pct":         ParamSpec(0.075, 0.02, 0.20, 0.9, "止盈百分比"),
    "max_position_size":       ParamSpec(0.20,  0.05, 0.30, 0.7, "最大仓位比例"),
    "trailing_activation_pct": ParamSpec(0.015, 0.003, 0.05, 0.6, "追踪止损激活"),
    "trailing_distance_pct":   ParamSpec(0.012, 0.003, 0.035, 0.6, "追踪止损距离"),
    "breakeven_activation_pct": ParamSpec(0.010, 0.003, 0.03, 0.5, "保本止损激活"),
    "breakeven_buffer_pct":    ParamSpec(0.002, 0.0005, 0.005, 0.4, "保本止损缓冲"),
    "default_leverage":        ParamSpec(8,    5,   20,  0.5, "默认杠杆"),
    "max_daily_loss":          ParamSpec(0.05, 0.03, 0.20, 0.5, "每日最大亏损"),
    # V3 整合：因子信号参数
    "factor_signal_weight":    ParamSpec(0.3,  0.0,  0.6,  0.7, "因子信号权重（0=关闭）"),
    "factor_signal_interval":  ParamSpec(6,    3,    12,   0.5, "因子信号计算间隔（bar数）"),
}


# ════════════════════════════════════════════════════════
#  4. 基因组参数 (来自 strategy_genome.py)
# ════════════════════════════════════════════════════════

GENOME_RISK_PARAMS: Dict[str, ParamSpec] = {
    "stop_loss_pct":            ParamSpec(0.04,  0.01, 0.15, 0.9, "止损"),
    "take_profit_pct":          ParamSpec(0.08,  0.02, 0.30, 0.9, "止盈"),
    "trailing_activation_pct":  ParamSpec(0.04,  0.02, 0.10, 0.6, "追踪止损激活"),
    "trailing_distance_pct":    ParamSpec(0.025, 0.01, 0.05, 0.6, "追踪止损距离"),
    "max_position_size":        ParamSpec(0.15,  0.03, 0.30, 0.7, "最大仓位"),
    "default_leverage":         ParamSpec(8.0,   5.0,  20.0, 0.5, "默认杠杆"),
    "atr_sl_mult":              ParamSpec(1.5,   0.5,  4.0,  0.7, "ATR止损倍数"),
    "atr_tp_mult":              ParamSpec(3.0,   1.0,  8.0,  0.7, "ATR止盈倍数"),
}

GENOME_DECISION_PARAMS: Dict[str, ParamSpec] = {
    "signal_threshold":  ParamSpec(0.25, 0.10, 0.50, 0.8, "信号阈值"),
    "confidence_min":    ParamSpec(0.25, 0.1,  0.7,  0.7, "最小置信度"),
    "weight_short":      ParamSpec(0.5,  0.2,  0.8,  0.5, "短期权重"),
    "weight_mid":        ParamSpec(0.3,  0.1,  0.6,  0.5, "中期权重"),
    "weight_long":       ParamSpec(0.2,  0.0,  0.5,  0.5, "长期权重"),
    "resonance_bonus":   ParamSpec(0.1,  0.0,  0.3,  0.4, "共振奖励"),
}

GENOME_TIMING_PARAMS: Dict[str, ParamSpec] = {
    "analysis_interval_short": ParamSpec(120,  30,  600,  0.3, "短期分析间隔(秒)"),
    "analysis_interval_mid":   ParamSpec(900,  300, 3600, 0.3, "中期分析间隔(秒)"),
    "analysis_interval_long":  ParamSpec(7200, 1800, 43200, 0.3, "长期分析间隔(秒)"),
    "min_trade_interval":      ParamSpec(120,  30,  1800, 0.6, "最小交易间隔(秒)"),
}

GENOME_TERMINATION_PARAMS: Dict[str, ParamSpec] = {
    "min_win_rate":        ParamSpec(0.25, 0.10, 0.50, 0.5, "最低胜率"),
    "max_drawdown":        ParamSpec(0.30, 0.10, 0.50, 0.5, "最大回撤"),
    "max_loss_ratio":      ParamSpec(2.5,  1.2,  5.0,  0.4, "最大盈亏比"),
    "circuit_breaker_pct": ParamSpec(0.20, 0.05, 0.40, 0.4, "熔断百分比"),
}

# 组合到统一 GENOME_SCHEMA（兼容 strategy_genome.py 的格式）
GENOME_SCHEMA: Dict[str, Dict[str, Tuple[float, float, float, float]]] = {
    "signal_params": {k: (v.default, v.min, v.max, v.mutation_weight) for k, v in SIGNAL_PARAMS.items()},
    "risk_params": {k: (v.default, v.min, v.max, v.mutation_weight) for k, v in GENOME_RISK_PARAMS.items()},
    "decision_params": {k: (v.default, v.min, v.max, v.mutation_weight) for k, v in GENOME_DECISION_PARAMS.items()},
    "timing_params": {k: (v.default, v.min, v.max, v.mutation_weight) for k, v in GENOME_TIMING_PARAMS.items()},
    "termination_params": {k: (v.default, v.min, v.max, v.mutation_weight) for k, v in GENOME_TERMINATION_PARAMS.items()},
    "pipeline_weights": {k: (v.default, v.min, v.max, v.mutation_weight)
                         for k, v in PIPELINE_PARAMS.items()
                         if k.startswith("weight_") or k == "direction_threshold"},
    "orchestrator_params": {k: (v.default, v.min, v.max, v.mutation_weight)
                            for k, v in PIPELINE_PARAMS.items()
                            if k.startswith(("mid_", "long_", "short_", "finalize_", "confirmation_", "intel_fusion_"))},
    # D7: 因子权重进化 — 动态注册，初始为空，由 genetic_optimizer 运行时注入
    "factor_weights": {},
}


# ════════════════════════════════════════════════════════
#  5. 风控硬限制 (统一 deterministic_risk_gate + risk_control_service)
# ════════════════════════════════════════════════════════

RISK_LIMITS: Dict[str, ParamSpec] = {
    "max_daily_loss_pct":              ParamSpec(5.0,   2.0,  10.0, 0.0, "单日最大亏损(%)"),
    "max_leverage":                    ParamSpec(20.0,  1.0,  50.0, 0.0, "最大杠杆"),
    "max_symbol_notional_pct":         ParamSpec(25.0,  10.0, 40.0, 0.0, "单品种最大名义占比(%)"),
    "max_side_margin_pct":             ParamSpec(40.0,  20.0, 60.0, 0.0, "单侧最大保证金占比(%)"),
    "min_available_balance_pct":       ParamSpec(10.0,  5.0,  20.0, 0.0, "最小可用余额占比(%)"),
    "max_loss_per_trade_pct":          ParamSpec(2.0,   0.5,  5.0,  0.0, "单笔最大亏损(%)"),
    "max_position_per_trade_pct":      ParamSpec(20.0,  5.0,  40.0, 0.0, "单笔最大仓位(%)"),
    "max_total_position_multiple":     ParamSpec(3.0,   1.0,  6.0,  0.0, "总仓位/权益倍数"),
    "max_margin_usage_pct":            ParamSpec(70.0,  40.0, 90.0, 0.0, "最大保证金使用率(%)"),
    "max_daily_trades":                ParamSpec(50,    5,    200,  0.0, "每日最大交易次数(极端安全网)"),
    "consecutive_loss_reduce":         ParamSpec(3,     2,    5,    0.0, "连续亏损缩减阈值"),
    "consecutive_loss_pause":          ParamSpec(5,     3,    8,    0.0, "连续亏损暂停阈值"),
    "circuit_breaker_cooldown_hours":  ParamSpec(24,    4,    48,   0.0, "熔断冷却时间(小时)"),
    # ── per-symbol 风控冻结参数 ──
    "max_symbol_daily_loss_pct":       ParamSpec(3.0,   1.0,  8.0,  0.0, "单symbol日最大亏损(%)"),
    "global_extreme_daily_loss_pct":   ParamSpec(15.0,  8.0,  25.0, 0.0, "全局极端日亏损安全网(%)"),
    "global_extreme_drawdown_pct":     ParamSpec(0.50,  0.30, 0.70, 0.0, "全局极端回撤安全网"),
    "symbol_freeze_cooldown_minutes":  ParamSpec(60,    15,   240,  0.0, "symbol亏损冻结冷却(分钟)"),
}


# ════════════════════════════════════════════════════════
#  6. 进化配置
# ════════════════════════════════════════════════════════

EVOLUTION_CONFIG: Dict[str, ParamSpec] = {
    "max_generations":    ParamSpec(12,  4,  30, 0.0, "最大进化代数"),
    "population_per_gen": ParamSpec(16,  6,  40, 0.0, "每代个体数"),
    "mutation_rate":      ParamSpec(0.3, 0.1, 0.6, 0.0, "变异率"),
    "lookback_days":      ParamSpec(730, 365, 1095, 0.0, "回看天数"),
    "max_workers":        ParamSpec(4,   1,  8,  0.0, "最大并行数"),
    "walk_forward_split": ParamSpec(0.7, 0.5, 0.8, 0.0, "Walk-Forward训练比例"),
}


# ════════════════════════════════════════════════════════
#  7. 验证阈值 (三级渐进式管线)
# ════════════════════════════════════════════════════════

VALIDATION_THRESHOLDS: Dict[str, Dict[str, ParamSpec]] = {
    "stage1_evolution": {
        "min_sharpe":          ParamSpec(1.0,  0.5, 2.0, 0.0, "进化门控: 最小Sharpe"),
        "max_drawdown":        ParamSpec(0.20, 0.10, 0.35, 0.0, "进化门控: 最大回撤"),
        "min_trades":          ParamSpec(100,  30,  300, 0.0, "进化门控: 最小交易数"),
        "min_profit_factor":   ParamSpec(1.50, 1.0, 3.0, 0.0, "进化门控: 最小利润因子"),
        "min_win_rate":        ParamSpec(0.45, 0.30, 0.60, 0.0, "进化门控: 最小胜率"),
        "max_overfit_ratio":   ParamSpec(0.6,  0.3, 1.0, 0.0, "进化门控: 最大过拟合比"),
    },
    "stage2_validation": {
        "min_sharpe":          ParamSpec(1.2,  0.8, 2.0, 0.0, "验证门控: 最小Sharpe"),
        "max_drawdown":        ParamSpec(0.18, 0.10, 0.30, 0.0, "验证门控: 最大回撤"),
        "min_trades":          ParamSpec(150,  50,  300, 0.0, "验证门控: 最小交易数"),
        "min_profit_factor":   ParamSpec(1.30, 1.0, 2.5, 0.0, "验证门控: 最小利润因子"),
        "max_overfit_ratio":   ParamSpec(0.5,  0.3, 0.8, 0.0, "验证门控: 最大过拟合比"),
        "min_wf_consistency":  ParamSpec(0.6,  0.3, 0.9, 0.0, "验证门控: WF一致性"),
        "max_winrate_diff":    ParamSpec(15.0, 5.0,  30.0, 0.0, "验证门控: 多空胜率差(%)"),
    },
    "stage3_paper_to_live": {
        "min_days":            ParamSpec(14,   7,   30,  0.0, "模拟→实盘: 最小运行天数"),
        "min_sharpe":          ParamSpec(0.8,  0.3, 1.5, 0.0, "模拟→实盘: 最小Sharpe"),
        "max_drawdown":        ParamSpec(0.12, 0.05, 0.20, 0.0, "模拟→实盘: 最大回撤"),
        "min_trades":          ParamSpec(30,   10,  100, 0.0, "模拟→实盘: 最小交易数"),
        "max_return_deviation": ParamSpec(0.30, 0.10, 0.50, 0.0, "模拟→实盘: 最大收益偏差"),
    },
}


# ════════════════════════════════════════════════════════
#  8. Tier 配置 (保持原样，不含参数范围)
# ════════════════════════════════════════════════════════

TIER_CONFIG: Dict[str, dict] = {
    "short": {
        "timeframes": ["5m", "15m"],
        "default_timeframe": "15m",
        "min_trades_per_year": 300,
        "ideal_trades_per_year": 800,
        "max_holding_bars": 48,
        "ideal_holding_bars": (3, 24),
        "eval_weights": {"win_rate": 0.30, "sharpe": 0.30, "frequency": 0.20, "drawdown": 0.20},
        "description": "短线/日内交易，持仓数小时",
    },
    "mid": {
        "timeframes": ["1h", "4h"],
        "default_timeframe": "1h",
        "min_trades_per_year": 50,
        "ideal_trades_per_year": 150,
        "max_holding_bars": 168,
        "ideal_holding_bars": (6, 72),
        "eval_weights": {"win_rate": 0.25, "sharpe": 0.30, "frequency": 0.15, "drawdown": 0.30},
        "description": "中线/波段交易，持仓数天",
    },
    "long": {
        "timeframes": ["4h", "1d"],
        "default_timeframe": "4h",
        "min_trades_per_year": 15,
        "ideal_trades_per_year": 50,
        "max_holding_bars": 90,
        "ideal_holding_bars": (7, 30),
        "eval_weights": {"win_rate": 0.25, "sharpe": 0.30, "frequency": 0.10, "drawdown": 0.35},
        "description": "长线/趋势跟随，持仓数周",
    },
}


# ════════════════════════════════════════════════════════
#  向后兼容导出 — 旧代码无需修改
# ════════════════════════════════════════════════════════

# backtest_evolution_engine.py 兼容
DEFAULT_SIGNAL_PARAMS: Dict[str, float] = {k: v.default for k, v in SIGNAL_PARAMS.items()}
SIGNAL_PARAM_RANGES: Dict[str, Tuple[float, float]] = {k: (v.min, v.max) for k, v in SIGNAL_PARAMS.items()}

def get_tier_signal_param_ranges(tier: str = "mid") -> dict:
    """获取指定 tier 的信号参数范围（基础范围 + tier覆盖）"""
    ranges = dict(SIGNAL_PARAM_RANGES)
    overrides = TIER_SIGNAL_PARAM_OVERRIDES.get(tier, {})
    ranges.update(overrides)
    return ranges

def get_category_defaults(category: str) -> dict:
    """获取指定策略类型的专属默认参数"""
    base = dict(DEFAULT_SIGNAL_PARAMS)
    override = CATEGORY_SIGNAL_DEFAULTS.get(category, {})
    base.update(override)
    return base

# live_pipeline_backtest_engine.py 兼容
DEFAULT_PIPELINE_PARAMS: Dict[str, float] = {k: v.default for k, v in PIPELINE_PARAMS.items()}
PIPELINE_PARAM_RANGES: Dict[str, Tuple[float, float]] = {k: (v.min, v.max) for k, v in PIPELINE_PARAMS.items()}

# strategy_evolver.py 兼容
PROMOTION_THRESHOLDS: Dict[str, float] = {
    "min_sharpe":        VALIDATION_THRESHOLDS["stage1_evolution"]["min_sharpe"].default,
    "min_win_rate":      VALIDATION_THRESHOLDS["stage1_evolution"]["min_win_rate"].default,
    "max_drawdown":      VALIDATION_THRESHOLDS["stage1_evolution"]["max_drawdown"].default,
    "min_trades":        VALIDATION_THRESHOLDS["stage1_evolution"]["min_trades"].default,
    "min_profit_factor": VALIDATION_THRESHOLDS["stage1_evolution"]["min_profit_factor"].default,
}

# 按 tier 分层的晋升阈值（min_trades 按策略类型区分）
# 修复（2026-06-24）：原阈值过严（Sharpe≥1.0/WR≥45%/short trades≥150），在加密回测
# 中几乎不可达，导致进化冠军 promoted 永远 False。放宽到现实水平，让进化策略能上场试。
PROMOTION_THRESHOLDS_BY_TIER: Dict[str, Dict[str, float]] = {
    "short": {
        "min_sharpe":        0.8,    # 原 1.0 → 0.8
        "min_win_rate":      0.40,   # 原 0.45 → 0.40
        "max_drawdown":      0.25,   # 原 0.20 → 0.25
        "min_trades":        50,     # 原 150 → 50（短线回测数据有限）
        "min_profit_factor": 1.30,   # 原 1.50 → 1.30
    },
    "mid": {
        "min_sharpe":        0.8,    # 原 1.0 → 0.8
        "min_win_rate":      0.40,   # 原 0.45 → 0.40
        "max_drawdown":      0.25,   # 原 0.20 → 0.25
        "min_trades":        40,     # 原 80 → 40
        "min_profit_factor": 1.30,   # 原 1.50 → 1.30
    },
    "long": {
        "min_sharpe":        0.6,    # 原 0.8 → 0.6
        "min_win_rate":      0.38,   # 原 0.40 → 0.38
        "max_drawdown":      0.30,   # 原 0.25 → 0.30
        "min_trades":        20,     # 原 30 → 20
        "min_profit_factor": 1.20,   # 原 1.30 → 1.20
    },
}


def get_promotion_thresholds(tier: str = "mid") -> Dict[str, float]:
    """获取指定 tier 的晋升阈值，未配置时回退到通用 PROMOTION_THRESHOLDS"""
    return PROMOTION_THRESHOLDS_BY_TIER.get(tier, PROMOTION_THRESHOLDS)


DEFAULT_EVOLUTION_CONFIG: Dict[str, float] = {k: v.default for k, v in EVOLUTION_CONFIG.items()}
# 保留 symbols 和 walk_forward_split 为兼容字段
DEFAULT_EVOLUTION_CONFIG["symbols"] = ["BTC", "ETH"]
DEFAULT_EVOLUTION_CONFIG["walk_forward_split"] = EVOLUTION_CONFIG["walk_forward_split"].default

# deterministic_risk_gate.py 兼容
DEFAULT_RULES: Dict[str, float] = {
    "max_symbol_notional_pct": RISK_LIMITS["max_symbol_notional_pct"].default / 100.0,
    "max_side_margin_pct":     RISK_LIMITS["max_side_margin_pct"].default / 100.0,
    "max_daily_loss_pct":      RISK_LIMITS["max_daily_loss_pct"].default / 100.0,
    "max_portfolio_leverage":  RISK_LIMITS["max_leverage"].default,
    "min_available_balance_pct": RISK_LIMITS["min_available_balance_pct"].default / 100.0,
}


# ════════════════════════════════════════════════════════
#  V3 Upgrade: Arbitrage & Scanner Rules (§7.3)
# ════════════════════════════════════════════════════════

ARBITRAGE_RULES: Dict[str, float] = {
    "max_hedge_delta_pct": 0.02,
    "max_total_arbitrage_pct": 0.40,
    "max_cross_exchange_exposure": 0.20,
    "min_annual_yield": 0.15,
    "funding_reversal_threshold": 3,
}

SCANNER_RULES: Dict[str, float] = {
    "min_24h_volume": 1_000_000,
    "min_volatility": 0.02,
    "max_spread": 0.005,
    "top_n": 20,
    "rescan_interval": 3600,
}


# ════════════════════════════════════════════════════════
#  一致性检查工具
# ════════════════════════════════════════════════════════

def validate_consistency() -> list:
    """验证注册表内部一致性，返回问题列表"""
    issues = []

    # 检查验证阈值渐进性: stage1 <= stage2
    s1 = VALIDATION_THRESHOLDS["stage1_evolution"]
    s2 = VALIDATION_THRESHOLDS["stage2_validation"]
    if s2["min_sharpe"].default < s1["min_sharpe"].default:
        issues.append(f"Stage2 Sharpe ({s2['min_sharpe'].default}) < Stage1 ({s1['min_sharpe'].default})")

    # 检查管线参数风险值与风控限制一致性
    bt_daily_loss = PIPELINE_PARAMS["max_daily_loss"].default
    live_daily_loss = RISK_LIMITS["max_daily_loss_pct"].default / 100.0
    if bt_daily_loss > live_daily_loss:
        issues.append(
            f"回测 max_daily_loss ({bt_daily_loss}) > 实盘 ({live_daily_loss}) — "
            f"回测更宽松会导致策略在实盘被熔断"
        )

    bt_leverage = PIPELINE_PARAMS["default_leverage"].default
    live_leverage = RISK_LIMITS["max_leverage"].default
    if bt_leverage > live_leverage:
        issues.append(
            f"回测 default_leverage ({bt_leverage}) > 实盘 max_leverage ({live_leverage})"
        )

    return issues


# 启动时自动检查
_issues = validate_consistency()
if _issues:
    for _i in _issues:
        logger.warning(f"[ParamsRegistry] 一致性问题: {_i}")
else:
    logger.info("[ParamsRegistry] 参数一致性检查通过")


# ════════════════════════════════════════════════════════
#  v3 整改：统一 genome 写入口
#  所有"写 AIStrategy.genome"的代码路径都走这里，
#  内部：行级锁 + 版本号 + SystemCoordinatorState.param_versions 记账。
# ════════════════════════════════════════════════════════

import json as _json
import threading as _threading
from datetime import datetime as _datetime, timezone as _timezone

_APPLY_GENOME_LOCK = _threading.Lock()


def apply_genome(db, strategy_id: str, genome: Dict, *, reason: str = "unknown") -> bool:
    """统一 genome 写入口（v3 整改）。

    Args:
        db: SQLAlchemy Session
        strategy_id: 策略 ID
        genome: 完整 genome dict（会被 dict() 浅拷贝后写入）
        reason: 写入原因（用于审计与 param_versions）

    Returns:
        True 写入成功；False 略过或失败（不抛异常，调用点可选择记录）
    """
    if not strategy_id or not isinstance(genome, dict):
        return False
    try:
        # 优先走 unified_learning._safe_modify_genome（行级锁 + flag_modified）
        from backend.services.unified_learning_service import unified_learning

        def _mod(existing: Dict) -> None:
            existing.clear()
            existing.update(dict(genome))

        unified_learning._safe_modify_genome(db, strategy_id, _mod)

        # 记录到 SystemCoordinatorState.param_versions（非致命，失败仅告警）
        try:
            from backend.database.models import SystemCoordinatorState as _SCS
            with _APPLY_GENOME_LOCK:
                state = db.query(_SCS).first()
                if state is None:
                    state = _SCS()
                    db.add(state)
                try:
                    versions = _json.loads(state.param_versions) if state.param_versions else {}
                except Exception:
                    versions = {}
                entry = versions.get(strategy_id, {"version": 0})
                entry["version"] = int(entry.get("version", 0)) + 1
                entry["reason"] = reason
                entry["updated_at"] = _datetime.now(_timezone.utc).isoformat()
                versions[strategy_id] = entry
                state.param_versions = _json.dumps(versions)
                db.flush()
        except Exception as _e:
            logger.debug(f"[ParamsRegistry] param_versions 记账失败 (非致命): {_e}")

        return True
    except Exception as e:
        logger.warning(f"[ParamsRegistry] apply_genome 失败 sid={strategy_id} reason={reason}: {e}")
        return False
