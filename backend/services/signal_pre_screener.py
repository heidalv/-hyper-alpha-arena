#!/usr/bin/env python3
"""
SignalPreScreener — 技术指标预筛选器（混合信号生成模式核心组件）

零 LLM 调用，纯数学计算。用已有的 technical_indicators.py 做 RSI/MACD/EMA/BOLL/ADX 计算，
在 LLM 分析前做确定性预筛选，通过的标的才进入 MasterController LLM 决策。

设计参考: Freqtrade populate_entry_trend(), Jesse should_long()/should_short()
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

import numpy as np

# 2026-07-06 整改（审查 3 #3）：tier→timeframe 映射此前在 trend_classifier.py /
# strategy_coordinator.py / multi_timeframe_orchestrator.py / 本文件里各自
# 硬编码一份，且互不相同（例如本文件原来 short primary=5m，coordinator 原来
# short primary=15m），导致"同一个 tier 名字在不同模块里代表不同的实际周期"。
# 统一改为从 backend/config/tier_timeframe_map.py 唯一权威定义读取。
from backend.config.tier_timeframe_map import TIER_TIMEFRAME_MAP

logger = logging.getLogger(__name__)


def _timeframe_to_minutes(tf: str) -> int:
    """把 "1m"/"15m"/"1h"/"4h"/"1d"/"1w" 这类周期字符串转换为分钟数，用于通用地
    比较两个周期谁"更大"、谁"更小"，避免在业务逻辑里写死具体周期名字符串。
    """
    if not tf:
        return 0
    tf = tf.strip().lower()
    try:
        if tf.endswith("w"):
            return int(tf[:-1]) * 7 * 24 * 60
        if tf.endswith("d"):
            return int(tf[:-1]) * 24 * 60
        if tf.endswith("h"):
            return int(tf[:-1]) * 60
        if tf.endswith("m"):
            return int(tf[:-1])
        return 0
    except ValueError:
        return 0


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class PreScreenResult:
    """单个标的的预筛选结果"""
    symbol: str
    passed: bool = False
    direction: str = "neutral"  # bullish / bearish / neutral
    signal_type: str = "none"   # oversold_bounce / overbought_reversal / macd_cross / bollinger / momentum / none
    strength: float = 0.0       # 0.0~1.0
    trigger_reason: str = ""
    indicators_snapshot: Dict[str, float] = field(default_factory=dict)


@dataclass
class BatchScreenResult:
    """批量筛选结果"""
    results: Dict[str, PreScreenResult] = field(default_factory=dict)
    passed_symbols: List[str] = field(default_factory=list)
    guaranteed_symbols: List[str] = field(default_factory=list)
    market_regime: str = "unknown"
    screen_time_ms: float = 0.0


# ── 预筛选器 ──────────────────────────────────────────────

class SignalPreScreener:
    """
    技术指标预筛选器

    用确定性技术指标（RSI/MACD/EMA/BOLL/ADX/成交量）做预筛选，
    通过的标的才进入 LLM 最终决策。零 LLM 调用，每标的 <10ms。
    """

    # 预筛选规则参数（按 tier 差异化）
    # 准确率优化：短线收紧阈值 + 增加 MACD 快参数 + 量能连续确认
    TIER_PARAMS = {
        "short": {
            "rsi_period": 7,
            "ema_period": 20,
            "boll_period": 20,
            "adx_period": 14,
            "vol_ma_period": 20,
            "rsi_oversold": 25,        # 收紧 30→25：7周期RSI波动大，30太松产生假信号
            "rsi_overbought": 75,      # 收紧 70→75
            "rsi_boll_oversold": 30,   # 收紧 35→30
            "rsi_boll_overbought": 70, # 收紧 65→70
            "vol_multiplier": 1.5,     # 收紧 1.3→1.5：避免单根异常放量误触发
            "vol_confirm_bars": 2,     # 新增：要求连续2根放量确认
            "adx_trend_threshold": 22, # 收紧 20→22：弱趋势不追
            "breakout_lookback": 20,
            "allow_mean_reversion": True,
            "macd_fast": 5,            # 新增：短线MACD快参数(原固定12/26/9太滞后)
            "macd_slow": 13,
            "macd_signal": 4,
        },
        "mid": {
            "rsi_period": 14,
            "ema_period": 50,
            "boll_period": 20,
            "adx_period": 14,
            "vol_ma_period": 20,
            "rsi_oversold": 25,
            "rsi_overbought": 75,
            "rsi_boll_oversold": 30,
            "rsi_boll_overbought": 70,
            "vol_multiplier": 1.2,
            "vol_confirm_bars": 1,     # mid 只需1根确认
            "adx_trend_threshold": 20,
            "breakout_lookback": 30,
            "allow_mean_reversion": False,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
        },
        "long": {
            "rsi_period": 14,
            "ema_period": 100,
            "boll_period": 20,
            "adx_period": 14,
            "vol_ma_period": 20,
            "rsi_oversold": 25,
            "rsi_overbought": 75,
            "rsi_boll_oversold": 30,
            "rsi_boll_overbought": 70,
            "vol_multiplier": 1.1,
            "vol_confirm_bars": 1,
            "adx_trend_threshold": 25,
            "breakout_lookback": 50,
            "allow_mean_reversion": False,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
        },
    }

    # 2026-07-06 整改（审查 3 #3）：tier → 主K线 timeframe / 辅助确认周期
    # 不再本文件自定义，统一从 TIER_TIMEFRAME_MAP 派生，确保和 trend_classifier /
    # strategy_coordinator 用的是同一套 tier 语义（short=15m / mid=1h / long=4h）。
    TIER_TIMEFRAME = {t: cfg["primary"] for t, cfg in TIER_TIMEFRAME_MAP.items()}
    CONFIRM_TIMEFRAMES = {t: list(cfg["confirm"]) for t, cfg in TIER_TIMEFRAME_MAP.items()}

    def screen_batch(
        self,
        symbols: List[str],
        market_summary: Dict[str, Dict],
        tier: str = "short",
    ) -> BatchScreenResult:
        """
        批量预筛选（多周期共振确认）

        Fix 4: 若 market_summary 里没有 kline_data，按 tier 自动从 DB 加载。
        主周期/确认周期均来自 TIER_TIMEFRAME_MAP（见 backend/config/tier_timeframe_map.py），
        例如 short tier：主周期 15m + 确认周期 5m/1m（三周期共振，非单一主周期孤立判断）。

        多周期共振逻辑（_apply_multi_tf_confirmation 按周期时长与主周期比较自动判断
        某个确认周期是"更大周期趋势背景"还是"更小周期动量确认"，不再写死具体周期名）：
        - 主周期出信号 → 进入候选
        - 比主周期时长更长的确认周期方向不能反对
        - 比主周期时长更短的确认周期确认微观动量方向
        - 共振通过 → 信号 strength 加成；共振失败 → 信号降级或否决
        """
        t0 = time.time()
        results: Dict[str, PreScreenResult] = {}
        passed_symbols: List[str] = []

        params = self.TIER_PARAMS.get(tier, self.TIER_PARAMS["short"])

        # 主周期 K线加载
        tf = self.TIER_TIMEFRAME.get(tier, "15m")
        confirm_tfs = self.CONFIRM_TIMEFRAMES.get(tier, [])

        # 2026-07-06 整改（P1 #14）：主周期 + 各确认周期 K线均改为"同周期多标的"
        # 批量加载（一次 IN 查询），把原来 N + N*M 次串行 DB round-trip 降到
        # 约 (1 + M) 次。批量加载器内部仍走缓存优先 + 数据过期时逐个 hyperliquid 兜底，
        # 数据质量不因批量化而下降。
        _symbols_needing_klines = [
            s for s in symbols
            if not (market_summary.get(s, {}).get("kline_data"))
        ]
        if _symbols_needing_klines:
            try:
                from backend.services.kline_data_service import kline_service
                _batch = kline_service.get_klines_batch_from_db(
                    _symbols_needing_klines, tf, count=60,
                )
                for sym in _symbols_needing_klines:
                    raw = _batch.get(sym.upper()) or _batch.get(sym)
                    if raw and len(raw) >= 30:
                        market_summary.setdefault(sym, {})["kline_data"] = raw
            except Exception as _kl_err:
                logger.debug(f"[SignalPreScreener] K线批量加载失败({tf}): {_kl_err}")

        # 辅助周期 K线预加载（多周期共振用）—— 按周期批量拉取
        if confirm_tfs:
            try:
                from backend.services.kline_data_service import kline_service
                for ctf in confirm_tfs:
                    cache_key = f"kline_data_{ctf}"
                    _need = [
                        s for s in symbols
                        if cache_key not in market_summary.get(s, {})
                    ]
                    if not _need:
                        continue
                    _cbatch = kline_service.get_klines_batch_from_db(_need, ctf, count=40)
                    for sym in _need:
                        raw_c = _cbatch.get(sym.upper()) or _cbatch.get(sym)
                        if raw_c and len(raw_c) >= 15:
                            market_summary.setdefault(sym, {})[cache_key] = raw_c
            except Exception:
                pass

        for symbol in symbols:
            sym_data = market_summary.get(symbol, {})
            result = self._screen_single(symbol, sym_data, params)

            # Fix 12: 多周期共振确认
            # 主周期(5m)出信号后，检查辅助周期方向是否支持
            if result.passed and confirm_tfs:
                result = self._apply_multi_tf_confirmation(
                    result, sym_data, confirm_tfs, params, tier,
                )

            results[symbol] = result
            if result.passed:
                passed_symbols.append(symbol)

        elapsed_ms = (time.time() - t0) * 1000

        ret = BatchScreenResult(
            results=results,
            passed_symbols=passed_symbols,
            guaranteed_symbols=[],
            market_regime="unknown",
            screen_time_ms=elapsed_ms,
        )

        if passed_symbols:
            logger.info(
                f"[SignalPreScreener] {tier} tier: {len(passed_symbols)}/{len(symbols)} passed → "
                f"{passed_symbols} ({elapsed_ms:.0f}ms)"
            )
        else:
            logger.debug(
                f"[SignalPreScreener] {tier} tier: 0/{len(symbols)} passed ({elapsed_ms:.0f}ms)"
            )

        return ret

    def _apply_multi_tf_confirmation(
        self,
        result: PreScreenResult,
        sym_data: Dict[str, Any],
        confirm_tfs: List[str],
        params: Dict,
        tier: str,
    ) -> PreScreenResult:
        """多周期共振确认：主信号出信号后，检查辅助周期方向。

        规则：
        - 比主周期时长更长的确认周期（趋势背景）方向与信号一致 → strength +0.15 加成
        - 方向相反 → strength × 0.5 降级（不否决，让 LLM 最终决定）
        - 比主周期时长更短的确认周期（动量确认）方向一致 → strength +0.1
        - 方向相反（加速反向）→ strength × 0.7
        - 辅助周期数据缺失 → 不影响（只做能做的确认）

        2026-07-06 整改（审查 3 #3）：此前用硬编码字符串比较判断某个 ctf 是"大周期"
        还是"小周期"（例如写死 `ctf == "15m" and tier == "short"`），这与
        TIER_TIMEFRAME_MAP 的确认周期集合强耦合——一旦该映射调整（例如本次统一
        映射后 short 的确认周期从 [15m,1m] 变成 [5m,1m]），硬编码比较就会失效
        （新的 5m 既不满足旧的"大周期"条件也不满足"小周期"条件，直接被静默忽略）。
        改为按周期真实时长与主周期比较，通用地判定"更大/更小"，不依赖具体周期名。

        注：不硬否决信号（除非极特殊情况），让 LLM 有最终决策权。
        """
        signal_dir = result.direction  # bullish / bearish
        if signal_dir == "neutral":
            return result

        primary_tf = TIER_TIMEFRAME_MAP.get(tier, {}).get("primary", "")
        primary_minutes = _timeframe_to_minutes(primary_tf)

        confirm_notes = []

        for ctf in confirm_tfs:
            kline_key = f"kline_data_{ctf}"
            raw_klines = sym_data.get(kline_key)
            if not raw_klines or len(raw_klines) < 10:
                continue

            try:
                import pandas as _pd
                kdf = _pd.DataFrame(raw_klines)
                for col in ('close', 'volume'):
                    if col in kdf.columns:
                        kdf[col] = _pd.to_numeric(kdf[col], errors='coerce')

                close = kdf["close"].values
                n = len(close)

                # EMA 趋势方向（9/21）
                if n >= 21:
                    import numpy as _np
                    def _ema_arr(arr, p):
                        alpha = 2 / (p + 1)
                        out = _np.zeros(len(arr))
                        out[0] = arr[0]
                        for i in range(1, len(arr)):
                            out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
                        return out
                    ema9 = _ema_arr(close, 9)[-1]
                    ema21 = _ema_arr(close, 21)[-1]
                    ctf_minutes = _timeframe_to_minutes(ctf)
                    is_higher_tf = primary_minutes > 0 and ctf_minutes > primary_minutes
                    is_lower_tf = primary_minutes > 0 and 0 < ctf_minutes < primary_minutes

                    if is_higher_tf:
                        # 大周期趋势背景：方向一致→加成，相反→降级
                        tf_bullish = ema9 > ema21 and close[-1] > ema21
                        tf_bearish = ema9 < ema21 and close[-1] < ema21
                        if signal_dir == "bullish" and tf_bullish:
                            result.strength = min(1.0, result.strength + 0.15)
                            confirm_notes.append(f"{ctf}趋势顺多(↑)")
                        elif signal_dir == "bearish" and tf_bearish:
                            result.strength = min(1.0, result.strength + 0.15)
                            confirm_notes.append(f"{ctf}趋势顺空(↓)")
                        elif (signal_dir == "bullish" and tf_bearish) or (signal_dir == "bearish" and tf_bullish):
                            result.strength *= 0.5
                            confirm_notes.append(f"{ctf}趋势逆信号(⚠️降级)")

                    if is_lower_tf:
                        # 小周期动量确认：最近3根close方向
                        if n >= 4:
                            recent_mom = close[-1] - close[-4]
                            if signal_dir == "bullish" and recent_mom > 0:
                                result.strength = min(1.0, result.strength + 0.1)
                                confirm_notes.append(f"{ctf}动量确认多(+)")
                            elif signal_dir == "bearish" and recent_mom < 0:
                                result.strength = min(1.0, result.strength + 0.1)
                                confirm_notes.append(f"{ctf}动量确认空(-)")
                            elif (signal_dir == "bullish" and recent_mom < 0) or (signal_dir == "bearish" and recent_mom > 0):
                                result.strength *= 0.7
                                confirm_notes.append(f"{ctf}动量反向(⚠️)")
            except Exception:
                continue

        if confirm_notes:
            result.trigger_reason += " | 共振: " + ", ".join(confirm_notes)

        return result

    def _screen_single(
        self,
        symbol: str,
        sym_data: Dict[str, Any],
        params: Dict,
    ) -> PreScreenResult:
        """
        单标的预筛选逻辑

        检测以下信号：
        1. 超卖反弹 / 超买反转 (RSI + 成交量)
        2. MACD 金叉/死叉 (MACD histogram + EMA)
        3. 布林带支撑/压力 (BOLL + RSI)
        4. 动量突破 (新高/新低 + ADX)
        """
        result = PreScreenResult(symbol=symbol)

        kline_data = sym_data.get("kline_data")
        if not kline_data or len(kline_data) < 30:
            return result

        # ── 计算指标 ──
        indicators = self._calc_indicators(kline_data, params)
        if not indicators:
            return result

        result.indicators_snapshot = indicators

        rsi = indicators.get("rsi", 50.0)
        rsi_prev = indicators.get("rsi_prev", 50.0)
        price = indicators.get("close", 0.0)
        vol_ratio = indicators.get("vol_ratio", 1.0)
        vol_confirmed = indicators.get("vol_confirmed", False)
        macd_hist = indicators.get("macd_histogram", 0.0)
        macd_hist_prev = indicators.get("macd_histogram_prev", 0.0)
        ema = indicators.get("ema", 0.0)
        boll_lower = indicators.get("boll_lower", 0.0)
        boll_upper = indicators.get("boll_upper", 0.0)
        adx = indicators.get("adx", 0.0)
        high_max = indicators.get("high_lookback", 0.0)

        # ── 规则 1: 超卖反弹 / 超买反转 ──
        # 准确率优化：要求量能连续确认(vol_confirmed) + RSI 连续两根确认
        if params["allow_mean_reversion"]:
            if (rsi < params["rsi_oversold"] and rsi_prev < params["rsi_oversold"] + 5
                    and vol_confirmed):
                result.passed = True
                result.direction = "bullish"
                result.signal_type = "oversold_bounce"
                result.strength = min(1.0, (params["rsi_oversold"] - rsi) / params["rsi_oversold"] + 0.3)
                result.trigger_reason = f"RSI连续偏低({rsi_prev:.0f}→{rsi:.0f}<{params['rsi_oversold']}) + 量能连续确认(vol_ratio={vol_ratio:.1f})"
                return result

            if (rsi > params["rsi_overbought"] and rsi_prev > params["rsi_overbought"] - 5
                    and vol_confirmed):
                result.passed = True
                result.direction = "bearish"
                result.signal_type = "overbought_reversal"
                result.strength = min(1.0, (rsi - params["rsi_overbought"]) / (100 - params["rsi_overbought"]) + 0.3)
                result.trigger_reason = f"RSI连续偏高({rsi_prev:.0f}→{rsi:.0f}>{params['rsi_overbought']}) + 量能连续确认(vol_ratio={vol_ratio:.1f})"
                return result

        # ── 规则 2: MACD 金叉/死叉 ──
        if macd_hist_prev <= 0 and macd_hist > 0 and price > ema:
            result.passed = True
            result.direction = "bullish"
            result.signal_type = "macd_cross"
            result.strength = min(1.0, abs(macd_hist) / max(price * 0.001, 1) + 0.4)
            result.trigger_reason = f"MACD histogram turned positive ({macd_hist:.4f}) + price>{'EMA'}{params['ema_period']}"
            return result

        if macd_hist_prev >= 0 and macd_hist < 0 and price < ema:
            result.passed = True
            result.direction = "bearish"
            result.signal_type = "macd_cross"
            result.strength = min(1.0, abs(macd_hist) / max(price * 0.001, 1) + 0.4)
            result.trigger_reason = f"MACD histogram turned negative ({macd_hist:.4f}) + price<EMA{params['ema_period']}"
            return result

        # ── 规则 3: 布林带支撑/压力 ──
        if boll_lower > 0 and price <= boll_lower and rsi < params["rsi_boll_oversold"]:
            result.passed = True
            result.direction = "bullish"
            result.signal_type = "bollinger"
            result.strength = min(1.0, (params["rsi_boll_oversold"] - rsi) / params["rsi_boll_oversold"] + 0.3)
            result.trigger_reason = f"Price={price:.2f}<=BOLL_lower={boll_lower:.2f} + RSI={rsi:.1f}<{params['rsi_boll_oversold']}"
            return result

        if boll_upper > 0 and price >= boll_upper and rsi > params["rsi_boll_overbought"]:
            result.passed = True
            result.direction = "bearish"
            result.signal_type = "bollinger"
            result.strength = min(1.0, (rsi - params["rsi_boll_overbought"]) / (100 - params["rsi_boll_overbought"]) + 0.3)
            result.trigger_reason = f"Price={price:.2f}>=BOLL_upper={boll_upper:.2f} + RSI={rsi:.1f}>{params['rsi_boll_overbought']}"
            return result

        # ── 规则 4: 动量突破 ──
        if high_max > 0 and price > high_max and adx > params["adx_trend_threshold"]:
            result.passed = True
            result.direction = "bullish"
            result.signal_type = "momentum"
            result.strength = min(1.0, (price - high_max) / max(high_max * 0.01, 1) + 0.4)
            result.trigger_reason = f"Price={price:.2f}>{high_max:.2f}({params['breakout_lookback']}-bar high) + ADX={adx:.1f}>{params['adx_trend_threshold']}"
            return result

        return result

    def _calc_indicators(
        self,
        kline_data: Any,
        params: Dict,
    ) -> Dict[str, float]:
        """
        从 K 线数据计算预筛选所需的指标值（取最新值）

        Returns:
            {rsi, close, vol_ratio, macd_histogram, macd_histogram_prev,
             ema, boll_lower, boll_upper, adx, high_lookback}
        """
        try:
            import pandas as pd

            if isinstance(kline_data, list):
                df = pd.DataFrame(kline_data)
            elif isinstance(kline_data, pd.DataFrame):
                df = kline_data.copy()
            else:
                return {}

            if df.empty or len(df) < 30:
                return {}

            for col in ('open', 'high', 'low', 'close', 'volume'):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            df = df.sort_values('timestamp').reset_index(drop=True)
            n = len(df)

            result: Dict[str, float] = {}

            # 收盘价
            result["close"] = float(df["close"].iloc[-1])

            # RSI (Wilder 平滑法 — 标准做法，比简单平均稳定，减少假信号)
            rsi_period = params["rsi_period"]
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            # Wilder 平滑：首值用 SMA，后续用 EMA(alpha=1/period)
            avg_gain = gain.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
            avg_loss = loss.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-10)
            rsi_series = 100 - (100 / (1 + rs))
            rsi_val = rsi_series.iloc[-1]
            result["rsi"] = float(rsi_val) if rsi_val == rsi_val else 50.0  # NaN check
            # 保留前一根 RSI 用于连续确认
            rsi_prev = rsi_series.iloc[-2] if n >= 2 else rsi_val
            result["rsi_prev"] = float(rsi_prev) if rsi_prev == rsi_prev else 50.0

            # 成交量比率 + 连续确认（准确率核心：单根放量不可靠）
            vol_ma_period = params["vol_ma_period"]
            vol_confirm_bars = params.get("vol_confirm_bars", 1)
            if n >= vol_ma_period and df["volume"].iloc[-vol_ma_period:].mean() > 0:
                vol_ma = df["volume"].iloc[-vol_ma_period:].mean()
                result["vol_ratio"] = float(df["volume"].iloc[-1] / vol_ma)
                # 连续确认：检查最后 N 根是否都放量（防止单根异常）
                recent_vols = df["volume"].iloc[-vol_confirm_bars:].values
                result["vol_confirmed"] = bool(
                    len(recent_vols) >= vol_confirm_bars
                    and all(v / vol_ma >= params["vol_multiplier"] * 0.8 for v in recent_vols)
                )
            else:
                result["vol_ratio"] = 1.0
                result["vol_confirmed"] = False

            # MACD (短线用快参数 5/13/4，中长线用标准 12/26/9)
            macd_fast = params.get("macd_fast", 12)
            macd_slow = params.get("macd_slow", 26)
            macd_sig = params.get("macd_signal", 9)
            if n >= macd_slow:
                ema_fast = df["close"].ewm(span=macd_fast, adjust=False).mean()
                ema_slow = df["close"].ewm(span=macd_slow, adjust=False).mean()
                macd_line = ema_fast - ema_slow
                signal_line = macd_line.ewm(span=macd_sig, adjust=False).mean()
                histogram = macd_line - signal_line
                result["macd_histogram"] = float(histogram.iloc[-1])
                result["macd_histogram_prev"] = float(histogram.iloc[-2]) if n >= 2 else 0.0
            else:
                result["macd_histogram"] = 0.0
                result["macd_histogram_prev"] = 0.0

            # EMA
            ema_period = params["ema_period"]
            if n >= ema_period:
                ema_series = df["close"].ewm(span=ema_period, adjust=False).mean()
                result["ema"] = float(ema_series.iloc[-1])
            else:
                result["ema"] = float(df["close"].iloc[-1])

            # 布林带 (20, 2)
            boll_period = params["boll_period"]
            if n >= boll_period:
                sma = df["close"].rolling(window=boll_period).mean()
                std = df["close"].rolling(window=boll_period).std()
                result["boll_upper"] = float(sma.iloc[-1] + 2 * std.iloc[-1]) if not std.iloc[-1] != std.iloc[-1] else 0.0
                result["boll_lower"] = float(sma.iloc[-1] - 2 * std.iloc[-1]) if not std.iloc[-1] != std.iloc[-1] else 0.0
            else:
                result["boll_upper"] = 0.0
                result["boll_lower"] = 0.0

            # ADX (简化版)
            adx_period = params["adx_period"]
            if n >= adx_period * 2:
                high = df["high"]
                low = df["low"]
                close = df["close"]
                tr1 = high - low
                tr2 = (high - close.shift(1)).abs()
                tr3 = (low - close.shift(1)).abs()
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr = tr.rolling(window=adx_period).mean()
                plus_dm = (high - high.shift(1)).where((high - high.shift(1)) > (low.shift(1) - low), 0.0)
                minus_dm = (low.shift(1) - low).where((low.shift(1) - low) > (high - high.shift(1)), 0.0)
                plus_di = 100 * (plus_dm.rolling(window=adx_period).mean() / atr.replace(0, 1e-10))
                minus_di = 100 * (minus_dm.rolling(window=adx_period).mean() / atr.replace(0, 1e-10))
                dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10))
                adx_series = dx.rolling(window=adx_period).mean()
                result["adx"] = float(adx_series.iloc[-1]) if not adx_series.iloc[-1] != adx_series.iloc[-1] else 0.0
            else:
                result["adx"] = 0.0

            # N 周期最高价
            lookback = params["breakout_lookback"]
            if n >= lookback:
                result["high_lookback"] = float(df["high"].iloc[-lookback:-1].max())
            else:
                result["high_lookback"] = float(df["high"].iloc[:-1].max()) if n > 1 else 0.0

            return result

        except Exception as e:
            logger.debug(f"[SignalPreScreener] 指标计算失败 {symbol if 'symbol' in dir() else '?'}: {e}")
            return {}

    def format_prescreen_prompt_section(self, batch_result: BatchScreenResult, tier: str) -> str:
        """
        生成注入 LLM prompt 的预筛选结果段落

        Returns:
            格式化的文本段落，空字符串表示无通过标的
        """
        if not batch_result.passed_symbols and not batch_result.guaranteed_symbols:
            return ""

        lines = [
            f"## 预筛选结果 (SignalPreScreener · {tier} tier)",
            "以下标的通过了技术指标预筛选，具有明确的技术面信号支撑：",
            "",
        ]

        all_symbols = set(batch_result.passed_symbols + batch_result.guaranteed_symbols)
        for symbol in sorted(all_symbols):
            r = batch_result.results.get(symbol)
            if not r:
                continue
            guaranteed_tag = " [频率保障]" if symbol in batch_result.guaranteed_symbols else ""
            lines.append(
                f"- **{symbol}**{guaranteed_tag}: {r.direction} | {r.signal_type} | "
                f"强度={r.strength:.0%} | {r.trigger_reason}"
            )

        lines.append("")
        # 2026-07-06 整改（审查 3 #22）：原文案"否则应优先考虑开仓"会被 LLM
        # 理解为"预筛选通过=默认应该开仓，找不到反对理由就开"，这与预筛选器
        # 自身"零 LLM 调用，纯数学规则粗筛，不做最终交易判断"的定位矛盾——
        # 真正的开平仓决策权重应该来自 LLM 对当前市场/仓位/风险的完整分析，
        # 预筛选通过只代表"值得让 LLM 深入看一眼"，不代表"结论已经是开仓"。
        lines.append(
            "**重要**: 预筛选通过的标的已具有技术面支撑，值得优先深入分析，"
            "但这不代表默认结论就是开仓——请基于完整的市场环境、仓位与风险状况独立判断，"
            "技术面支撑只是入场理由之一，不是开仓的默认前提。"
        )

        return "\n".join(lines)


# ── 单例 ──────────────────────────────────────────────────

_instance: Optional[SignalPreScreener] = None


def get_signal_pre_screener() -> SignalPreScreener:
    global _instance
    if _instance is None:
        _instance = SignalPreScreener()
    return _instance
