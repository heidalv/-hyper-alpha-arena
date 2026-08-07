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
    if db is None:
        return
    try:
        from backend.services.mlto.db_models import MltoThesisEvent
        db.add(
            MltoThesisEvent(
                thesis_id=thesis_id,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False)[:8000],
            )
        )
        db.commit()
    except Exception as exc:
        logger.debug("[MLTO] audit event skip: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


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
        from backend.services.mlto.db_models import MltoThesis
        # [中长线合并修复] 独立短连接落库：thesis 的 LLM 调用（30-90s）期间
        # 传入的连接可能被 PostgreSQL 掐断（server closed the connection
        # unexpectedly），复用死连接 commit 必然失败。这里无视传入 db，
        # 每次用全新连接按 thesis_id 写入，保证 thesis/mid_view 稳定落库。
        with _ASL() as _db:
            row = _db.query(MltoThesis).filter(MltoThesis.thesis_id == thesis.thesis_id).first()
            if not row:
                row = MltoThesis(thesis_id=thesis.thesis_id)
                _db.add(row)
            row.session_id = thesis.session_id
            row.symbol = thesis.symbol
            row.tier = thesis.tier
            row.direction = thesis.direction
            row.thesis_summary = thesis.thesis_summary
            # [add] 持久化 reasoning 模型完整思维链（列由 _ensure_columns_safe 幂等补齐）。
            # 若旧库尚未补列，setAttribute 失败会被外层 except 兜住，不影响其它字段。
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
            # [v6 S2-7] regime 参数建议通道落库（校验后 applied dict）；
            # 列类型 JSON(→PG JSONB)，直接赋 dict 由 ORM 序列化；
            # 旧库未补列时 hasattr 守卫跳过。
            if hasattr(row.__class__, "regime_suggestion_json"):
                row.regime_suggestion_json = (
                    dict(thesis.regime_suggestion) if thesis.regime_suggestion else None
                )
            # [v6 阶段2 审计项7] LLM exit_plan 止损参数直通落库（0017 加列）；
            # >0 才写（0.0=本轮未提供，不覆盖历史有效值）；旧库未补列时守卫跳过。
            if hasattr(row.__class__, "sl_pct"):
                if float(thesis.sl_pct or 0) > 0:
                    row.sl_pct = thesis.sl_pct
                # else: 保留 DB 历史值（DTO 0.0 表示本轮未提供，不覆盖）
            if hasattr(row.__class__, "tp_pct"):
                if float(thesis.tp_pct or 0) > 0:
                    row.tp_pct = thesis.tp_pct
            # [v6 4.2] 注入的回测智慧 id 落库（列由 main._ensure_columns_safe 幂等补齐）
            if hasattr(row.__class__, "wisdom_ids_json") and thesis.wisdom_ids:
                row.wisdom_ids_json = json.dumps(thesis.wisdom_ids, ensure_ascii=False)
            # [阶段2] mid_view 以 JSONB/TEXT 持久化；列由 0009 迁移幂等补齐。
            # 旧库未补列时 hasattr 守卫跳过，不影响其它字段。
            # [中长线合并修复] 直接赋 dict（JSON→PG JSONB 类型自动序列化），
            # 弃用 cast(json_str, JSONB)：真实 PG 上会生成 CAST(%s::JSONB AS JSONB)
            # + Jsonb 包装参数，INSERT 整句失败被 except 吞掉导致落库静默丢失。
            if hasattr(row.__class__, "mid_view_json"):
                row.mid_view_json = (
                    thesis.mid_view.to_dict() if thesis.mid_view else None
                )
            _db.commit()
    except Exception as exc:
        logger.debug("[MLTO] thesis persist skip: %s", exc)
        try:
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
    except Exception:
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
