"""AI因子: 订单流不平衡因子 | 置信:55% | 利用OHLC数据模拟买卖压力差，通过计算日内上涨成交占比与价格变动协同性，捕捉做空/做多陷阱。在成交量放大但价格未能持续时，预示逆转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OrderFlowImbalance(BaseFactor):
    """利用OHLC数据模拟买卖压力差，通过计算日内上涨成交占比与价格变动协同性，捕捉做空/做多陷阱。在成交量放大但价格未能持续时，预示逆转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_order_imbalance",
            name="Order_Flow_Imbalance",
            display_name="订单流不平衡因子",
            description="利用OHLC数据模拟买卖压力差，通过计算日内上涨成交占比与价格变动协同性，捕捉做空/做多陷阱。在成交量放大但价格未能持续时，预示逆转。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 估计买卖压力：基于开盘到收盘变化与成交量
        delta_price = data['close'] - data['open']
        # 日内振幅
        amp = data['high'] - data['low']
        # 估计买方成交量比例：假设价格变化方向与成交量正相关
        buy_vol = data['volume'] * (delta_price.abs() / amp.replace(0, 1)).clip(0, 1)
        sell_vol = data['volume'] - buy_vol
        # 标准化不平衡
        net_vol = buy_vol - sell_vol
        # 用移动平均平滑
        net_vol_smooth = net_vol.rolling(5).mean()
        # 除以成交量均值标准化
        avg_vol = data['volume'].rolling(20).mean()
        imbalance = net_vol_smooth / avg_vol.replace(0, np.nan)
        # 结合价格趋势检验：当不平衡与价格方向相反时信号更强
        price_trend = data['close'].pct_change(5)
        contrarian = np.sign(imbalance) * (-1) * np.sign(price_trend)
        raw = imbalance * contrarian
        result = pd.Series(np.tanh(raw), index=data.index)
        return result.fillna(0)
