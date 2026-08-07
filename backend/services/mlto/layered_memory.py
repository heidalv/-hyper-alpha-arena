"""FinMem-style layered memory with gamma retrieval."""
from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from backend.services.mlto.types import MemoryEventDTO, ThesisDTO

logger = logging.getLogger(__name__)

LAYER_Q_HOURS = {
    "mid": {"shallow": 2.0, "intermediate": 48.0, "deep": 336.0},
    "long": {"shallow": 6.0, "intermediate": 168.0, "deep": 720.0},
}

LAYER_ALPHA = {"shallow": 0.90, "intermediate": 0.967, "deep": 0.988}

# In-process cache per thesis_id
_CACHE: dict = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def keyword_overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta = set(re.findall(r"[\w\u4e00-\u9fff]+", a.lower()))
    tb = set(re.findall(r"[\w\u4e00-\u9fff]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def compute_gamma(
    event_ts: datetime,
    query_ts: datetime,
    layer: str,
    tier: str,
    summary: str,
    context: str,
    base_importance: float = 5.0,
) -> tuple:
    delta_h = max(0.0, (query_ts - event_ts).total_seconds() / 3600.0)
    delta_d = delta_h / 24.0
    Q = LAYER_Q_HOURS.get(tier, LAYER_Q_HOURS["mid"]).get(layer, 48.0)
    s_recency = math.exp(-delta_h / max(Q, 0.1))
    s_relevancy = keyword_overlap(summary, context)
    alpha = LAYER_ALPHA.get(layer, 0.95)
    s_importance = min(1.0, base_importance * (alpha ** delta_d) / 10.0)
    gamma = min(1.0, s_recency) + s_relevancy + s_importance
    return s_recency, s_relevancy, s_importance, gamma


def store_event(
    thesis: ThesisDTO,
    layer: str,
    source: str,
    signal: str,
    summary: str,
    raw_payload: Optional[dict] = None,
    base_importance: float = 5.0,
    db=None,
    decay_tier: Optional[str] = None,
) -> MemoryEventDTO:
    event_id = str(uuid.uuid4())
    now = _utcnow()
    ctx = thesis.thesis_summary or thesis.symbol
    # [阶段2] 衰减层级：显式 decay_tier 优先（用于长线 thesis 里 mid 子分析事件，
    # 使其走中周期 2h 浅层衰减），否则回退到 thesis.tier（现状）。
    eff_tier = decay_tier or thesis.tier
    if eff_tier not in LAYER_Q_HOURS:
        eff_tier = thesis.tier
    s_r, s_rel, s_imp, gamma = compute_gamma(
        now, now, layer, eff_tier, summary, ctx, base_importance
    )
    dto = MemoryEventDTO(
        event_id=event_id,
        thesis_id=thesis.thesis_id,
        layer=layer,
        source=source,
        signal=signal,
        summary=summary[:500],
        gamma=gamma,
        ts=now,
        raw_payload=raw_payload or {},
        decay_tier=decay_tier,
    )
    _cache_events(thesis.thesis_id).append(dto)
    if db is not None:
        _persist_event(db, dto, s_r, s_rel, s_imp)
    return dto


def retrieve(thesis_id: str, tier: str, context: str = "", k: int = 8, db=None) -> List[MemoryEventDTO]:
    events = list(_cache_events(thesis_id))
    if db is not None and not events:
        events = _load_events(db, thesis_id)
        _CACHE[thesis_id] = events
    now = _utcnow()
    scored = []
    for e in events:
        ts = e.ts or now
        # [阶段2] 单事件衰减层级：优先用事件自带的 decay_tier（mid 子分析），
        # 否则用 retrieve 入参的 tier（thesis 级）。
        eff_tier = e.decay_tier or tier
        if eff_tier not in LAYER_Q_HOURS:
            eff_tier = tier
        _, _, _, gamma = compute_gamma(ts, now, e.layer, eff_tier, e.summary, context)
        e.gamma = gamma
        scored.append(e)
    scored.sort(key=lambda x: x.gamma, reverse=True)
    by_layer: dict = {"shallow": [], "intermediate": [], "deep": []}
    for e in scored:
        by_layer.setdefault(e.layer, []).append(e)
    out: List[MemoryEventDTO] = []
    for layer in ("shallow", "intermediate", "deep"):
        out.extend(by_layer.get(layer, [])[:2])
    out.sort(key=lambda x: x.gamma, reverse=True)
    return out[:k]


def format_for_prompt(events: List[MemoryEventDTO]) -> str:
    if not events:
        return "（暂无记忆事件）"
    lines = []
    for e in events:
        lines.append(
            f"- [{e.event_id}] layer={e.layer} src={e.source} γ={e.gamma:.2f} | {e.summary[:120]}"
        )
    return "\n".join(lines)


def _cache_events(thesis_id: str) -> List[MemoryEventDTO]:
    if thesis_id not in _CACHE:
        _CACHE[thesis_id] = []
    return _CACHE[thesis_id]


def _persist_event(db, dto: MemoryEventDTO, s_r, s_rel, s_imp):
    try:
        from backend.services.mlto.db_models import MltoMemoryEvent
        row = MltoMemoryEvent(
            event_id=dto.event_id,
            thesis_id=dto.thesis_id,
            layer=dto.layer,
            source=dto.source,
            signal=dto.signal,
            summary=dto.summary,
            raw_payload_json=json.dumps(dto.raw_payload, ensure_ascii=False)[:4000],
            recency_score=s_r,
            relevancy_score=s_rel,
            importance_score=s_imp,
            gamma=dto.gamma,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        logger.debug("[MLTO] memory persist skip: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def _load_events(db, thesis_id: str) -> List[MemoryEventDTO]:
    try:
        from backend.services.mlto.db_models import MltoMemoryEvent
        rows = (
            db.query(MltoMemoryEvent)
            .filter(MltoMemoryEvent.thesis_id == thesis_id)
            .order_by(MltoMemoryEvent.gamma.desc())
            .limit(50)
            .all()
        )
        out = []
        for r in rows:
            out.append(
                MemoryEventDTO(
                    event_id=r.event_id,
                    thesis_id=r.thesis_id,
                    layer=r.layer,
                    source=r.source,
                    signal=r.signal,
                    summary=r.summary or "",
                    gamma=float(r.gamma or 0),
                    ts=r.ts,
                    # [阶段2] 若 DB 有 decay_tier 列则读回（幂等容错）。
                    decay_tier=getattr(r, "decay_tier", None),
                )
            )
        return out
    except Exception:
        return []
