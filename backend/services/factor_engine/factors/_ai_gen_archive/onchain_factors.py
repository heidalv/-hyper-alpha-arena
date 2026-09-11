"""
ATAS V2 - 链上数据因子

包含5个链上数据相关因子:
- ExchangeNetFlowFactor: 交易所净流量
- WhaleTransactionFactor: 鲸鱼大额交易
- TVLChangeFactor: DeFi TVL变化率
- ActiveAddressFactor: 链上活跃地址数
- StablecoinMintBurnFactor: 稳定币铸造/销毁

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入对应列。

[2026-09 修复 P0-4] 「无数据」诚实约定：因子框架以全 NaN 序列作为「数据不可用」
标记（factor_calculator 同款约定），冷池扫描（midlong_cold_pool）对 isfinite < 60
的序列直接丢弃、评估器 dropna 后计算 IC。此前列缺失时返回 0/1 中性序列，等于把
「数据真空」伪装成「真实中性」参与滚动 IC 与打分。现改为：列缺失或全 NaN → 返回
全 NaN 序列；存在部分数据 → NaN 自然传播（不 fillna(0) 冒充）。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


def _missing_series(data: pd.DataFrame, name: str) -> pd.Series:
    """数据列缺失时的诚实输出：全 NaN（框架据此跳过该因子）。"""
    return pd.Series(np.nan, index=data.index, name=name)


@register_factor()
class ExchangeNetFlowFactor(BaseFactor):
    """
    交易所净流量因子
    正值=流入（卖压），负值=流出（囤币）
    数据源: Glassnode Free API / CryptoQuant
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='exchange_net_flow',
            name='ExchangeNetFlow',
            display_name='交易所净流量',
            description='交易所BTC/ETH净流入流出量（Z-Score标准化）',
            category='onchain',
            subcategory='flow',
            lookback_period=24,
            required_data_fields=['close'],
            cache_ttl=3600,
            aliases=['onchain_netflow'],
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24, 'normalize': True}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'exchange_net_flow' not in data.columns:
            return _missing_series(data, 'exchange_net_flow')
        flow = data['exchange_net_flow'].astype(float)
        if not flow.notna().any():
            return _missing_series(data, 'exchange_net_flow')
        if self.params.get('normalize', True):
            window = self.params.get('window', 24)
            mean = flow.rolling(window).mean()
            std = flow.rolling(window).std()
            return (flow - mean) / (std + 1e-10)
        return flow


@register_factor()
class WhaleTransactionFactor(BaseFactor):
    """
    大额转账因子
    追踪>$1M的链上转账数量和方向
    数据源: Etherscan + whale-alert 类API
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='whale_transactions',
            name='WhaleTransactions',
            display_name='鲸鱼交易',
            description='大额链上转账活跃度指标',
            category='onchain',
            subcategory='whale',
            lookback_period=12,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'whale_tx_count' not in data.columns or 'whale_tx_volume' not in data.columns:
            return _missing_series(data, 'whale_transactions')
        count = data['whale_tx_count'].astype(float)
        volume = data['whale_tx_volume'].astype(float)
        if not count.notna().any() and not volume.notna().any():
            return _missing_series(data, 'whale_transactions')
        window = self.params.get('window', 24)
        count_ma = count.rolling(window).mean()
        volume_ma = volume.rolling(window).mean()
        score = (count / (count_ma + 1e-10)) * (volume / (volume_ma + 1e-10))
        return score


@register_factor()
class TVLChangeFactor(BaseFactor):
    """
    DeFi TVL变化率因子
    数据源: DefiLlama API（免费、无限制）
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='tvl_change',
            name='TVLChange',
            display_name='TVL变化率',
            description='DeFi协议总锁仓量变化率',
            category='onchain',
            subcategory='defi',
            lookback_period=7,
            required_data_fields=['close'],
            cache_ttl=7200,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'period': 7}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'tvl' not in data.columns:
            return _missing_series(data, 'tvl_change')
        tvl = data['tvl'].astype(float)
        if not tvl.notna().any():
            return _missing_series(data, 'tvl_change')
        period = self.params.get('period', 7)
        return tvl.pct_change(period)


@register_factor()
class ActiveAddressFactor(BaseFactor):
    """
    链上活跃地址数因子
    数据源: Glassnode Free / Etherscan
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='active_addresses',
            name='ActiveAddresses',
            display_name='活跃地址数',
            description='链上日活跃地址数量（相对均值比率）',
            category='onchain',
            subcategory='network',
            lookback_period=14,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 14}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'active_addresses' not in data.columns:
            return _missing_series(data, 'active_addresses')
        aa = data['active_addresses'].astype(float)
        if not aa.notna().any():
            return _missing_series(data, 'active_addresses')
        window = self.params.get('window', 14)
        return aa / (aa.rolling(window).mean() + 1e-10)


@register_factor()
class StablecoinMintBurnFactor(BaseFactor):
    """
    稳定币铸造/销毁因子（v6 阶段 2 补齐）

    净铸造（正值）→ 增量流动性入市 → 看多；净销毁（负值）→ 流动性收缩 → 看空。
    数据列 stablecoin_mint_burn 由 onchain_data_collector 的 Coinglass 通道注入。
    [2026-09 修复 P0-4] 列缺失/全空时返回全 NaN（框架跳过），不再返回中性 0。
    """

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='stablecoin_mint_burn',
            name='StablecoinMintBurn',
            display_name='稳定币铸造/销毁',
            description='稳定币净铸造量（流动性扩张/收缩信号，Z-Score）',
            category='onchain',
            subcategory='flow',
            lookback_period=24,
            required_data_fields=['close'],
            cache_ttl=3600,
        )

    def get_default_params(self) -> Dict[str, Any]:
        return {'window': 24, 'normalize': True}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        if 'stablecoin_mint_burn' not in data.columns:
            return _missing_series(data, 'stablecoin_mint_burn')

        flow = data['stablecoin_mint_burn'].astype(float)
        if not flow.notna().any():
            return _missing_series(data, 'stablecoin_mint_burn')

        if self.params.get('normalize', True):
            window = self.params.get('window', 24)
            mean = flow.rolling(window).mean()
            std = flow.rolling(window).std()
            # 起始段（窗口不足）产出 NaN → 保留 NaN，由评估器 dropna 处理
            return (flow - mean) / (std + 1e-10)
        return flow
