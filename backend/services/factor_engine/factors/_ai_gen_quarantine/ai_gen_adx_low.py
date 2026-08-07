"""AI因子: 低ADX状态检测 | 置信:70% | 计算14周期ADX，若ADX低于25（典型无趋势阈值）则输出负信号，指示当前市场处于震荡或趋势不明朗状态，容易导致持仓超时或止损。使用平滑后的ADX并映射到[-1,1]区间，ADX越低信号越负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_ADX_Regime_Detector(BaseFactor):
    """计算14周期ADX，若ADX低于25（典型无趋势阈值）则输出负信号，指示当前市场处于震荡或趋势不明朗状态，容易导致持仓超时或止损。使用平滑后的ADX并映射到[-1,1]区间，ADX越低信号越负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_low",
            name="Low ADX Regime Detector",
            display_name="低ADX状态检测",
            description="计算14周期ADX，若ADX低于25（典型无趋势阈值）则输出负信号，指示当前市场处于震荡或趋势不明朗状态，容易导致持仓超时或止损。使用平滑后的ADX并映射到[-1,1]区间，ADX越低信号越负。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 计算TR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 计算+DM, -DM
        up = high - high.shift()
        down = low.shift() - low
        pos_dm = pd.Series(0, index=data.index)
        neg_dm = pd.Series(0, index=data.index)
        pos_dm[(up > down) & (up > 0)] = up
        neg_dm[(down > up) & (down > 0)] = down
        # 平滑
        sma_period = period
        pos_di = 100 * pos_dm.rolling(sma_period).mean() / atr
        neg_di = 100 * neg_dm.rolling(sma_period).mean() / atr
        # DX和ADX
        dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di)
        adx = dx.rolling(sma_period).mean()
        # 映射到[-1,1]，低于25为负，低于15为-1，高于40为正
        result = 2 * (adx - 25) / (40 - 15)  # 线性映射
        result = result.clip(-1, 1)
        return result.fillna(0)
