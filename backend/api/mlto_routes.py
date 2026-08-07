"""MLTO API routes."""

from __future__ import annotations



from fastapi import APIRouter, Depends, Query

from sqlalchemy.orm import Session



try:

    from backend.database.connection import AnalyticsSessionLocal, get_db

except ImportError:

    from database.connection import AnalyticsSessionLocal, get_db



router = APIRouter(prefix="/api/mlto", tags=["mlto"])





def _analytics_db():

    db = AnalyticsSessionLocal()

    try:

        yield db

    finally:

        db.close()





def _parse_symbols(symbols: str) -> list:
    return [x.strip().upper().replace("/USDT", "").replace("USDT", "") for x in (symbols or "").split(",") if x.strip()]


def _resolve_session_symbols(session_id: str, symbols_param: str) -> list:
    """合并 API 参数 + FullAuto 会话 symbols（去重）。"""
    out: list = []
    seen: set = set()
    for sym in _parse_symbols(symbols_param):
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession

        db = SessionLocal()
        try:
            row = db.query(FullAutoSession).filter(FullAutoSession.session_id == session_id).first()
            if row and row.symbols:
                for sym in row.symbols:
                    base = str(sym).upper().replace("USDT", "").strip()
                    if base and base not in seen:
                        seen.add(base)
                        out.append(base)
        finally:
            db.close()
    except Exception:
        pass
    return out






def _load_session_market_sym(session_id: str, symbol: str) -> tuple[dict, dict, float]:
    """从 FullAutoSession.last_market_summary 取单币行情（供 gate_status 展示用）。

    历史 bug：_enrich_thesis_row / thesis_detail 把 market_summary_sym={} 硬编码，
    UI 永久显示「数据缺失: market_summary empty」（截断成 market_），误导为 K 线没传。
    """
    sym_u = (symbol or "").strip().upper()
    if not session_id or not sym_u:
        return {}, {}, 0.0
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession

        db = SessionLocal()
        try:
            row = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == session_id
            ).first()
            ms_all = getattr(row, "last_market_summary", None) if row else None
            if not isinstance(ms_all, dict):
                return {}, {}, 0.0
            ms = ms_all.get(sym_u) or ms_all.get(symbol) or {}
            if not isinstance(ms, dict):
                return {}, {}, 0.0
            orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
            price = float(ms.get("current_price") or ms.get("price") or 0.0)
            return ms, orch, price
        finally:
            db.close()
    except Exception:
        return {}, {}, 0.0


def _placeholder_thesis(symbol: str, tier: str) -> dict:

    return {

        "thesis_id": "",

        "symbol": symbol.upper(),

        "tier": tier,

        "direction": "neutral",

        "thesis_summary": "",

        "llm_conviction": 0,

        "hub_adjusted": 0,

        "open_readiness": 0,

        "review_count": 0,

        "tranche_stage": 0,

        "pending": True,

        "gate_status": {

            "summary": "尚未运行 MLTO tick（等待编排器调度 long）",

            "can_open": False,

            "checks": [],

        },

    }





def _enrich_thesis_row(row: dict, session_id: str, db) -> dict:

    from backend.services.mlto import thesis_store

    from backend.services.mlto import open_gate

    from backend.services.mlto.types import HubDecision, PerceptionPacket



    t_dict = dict(row)

    try:

        t = thesis_store.get_by_id(row.get("thesis_id", ""), db=db)

        if t:

            thr = 0.70 if t.tier == "mid" else 0.75

            action = "BUILD" if t.hub_adjusted >= thr else "NIBBLE" if t.hub_adjusted >= 0.55 else "WAIT"

            hub = HubDecision(

                action=action,

                direction=t.direction,

                composite=t.hub_composite,

                adjusted=t.hub_adjusted,

                consistency=t.consistency,

                open_readiness=t.open_readiness,

                reason_text="",

            )

            _ms, _orch, _price = _load_session_market_sym(session_id, t.symbol)

            pkt = PerceptionPacket(

                symbol=t.symbol,

                tier=t.tier,

                session_id=session_id,

                ts=0,

                price=_price,

                market_summary_sym=_ms,

                orchestrator=_orch,

                quant_brief={},

                analyst_reports={},

                pre_screener_passed=True,

            )

            t_dict["gate_status"] = open_gate.describe_gate_status(t, hub, pkt, {})

    except Exception:

        t_dict["gate_status"] = {"summary": "计算中", "can_open": False, "checks": []}

    return t_dict





@router.get("/sessions/{session_id}/thesis/summary")

def thesis_summary(

    session_id: str,

    symbols: str = Query("", description="逗号分隔，如 BTC,ETH"),

    db: Session = Depends(_analytics_db),

):

    from backend.services.mlto import thesis_store

    from backend.services.mlto.learning_bridge import get_learning_metrics



    items = thesis_store.list_session_theses(session_id, db=db)

    enriched = [_enrich_thesis_row(row, session_id, db) for row in items]

    # 中线已不走 MLTO（三层独立架构 2026-07-03）：默认从 thesis 汇总过滤掉 mid，
    # 避免前端显示中线"僵尸卡片"（重启前遗留、已停更的旧 mid thesis）。
    # 仅当 MIDLONG_MID_VIA_MLTO=True（中线回退到 MLTO 与长线复合）时才保留 mid。
    from backend.config.settings import MIDLONG_MID_VIA_MLTO as _MID_VIA_MLTO
    _mlto_tiers = ("mid", "long") if _MID_VIA_MLTO else ("long",)
    if not _MID_VIA_MLTO:
        enriched = [r for r in enriched if r.get("tier") != "mid"]

    # [2026-07-23 统一守卫] 替换 _auto_coin_set 反向排除法。
    # 原来：读 session.auto_coin_symbols 快照做"排除"——但 AI 选币动态轮换，
    # ORM session 对象的 auto_coin_symbols 属性可能 stale，导致已退役 AI 选币
    # 漏过过滤出现在前端。现在：统一用 is_long_allowed 正向白名单过滤长线 thesis。
    from backend.services.auto_coin_selector import is_long_allowed as _is_long_allowed
    enriched = [
        r for r in enriched
        if r.get("tier") != "long" or _is_long_allowed(r.get("symbol", ""), session_id, db=db)
    ]

    existing = {(r.get("symbol", "").upper(), r.get("tier")) for r in enriched}

    for sym in _resolve_session_symbols(session_id, symbols):
        for tier in _mlto_tiers:
            if tier == "long" and not _is_long_allowed(sym, session_id, db=db):
                continue
            if (sym, tier) not in existing:
                enriched.append(_placeholder_thesis(sym, tier))



    enriched.sort(key=lambda x: (0 if x.get("tier") == "mid" else 1, x.get("symbol", "")))

    metrics = get_learning_metrics(session_id, db)

    return {"session_id": session_id, "theses": enriched, "metrics": metrics}





@router.get("/sessions/{session_id}/thesis")

def thesis_detail(

    session_id: str,

    symbol: str = Query(...),

    tier: str = Query("mid"),

    db: Session = Depends(_analytics_db),

):

    from backend.services.mlto import thesis_store

    from backend.services.mlto import layered_memory

    from backend.services.mlto import open_gate

    from backend.services.mlto.types import HubDecision, PerceptionPacket



    t = thesis_store.get(session_id, symbol, tier, db=db)

    if not t:

        placeholder = _placeholder_thesis(symbol, tier)

        return {

            "thesis": placeholder,

            "gate_status": placeholder["gate_status"],

            "memory_events": [],

        }



    events = layered_memory.retrieve(t.thesis_id, tier, t.thesis_summary, k=20, db=db)

    thr = 0.75 if tier == "long" else 0.70

    hub = HubDecision(

        action="BUILD" if t.hub_adjusted >= thr else ("NIBBLE" if t.hub_adjusted >= 0.55 else "WAIT"),

        direction=t.direction,

        composite=t.hub_composite,

        adjusted=t.hub_adjusted,

        consistency=t.consistency,

        open_readiness=t.open_readiness,

        reason_text="",

    )

    _ms, _orch, _price = _load_session_market_sym(session_id, symbol)

    pkt = PerceptionPacket(

        symbol=symbol, tier=tier, session_id=session_id, ts=0, price=_price,

        market_summary_sym=_ms, orchestrator=_orch, quant_brief={}, analyst_reports={},

        pre_screener_passed=True,

    )

    gate_status = open_gate.describe_gate_status(t, hub, pkt, {})

    return {

        "thesis": t.to_dict(),

        "gate_status": gate_status,

        "memory_events": [

            {

                "event_id": e.event_id,

                "layer": e.layer,

                "source": e.source,

                "signal": e.signal,

                "summary": e.summary,

                "gamma": e.gamma,

            }

            for e in events

        ],

    }

