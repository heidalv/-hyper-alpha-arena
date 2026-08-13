"""AI 选币 → 快速策略选择器观察者。

每 PAIR_SELECTOR_WATCHER_INTERVAL_SEC（默认 300s）扫运行中会话的
auto_coin_symbols；对 24h 内未扫描过的币，在后台线程跑 pair_selector
（数据不足自动回填）。一次只启动一个币，避免压垮数据源。

候选写入 pair_strategy_candidates；达标时可由 auto_promote_best
自动晋级绑定（受 PAIR_AUTO_PROMOTE_MIN_* 门槛约束）。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List

from sqlalchemy import text

logger = logging.getLogger(__name__)

_processing: set = set()
_lock = threading.Lock()


def _active_auto_symbols() -> List[str]:
    """真实 AI 选币来源：full_auto_sessions.auto_coin_symbols（与前端一致）。"""
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    out: List[str] = []
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT auto_coin_symbols FROM full_auto_sessions "
                    "WHERE status = 'running'"
                ),
            ).mappings().all()
    for r in rows:
        try:
            import json as _json
            syms = r["auto_coin_symbols"]
            if isinstance(syms, str):
                syms = _json.loads(syms)
            if isinstance(syms, list):
                out.extend(str(s) for s in syms)
        except Exception:
            continue
    return sorted(set(out))


def _worker(symbol: str) -> None:
    try:
        from backend.services.scalp.pair_selector import run_pair_selector
        report = run_pair_selector(symbol, ensure_data_first=True, max_wait_sec=300)
        logger.info("[PairWatcher] %s 完成: pass=%d candidates=%d",
                    symbol, report.get("n_pass", 0), len(report.get("candidates", [])))
    except Exception as e:
        logger.exception("[PairWatcher] %s 处理失败: %s", symbol, e)
    finally:
        with _lock:
            _processing.discard(symbol)


def run_pair_selector_watcher() -> Dict[str, Any]:
    if os.getenv("PAIR_SELECTOR_WATCHER_ENABLED", "true").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        return {"skipped": True, "reason": "PAIR_SELECTOR_WATCHER_ENABLED=0"}

    from backend.services.scalp.pair_selector import auto_promote_best, processed_within_hours
    from backend.services.scalp.scalp_heartbeat import touch

    symbols = _active_auto_symbols()
    report: Dict[str, Any] = {"checked": len(symbols), "started": [], "auto_enabled": []}
    with _lock:
        running = len(_processing)
    for sym in symbols:
        with _lock:
            if sym in _processing or running >= 1:
                continue
            _processing.add(sym)
            running += 1
        if processed_within_hours(sym, 24):
            with _lock:
                _processing.discard(sym)
            try:
                promo = auto_promote_best(sym)
                if promo and promo.get("id"):
                    report["auto_enabled"].append({
                        "symbol": sym, "binding": promo,
                    })
                    logger.info("[PairWatcher] %s 已有候选自动晋级绑定 %s",
                                sym, promo.get("id"))
            except Exception as e:
                logger.warning("[PairWatcher] %s 自动晋级失败: %s", sym, e)
            continue
        t = threading.Thread(target=_worker, args=(sym,), daemon=True,
                             name="pair-selector-%s" % sym)
        t.start()
        report["started"].append(sym)
        break  # 每个 tick 只处理一个币
    # 每个 tick 对全部活跃币做一次自动晋级（幂等，不扫描，只查已有 pass 候选）
    for sym in symbols:
        try:
            promo = auto_promote_best(sym)
            if promo and promo.get("id"):
                report["auto_enabled"].append({
                    "symbol": sym, "binding": promo,
                })
                logger.info("[PairWatcher] %s 自动晋级绑定 %s", sym, promo.get("id"))
        except Exception as e:
            logger.warning("[PairWatcher] %s 自动晋级失败: %s", sym, e)
    touch("pair_selector_watcher", "ok", report)
    return report
