"""
衍生品数据采集器（P2.6c，方案 §P2.6c）。

目标：funding/OI/清算独立采集与质量控制，不再散在 intel_signal。
数据源：Coinglass（聚合 funding）/ Velo（OI/basis）/ Kingfisher（清算热力图）。
任一源 down 降级不阻断；清算簇超阈触发 RiskAgent 波动 gating。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DerivativesSnapshot:
    """单品种衍生品快照。"""
    symbol: str
    funding_rate: Optional[float] = None       # 年化或 8h
    open_interest: Optional[float] = None      # USD
    basis: Optional[float] = None              # perp - spot，相对值
    liquidation_cluster_usd: Optional[float] = None  # 近 N 小时清算量
    ts_ns: int = 0
    sources_ok: list[str] = field(default_factory=list)    # 成功的源
    sources_failed: list[str] = field(default_factory=list)


class DerivativesCollector:
    """
    衍生品数据采集器（多源聚合 + 降级）。

    生产环境接 Coinglass/Velo/Kingfisher API；当前提供接口 + 降级逻辑，
    实际 HTTP 调用在接入 API key 后实现（fetch_* 方法）。
    """

    def __init__(self, liquidation_alert_threshold_usd: float = 5e7):
        self.liquidation_alert_threshold = liquidation_alert_threshold_usd

    def fetch_coinglass_funding(self, symbol: str) -> Optional[float]:
        """接 Coinglass API（[2026-08-06 2.2] 占位改为接线统一 DataProvider）。

        走 DataProvider 统一通道（免费→付费无缝切换、代理、统计/降级），
        与 derivatives_analytics_service 的 Layer5 同源；无 key 时返回 None 不阻断。
        注意：本类 DerivativesCollector 已被 derivatives_analytics_service 替代
        （无生产引用），本函数仅保持契约完整性。
        """
        try:
            from backend.services.data.data_provider import get_coinglass_provider

            return get_coinglass_provider().fetch_funding(symbol)
        except Exception:
            return None  # 采集不阻断

    def fetch_velo_oi_basis(self, symbol: str) -> tuple[Optional[float], Optional[float]]:
        """接 Velo API。占位。"""
        return None, None

    def fetch_kingfisher_liquidation(self, symbol: str) -> Optional[float]:
        """接 Kingfisher API。占位。"""
        return None

    def collect(self, symbol: str) -> DerivativesSnapshot:
        """聚合多源衍生品数据（任一源 down 降级不阻断）。"""
        snap = DerivativesSnapshot(symbol=symbol)
        # funding
        try:
            f = self.fetch_coinglass_funding(symbol)
            if f is not None:
                snap.funding_rate = f
                snap.sources_ok.append("coinglass")
            else:
                snap.sources_failed.append("coinglass")
        except Exception as e:
            snap.sources_failed.append("coinglass")
            logger.debug(f"coinglass funding 失败: {e}")
        # OI/basis
        try:
            oi, basis = self.fetch_velo_oi_basis(symbol)
            if oi is not None:
                snap.open_interest = oi
            if basis is not None:
                snap.basis = basis
            snap.sources_ok.append("velo")
        except Exception as e:
            snap.sources_failed.append("velo")
            logger.debug(f"velo oi/basis 失败: {e}")
        # 清算
        try:
            liq = self.fetch_kingfisher_liquidation(symbol)
            if liq is not None:
                snap.liquidation_cluster_usd = liq
            snap.sources_ok.append("kingfisher")
        except Exception as e:
            snap.sources_failed.append("kingfisher")
            logger.debug(f"kingfisher 清算失败: {e}")
        return snap

    def is_liquidation_alert(self, snap: DerivativesSnapshot) -> bool:
        """清算量超阈 → 触发 RiskAgent 波动 gating。"""
        return (snap.liquidation_cluster_usd is not None
                and snap.liquidation_cluster_usd > self.liquidation_alert_threshold)
