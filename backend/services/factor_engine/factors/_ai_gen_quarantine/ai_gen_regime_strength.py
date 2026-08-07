"""AI因子: 多周期趋势一致性因子 | 置信:60% | 衡量不同时间周期趋势方向的一致性。当短周期（5日）和长周期（20日）的趋势方向一致向上时，信号为+1；方向不一致或均向下时，信号为-1。使用线性回归斜率衡量趋势方向。短周期斜率大于0且长周期斜率大于0 => 一致向上，返回+1；若短周期斜率小于0且长周期斜率小于0 => 一致向下，返回-1；其他情况（分歧或震荡）返回0。以此避免在趋势不明或矛盾时追多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Trend_Consistency(BaseFactor):
    """衡量不同时间周期趋势方向的一致性。当短周期（5日）和长周期（20日）的趋势方向一致向上时，信号为+1；方向不一致或均向下时，信号为-1。使用线性回归斜率衡量趋势方向。短周期斜率大于0且长周期斜率大于0 => 一致向上，返回+1；若短周期斜率小于0且长周期斜率小于0 => 一致向下，返回-1；其他情况（分歧或震荡）返回0。以此避免在趋势不明或矛盾时追多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_strength",
            name="Multi-Timeframe Trend Consistency",
            display_name="多周期趋势一致性因子",
            description="衡量不同时间周期趋势方向的一致性。当短周期（5日）和长周期（20日）的趋势方向一致向上时，信号为+1；方向不一致或均向下时，信号为-1。使用线性回归斜率衡量趋势方向。短周期斜率大于0且长周期斜率大于0 => 一致向上，返回+1；若短周期斜率小于0且长周期斜率小于0 => 一致向下，返回-1；其他情况（分歧或震荡）返回0。以此避免在趋势不明或矛盾时追多。",
            category="behavioral",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        from sklearn.linear_model import LinearRegression
        df = data.copy()
        def slope(series):
            # 计算滚动线性斜率，使用最近period个点
            x = np.arange(len(series))
            # 仅当非空值足够时才计算
            if series.isnull().sum() > 0:
                return np.nan
            model = LinearRegression()
            model.fit(x.reshape(-1,1), series.values)
            return model.coef_[0]
        # 短周期5日斜率
        df['slope_5'] = df['close'].rolling(5).apply(lambda s: slope(s) if s.notna().sum()==5 else np.nan, raw=False)
        # 长周期20日斜率（需要20个数据点）
        df['slope_20'] = df['close'].rolling(20).apply(lambda s: slope(s) if s.notna().sum()==20 else np.nan, raw=False)
        # 方向判断
        cond_up = (df['slope_5'] > 0) & (df['slope_20'] > 0)
        cond_down = (df['slope_5'] < 0) & (df['slope_20'] < 0)
        signal = np.where(cond_up, 1.0, np.where(cond_down, -1.0, 0.0))
        result = pd.Series(signal, index=df.index).fillna(0.0)
        return result
