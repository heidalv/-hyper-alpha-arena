"""AI因子: 均值回归疲态 | 置信:60% | 检测价格围绕移动平均线反复穿越且振幅衰减的状态，这种“均值回归疲态”常出现在趋势启动失败或无方向震荡市中，容易导致持仓超时。因子计算价格与均线的标准化距离及其变化率，当穿越频率高而偏离幅度缩小时输出-1，当出现持续偏离(趋势)时输出+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionFatigue(BaseFactor):
    """检测价格围绕移动平均线反复穿越且振幅衰减的状态，这种“均值回归疲态”常出现在趋势启动失败或无方向震荡市中，容易导致持仓超时。因子计算价格与均线的标准化距离及其变化率，当穿越频率高而偏离幅度缩小时输出-1，当出现持续偏离(趋势)时输出+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mft",
            name="Mean Reversion Fatigue",
            display_name="均值回归疲态",
            description="检测价格围绕移动平均线反复穿越且振幅衰减的状态，这种“均值回归疲态”常出现在趋势启动失败或无方向震荡市中，容易导致持仓超时。因子计算价格与均线的标准化距离及其变化率，当穿越频率高而偏离幅度缩小时输出-1，当出现持续偏离(趋势)时输出+1。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        z_score = (close - ma) / (std + 1e-9)
        sign = np.sign(z_score)
        crossings = sign.diff().abs().rolling(10).sum()
        amplitude = z_score.rolling(10).std()
        cross_norm = np.clip(crossings / 5.0, 0, 1)
        amp_norm = np.clip(amplitude / 0.8, 0, 1)
        fatigue = cross_norm * (1 - amp_norm)
        result = -2 * fatigue + 1.0
        result = np.clip(result, -1.0, 1.0)
        return pd.Series(result, index=data.index)
