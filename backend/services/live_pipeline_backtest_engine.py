"""
实盘管线离线回放引擎 — LivePipelineBacktestEngine

与 AI 自主交易（Full Auto）使用完全相同的决策管线：
  多周期编排器 → 三维信号确认 → 规则决策引擎

信号逻辑 100% 对齐实盘，仓位管理复用 backtest_evolution_engine 的框架。
进化器优化的参数直接控制实盘行为。
"""
import logging
import math
import os
import time
import uuid
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from backend.services.backtest_evolution_engine import (
    Bar, Position, TradeRecord, BacktestResult, TIER_CONFIG,
    TAKER_FEE, SLIPPAGE,
)

# 从统一参数注册表导入管线参数
from backend.services.strategy_params_registry import (
    DEFAULT_PIPELINE_PARAMS,
    PIPELINE_PARAM_RANGES,
)

logger = logging.getLogger(__name__)


# ═══════════════════ 默认管线参数（从注册表导入） ═══════════════════

# Legacy: TIER_RISK_DEFAULTS 保留空字典以兼容旧 import
TIER_RISK_DEFAULTS: Dict[str, Dict[str, float]] = {}


# ═══════════════════ 纯函数：实盘管线离线版 ═══════════════════

def _calc_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """与实盘 RSI 相同的计算"""
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


def _calc_macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """与实盘 MACD 相同的计算"""
    def ema(data, span):
        out = np.zeros_like(data)
        out[0] = data[0]
        k = 2.0 / (span + 1)
        for i in range(1, len(data)):
            out[i] = data[i] * k + out[i - 1] * (1 - k)
        return out
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def replay_mid_signal(rsi: float, macd: float, p: Dict) -> tuple:
    """回放编排器中期信号 → (bias, confidence)"""
    if rsi > p["mid_rsi_bull"] and macd > 0:
        return "bullish", min(p["mid_conf_strong"], (rsi - 45) / 40 + abs(macd) * 8)
    elif rsi < p["mid_rsi_bear"] and macd < 0:
        return "bearish", min(p["mid_conf_strong"], (55 - rsi) / 40 + abs(macd) * 8)
    elif rsi > p["mid_rsi_weak_bull"] and macd > 0:
        return "bullish", p["mid_conf_weak"]
    elif rsi < p["mid_rsi_weak_bear"] and macd < 0:
        return "bearish", p["mid_conf_weak"]
    return "neutral", p["mid_conf_neutral"]


def replay_long_signal(fgi: float, intel_dir: str, intel_conf_pct: float, p: Dict) -> tuple:
    """回放编排器长期信号 → (bias, confidence)"""
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


def replay_short_signal(whale_dir: float, funding_signal: str, p: Dict) -> tuple:
    """回放编排器短期信号 → (bias, confidence)"""
    wt = p["short_whale_threshold"]
    if whale_dir > wt and funding_signal != "bearish":
        return "bullish", 0.3
    elif whale_dir < -wt and funding_signal != "bullish":
        return "bearish", 0.3
    return "neutral", 0.0


def replay_intel_fusion(mid_bias: str, mid_conf: float,
                        intel_dir: str, intel_conf_pct: float, p: Dict) -> tuple:
    """情报信号对中期的融合修正 → (bias, confidence)"""
    intel_conf = intel_conf_pct / 100.0
    if intel_dir in ("bullish", "bearish") and intel_conf > p["intel_fusion_min_conf"]:
        if mid_bias == "neutral":
            return intel_dir, max(mid_conf, p["intel_fusion_neutral_boost"] + intel_conf * 0.5)
        elif mid_bias == intel_dir:
            return mid_bias, min(1.0, mid_conf + p["intel_fusion_agree_boost"] + intel_conf * 0.3)
        else:
            return mid_bias, mid_conf * p["intel_fusion_conflict_mult"]
    return mid_bias, mid_conf


def replay_finalize(long_bias: str, long_conf: float,
                    mid_bias: str, mid_conf: float,
                    short_bias: str, short_conf: float,
                    p: Dict) -> tuple:
    """回放 _finalize → (action, side, position_pct)
    action: "enter" or "wait"
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


def replay_confirmation(tech_dir: int, flow_dir: int, sent_dir: int,
                        min_dims: int = 2) -> tuple:
    """回放三维信号确认 → (action, direction, level)
    tech_dir/flow_dir/sent_dir: +1 看多, -1 看空, 0 中性
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


def replay_rule_decision(confirm_action: str, confirm_dir: int,
                         mid_bias: str, mid_conf: float) -> str:
    """回放规则引擎覆盖 → "buy" / "sell" / "hold"
    简化版：三维确认通过则用确认结果，否则回退到编排器中期
    """
    if confirm_action in ("BUY", "SELL"):
        return confirm_action.lower()
    # 三维确认为 HOLD 时，回退到编排器中期方向（弱化仓位）
    if mid_bias == "bullish" and mid_conf >= 0.2:
        return "buy"
    elif mid_bias == "bearish" and mid_conf >= 0.2:
        return "sell"
    return "hold"


# ═══════════════════ 引擎主体 ═══════════════════


class LivePipelineBacktestEngine:
    """用实盘同款管线在历史数据上回放"""

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital

    def run(
        self,
        bars: List[Bar],
        pipeline_params: Dict[str, Any],
        run_id: Optional[str] = None,
        progress_callback=None,
        tier: str = "mid",
        funding_rate_series: Optional[Dict[int, float]] = None,
        fgi_series: Optional[Dict[int, float]] = None,
    ) -> BacktestResult:
        """
        主回测循环 — 逐 bar 调用实盘同款决策管线

        Args:
            bars: K线序列
            pipeline_params: 管线参数（编排器阈值+情报权重+风控）
            funding_rate_series: {timestamp: rate} 历史资金费率
            fgi_series: {timestamp: fgi_value} 历史恐贪指数
        """
        run_id = run_id or f"lp_{uuid.uuid4().hex[:10]}"
        result = BacktestResult(run_id=run_id, bars_total=len(bars))
        result._bars_ref = bars
        t0 = time.time()

        if len(bars) < 50:
            result.error = "K线数据不足（需要至少50根）"
            return result

        p = {**DEFAULT_PIPELINE_PARAMS}
        p.update(pipeline_params)

        # 风控参数
        sl_pct = p["stop_loss_pct"]
        tp_pct = p["take_profit_pct"]
        max_pos_pct = p["max_position_size"]
        trailing_act = p["trailing_activation_pct"]
        trailing_dist = p["trailing_distance_pct"]
        be_activation = p.get("breakeven_activation_pct", 0.01)
        be_buffer = p.get("breakeven_buffer_pct", 0.002)
        lev = p["default_leverage"]

        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])
        max_holding = tier_cfg["max_holding_bars"]
        daily_loss_limit = p["max_daily_loss"]

        funding_rates = funding_rate_series or {}
        fgi_map = fgi_series or {}

        # 预计算指标
        closes = np.array([b.c for b in bars], dtype=np.float64)
        rsi_arr = _calc_rsi(closes, 14)
        macd_line, _ = _calc_macd(closes)

        warmup = 30
        min_bars_gap = 3

        # 状态
        equity = self.initial_capital
        peak_equity = equity
        position: Optional[Position] = None
        trades: List[TradeRecord] = []
        equity_curve = [equity]
        last_exit_bar = -min_bars_gap
        day_equity_start = equity
        last_day_ts = bars[0].timestamp if bars else 0
        circuit_breaker_until = 0
        total_funding_fees = 0.0

        # 资金费率间隔估算
        if len(bars) > 1:
            bar_interval = bars[1].timestamp - bars[0].timestamp
            bars_per_8h = max(1, int(28800 / max(bar_interval, 1)))
        else:
            bars_per_8h = 8

        data_dims_used = {"rsi_macd": True, "funding": bool(funding_rates), "fgi": bool(fgi_map), "factor_signal": float(p.get("factor_signal_weight", 0.3)) > 0}

        for i in range(warmup, len(bars)):
            bar = bars[i]

            # 跨日重置
            if bar.timestamp - last_day_ts >= 86400:
                day_equity_start = equity
                last_day_ts = bar.timestamp
                if circuit_breaker_until and bar.timestamp >= circuit_breaker_until:
                    circuit_breaker_until = 0

            # 熔断期
            if circuit_breaker_until and bar.timestamp < circuit_breaker_until:
                if position:
                    equity, trade = self._close_position(position, bar, equity, "circuit_breaker")
                    trades.append(trade)
                    position = None
                    last_exit_bar = i
                equity_curve.append(equity)
                continue

            # 持仓管理
            if position:
                # 资金费率（[P0-5 相位修复] 按 8h UTC 边界 00/08/16 结算：跨边界即结算一次。
                # 原 i % bars_per_8h 相对序列起点对齐，结算相位与交易所真实时刻脱钩。）
                _prev_ts = bars[i - 1].timestamp if i > 0 else bar.timestamp
                if (bar.timestamp // 28800) != (_prev_ts // 28800):
                    fr = self._get_funding_rate(bar.timestamp, funding_rates)
                    fee_usd = position.quantity * bar.c * fr
                    if position.side == "long":
                        equity -= fee_usd
                    else:
                        equity += fee_usd
                    total_funding_fees += abs(fee_usd)

                # 极值追踪
                position.highest_since_entry = max(position.highest_since_entry, bar.h)
                position.lowest_since_entry = min(position.lowest_since_entry, bar.l)

                # 超时平仓
                if (i - position.entry_bar) >= max_holding:
                    equity, trade = self._close_position(position, bar, equity, "timeout")
                    trades.append(trade)
                    position = None
                    last_exit_bar = i
                    equity_curve.append(equity)
                    continue

                # 止损止盈
                closed = False
                if position.side == "long":
                    if bar.l <= position.sl_price:
                        equity, trade = self._close_position(position, bar, equity, "stop_loss", position.sl_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                    elif bar.h >= position.tp_price:
                        equity, trade = self._close_position(position, bar, equity, "take_profit", position.tp_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                else:
                    if bar.h >= position.sl_price:
                        equity, trade = self._close_position(position, bar, equity, "stop_loss", position.sl_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True
                    elif bar.l <= position.tp_price:
                        equity, trade = self._close_position(position, bar, equity, "take_profit", position.tp_price)
                        trades.append(trade)
                        position = None
                        last_exit_bar = i
                        closed = True

                # 保本止损 + 移动止损（与实盘 paper_trading_engine 对齐）
                if position and not closed:
                    if position.side == "long":
                        profit_pct = (bar.c - position.entry_price) / position.entry_price
                        # 保本止损推进
                        if profit_pct >= be_activation:
                            be_sl = position.entry_price * (1 + be_buffer)
                            if position.sl_price < be_sl:
                                position.sl_price = be_sl
                        # 追踪止损
                        if profit_pct >= trailing_act:
                            position.trailing_activated = True
                        if position.trailing_activated:
                            new_trail = bar.c * (1 - trailing_dist)
                            position.trailing_price = max(position.trailing_price, new_trail)
                            if bar.l <= position.trailing_price:
                                equity, trade = self._close_position(position, bar, equity, "trailing_stop", position.trailing_price)
                                trades.append(trade)
                                position = None
                                last_exit_bar = i
                    else:
                        profit_pct = (position.entry_price - bar.c) / position.entry_price
                        # 保本止损推进
                        if profit_pct >= be_activation:
                            be_sl = position.entry_price * (1 - be_buffer)
                            if position.sl_price > be_sl or position.sl_price == 0:
                                position.sl_price = be_sl
                        # 追踪止损
                        if profit_pct >= trailing_act:
                            position.trailing_activated = True
                        if position.trailing_activated:
                            new_trail = bar.c * (1 + trailing_dist)
                            if position.trailing_price == 0:
                                position.trailing_price = new_trail
                            else:
                                position.trailing_price = min(position.trailing_price, new_trail)
                            if bar.h >= position.trailing_price:
                                equity, trade = self._close_position(position, bar, equity, "trailing_stop", position.trailing_price)
                                trades.append(trade)
                                position = None
                                last_exit_bar = i

                equity_curve.append(self._mark_equity(equity, position, bar))
                peak_equity = max(peak_equity, equity_curve[-1])
                continue

            # 单日熔断
            if day_equity_start > 0 and (day_equity_start - equity) / day_equity_start > daily_loss_limit:
                circuit_breaker_until = bar.timestamp + 86400
                equity_curve.append(equity)
                continue

            # ═══════ 核心：实盘管线信号检测 ═══════
            if equity > 0 and (i - last_exit_bar) >= min_bars_gap:
                signal = self._pipeline_signal(i, bars, rsi_arr, macd_line, p, funding_rates, fgi_map)

                if signal in ("long", "short"):
                    # [P0-5 前视修复] 信号在 bar i 收盘产生，默认按【下一根开盘】成交
                    # （next_open）；原实现按 bar i 收盘成交 = 收盘决策按收盘成交的前视，
                    # 系统性高估回测收益并误导 GA/晋升。env BACKTEST_LP_FILL_MODEL=close
                    # 仅用于旧口径对比。
                    _fill_model = os.getenv("BACKTEST_LP_FILL_MODEL", "next_open").lower()
                    if _fill_model == "next_open" and i + 1 < len(bars):
                        _fill_bar = bars[i + 1]
                        _entry_bar = i + 1
                    else:
                        _fill_bar = bar
                        _entry_bar = i
                    _fill_price = float(getattr(_fill_bar, "o", _fill_bar.c) or _fill_bar.c)
                    pos_size_usd = equity * max_pos_pct * lev
                    qty = pos_size_usd / _fill_price
                    open_fee = pos_size_usd * TAKER_FEE
                    equity -= open_fee

                    if signal == "long":
                        entry = _fill_price * (1 + SLIPPAGE)
                        sl = entry * (1 - sl_pct)
                        tp = entry * (1 + tp_pct)
                    else:
                        entry = _fill_price * (1 - SLIPPAGE)
                        sl = entry * (1 + sl_pct)
                        tp = entry * (1 - tp_pct)

                    position = Position(
                        side=signal, entry_price=entry, quantity=qty,
                        leverage=lev, entry_bar=_entry_bar, entry_time=_fill_bar.dt_str,
                        sl_price=sl, tp_price=tp,
                        highest_since_entry=_fill_bar.h, lowest_since_entry=_fill_bar.l,
                    )

            equity_curve.append(self._mark_equity(equity, position, bar))
            peak_equity = max(peak_equity, equity_curve[-1])

            if progress_callback and i % 500 == 0:
                progress_callback(i / len(bars))

        # 结束时强制平仓
        if position:
            equity, trade = self._close_position(position, bars[-1], equity, "end_of_data")
            trades.append(trade)

        result.trades = trades
        result.equity_curve = equity_curve
        result.final_equity = equity
        result.total_trades = len(trades)
        result.funding_fees_total = total_funding_fees
        result.duration_seconds = time.time() - t0

        if not hasattr(result, 'data_completeness'):
            result.data_completeness = sum(1 for v in data_dims_used.values() if v)

        self._calculate_metrics(result)
        return result

    # ═══════ 管线信号 — 核心 ═══════

    def _pipeline_signal(self, i: int, bars: List[Bar],
                         rsi_arr: np.ndarray, macd_arr: np.ndarray,
                         p: Dict, funding_rates: Dict, fgi_map: Dict) -> Optional[str]:
        """调用实盘同款管线判定开仓信号"""
        bar = bars[i]
        rsi_val = float(rsi_arr[i])
        macd_val = float(macd_arr[i])

        # 1. 中期信号（RSI/MACD — 与实盘编排器完全相同）
        mid_bias, mid_conf = replay_mid_signal(rsi_val, macd_val, p)

        # 2. 长期信号（恐贪指数）
        fgi = self._get_fgi(bar.timestamp, fgi_map)
        # 简化情报方向：用最近N根的 RSI 趋势估算
        intel_dir = "neutral"
        intel_conf_pct = 0.0
        if i >= 10:
            rsi_avg_recent = float(np.mean(rsi_arr[max(0, i - 5):i + 1]))
            rsi_avg_past = float(np.mean(rsi_arr[max(0, i - 10):max(0, i - 5)]))
            if rsi_avg_recent > rsi_avg_past + 3:
                intel_dir = "bullish"
                intel_conf_pct = min(40, (rsi_avg_recent - rsi_avg_past) * 3)
            elif rsi_avg_recent < rsi_avg_past - 3:
                intel_dir = "bearish"
                intel_conf_pct = min(40, (rsi_avg_past - rsi_avg_recent) * 3)

        long_bias, long_conf = replay_long_signal(fgi, intel_dir, intel_conf_pct, p)

        # 3. 情报融合（修正中期）
        mid_bias, mid_conf = replay_intel_fusion(mid_bias, mid_conf, intel_dir, intel_conf_pct, p)

        # 4. 短期信号（鲸鱼用 0，资金费率从历史数据）
        whale_dir = 0.0
        fr = self._get_funding_rate(bar.timestamp, funding_rates)
        if fr > 0.0003:
            funding_signal = "bullish"
        elif fr < -0.0003:
            funding_signal = "bearish"
        else:
            funding_signal = "neutral"
        short_bias, short_conf = replay_short_signal(whale_dir, funding_signal, p)

        # 5. 编排器最终决策
        action, side, pos_pct = replay_finalize(
            long_bias, long_conf, mid_bias, mid_conf, short_bias, short_conf, p
        )
        if action != "enter":
            return None

        # ═══════ V3 整合：因子信号作为额外维度 ═══════
        factor_dir = self._compute_factor_direction(i, bars, p)

        # 6. 三维确认（技术面 / 订单流 / 情绪面）— 融入 weight_* 参数
        w_funding = float(p.get("weight_funding", 0.22))
        w_oi = float(p.get("weight_oi", 0.22))
        w_fgi = float(p.get("weight_fear_greed", 0.06))
        w_whale = float(p.get("weight_whale", 0.10))

        tech_dir = 0
        if rsi_val > 55 and macd_val > 0:
            tech_dir = 1
        elif rsi_val < 45 and macd_val < 0:
            tech_dir = -1

        # 订单流方向（资金费率加权）
        flow_raw = 0.0
        if fr > 0.0002:
            flow_raw = -1.0 * w_funding
        elif fr < -0.0002:
            flow_raw = 1.0 * w_funding
        flow_dir = 1 if flow_raw > 0.05 else (-1 if flow_raw < -0.05 else 0)

        # 情绪面方向（恐贪加权）
        sent_raw = 0.0
        if fgi < 35:
            sent_raw = -1.0 * w_fgi
        elif fgi > 65:
            sent_raw = 1.0 * w_fgi
        sent_dir = 1 if sent_raw > 0.01 else (-1 if sent_raw < -0.01 else 0)

        # 因子信号融合到技术面维度
        factor_signal_weight = float(p.get("factor_signal_weight", 0.3))
        if factor_dir != 0 and factor_signal_weight > 0:
            # 因子方向与已有技术面融合
            if tech_dir == 0:
                # 技术面无方向时，因子单独提供方向
                tech_dir = factor_dir
            elif tech_dir == factor_dir:
                # 方向一致时，增强（不改变方向，仅影响后续决策的置信度感知）
                pass
            else:
                # 方向冲突时，按因子权重衰减技术面
                # 如果因子权重足够大，可以翻转技术面方向
                if factor_signal_weight > 0.5:
                    tech_dir = factor_dir

        confirm_action, confirm_dir, confirm_level = replay_confirmation(
            tech_dir, flow_dir, sent_dir, int(p["confirmation_min_dims"])
        )

        # 7. 规则引擎覆盖
        final_op = replay_rule_decision(confirm_action, confirm_dir, mid_bias, mid_conf)

        if final_op == "buy":
            return "long"
        elif final_op == "sell":
            return "short"
        return None

    # ═══════ V3 整合：因子信号计算 ═══════

    def _compute_factor_direction(
        self, i: int, bars: List[Bar], p: Dict
    ) -> int:
        """
        V3 整合：使用因子引擎计算当前 bar 的因子信号方向。
        
        使用滑动窗口 K 线数据计算因子值，通过 FactorSignalGenerator 生成合成信号。
        当 factor_signal_weight == 0 时跳过（完全关闭因子信号）。
        
        Returns:
            1 (看多), -1 (看空), 0 (中性)
        """
        factor_signal_weight = float(p.get("factor_signal_weight", 0.3))
        factor_signal_interval = int(p.get("factor_signal_interval", 6))

        # 因子信号关闭时跳过
        if factor_signal_weight <= 0:
            return 0

        # 按间隔计算因子（避免每根 bar 都计算，提升性能）
        if i % factor_signal_interval != 0:
            # 使用缓存的因子方向
            cached = getattr(self, '_cached_factor_dir', 0)
            return cached

        try:
            from services.factor_engine import factor_engine, FactorSignalGenerator

            # 使用最近 30 根 K 线作为计算窗口
            window_start = max(0, i - 29)
            window_bars = bars[window_start:i + 1]
            if len(window_bars) < 15:
                return 0

            # 构建 DataFrame
            import pandas as pd
            klines_df = pd.DataFrame([{
                'open': b.o, 'high': b.h, 'low': b.l,
                'close': b.c, 'volume': b.v,
                'timestamp': b.timestamp,
            } for b in window_bars])

            # 计算因子值
            factor_values = factor_engine.compute_all_factors(klines_df)
            if not factor_values:
                return 0

            # 生成因子信号
            signal_gen = FactorSignalGenerator()
            composite = signal_gen.generate_signals(factor_values)

            # 根据合成信号方向返回
            threshold = 0.3
            if composite.direction > threshold and composite.strength > 0.3:
                factor_dir = 1
            elif composite.direction < -threshold and composite.strength > 0.3:
                factor_dir = -1
            else:
                factor_dir = 0

            # 缓存结果
            self._cached_factor_dir = factor_dir
            return factor_dir

        except Exception as e:
            # 因子计算失败时静默降级，不影响原有信号管线
            return 0

    # ═══════ 工具函数 ═══════

    @staticmethod
    def _get_funding_rate(ts: int, rates: Dict[int, float]) -> float:
        """[P0-5 前视修复] 只取 ≤ ts 的最近历史样本（backward）。

        原实现 min(abs(t-ts)) 会命中决策点之后的未来样本（±1 天），
        回放引擎因此系统性高估收益、与实盘不一致。未来样本一律不可用。
        """
        if not rates:
            return 0.0
        past = [t for t in rates.keys() if t <= ts]
        if not past:
            return 0.0
        closest = max(past)
        if ts - closest < 86400:
            return rates[closest]
        return 0.0

    @staticmethod
    def _get_fgi(ts: int, fgi_map: Dict[int, float]) -> float:
        """[P0-5 前视修复] 同 funding：只取 ≤ ts 的最近样本。"""
        if not fgi_map:
            return 50.0
        past = [t for t in fgi_map.keys() if t <= ts]
        if not past:
            return 50.0
        closest = max(past)
        if ts - closest < 86400 * 2:
            return fgi_map[closest]
        return 50.0

    @staticmethod
    def _mark_equity(cash_equity: float, position: Optional[Position], bar: Bar) -> float:
        if not position:
            return cash_equity
        if position.side == "long":
            unrealized = (bar.c - position.entry_price) * position.quantity
        else:
            unrealized = (position.entry_price - bar.c) * position.quantity
        return cash_equity + unrealized

    @staticmethod
    def _close_position(position: Position, bar: Bar, equity: float,
                        reason: str, exit_price: float = None) -> tuple:
        if exit_price is None:
            exit_price = bar.c
        notional = position.quantity * exit_price
        close_fee = notional * TAKER_FEE
        if position.side == "long":
            pnl = (exit_price - position.entry_price) * position.quantity - close_fee
        else:
            pnl = (position.entry_price - exit_price) * position.quantity - close_fee
        margin = position.quantity * position.entry_price / position.leverage
        pnl_pct = pnl / margin if margin > 0 else 0
        equity += pnl
        trade = TradeRecord(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            leverage=position.leverage,
            entry_bar=position.entry_bar,
            exit_bar=bar.idx,
            entry_time=position.entry_time,
            exit_time=bar.dt_str,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=close_fee + (notional * TAKER_FEE),
            exit_reason=reason,
        )
        return equity, trade

    def _calculate_metrics(self, result: BacktestResult):
        if not result.trades:
            return
        wins = [t for t in result.trades if t.pnl > 0]
        losses = [t for t in result.trades if t.pnl <= 0]
        result.win_rate = len(wins) / len(result.trades) if result.trades else 0
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))
        result.profit_factor = total_profit / total_loss if total_loss > 0 else 999
        result.total_return = (result.final_equity - self.initial_capital) / self.initial_capital

        if hasattr(result, '_bars_ref') and result._bars_ref and len(result._bars_ref) > 1:
            ts_range = result._bars_ref[-1].timestamp - result._bars_ref[0].timestamp
            years = max(ts_range / (365.25 * 86400), 0.01)
            ratio = result.final_equity / self.initial_capital
            result.annualized_return = (ratio ** (1 / years) - 1) if ratio > 0 else -1
        else:
            result.annualized_return = result.total_return

        # 最大回撤
        eq = np.array(result.equity_curve)
        if len(eq) > 0:
            peaks = np.maximum.accumulate(eq)
            dd = (peaks - eq) / np.where(peaks > 0, peaks, 1)
            result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0
        # Sharpe
        pnl_pcts = [t.pnl_pct for t in result.trades]
        if len(pnl_pcts) > 1:
            avg_r = np.mean(pnl_pcts)
            std_r = np.std(pnl_pcts)
            trades_per_year = len(result.trades) / max(0.01,
                (result._bars_ref[-1].timestamp - result._bars_ref[0].timestamp) / (365.25 * 86400)) if hasattr(result, '_bars_ref') else 100
            result.sharpe_ratio = (avg_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0
            result.avg_trade_return = avg_r
        # 连续胜负
        streak_w = streak_l = max_w = max_l = 0
        for t in result.trades:
            if t.pnl > 0:
                streak_w += 1
                streak_l = 0
            else:
                streak_l += 1
                streak_w = 0
            max_w = max(max_w, streak_w)
            max_l = max(max_l, streak_l)
        result.max_consecutive_wins = max_w
        result.max_consecutive_losses = max_l
        if result.trades:
            bars_held = [t.exit_bar - t.entry_bar for t in result.trades]
            result.avg_holding_bars = np.mean(bars_held) if bars_held else 0
