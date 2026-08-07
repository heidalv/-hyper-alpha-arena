"""
ATAS V2 - 衍生品因子

包含3个衍生品相关因子:
- FundingOIDivergenceFactor: 资金费率与OI的背离指标
- LongShortRatioFactor: 多空比率因子
- LiquidationHeatmapFactor: 清算压力因子

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入
funding_rate, oi, long_short_ratio 等列。
因子检查列是否存在，无数据时优雅降级。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class FundingOIDivergenceFactor(BaseFactor):
    """
    资金费率与OI变化的背离指标
    当OI上升但funding下降 → 多头积累（看多信号）
    当OI上升且funding上升 → 过度杠杆（反转警告）
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='funding_oi_divergence',
            name='FundingOIDivergence',
            display_name='资金费率-OI背离',
            description='资金费率与持仓量的背离指标',
            category='derivatives',
            subcategory='structure',
            lookback_period=24,
            required_data_fields=['close', 'funding_rate'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'funding_rate' not in data.columns or 'oi' not in data.columns:
            return pd.Series(0.0, index=data.index, name='funding_oi_divergence')

        window = self.params.get('window', 24)
        fr = data['funding_rate'].fillna(0.0)
        oi = data['oi'].fillna(0).astype(float)

        fr_mean = fr.rolling(window).mean()
        fr_std = fr.rolling(window).std()
        fr_z = (fr - fr_mean) / (fr_std + 1e-10)

        oi_mean = oi.rolling(window).mean()
        oi_std = oi.rolling(window).std()
        oi_z = (oi - oi_mean) / (oi_std + 1e-10)

        return oi_z - fr_z


@register_factor()
class LongShortRatioFactor(BaseFactor):
    """
    多空比因子
    使用衍生品快照中的 long_short_ratio 数据
    log(ratio) > 0 表示多头占优，< 0 表示空头占优
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='long_short_ratio',
            name='LongShortRatio',
            display_name='多空比',
            description='多空持仓比率（对数）',
            category='derivatives',
            subcategory='positioning',
            lookback_period=12,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'long_short_ratio' in data.columns:
            ratio = data['long_short_ratio'].fillna(1.0).astype(float)
            return np.log(ratio.clip(lower=1e-10))
        return pd.Series(0.0, index=data.index, name='long_short_ratio')


@register_factor()
class LiquidationHeatmapFactor(BaseFactor):
    """
    清算压力因子

    优先使用真实清算数据（liquidation_long / liquidation_short 列，由
    unified_data_pool 从 MarketAssetMetrics 注入）；无真实数据时回退到
    OI + 价格位移估算（标记 lower-confidence）。

    历史 BUG (G3)：原实现只用 K 线 high-low 伪造清算压力，完全没用真实清算
    数据 —— 这等于估错了币圈最关键的信号。现修正为真实数据优先。

    输出符号约定：
    - 正值 → 上方清算压力（空头清算密集，价格有向上磁吸）
    - 负值 → 下方清算压力（多头清算密集，价格有向下磁吸）
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='liquidation_pressure',
            name='LiquidationPressure',
            display_name='清算压力',
            description='基于真实清算数据的清算压力（无数据时OI估算降级）',
            category='derivatives',
            subcategory='risk',
            lookback_period=12,
            required_data_fields=['close', 'high', 'low'],
            cache_ttl=3600,
            aliases=['liquidation_heatmap'],
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # ── 优先：真实清算数据 ──
        # unified_data_pool 注入的列名：liquidation_long / liquidation_short
        has_real = (
            'liquidation_long' in data.columns
            and 'liquidation_short' in data.columns
        )
        if has_real:
            try:
                liq_long = data['liquidation_long'].fillna(0).astype(float)
                liq_short = data['liquidation_short'].fillna(0).astype(float)
                total = liq_long + liq_short
                # 磁吸压力 = (空头清算 - 多头清算) / total
                # 正值（空头清算多）→ 上方磁吸；负值（多头清算多）→ 下方磁吸
                # total=0 时中性
                magnet = np.where(total > 0, (liq_short - liq_long) / (total + 1e-10), 0.0)
                # 用成交量加权，让大额清算更显著
                if 'volume' in data.columns:
                    vol = data['volume'].fillna(1).astype(float)
                    vol_ma = vol.rolling(24, min_periods=1).mean()
                    vol_boost = (vol / (vol_ma + 1e-10)).clip(0.5, 3.0)
                    magnet = magnet * vol_boost
                return pd.Series(magnet, index=data.index, name='liquidation_pressure')
            except Exception:
                pass  # 降级到估算

        # ── 降级：OI + 价格位移估算（lower-confidence）──
        price_move = (data['high'] - data['low']) / (data['close'] + 1e-10)
        if 'oi' in data.columns:
            oi = data['oi'].fillna(0).astype(float)
            oi_change = oi.pct_change().abs()
            return price_move * oi_change * 100
        return price_move


@register_factor()
class LiquidationMagnetFactor(BaseFactor):
    """
    清算磁吸因子（币圈最强独有 alpha）

    基于真实清算数据的多/空清算不对称，预测价格级联移动方向：
    - 空头清算远超多头 → 价格倾向上涨（空头被强平推高）
    - 多头清算远超空头 → 价格倾向下跌（多头被强平砸低）

    与 LiquidationHeatmapFactor 的区别：本因子输出标准化方向信号
    (-1 ~ +1)，可直接用于因子加权管线；Heatmap 输出原始磁吸强度。

    数据源：liquidation_long / liquidation_short 列（真实清算）。
    无数据时返回 0（中性，不伪造）。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='liquidation_magnet',
            name='LiquidationMagnet',
            display_name='清算磁吸',
            description='基于真实清算数据的磁吸方向信号（币圈独有alpha）',
            category='derivatives',
            subcategory='positioning',
            lookback_period=6,
            required_data_fields=['close'],
            cache_ttl=1800,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 6}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        has_real = (
            'liquidation_long' in data.columns
            and 'liquidation_short' in data.columns
        )
        if not has_real:
            return pd.Series(0.0, index=data.index, name='liquidation_magnet')

        window = self.params.get('window', 6)
        try:
            liq_long = data['liquidation_long'].fillna(0).astype(float)
            liq_short = data['liquidation_short'].fillna(0).astype(float)

            # 窗口内累计清算
            cum_long = liq_long.rolling(window, min_periods=1).sum()
            cum_short = liq_short.rolling(window, min_periods=1).sum()
            total = cum_long + cum_short

            # 磁吸方向：(空头清算 - 多头清算) / total
            # +1 → 强上方磁吸（看多），-1 → 强下方磁吸（看空），0 → 中性
            magnet = np.where(
                total > 0,
                (cum_short - cum_long) / (total + 1e-10),
                0.0,
            )
            return pd.Series(magnet, index=data.index, name='liquidation_magnet')
        except Exception:
            return pd.Series(0.0, index=data.index, name='liquidation_magnet')


@register_factor()
class WickProtectionFactor(BaseFactor):
    """
    插针保护因子（币圈独有 alpha，v6 阶段 2 补齐）

    识别 K 线影线插针（wick sweep / liquidity grab）形态：
    - 长下影线 + 收盘收复（空头猎杀失败，流动性被扫后反弹）→ 看多保护信号
    - 长上影线 + 收盘回落（多头陷阱，冲高被砸回）→ 看空保护信号
    - 无明显插针 → 0（中性）

    输出窗口内平均方向信号 ∈ [-1, +1]，正值偏多、负值偏空。
    数据缺失（无 open/high/low/close）时返回中性 0 序列，不伪造。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='wick_protection',
            name='WickProtection',
            display_name='插针保护',
            description='K线影线插针形态的看多/看空保护信号（币圈独有alpha）',
            category='derivatives',
            subcategory='orderflow',
            lookback_period=12,
            required_data_fields=['open', 'high', 'low', 'close'],
            cache_ttl=1800,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 12, 'wick_ratio': 2.0}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        need = ['open', 'high', 'low', 'close']
        if not all(c in data.columns for c in need):
            return pd.Series(0.0, index=data.index, name='wick_protection')

        o = data['open'].astype(float)
        h = data['high'].astype(float)
        l = data['low'].astype(float)
        c = data['close'].astype(float)

        body = (c - o).abs()
        hi = pd.concat([o, c], axis=1).max(axis=1)
        lo = pd.concat([o, c], axis=1).min(axis=1)
        upper_wick = h - hi
        lower_wick = lo - l
        rng = h - l
        wick_ratio = self.params.get('wick_ratio', 2.0)

        # 单K信号：下影插针且收盘收复 → +1；上影插针且收盘回落 → -1；否则 0
        bullish = (lower_wick > body * wick_ratio) & (c >= o) & (rng > 0)
        bearish = (upper_wick > body * wick_ratio) & (c <= o) & (rng > 0)
        signal = pd.Series(
            np.where(bullish, 1.0, np.where(bearish, -1.0, 0.0)),
            index=data.index,
            name='wick_protection',
        )

        # 窗口内平均（min_periods=1 避免起始 NaN）
        window = self.params.get('window', 12)
        return signal.rolling(window, min_periods=1).mean().fillna(0.0)
