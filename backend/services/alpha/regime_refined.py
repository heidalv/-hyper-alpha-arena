"""
细分 Regime 识别（P2.7，方案 §P2.7）。

现状（诊断）：3 态（trend/range/extreme）过粗。
P2.7：扩为 ≥6 子 regime，因子表现对子 regime 极敏感。

regime（异步广播，不阻塞 tick，AlphaEnsemble 订阅切子策略）:
    trend_high_vol   趋势 + 高波动
    trend_low_vol    趋势 + 低波动
    range            区间震荡
    squeeze          逼空（OI×funding 极端）
    liquidation_cascade  连环清算
    extreme          极端（黑天鹅）
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Regime(str, Enum):
    TREND_HIGH_VOL = "trend_high_vol"
    TREND_LOW_VOL = "trend_low_vol"
    RANGE = "range"
    SQUEEZE = "squeeze"
    LIQUIDATION_CASCADE = "liquidation_cascade"
    EXTREME = "extreme"


@dataclass
class RegimeFeatures:
    """判定 regime 所需特征（由数据层产出）。

    [P5-修复] 量纲约定：volatility_pct 必须为「年化波动率小数」（如 0.6=60%、
    2.0=200%），阈值 0.6/2.0 即按此口径设计。严禁传入 per-bar 小数
    (ATR/price≈0.01~0.05) 或百分数(>1)，否则会与 decision_core/regime_agent
    （per-bar 口径）发生互斥误判。调用方需自行从 per-bar 值乘以 sqrt(252) 换算。
    """
    volatility_pct: float = 0.0       # 年化波动率（小数）
    trend_strength: float = 0.0       # 趋势强度（|ADX/100| 或回归斜率）
    funding_extreme: bool = False     # funding 极端（>0.1%/8h）
    oi_surge: bool = False            # OI 异常激增
    liquidation_burst: bool = False   # 清算爆发
    price_gap: float = 0.0            # 近期最大单 bar 跳幅（小数）


def classify_regime(f: RegimeFeatures) -> Regime:
    """
    规则 + 特征 → regime 标签。

    优先级（从极端到常态）：
        liquidation_cascade > extreme > squeeze > trend/range
    """
    # 连环清算优先
    if f.liquidation_burst and f.price_gap > 0.05:
        return Regime.LIQUIDATION_CASCADE
    # 极端（黑天鹅级跳幅）
    if f.price_gap > 0.10 or f.volatility_pct > 2.0:
        return Regime.EXTREME
    # 逼空（OI 激增 + funding 极端）
    if f.oi_surge and f.funding_extreme:
        return Regime.SQUEEZE
    # 趋势 vs 区间
    if f.trend_strength > 0.3:
        return Regime.TREND_HIGH_VOL if f.volatility_pct > 0.6 else Regime.TREND_LOW_VOL
    return Regime.RANGE
