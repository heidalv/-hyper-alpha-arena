"""AI因子: 微观安静因子 | 置信:50% | 检测市场是否进入窄幅震荡、成交量萎缩的‘安静’状态，该状态下容易产生不明方向的微小亏损。因子为负值时警告安静状态，正值表示正常波动。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro_Quiet(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_miq", name="Micro_Quiet",
        display_name="微观安静因子", description="检测市场是否进入窄幅震荡、成交量萎缩的‘安静’状态，该状态下容易产生不明方向的微小亏损。因子为负值时警告安静状态，正值表示正常波动。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close'].values
    high = data['high'].values
    low = data['low'].values
    volume = data['volume'].values
    # 计算每根K线实体百分比和振幅
    body = np.abs(close - open) / open if 'open' in data else (high - low) / close
    # 实际代码使用open
    open_ = data['open'].values if 'open' in data else close
    body = np.abs(close - open_) / (open_ + 1e-10)
    amplitude = (high - low) / (open_ + 1e-10)
    # 近10期平均body和振幅
    window = 10
    avg_body = pd.Series(body).rolling(window).mean().fillna(body.mean())
    avg_amp = pd.Series(amplitude).rolling(window).mean().fillna(amplitude.mean())
    # 成交量萎缩：近10期平均成交量与过去50期对比
    avg_vol_10 = pd.Series(volume).rolling(10).mean()
    avg_vol_50 = pd.Series(volume).rolling(50).mean().fillna(avg_vol_10.mean())
    vol_ratio = avg_vol_10 / (avg_vol_50 + 1e-10)
    # 安静状态：body小且振幅小且成交量低
    quiet_score = (1 - avg_body / (avg_body.max()+1e-10)) * (1 - avg_amp / (avg_amp.max()+1e-10)) * (1 - vol_ratio)
    quiet_score = quiet_score.fillna(0)
    # 映射到[-1,0] 安静时为负，正常为正
    result = -quiet_score
    # 归一化到[-1,1]
    result = 2 * (result - result.min()) / (result.max() - result.min() + 1e-10) - 1
    return result.clip(-1,1)
