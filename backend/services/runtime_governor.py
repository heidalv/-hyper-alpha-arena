"""RuntimeGovernor — runtime_tuning 意图仲裁、patch 审批与 session overlay。"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

OVERLAY_DIR = os.path.join("data", "runtime_tuning_overlays")
PENDING_DIR = os.path.join("data", "runtime_tuning_pending")
INTENTS_FILE = os.path.join("data", "runtime_tuning_intents.json")
DECISIONS_LOG = os.path.join("data", "runtime_governor_decisions.jsonl")

# 多来源意图优先级（高者优先）；与 LOCAL_LLM_SELF_TRAINING_DESIGN 对齐
SOURCE_PRIORITY: Dict[str, int] = {
    "manual": 100,
    "opencode": 80,
    "parity_score": 65,   # 回测/实盘一致性验证的冻结信号，略高于decision_feedback（更硬的安全刹车）
    "decision_feedback": 60,
    "local_llm_optimizer": 55,
    "evolution_gc": 50,
    "maturity": 40,
    "default": 30,
}

DEFAULT_TTL_SEC: Dict[str, Optional[float]] = {
    "manual": None,
    "opencode": 7 * 86400,
    "parity_score": 7 * 86400,  # 与Parity Score每周复算周期对齐，下一轮结果会覆盖/续期
    "decision_feedback": 36 * 3600,
    "local_llm_optimizer": 36 * 3600,
    "evolution_gc": 7 * 86400,
    "maturity": 3 * 86400,
    "default": 24 * 3600,
}

GOVERNED_KEYS: Tuple[str, ...] = (
    "disabled_natures",
    "max_daily_trades",
    "min_risk_reward",
    "scalp_min_confidence",
    "maturity_max_warmup_relief",
    "maturity_global_n1",
    "maturity_global_n2",
)


@dataclass
class TuningPatch:
    patch_id: str
    keys: Dict[str, Any]
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "patch_id": self.patch_id,
            "keys": self.keys,
            "reason": self.reason,
            "created_at": self.created_at,
            "status": self.status,
        }


class RuntimeGovernor:
    """运行时门槛 patch 仲裁（进化/OpenCode → 人工/自动 Approve → 生效）。"""

    _instance: Optional["RuntimeGovernor"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            os.makedirs(PENDING_DIR, exist_ok=True)
            os.makedirs(OVERLAY_DIR, exist_ok=True)
            os.makedirs(os.path.dirname(INTENTS_FILE) or "data", exist_ok=True)
        return cls._instance

    # ── 意图仲裁（submit_intent / withdraw）────────────────────────────

    @staticmethod
    def _load_intents() -> List[dict]:
        if not os.path.isfile(INTENTS_FILE):
            return []
        try:
            with open(INTENTS_FILE, encoding="utf-8") as f:
                data = json.load(f) or []
            return data if isinstance(data, list) else []
        except Exception as err:
            logger.warning("[RuntimeGovernor] 读取 intents 失败: %s", err)
            return []

    @staticmethod
    def _save_intents(intents: List[dict]) -> None:
        os.makedirs(os.path.dirname(INTENTS_FILE) or "data", exist_ok=True)
        with open(INTENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(intents, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _purge_expired(intents: List[dict]) -> List[dict]:
        now = time.time()
        kept: List[dict] = []
        for it in intents:
            exp = it.get("expires_at")
            if exp is not None and float(exp) <= now:
                continue
            kept.append(it)
        return kept

    @staticmethod
    def _priority(source: str) -> int:
        return int(SOURCE_PRIORITY.get(source or "default", SOURCE_PRIORITY["default"]))

    @classmethod
    def _pick_winner(cls, intents_for_key: List[dict]) -> Optional[dict]:
        if not intents_for_key:
            return None
        ranked = sorted(
            intents_for_key,
            key=lambda it: (
                cls._priority(str(it.get("source") or "default")),
                float(it.get("confidence") or 0),
                float(it.get("submitted_at") or 0),
            ),
            reverse=True,
        )
        return ranked[0]

    @staticmethod
    def _default_for_key(key: str) -> Any:
        from backend.services.runtime_tuning_store import _DEFAULT_SCHEMA

        entry = _DEFAULT_SCHEMA.get(key)
        if isinstance(entry, dict) and "value" in entry:
            return entry["value"]
        return entry

    @staticmethod
    def _log_decision(entry: dict) -> None:
        try:
            os.makedirs(os.path.dirname(DECISIONS_LOG) or "data", exist_ok=True)
            with open(DECISIONS_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _reconcile_keys(self, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """按当前意图重新计算受管 key 并写入 runtime_tuning.json。"""
        intents = self._purge_expired(self._load_intents())
        self._save_intents(intents)

        target_keys = list(keys) if keys else list(GOVERNED_KEYS)
        by_key: Dict[str, List[dict]] = {}
        for it in intents:
            k = it.get("key")
            if not k:
                continue
            by_key.setdefault(str(k), []).append(it)

        from backend.services.runtime_tuning_store import apply_patches

        applied: Dict[str, Any] = {}
        reverted: List[str] = []
        for key in target_keys:
            winner = self._pick_winner(by_key.get(key) or [])
            if winner:
                patch = apply_patches({key: winner["value"]})
                if key in patch:
                    applied[key] = patch[key]
                    self._log_decision(
                        {
                            "ts": time.time(),
                            "key": key,
                            "value": patch[key],
                            "winner_source": winner.get("source"),
                            "confidence": winner.get("confidence"),
                            "reason": winner.get("reason"),
                            "action": "applied",
                        }
                    )
            else:
                default_val = self._default_for_key(key)
                if default_val is not None:
                    patch = apply_patches({key: default_val})
                    if key in patch:
                        reverted.append(key)
                        self._log_decision(
                            {
                                "ts": time.time(),
                                "key": key,
                                "value": patch[key],
                                "winner_source": None,
                                "action": "reverted_default",
                            }
                        )
        return {"applied": applied, "reverted": reverted}

    def submit_intent(
        self,
        key: str,
        value: Any,
        *,
        source: str = "default",
        confidence: float = 0.5,
        reason: str = "",
        ttl_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """提交单 key 调参意图，按优先级仲裁后立即写入 runtime_tuning。"""
        if key not in GOVERNED_KEYS:
            logger.warning("[RuntimeGovernor] 非受管 key，仍接受: %s", key)

        now = time.time()
        if ttl_sec is None:
            ttl_sec = DEFAULT_TTL_SEC.get(source, DEFAULT_TTL_SEC["default"])
        expires_at = (now + float(ttl_sec)) if ttl_sec else None

        intents = self._purge_expired(self._load_intents())
        intents = [it for it in intents if not (it.get("source") == source and it.get("key") == key)]
        intents.append(
            {
                "key": key,
                "value": value,
                "source": source,
                "confidence": float(confidence),
                "reason": reason or "",
                "submitted_at": now,
                "expires_at": expires_at,
            }
        )
        self._save_intents(intents)

        recon = self._reconcile_keys([key])
        winner = self._pick_winner(
            [it for it in intents if it.get("key") == key]
        )
        winner_source = (winner or {}).get("source")
        applied_now = key in (recon.get("applied") or {})
        logger.info(
            "[RuntimeGovernor] submit_intent %s=%s source=%s conf=%.2f → applied=%s winner=%s",
            key, value, source, confidence, applied_now, winner_source,
        )
        return {
            "ok": True,
            "key": key,
            "applied": applied_now,
            "winner_source": winner_source,
            "value": (recon.get("applied") or {}).get(key, value),
        }

    def withdraw(self, source: str, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """撤销某来源的意图；keys 为空则撤销该来源全部。"""
        intents = self._load_intents()
        affected: List[str] = []
        kept: List[dict] = []
        for it in intents:
            if it.get("source") != source:
                kept.append(it)
                continue
            k = str(it.get("key") or "")
            if keys is None or k in keys:
                if k and k not in affected:
                    affected.append(k)
            else:
                kept.append(it)
        self._save_intents(kept)
        recon = self._reconcile_keys(affected if affected else None)
        logger.info("[RuntimeGovernor] withdraw source=%s keys=%s → %s", source, keys, recon)
        return {"ok": True, "source": source, "affected_keys": affected, **recon}

    def recent_decisions(self, *, limit: int = 50) -> List[dict]:
        """最近仲裁决策（供 OpenCode / 学习中心展示）。"""
        if not os.path.isfile(DECISIONS_LOG):
            return []
        try:
            with open(DECISIONS_LOG, encoding="utf-8") as f:
                lines = f.readlines()
            out: List[dict] = []
            for line in reversed(lines[-limit * 2 :]):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

    @staticmethod
    def _paper_auto_approve_enabled() -> bool:
        try:
            from backend.config.settings import RUNTIME_GOVERNOR_AUTO_APPROVE_PAPER
            if not RUNTIME_GOVERNOR_AUTO_APPROVE_PAPER:
                return False
            from backend.services.lock_strength_service import get_lock_strength_service
            return bool(get_lock_strength_service().get_profile("paper").disable_loss_locks)
        except Exception:
            return False

    def propose_patch(self, keys: Dict[str, Any], reason: str = "") -> TuningPatch:
        patch = TuningPatch(patch_id=str(uuid.uuid4())[:12], keys=dict(keys), reason=reason)
        path = os.path.join(PENDING_DIR, f"{patch.patch_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(patch.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("[RuntimeGovernor] 新 patch 待审批: %s keys=%s", patch.patch_id, list(keys))
        if self._paper_auto_approve_enabled():
            if self.approve(patch.patch_id):
                patch.status = "approved"
                logger.info("[RuntimeGovernor] Paper 自动批准 patch %s", patch.patch_id)
        return patch

    def list_pending(self) -> List[dict]:
        out: List[dict] = []
        if not os.path.isdir(PENDING_DIR):
            return out
        for name in sorted(os.listdir(PENDING_DIR)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(PENDING_DIR, name), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
        return out

    def approve(self, patch_id: str) -> bool:
        pending_path = os.path.join(PENDING_DIR, f"{patch_id}.json")
        if not os.path.isfile(pending_path):
            logger.warning("[RuntimeGovernor] patch 不存在: %s", patch_id)
            return False
        try:
            with open(pending_path, encoding="utf-8") as f:
                data = json.load(f)
            keys = data.get("keys") or {}
            patch_type = keys.get("_patch_type") or "runtime_tuning"

            if patch_type == "hermes_prompt":
                ok = self._apply_hermes_prompt_patch(keys)
                if not ok:
                    return False
            elif patch_type == "hermes_genesis_promote":
                ok = self._apply_genesis_promote(keys)
                if not ok:
                    return False
            elif patch_type == "training_live_promote":
                ok = self._apply_live_promote(keys)
                if not ok:
                    return False
            elif patch_type == "promotion_gate":
                ok = self._apply_promotion_gate_patch(keys)
                if not ok:
                    return False
            else:
                from backend.services.runtime_tuning_store import apply_patches
                apply_patches({k: v for k, v in keys.items() if not str(k).startswith("_")})

            proposal_id = keys.get("_proposal_id")
            if proposal_id and keys.get("_source") == "hermes_l3":
                try:
                    from backend.services.hermes_architecture_evolution_engine import (
                        architecture_evolution,
                    )
                    exec_keys = {
                        k: v for k, v in keys.items()
                        if not str(k).startswith("_") and k != "hermes_l3_note"
                    }
                    architecture_evolution.mark_implemented(
                        int(proposal_id),
                        governor_patch_id=patch_id,
                        executable=bool(exec_keys),
                    )
                except Exception as mark_err:
                    logger.debug("[RuntimeGovernor] L3 mark_implemented 跳过: %s", mark_err)

            data["status"] = "approved"
            os.remove(pending_path)
            logger.info("[RuntimeGovernor] 已批准 patch %s type=%s", patch_id, patch_type)
            return True
        except Exception as err:
            logger.error("[RuntimeGovernor] approve 失败 %s: %s", patch_id, err)
            return False

    def propose_hermes_prompt(self, task_id: str, version: str, reason: str = "") -> TuningPatch:
        """Hermes L2：新 prompt 版本激活需 Governor 审批（可选自动批准）。"""
        return self.propose_patch(
            {
                "_patch_type": "hermes_prompt",
                "task_id": task_id,
                "version": version,
            },
            reason or f"Hermes L2 激活 prompt {task_id}@{version}",
        )

    def propose_genesis_promote(self, candidate_id: int, strategy_id: str, reason: str = "") -> TuningPatch:
        return self.propose_patch(
            {
                "_patch_type": "hermes_genesis_promote",
                "candidate_id": candidate_id,
                "strategy_id": strategy_id,
            },
            reason or f"Hermes L4 晋升策略 {strategy_id}",
        )

    @staticmethod
    def _apply_hermes_prompt_patch(keys: Dict[str, Any]) -> bool:
        task_id = keys.get("task_id")
        version = keys.get("version")
        if not task_id or not version:
            return False
        try:
            from backend.services.hermes_db import hermes_execute
            hermes_execute(
                "UPDATE prompt_versions SET status='deprecated' "
                "WHERE task_id=? AND status IN ('active','ab_testing') AND version!=?",
                (task_id, version),
            )
            hermes_execute(
                "UPDATE prompt_versions SET status='active', activated_at=datetime('now') "
                "WHERE task_id=? AND version=?",
                (task_id, version),
            )
            logger.info("[RuntimeGovernor] Hermes prompt 已激活 %s@%s", task_id, version)
            return True
        except Exception as exc:
            logger.error("[RuntimeGovernor] Hermes prompt 激活失败: %s", exc)
            return False

    @staticmethod
    def _apply_genesis_promote(keys: Dict[str, Any]) -> bool:
        strategy_id = keys.get("strategy_id")
        candidate_id = keys.get("candidate_id")
        if not strategy_id:
            return False
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import AIStrategy
            db = SessionLocal()
            try:
                row = db.query(AIStrategy).filter(AIStrategy.strategy_id == strategy_id).first()
                if not row:
                    return False
                genome = dict(row.genome or {})
                genome["paper_only"] = False
                genome["hermes_promoted_at"] = time.time()
                row.genome = genome
                row.status = "active"
                db.commit()
            finally:
                db.close()
            if candidate_id:
                from backend.services.hermes_db import hermes_execute
                hermes_execute(
                    "UPDATE strategy_genesis_candidates SET paper_status='promoted_live' WHERE id=?",
                    (int(candidate_id),),
                )
            logger.info("[RuntimeGovernor] Genesis 策略已晋升 live: %s", strategy_id)
            return True
        except Exception as exc:
            logger.error("[RuntimeGovernor] Genesis 晋升失败: %s", exc)
            return False

    # ── 阶段三：paper→live 人工确认闸门（真金零自动切换）──
    def has_pending_live_promote(self, strategy_id: str) -> bool:
        """该策略是否已有一个待确认的真金晋升 patch（避免每次扫描重复提交）。"""
        for p in self.list_pending():
            keys = p.get("keys") or {}
            if (
                keys.get("_patch_type") == "training_live_promote"
                and keys.get("strategy_id") == strategy_id
            ):
                return True
        return False

    def propose_live_promote(
        self,
        *,
        strategy_id: str,
        proposal_id: Optional[int],
        session_id: Optional[str],
        base_size: float,
        size_mult: float,
        gate2_details: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> TuningPatch:
        """把\"模拟盘达标\"的策略放入待人工确认队列。

        关键：真金晋升 **绝不自动批准**——即使 Paper 自动批准开关打开也不生效，
        必须由人在 `/runtime/approve/{patch_id}` 显式 approve 后才切真金小仓。
        """
        patch = TuningPatch(
            patch_id=str(uuid.uuid4())[:12],
            keys={
                "_patch_type": "training_live_promote",
                "strategy_id": strategy_id,
                "proposal_id": proposal_id,
                "session_id": session_id,
                "base_size": float(base_size),
                "size_mult": float(size_mult),
                "gate2": gate2_details or {},
            },
            reason=reason or f"Gate2 通过，待人工确认真金晋升 {strategy_id}",
        )
        path = os.path.join(PENDING_DIR, f"{patch.patch_id}.json")
        os.makedirs(PENDING_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(patch.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(
            "[RuntimeGovernor] 真金晋升待人工确认: patch=%s strategy=%s (不自动批准)",
            patch.patch_id, strategy_id,
        )
        return patch

    @staticmethod
    def _apply_promotion_gate_patch(keys: Dict[str, Any]) -> bool:
        """晋升门 patch 批准 → 更新 registry 阶段。"""
        cid = keys.get("candidate_id")
        to_stage = keys.get("to_stage")
        if not cid or not to_stage:
            return False
        try:
            from backend.services.promotion_scan_service import apply_promotion_stage
            return apply_promotion_stage(
                str(cid),
                str(to_stage),
                domain=str(keys.get("domain") or "factor_weighting"),
                dsr=keys.get("dsr"),
                patch_id=keys.get("patch_id"),
            )
        except Exception as exc:
            logger.error("[RuntimeGovernor] promotion_gate 执行失败: %s", exc)
            return False

    @staticmethod
    def _apply_live_promote(keys: Dict[str, Any]) -> bool:
        """人工 approve 后真正执行 paper→live 切换（此处才动真金）。"""
        strategy_id = keys.get("strategy_id")
        if not strategy_id:
            return False
        proposal_id = keys.get("proposal_id")
        session_id = keys.get("session_id")
        base_size = float(keys.get("base_size") or 0.2)
        size_mult = float(keys.get("size_mult") or 0.1)
        try:
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import AIStrategy, FullAutoSession
            from backend.services.opencode_proposal_applier import apply_proposal
            from backend.services.training_phase_service import dequeue_graduation
            from backend.services.training_audit import log_live_event

            db = SessionLocal()
            try:
                strat = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                ).first()
                if not strat:
                    logger.warning("[RuntimeGovernor] live_promote 策略不存在: %s", strategy_id)
                    return False

                genome = dict(strat.genome or {})
                genome["live_stage"] = "probe"
                genome["live_probe_base_size"] = base_size
                genome["live_promoted_at"] = time.time()
                genome["live_promote_confirmed_by"] = "manual_governor"
                strat.max_position_size = base_size * size_mult
                strat.genome = genome

                if proposal_id:
                    try:
                        apply_proposal(db, int(proposal_id), to_live=True, auto_promoted=False)
                    except Exception as err:
                        logger.warning("[RuntimeGovernor] apply proposal %s: %s", proposal_id, err)

                if session_id:
                    sess = db.query(FullAutoSession).filter(
                        FullAutoSession.session_id == session_id
                    ).first()
                    if sess:
                        sess.trading_mode = "live"

                sqlite_write_commit(db)
                try:
                    dequeue_graduation(strategy_id)
                except Exception:
                    pass
                log_live_event(
                    "promote_l0_probe_manual",
                    strategy_id=strategy_id,
                    proposal_id=proposal_id,
                    size_mult=size_mult,
                )
            finally:
                db.close()
            logger.info("[RuntimeGovernor] 真金晋升已人工确认执行: %s", strategy_id)
            return True
        except Exception as exc:
            logger.error("[RuntimeGovernor] live_promote 执行失败 %s: %s", strategy_id, exc)
            return False

    def get_ownership_map(self) -> Dict[str, str]:
        """参数所有权：runtime_tuning vs hermes_prompt vs opencode。"""
        return {
            "runtime_tuning": "RuntimeGovernor",
            "hermes_l2_prompt": "Hermes+Governor",
            "strategy_prompt": "Hermes L2 (task-level)",
            "opencode_proposal": "OpenCode+Governor",
            "hermes_genesis": "Hermes L4+Governor",
        }

    def reject(self, patch_id: str) -> bool:
        pending_path = os.path.join(PENDING_DIR, f"{patch_id}.json")
        if os.path.isfile(pending_path):
            os.remove(pending_path)
            return True
        return False

    def set_session_overlay(self, session_id: str, keys: Dict[str, Any]) -> str:
        """Paper A/B：session 级 overlay。"""
        oid = f"session_{session_id}"
        path = os.path.join(OVERLAY_DIR, f"{oid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"overlay_id": oid, "keys": keys, "ts": time.time()}, f)
        return oid

    def get_session_overlay(self, session_id: str) -> Dict[str, Any]:
        path = os.path.join(OVERLAY_DIR, f"session_{session_id}.json")
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("keys") or {}
        except Exception:
            return {}


runtime_governor = RuntimeGovernor()
