"""MLTO learning bridge — OWM + post-close."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OWM_SOURCES = (
    "orch", "quant", "analyst", "llm", "learning", "prescreen", "regime",
)

# ─────────────────────────────────────────────────────────────────────
# [阶段3f] OWM delta 相对化（决策4 + 计划 §5.3）
# 旧:绝对 ±0.02/±0.03。阶段3a 把 llm_qual 基础权重 0.04→0.30 后,
#   绝对 ±0.02 对 llm 是 ~6.7% 相对摆动,对 orch(0.12)却是 ~16.7%,
#   对 feedback_loop(0.03)更是 ~67%——相对影响差 13 倍,小权重源被
#   过度调整,OWM 失调。
# 新:delta = 基础权重 × 5%(赢)/−5%(输),invalidation 额外 −10%。
#   反馈与源的基础权重成正比,无论 0.03 还是 0.30 都是 5% 调整。
# OWM 乘子在 hub 仍 clamp 到 [0.5, 1.5](见 decision_hub.fuse_signals)。
# ─────────────────────────────────────────────────────────────────────
_OWM_DELTA_PCT = 0.05              # ±5% of the source's base weight
_OWM_INVALIDATION_PENALTY_PCT = 0.10  # invalidation 退出额外 -10% of base
_OWM_DEFAULT_BASE_WEIGHT = 0.1     # 兜底(prescreen/regime 无对应信号)

# OWM 源(=MltoMemoryEvent.source)→ decision_hub 中的信号名映射。
# 注意:OWM 源命名空间(orch/quant/...)与 Signal.source(framework/llm/...)
# 不同;这里把 OWM 源映射到它代表的信号的基础权重。
_OWM_SOURCE_TO_SIGNAL_LONG = {
    "orch": "orch_long_bias",
    "quant": "quant_alignment",
    "analyst": "analyst_consensus",
    "llm": "llm_qual",
    "learning": "feedback_loop",
}
_OWM_SOURCE_TO_SIGNAL_MID = {
    "orch": "orch_mid_bias",
    "quant": "quant_alignment",
    "analyst": "analyst_consensus",
    "llm": "llm_qual",
    "learning": "feedback_loop",
}


def _base_weight_for_source(source: str, tier: str) -> float:
    """查 decision_hub.WEIGHTS_LONG/MID 得到该 OWM 源对应信号的基础权重。"""
    try:
        from backend.services.mlto.decision_hub import WEIGHTS_LONG, WEIGHTS_MID
        if tier == "long":
            table = WEIGHTS_LONG
            name_map = _OWM_SOURCE_TO_SIGNAL_LONG
        else:
            table = WEIGHTS_MID
            name_map = _OWM_SOURCE_TO_SIGNAL_MID
        sig_name = name_map.get(source)
        if sig_name and sig_name in table:
            return float(table[sig_name])
    except Exception:
        pass
    return _OWM_DEFAULT_BASE_WEIGHT


def record_outcome(db, outcome, analytics_db=None) -> None:
    meta = outcome.metadata if isinstance(outcome.metadata, dict) else {}
    thesis_id = meta.get("thesis_id")
    if not thesis_id:
        return
    cited = meta.get("memory_event_ids") or []
    pnl = float(outcome.pnl or 0)
    session_id = meta.get("session_id") or ""
    tier = meta.get("tier") or outcome.tier or "mid"

    _bump_owm(db, session_id, tier, cited, pnl, meta, analytics_db)
    try:
        from backend.services.mlto import thesis_store
        thesis_store.append_event(
            thesis_id,
            "postmortem",
            {
                "pnl": pnl,
                "close_reason": meta.get("close_reason") or outcome.exit_channel,
                "hub_at_entry": meta.get("hub_adjusted_at_entry"),
            },
            db=analytics_db,
        )
    except Exception as exc:
        logger.debug("[MLTO learning] postmortem skip: %s", exc)


def _bump_owm(db, session_id, tier, cited_ids, pnl, meta, analytics_db):
    adb = analytics_db or db
    if adb is None:
        return
    try:
        from backend.services.mlto.db_models import MltoMemoryEvent, MltoSignalWeight
        sources: List[str] = []
        if cited_ids:
            rows = adb.query(MltoMemoryEvent).filter(MltoMemoryEvent.event_id.in_(list(cited_ids)[:20])).all()
            sources = list({r.source for r in rows if r.source})
        if not sources:
            sources = ["llm"]
        close_reason = (meta.get("close_reason") or "")
        is_invalidation = "invalidation" in close_reason
        for src in sources:
            base_w = _base_weight_for_source(src, tier)
            delta = base_w * _OWM_DELTA_PCT if pnl > 0 else -(base_w * _OWM_DELTA_PCT)
            if is_invalidation:
                delta -= base_w * _OWM_INVALIDATION_PENALTY_PCT
            row = (
                adb.query(MltoSignalWeight)
                .filter(
                    MltoSignalWeight.session_id == session_id,
                    MltoSignalWeight.tier == tier,
                    MltoSignalWeight.source == src,
                )
                .first()
            )
            if not row:
                row = MltoSignalWeight(session_id=session_id, tier=tier, source=src, weight=1.0)
                adb.add(row)
            row.weight = max(0.5, min(1.5, float(row.weight or 1) + delta))
            if pnl > 0:
                row.win_count = int(row.win_count or 0) + 1
            else:
                row.loss_count = int(row.loss_count or 0) + 1
        adb.commit()
    except Exception as exc:
        logger.debug("[MLTO OWM] skip: %s", exc)
        try:
            adb.rollback()
        except Exception:
            pass


def load_owm_weights(session_id: str, tier: str, db) -> Dict[str, float]:
    if db is None:
        return {}
    try:
        from backend.services.mlto.db_models import MltoSignalWeight
        rows = (
            db.query(MltoSignalWeight)
            .filter(MltoSignalWeight.session_id == session_id, MltoSignalWeight.tier == tier)
            .all()
        )
        return {r.source: float(r.weight or 1.0) for r in rows}
    except Exception:
        return {}


def get_learning_metrics(session_id: str, db) -> Dict[str, Any]:
    """Thesis hit rate / premature open / source contribution."""
    out = {
        "thesis_hit_rate": None,
        "premature_open_rate": None,
        "evidence_source_contribution": {},
        "thesis_drift_resets": 0,
        "sample_count": 0,
    }
    if db is None:
        return out
    try:
        from backend.services.mlto.db_models import MltoSignalWeight, MltoThesisEvent
        resets = (
            db.query(MltoThesisEvent)
            .filter(MltoThesisEvent.event_type.in_(("regime_reset", "macro_phase_shift")))
            .count()
        )
        out["thesis_drift_resets"] = resets

        import json as _json
        postmortems = (
            db.query(MltoThesisEvent)
            .filter(MltoThesisEvent.event_type == "postmortem")
            .all()
        )
        premature = 0
        closed = 0
        for ev in postmortems:
            try:
                payload = _json.loads(ev.payload_json or "{}")
            except Exception:
                payload = {}
            closed += 1
            reason = str(payload.get("close_reason") or "").lower()
            if "master" in reason and float(payload.get("pnl") or 0) < 0:
                premature += 1
        if closed >= 3:
            out["premature_open_rate"] = round(premature / closed, 3)

        rows = db.query(MltoSignalWeight).filter(MltoSignalWeight.session_id == session_id).all()
        if not rows and session_id == "":
            rows = db.query(MltoSignalWeight).all()
        total_w = sum(r.win_count or 0 for r in rows)
        total_l = sum(loss_count if (loss_count := r.loss_count) else 0 for r in rows)
        out["sample_count"] = total_w + total_l
        if total_w + total_l >= 5:
            out["thesis_hit_rate"] = round(total_w / max(total_w + total_l, 1), 3)
        out["evidence_source_contribution"] = {
            r.source: {"weight": r.weight, "wins": r.win_count, "losses": r.loss_count}
            for r in rows
        }
    except Exception:
        pass
    return out
