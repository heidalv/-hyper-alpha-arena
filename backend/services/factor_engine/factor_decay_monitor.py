"""
D7: Factor Decay Monitor — 因子衰减监控 + 自动淘汰

定期评估每个因子的预测能力，自动降权或淘汰衰减因子。
配合 FactorSelector 的 IC 计算和 genetic_optimizer 的权重进化使用。
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DecayStatus:
    factor_id: str
    current_ic: float           # 最近IC均值
    historical_ic: float         # 历史IC均值
    decay_rate: float            # 衰减速率 (正=改善, 负=衰减)
    half_life_days: float        # 半衰期（天）
    trend: str                   # "improving" / "stable" / "declining" / "dead"
    recommendation: str          # "keep" / "reduce" / "retire"
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FactorDecayMonitor:
    """因子衰减监控器 — 单例"""
    
    _instance = None
    
    DECAY_THRESHOLDS = {
        "retire_ic": 0.01,        # IC 低于此值自动退役
        "reduce_ic": 0.03,        # IC 低于此值降权
        "decline_rate": -0.02,    # 月衰减率低于此 = declining
        "check_interval_days": 7, # 每7天全面检查一次
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._ic_history: Dict[str, List[float]] = {}  # factor_id → [ic1, ic2, ...]
        self._decay_status: Dict[str, DecayStatus] = {}
        self._last_full_check: Optional[datetime] = None
        logger.info("[DecayMonitor] 因子衰减监控初始化")
    
    def record_ic(self, factor_id: str, ic: float):
        """记录一次IC值（每次因子评估时调用）"""
        if factor_id not in self._ic_history:
            self._ic_history[factor_id] = []
        self._ic_history[factor_id].append(ic)
        # 保留最近 100 次记录
        if len(self._ic_history[factor_id]) > 100:
            self._ic_history[factor_id] = self._ic_history[factor_id][-100:]
    
    def evaluate_factor(self, factor_id: str) -> DecayStatus:
        """评估单个因子的衰减状态"""
        history = self._ic_history.get(factor_id, [])
        
        if len(history) < 20:
            return DecayStatus(
                factor_id=factor_id,
                current_ic=sum(history[-10:]) / max(len(history[-10:]), 1) if history else 0,
                historical_ic=sum(history) / len(history) if history else 0,
                decay_rate=0,
                half_life_days=999,
                trend="stable",
                recommendation="keep",
            )
        
        # 最近10次 vs 全部历史
        recent = sum(history[-10:]) / 10
        all_time = sum(history) / len(history)
        decay_rate = (recent - all_time) / max(abs(all_time), 0.001)
        
        # 半衰期估算
        if decay_rate < 0:
            half_life = -0.693 / decay_rate  # 简单的指数衰减
        else:
            half_life = 999
        
        # 判断趋势
        if recent < self.DECAY_THRESHOLDS["retire_ic"]:
            trend = "dead"
            recommendation = "retire"
        elif recent < self.DECAY_THRESHOLDS["reduce_ic"]:
            trend = "declining"
            recommendation = "reduce"
        elif decay_rate < self.DECAY_THRESHOLDS["decline_rate"]:
            trend = "declining"
            recommendation = "reduce"
        elif decay_rate > 0.02:
            trend = "improving"
            recommendation = "keep"
        else:
            trend = "stable"
            recommendation = "keep"
        
        status = DecayStatus(
            factor_id=factor_id,
            current_ic=round(recent, 4),
            historical_ic=round(all_time, 4),
            decay_rate=round(decay_rate, 4),
            half_life_days=round(half_life, 1),
            trend=trend,
            recommendation=recommendation,
        )
        
        self._decay_status[factor_id] = status
        return status
    
    def evaluate_all_factors(self) -> Dict[str, DecayStatus]:
        """评估所有因子"""
        results = {}
        for fid in self._ic_history:
            results[fid] = self.evaluate_factor(fid)
        self._last_full_check = datetime.now(timezone.utc)
        
        retired = [fid for fid, s in results.items() if s.recommendation == "retire"]
        reduced = [fid for fid, s in results.items() if s.recommendation == "reduce"]
        
        if retired:
            logger.warning(f"[DecayMonitor] {len(retired)}个因子建议退役: {retired}")
        if reduced:
            logger.info(f"[DecayMonitor] {len(reduced)}个因子建议降权: {reduced}")
        
        return results
    
    def get_factor_weight_penalty(self, factor_id: str) -> float:
        """返回因子权重惩罚系数 (1.0=无惩罚, 0.0=完全淘汰)"""
        status = self._decay_status.get(factor_id)
        if not status:
            return 1.0
        
        if status.recommendation == "retire":
            return 0.0
        elif status.recommendation == "reduce":
            return max(0.3, status.current_ic / max(status.historical_ic, 0.001))
        else:
            return 1.0


# 全局单例
decay_monitor = FactorDecayMonitor()
