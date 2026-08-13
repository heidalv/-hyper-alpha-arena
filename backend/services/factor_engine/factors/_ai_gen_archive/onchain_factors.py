"""
ATAS V2 - 链上数据因子

包含4个链上数据相关因子:
- ExchangeNetFlowFactor: 交易所净流量
- WhaleTransactionFactor: 鲸鱼大额交易
- TVLChangeFactor: DeFi TVL变化率
- ActiveAddressFactor: 链上活跃地址数

数据注入方式: unified_data_pool 在 K线 DataFrame 中注入对应列。
因子检查列是否存在，无数据时优雅降级为零/中性序列。
"""
import pandas as pd
import numpy as np
from typing import Dict, Any

from ...factor_base import BaseFactor, FactorMetadata
from ...factor_registry import register_factor


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
        if 'exchange_net_flow' in data.columns:
            flow = data['exchange_net_flow'].fillna(0.0)
            if self.params.get('normalize', True):
                window = self.params.get('window', 24)
                mean = flow.rolling(window).mean()
                std = flow.rolling(window).std()
                return (flow - mean) / (std + 1e-10)
            return flow
        return pd.Series(0.0, index=data.index, name='exchange_net_flow')


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
        if 'whale_tx_count' in data.columns and 'whale_tx_volume' in data.columns:
            count = data['whale_tx_count'].fillna(0).astype(float)
            volume = data['whale_tx_volume'].fillna(0.0)
            window = self.params.get('window', 24)
            count_ma = count.rolling(window).mean()
            volume_ma = volume.rolling(window).mean()
            score = (count / (count_ma + 1e-10)) * (volume / (volume_ma + 1e-10))
            return score.fillna(1.0)
        return pd.Series(1.0, index=data.index, name='whale_transactions')


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
        if 'tvl' in data.columns:
            period = self.params.get('period', 7)
            return data['tvl'].fillna(0.0).pct_change(period)
        return pd.Series(0.0, index=data.index, name='tvl_change')


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
        if 'active_addresses' in data.columns:
            aa = data['active_addresses'].fillna(0).astype(float)
            window = self.params.get('window', 14)
            return aa / (aa.rolling(window).mean() + 1e-10)
        return pd.Series(1.0, index=data.index, name='active_addresses')


@register_factor()
class StablecoinMintBurnFactor(BaseFactor):
    """
    稳定币铸造/销毁因子（v6 阶段 2 补齐）

    净铸造（正值）→ 增量流动性入市 → 看多；净销毁（负值）→ 流动性收缩 → 看空。
    数据列 stablecoin_mint_burn 由 onchain_data_collector 的 Coinglass 通道注入。
    列缺失时返回中性 0，不伪造。
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
            return pd.Series(0.0, index=data.index, name='stablecoin_mint_burn')

        flow = data['stablecoin_mint_burn'].fillna(0.0)
        if self.params.get('normalize', True):
            window = self.params.get('window', 24)
            mean = flow.rolling(window).mean()
            std = flow.rolling(window).std()
            # 起始段（窗口不足）与恒定流量（std=0）产出 NaN → 中性 0
            return ((flow - mean) / (std + 1e-10)).fillna(0.0)
        return flow
