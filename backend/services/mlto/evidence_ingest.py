"""Evidence ingest into layered memory."""
from __future__ import annotations

from typing import List

from backend.services.mlto import layered_memory
from backend.services.mlto.types import MemoryEventDTO, PerceptionPacket, ThesisDTO


def ingest_tick(packet: PerceptionPacket, thesis: ThesisDTO, db=None) -> List[MemoryEventDTO]:
    events: List[MemoryEventDTO] = []
    orch = packet.orchestrator or {}

    mb = orch.get("mid_bias")
    lb = orch.get("long_bias")
    if packet.tier == "mid" and mb:
        events.append(
            layered_memory.store_event(
                thesis, "shallow", "orch", "mid_bias",
                f"mid_bias={mb} conf={orch.get('mid_confidence', 0)}",
                {"orch": orch}, db=db,
            )
        )
    if packet.tier == "long" and lb:
        events.append(
            layered_memory.store_event(
                thesis, "intermediate", "orch", "long_bias",
                f"long_bias={lb} conf={orch.get('long_confidence', 0)}",
                {"orch": orch}, db=db,
            )
        )
    # [阶段2] 长线 thesis 也 ingest mid_bias：为嵌入的 mid_view 子分析提供中周期
    # 上下文。走浅层(shallow)+中周期衰减(2h)，让 mid 子分析拥有独立的短半衰期记忆，
    # 而不与长线长半衰期(6h)事件混在一起被稀释。
    if packet.tier == "long" and mb:
        events.append(
            layered_memory.store_event(
                thesis, "shallow", "orch", "mid_bias",
                f"mid_bias={mb} conf={orch.get('mid_confidence', 0)}",
                {"orch": orch}, db=db, decay_tier="mid",
            )
        )

    qb = packet.quant_brief or {}
    align = int(qb.get("alignment_score") or 0)
    events.append(
        layered_memory.store_event(
            thesis, "intermediate", "quant", "alignment",
            f"alignment={align}/15 avail={qb.get('evidence_available_ratio', 0)}",
            qb, base_importance=3.0 if align == 0 else 5.0, db=db,
        )
    )

    if not packet.pre_screener_passed:
        events.append(
            layered_memory.store_event(
                thesis, "shallow", "prescreen", "fail",
                packet.pre_screener_reason or "pre_screener fail",
                db=db,
            )
        )

    if packet.slot_action:
        events.append(
            layered_memory.store_event(
                thesis, "shallow", "orch", "slot_action",
                f"slot={packet.slot_action}",
                db=db,
            )
        )

    sym = packet.symbol.upper()
    tier_key = "mid" if packet.tier == "mid" else "long"
    slices_root = (packet.analyst_reports or {}).get("_symbol_tier_slices") or {}
    sym_slices = (slices_root.get(sym) or {}).get(tier_key) or []

    if sym_slices:
        for item in sym_slices:
            if not isinstance(item, dict):
                continue
            events.append(
                layered_memory.store_event(
                    thesis, "intermediate", "analyst",
                    str(item.get("signal") or item.get("analyst") or "analyst"),
                    (item.get("detail") or item.get("summary") or "")[:200],
                    item, db=db,
                )
            )
    else:
        for _analyst, rep in (packet.analyst_reports or {}).items():
            if _analyst.startswith("_") or not isinstance(rep, dict):
                continue
            for sig in rep.get("signals") or []:
                if not isinstance(sig, dict):
                    continue
                if str(sig.get("symbol", "")).upper() != sym:
                    continue
                events.append(
                    layered_memory.store_event(
                        thesis, "intermediate", "analyst", str(sig.get("signal") or _analyst),
                        (sig.get("detail") or rep.get("summary") or "")[:200],
                        sig, db=db,
                    )
                )

    try:
        from backend.services.strategic_analyst.strategic_memory import StrategicMemorySystem
        sms = StrategicMemorySystem()
        ctx = f"{packet.symbol} {packet.tier} midlong"
        for mem in sms.retrieve_relevant(ctx, top_k=2):
            lesson = getattr(mem, "lesson", "") or getattr(mem, "observation", "")
            if lesson:
                events.append(
                    layered_memory.store_event(
                        thesis, "deep", "learning", "strategic_memory",
                        str(lesson)[:200],
                        {"memory_type": getattr(mem, "memory_type", "")},
                        base_importance=6.0, db=db,
                    )
                )
    except Exception:
        pass

    return events


def build_regime_hash(ms: dict) -> str:
    if not isinstance(ms, dict):
        return "unknown"
    orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
    phase = ms.get("market_cycle") or ms.get("regime") or "unknown"
    fg = ms.get("fear_greed") or (ms.get("onchain_macro") or {}).get("fear_greed") or 50
    try:
        fg_bucket = int(float(fg) // 20)
    except Exception:
        fg_bucket = 2
    return f"{phase}:{orch.get('macro_direction_constraint', '')}:{fg_bucket}"
