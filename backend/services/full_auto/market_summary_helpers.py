"""市场摘要填充与价格补全 — 从 monolith 迁出。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MarketSummaryContext:
    """monolith 状态切片，供 bootstrap/ensure 使用。"""

    market_scan_cache: Dict[str, Any]
    last_unified_snapshot: Any = None
    bg_scan_running: bool = False
    start_bg_scan: Optional[Callable[[List[str]], None]] = None


def sanitize_market_summary_for_qaa(market_summary: dict) -> dict:
    """QAA workflow JSON 持久化前剔除 DataFrame 等不可序列化对象。"""
    if not market_summary:
        return {}
    try:
        from qaa.workflow.store.payload_sanitize import coerce_json_safe

        return coerce_json_safe(market_summary)
    except Exception:
        out = {}
        for sym, info in market_summary.items():
            if isinstance(info, dict):
                out[sym] = {
                    k: v
                    for k, v in info.items()
                    if isinstance(v, (str, int, float, bool, type(None), list, dict))
                }
            else:
                out[sym] = info
        return out


def annotate_auto_coin_meta(session_id: str, market_summary: dict) -> None:
    """给 market_summary 中的 AI 自动选币打标。"""
    try:
        from backend.services.auto_coin_selector import auto_coin_scheduler

        meta = auto_coin_scheduler.get_selection_meta(session_id)
        if not meta:
            return
        for sym, m in meta.items():
            info = market_summary.get(sym)
            if isinstance(info, dict):
                info["auto_coin_meta"] = m
    except Exception as e:
        logger.debug(f"[FullAuto] auto_coin_meta 标注跳过: {e}")


def bootstrap_market_summary(symbols: List[str], ctx: MarketSummaryContext) -> Dict[str, Any]:
    """健康检查开头填充市场概览：缓存 → 统一快照/价格池 → 实时价。"""
    market_summary: Dict[str, Any] = {}
    if not symbols:
        return market_summary

    for s in symbols:
        cached = ctx.market_scan_cache.get(s)
        if isinstance(cached, dict) and cached.get("current_price"):
            market_summary[s] = dict(cached)
            # 修复3：校验cache新鲜度——超300s的cache标stale
            _cache_ts = ctx.market_scan_cache_ts
            if _cache_ts and (time.time() - _cache_ts > 300):
                market_summary[s]["data_stale"] = True
                market_summary[s]["data_reliable"] = False

    missing = [s for s in symbols if not (market_summary.get(s) or {}).get("current_price")]

    snap = ctx.last_unified_snapshot
    if missing and snap and getattr(snap, "markets", None):
        for sym in list(missing):
            mkt = snap.markets.get(sym)
            if mkt and getattr(mkt, "price", 0) > 0:
                market_summary[sym] = {
                    "current_price": float(mkt.price),
                    "data_reliable": True,
                    "data_source": "unified_snapshot",
                    "price_source": "snapshot",
                    "funding_rate": getattr(mkt, "funding_rate", 0) or 0,
                    "volume_24h": getattr(mkt, "volume_24h", 0) or 0,
                }
        missing = [s for s in symbols if not (market_summary.get(s) or {}).get("current_price")]

    if missing:
        try:
            from backend.services.exchange_config import get_active_exchange

            _env = get_active_exchange()
            # 修复：asterdex 主所先用数据中心 ticker 内存快照，不再拿 HL 数据填充
            if (_env or "asterdex").strip().lower() in ("aster", "asterdex"):
                try:
                    from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                    for _sym in list(missing):
                        _st = asterdex_ticker_poller.get_stats(_sym)
                        if _st and _st.get("price"):
                            market_summary[_sym] = {
                                "current_price": float(_st["price"]),
                                "data_reliable": True,
                                "data_source": "dc_ticker",
                                "price_source": "data_center",
                                "funding_rate": 0.0,
                                "volume_24h": float(_st.get("quote_volume_24h") or 0),
                            }
                            missing = [s for s in missing if s != _sym]
                except Exception:
                    pass
            if (_env or "").strip().lower() == "hyperliquid":
                try:
                    # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连
                    # HL bulk ticker 兜底，统一从数据中心 DB 读价（下方 get_ticker_data
                    # 已受 DC_ONLY 保护）。
                    from backend.services.market_data import _dc_only_enabled
                    if _dc_only_enabled():
                        pass
                    else:
                        from backend.services.hyperliquid_market_data import get_bulk_ticker_data_from_hyperliquid

                        bulk = get_bulk_ticker_data_from_hyperliquid(missing, _env) or {}
                        for sym, tk in bulk.items():
                            price = float(tk.get("price", 0) or tk.get("last", 0) or 0)
                            if price > 0:
                                market_summary[sym] = {
                                    "current_price": price,
                                    "data_reliable": True,
                                    "data_source": "bulk_ticker",
                                    "price_source": "realtime",
                                    "funding_rate": float(tk.get("funding_rate", 0) or 0),
                                    "volume_24h": float(tk.get("volume_24h", 0) or 0),
                                }
                        missing = [s for s in symbols if not (market_summary.get(s) or {}).get("current_price")]
                except Exception:
                    pass
            if missing:
                from backend.services.market_data import get_ticker_data

                for sym in list(missing):
                    tk = get_ticker_data(sym, _env) or {}
                    price = float(tk.get("price", 0) or tk.get("last", 0) or 0)
                    if price > 0:
                        market_summary[sym] = {
                            "current_price": price,
                            "data_reliable": True,
                            "data_source": "ticker",
                            "price_source": "realtime",
                            "funding_rate": float(tk.get("funding_rate", 0) or 0),
                            "volume_24h": float(tk.get("volume_24h", 0) or 0),
                        }
                missing = [s for s in symbols if not (market_summary.get(s) or {}).get("current_price")]
        except Exception as e:
            logger.debug(f"[FullAuto] ticker 预热失败: {e}")

    if missing:
        try:
            from services.price_cache import get_cached_price
            from backend.services.exchange_config import get_active_exchange

            _env = get_active_exchange()
            for sym in missing:
                p = get_cached_price(sym, "CRYPTO", _env)
                if p and float(p) > 0:
                    market_summary[sym] = {
                        "current_price": float(p),
                        "data_reliable": True,
                        "data_source": "price_cache",
                        "price_source": "cache",
                    }
            missing = [s for s in symbols if not (market_summary.get(s) or {}).get("current_price")]
        except Exception as e:
            logger.debug(f"[FullAuto] price_cache 预热失败: {e}")

    for sym in missing:
        market_summary[sym] = {
            "current_price": 0,
            "data_reliable": False,
            "data_source": "pending",
            "error": "价格加载中，本轮回填",
        }

    if missing and not ctx.bg_scan_running and ctx.start_bg_scan:
        try:
            ctx.bg_scan_running = True
            threading.Thread(
                target=ctx.start_bg_scan,
                args=(list(symbols),),
                daemon=True,
            ).start()
        except Exception:
            ctx.bg_scan_running = False

    return market_summary


def ensure_market_prices(
    market_summary: Dict[str, Any],
    symbols: List[str],
    ctx: MarketSummaryContext,
) -> None:
    """补全 market_summary 中缺失的 current_price。"""
    if not market_summary or not symbols:
        return

    def _missing() -> List[str]:
        out: List[str] = []
        for sym in symbols:
            info = market_summary.get(sym)
            if not isinstance(info, dict):
                out.append(sym)
                continue
            if float(info.get("current_price") or 0) <= 0:
                out.append(sym)
        return out

    missing = _missing()
    if not missing:
        return

    try:
        from backend.services.price_cache import get_cached_price
        from backend.services.exchange_config import get_active_exchange

        _env = get_active_exchange()
        still: List[str] = []
        for sym in missing:
            p = get_cached_price(sym, "CRYPTO", _env)
            if p and float(p) > 0:
                info = market_summary.setdefault(sym, {})
                if isinstance(info, dict):
                    info["current_price"] = float(p)
                    info["price_source"] = info.get("price_source") or "live_cache"
                    info["data_reliable"] = True
                    info.pop("error", None)
            else:
                still.append(sym)
        missing = still
    except Exception as e:
        logger.debug(f"[FullAuto] ensure_market_prices cache: {e}")

    still = []
    for sym in missing:
        info = market_summary.get(sym)
        if not isinstance(info, dict):
            still.append(sym)
            continue
        klines = info.get("kline_data") or info.get("klines") or []
        close = 0.0
        if isinstance(klines, list) and klines:
            last = klines[-1]
            if isinstance(last, dict):
                close = float(last.get("close") or 0)
        if close > 0:
            info["current_price"] = close
            info["price_source"] = info.get("price_source") or "kline_close"
            info["data_reliable"] = True
            info.pop("error", None)
        else:
            still.append(sym)
    missing = still

    if missing:
        try:
            boot = bootstrap_market_summary(missing, ctx)
            for sym, info in (boot or {}).items():
                if not isinstance(info, dict) or not info.get("current_price"):
                    continue
                base = market_summary.get(sym)
                if isinstance(base, dict):
                    base.update(info)
                else:
                    market_summary[sym] = dict(info)
        except Exception as e:
            logger.debug(f"[FullAuto] ensure_market_prices bootstrap: {e}")
