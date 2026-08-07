"""Legacy 短名因子兼容层（L4 因子并轨）。

把旧 FactorEngine.FACTORS 的 21 个因子原样迁成 BaseFactor 子类，
保留原 factor_id（rsi/macd/adx 等短名）和原算法参数，
注册到新 FactorRegistry，使下游按短名查因子时行为不变。
"""
