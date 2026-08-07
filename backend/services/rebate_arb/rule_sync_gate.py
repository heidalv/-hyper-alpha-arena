"""
RuleSyncGate — 规则同步执行闸门。

MVP 目标:
- 默认只暂停 Rebate/S1-S8 自动/手动执行
- V3 仅在 v3_pause=true 时暂停
- 手动 override / resume / live apply 必须写审计日志
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RuleSyncGateSnapshot:
    rebate_pause: bool = False
    v3_pause: bool = False
    paused_strategies: List[str] = field(default_factory=list)
    pause_reason: str = ""
    allow_manual_override: bool = False
    requires_code_change: bool = False
    paused_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rebate_pause": self.rebate_pause,
            "v3_pause": self.v3_pause,
            "paused_strategies": self.paused_strategies,
            "pause_reason": self.pause_reason,
            "allow_manual_override": self.allow_manual_override,
            "requires_code_change": self.requires_code_change,
            "paused_at": self.paused_at,
            "is_rebate_paused": self.rebate_pause or bool(self.paused_strategies),
            "is_v3_paused": self.v3_pause,
        }


class RuleSyncGate:
    """Persistent execution gate for rule-sync safety pauses."""

    def __init__(self):
        self._state = RuleSyncGateSnapshot()
        self._loaded = False

    def get_state(self) -> Dict[str, Any]:
        self._ensure_loaded()
        return self._state.to_dict()

    def is_rebate_blocked(self, strategy_type: str = "", manual: bool = False) -> bool:
        self._ensure_loaded()
        strategy = (strategy_type or "").upper()
        blocked = self._state.rebate_pause or (
            strategy and strategy in {s.upper() for s in self._state.paused_strategies}
        )
        if manual and blocked and self._state.allow_manual_override:
            return False
        return blocked

    def is_v3_blocked(self) -> bool:
        self._ensure_loaded()
        return bool(self._state.v3_pause)

    def block_reason(self, strategy_type: str = "") -> str:
        self._ensure_loaded()
        strategy = (strategy_type or "").upper()
        if self._state.rebate_pause:
            return self._state.pause_reason or "规则同步闸门已暂停 Rebate 自动执行"
        if strategy and strategy in {s.upper() for s in self._state.paused_strategies}:
            return self._state.pause_reason or f"策略 {strategy} 因规则同步暂停"
        if self._state.v3_pause:
            return self._state.pause_reason or "规则同步闸门已暂停 V3 套利"
        return ""

    def pause(
        self,
        *,
        strategies: Optional[List[str]] = None,
        reason: str = "",
        rebate_pause: bool = True,
        v3_pause: bool = False,
        requires_code_change: bool = False,
        allow_manual_override: Optional[bool] = None,
        actor_user_id: Optional[int] = None,
        risk_acknowledged: bool = False,
    ) -> Dict[str, Any]:
        self._ensure_loaded()
        before = self._state.to_dict()
        paused = sorted({s.upper() for s in (strategies or []) if s})
        self._state.rebate_pause = bool(rebate_pause)
        self._state.v3_pause = bool(v3_pause)
        self._state.paused_strategies = paused
        self._state.pause_reason = reason or "规则同步触发暂停"
        self._state.requires_code_change = bool(requires_code_change)
        if allow_manual_override is not None:
            self._state.allow_manual_override = bool(allow_manual_override)
        self._state.paused_at = time.time()
        self._persist()
        self._audit(
            action="pause",
            target_type="rule_sync_gate",
            target_id="global",
            before=before,
            after=self._state.to_dict(),
            reason=reason,
            actor_user_id=actor_user_id,
            risk_acknowledged=risk_acknowledged,
        )
        logger.warning("[RuleSyncGate] paused: %s", self._state.to_dict())
        return self._state.to_dict()

    def resume(
        self,
        *,
        reason: str = "",
        actor_user_id: Optional[int] = None,
        risk_acknowledged: bool = False,
    ) -> Dict[str, Any]:
        self._ensure_loaded()
        before = self._state.to_dict()
        self._state = RuleSyncGateSnapshot()
        self._persist()
        self._audit(
            action="resume",
            target_type="rule_sync_gate",
            target_id="global",
            before=before,
            after=self._state.to_dict(),
            reason=reason,
            actor_user_id=actor_user_id,
            risk_acknowledged=risk_acknowledged,
        )
        logger.info("[RuleSyncGate] resumed")
        return self._state.to_dict()

    def record_override(
        self,
        *,
        strategy_type: str,
        reason: str,
        actor_user_id: Optional[int] = None,
        risk_acknowledged: bool = False,
    ) -> None:
        self._ensure_loaded()
        self._audit(
            action="override_execute",
            target_type="strategy",
            target_id=strategy_type,
            before=self._state.to_dict(),
            after=self._state.to_dict(),
            reason=reason,
            actor_user_id=actor_user_id,
            risk_acknowledged=risk_acknowledged,
        )

    def record_audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        reason: str = "",
        actor_user_id: Optional[int] = None,
        risk_acknowledged: bool = False,
    ) -> None:
        """Public audit helper for rule-sync/event/proposal state changes."""
        self._ensure_loaded()
        self._audit(
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=reason,
            actor_user_id=actor_user_id,
            risk_acknowledged=risk_acknowledged,
        )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import RuleSyncGateStateDB

            db = SessionLocal()
            try:
                row = db.query(RuleSyncGateStateDB).order_by(RuleSyncGateStateDB.id.desc()).first()
                if row:
                    try:
                        paused = json.loads(row.paused_strategies_json or "[]")
                    except Exception:
                        paused = []
                    self._state = RuleSyncGateSnapshot(
                        rebate_pause=bool(row.rebate_pause),
                        v3_pause=bool(row.v3_pause),
                        paused_strategies=paused,
                        pause_reason=row.pause_reason or "",
                        allow_manual_override=bool(row.allow_manual_override),
                        requires_code_change=bool(row.requires_code_change),
                        paused_at=row.paused_at,
                    )
            finally:
                db.close()
        except Exception as e:
            logger.debug("[RuleSyncGate] load fallback: %s", e)
        self._loaded = True

    def _persist(self) -> None:
        try:
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import RuleSyncGateStateDB

            db = SessionLocal()
            try:
                row = RuleSyncGateStateDB(
                    rebate_pause=self._state.rebate_pause,
                    v3_pause=self._state.v3_pause,
                    paused_strategies_json=json.dumps(self._state.paused_strategies),
                    pause_reason=self._state.pause_reason,
                    allow_manual_override=self._state.allow_manual_override,
                    requires_code_change=self._state.requires_code_change,
                    paused_at=self._state.paused_at,
                )
                db.add(row)
                sqlite_write_commit(db, label="rule_sync_gate_persist")
            finally:
                db.close()
        except Exception as e:
            logger.warning("[RuleSyncGate] persist failed: %s", e)

    def _audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before: Dict[str, Any],
        after: Dict[str, Any],
        reason: str = "",
        actor_user_id: Optional[int] = None,
        risk_acknowledged: bool = False,
    ) -> None:
        try:
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import RuleSyncAuditLogDB

            db = SessionLocal()
            try:
                db.add(RuleSyncAuditLogDB(
                    actor_user_id=actor_user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    before_json=json.dumps(before, ensure_ascii=False, default=str),
                    after_json=json.dumps(after, ensure_ascii=False, default=str),
                    reason=reason,
                    risk_acknowledged=bool(risk_acknowledged),
                ))
                sqlite_write_commit(db, label="rule_sync_audit")
            finally:
                db.close()
        except Exception as e:
            logger.warning("[RuleSyncGate] audit failed: %s", e)


rule_sync_gate = RuleSyncGate()
