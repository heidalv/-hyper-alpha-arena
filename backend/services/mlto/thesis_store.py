"""Thesis persistence + audit events."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.services.mlto.types import HubDecision, MidViewDTO, QualUpdateResult, ThesisDTO

logger = logging.getLogger(__name__)

_THESIS_CACHE: Dict[str, ThesisDTO] = {}
# [B2-加固] get_or_create 的 check-then-act 必须原子：主循环/独立循环/analyst 多线程
# 同时 miss 缓存时若各建一个 ThesisDTO，后写覆盖先写，同一 symbol:tier 出现"换 thesis"
# 的假象。用锁把"查缓存→落库→写缓存"收敛为临界区。
_THESIS_LOCK = threading.RLock()


def _key(session_id: str, symbol: str, tier: str) -> str:
    return f"{session_id}:{symbol.upper()}:{tier}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_wisdom_ids(raw: Any) -> list:
    """从 wisdom_ids_json 列解析 id 列表（None/空/坏 JSON → []）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).isdigit()]
    except Exception:
        pass
    return []


def get(
    session_id: str,
    symbol: str,
    tier: str,
    db=None,
) -> Optional[ThesisDTO]:
    k = _key(session_id, symbol, tier)
    if k in _THESIS_CACHE:
        return _THESIS_CACHE[k]
    if db is not None:
        loaded = _load(db, session_id, symbol, tier)
        if loaded:
            _THESIS_CACHE[k] = loaded
            return loaded
    return None


def get_or_create(
    session_id: str,
    symbol: str,
    tier: str,
    regime_hash: str = "",
    db=None,
) -> ThesisDTO:
    k = _key(session_id, symbol, tier)
    # [B2-加固] 原子 check-then-create：先查缓存，再查库，miss 才创建并落库。
    # 与 _load 内的写缓存一并收进锁内，防止并发重复创建/覆盖。
    with _THESIS_LOCK:
        if k in _THESIS_CACHE:
            return _THESIS_CACHE[k]
        if db is not None:
            loaded = _load(db, session_id, symbol, tier)
            if loaded:
                _THESIS_CACHE[k] = loaded
                return loaded
        now = _utcnow()
        dto = ThesisDTO(
            thesis_id=str(uuid.uuid4()),
            session_id=session_id,
            symbol=symbol.upper(),
            tier=tier,
            stable_since=now,
            regime_hash=regime_hash,
            updated_at=now,
        )
        _THESIS_CACHE[k] = dto
        if db is not None:
            _persist(db, dto)
            # _persist 可能因唯一约束 adopt 库内规范 thesis_id；回读保证缓存一致
            loaded = _load(db, session_id, symbol, tier)
            if loaded:
                _THESIS_CACHE[k] = loaded
                return loaded
        return dto


def update_hub(thesis: ThesisDTO, hub: HubDecision, db=None) -> None:
    old_dir = thesis.direction
    thesis.hub_composite = hub.composite
    thesis.hub_adjusted = hub.adjusted
    thesis.consistency = hub.consistency
    thesis.open_readiness = hub.open_readiness
    thesis.direction = hub.direction
    thesis.updated_at = _utcnow()
    if old_dir != hub.direction:
        thesis.stable_since = _utcnow()
    if db is not None:
        _persist(db, thesis)


def apply_llm_update(thesis: ThesisDTO, qual: QualUpdateResult, db=None) -> None:
    # [P5-修复] review_count 只在 LLM 给出实质方向研判时 +1（direction 非 neutral
    # 或 conviction_delta≠0）；持续 neutral / 空响应（LLM 失败返回 {} 时
    # direction 默认 "neutral"）的“空转”轮次不再计数。此前无条件 +1 会把计数
    # 通胀到上万（BTC 11551），使 quant_layer 的“从未评级”判据失效。
    if (qual.direction or "").strip().lower() in ("long", "short") or int(qual.conviction_delta or 0) != 0:
        thesis.review_count += 1
    if qual.thesis_summary:
        thesis.thesis_summary = qual.thesis_summary[:500]
    # [add] 透传 reasoning 模型完整思维链（放宽到 6000 字，区别于精简的 thesis_summary）。
    if qual.reasoning_content:
        thesis.reasoning_content = qual.reasoning_content[:6000]
    if qual.direction in ("long", "short", "neutral"):
        # [fix 2026-07-01] 用 direction_history 多数票代替"方向变了就重置stable_since"。
        # 旧逻辑：LLM每30秒更新，方向频繁切换(long↔neutral)→每次重置stable_since
        # → stable永远0s<1800s门槛 → 中长线100%不成交（死锁）。
        # 新逻辑：记录最近5次LLM方向，open_gate 用多数票判定稳定性，不再依赖时间。
        thesis.direction_history.append(qual.direction)
        if len(thesis.direction_history) > 5:
            thesis.direction_history = thesis.direction_history[-5:]
        thesis.direction = qual.direction
    delta = max(-8, min(8, int(qual.conviction_delta or 0)))
    thesis.llm_conviction = max(0, min(100, thesis.llm_conviction + delta))
    if qual.invalidation:
        thesis.invalidation = qual.invalidation
    if qual.missing_evidence:
        thesis.missing_evidence = qual.missing_evidence[:10]
    # [阶段3b] 透传 LLM 的 recommend_open（open_gate 风险底线之一）。
    # None=未明确（放行）；False=明确拒绝；True=建议开。
    # [2026-07-31] 禁止 bool(None)→False，否则缺字段会被当成明确拒绝。
    if qual.thesis_summary or qual.direction:
        if qual.recommend_open is None and "recommend_open" not in (qual.raw or {}):
            # LLM 本轮未给该字段：保持 thesis 原值（或保持 None）
            pass
        else:
            thesis.recommend_open = qual.recommend_open
        # [Phase A 修复 Bug2] 透传 LLM 的 should_close（thesis 完全失效 → 主动离场）。
        thesis.should_close = bool(qual.should_close)
    # [阶段2] 从 LLM 输出更新中周期子视图。qual.mid_view 为 None 时不覆盖
    # （保留历史 mid_view；LLM 偶尔漏字段不应清空已有择时分析）。
    if qual.mid_view:
        mv = MidViewDTO.from_dict(qual.mid_view)
        if mv is not None:
            mv.updated_at = time.time()
            thesis.mid_view = mv
    # [2026-08-05 v6 6.3 第3项] LLM exit_plan 止损参数直通：仅在 LLM 本轮
    # 给出非零值（>0）时更新；漏字段（0.0）不覆盖历史有效值（向后兼容：
    # 从未提供的 thesis 保持 0.0 → 执行层走 structure_stops 兜底）。
    if float(getattr(qual, "sl_pct", 0) or 0) > 0:
        thesis.sl_pct = float(qual.sl_pct)
    if float(getattr(qual, "tp_pct", 0) or 0) > 0:
        thesis.tp_pct = float(qual.tp_pct)
    # [v6 S2-7] regime 参数建议通道：LLM 本轮给出 → 覆盖；缺省 → 保留历史（不覆盖）
    rs = getattr(qual, "regime_suggestion", None)
    if isinstance(rs, dict) and rs:
        thesis.regime_suggestion = dict(rs)
    thesis.updated_at = _utcnow()
    if db is not None:
        _persist(db, thesis)


def apply_regime_reset(thesis: ThesisDTO, new_hash: str, db=None) -> None:
    thesis.llm_conviction = int(thesis.llm_conviction * 0.5)
    thesis.hub_adjusted *= 0.5
    thesis.open_readiness = int(thesis.open_readiness * 0.5)
    thesis.regime_hash = new_hash
    thesis.direction = "neutral"
    thesis.stable_since = _utcnow()
    thesis.updated_at = _utcnow()
    append_event(thesis.thesis_id, "regime_reset", {"new_hash": new_hash}, db=db)
    if db is not None:
        _persist(db, thesis)


def append_event(
    thesis_id: str,
    event_type: str,
    payload: Dict[str, Any],
    db=None,
) -> None:
    # db 参数保留兼容；与 _persist 一样用独立短连接，避免 LLM 后传入连接已死导致事件丢失。
    if not thesis_id:
        return
    try:
        from backend.database.connection import AnalyticsSessionLocal as _ASL
        from backend.services.mlto.db_models import MltoThesisEvent
        with _ASL() as _db:
            _db.add(
                MltoThesisEvent(
                    thesis_id=thesis_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload, ensure_ascii=False)[:8000],
                )
            )
            _db.commit()
    except Exception as exc:
        logger.warning("[MLTO] audit event skip thesis=%s type=%s: %s", thesis_id, event_type, exc)


def list_session_theses(session_id: str, db=None) -> list:
    by_key: Dict[str, ThesisDTO] = {}
    for t in _THESIS_CACHE.values():
        if t.session_id == session_id:
            by_key[_key(session_id, t.symbol, t.tier)] = t
    if db is not None:
        try:
            from backend.services.mlto.db_models import MltoThesis
            rows = db.query(MltoThesis).filter(MltoThesis.session_id == session_id).all()
            for r in rows:
                dto = _row_to_dto(r)
                by_key[_key(session_id, dto.symbol, dto.tier)] = dto
                _THESIS_CACHE[_key(session_id, dto.symbol, dto.tier)] = dto
        except Exception:
            pass
    return [t.to_dict() for t in by_key.values()]


def get_by_id(thesis_id: str, db=None) -> Optional[ThesisDTO]:
    for t in _THESIS_CACHE.values():
        if t.thesis_id == thesis_id:
            return t
    if db is None:
        return None
    try:
        from backend.services.mlto.db_models import MltoThesis
        r = db.query(MltoThesis).filter(MltoThesis.thesis_id == thesis_id).first()
        return _row_to_dto(r) if r else None
    except Exception:
        return None


def _persist(db, thesis: ThesisDTO) -> None:
    try:
        from backend.database.connection import AnalyticsSessionLocal as _ASL
        from backend.services.mlto.db_models import MltoThesis, MltoThesisEvent
        # 独立短连接落库。主键对齐顺序：
        # 1) (session, symbol, tier) 唯一约束行（规范行）
        # 2) thesis_id
        # 3) 都不存在才 INSERT
        # 曾出现：缓存里换了新 UUID → INSERT 撞 uq_mlto_thesis_session_sym_tier
        # → 异常被 debug 吞掉 → BTC long 论点永远停在旧行，事件写成孤儿 thesis_id，
        # 前端 JOIN mlto_thesis 看不到「BTC 长线分析」。
        with _ASL() as _db:
            sym_u = str(thesis.symbol or "").upper()
            row = None
            try:
                if thesis.session_id and sym_u and thesis.tier:
                    row = (
                        _db.query(MltoThesis)
                        .filter(
                            MltoThesis.session_id == thesis.session_id,
                            MltoThesis.symbol == sym_u,
                            MltoThesis.tier == thesis.tier,
                        )
                        .first()
                    )
            except Exception as _sel_err:
                msg = str(_sel_err).lower()
                if "dataconrupted" in msg or "toast" in msg or "missing chunk" in msg:
                    logger.warning(
                        "[MLTO] persist SELECT toast corrupt %s %s, null reasoning: %s",
                        sym_u, thesis.tier, _sel_err,
                    )
                    try:
                        _db.rollback()
                        from sqlalchemy import text as _sa_text
                        _db.execute(
                            _sa_text(
                                "UPDATE mlto_thesis SET reasoning_snapshot = NULL "
                                "WHERE session_id = :sid AND symbol = :sym AND tier = :tier"
                            ),
                            {
                                "sid": thesis.session_id,
                                "sym": sym_u,
                                "tier": thesis.tier,
                            },
                        )
                        _db.commit()
                        row = (
                            _db.query(MltoThesis)
                            .filter(
                                MltoThesis.session_id == thesis.session_id,
                                MltoThesis.symbol == sym_u,
                                MltoThesis.tier == thesis.tier,
                            )
                            .first()
                        )
                    except Exception as _heal_err:
                        logger.warning("[MLTO] persist toast heal failed: %s", _heal_err)
                        try:
                            _db.rollback()
                        except Exception:
                            pass
                        row = None
                else:
                    raise
            if row is None and thesis.thesis_id:
                row = (
                    _db.query(MltoThesis)
                    .filter(MltoThesis.thesis_id == thesis.thesis_id)
                    .first()
                )
            orphan_id = None
            if row is None:
                row = MltoThesis(thesis_id=thesis.thesis_id)
                _db.add(row)
            elif row.thesis_id != thesis.thesis_id:
                orphan_id = thesis.thesis_id
                logger.warning(
                    "[MLTO] adopt canonical thesis_id %s → %s (%s %s)",
                    orphan_id, row.thesis_id, sym_u, thesis.tier,
                )
                thesis.thesis_id = row.thesis_id
            row.session_id = thesis.session_id
            row.symbol = sym_u
            row.tier = thesis.tier
            row.direction = thesis.direction
            row.thesis_summary = thesis.thesis_summary
            if hasattr(row.__class__, "reasoning_snapshot"):
                row.reasoning_snapshot = (thesis.reasoning_content or "")[:6000]
            row.llm_conviction = thesis.llm_conviction
            row.hub_composite = thesis.hub_composite
            row.hub_adjusted = thesis.hub_adjusted
            row.consistency = thesis.consistency
            row.open_readiness = thesis.open_readiness
            row.stable_since = thesis.stable_since
            row.review_count = thesis.review_count
            row.tranche_stage = thesis.tranche_stage
            row.regime_hash = thesis.regime_hash
            row.invalidation_json = json.dumps(thesis.invalidation, ensure_ascii=False)
            row.missing_evidence_json = json.dumps(thesis.missing_evidence, ensure_ascii=False)
            row.owm_weights_json = json.dumps(thesis.owm_weights, ensure_ascii=False)
            row.updated_at = thesis.updated_at or _utcnow()
            if hasattr(row.__class__, "regime_suggestion_json"):
                row.regime_suggestion_json = (
                    dict(thesis.regime_suggestion) if thesis.regime_suggestion else None
                )
            if hasattr(row.__class__, "sl_pct"):
                if float(thesis.sl_pct or 0) > 0:
                    row.sl_pct = thesis.sl_pct
            if hasattr(row.__class__, "tp_pct"):
                if float(thesis.tp_pct or 0) > 0:
                    row.tp_pct = thesis.tp_pct
            if hasattr(row.__class__, "wisdom_ids_json") and thesis.wisdom_ids:
                row.wisdom_ids_json = json.dumps(thesis.wisdom_ids, ensure_ascii=False)
            if hasattr(row.__class__, "mid_view_json"):
                row.mid_view_json = (
                    thesis.mid_view.to_dict() if thesis.mid_view else None
                )
            # 把孤儿事件挂回规范 thesis_id，否则活动流 JOIN 永远看不到
            if orphan_id and orphan_id != row.thesis_id:
                try:
                    _db.execute(
                        MltoThesisEvent.__table__.update()
                        .where(MltoThesisEvent.thesis_id == orphan_id)
                        .values(thesis_id=row.thesis_id)
                    )
                except Exception as _heal_err:
                    logger.debug("[MLTO] orphan event heal skip: %s", _heal_err)
            _db.commit()
    except Exception as exc:
        logger.warning(
            "[MLTO] thesis persist skip %s %s %s: %s",
            getattr(thesis, "symbol", "?"),
            getattr(thesis, "tier", "?"),
            getattr(thesis, "thesis_id", "?"),
            exc,
        )
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass


def _load(db, session_id, symbol, tier) -> Optional[ThesisDTO]:
    try:
        from backend.services.mlto.db_models import MltoThesis
        r = (
            db.query(MltoThesis)
            .filter(
                MltoThesis.session_id == session_id,
                MltoThesis.symbol == symbol.upper(),
                MltoThesis.tier == tier,
            )
            .first()
        )
        return _row_to_dto(r) if r else None
    except Exception as exc:
        # PG TOAST 损坏（常见于超大 reasoning_snapshot）会导致整行 ORM 读失败，
        # 进而 get_or_create 换新 UUID → 唯一约束撞车 → 长线论点「消失」。
        # 尝试清空损坏列后重读。
        msg = str(exc).lower()
        if "dataconrupted" in msg or "toast" in msg or "missing chunk" in msg:
            logger.warning(
                "[MLTO] thesis row TOAST corrupt %s %s %s, null reasoning_snapshot: %s",
                session_id, symbol, tier, exc,
            )
            try:
                from sqlalchemy import text as _sa_text
                db.rollback()
                db.execute(
                    _sa_text(
                        "UPDATE mlto_thesis SET reasoning_snapshot = NULL "
                        "WHERE session_id = :sid AND symbol = :sym AND tier = :tier"
                    ),
                    {"sid": session_id, "sym": str(symbol).upper(), "tier": tier},
                )
                db.commit()
                from backend.services.mlto.db_models import MltoThesis
                r = (
                    db.query(MltoThesis)
                    .filter(
                        MltoThesis.session_id == session_id,
                        MltoThesis.symbol == symbol.upper(),
                        MltoThesis.tier == tier,
                    )
                    .first()
                )
                return _row_to_dto(r) if r else None
            except Exception as heal_err:
                logger.warning("[MLTO] toast heal failed: %s", heal_err)
                try:
                    db.rollback()
                except Exception:
                    pass
        return None


def _row_to_dto(r) -> ThesisDTO:
    inv = {}
    miss = []
    owm = {}
    mid_view = None
    regime_suggestion = None
    try:
        if r.invalidation_json:
            inv = json.loads(r.invalidation_json)
        if r.missing_evidence_json:
            miss = json.loads(r.missing_evidence_json)
        if r.owm_weights_json:
            owm = json.loads(r.owm_weights_json)
        # [阶段2] 读回中周期子视图；getattr 容错旧库未补列的情况。
        # JSONB 列在 PG 下 ORM 已反序列化为 dict，SQLite TEXT 下是 str，兼容两者。
        mv_raw = getattr(r, "mid_view_json", None)
        if mv_raw:
            mid_view = MidViewDTO.from_dict(
                mv_raw if isinstance(mv_raw, dict) else json.loads(mv_raw)
            )
        # [v6 S2-7] 读回 regime 参数建议（校验后 applied dict）；
        # getattr 容错旧库未补列的情况；同样兼容 dict/str 两种形态。
        rs_raw = getattr(r, "regime_suggestion_json", None)
        if rs_raw:
            rs_parsed = rs_raw if isinstance(rs_raw, dict) else json.loads(rs_raw)
            if isinstance(rs_parsed, dict) and rs_parsed:
                regime_suggestion = rs_parsed
    except Exception:
        pass
    return ThesisDTO(
        thesis_id=r.thesis_id,
        session_id=r.session_id,
        symbol=r.symbol,
        tier=r.tier,
        direction=r.direction or "neutral",
        thesis_summary=r.thesis_summary or "",
        # [add] 读回思维链；getattr 容错旧库未补列的情况。
        reasoning_content=getattr(r, "reasoning_snapshot", "") or "",
        llm_conviction=int(r.llm_conviction or 0),
        hub_composite=float(r.hub_composite or 0),
        hub_adjusted=float(r.hub_adjusted or 0),
        consistency=float(r.consistency or 0),
        open_readiness=int(r.open_readiness or 0),
        stable_since=r.stable_since,
        review_count=int(r.review_count or 0),
        tranche_stage=int(r.tranche_stage or 0),
        regime_hash=r.regime_hash or "",
        invalidation=inv,
        missing_evidence=miss,
        owm_weights=owm,
        mid_view=mid_view,
        # [v6 阶段2 审计项7] 读回 LLM 止损参数；getattr 容错旧库未补列。
        sl_pct=float(getattr(r, "sl_pct", 0) or 0),
        tp_pct=float(getattr(r, "tp_pct", 0) or 0),
        regime_suggestion=regime_suggestion,
        # [v6 4.2] 读回注入的回测智慧 id；getattr 容错旧库未补列。
        wisdom_ids=_parse_wisdom_ids(getattr(r, "wisdom_ids_json", None)),
        updated_at=r.updated_at,
    )


def clear_cache() -> None:
    """清空进程内 thesis 缓存（用于重启后 DB 恢复验收）。"""
    _THESIS_CACHE.clear()


def reset_all_for_macro_phase(
    old_phase: str,
    new_phase: str,
    macro_symbol: str = "GLOBAL",
    db=None,
) -> int:
    """宏观 phase 切换：对所有活跃 thesis 执行 regime_reset。"""
    count = 0
    new_hash = f"macro_phase:{new_phase}:{macro_symbol}"
    seen: set = set()

    for dto in list(_THESIS_CACHE.values()):
        if dto.thesis_id in seen:
            continue
        seen.add(dto.thesis_id)
        apply_regime_reset(dto, new_hash, db=db)
        append_event(
            dto.thesis_id,
            "macro_phase_shift",
            {"old_phase": old_phase, "new_phase": new_phase, "symbol": macro_symbol},
            db=db,
        )
        count += 1

    if db is not None:
        try:
            from backend.services.mlto.db_models import MltoThesis
            rows = db.query(MltoThesis).all()
            for r in rows:
                if r.thesis_id in seen:
                    continue
                dto = _row_to_dto(r)
                _THESIS_CACHE[_key(dto.session_id, dto.symbol, dto.tier)] = dto
                apply_regime_reset(dto, new_hash, db=db)
                append_event(
                    dto.thesis_id,
                    "macro_phase_shift",
                    {"old_phase": old_phase, "new_phase": new_phase, "symbol": macro_symbol},
                    db=db,
                )
                seen.add(dto.thesis_id)
                count += 1
        except Exception as exc:
            logger.debug("[MLTO] macro phase reset db skip: %s", exc)
    return count
