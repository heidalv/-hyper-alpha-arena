"""AI因子: 主力资金流向因子 | 置信:55% | 基于成交量与价格变动的方向一致性，识别主力资金（大单）的净流入/流出。使用日内价格区间内的成交量分布近似模拟资金流：当收盘价位于当天成交量的加权平均价（VWAP）之上且成交量放大时，认为主力净流入；反之为净流出。同时结合多周期背离。因子值为正表示主力看多，负表示看空，趋近0表示无明确方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Smart_Money_Flow_Index(BaseFactor):
    """基于成交量与价格变动的方向一致性，识别主力资金（大单）的净流入/流出。使用日内价格区间内的成交量分布近似模拟资金流：当收盘价位于当天成交量的加权平均价（VWAP）之上且成交量放大时，认为主力净流入；反之为净流出。同时结合多周期背离。因子值为正表示主力看多，负表示看空，趋近0表示无明确方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_flow",
            name="Smart Money Flow Index",
            display_name="主力资金流向因子",
            description="基于成交量与价格变动的方向一致性，识别主力资金（大单）的净流入/流出。使用日内价格区间内的成交量分布近似模拟资金流：当收盘价位于当天成交量的加权平均价（VWAP）之上且成交量放大时，认为主力净流入；反之为净流出。同时结合多周期背离。因子值为正表示主力看多，负表示看空，趋近0表示无明确方向。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 典型价格 (TP) 用于估算VWAP
        typical_price = (high + low + close) / 3
        # 滚动VWAP（20日）
        vwap_20 = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum()

        # 当日资金流方向：收盘价高于VWAP且成交量放大时为正
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20

        # 基础方向
        price_vs_vwap = (close - vwap_20) / (high.rolling(20).std() + 1e-10)  # 标准化
        flow_raw = np.sign(price_vs_vwap) * np.minimum(vol_ratio, 2.0)  # 量比最大2

        # 加入短期动量修正：如果价格连续上涨但量能萎缩，则主力可能出货
        close_roc = close.pct_change(5)
        vol_roc = volume.pct_change(5)
        divergence = np.where((close_roc > 0) & (vol_roc < -0.1), -0.5,
                    np.where((close_roc < 0) & (vol_roc > 0.1), 0.5, 0))
        # 合成
        factor = flow_raw + divergence
        # 平滑并限幅
        result = factor.rolling(3, min_periods=1).mean().clip(-1, 1)
        return result
