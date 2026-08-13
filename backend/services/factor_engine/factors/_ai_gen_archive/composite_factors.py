"""
ATAS V2 - 中级复合因子

从基础因子组合而成的 10 个交叉信号因子。
所有因子优雅降级: 缺失输入列时返回 0 序列。

因子列表:
1. rsi_vol_ratio       — 动量强度 / 波动率
2. cvd_volume_residual — 真实买卖压力 (CVD - 成交量回归)
3. trend_persistence   — ADX * sign(EMA12-EMA26)
4. mean_reversion_score— -BB_position * (1-ADX/100)
5. liquidity_premium   — spread_z * amihud
6. smart_money_flow    — whale_tx * sign(OI_change)
7. fear_momentum       — fear_greed变化 * 波动率比
8. funding_oi_alignment— sign(funding) * sign(OI_change)
9. multi_tf_momentum   — 多周期RSI共振
10. regime_transition_score — 状态转换压力
"""

import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


def _safe_col(data: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    """Return column if exists, else constant Series."""
    if col in data.columns:
        return data[col].fillna(default).astype(float)
    return pd.Series(default, index=data.index)


def _zero(data: pd.DataFrame) -> pd.Series:
    return pd.Series(0.0, index=data.index)


# ────────────────────────────────────────────────
# 1. RSI / Volatility Ratio
# ────────────────────────────────────────────────

@register_factor()
class RSIVolRatioFactor(BaseFactor):
    """动量强度相对波动率: RSI14 / ATR14_pct"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="rsi_vol_ratio",
            name="RSIVolRatio",
            display_name="RSI/波动率比",
            description="RSI14 除以 ATR14 百分比，衡量动量效率",
            category="composite",
            subcategory="momentum_vol",
            lookback_period=20,
            required_data_fields=["close", "high", "low"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"rsi_period": 14, "atr_period": 14}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        rsi_p = self.params["rsi_period"]
        atr_p = self.params["atr_period"]
        close = data["close"].astype(float)

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(rsi_p).mean()
        loss = (-delta.clip(upper=0)).rolling(rsi_p).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)

        # ATR %
        high = data["high"].astype(float) if "high" in data.columns else close
        low = data["low"].astype(float) if "low" in data.columns else close
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_pct = (tr.rolling(atr_p).mean() / (close + 1e-10)) * 100

        return rsi / (atr_pct + 1e-10)


# ────────────────────────────────────────────────
# 2. CVD Volume Residual
# ────────────────────────────────────────────────

@register_factor()
class CVDVolumeResidualFactor(BaseFactor):
    """真实买卖压力: CVD 减去成交量线性回归预测"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cvd_volume_residual",
            name="CVDVolumeResidual",
            display_name="CVD成交量残差",
            description="CVD 减去 Volume 回归值，反映真实买卖压力",
            category="composite",
            subcategory="volume",
            lookback_period=24,
            required_data_fields=["close", "volume"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 24}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "cvd" not in data.columns:
            close = data["close"].astype(float)
            vol = _safe_col(data, "volume")
            price_dir = close.diff().apply(np.sign)
            cvd = (vol * price_dir).cumsum()
        else:
            cvd = _safe_col(data, "cvd")

        vol = _safe_col(data, "volume")
        window = self.params["window"]

        residual = _zero(data)
        for i in range(window, len(data)):
            v_slice = vol.iloc[i - window:i].values
            c_slice = cvd.iloc[i - window:i].values
            if np.std(v_slice) < 1e-10:
                continue
            slope = np.polyfit(v_slice, c_slice, 1)
            predicted = slope[0] * vol.iloc[i] + slope[1]
            residual.iloc[i] = cvd.iloc[i] - predicted

        return residual


# ────────────────────────────────────────────────
# 3. Trend Persistence
# ────────────────────────────────────────────────

@register_factor()
class TrendPersistenceFactor(BaseFactor):
    """趋势持续性: ADX * sign(EMA_fast - EMA_slow)"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="trend_persistence",
            name="TrendPersistence",
            display_name="趋势持续性",
            description="ADX 乘以 EMA 方向，衡量趋势强度与方向",
            category="composite",
            subcategory="trend",
            lookback_period=30,
            required_data_fields=["close", "high", "low"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"adx_period": 14, "ema_fast": 12, "ema_slow": 26}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        high = data["high"].astype(float) if "high" in data.columns else close
        low = data["low"].astype(float) if "low" in data.columns else close
        p = self.params["adx_period"]

        # Directional movement
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=data.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=data.index)

        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(p).mean()

        plus_di = 100 * plus_dm.rolling(p).mean() / (atr + 1e-10)
        minus_di = 100 * minus_dm.rolling(p).mean() / (atr + 1e-10)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(p).mean()

        ema_fast = close.ewm(span=self.params["ema_fast"]).mean()
        ema_slow = close.ewm(span=self.params["ema_slow"]).mean()
        direction = (ema_fast - ema_slow).apply(np.sign)

        return adx * direction


# ────────────────────────────────────────────────
# 4. Mean Reversion Score
# ────────────────────────────────────────────────

@register_factor()
class MeanReversionScoreFactor(BaseFactor):
    """均值回归倾向: -BB_position * (1 - ADX/100)"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="mean_reversion_score",
            name="MeanReversionScore",
            display_name="均值回归得分",
            description="布林带位置与低ADX结合, 衡量均值回归概率",
            category="composite",
            subcategory="mean_reversion",
            lookback_period=20,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"bb_period": 20, "bb_std": 2.0, "adx_period": 14}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        p = self.params["bb_period"]

        mid = close.rolling(p).mean()
        std = close.rolling(p).std()
        upper = mid + self.params["bb_std"] * std
        lower = mid - self.params["bb_std"] * std
        bb_pos = (close - lower) / (upper - lower + 1e-10)  # 0-1

        # Simple ADX proxy using close-only data
        high = data["high"].astype(float) if "high" in data.columns else close
        low = data["low"].astype(float) if "low" in data.columns else close
        ap = self.params["adx_period"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(ap).mean()
        price_range = close.rolling(ap).apply(lambda x: x.max() - x.min(), raw=True)
        adx_proxy = (price_range / (atr * ap + 1e-10)) * 100
        adx_proxy = adx_proxy.clip(0, 100)

        return -(bb_pos - 0.5) * 2.0 * (1 - adx_proxy / 100)


# ────────────────────────────────────────────────
# 5. Liquidity Premium
# ────────────────────────────────────────────────

@register_factor()
class LiquidityPremiumFactor(BaseFactor):
    """流动性溢价: spread_z * amihud illiquidity"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="liquidity_premium",
            name="LiquidityPremium",
            display_name="流动性溢价",
            description="价差z-score 乘以 Amihud 非流动性比率",
            category="composite",
            subcategory="liquidity",
            lookback_period=20,
            required_data_fields=["close", "volume"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 20}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        vol = _safe_col(data, "volume", 1.0)
        high = data["high"].astype(float) if "high" in data.columns else close
        low = data["low"].astype(float) if "low" in data.columns else close
        w = self.params["window"]

        # Spread proxy: (high-low)/close
        spread = (high - low) / (close + 1e-10)
        spread_mean = spread.rolling(w).mean()
        spread_std = spread.rolling(w).std()
        spread_z = (spread - spread_mean) / (spread_std + 1e-10)

        # Amihud illiquidity: |return| / volume
        ret = close.pct_change().abs()
        amihud = ret / (vol + 1e-10)
        amihud_norm = amihud / (amihud.rolling(w).mean() + 1e-10)

        return spread_z * amihud_norm


# ────────────────────────────────────────────────
# 6. Smart Money Flow
# ────────────────────────────────────────────────

@register_factor()
class SmartMoneyFlowFactor(BaseFactor):
    """聪明钱方向: whale_tx * sign(OI_change)"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="smart_money_flow",
            name="SmartMoneyFlow",
            display_name="聪明钱方向",
            description="鲸鱼交易量 乘以 OI变化方向",
            category="composite",
            subcategory="positioning",
            lookback_period=24,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 12}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        whale = _safe_col(data, "whale_tx_volume")
        oi = _safe_col(data, "oi")
        w = self.params["window"]

        if whale.sum() == 0 and oi.sum() == 0:
            return _zero(data)

        whale_z = (whale - whale.rolling(w).mean()) / (whale.rolling(w).std() + 1e-10)
        oi_change = oi.diff(3).apply(np.sign)

        return whale_z * oi_change


# ────────────────────────────────────────────────
# 7. Fear Momentum
# ────────────────────────────────────────────────

@register_factor()
class FearMomentumFactor(BaseFactor):
    """恐慌加速度: fear_greed 变化率 * 波动率比"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="fear_momentum",
            name="FearMomentum",
            display_name="恐慌加速度",
            description="恐惧贪婪指数变化率 乘以 短/长期波动率比",
            category="composite",
            subcategory="sentiment",
            lookback_period=24,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"fast_vol": 6, "slow_vol": 24}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        fg = _safe_col(data, "fear_greed", 50.0)
        close = data["close"].astype(float)
        fast = self.params["fast_vol"]
        slow = self.params["slow_vol"]

        fg_change = fg.diff(3)
        vol_fast = close.pct_change().abs().rolling(fast).mean()
        vol_slow = close.pct_change().abs().rolling(slow).mean()
        vol_ratio = vol_fast / (vol_slow + 1e-10)

        return fg_change * vol_ratio


# ────────────────────────────────────────────────
# 8. Funding-OI Alignment
# ────────────────────────────────────────────────

@register_factor()
class FundingOIAlignmentFactor(BaseFactor):
    """资金-持仓一致性: sign(funding) * sign(OI_change)"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="funding_oi_alignment",
            name="FundingOIAlignment",
            display_name="资金-持仓一致性",
            description="资金费率方向与OI变化方向的一致性",
            category="composite",
            subcategory="derivatives",
            lookback_period=24,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"oi_diff_period": 6}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        funding = _safe_col(data, "funding_rate")
        oi = _safe_col(data, "oi")

        if funding.abs().sum() == 0 and oi.sum() == 0:
            return _zero(data)

        p = self.params["oi_diff_period"]
        return funding.apply(np.sign) * oi.diff(p).apply(np.sign)


# ────────────────────────────────────────────────
# 9. Multi-Timeframe Momentum
# ────────────────────────────────────────────────

@register_factor()
class MultiTFMomentumFactor(BaseFactor):
    """多周期动量共振: avg(RSI_short, RSI_mid, RSI_long) 归一化"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="multi_tf_momentum",
            name="MultiTFMomentum",
            display_name="多周期动量共振",
            description="短/中/长三个RSI的平均值，衡量多周期动量共振",
            category="composite",
            subcategory="momentum",
            lookback_period=50,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"rsi_short": 7, "rsi_mid": 14, "rsi_long": 28}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)

        def _rsi(period):
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(period).mean()
            loss = (-delta.clip(upper=0)).rolling(period).mean()
            rs = gain / (loss + 1e-10)
            return 100 - 100 / (1 + rs)

        rsi_s = _rsi(self.params["rsi_short"])
        rsi_m = _rsi(self.params["rsi_mid"])
        rsi_l = _rsi(self.params["rsi_long"])

        avg_rsi = (rsi_s + rsi_m + rsi_l) / 3.0
        return (avg_rsi - 50.0) / 50.0  # normalize to -1..+1


# ────────────────────────────────────────────────
# 10. Regime Transition Score
# ────────────────────────────────────────────────

@register_factor()
class RegimeTransitionScoreFactor(BaseFactor):
    """状态转换压力: regime变化信号 * 波动率异常"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="regime_transition_score",
            name="RegimeTransitionScore",
            display_name="状态转换压力",
            description="市场状态变化信号 乘以 波动率异常度",
            category="composite",
            subcategory="regime",
            lookback_period=30,
            required_data_fields=["close"],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"vol_window": 20, "regime_window": 10}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        vw = self.params["vol_window"]
        rw = self.params["regime_window"]

        # Volatility anomaly: current vol vs rolling mean
        returns = close.pct_change()
        vol = returns.rolling(rw).std()
        vol_mean = vol.rolling(vw).mean()
        vol_std = vol.rolling(vw).std()
        vol_z = (vol - vol_mean) / (vol_std + 1e-10)

        # Regime change signal: rolling mean return flipping sign
        roll_ret = returns.rolling(rw).mean()
        sign_change = (roll_ret.apply(np.sign) != roll_ret.shift(1).apply(np.sign)).astype(float)
        # Smooth: count sign changes in recent window
        regime_change = sign_change.rolling(rw).sum()

        return regime_change * vol_z
