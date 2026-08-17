"""Legacy 21 因子兼容层 (L4 因子并轨)

把旧 ``FactorEngine.FACTORS`` 的 21 个因子按原算法、原参数、原 factor_id
封装成 ``BaseFactor`` 子类，注册到新 ``FactorRegistry``。

为什么需要这一层：
  新系统虽有同名指标（如 RSI），但参数不同（新 rsi_7=7周期，旧 rsi=14周期）。
  下游 factor_weighting.py 等硬编码按短名（'rsi'/'adx'/'macd'）查因子，
  且依赖原参数语义。直接别名会导致数值语义变化。
  故把旧算法原样迁成插件，既统一了调度入口，又保持下游零改动。

注册后，新旧因子共存于同一注册表：
  - legacy 短名因子（rsi/macd/adx ... 共21个）← 本文件
  - 新规范名因子（rsi_7/a158_rsi6/... 共百余个）← factors/ 各目录
下游按短名查到的仍是 14 周期 RSI 等原算法，行为不变。

旧标量算法 (klines, market_data)->float 被适配为返回末值 Series 的 calculate。
market_data 通过 params['_market_data'] 传入（FactorCalculator 调用时注入）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ....factor_engine.factor_base import BaseFactor, FactorMetadata
from ....factor_engine.factor_registry import register_factor


# ── 辅助函数（移植自 base_factors._ema / _calculate_directional_indicator）──

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    if len(data) < period:
        period = len(data)
    alpha = 2 / (period + 1)
    ema = np.zeros(len(data))
    if period > 0 and len(data) >= period:
        ema[period - 1] = np.mean(data[:period])
        for i in range(period, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
    return ema


def _calc_di(high, low, close, period, is_plus) -> np.ndarray:
    n = len(high)
    if n < period + 1:
        return np.zeros(n)
    high_diff = np.diff(high)
    low_diff = np.diff(low)
    if is_plus:
        dm = np.where((high_diff > 0) & (high_diff > -low_diff), high_diff, 0.0)
    else:
        dm = np.where((low_diff > 0) & (low_diff > high_diff), low_diff, 0.0)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    smoothed_dm = np.zeros(n - 1)
    smoothed_tr = np.zeros(n - 1)
    if n - 1 >= period:
        smoothed_dm[period - 1] = np.sum(dm[:period])
        smoothed_tr[period - 1] = np.sum(tr[:period])
        for i in range(period, n - 1):
            smoothed_dm[i] = smoothed_dm[i - 1] - smoothed_dm[i - 1] / period + dm[i]
            smoothed_tr[i] = smoothed_tr[i - 1] - smoothed_tr[i - 1] / period + tr[i]
    di = np.zeros(n)
    valid = smoothed_tr[period - 1:] > 1e-8
    di[period:][valid] = smoothed_dm[period - 1:][valid] / smoothed_tr[period - 1:][valid] * 100
    return di


def _md(outcome_dict):
    """从 params 取 market_data（旧算法第二入参），无则 None。"""
    return outcome_dict


class _LegacyBase(BaseFactor):
    """旧标量因子基类：calculate 返回末值单点 Series。

    子类实现 ``_compute(self, df, market_data) -> float``。
    """

    # [2026-08-16] 历史评分路径：market_data dict 缺失时从 df 富化列回退取末值
    # （registry 因子回测经 midlong_registry_factors._enrich_flow_history 注入
    # 真实 oi/buy_notional/sell_notional/cvd 历史列）。
    _MD_DF_KEYS = ("oi", "prev_oi", "buy_notional", "sell_notional",
                   "cvd", "total_notional", "funding_rate", "oi_delta_pct")

    @classmethod
    def _md_from_df(cls, data: pd.DataFrame):
        if data is None or not len(data):
            return None
        md = {}
        for k in cls._MD_DF_KEYS:
            if k not in data.columns:
                continue
            try:
                last = data[k].iloc[-1]
            except Exception:  # noqa: BLE001
                continue
            if last is None or (isinstance(last, float) and np.isnan(last)):
                continue
            try:
                md[k] = float(last)
            except (TypeError, ValueError):
                continue
        if "oi" in md and "prev_oi" not in md:
            oi_series = data["oi"].dropna()
            if len(oi_series) >= 2:
                md["prev_oi"] = float(oi_series.iloc[-2])
        return md or None

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        market_data = (self.params or {}).get("_market_data")
        if not market_data:
            market_data = self._md_from_df(data)
        try:
            val = self._compute(data, market_data)
        except Exception:
            val = 0.0
        if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
            val = 0.0
        # 返回与 df 等长的 Series，末值为计算结果（便于 series_to_factor_values 取末值）
        s = pd.Series(np.nan, index=data.index)
        s.iloc[-1] = float(val)
        return s

    def _compute(self, df: pd.DataFrame, market_data) -> float:  # pragma: no cover
        raise NotImplementedError


# ══════════════════════════════════════════════════
#  动量因子
# ══════════════════════════════════════════════════

@register_factor(override=True)
class LegacyRSIFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="rsi", name="RSI", display_name="RSI(14)",
            description="Legacy 14周期 RSI（base_factors.compute_rsi 原样迁移）",
            category="technical", subcategory="momentum",
            lookback_period=14, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 14:
            return 50.0
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gain[-14:])
        avg_loss = np.mean(loss[-14:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(np.clip(100 - (100 / (1 + rs)), 0, 100))


@register_factor(override=True)
class LegacyMACDFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="macd", name="MACD", display_name="MACD(12,26,9)",
            description="Legacy MACD（base_factors.compute_macd 原样迁移）",
            category="technical", subcategory="momentum",
            lookback_period=26, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 26:
            return 0.0
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd_line = ema12[-1] - ema26[-1]
        signal = _ema(np.concatenate([[0], [macd_line]]), 9)[-1]
        return float(macd_line - signal)


@register_factor(override=True)
class LegacyMomentumFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="momentum", name="Momentum", display_name="Momentum(10)",
            description="Legacy 10日动量",
            category="technical", subcategory="momentum",
            lookback_period=10, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 10:
            return 0.0
        return float((close[-1] - close[-10]) / close[-10] * 100)


@register_factor(override=True)
class LegacyROCFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="roc", name="ROC", display_name="ROC(10)",
            description="Legacy 10日变化率",
            category="technical", subcategory="momentum",
            lookback_period=10, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 10:
            return 0.0
        return float((close[-1] - close[-10]) / close[-10] * 100)


@register_factor(override=True)
class LegacyADXFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="adx", name="ADX", display_name="ADX(14)",
            description="Legacy ADX（Wilder 平滑法，14周期）",
            category="technical", subcategory="trend",
            lookback_period=28, required_data_fields=["high", "low", "close"],
        )

    def _compute(self, df, md):
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)
        if n < 28:
            return 20.0
        plus_di = _calc_di(high, low, close, 14, True)
        minus_di = _calc_di(high, low, close, 14, False)
        denom = plus_di + minus_di
        valid = denom > 1e-8
        with np.errstate(invalid="ignore", divide="ignore"):
            dx = np.where(valid, np.abs(plus_di - minus_di) / np.where(valid, denom, 1.0) * 100, 0.0)
        adx_val = float(np.mean(dx[-14:]))
        return adx_val if not np.isnan(adx_val) else 20.0


# ══════════════════════════════════════════════════
#  均值回归因子
# ══════════════════════════════════════════════════

@register_factor(override=True)
class LegacyBBWidthFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="bb_width", name="BB_Width", display_name="BB Width(20,2)",
            description="Legacy 布林带宽度",
            category="technical", subcategory="mean_reversion",
            lookback_period=20, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 20:
            return 0.05
        sma20 = np.mean(close[-20:])
        std20 = np.std(close[-20:])
        if sma20 == 0:
            return 0.05
        return float(((sma20 + 2 * std20) - (sma20 - 2 * std20)) / sma20)


@register_factor(override=True)
class LegacyZScoreFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="zscore", name="Z-Score", display_name="Z-Score(20)",
            description="Legacy 价格 Z 分数",
            category="technical", subcategory="mean_reversion",
            lookback_period=20, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 20:
            return 0.0
        mean = np.mean(close[-20:])
        std = np.std(close[-20:])
        if std == 0:
            return 0.0
        return float((close[-1] - mean) / std)


@register_factor(override=True)
class LegacyATRRatioFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="atr_ratio", name="ATR Ratio", display_name="ATR/Price",
            description="Legacy ATR/价格比",
            category="technical", subcategory="volatility",
            lookback_period=14, required_data_fields=["high", "low", "close"],
        )

    def _compute(self, df, md):
        atr = _atr14(df)
        close = df["close"].values[-1]
        if close == 0:
            return 0.0
        return float(atr / close)


# ══════════════════════════════════════════════════
#  波动率因子
# ══════════════════════════════════════════════════

def _atr14(df) -> float:
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    if len(close) < 14:
        return 0.0
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    return float(np.mean(tr[-14:]))


@register_factor(override=True)
class LegacyATRFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="atr", name="ATR", display_name="ATR(14)",
            description="Legacy ATR",
            category="technical", subcategory="volatility",
            lookback_period=14, required_data_fields=["high", "low", "close"],
        )

    def _compute(self, df, md):
        return _atr14(df)


@register_factor(override=True)
class LegacyHVFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="hv", name="HV", display_name="HV(20)",
            description="Legacy 历史波动率（年化）",
            category="technical", subcategory="volatility",
            lookback_period=20, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 20:
            return 0.0
        returns = np.diff(np.log(close))
        vol = np.std(returns[-20:]) * np.sqrt(365 * 24)
        return float(vol * 100)


@register_factor(override=True)
class LegacyParkinsonVolFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="parkinson_vol", name="Parkinson Vol", display_name="Parkinson(10)",
            description="Legacy Parkinson 波动率",
            category="technical", subcategory="volatility",
            lookback_period=10, required_data_fields=["high", "low"],
        )

    def _compute(self, df, md):
        high = df["high"].values
        low = df["low"].values
        if len(high) < 10:
            return 0.0
        safe_low = np.where(low > 0, low, 1e-8)
        log_hl = np.log(high / safe_low)
        parkinson = np.sqrt((1 / (4 * np.log(2))) * np.mean(log_hl[-10:] ** 2))
        return float(parkinson * 100)


# ══════════════════════════════════════════════════
#  成交量因子
# ══════════════════════════════════════════════════

@register_factor(override=True)
class LegacyOBVFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="obv", name="OBV", display_name="OBV Ratio",
            description="Legacy OBV 变化率",
            category="technical", subcategory="volume",
            lookback_period=2, required_data_fields=["close", "volume"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        volume = df["volume"].values if "volume" in df.columns else np.zeros(len(close))
        if len(close) < 2:
            return 0.0
        obv = 0
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv += volume[i]
            elif close[i] < close[i - 1]:
                obv -= volume[i]
        return float(obv / (volume[-1] + 1e-8))


@register_factor(override=True)
class LegacyVWAPFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="vwap", name="VWAP", display_name="VWAP Dev",
            description="Legacy VWAP 偏离度",
            category="technical", subcategory="volume",
            lookback_period=1, required_data_fields=["high", "low", "close", "volume"],
        )

    def _compute(self, df, md):
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))
        typical_price = (high + low + close) / 3
        vol_sum = np.sum(volume)
        vwap = np.sum(typical_price * volume) / (vol_sum + 1e-8)
        if close[-1] == 0 or abs(vwap) < 1e-10:
            return 0.0
        return float((close[-1] - vwap) / vwap)


@register_factor(override=True)
class LegacyVolumeZScoreFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="volume_zscore", name="Volume Z-Score", display_name="Vol Z(20)",
            description="Legacy 成交量 Z 分数",
            category="technical", subcategory="volume",
            lookback_period=20, required_data_fields=["volume"],
        )

    def _compute(self, df, md):
        volume = df["volume"].values if "volume" in df.columns else np.zeros(len(df))
        if len(volume) < 20:
            return 0.0
        mean_vol = np.mean(volume[-20:])
        std_vol = np.std(volume[-20:])
        if std_vol == 0:
            return 0.0
        return float((volume[-1] - mean_vol) / std_vol)


@register_factor(override=True)
class LegacyCVDRatioFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="cvd_ratio", name="CVD Ratio", display_name="CVD/Notional",
            description="Legacy CVD/总名义",
            category="derivatives", subcategory="market_flow",
            lookback_period=1, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        if md and "cvd" in md:
            cvd = md["cvd"]
            total = md.get("total_notional", 1)
            if total:
                return float(cvd / total)
        return 0.0


# ══════════════════════════════════════════════════
#  趋势因子
# ══════════════════════════════════════════════════

@register_factor(override=True)
class LegacySMACrossFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="sma_cross", name="SMA Cross", display_name="SMA(20,50)",
            description="Legacy SMA 金叉/死叉信号",
            category="technical", subcategory="trend",
            lookback_period=50, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 50:
            return 0.0
        sma20 = np.mean(close[-20:])
        sma50 = np.mean(close[-50:])
        if sma50 == 0:
            return 0.0
        return float((sma20 - sma50) / sma50)


@register_factor(override=True)
class LegacyEMATrendFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="ema_trend", name="EMA Trend", display_name="EMA(9,21,50)",
            description="Legacy EMA 趋势对齐得分",
            category="technical", subcategory="trend",
            lookback_period=50, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        close = df["close"].values
        if len(close) < 50:
            return 0.0
        ema9 = _ema(close, 9)[-1]
        ema21 = _ema(close, 21)[-1]
        ema50 = _ema(close, 50)[-1]
        score = 0
        if close[-1] > ema9:
            score += 0.3
        if close[-1] > ema21:
            score += 0.3
        if close[-1] > ema50:
            score += 0.4
        return float(score)


@register_factor(override=True)
class LegacySupertrendFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="supertrend", name="SuperTrend", display_name="SuperTrend",
            description="Legacy SuperTrend 信号",
            category="technical", subcategory="trend",
            lookback_period=14, required_data_fields=["high", "low", "close"],
        )

    def _compute(self, df, md):
        # [2026-08-16 修复] 旧实现「当前 bar 中点 ± 3×ATR」判突破，ATR 含当前 bar
        # 区间，数学上恒不触发（BTC 4h 400 天 0 次 → 注册因子评分恒 F）。
        # 改用标准跟踪带算法（见 factor_engine/supertrend.py）。
        from ....factor_engine.supertrend import supertrend_direction

        d = supertrend_direction(df["high"].values, df["low"].values, df["close"].values)
        return float(d[-1])


# ══════════════════════════════════════════════════
#  市场流向因子
# ══════════════════════════════════════════════════

@register_factor(override=True)
class LegacyTakerRatioFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="taker_ratio", name="Taker Ratio", display_name="ln(Buy/Sell)",
            description="Legacy Taker 比率 ln(买/卖)",
            category="derivatives", subcategory="market_flow",
            lookback_period=1, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        if md and "buy_notional" in md and "sell_notional" in md:
            buy = md["buy_notional"]
            sell = md["sell_notional"]
            if sell > 0:
                return float(np.log(buy / sell))
        return 0.0


@register_factor(override=True)
class LegacyOIDeltaFactor(_LegacyBase):
    def get_metadata(self):
        return FactorMetadata(
            factor_id="oi_delta", name="OI Delta", display_name="OI Change %",
            description="Legacy OI 变化百分比",
            category="derivatives", subcategory="open_interest",
            lookback_period=1, required_data_fields=["close"],
        )

    def _compute(self, df, md):
        if md and "oi" in md and "prev_oi" in md:
            oi = md["oi"]
            prev_oi = md["prev_oi"]
            if prev_oi:
                return float((oi - prev_oi) / prev_oi * 100)
        return 0.0


# 注：funding_rate 不在此注册 —— sentiment/funding_factors.py 已有更专业的
# DataFrame 列读取版本（data['funding_rate']），factor_id 同名。
# 让 sentiment 版胜出，淘汰 legacy 的 market_data dict 旧路径。

