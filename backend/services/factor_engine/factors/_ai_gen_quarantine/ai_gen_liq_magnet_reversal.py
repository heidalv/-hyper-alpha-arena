"""AI因子: 流动性磁铁反转风险 | 置信:60% | 基于价格极端波动后成交量衰减与快速均值回归的因子，捕捉类似liq_magnet_reversal的亏损模式。计算过去N根K线的价格极值偏离(最高-最低)/收盘，结合成交量相对均值的萎缩程度，当价格偏离大且成交量萎缩时预示反转风险。输出[-1,1]，正值表示看涨反转概率高，负值看跌反转概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversalRisk(BaseFactor):
    """基于价格极端波动后成交量衰减与快速均值回归的因子，捕捉类似liq_magnet_reversal的亏损模式。计算过去N根K线的价格极值偏离(最高-最低)/收盘，结合成交量相对均值的萎缩程度，当价格偏离大且成交量萎缩时预示反转风险。输出[-1,1]，正值表示看涨反转概率高，负值看跌反转概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_magnet_reversal",
            name="Liquidity Magnet Reversal Risk",
            display_name="流动性磁铁反转风险",
            description="基于价格极端波动后成交量衰减与快速均值回归的因子，捕捉类似liq_magnet_reversal的亏损模式。计算过去N根K线的价格极值偏离(最高-最低)/收盘，结合成交量相对均值的萎缩程度，当价格偏离大且成交量萎缩时预示反转风险。输出[-1,1]，正值表示看涨反转概率高，负值看跌反转概率高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 确保数据包含必要列
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        # 计算过去10根K线的价格波动幅度（最高-最低）/收盘
        window = 10
        price_range = (high.rolling(window).max() - low.rolling(window).min()) / close
        # 计算成交量相对20日均值的比率
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 成交量萎缩信号：当vol_ratio < 0.7时认为萎缩
        vol_shrink = (vol_ratio < 0.7).astype(float)
    
        # 价格偏离极端：使用z-score标准化price_range
        range_mean = price_range.rolling(30).mean()
        range_std = price_range.rolling(30).std()
        range_z = (price_range - range_mean) / range_std
        # 当range_z > 1.5时认为价格极端
        extreme = (range_z > 1.5).astype(float)
    
        # 方向性：根据当前收盘相对前一根收盘判断短期方向
        direction = close.pct_change()
        # 结合极端和成交量萎缩得到反转风险信号
        # 正向：极端上涨后成交量萎缩 => 看跌反转 => 负值
        # 逆向：极端下跌后成交量萎缩 => 看涨反转 => 正值
        # 这里直接输出综合评分
        signal = -extreme * vol_shrink * np.sign(direction)
        # 平滑并限制到[-1,1]
        result = signal.clip(-1, 1)
        return result
