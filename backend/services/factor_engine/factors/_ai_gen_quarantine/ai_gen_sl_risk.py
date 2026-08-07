"""AI因子: 止损触发风险因子 | 置信:50% | 通过计算价格加速下跌/上涨的动量与极端RSI值，预测短期反向波动风险。当RSI进入超买/超卖区域且价格加速度异常时，容易触发止损。因子输出负值表示高风险，反之低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossTriggerRisk(BaseFactor):
    """通过计算价格加速下跌/上涨的动量与极端RSI值，预测短期反向波动风险。当RSI进入超买/超卖区域且价格加速度异常时，容易触发止损。因子输出负值表示高风险，反之低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_risk",
            name="Stop Loss Trigger Risk",
            display_name="止损触发风险因子",
            description="通过计算价格加速下跌/上涨的动量与极端RSI值，预测短期反向波动风险。当RSI进入超买/超卖区域且价格加速度异常时，容易触发止损。因子输出负值表示高风险，反之低风险。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 价格加速度（价格变化率的变化率）
        price_roc = close.pct_change(5)
        price_acc = price_roc.diff(3)  # 加速度
        # 反转信号：超买(>70)或超卖(<30)时风险高，结合加速度方向
        rsi_extreme = ((rsi > 70) | (rsi < 30)).astype(float) * 0.5
        # 加速度过大也视为风险
        acc_norm = np.abs(price_acc) / 0.05  # 假设正常加速度在±5%以内
        acc_risk = np.clip(acc_norm, 0, 1) * 0.5
        # 同时考虑价格与移动平均的乖离率
        ma20 = close.rolling(20).mean()
        ma_dev = (close - ma20) / ma20 * 100
        ma_norm = np.clip(np.abs(ma_dev) / 10, 0, 1) * 0.5  # 乖离>10%时风险高
        # 综合
        risk = (rsi_extreme + acc_risk + ma_norm) / 1.5
        # 反转：高风险时因子为负
        result = -risk + 0.2  # 偏移使中心接近0
        result = np.clip(result, -0.999, 0.999)
        result = pd.Series(result, index=close.index).fillna(0)
        return result
