"""数据中心快特征采集 — 禁止主动打交易所目录。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_LIQUID_PREF = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT", "ATOM",
    "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA", "SEI", "AAVE", "UNI",
    "LTC", "FIL", "RENDER", "FET", "ONDO", "HYPE", "WIF", "TON", "ADA", "TRX",
)


def norm_sym(s: str) -> str:
    u = (s or "").upper().strip()
    for suf in ("USDT", "USD", "-PERP", "PERP"):
        if u.endswith(suf) and len(u) > len(suf):
            u = u[: -len(suf)]
    return u.replace("-", "").replace("/", "")


def load_dc_ticker_rows() -> Dict[str, Dict[str, Any]]:
    """合并 ticker poller + catalog + universe → {SYM: row}。"""
    by_sym: Dict[str, Dict[str, Any]] = {}

    def _upsert(
        sym: str,
        *,
        volume: float = 0.0,
        change: float = 0.0,
        price: float = 0.0,
        change_1h: float = 0.0,
        change_4h: float = 0.0,
        source: str = "",
    ) -> None:
        u = norm_sym(sym)
        if not u:
            return
        row = by_sym.get(u) or {
            "symbol": u,
            "volume_24h": 0.0,
            "change_24h": 0.0,
            "change_1h": 0.0,
            "change_4h": 0.0,
            "price": 0.0,
            "sources": [],
        }
        if volume and volume > float(row.get("volume_24h") or 0):
            row["volume_24h"] = float(volume)
        if change:
            row["change_24h"] = float(change)
        if change_1h:
            row["change_1h"] = float(change_1h)
        if change_4h:
            row["change_4h"] = float(change_4h)
        if price:
            row["price"] = float(price)
        if source and source not in row["sources"]:
            row["sources"].append(source)
        by_sym[u] = row

    catalog_set: Set[str] = set()

    # 1) ticker poller
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

        stats = asterdex_ticker_poller.get_all_stats() or {}
        for sym, st in stats.items():
            if not isinstance(st, dict):
                continue
            _upsert(
                sym,
                volume=float(st.get("quote_volume_24h") or st.get("volume_24h") or 0),
                change=float(st.get("change_24h") or st.get("percentage") or 0),
                change_1h=float(st.get("change_1h") or st.get("percentage_1h") or 0),
                change_4h=float(st.get("change_4h") or st.get("percentage_4h") or 0),
                price=float(st.get("price") or st.get("last") or 0),
                source="dc_ticker",
            )
        for sym, px in (asterdex_ticker_poller.get_all_prices() or {}).items():
            _upsert(sym, price=float(px or 0), source="dc_price")
    except Exception as e:
        logger.warning("[CoinRank] ticker poller unavailable: %s", e)

    # 2) catalog
    try:
        from backend.services.kline_sync_meta import list_catalog_symbols

        for ex in ("asterdex", "binance", "hyperliquid"):
            cats = list_catalog_symbols(ex, status="trading") or []
            if not cats:
                continue
            for sym in cats:
                u = norm_sym(sym)
                if u:
                    catalog_set.add(u)
                    if u not in by_sym:
                        _upsert(u, source=f"catalog:{ex}")
            break
    except Exception as e:
        logger.debug("[CoinRank] catalog: %s", e)

    # 3) universe
    try:
        from backend.services.alpha.universe_manager import universe_manager

        state = universe_manager.get_state()
        selected = getattr(state, "selected", None) or []
        for r in selected:
            sym = getattr(r, "symbol", None) or (r.get("symbol") if isinstance(r, dict) else None)
            adv = float(
                getattr(r, "adv_usd", 0)
                or (r.get("adv_usd") if isinstance(r, dict) else 0)
                or 0
            )
            score = float(
                getattr(r, "composite_score", 0)
                or (r.get("composite_score") if isinstance(r, dict) else 0)
                or 0
            )
            u = norm_sym(sym or "")
            if not u:
                continue
            _upsert(u, volume=adv, source="universe")
            if score:
                by_sym[u]["universe_score"] = score
    except Exception as e:
        logger.debug("[CoinRank] universe: %s", e)

    if not by_sym:
        logger.error("[CoinRank] 数据中心无行情，使用主流币兜底")
        for s in _LIQUID_PREF[:12]:
            _upsert(s, volume=1.0, source="emergency_fallback")

    return by_sym


def factor_soft(symbol: str) -> tuple[Optional[float], Dict[str, Any]]:
    """软因子匹配：与 auto_coin / VIP 看板共用 summarize_exposure。"""
    try:
        from backend.services.factor_engine.exposure_service import summarize_exposure

        return summarize_exposure(symbol, "15m", 200)
    except Exception as e:
        logger.debug("[CoinRank] factor_soft %s: %s", symbol, e)
        return None, {"reason": "error", "error": str(e)[:200], "top": [], "n": 0}


def list_universe_symbols(limit: int = 200) -> List[str]:
    rows = load_dc_ticker_rows()
    has_vol = any(float(r.get("volume_24h") or 0) > 0 for r in rows.values())
    if has_vol:
        ranked = sorted(rows.values(), key=lambda x: float(x.get("volume_24h") or 0), reverse=True)
    else:
        pref_idx = {s: i for i, s in enumerate(_LIQUID_PREF)}
        ranked = sorted(
            rows.values(),
            key=lambda x: pref_idx.get(x["symbol"], 999),
        )
    return [r["symbol"] for r in ranked[:limit]]
