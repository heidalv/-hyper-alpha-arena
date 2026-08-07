"""
回测引擎 — 高速历史数据回放 + 模拟交易

所有信号检测参数均可通过 signal_params 外部传入，
使得 AI 和进化器能真正修改入场/出场逻辑。
"""
import logging
import json
import math
import time
import uuid
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# 统一费率（Phase 3B §修复⑥）：与 backtest_engine.py 保持一致
# HyperLiquid taker 0.035%，旧值 0.0006 已修正
from backend.services.backtest_engine.backtest_engine import TAKER_FEE as _TAKER_FEE
TAKER_FEE = _TAKER_FEE   # 0.00035
SLIPPAGE = 0.0003

# ══════════════════════════════════════════════════
#  从统一参数注册表导入所有参数定义
# ══════════════════════════════════════════════════
from backend.services.strategy_params_registry import (
    TIER_CONFIG,
    TIER_SIGNAL_PARAM_OVERRIDES,
    DEFAULT_SIGNAL_PARAMS,
    SIGNAL_PARAM_RANGES,
    CATEGORY_SIGNAL_DEFAULTS,
    CATEGORY_KEY_PARAMS,
)
from backend.services.strategy_params_registry import (
    get_tier_signal_param_ranges,
    get_category_defaults,
)


@dataclass
class Bar:
    timestamp: int
    dt_str: str
    o: float
    h: float
    l: float
    c: float
    v: float
    idx: int = 0


@dataclass
class Position:
    side: str
    entry_price: float
    quantity: float
    leverage: float
    entry_bar: int
    entry_time: str
    sl_price: float = 0.0
    tp_price: float = 0.0
    trailing_price: float = 0.0
    trailing_activated: bool = False
    highest_since_entry: float = 0.0
    lowest_since_entry: float = float('inf')


@dataclass
class TradeRecord:
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    leverage: float
    entry_bar: int
    exit_bar: int
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    fee: float
    exit_reason: str


@dataclass
class BacktestResult:
    run_id: str
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_trade_return: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_holding_bars: float = 0.0
    final_equity: float = 0.0
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    duration_seconds: float = 0.0
    bars_total: int = 0
    error: Optional[str] = None
    regime_performance: Dict[str, Dict] = field(default_factory=dict)
    funding_fees_total: float = 0.0


class BacktestEngine:
    """回测引擎 — 所有参数均可外部调控，支持 tier 感知"""

    def __init__(self, initial_capital: float = 10000.0, leverage: float = 1.0):
        self.initial_capital = initial_capital
        self.leverage = leverage

    def run(
        self,
        bars: List[Bar],
        strategy_config: Dict[str, Any],
        risk_params: Dict[str, Any],
        run_id: Optional[str] = None,
        progress_callback=None,
        tier: str = "mid",
    ) -> BacktestResult:
        run_id = run_id or f"bt_{uuid.uuid4().hex[:10]}"
        result = BacktestResult(run_id=run_id, bars_total=len(bars))
        result._bars_ref = bars
        t0 = time.time()

        if len(bars) < 50:
            result.error = "K线数据不足（需要至少50根）"
            return result

        equity = self.initial_capital
        peak_equity = equity
        position: Optional[Position] = None
        trades: List[TradeRecord] = []
        equity_curve = [equity]

        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["mid"])
        sl_pct = risk_params.get("stop_loss_pct", 0.025)
        tp_pct = risk_params.get("take_profit_pct", 0.075)
        max_pos_pct = risk_params.get("max_position_size", 0.20)
        trailing_activation = risk_params.get("trailing_activation_pct", 0.015)
        trailing_distance = risk_params.get("trailing_distance_pct", 0.012)
        breakeven_activation = risk_params.get("breakeven_activation_pct", 0.01)
        breakeven_buffer = risk_params.get("breakeven_buffer_pct", 0.002)
        lev = risk_params.get("default_leverage", self.leverage)

        max_holding = tier_cfg["max_holding_bars"]
        daily_loss_limit = risk_params.get("max_daily_loss", 0.10)

        # 入场信号参数（可被 AI 修改的核心）
        sp = {**DEFAULT_SIGNAL_PARAMS}
        sp.update(risk_params.get("signal_params", {}))
        tier_ranges = get_tier_signal_param_ranges(tier)
        for k, v in sp.items():
            if k in tier_ranges:
                lo, hi = tier_ranges[k]
                if isinstance(v, (int, float)):
                    sp[k] = type(v)(max(lo, min(hi, v)))

        # 按当前参数计算技术指标
        closes = np.array([b.c for b in bars], dtype=np.float64)
        highs = np.array([b.h for b in bars], dtype=np.float64)
        lows = np.array([b.l for b in bars], dtype=np.float64)
        volumes = np.array([b.v for b in bars], dtype=np.float64)

        ema_fast = self._ema(closes, int(sp["ema_fast"]))
        ema_mid = self._ema(closes, int(sp["ema_mid"]))
        ema_slow = self._ema(closes, int(sp["ema_slow"]))
        rsi = self._rsi(closes, int(sp["rsi_period"]))
        atr = self._atr(highs, lows, closes, 14)
        bb_upper, bb_mid, bb_lower = self._bollinger(closes, int(sp["bb_period"]), float(sp["bb_std"]))
        macd_line, signal_line, macd_hist = self._macd(
            closes, int(sp["macd_fast"]), int(sp["macd_slow"]), int(sp["macd_signal"])
        )
        vol_ma = self._sma(volumes, 20)

        # 市况检测（按段标记）
        from backend.services.market_regime_detector import detect_regime
        regime_at_bar = ["ranging"] * len(bars)
        for i in range(30, len(bars)):
            if i % 10 == 0:
                regime_at_bar[i] = detect_regime(closes, highs, lows, 20, i)
            else:
                regime_at_bar[i] = regime_at_bar[i - 1]

        warmup = max(int(sp["ema_slow"]), int(sp["bb_period"]), int(sp["macd_slow"])) + 5
        min_bars_gap = int(sp.get("min_bars_between", 3))
        last_exit_bar = -min_bars_gap

        # 资金费率模拟（每8小时0.01%，根据K线间隔推算）
        funding_rate = 0.0001
        if len(bars) >= 2:
            bar_interval_s = bars[1].timestamp - bars[0].timestamp
            bars_per_8h = max(1, int(8 * 3600 / max(bar_interval_s, 1)))
        else:
            bars_per_8h = 8

        total_funding_fees = 0.0

        # 单日亏损熔断跟踪
        day_equity_start = equity
        last_day_ts = bars[0].timestamp if bars else 0
        circuit_breaker_until = 0

        # 市况分桶统计
        regime_trades: Dict[str, list] = {"trending": [], "ranging": [], "volatile": []}

        for i in range(warmup, len(bars)):
            bar = bars[i]

            # 单日亏损熔断检测（每24h重置）
            if bar.timestamp - last_day_ts >= 86400:
                day_equity_start = equity
                last_day_ts = bar.timestamp
                circuit_breaker_until = 0

            if circuit_breaker_until > 0 and bar.timestamp < circuit_breaker_until:
                if position:
                    equity = self._close_position(position, bar.c, bar, equity, trades, "circuit_breaker", lev)
                    last_exit_bar = i
                    position = None
                equity_curve.append(equity)
                continue

            # 资金费率扣除
            if position and i % bars_per_8h == 0:
                notional = position.quantity * bar.c
                ff = notional * funding_rate
                if position.side == "long":
                    equity -= ff
                else:
                    equity += ff * 0.5
                total_funding_fees += ff

            if position:
                position.highest_since_entry = max(position.highest_since_entry, bar.h)
                position.lowest_since_entry = min(position.lowest_since_entry, bar.l)

                # 持仓超时强平
                bars_held = i - position.entry_bar
                if bars_held >= max_holding:
                    equity = self._close_position(position, bar.c, bar, equity, trades, "timeout", lev)
                    last_exit_bar = i
                    position = None

            if position:
                # 止损/止盈
                sl_hit = False
                tp_hit = False
                if position.side == "long":
                    sl_hit = bar.l <= position.sl_price
                    tp_hit = bar.h >= position.tp_price
                elif position.side == "short":
                    sl_hit = bar.h >= position.sl_price
                    tp_hit = bar.l <= position.tp_price

                if sl_hit and tp_hit:
                    sl_dist = abs(bar.o - position.sl_price)
                    tp_dist = abs(bar.o - position.tp_price)
                    if sl_dist <= tp_dist:
                        equity = self._close_position(position, position.sl_price, bar, equity, trades, "sl", lev)
                    else:
                        equity = self._close_position(position, position.tp_price, bar, equity, trades, "tp", lev)
                    last_exit_bar = i
                    position = None
                elif sl_hit:
                    equity = self._close_position(position, position.sl_price, bar, equity, trades, "sl", lev)
                    last_exit_bar = i
                    position = None
                elif tp_hit:
                    equity = self._close_position(position, position.tp_price, bar, equity, trades, "tp", lev)
                    last_exit_bar = i
                    position = None

                # 保本止损 + 移动止损（与实盘 paper_trading_engine 对齐）
                if position:
                    pnl_pct = self._unrealized_pnl_pct(position, bar.c)

                    # 保本止损：盈利达到阈值时推进 SL 到入场价附近
                    if pnl_pct >= breakeven_activation:
                        if position.side == "long":
                            be_sl = position.entry_price * (1 + breakeven_buffer)
                            if position.sl_price < be_sl:
                                position.sl_price = be_sl
                        else:
                            be_sl = position.entry_price * (1 - breakeven_buffer)
                            if position.sl_price > be_sl or position.sl_price == 0:
                                position.sl_price = be_sl

                    # 渐进式追踪止损
                    if pnl_pct >= trailing_activation:
                        position.trailing_activated = True
                    if position.trailing_activated:
                        if position.side == "long":
                            new_trail = bar.c * (1 - trailing_distance)
                            if new_trail > position.trailing_price:
                                position.trailing_price = new_trail
                            if bar.l <= position.trailing_price:
                                equity = self._close_position(position, position.trailing_price, bar, equity, trades, "trailing", lev)
                                last_exit_bar = i
                                position = None
                        else:
                            new_trail = bar.c * (1 + trailing_distance)
                            if position.trailing_price == 0 or new_trail < position.trailing_price:
                                position.trailing_price = new_trail
                            if bar.h >= position.trailing_price:
                                equity = self._close_position(position, position.trailing_price, bar, equity, trades, "trailing", lev)
                                last_exit_bar = i
                                position = None

            # 记录刚平仓交易的市况
            if trades and trades[-1].exit_bar == i:
                regime = regime_at_bar[trades[-1].entry_bar]
                if regime in regime_trades:
                    regime_trades[regime].append(trades[-1])

            # 单日亏损熔断检查
            if day_equity_start > 0 and (day_equity_start - equity) / day_equity_start > daily_loss_limit:
                circuit_breaker_until = bar.timestamp + 86400
                if position:
                    equity = self._close_position(position, bar.c, bar, equity, trades, "circuit_breaker", lev)
                    last_exit_bar = i
                    position = None
                equity_curve.append(equity)
                continue

            # 入场信号（含冷却期检查）
            if not position and equity > 0 and (i - last_exit_bar) >= min_bars_gap:
                sig = self._detect_signal(
                    i, bar, closes, ema_fast, ema_mid, ema_slow, rsi, atr,
                    bb_upper, bb_mid, bb_lower, macd_line, signal_line, macd_hist,
                    volumes, vol_ma, strategy_config, sp,
                )
                if sig and sig != "hold":
                    pos_size_usd = equity * max_pos_pct * lev
                    qty = pos_size_usd / bar.c
                    fee = pos_size_usd * TAKER_FEE
                    entry = bar.c * (1 + SLIPPAGE) if sig == "long" else bar.c * (1 - SLIPPAGE)

                    if sig == "long":
                        sl = entry * (1 - sl_pct)
                        tp = entry * (1 + tp_pct)
                    else:
                        sl = entry * (1 + sl_pct)
                        tp = entry * (1 - tp_pct)

                    equity -= fee
                    position = Position(
                        side=sig, entry_price=entry, quantity=qty,
                        leverage=lev, entry_bar=i, entry_time=bar.dt_str,
                        sl_price=sl, tp_price=tp,
                        highest_since_entry=bar.h, lowest_since_entry=bar.l,
                    )

            if position:
                upnl = self._unrealized_pnl(position, bar.c)
                equity_curve.append(equity + upnl)
            else:
                equity_curve.append(equity)
            if equity_curve[-1] > peak_equity:
                peak_equity = equity_curve[-1]

            if progress_callback and i % 500 == 0:
                progress_callback(i / len(bars))

        if position:
            equity = self._close_position(position, bars[-1].c, bars[-1], equity, trades, "end_of_data", lev)

        result.duration_seconds = time.time() - t0
        result.trades = trades
        result.equity_curve = equity_curve
        result.final_equity = equity
        result.funding_fees_total = total_funding_fees

        # 市况分桶绩效
        for regime, rtrades in regime_trades.items():
            if rtrades:
                wins = [t for t in rtrades if t.pnl > 0]
                result.regime_performance[regime] = {
                    "trades": len(rtrades),
                    "win_rate": len(wins) / len(rtrades),
                    "avg_pnl_pct": float(np.mean([t.pnl_pct for t in rtrades])),
                    "total_pnl": sum(t.pnl for t in rtrades),
                }

        self._calculate_metrics(result, trades, equity_curve)
        return result

    # ══════════════════════════════════════════════════
    #  信号检测 — 全部参数化
    # ══════════════════════════════════════════════════

    def _detect_signal(
        self, i, bar, closes, ema_f, ema_m, ema_s, rsi, atr,
        bb_up, bb_mid, bb_lo, macd, signal, hist, volumes, vol_ma,
        config, sp,
    ) -> Optional[str]:
        category = config.get("category", "trend")
        logic = config.get("strategy_logic", "")

        if category == "trend" or "趋势" in logic:
            return self._signal_trend(i, closes, ema_f, ema_m, ema_s, rsi, macd, signal, hist, volumes, vol_ma, sp)
        elif category == "mean_reversion" or "均值回归" in logic or "布林" in logic:
            return self._signal_mean_reversion(i, closes, rsi, bb_up, bb_mid, bb_lo, volumes, vol_ma, sp)
        elif category == "range" or "区间" in logic or "网格" in logic:
            return self._signal_range(i, closes, rsi, bb_up, bb_lo, atr, sp)
        elif category == "breakout" or "突破" in logic:
            return self._signal_breakout(i, closes, atr, volumes, vol_ma, ema_f, ema_m, sp)
        elif category == "swing" or "波段" in logic or "斐波那契" in logic:
            return self._signal_swing(i, closes, ema_f, ema_m, ema_s, rsi, atr, macd, signal, sp)
        elif category == "momentum" or "动量" in logic:
            return self._signal_momentum(i, closes, ema_f, ema_m, ema_s, rsi, macd, hist, volumes, vol_ma, sp)
        else:
            return self._signal_trend(i, closes, ema_f, ema_m, ema_s, rsi, macd, signal, hist, volumes, vol_ma, sp)

    @staticmethod
    def _signal_trend(i, closes, ema_f, ema_m, ema_s, rsi, macd, signal, hist, volumes, vol_ma, sp):
        """趋势策略 — 严格筛选，只在高质量趋势信号出现时入场"""
        rsi_val = rsi[i]
        vol_ok = volumes[i] > vol_ma[i] * 1.0

        # ── 多头信号（需 EMA 多头排列 + 量能确认）──
        if ema_f[i] > ema_m[i]:
            rsi_ok = sp["rsi_long_lo"] < rsi_val < sp["rsi_long_hi"]
            # 1) EMA金叉 + 量能配合
            if ema_f[i-1] <= ema_m[i-1] and rsi_ok and vol_ok:
                return "long"
            # 2) 趋势延续：回踩中线反弹（需要EMA三线多头）
            if ema_f[i] > ema_s[i] and rsi_ok:
                if closes[i-1] <= ema_m[i-1] * 1.01 and closes[i] > ema_m[i]:
                    return "long"
            # 3) MACD柱由负转正 + 放量（趋势内动量恢复）
            if hist[i] > 0 and hist[i-1] <= 0 and rsi_ok and vol_ok:
                return "long"

        # ── 空头信号（对称但更严格，加密做空风险更大）──
        if ema_f[i] < ema_m[i]:
            rsi_ok = sp["rsi_short_lo"] < rsi_val < sp["rsi_short_hi"]
            # 1) EMA死叉 + 放量
            if ema_f[i-1] >= ema_m[i-1] and rsi_ok and vol_ok:
                return "short"
            # 2) 趋势延续：反弹中线受阻
            if ema_f[i] < ema_s[i] and rsi_ok:
                if closes[i-1] >= ema_m[i-1] * 0.99 and closes[i] < ema_m[i]:
                    return "short"
            # 3) MACD柱由正转负 + 放量
            if hist[i] < 0 and hist[i-1] >= 0 and rsi_ok and vol_ok:
                return "short"

        return None

    @staticmethod
    def _signal_mean_reversion(i, closes, rsi, bb_up, bb_mid, bb_lo, volumes, vol_ma, sp):
        """均值回归 — 仅在量能枯竭+极端超买超卖时触发（加密市场需极其谨慎）"""
        rsi_val = rsi[i]
        vol_quiet = volumes[i] < vol_ma[i] * sp.get("vol_quiet_mult", 0.8)

        # ── 做多：价格触下轨 + RSI超卖 + 量能枯竭（卖压耗尽）──
        if closes[i] <= bb_lo[i] and rsi_val < sp["rsi_os"] and vol_quiet:
            return "long"
        # 价格跌破下轨后收回 + RSI超卖（假突破回归）
        if i >= 2 and closes[i-1] < bb_lo[i-1] and closes[i] > bb_lo[i] and rsi_val < sp["rsi_os"] + 5:
            return "long"

        # ── 做空：价格触上轨 + RSI超买 + 量能枯竭（买压耗尽）──
        if closes[i] >= bb_up[i] and rsi_val > sp["rsi_ob"] and vol_quiet:
            return "short"
        if i >= 2 and closes[i-1] > bb_up[i-1] and closes[i] < bb_up[i] and rsi_val > sp["rsi_ob"] - 5:
            return "short"

        return None

    @staticmethod
    def _signal_range(i, closes, rsi, bb_up, bb_lo, atr, sp):
        """区间策略 — 仅在布林带极端位置 + RSI超买超卖时入场"""
        rng = bb_up[i] - bb_lo[i]
        edge = sp["bb_edge_pct"]
        if rng <= 0:
            return None
        rsi_val = rsi[i]
        pos_in_band = (closes[i] - bb_lo[i]) / rng

        # 做多：价格在下边缘 + RSI超卖区域（而非仅<88）
        if pos_in_band < edge and rsi_val < sp["rsi_os"] + 10:
            return "long"

        # 做空：价格在上边缘 + RSI超买区域
        if pos_in_band > (1 - edge) and rsi_val > sp["rsi_ob"] - 10:
            return "short"

        return None

    @staticmethod
    def _signal_breakout(i, closes, atr, volumes, vol_ma, ema_f, ema_m, sp):
        """突破策略 — 新高/新低 + 放量 + EMA趋势确认（全部AND）"""
        lookback = int(sp["breakout_lookback"])
        if i < lookback + 1:
            return None
        recent_high = max(closes[i-lookback:i])
        recent_low = min(closes[i-lookback:i])
        vol_ok = volumes[i] > vol_ma[i] * sp["vol_surge_mult"]
        ema_ok_long = ema_f[i] > ema_m[i]
        ema_ok_short = ema_f[i] < ema_m[i]

        # 经典突破：新高/新低 + 放量 + EMA方向一致（全部AND，减少假突破）
        if closes[i] > recent_high and vol_ok and ema_ok_long:
            return "long"
        if closes[i] < recent_low and vol_ok and ema_ok_short:
            return "short"

        # ATR突破：单根K线振幅超1.5倍ATR + 放量 + EMA确认
        if atr[i] > 0 and vol_ok:
            candle_range = abs(closes[i] - closes[i-1])
            if candle_range > atr[i] * 1.5:
                if closes[i] > closes[i-1] and ema_ok_long:
                    return "long"
                if closes[i] < closes[i-1] and ema_ok_short:
                    return "short"

        return None

    @staticmethod
    def _signal_swing(i, closes, ema_f, ema_m, ema_s, rsi, atr, macd, signal, sp):
        """波段策略 — 多种回调买入/反弹做空模式"""
        rsi_val = rsi[i]
        # ── 做多波段 ──
        if ema_m[i] > ema_s[i]:
            pullback = (closes[i] - ema_m[i]) / (ema_m[i] if ema_m[i] else 1)
            # 1) 经典回调到EMA中线
            if sp["swing_pullback_lo"] < pullback < sp["swing_pullback_hi"]:
                if rsi_val < (sp["rsi_long_lo"] + 25) or macd[i] > signal[i]:
                    return "long"
            # 2) 回调后出现反转阳线（放宽：pullback<2%即可）
            if pullback < 0.02 and closes[i] > closes[i-1] and rsi_val < 55:
                if closes[i-1] <= ema_m[i-1] * 1.01:
                    return "long"
            # 3) 价格触及EMA慢线附近（放宽深度回调区间）
            deep_pullback = (closes[i] - ema_s[i]) / (ema_s[i] if ema_s[i] else 1)
            if -0.03 < deep_pullback < 0.02 and rsi_val < 45 and closes[i] > closes[i-1]:
                return "long"
            # 4) 价格在EMA中线±4%内+阳线+RSI<50（宽区间增加信号）
            if -0.04 < pullback < 0.04 and rsi_val < 50 and macd[i] > signal[i] and closes[i] > closes[i-1]:
                return "long"

        # ── 做空波段 ──
        if ema_m[i] < ema_s[i]:
            pullback = (closes[i] - ema_m[i]) / (ema_m[i] if ema_m[i] else 1)
            if -sp["swing_pullback_hi"] < pullback < -sp["swing_pullback_lo"]:
                if rsi_val > (sp["rsi_short_hi"] - 25) or macd[i] < signal[i]:
                    return "short"
            if pullback > -0.02 and closes[i] < closes[i-1] and rsi_val > 45:
                if closes[i-1] >= ema_m[i-1] * 0.99:
                    return "short"
            deep_pullback = (closes[i] - ema_s[i]) / (ema_s[i] if ema_s[i] else 1)
            if -0.02 < deep_pullback < 0.03 and rsi_val > 55 and closes[i] < closes[i-1]:
                return "short"
            if -0.04 < pullback < 0.04 and rsi_val > 50 and macd[i] < signal[i] and closes[i] < closes[i-1]:
                return "short"

        return None

    @staticmethod
    def _signal_momentum(i, closes, ema_f, ema_m, ema_s, rsi, macd, hist, volumes, vol_ma, sp):
        """动量策略 — 要求放量确认动量延续（强者恒强）"""
        rsi_val = rsi[i]
        vol_mult = sp.get("momentum_vol_mult", 1.3)
        vol_ok = volumes[i] > vol_ma[i] * vol_mult

        # ── 多头动量：EMA多头 + 放量 + 动量指标确认 ──
        if ema_f[i] > ema_m[i] and vol_ok:
            # MACD柱加速增长 + 放量
            if hist[i] > 0 and hist[i] > hist[i-1] and rsi_val < sp["rsi_long_hi"]:
                return "long"
            # RSI上穿50 + MACD为正 + 放量
            if rsi[i-1] < 50 and rsi_val >= 50 and hist[i] > 0:
                return "long"

        # ── 空头动量：EMA空头 + 放量 + 动量指标确认 ──
        if ema_f[i] < ema_m[i] and vol_ok:
            if hist[i] < 0 and hist[i] < hist[i-1] and rsi_val > sp["rsi_short_lo"]:
                return "short"
            if rsi[i-1] > 50 and rsi_val <= 50 and hist[i] < 0:
                return "short"

        return None

    # ══════════════════════════════════════════════════
    #  仓位管理
    # ══════════════════════════════════════════════════

    def _close_position(self, pos, exit_price, bar, equity, trades, reason, leverage):
        fee = pos.quantity * exit_price * TAKER_FEE
        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity
        pnl -= fee
        pnl_pct = pnl / (pos.quantity * pos.entry_price / leverage) if pos.entry_price else 0
        trades.append(TradeRecord(
            side=pos.side, entry_price=pos.entry_price, exit_price=exit_price,
            quantity=pos.quantity, leverage=leverage,
            entry_bar=pos.entry_bar, exit_bar=bar.idx,
            entry_time=pos.entry_time, exit_time=bar.dt_str,
            pnl=pnl, pnl_pct=pnl_pct, fee=fee, exit_reason=reason,
        ))
        return equity + pnl

    @staticmethod
    def _unrealized_pnl(pos, price):
        if pos.side == "long":
            return (price - pos.entry_price) * pos.quantity
        return (pos.entry_price - price) * pos.quantity

    @staticmethod
    def _unrealized_pnl_pct(pos, price):
        if pos.entry_price == 0:
            return 0
        if pos.side == "long":
            return (price - pos.entry_price) / pos.entry_price
        return (pos.entry_price - price) / pos.entry_price

    # ══════════════════════════════════════════════════
    #  绩效计算
    # ══════════════════════════════════════════════════

    def _calculate_metrics(self, result, trades, equity_curve):
        if not trades:
            return
        result.total_trades = len(trades)
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        result.win_rate = len(wins) / len(trades) if trades else 0
        total_profit = sum(t.pnl for t in wins) if wins else 0
        total_loss = abs(sum(t.pnl for t in losses)) if losses else 0.001
        result.profit_factor = total_profit / total_loss if total_loss > 0 else 0
        result.total_return = (result.final_equity - self.initial_capital) / self.initial_capital
        result.avg_trade_return = float(np.mean([t.pnl_pct for t in trades])) if trades else 0

        bars_count = len(equity_curve)
        # 从实际K线时间戳推算持续时间，而非硬编码1h
        if hasattr(result, '_bars_ref') and result._bars_ref and len(result._bars_ref) >= 2:
            duration_secs = result._bars_ref[-1].timestamp - result._bars_ref[0].timestamp
            years = duration_secs / (365.25 * 24 * 3600) if duration_secs > 0 else 0
        elif bars_count > 1:
            years = bars_count / (365 * 24)
        else:
            years = 0
        if years > 0 and result.final_equity > 0:
            result.annualized_return = (result.final_equity / self.initial_capital) ** (1 / max(years, 0.01)) - 1

        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.where(peak > 0, peak, 1)
        result.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0

        # ── Sharpe 计算（双模式：逐笔交易 + 权益曲线备选）──
        sharpe_val = 0.0

        # 方法1：用逐笔交易收益（对低频策略更准确，业界标准）
        if trades and len(trades) >= 2:
            trade_returns = np.array([t.pnl_pct for t in trades], dtype=np.float64)
            trade_returns = trade_returns[np.isfinite(trade_returns)]
            if len(trade_returns) >= 2:
                tr_std = float(np.std(trade_returns, ddof=1))
                tr_mean = float(np.mean(trade_returns))
                if tr_std > 1e-10:
                    trades_per_year = len(trades) / max(years, 0.01) if years > 0 else len(trades)
                    sharpe_val = tr_mean / tr_std * math.sqrt(max(trades_per_year, 1))

        # 方法2（备选）：用权益曲线逐bar收益（高频策略时更好）
        if abs(sharpe_val) < 1e-10 and len(equity_curve) > 2:
            eq_safe = np.where(eq[:-1] != 0, eq[:-1], 1)
            returns = np.diff(eq) / eq_safe
            returns = returns[np.isfinite(returns)]
            if len(returns) > 1:
                ret_std = float(np.std(returns))
                ret_mean = float(np.mean(returns))
                if ret_std > 1e-12:
                    sharpe_val = ret_mean / ret_std * math.sqrt(365 * 24)

        # 防止 NaN/inf 泄漏
        if math.isfinite(sharpe_val):
            result.sharpe_ratio = round(sharpe_val, 4)
        else:
            result.sharpe_ratio = 0.0

        max_cw, max_cl, cw, cl = 0, 0, 0, 0
        for t in trades:
            if t.pnl > 0:
                cw += 1; cl = 0
            else:
                cl += 1; cw = 0
            max_cw = max(max_cw, cw)
            max_cl = max(max_cl, cl)
        result.max_consecutive_wins = max_cw
        result.max_consecutive_losses = max_cl
        holding = [t.exit_bar - t.entry_bar for t in trades if t.exit_bar > 0]
        result.avg_holding_bars = float(np.mean(holding)) if holding else 0

    # ══════════════════════════════════════════════════
    #  技术指标（向量化）
    # ══════════════════════════════════════════════════

    @staticmethod
    def _ema(data, period):
        result = np.zeros_like(data)
        result[0] = data[0]
        k = 2 / (period + 1)
        for i in range(1, len(data)):
            result[i] = data[i] * k + result[i-1] * (1 - k)
        return result

    @staticmethod
    def _sma(data, period):
        result = np.zeros_like(data)
        cumsum = np.cumsum(data)
        result[period-1:] = (cumsum[period-1:] - np.concatenate([[0], cumsum[:-period]])) / period
        result[:period-1] = data[:period-1]
        return result

    @staticmethod
    def _rsi(closes, period=14):
        delta = np.diff(closes, prepend=closes[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = np.zeros_like(closes)
        avg_loss = np.zeros_like(closes)
        avg_gain[period] = np.mean(gain[1:period+1])
        avg_loss[period] = np.mean(loss[1:period+1])
        for i in range(period+1, len(closes)):
            avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
        rsi_arr = 100 - 100 / (1 + rs)
        rsi_arr[:period] = 50
        return rsi_arr

    @staticmethod
    def _atr(highs, lows, closes, period=14):
        tr = np.maximum(highs - lows,
                        np.maximum(np.abs(highs - np.roll(closes, 1)),
                                   np.abs(lows - np.roll(closes, 1))))
        tr[0] = highs[0] - lows[0]
        atr = np.zeros_like(tr)
        atr[period-1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i-1] * (period-1) + tr[i]) / period
        return atr

    @staticmethod
    def _bollinger(closes, period=20, std_dev=2.0):
        mid = BacktestEngine._sma(closes, period)
        std = np.zeros_like(closes)
        for i in range(period-1, len(closes)):
            std[i] = np.std(closes[max(0, i-period+1):i+1])
        return mid + std_dev * std, mid, mid - std_dev * std

    @staticmethod
    def _macd(closes, fast=12, slow=26, sig=9):
        ema_f = BacktestEngine._ema(closes, fast)
        ema_s = BacktestEngine._ema(closes, slow)
        macd_line = ema_f - ema_s
        signal_line = BacktestEngine._ema(macd_line, sig)
        return macd_line, signal_line, macd_line - signal_line

    # ══════════════════════════════════════════════════════
    #  Phase 4: AI审查冠军策略 — 解读而非生成
    # ══════════════════════════════════════════════════════

    @staticmethod
    def ai_review_champion(strategy_config: Dict[str, Any], result: BacktestResult) -> str:
        """
        回测完成后，将冠军策略的参数+表现数据发送给LLM，
        LLM返回：为什么这个策略在当前市场有效？什么情况下会失效？
        结果供后续RAG检索和prompt注入使用。

        不改造遗传算法原因：遗传算法已经很高效且确定性；
        AI驱动的变异引入LLM调用延迟和随机性，成本收益不成比例。
        AI适合做"解读"而非"生成"。
        """
        try:
            strategy_id = strategy_config.get("strategy_id", "unknown")
            nature = strategy_config.get("trade_nature", "unknown")
            tier = strategy_config.get("tier", "unknown")
            signal_params = strategy_config.get("signal_params", {})
            risk_params = strategy_config.get("risk_params", {})

            # 构建分析prompt
            prompt_parts = [
                "你是量化策略分析师。以下是一个回测冠军策略的完整数据，请分析：",
                f"\n**策略ID**: {strategy_id}",
                f"**交易性质**: {nature} | **周期**: {tier}",
                f"\n**回测结果**:",
                f"- 总收益率: {result.total_return:.2%}",
                f"- 年化收益: {result.annualized_return:.2%}",
                f"- 最大回撤: {result.max_drawdown:.2%}",
                f"- 夏普比率: {result.sharpe_ratio:.2f}",
                f"- 胜率: {result.win_rate:.2%}",
                f"- 盈亏比: {result.profit_factor:.2f}",
                f"- 交易笔数: {result.total_trades}",
                f"- 连续最大亏损: {result.max_consecutive_losses}笔",
                f"\n**策略参数**:",
                f"- 信号参数: {json.dumps(signal_params, ensure_ascii=False)[:300]}",
                f"- 风控参数: {json.dumps(risk_params, ensure_ascii=False)[:200]}",
                "\n请用3-5句话回答：",
                "1. 这个策略的竞争优势是什么？（为什么它在回测中表现好）",
                "2. 什么市场条件下它会失效？",
                "3. 实盘中应该注意什么风险？",
                "只输出分析文本，不要JSON格式。",
            ]
            prompt = "\n".join(prompt_parts)

            # 调用LLM
            from backend.services.llm_config_service import (
                call_llm_api_sync,
                get_llm_config_for_analysis,
            )
            cfg = get_llm_config_for_analysis()
            if cfg is None:
                return ""

            resp = call_llm_api_sync(
                cfg,
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=400,
                timeout=60,
                caller="ai_review_champion",
            )
            if resp:
                content = (
                    resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if isinstance(resp, dict) else ""
                )
                insight = str(content).strip()[:500]
                logger.info(f"[BacktestEngine] AI审查冠军策略 {strategy_id}: {insight[:80]}...")
                return insight

        except Exception as exc:
            logger.debug(f"[BacktestEngine] AI审查失败(降级): {exc}")
        return ""
