"""24h/72h 反馈回写 + 衰减乘数。"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_decay_cache: Dict[str, float] = {}
_hist_cache: Dict[str, Dict[str, Any]] = {}
_cache_ts: float = 0.0
_lock = threading.Lock()
_CACHE_TTL = 300.0


def feedback_enabled() -> bool:
    try:
        from backend.config.settings import COIN_RANK_FEEDBACK_ENABLED
        return bool(COIN_RANK_FEEDBACK_ENABLED)
    except Exception:
        return os.getenv("COIN_RANK_FEEDBACK_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def get_decay_map() -> Dict[str, float]:
    _refresh_caches_if_needed()
    return dict(_decay_cache)


def get_hist_map() -> Dict[str, Dict[str, Any]]:
    _refresh_caches_if_needed()
    return dict(_hist_cache)


def _refresh_caches_if_needed() -> None:
    global _cache_ts
    if time.time() - _cache_ts < _CACHE_TTL:
        return
    with _lock:
        if time.time() - _cache_ts < _CACHE_TTL:
            return
        try:
            _rebuild_from_db()
            _cache_ts = time.time()
        except Exception as e:
            logger.debug("[CoinRank.feedback] cache rebuild: %s", e)


def _rebuild_from_db() -> None:
    from backend.core.tenant import set_system_identity
    from backend.database.connection import SessionLocal
    from backend.database.models import AutoCoinSelection

    set_system_identity()  # 穿透 RLS，否则 hit_24h 样本永远读不到
    db = SessionLocal()
    decay: Dict[str, float] = {}
    hist: Dict[str, Dict[str, Any]] = {}
    try:
        # 注意：created_at 由 PG server_default current_timestamp() 写入，
        # 会话时区为 Asia/Shanghai（naive CST）。必须用本地时间作参照系，
        # 否则 utcnow() 与 CST 相差 8h → age 为负 → 24h 回填永不触发。
        since = datetime.now() - timedelta(days=30)
        q = (
            db.query(AutoCoinSelection)
            .filter(
                AutoCoinSelection.action == "injected",
                AutoCoinSelection.created_at >= since,
                AutoCoinSelection.hit_24h.isnot(None),
            )
            .all()
        )
        buckets: Dict[str, list] = {}
        for r in q:
            sym = (r.symbol or "").upper()
            buckets.setdefault(sym, []).append(r)

        for sym, items in buckets.items():
            hits = [1.0 if bool(x.hit_24h) else 0.0 for x in items]
            pnls = []
            for x in items:
                if x.realized_pnl is not None:
                    try:
                        pnls.append(float(x.realized_pnl))
                    except Exception:
                        pass
                elif x.price_at_selection and x.price_after_24h:
                    try:
                        p0 = float(x.price_at_selection)
                        p1 = float(x.price_after_24h)
                        if p0 > 0:
                            pnls.append((p1 - p0) / p0 * 100.0)
                    except Exception:
                        pass
            n = len(hits)
            hit_rate = sum(hits) / n if n else None
            avg_pnl = (sum(pnls) / len(pnls)) if pnls else None
            hist[sym] = {
                "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                "avg_pnl_24h": round(avg_pnl, 4) if avg_pnl is not None else None,
                "samples": n,
            }
            # 衰减：样本≥3 且命中率很差 → 压分；好则略抬
            if n >= 3 and hit_rate is not None:
                if hit_rate < 0.35:
                    decay[sym] = 0.75
                elif hit_rate < 0.45:
                    decay[sym] = 0.88
                elif hit_rate >= 0.60:
                    decay[sym] = 1.05
                else:
                    decay[sym] = 1.0
    except Exception as e:
        logger.warning("[CoinRank.feedback] rebuild failed: %s", e)
    finally:
        db.close()

    global _decay_cache, _hist_cache
    _decay_cache = decay
    _hist_cache = hist


def write_price_feedback(db, *, max_rows: int = 200) -> Dict[str, int]:
    """回写 price_after_24h/72h 与 hit 标志；并补齐缺失的 price_at_selection。"""
    if not feedback_enabled():
        return {"skipped": 1}

    from backend.database.models import AutoCoinSelection

    now = datetime.now()  # 与 PG created_at（CST naive）同参照系，见 _rebuild_from_db 注释
    updated_24 = 0
    updated_72 = 0
    filled_entry = 0

    try:
        pending = (
            db.query(AutoCoinSelection)
            .filter(AutoCoinSelection.action == "injected")
            .order_by(AutoCoinSelection.id.desc())
            .limit(max_rows * 3)
            .all()
        )
    except Exception as e:
        logger.warning("[CoinRank.feedback] query: %s", e)
        return {"error": 1}

    def _px(sym: str) -> Optional[float]:
        sym_u = (sym or "").upper().split("-")[0].split("/")[0]
        prices = _px.cache  # type: ignore[attr-defined]
        if sym_u in prices and float(prices[sym_u]) > 0:
            return float(prices[sym_u])
        return None

    # 1) ticker 快照（失败就跳过，别拖死）
    _px.cache = {}  # type: ignore[attr-defined]
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

        prices = asterdex_ticker_poller.get_all_prices() or {}
        if len(prices) >= 20:
            _px.cache = {str(k).upper(): float(v) for k, v in prices.items() if v}  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("[CoinRank.feedback] ticker load: %s", e)

    # 2) 用 market DB 最新 1m 收盘批量补齐（不依赖外网）
    need_syms = sorted({
        (r.symbol or "").upper().split("-")[0].split("/")[0]
        for r in pending
        if r.symbol and (
            r.price_at_selection is None
            or r.price_after_24h is None
            or r.price_after_72h is None
        )
    })
    missing = [s for s in need_syms if s not in _px.cache]  # type: ignore[attr-defined]
    if missing:
        try:
            from sqlalchemy import text as sa_text
            from backend.database.connection import MarketSessionLocal

            with MarketSessionLocal() as mdb:
                # 最近一根 1m
                rows = mdb.execute(sa_text("""
                    SELECT DISTINCT ON (symbol) symbol, close_price
                    FROM crypto_klines
                    WHERE exchange = 'asterdex' AND period = '1m'
                      AND symbol = ANY(:syms)
                    ORDER BY symbol, timestamp DESC
                """), {"syms": missing}).fetchall()
                for r in rows:
                    try:
                        c = float(r.close_price)
                        if c > 0:
                            _px.cache[str(r.symbol).upper()] = c  # type: ignore[attr-defined]
                    except Exception:
                        pass
                logger.info(
                    "[CoinRank.feedback] price cache from_db=%d need=%d",
                    len(_px.cache),  # type: ignore[attr-defined]
                    len(need_syms),
                )
        except Exception as e:
            logger.warning("[CoinRank.feedback] db last-price: %s", e)
    for row in pending:
        try:
            created = row.created_at
            if not created:
                continue
            if getattr(created, "tzinfo", None) is not None:
                created_naive = created.replace(tzinfo=None)
            else:
                created_naive = created
            age_h = (now - created_naive).total_seconds() / 3600.0
            sym = (row.symbol or "").upper()

            # 历史注入缺进场价：优先用注入时刻附近的 K 线收盘，避免与现价同值导致伪命中
            if row.price_at_selection is None:
                px_entry = None
                try:
                    from sqlalchemy import text as sa_text
                    from backend.database.connection import MarketSessionLocal

                    ts0 = int(created_naive.timestamp())
                    with MarketSessionLocal() as mdb:
                        hist = mdb.execute(sa_text("""
                            SELECT close_price FROM crypto_klines
                            WHERE exchange='asterdex' AND period='1m' AND symbol=:s
                              AND timestamp <= :ts
                            ORDER BY timestamp DESC LIMIT 1
                        """), {"s": sym.split("-")[0].split("/")[0], "ts": ts0}).scalar()
                        if hist and float(hist) > 0:
                            px_entry = float(hist)
                except Exception:
                    px_entry = None
                if px_entry is None:
                    px_entry = _px(sym)
                if px_entry and px_entry > 0:
                    row.price_at_selection = px_entry
                    filled_entry += 1

            px0 = float(row.price_at_selection) if row.price_at_selection is not None else None
            if px0 is None or px0 <= 0:
                continue

            if age_h >= 24 and row.price_after_24h is None:
                px = _px(sym)
                if px and px > 0:
                    row.price_after_24h = px
                    row.hit_24h = bool(px >= px0)
                    updated_24 += 1

            if age_h >= 72 and row.price_after_72h is None:
                px = _px(sym)
                if px and px > 0:
                    row.price_after_72h = px
                    row.hit_72h = bool(px >= px0)
                    updated_72 += 1
        except Exception as e:
            logger.debug("[CoinRank.feedback] row %s: %s", getattr(row, "id", "?"), e)

    try:
        db.commit()
    except Exception as e:
        logger.warning("[CoinRank.feedback] commit: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return {"error": 1}

    # 使缓存失效
    global _cache_ts
    _cache_ts = 0.0
    logger.info(
        "[CoinRank.feedback] filled_entry=%d updated_24=%d updated_72=%d",
        filled_entry, updated_24, updated_72,
    )
    return {
        "filled_entry_price": filled_entry,
        "updated_24h": updated_24,
        "updated_72h": updated_72,
    }


class CoinRankFeedbackScheduler:
    """轻量后台：定期回写反馈。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    async def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not feedback_enabled():
            logger.info("[CoinRank.feedback] scheduler disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="coin-rank-feedback", daemon=True)
        self._thread.start()
        logger.info("[CoinRank.feedback] scheduler started")

    async def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # 启动延迟
        if self._stop.wait(60):
            return
        while not self._stop.is_set():
            try:
                from backend.core.tenant import set_system_identity
                from backend.database.connection import SessionLocal

                set_system_identity()  # 后台回写须穿透 RLS
                db = SessionLocal()
                try:
                    write_price_feedback(db)
                finally:
                    db.close()
            except Exception as e:
                logger.warning("[CoinRank.feedback] loop: %s", e)
            interval = int(os.getenv("COIN_RANK_FEEDBACK_INTERVAL_SEC", "900"))
            if self._stop.wait(max(300, interval)):
                break


coin_rank_feedback_scheduler = CoinRankFeedbackScheduler()
