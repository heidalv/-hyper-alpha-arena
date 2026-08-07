"""
Insert 10 mature trading strategy templates into strategy_templates table.

Coverage after insert:
  bull:    short(trend, momentum, pullback) | mid(bull_momentum, breakout) | long(trend, breakout)
  sideways: short(range, mean_reversion)    | mid(mean_reversion, range)  | long(mean_reversion)
  all:     short(scalp, vol_squeeze)        | mid(swing, high_vol)        | long(swing)

Run: python scripts/insert_mature_templates.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from database.connection import SessionLocal
from database.models import StrategyTemplate

TEMPLATES = [
    {
        "template_id": "tpl_short_scalp",
        "name": "超短线剥头皮",
        "description": "1分钟超短线剥头皮策略，适合高波动市场。快EMA交叉+成交量确认，极紧止损，追求高胜率微利。",
        "category": "momentum",
        "market_regime": "all",
        "risk_level": "aggressive",
        "timeframe": "1m",
        "tier": "short",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "EMA 3/8/21 极速交叉 + RSI 7 动态阈值 + 成交量放大确认",
            "risk_params": {
                "max_position_size": 0.08,
                "stop_loss_pct": 0.008,
                "take_profit_pct": 0.015,
                "trailing_activation_pct": 0.008,
                "trailing_distance_pct": 0.004,
                "max_daily_loss": 0.04,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "ema_fast": 3,
                    "ema_mid": 8,
                    "ema_slow": 21,
                    "rsi_period": 7,
                    "rsi_long_lo": 20,
                    "rsi_long_hi": 90,
                    "rsi_short_lo": 10,
                    "rsi_short_hi": 80,
                    "momentum_vol_mult": 1.2,
                    "min_bars_between": 1,
                }
            }
        },
        "source": "builtin",
        "rating": 3.5,
        "tags": ["短线", "剥头皮", "超高频", "EMA", "全市场"],
    },
    {
        "template_id": "tpl_mid_bull_momentum",
        "name": "中周期动量追踪",
        "description": "1小时中周期牛市动量策略。EMA多头排列+MACD金叉+成交量放大，适合趋势行情中段追涨。",
        "category": "momentum",
        "market_regime": "bull",
        "risk_level": "moderate",
        "timeframe": "1h",
        "tier": "mid",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "EMA 8/21/55 多头排列 + MACD 金叉 + 成交量放大 RSI 过滤超买",
            "risk_params": {
                "max_position_size": 0.15,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.08,
                "trailing_activation_pct": 0.03,
                "trailing_distance_pct": 0.015,
                "max_daily_loss": 0.08,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "ema_fast": 8,
                    "ema_mid": 21,
                    "ema_slow": 55,
                    "rsi_period": 14,
                    "rsi_long_lo": 35,
                    "rsi_long_hi": 85,
                    "rsi_short_lo": 15,
                    "rsi_short_hi": 65,
                    "momentum_vol_mult": 1.15,
                    "min_bars_between": 2,
                }
            }
        },
        "source": "builtin",
        "rating": 4.0,
        "tags": ["中周期", "动量", "牛市", "EMA", "MACD"],
    },
    {
        "template_id": "tpl_short_vol_squeeze",
        "name": "波动率挤压突破",
        "description": "布林带宽度收缩后放量突破策略。经典\"挤压-爆发\"模式，BB宽度降至低位后K线突破边界+成交量激增。",
        "category": "breakout",
        "market_regime": "all",
        "risk_level": "aggressive",
        "timeframe": "15m",
        "tier": "short",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "BB宽度收缩至低点 → 价格突破边界 + 成交量1.4x激增 → 方向确认",
            "risk_params": {
                "max_position_size": 0.12,
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
                "trailing_activation_pct": 0.02,
                "trailing_distance_pct": 0.01,
                "max_daily_loss": 0.06,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "breakout_lookback": 20,
                    "vol_surge_mult": 1.4,
                    "ema_fast": 5,
                    "ema_mid": 13,
                    "min_bars_between": 2,
                }
            }
        },
        "source": "builtin",
        "rating": 3.8,
        "tags": ["短线", "突破", "波动率挤压", "布林带", "成交量"],
    },
    {
        "template_id": "tpl_mid_breakout",
        "name": "中周期突破",
        "description": "4小时中周期突破策略。基于N日高低点突破+成交量1.25x确认，EMA方向过滤假突破。",
        "category": "breakout",
        "market_regime": "bull",
        "risk_level": "moderate",
        "timeframe": "4h",
        "tier": "mid",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "突破N日最高/最低 + 成交量1.25x激增 + EMA10/25方向过滤",
            "risk_params": {
                "max_position_size": 0.15,
                "stop_loss_pct": 0.04,
                "take_profit_pct": 0.12,
                "trailing_activation_pct": 0.04,
                "trailing_distance_pct": 0.02,
                "max_daily_loss": 0.10,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "breakout_lookback": 25,
                    "vol_surge_mult": 1.25,
                    "ema_fast": 10,
                    "ema_mid": 25,
                    "min_bars_between": 3,
                }
            }
        },
        "source": "builtin",
        "rating": 3.8,
        "tags": ["中周期", "突破", "牛市", "成交量"],
    },
    {
        "template_id": "tpl_long_swing",
        "name": "长线摆动交易",
        "description": "日线级别长线摆动策略。EMA 20/50/200 经典均线系统+深度回调买入+轻仓长持，适合大趋势行情。",
        "category": "swing",
        "market_regime": "all",
        "risk_level": "conservative",
        "timeframe": "1d",
        "tier": "long",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "EMA 20/50/200 大周期排列 + 回调至EMA50附近买入 + RSI过滤极端",
            "risk_params": {
                "max_position_size": 0.10,
                "stop_loss_pct": 0.10,
                "take_profit_pct": 0.25,
                "trailing_activation_pct": 0.08,
                "trailing_distance_pct": 0.05,
                "max_daily_loss": 0.12,
                "max_leverage": 20,
                "default_leverage": 5,
                "signal_params": {
                    "ema_fast": 20,
                    "ema_mid": 50,
                    "ema_slow": 200,
                    "swing_pullback_lo": -0.10,
                    "swing_pullback_hi": 0.02,
                    "rsi_long_lo": 30,
                    "rsi_long_hi": 75,
                    "rsi_short_lo": 25,
                    "rsi_short_hi": 70,
                    "min_bars_between": 5,
                }
            }
        },
        "source": "builtin",
        "rating": 4.2,
        "tags": ["长线", "摆动", "EMA", "回调买入", "保守"],
    },
    {
        "template_id": "tpl_long_mean_reversion",
        "name": "长线均值回归",
        "description": "日线级别均值回归策略。宽布林带(2.5σ)+极端RSI+低波动过滤，在价格极端偏离时逆向入场，适合横盘大周期。",
        "category": "mean_reversion",
        "market_regime": "sideways",
        "risk_level": "conservative",
        "timeframe": "1d",
        "tier": "long",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "价格触及BB 2.5σ边界 + RSI超买(>75)/超卖(<25) + 波动率低于均值 → 逆向入场",
            "risk_params": {
                "max_position_size": 0.10,
                "stop_loss_pct": 0.08,
                "take_profit_pct": 0.15,
                "trailing_activation_pct": 0.06,
                "trailing_distance_pct": 0.03,
                "max_daily_loss": 0.10,
                "max_leverage": 20,
                "default_leverage": 5,
                "signal_params": {
                    "bb_period": 20,
                    "bb_std": 2.5,
                    "rsi_ob": 75,
                    "rsi_os": 25,
                    "vol_quiet_mult": 0.85,
                    "min_bars_between": 5,
                }
            }
        },
        "source": "builtin",
        "rating": 3.8,
        "tags": ["长线", "均值回归", "布林带", "RSI", "保守"],
    },
    {
        "template_id": "tpl_mid_range",
        "name": "中周期区间交易",
        "description": "1小时中周期区间震荡策略。布林带(2.0σ)上下轨+RSI 30/70确认+成交量萎缩时进场，适合横盘行情。",
        "category": "range",
        "market_regime": "sideways",
        "risk_level": "moderate",
        "timeframe": "1h",
        "tier": "mid",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "价格触及BB 2.0σ边界 + RSI 30/70确认 + 成交量不异常放大 → 区间逆势交易",
            "risk_params": {
                "max_position_size": 0.12,
                "stop_loss_pct": 0.025,
                "take_profit_pct": 0.05,
                "trailing_activation_pct": 0.02,
                "trailing_distance_pct": 0.01,
                "max_daily_loss": 0.07,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "bb_period": 20,
                    "bb_std": 2.0,
                    "bb_edge_pct": 0.2,
                    "rsi_ob": 70,
                    "rsi_os": 30,
                    "min_bars_between": 2,
                }
            }
        },
        "source": "builtin",
        "rating": 3.8,
        "tags": ["中周期", "区间交易", "布林带", "RSI", "震荡"],
    },
    {
        "template_id": "tpl_short_pullback",
        "name": "短线回调入场",
        "description": "5分钟牛市回调策略。在EMA多头排列中价格回调至EMA_mid附近时入场，RSI过滤超卖，追求趋势中低风险入场点。",
        "category": "swing",
        "market_regime": "bull",
        "risk_level": "moderate",
        "timeframe": "5m",
        "tier": "short",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "EMA 5/12/30 多头排列 → 价格回调至EMA12附近 + RSI不超卖 → 顺势买入",
            "risk_params": {
                "max_position_size": 0.12,
                "stop_loss_pct": 0.015,
                "take_profit_pct": 0.035,
                "trailing_activation_pct": 0.015,
                "trailing_distance_pct": 0.008,
                "max_daily_loss": 0.06,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "ema_fast": 5,
                    "ema_mid": 12,
                    "ema_slow": 30,
                    "swing_pullback_lo": -0.03,
                    "swing_pullback_hi": 0.005,
                    "rsi_long_lo": 30,
                    "rsi_long_hi": 75,
                    "rsi_short_lo": 20,
                    "rsi_short_hi": 65,
                    "min_bars_between": 1,
                }
            }
        },
        "source": "builtin",
        "rating": 4.0,
        "tags": ["短线", "回调入场", "牛市", "EMA", "顺势"],
    },
    {
        "template_id": "tpl_short_mean_reversion",
        "name": "短线均值回归",
        "description": "5分钟均值回归策略。价格偏离布林带中轨+RSI极端+波动率正常时逆向入场，快进快出。",
        "category": "mean_reversion",
        "market_regime": "sideways",
        "risk_level": "moderate",
        "timeframe": "5m",
        "tier": "short",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "价格触及BB边界 + RSI超买/超卖 + 波动率正常(非挤压/非爆发) → 均值回归",
            "risk_params": {
                "max_position_size": 0.10,
                "stop_loss_pct": 0.012,
                "take_profit_pct": 0.025,
                "trailing_activation_pct": 0.01,
                "trailing_distance_pct": 0.005,
                "max_daily_loss": 0.05,
                "max_leverage": 20,
                "default_leverage": 10,
                "signal_params": {
                    "bb_period": 14,
                    "bb_std": 2.0,
                    "rsi_ob": 72,
                    "rsi_os": 28,
                    "vol_quiet_mult": 0.9,
                    "min_bars_between": 1,
                }
            }
        },
        "source": "builtin",
        "rating": 3.8,
        "tags": ["短线", "均值回归", "布林带", "RSI", "震荡"],
    },
    {
        "template_id": "tpl_mid_high_vol",
        "name": "高波动动量",
        "description": "1小时高波动动量策略。在高波动环境中捕捉强趋势，更宽的止损容忍度+更大的止盈目标，低杠杆控制风险。",
        "category": "momentum",
        "market_regime": "all",
        "risk_level": "aggressive",
        "timeframe": "1h",
        "tier": "mid",
        "strategy_config": {
            "signal_params": {},
            "strategy_logic": "EMA 10/25/60 + 成交量0.9x过滤(允许正常波动) + RSI极端阈值放宽 + 高波动中顺势追入",
            "risk_params": {
                "max_position_size": 0.10,
                "stop_loss_pct": 0.04,
                "take_profit_pct": 0.10,
                "trailing_activation_pct": 0.04,
                "trailing_distance_pct": 0.02,
                "max_daily_loss": 0.08,
                "max_leverage": 20,
                "default_leverage": 5,
                "signal_params": {
                    "ema_fast": 10,
                    "ema_mid": 25,
                    "ema_slow": 60,
                    "rsi_period": 14,
                    "rsi_long_lo": 25,
                    "rsi_long_hi": 88,
                    "rsi_short_lo": 12,
                    "rsi_short_hi": 75,
                    "momentum_vol_mult": 0.9,
                    "min_bars_between": 2,
                }
            }
        },
        "source": "builtin",
        "rating": 3.5,
        "tags": ["中周期", "高波动", "动量", "全市场", "顺势"],
    },
]


def insert_templates():
    db = SessionLocal()
    try:
        inserted = 0
        skipped = 0
        for tpl_data in TEMPLATES:
            existing = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == tpl_data["template_id"]
            ).first()
            if existing:
                print(f"  SKIP {tpl_data['template_id']} — already exists")
                skipped += 1
                continue

            tpl = StrategyTemplate(**tpl_data)
            db.add(tpl)
            inserted += 1
            print(f"  INSERT {tpl_data['template_id']} — {tpl_data['name']} [{tpl_data['category']}, {tpl_data['market_regime']}, {tpl_data['tier']}]")

        db.commit()
        print(f"\nDone: {inserted} inserted, {skipped} skipped, {inserted + skipped} total")

        # Verify
        count = db.query(StrategyTemplate).filter(StrategyTemplate.is_active == True).count()
        print(f"Active templates after insert: {count}")
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    insert_templates()
