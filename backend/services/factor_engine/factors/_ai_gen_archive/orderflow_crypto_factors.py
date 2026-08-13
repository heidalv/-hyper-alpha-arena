"""
加密货币专属订单流因子（P1，规划文档 §2.2）— CVD 背离 / OFI / 资金费率动量。

数据现状说明（诚实标注，避免"看起来接了真实数据但实际没有"）：
    本项目 K 线管道（backend/services/data_center.py → crypto_klines 表）目前只落库
    open/high/low/close/volume，**没有**逐笔 taker 买卖量拆分列，也没有 L2 orderbook
    深度列。真正基于成交明细的 CVD（累计成交量差）和基于盘口深度的 OFI（订单流
    不平衡）需要新增数据采集（如 Binance K线接口本身就带的免费字段
    taker_buy_base_asset_volume）——这是一次数据管道改动，为避免在本轮改动中
    影响正在运行的实盘数据采集器，未在本次一并做，留给后续单独排期（见规划文档
    §2.4 数据管道缺口清单）。

    本文件采用行业通用的 Tick Rule 代理算法（与本文件同目录 behavioral/
    orderflow_factors.py 中已有的 AggressiveBuyFactor 同一思路：用 K 线内收盘价在
    high-low 区间的位置估算买卖压力占比），并做了显式的"真实数据优先，代理降级"
    钩子——一旦 K 线 DataFrame 里出现 taker_buy_volume 列（数据管道升级后），
    这里会自动切换到真实数据，无需再改代码。

    资金费率动量因子例外：funding_rate 是真实数据（unified_data_pool 从
    Hyperliquid PerpFunding 表按时间对齐注入，非估算），可放心直接使用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


def _proxy_signed_volume(data: pd.DataFrame) -> pd.Series:
    """Tick Rule 代理成交方向量：优先真实 taker_buy_volume，否则用 K 线内价格
    位置估算买卖占比（与 orderflow_factors.py 的 AggressiveBuy/Sell 同一思路）。
    """
    if "taker_buy_volume" in data.columns:
        buy = data["taker_buy_volume"].astype(float)
        sell = (data["volume"].astype(float) - buy).clip(lower=0)
        return buy - sell
    rng = (data["high"] - data["low"]).clip(lower=1e-10)
    buy_ratio = ((data["close"] - data["low"]) / rng).clip(0, 1)
    return data["volume"].astype(float) * (2 * buy_ratio - 1)


@register_factor()
class CVDDivergenceFactor(BaseFactor):
    """
    CVD（累计成交量差）背离因子。

    思路：价格创新高/新低，但 CVD 未同步创新高/新低 → 量价背离，往往是趋势
    衰竭的早期信号（尤其在加密市场，散户追涨杀跌导致的虚假突破常伴随 CVD 背离）。

    输出：price_zscore - cvd_zscore（同向滚动窗口内标准化后的差值）。
    正值越大 → 价格涨幅跑赢量能支撑（背离风险，警惕假突破）；
    负值越大 → 价格跌幅超过量能确认（可能存在超卖反弹机会）。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cvd_divergence",
            name="CVDDivergence",
            display_name="CVD背离",
            description="累计成交量差与价格的背离信号（加密市场趋势衰竭早期预警）",
            category="derivatives",
            subcategory="orderflow",
            lookback_period=30,
            required_data_fields=["close", "high", "low", "volume"],
            cache_ttl=300,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 30}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        window = self.params.get("window", 30)
        signed_vol = _proxy_signed_volume(data)
        cvd = signed_vol.cumsum()

        price = data["close"].astype(float)
        price_mean = price.rolling(window).mean()
        price_std = price.rolling(window).std()
        price_z = (price - price_mean) / (price_std + 1e-10)

        cvd_mean = cvd.rolling(window).mean()
        cvd_std = cvd.rolling(window).std()
        cvd_z = (cvd - cvd_mean) / (cvd_std + 1e-10)

        return (price_z - cvd_z).rename("cvd_divergence")


@register_factor()
class OrderFlowImbalanceFactor(BaseFactor):
    """
    OFI（订单流不平衡）因子 — 无 L2 深度时的代理版本。

    真实 OFI = Δ(bid_size) - Δ(ask_size)（逐笔盘口变化），此处用签名成交量的
    短期动量代理："买卖力量最近是否在加速失衡"，而不是单纯的净量。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="order_flow_imbalance",
            name="OrderFlowImbalance",
            display_name="订单流不平衡(OFI代理)",
            description="签名成交量短期加速度，无L2深度时代理订单流不平衡",
            category="derivatives",
            subcategory="orderflow",
            lookback_period=20,
            required_data_fields=["close", "high", "low", "volume"],
            cache_ttl=300,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"short_window": 5, "long_window": 20}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        short_w = self.params.get("short_window", 5)
        long_w = self.params.get("long_window", 20)
        signed_vol = _proxy_signed_volume(data)

        short_sum = signed_vol.rolling(short_w).sum()
        long_avg_abs = signed_vol.abs().rolling(long_w).mean() * short_w + 1e-10

        return (short_sum / long_avg_abs).rename("order_flow_imbalance")


@register_factor()
class L2DepthImbalanceFactor(BaseFactor):
    """
    L2 盘口深度失衡因子（真实订单簿数据，v6 阶段 2 第 7 项）。

    消费 `l2_reconstructor.KlineDepthAggregator` 附加的 depth_imbalance 列
    （前 5 档名义深度失衡 [−1,1]，>0 买盘占优），输出其滚动均值——深度失衡
    持续为正往往预示短期买压主导（与 OFI 代理因子互补：代理版看成交方向，
    本因子看盘口挂单方向）。

    L2 列未就绪时优雅降级为 0（与 derivatives_factors 降级口径一致）。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="l2_depth_imbalance",
            name="L2DepthImbalance",
            display_name="L2盘口深度失衡",
            description="真实订单簿前5档名义深度失衡的滚动均值（买压/卖压）",
            category="derivatives",
            subcategory="orderflow",
            lookback_period=12,
            required_data_fields=["close", "depth_imbalance"],
            cache_ttl=120,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 12}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "depth_imbalance" not in data.columns:
            return pd.Series(0.0, index=data.index, name="l2_depth_imbalance")
        window = self.params.get("window", 12)
        imb = data["depth_imbalance"].astype(float).fillna(0.0)
        return imb.rolling(window).mean().rename("l2_depth_imbalance")


@register_factor()
class FundingRateMomentumFactor(BaseFactor):
    """
    资金费率动量因子（真实数据：funding_rate 由 unified_data_pool 从交易所真实
    资金费率历史按时间对齐注入，非估算）。

    思路：资金费率本身的变化速度比费率绝对值更有信息量——费率从低速转向
    快速拉升，往往先于价格见顶（过度杠杆堆积）；反之快速回落常伴随挤空反弹。
    与已有的静态 funding_rate 因子（sentiment/funding_factors.py）互补，
    那个看的是"现在贵不贵"，这个看的是"变化快不快"。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="funding_rate_momentum",
            name="FundingRateMomentum",
            display_name="资金费率动量",
            description="资金费率变化速度（真实数据），过度杠杆堆积/挤空预警",
            category="derivatives",
            subcategory="funding",
            lookback_period=24,
            required_data_fields=["close", "funding_rate"],
            cache_ttl=1800,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {"window": 24}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if "funding_rate" not in data.columns:
            return pd.Series(0.0, index=data.index, name="funding_rate_momentum")

        window = self.params.get("window", 24)
        fr = data["funding_rate"].astype(float).fillna(0.0)
        fr_change = fr.diff()

        fr_change_mean = fr_change.rolling(window).mean()
        fr_change_std = fr_change.rolling(window).std()
        momentum_z = (fr_change - fr_change_mean) / (fr_change_std + 1e-10)

        return momentum_z.rename("funding_rate_momentum")
