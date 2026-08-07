"""ScalpSignalLogger — 短线真实信号日志（元标签数据采集）。

定位
====
把 `scalp_factor_router` 每次【触发的信号】（有明确方向且分数过门槛）连同当时的
因子快照落库（`scalp_signal_log` 表），事后由结算任务回填"信号方向上、horizon 之后
的净收益与输赢"。攒够数据后，可在【真实信号】上训练元标签模型（预测"这一单会不会
赢"），比离线代理信号忠实得多。

设计要点
--------
- 只记录"信号真的触发"的样本（direction ∈ {long,short} 且 score ≥ 记录门槛），
  避免把海量 hold 也写进去（既省库又聚焦元标签目标人群）。
- 用独立短事务，绝不与交易主链的 DB 会话耦合；任何异常都安全降级（不影响交易）。
- flag 门控：SCALP_SIGNAL_LOG_ENABLED=false 可一键关闭。

对外接口
--------
- log_signal(...): 交易循环里信号处调用，写一行（未结算）。
- settle_pending(limit): 定时任务调用，回填到期信号的结果。
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("SCALP_SIGNAL_LOG_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def _horizon_sec() -> int:
    try:
        return max(300, int(os.getenv("SCALP_META_HORIZON_SEC", "1800") or 1800))
    except Exception:
        return 1800


def _round_trip_cost() -> float:
    try:
        return float(os.getenv("SCALP_META_COST", "0.0008") or 0.0008)
    except Exception:
        return 0.0008


# 记录门槛：分数低于此值的弱信号不记（默认与 CONFIRM 门槛一致，聚焦真正会开的信号）
def _min_score() -> float:
    try:
        return float(os.getenv("SCALP_SIGNAL_LOG_MIN_SCORE", "25") or 25)
    except Exception:
        return 25.0


def _ensure_table() -> None:
    """确保表已建（主库启动会 create_all，这里兜底一次，幂等）。"""
    try:
        from backend.database.connection import engine, Base  # noqa
        from backend.database.models import ScalpSignalLog  # noqa
        ScalpSignalLog.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] ensure_table 跳过: {e}")


_TABLE_READY = False


def log_signal(
    *,
    symbol: str,
    direction: str,
    action: str,
    factor_score: float,
    threshold: Optional[float] = None,
    entry_price: Optional[float] = None,
    features: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    account_id: Optional[int] = None,
    signal_ts: Optional[int] = None,
) -> None:
    """记录一条触发的短线信号（安全降级，不抛异常）。"""
    global _TABLE_READY
    if not _enabled():
        return
    try:
        dir_l = (direction or "").lower()
        if dir_l not in ("long", "short"):
            return  # 只记有方向的
        if factor_score is None or float(factor_score) < _min_score():
            return
        if not entry_price or float(entry_price) <= 0:
            return
        if not _TABLE_READY:
            _ensure_table()
            _TABLE_READY = True

        from backend.database.connection import SessionLocal
        from backend.database.models import ScalpSignalLog

        row = ScalpSignalLog(
            symbol=(symbol or "").upper(),
            signal_ts=int(signal_ts or time.time()),
            direction=dir_l,
            action=(action or "").lower(),
            factor_score=float(factor_score),
            threshold=float(threshold) if threshold is not None else None,
            entry_price=float(entry_price),
            session_id=session_id,
            account_id=account_id,
            features_json=json.dumps(features or {}, ensure_ascii=False, default=str)[:20000],
            horizon_sec=_horizon_sec(),
            settled=False,
        )
        db = SessionLocal()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] log_signal 跳过({symbol}): {e}")


def _kline_lookback() -> int:
    """结算取价的 5m K线回看根数。500 根≈41.7 小时，积压越久需要越大。"""
    try:
        return max(500, int(os.getenv("SCALP_SETTLE_KLINE_LOOKBACK", "500") or 500))
    except Exception:
        return 500


def _settle_exchange() -> Optional[str]:
    """结算取价所用交易所，默认跟随活跃交易所（即实际成交所）。"""
    ex = (os.getenv("SCALP_SETTLE_EXCHANGE", "") or "").strip().lower()
    if ex:
        return ex
    try:
        from backend.services.exchange_config import get_active_exchange
        return (get_active_exchange() or "").strip().lower() or None
    except Exception:
        return None


def _load_klines(symbol: str, cache: Dict[str, List[Tuple[int, float]]]) -> List[Tuple[int, float]]:
    """按 symbol 取一次 5m K线并按时间升序缓存为 (ts, close)。

    结算是逐行进行的，若每行都回库取 500 根 K线，一次积压回填会放大成百万级
    行读取。这里按 symbol 缓存，使单次 settle_pending 内每个币只查一次。
    """
    key = (symbol or "").upper()
    if key in cache:
        return cache[key]
    rows: List[Tuple[int, float]] = []
    try:
        from backend.services.kline_data_service import kline_service
        # 结算价必须取自实际成交所。曾硬编码 hyperliquid，导致非 hyperliquid 币种
        # 取不到价、信号被整批标记 no_price；而 exchange=None 会让 data_center 按
        # "行数最多"挑源，实测反而挑中滞后 75 分钟的 hyperliquid（同时刻 asterdex
        # 仅滞后 5 分钟）。故显式跟随活跃交易所，取不到再由下方 fallback 兜底。
        raw = kline_service.get_klines_from_db(
            key, "5m", _kline_lookback(), exchange=_settle_exchange(),
        ) or []
        if not raw:
            raw = kline_service.get_klines_from_db(key, "5m", _kline_lookback()) or []
        rows = sorted(
            (int(r.get("timestamp", 0)), float(r.get("close") or 0)) for r in raw
        )
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] 取K线失败({key}): {e}")
    cache[key] = rows
    return rows


def _exit_price_at(
    symbol: str,
    target_ts: int,
    cache: Optional[Dict[str, List[Tuple[int, float]]]] = None,
) -> Optional[float]:
    """取 target_ts（秒）时刻之后最近一根 5m K线收盘价作为结算价。"""
    rows = _load_klines(symbol, cache if cache is not None else {})
    if not rows:
        return None
    # rows 按 ts 升序；找第一根 ts >= target_ts 的收盘价
    idx = bisect.bisect_left(rows, (int(target_ts), float("-inf")))
    if idx >= len(rows):
        return None
    return rows[idx][1] or None


def settle_pending(limit: int = 500) -> Dict[str, int]:
    """结算到期未结算信号：回填 fwd_ret/net_ret/win。返回统计。"""
    if not _enabled():
        return {"checked": 0, "settled": 0}
    stats = {"checked": 0, "settled": 0, "wins": 0, "skipped": 0}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ScalpSignalLog

        now = int(time.time())
        cost = _round_trip_cost()
        kline_cache: Dict[str, List[Tuple[int, float]]] = {}
        db = SessionLocal()
        try:
            pend = (db.query(ScalpSignalLog)
                    .filter(ScalpSignalLog.settled == False)  # noqa: E712
                    .order_by(ScalpSignalLog.signal_ts.asc())
                    .limit(limit).all())
            for r in pend:
                stats["checked"] += 1
                horizon = int(r.horizon_sec or _horizon_sec())
                target = int(r.signal_ts or 0) + horizon
                if now < target:
                    continue  # 还没到结算时间
                ex = _exit_price_at(r.symbol, target, kline_cache)
                if ex is None:
                    # 到期但暂时取不到价：过久则标记放弃，避免永远卡着
                    if now - target > horizon * 4:
                        r.settled = True
                        r.settle_note = "no_price"
                        stats["skipped"] += 1
                    continue
                entry = float(r.entry_price or 0)
                if entry <= 0:
                    r.settled = True
                    r.settle_note = "no_entry"
                    stats["skipped"] += 1
                    continue
                fwd = ex / entry - 1.0
                dir_ret = fwd if r.direction == "long" else -fwd
                net = dir_ret - cost
                r.exit_price = ex
                r.fwd_ret = float(dir_ret)
                r.net_ret = float(net)
                r.win = bool(net > 0)
                r.settle_ts = now
                r.settled = True
                r.settle_note = "ok"
                stats["settled"] += 1
                if r.win:
                    stats["wins"] += 1
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[ScalpSignalLog] settle_pending 失败: {e}")
    if stats["settled"]:
        wr = stats["wins"] / stats["settled"]
        logger.info(f"[ScalpSignalLog] 结算 {stats['settled']} 条，胜率 {wr:.1%}")
    return stats
