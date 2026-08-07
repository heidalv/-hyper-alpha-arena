"""
在线学习机制职责边界（P4.7d，方案 §P4.7d / 解决环境7缺陷2）。

目标：OWM、continual_learning、RL 三套在线机制职责重叠，谁主导不清。
本路由表按 drift 类型/regime 显式路由到唯一主导机制。

路由规则：
    平稳/缓漂移         → River 在线线性（P4.3）主导
    突变 drift（ADWIN）  → RegimeAgent 切 regime + AlphaEnsemble 切子策略
    新 regime 持续      → MAML few-step adapt（P4.4）
    离线策略结构进化    → CMA-ES/MAP-Elites（P4.7b）+ RL 重训
    OWM（MLTO 中长线）  → 仅 mid/long thesis，不与短线 AlphaEnsemble 抢权重
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.services.evolution.drift_watcher import DriftEvent, DriftType


class Mechanism(str, Enum):
    """在线学习机制。"""
    RIVER_ONLINE = "river_online"          # P4.3 平稳/缓漂移
    REGIME_SWITCH = "regime_switch"        # 突变 drift → 切子策略
    MAML_ADAPT = "maml_adapt"              # 新 regime few-step
    HPO_OFFLINE = "hpo_offline"            # CMA-ES/MAP-Elites 结构进化
    RL_RETRAIN = "rl_retrain"              # RL 重训
    OWM_MIDLONG = "owm_midlong"            # MLTO 中长线专用
    NONE = "none"


@dataclass
class MechanismRoute:
    """路由决策。"""
    mechanism: Mechanism
    reason: str
    horizon: str = "short"  # short / mid / long


class MechanismRouter:
    """
    在线机制路由器（解耦三套重叠机制）。

    LearningOrchestrator 按 drift 类型/regime/horizon 路由到唯一主导机制。
    """

    def __init__(self):
        self._active: dict[str, Mechanism] = {}  # horizon -> active mechanism

    def route(
        self, drift: Optional[DriftEvent] = None,
        regime_changed: bool = False,
        regime_sustained: bool = False,
        horizon: str = "short",
        needs_structural_evolution: bool = False,
    ) -> MechanismRoute:
        """
        路由到主导机制。

        优先级：结构进化 > regime 持续 > 突变 drift > 缓漂移 > 平稳。
        """
        # 离线结构进化优先（最高代价，显式触发）
        if needs_structural_evolution:
            m = Mechanism.HPO_OFFLINE
            self._active[horizon] = m
            return MechanismRoute(m, "结构进化（CMA-ES/MAP-Elites）", horizon)

        # 中长线走 OWM，不与短线抢
        if horizon in ("mid", "long"):
            m = Mechanism.OWM_MIDLONG
            self._active[horizon] = m
            return MechanismRoute(m, "中长线 OWM（不抢短线权重）", horizon)

        # regime 持续 → MAML few-step adapt
        if regime_sustained:
            m = Mechanism.MAML_ADAPT
            self._active[horizon] = m
            return MechanismRoute(m, "新 regime 持续 → few-step adapt", horizon)

        # 突变 drift → regime 切换 + 子策略
        if regime_changed or (drift and drift.drift_type == DriftType.ABRUPT):
            m = Mechanism.REGIME_SWITCH
            self._active[horizon] = m
            return MechanismRoute(m, "突变 drift → 切 regime + 子策略", horizon)

        # 缓漂移 → River 在线
        if drift and drift.drift_type == DriftType.GRADUAL:
            m = Mechanism.RIVER_ONLINE
            self._active[horizon] = m
            return MechanismRoute(m, "缓漂移 → 在线权重更新", horizon)

        # 平稳 → River 在线（持续微调）
        m = Mechanism.RIVER_ONLINE
        self._active[horizon] = m
        return MechanismRoute(m, "平稳 → 在线微调", horizon)

    def active(self, horizon: str) -> Mechanism:
        return self._active.get(horizon, Mechanism.NONE)

    def ensure_no_conflict(self) -> list[str]:
        """
        验证短线 horizon 只有一个主导机制（无重叠争抢）。
        返回冲突描述列表（空 = 无冲突）。
        """
        conflicts = []
        short_active = self._active.get("short")
        # OWM 不应在 short horizon 激活
        if short_active == Mechanism.OWM_MIDLONG:
            conflicts.append("OWM 不应在 short horizon 激活（仅 mid/long）")
        return conflicts
