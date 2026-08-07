"""
ShadowJudge — 自动晋升/回滚（P4.5，方案 §P4.5 / §4.8）。

目标（R4 阈值驱动）：把因子/模型生命周期状态机（P1.3）与实时指标连接，
自动驱动 promote/deweigh/rollback/rollback，全程无人工闸门
（仅高破坏性转换 SMALL_LIVE/ACTIVE 保留 OversightAgent 审批，超时默认拒）。

整合：
    - DriftWatcher（P4.2）：drift 未消解 → 触发模型 ROLLBACK
    - FactorMetrics（P1.3 lifecycle）：阈值达标 → 自动状态转换
    - DualTrackExecutor（P3.1）：ShadowDeviation → 因子降权依据
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.services.factor_engine.lifecycle import (
    FactorMetrics,
    LifecycleThresholds,
    TransitionDecision,
    evaluate_transition,
    needs_approval,
)

logger = logging.getLogger(__name__)


@dataclass
class ShadowJudgment:
    """ShadowJudge 的判定结果。"""
    factor_id: str
    decision: TransitionDecision
    executed: bool = False      # 是否已执行（auto=True 且不需审批）
    pending_approval: bool = False  # 是否待审批
    approval_timeout_ns: int = 24 * 3600 * 1_000_000_000  # 24h 超时默认拒
    ts_ns: int = 0


class ShadowJudge:
    """
    自动晋升/回滚判定器。

    周期性（如每日）调用 judge() 评估所有因子，驱动状态机转换。
    审批：仅 SMALL_LIVE/ACTIVE 需 OversightAgent；超时默认拒（R4 不留悬置）。
    """

    def __init__(self, thresholds: LifecycleThresholds | None = None):
        self.thresholds = thresholds or LifecycleThresholds()
        self._pending: dict[str, ShadowJudgment] = {}  # 待审批
        self._history: list[ShadowJudgment] = []

    def judge(self, metrics: FactorMetrics) -> ShadowJudgment:
        """
        评估单个因子，返回 ShadowJudgment。

        调用方据此决定：
            executed=True → 已自动转换（如 ORTHO→PAPER、降权、隔离）
            pending_approval=True → 需 OversightAgent 审批，超时拒
        """
        decision = evaluate_transition(metrics, self.thresholds)
        ts = time.time_ns()
        judgment = ShadowJudgment(
            factor_id=metrics.factor_id, decision=decision, ts_ns=ts,
        )

        if decision.to_state == decision.from_state:
            # 无转换
            return judgment

        if decision.auto and not needs_approval(decision):
            # 自动执行（ORTHO→PAPER、降权、隔离、bug 拒绝）
            judgment.executed = True
            logger.info(
                f"[ShadowJudge] 自动转换 {metrics.factor_id}: "
                f"{decision.from_state}→{decision.to_state} ({decision.reason})"
            )
        elif decision.auto and needs_approval(decision):
            # 需审批（SMALL_LIVE/ACTIVE）
            judgment.pending_approval = True
            self._pending[metrics.factor_id] = judgment
            logger.info(
                f"[ShadowJudge] 待审批 {metrics.factor_id}: "
                f"{decision.from_state}→{decision.to_state} ({decision.reason})"
            )
        self._history.append(judgment)
        return judgment

    def approve(self, factor_id: str) -> bool:
        """OversightAgent 审批通过。"""
        if factor_id in self._pending:
            j = self._pending.pop(factor_id)
            j.executed = True
            j.pending_approval = False
            logger.info(f"[ShadowJudge] 审批通过 {factor_id}: {j.decision.to_state}")
            return True
        return False

    def check_approval_timeout(self) -> list[str]:
        """检查审批超时（超时默认拒，R4）。返回被拒的 factor_id 列表。"""
        now = time.time_ns()
        timed_out = []
        for fid, j in list(self._pending.items()):
            if now - j.ts_ns > j.approval_timeout_ns:
                self._pending.pop(fid)
                timed_out.append(fid)
                logger.warning(
                    f"[ShadowJudge] 审批超时拒绝 {fid}: "
                    f"{j.decision.from_state}→{j.decision.to_state}（超时默认拒）"
                )
        return timed_out

    def pending_count(self) -> int:
        return len(self._pending)

    def stats(self) -> dict:
        auto = sum(1 for j in self._history if j.executed and not j.pending_approval)
        approved = sum(1 for j in self._history if j.executed)
        return {
            "total_judgments": len(self._history),
            "auto_executed": auto,
            "pending_approval": len(self._pending),
            "approved": approved,
        }

    def history(self, limit: int = 50) -> list[ShadowJudgment]:
        return self._history[-limit:]
