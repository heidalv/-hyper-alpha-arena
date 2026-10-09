# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化·③ 可观测性] K 线**陈旧清单**的周期常态可见。

## 为什么需要
本轮之前，"哪些标的的 K 线陈旧了"**只能靠临时脚本查**
（`scripts/probe_kline_freshness_fixed.py`）。而这次事故的教训正是：
**没有人看的东西等于不存在** —— SUI 的 1d 陈旧 **102 小时**没人发现，
直到主脑因为它判 `K线:*` 硬缺项、论题被自动拒，才在复查里被翻出来（报告 §12）。

本模块把"陈旧清单"变成**数据中心日志里的常态一行**：
- 每 `KLINE_FRESHNESS_WATCH_INTERVAL_S`（默认 1800s）扫一次**分析宇宙**
  （交易宇宙 ∪ 分析看板，与 `kline_history_sync._depth_symbols` 同源）；
- 有陈旧 ⇒ `warning` 一行列出 `SYM/tf(年龄h)`；全新鲜 ⇒ `info` 一行；
- **纯观察**：不写库、不改采集行为、不阻塞。

口径：阈值 = 3 个周期长度（15m→0.75h、1h→3h、4h→12h、1d→72h），与
`scripts/probe_kline_freshness_fixed.py` 一致，避免两处口径打架。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 周期 → 陈旧阈值（小时）：3 个周期长度
ANALYSIS_TF_MAX_HOURS: Dict[str, float] = {
    "15m": 0.75, "1h": 3.0, "4h": 12.0, "1d": 72.0,
}
#: 需要盯的周期（与主脑 `_kline_ok` 对 mid 档的要求一致：1h/4h/1d，外加 15m 供短档）
ANALYSIS_TFS: Tuple[str, ...] = ("15m", "1h", "4h", "1d")


def classify(
    rows: Iterable[Sequence[Any]],
    *,
    now: Optional[float] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """纯函数：把 `[(symbol, period, max_ts_epoch)]` 分成 (陈旧, 新鲜)。

    - `max_ts_epoch` 为空/None ⇒ 记为"无数据"（也算陈旧，`age_h=None`）；
    - 阈值缺省用 `ANALYSIS_TF_MAX_HOURS`；未知周期不判（既不进陈旧也不进新鲜）。
    """
    _now = float(now if now is not None else time.time())
    _th = thresholds or ANALYSIS_TF_MAX_HOURS
    stale: List[Dict[str, Any]] = []
    fresh: List[Dict[str, Any]] = []
    for row in rows or []:
        try:
            sym, tf, ts = str(row[0]).upper(), str(row[1]), row[2]
        except Exception:  # noqa: BLE001
            continue
        if tf not in _th:
            continue
        if ts is None:
            stale.append({"symbol": sym, "period": tf, "age_h": None,
                          "reason": "无数据", "limit_h": _th[tf]})
            continue
        try:
            age_h = (_now - float(ts)) / 3600.0
        except (TypeError, ValueError):
            continue
        item = {"symbol": sym, "period": tf, "age_h": round(age_h, 2),
                "limit_h": _th[tf]}
        (stale if age_h > _th[tf] else fresh).append(item)
    return stale, fresh


def analysis_universe() -> List[str]:
    """分析宇宙 = 交易宇宙 ∪ 分析看板（与 `_depth_symbols` 同源，失败返回空）。"""
    out: List[str] = []
    try:
        from backend.services.kline_history_sync import _analysis_board_symbols
        out += list(_analysis_board_symbols() or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.services.kline_realtime_collector import get_trade_universe_symbols
        out += list(get_trade_universe_symbols() or [])
    except Exception:  # noqa: BLE001
        pass
    seen, uniq = set(), []
    for s in out:
        su = str(s or "").upper().strip()
        if su and su not in seen:
            seen.add(su)
            uniq.append(su)
    return uniq


def stale_snapshot(symbols: Optional[Sequence[str]] = None) -> Tuple[List[Dict[str, Any]], int]:
    """查 DB 得到 (陈旧列表, 检查项数)。失败返回 ([], 0) —— 不抛、不影响采集。"""
    syms = [str(s).upper() for s in (symbols or analysis_universe()) if s]
    if not syms:
        return [], 0
    try:
        from sqlalchemy import text

        from backend.database.connection import MarketSessionLocal
        rows: List[Tuple[str, str, Any]] = []
        with MarketSessionLocal() as db:
            db.execute(text("SET app.is_admin='on'"))
            res = db.execute(text("""
                SELECT symbol, period, max(timestamp) AS max_ts
                FROM crypto_klines
                WHERE symbol = ANY(:syms) AND period = ANY(:tfs)
                GROUP BY symbol, period
            """), {"syms": syms, "tfs": list(ANALYSIS_TFS)}).fetchall()
            for r in res:
                rows.append((r[0], r[1], r[2]))
        # 补齐"完全没有任何行"的组合（GROUP BY 不会返回它们）
        have = {(str(a).upper(), str(b)) for a, b, _ in rows}
        for s in syms:
            for tf in ANALYSIS_TFS:
                if (s, tf) not in have:
                    rows.append((s, tf, None))
        stale, _fresh = classify(rows)
        return stale, len(rows)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FreshnessWatch] 陈旧清单查询失败（不阻塞）: %s", exc)
        return [], 0


def format_line(stale: Sequence[Dict[str, Any]], checked: int) -> str:
    """把陈旧清单格式化成一行（供日志/自检复用）。"""
    if not stale:
        return f"[FreshnessWatch] {checked} 个(标的×周期)组合全部新鲜"
    parts = []
    for it in sorted(stale, key=lambda x: -float(x.get("age_h") or 1e9))[:15]:
        age = "无数据" if it.get("age_h") is None else f"{it['age_h']}h"
        parts.append(f"{it['symbol']}/{it['period']}({age})")
    more = "" if len(stale) <= 15 else f" …共{len(stale)}项"
    return (f"[FreshnessWatch] ⚠️ 陈旧 {len(stale)}/{checked} 项："
            f"{', '.join(parts)}{more} —— 陈旧会让主脑判 `K线:*` 硬缺项 ⇒ 论题被自动拒")


def log_summary(symbols: Optional[Sequence[str]] = None) -> Optional[str]:
    """扫一次并落一行日志；返回该行文本（便于测试/自检）。"""
    stale, checked = stale_snapshot(symbols)
    if not checked:
        return None
    line = format_line(stale, checked)
    (logger.warning if stale else logger.info)(line)
    return line


def interval_s() -> float:
    try:
        return max(60.0, float(os.getenv("KLINE_FRESHNESS_WATCH_INTERVAL_S", "1800") or 1800))
    except (TypeError, ValueError):
        return 1800.0


def watch_once_if_enabled() -> Optional[str]:
    """开关 `KLINE_FRESHNESS_WATCH_ENABLED`（默认开）控制。"""
    if str(os.getenv("KLINE_FRESHNESS_WATCH_ENABLED", "1")).strip().lower() in (
        "0", "false", "no", "off",
    ):
        return None
    return log_summary()


def start_watch_thread(stop_event: Optional[threading.Event] = None) -> threading.Thread:
    """后台守护线程：周期性落"陈旧清单"一行（与 `_whale_loop` 同范式）。"""
    def _loop() -> None:
        # 启动后先等一会儿，避开采集预热期
        time.sleep(float(os.getenv("KLINE_FRESHNESS_WATCH_WARMUP_S", "120") or 120))
        while not (stop_event is not None and stop_event.is_set()):
            try:
                watch_once_if_enabled()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[FreshnessWatch] 扫描异常（忽略）: %s", exc)
            time.sleep(interval_s())

    t = threading.Thread(target=_loop, name="kline-freshness-watch", daemon=True)
    t.start()
    return t
