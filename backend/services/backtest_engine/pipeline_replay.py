"""
管线回放纯函数 — 从 live_pipeline_backtest_engine.py 提取

所有函数为纯函数（无副作用），可被回测引擎、进化系统、
验证管线等并行调用。

信号逻辑 100% 对齐实盘决策管线:
  多周期编排器 → 三维信号确认 → 规则决策引擎
"""

import numpy as np
from typing import Dict, Tuple


# ═══════════════════ 技术指标计算 ═══════════════════

def calc_ema(data: np.ndarray, span: int) -> np.ndarray:
    """指数移动平均"""
    out = np.zeros_like(data, dtype=np.float64)
    out[0] = data[0]
    k = 2.0 / (span + 1)
    for i in range(1, len(data)):
        out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


def calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI 计算 — 与实盘 RSI 完全一致"""
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


def calc_macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[np.ndarray, np.ndarray]:
    """MACD 计算 — 与实盘 MACD 完全一致

    Returns:
        (macd_line, signal_line)
    """
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line


# ═══════════════════ 编排器信号回放 ═══════════════════

def replay_mid_signal(rsi: float, macd: float, p: Dict) -> Tuple[str, float]:
    """回放编排器中期信号

    Args:
        rsi: 当前 RSI 值
        macd: 当前 MACD 值
        p: 管线参数字典

    Returns:
        (bias, confidence): bias 为 "bullish"/"bearish"/"neutral"
    """
    if rsi > p["mid_rsi_bull"] and macd > 0:
        return "bullish", min(p["mid_conf_strong"], (rsi - 45) / 40 + abs(macd) * 8)
    elif rsi < p["mid_rsi_bear"] and macd < 0:
        return "bearish", min(p["mid_conf_strong"], (55 - rsi) / 40 + abs(macd) * 8)
    elif rsi > p["mid_rsi_weak_bull"] and macd > 0:
        return "bullish", p["mid_conf_weak"]
    elif rsi < p["mid_rsi_weak_bear"] and macd < 0:
        return "bearish", p["mid_conf_weak"]
    return "neutral", p["mid_conf_neutral"]


def replay_long_signal(
    fgi: float,
    intel_dir: str,
    intel_conf_pct: float,
    p: Dict,
) -> Tuple[str, float]:
    """回放编排器长期信号（恐贪指数 + 情报）

    Returns:
        (bias, confidence)
    """
    if fgi < p["long_fgi_extreme_fear"]:
        return "bearish", 0.5
    elif fgi > p["long_fgi_extreme_greed"]:
        return "bullish", 0.5
    elif fgi < p["long_fgi_fear"]:
        return "bearish", 0.35
    elif fgi > p["long_fgi_greed"]:
        return "bullish", 0.35
    elif intel_dir in ("bullish", "bearish") and intel_conf_pct > p["long_intel_min_conf"]:
        return intel_dir, 0.3
    return "neutral", 0.05


def replay_short_signal(
    whale_dir: float,
    funding_signal: str,
    p: Dict,
) -> Tuple[str, float]:
    """回放编排器短期信号（鲸鱼 + 资金费率）

    Args:
        whale_dir: 鲸鱼方向指标（正=看多，负=看空）
        funding_signal: 资金费率方向 "bullish"/"bearish"/"neutral"

    Returns:
        (bias, confidence)
    """
    wt = p["short_whale_threshold"]
    if whale_dir > wt and funding_signal != "bearish":
        return "bullish", 0.3
    elif whale_dir < -wt and funding_signal != "bullish":
        return "bearish", 0.3
    return "neutral", 0.0


def replay_intel_fusion(
    mid_bias: str,
    mid_conf: float,
    intel_dir: str,
    intel_conf_pct: float,
    p: Dict,
) -> Tuple[str, float]:
    """情报信号对中期的融合修正

    Returns:
        (bias, confidence)
    """
    intel_conf = intel_conf_pct / 100.0
    if intel_dir in ("bullish", "bearish") and intel_conf > p["intel_fusion_min_conf"]:
        if mid_bias == "neutral":
            return intel_dir, max(mid_conf, p["intel_fusion_neutral_boost"] + intel_conf * 0.5)
        elif mid_bias == intel_dir:
            return mid_bias, min(1.0, mid_conf + p["intel_fusion_agree_boost"] + intel_conf * 0.3)
        else:
            return mid_bias, mid_conf * p["intel_fusion_conflict_mult"]
    return mid_bias, mid_conf


# ═══════════════════ 最终决策回放 ═══════════════════

def replay_finalize(
    long_bias: str,
    long_conf: float,
    mid_bias: str,
    mid_conf: float,
    short_bias: str,
    short_conf: float,
    p: Dict,
) -> Tuple[str, str, float]:
    """回放 _finalize -> (action, side, position_pct)

    Args:
        long_bias/conf: 长期信号
        mid_bias/conf: 中期信号
        short_bias/conf: 短期信号
        p: 管线参数字典

    Returns:
        (action, side, position_pct): action 为 "enter" 或 "wait"
    """
    # 方向判定
    final_side = ""
    if short_bias == "bullish":
        final_side = "long"
    elif short_bias == "bearish":
        final_side = "short"
    elif mid_bias == "bullish" and mid_conf >= p["finalize_mid_fallback_conf"]:
        final_side = "long"
    elif mid_bias == "bearish" and mid_conf >= p["finalize_mid_fallback_conf"]:
        final_side = "short"
    elif long_bias == "bullish" and long_conf >= p["finalize_long_fallback_conf"]:
        final_side = "long"
    elif long_bias == "bearish" and long_conf >= p["finalize_long_fallback_conf"]:
        final_side = "short"

    if not final_side:
        return "wait", "", 0.0

    # 置信度
    weighted_conf = (
        long_conf * p["finalize_long_weight"]
        + mid_conf * p["finalize_mid_weight"]
        + short_conf * p["finalize_short_weight"]
    )
    active_confs = []
    for bias, conf in [(long_bias, long_conf), (mid_bias, mid_conf), (short_bias, short_conf)]:
        if bias != "neutral" and conf > 0:
            active_confs.append(conf)
    max_active = max(active_confs) if active_confs else 0
    ratio = p["finalize_max_active_ratio"]
    avg_conf = max_active * ratio + weighted_conf * (1 - ratio)

    if avg_conf < p["finalize_min_conf"]:
        return "wait", "", 0.0

    pos_pct = p["max_position_size"] * avg_conf
    pos_pct = max(0.02, min(0.5, pos_pct))
    return "enter", final_side, pos_pct


# ═══════════════════ 三维确认回放 ═══════════════════

def replay_confirmation(
    tech_dir: int,
    flow_dir: int,
    sent_dir: int,
    min_dims: int = 2,
) -> Tuple[str, int, str]:
    """回放三维信号确认

    Args:
        tech_dir/flow_dir/sent_dir: +1 看多, -1 看空, 0 中性
        min_dims: 最少需要几个维度同向

    Returns:
        (action, direction, level): action 为 "BUY"/"SELL"/"HOLD"
    """
    non_zero = [(d, 1.0) for d in [tech_dir, flow_dir, sent_dir] if d != 0]
    if len(non_zero) < min_dims:
        return "HOLD", 0, "none"
    directions = [d for d, _ in non_zero]
    if not all(d == directions[0] for d in directions):
        return "HOLD", 0, "none"
    confirmed = directions[0]
    level = "strong" if len(non_zero) == 3 else "normal"
    action = "BUY" if confirmed > 0 else "SELL"
    return action, confirmed, level


# ═══════════════════ 规则引擎回放 ═══════════════════

def replay_rule_decision(
    confirm_action: str,
    confirm_dir: int,
    mid_bias: str,
    mid_conf: float,
) -> str:
    """回放规则引擎覆盖

    Args:
        confirm_action: 三维确认结果 "BUY"/"SELL"/"HOLD"
        confirm_dir: 三维确认方向 +1/-1/0
        mid_bias: 编排器中期偏向
        mid_conf: 编排器中期置信度

    Returns:
        "buy" / "sell" / "hold"
    """
    if confirm_action in ("BUY", "SELL"):
        return confirm_action.lower()
    # 三维确认为 HOLD 时，回退到编排器中期方向
    if mid_bias == "bullish" and mid_conf >= 0.2:
        return "buy"
    elif mid_bias == "bearish" and mid_conf >= 0.2:
        return "sell"
    return "hold"
