"""持仓本地刷新 — 从 monolith _refresh_positions_local 迁出（整改#8 Phase2）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class RefreshPositionsHost:
    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)


def build_refresh_positions_host(svc) -> RefreshPositionsHost:
    return RefreshPositionsHost(
        NATURE_TO_TIER_MAP=getattr(svc, "_NATURE_TO_TIER_MAP", {}) or {},
    )


def refresh_positions_local(
    db,
    account_id: int,
    positions_list: list,
    position_map: dict,
    symbol_positions: dict,
    host: RefreshPositionsHost,
    affected_symbol: str = None,
) -> tuple:
    from backend.services.paper_trading_engine import paper_engine
    from backend.database.models import AIStrategy as _AIStrategy

    fresh = paper_engine.get_positions(db, account_id) or []

    # 重新注入 trade_nature / timeframe_tier（与 _run_analyst_system 一致）
    pos_strat_ids = list({p.get("strategy_id") for p in fresh if p.get("strategy_id")})
    _meta_cache = {}
    if pos_strat_ids:
        _meta_strats = db.query(_AIStrategy).filter(
            _AIStrategy.strategy_id.in_(pos_strat_ids)
        ).all()
        _meta_cache = {s.strategy_id: {
            "timeframe_tier": getattr(s, "timeframe_tier", None) or host.NATURE_TO_TIER_MAP.get(
                (s.genome or {}).get("trade_nature", ""), "mid") if s.genome else "mid",
            "trade_nature": (s.genome or {}).get("trade_nature", "swing"),
        } for s in _meta_strats}
    from backend.services.strategy_analysis_context import enrich_positions_with_strategy_meta
    enrich_positions_with_strategy_meta(fresh, _meta_cache)

    if affected_symbol:
        # 增量刷新：只替换受影响 symbol 的条目
        keys_to_del = [k for k, v in position_map.items()
                       if v.get("symbol") == affected_symbol]
        for k in keys_to_del:
            del position_map[k]
        symbol_positions.pop(affected_symbol, None)
        for p in fresh:
            sym = p.get("symbol", "")
            if sym != affected_symbol:
                continue
            tier = p.get("timeframe_tier") or "mid"
            pos_key = f"{sym}_{tier}"
            position_map[pos_key] = p
            symbol_positions.setdefault(sym, []).append(p)
    else:
        position_map.clear()
        symbol_positions.clear()
        for p in fresh:
            sym = p.get("symbol", "")
            tier = p.get("timeframe_tier") or "mid"
            pos_key = f"{sym}_{tier}"
            position_map[pos_key] = p
            symbol_positions.setdefault(sym, []).append(p)

    positions_list.clear()
    positions_list.extend(fresh)

    # 重算敞口
    long_m = sum(float(p.get("margin", 0)) for p in fresh if p.get("side") == "long")
    short_m = sum(float(p.get("margin", 0)) for p in fresh if p.get("side") == "short")
    return long_m, short_m

    # ══════════════════════════════════════════════════
    #  公开 API
    # ══════════════════════════════════════════════════
