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


def _tb_max_hold_sec() -> int:
    """triple-barrier 的垂直轨：短线实际持仓上限。

    [2026-09-02 P1.1] 取 TIER_PROTECTION_PARAMS['short']['max_hold_sec']，而固定
    horizon 标签用的是 1800 —— 旧标签只覆盖了实际持仓窗口的一小段，这正是
    "模型学的"与"实盘发生的"错配的量化体现。

    刻意做成**动态跟随**而非写死：持仓上限一改（P2.2 已由 7200→5400），标签
    的垂直轨必须同步，否则标签窗口 ≠ 执行窗口，又回到旧 horizon 的错配。
    每条样本结算时用的实际值另存 scalp_signal_log.tb_max_hold_sec 列，便于跨
    口径变更后区分/重算历史样本。
    """
    try:
        _v = os.getenv("SCALP_META_TB_MAX_HOLD_SEC", "")
        if _v.strip():
            return max(300, int(_v))
    except (TypeError, ValueError):
        pass
    try:
        from backend.config.settings import TIER_PROTECTION_PARAMS
        return max(300, int(
            TIER_PROTECTION_PARAMS.get("short", {}).get("max_hold_sec", 7200) or 7200
        ))
    except Exception:
        return 7200


def _tb_backfill_tpsl() -> Tuple[float, float]:
    """历史样本缺 tp/sl 时的回填值（小数）。

    31.5 万条历史信号写库时未记录当时的 TP/SL 距离，无法精确复算 triple-barrier。
    这里用实测中位口径回填并在 settle_note 标注 approx，使两套标签能立刻对比；
    新样本一律用信号自带的真实 tp_pct/sl_pct。
    """
    def _f(key: str, dflt: float) -> float:
        try:
            return max(0.0005, float(os.getenv(key, str(dflt)) or dflt))
        except (TypeError, ValueError):
            return dflt
    return _f("SCALP_META_TB_BACKFILL_TP", 0.013), _f("SCALP_META_TB_BACKFILL_SL", 0.011)


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
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
) -> None:
    """记录一条触发的短线信号（安全降级，不抛异常）。

    [2026-09-02 P1.1] 新增 tp_pct/sl_pct：triple-barrier 标签需要知道这条信号
    当时的止盈止损距离，才能复算"TP 和 SL 谁先被触及"。二者缺省时该行的 tb 标签
    退化为按 SCALP_META_TB_BACKFILL_* 的近似口径（settle_note 会标 approx）。
    """
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
            # [2026-09-02 P1.1] triple-barrier 输入：轨道位置随信号一起冻结，
            # 事后结算才能复现"当时那笔如果按计划 TP/SL 走会怎样"。
            tb_tp_pct=(float(tp_pct) if tp_pct and float(tp_pct) > 0 else None),
            tb_sl_pct=(float(sl_pct) if sl_pct and float(sl_pct) > 0 else None),
            tb_max_hold_sec=_tb_max_hold_sec(),
            tb_settled=False,
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


def _fetch_settle_klines(symbol: str, count: int) -> List[Dict[str, Any]]:
    """结算专用 5m K 线读取：research 用途 + 显式结算所 + 只取已收盘 bar。

    [2026-09-02 修复] 此前经 kline_service.get_klines_from_db → data_center(purpose="trade")。
    trade 用途是给实盘开仓设计的两条规则，对**历史标签结算**都是错配：
      1. 数据过期即返回空（"数据过期 stale=…，trade 用途返回不可用"）——币一旦退出活跃池、
         K 线停更，它过去几周的信号就永远拿不到窗口内的历史 K 线，全被判 none。
         实测 DOT 1382 条、KAITO 1.4 万条等共 5.7 万条因此丢失标签。
      2. 强制切到当前活跃交易所——信号在 A 所生成、结算却读 B 所。
    research 用途允许显式 exchange 且不设新鲜度门；closed_only=True 保持"不吃未收盘 bar"。
    结算所取不到再退到多所择优（仅历史窗口，滞后与否无关）。
    """
    key = (symbol or "").upper()
    try:
        from backend.services.data_center import data_center
        ex = _settle_exchange()
        res = data_center.get_klines(
            key, "5m", count=int(count), exchange=ex, purpose="research", closed_only=True,
        )
        rows = list(res.rows or []) if res is not None else []
        if not rows and ex:
            res = data_center.get_klines(
                key, "5m", count=int(count), exchange=None, purpose="research", closed_only=True,
            )
            rows = list(res.rows or []) if res is not None else []
        return rows[-int(count):] if len(rows) > int(count) else rows
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] data_center 取K线失败({key})，降级 kline_service: {e}")
    try:
        from backend.services.kline_data_service import kline_service
        raw = kline_service.get_klines_from_db(key, "5m", int(count), exchange=_settle_exchange()) or []
        if not raw:
            raw = kline_service.get_klines_from_db(key, "5m", int(count)) or []
        return list(raw)
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] 取K线失败({key}): {e}")
        return []


def _load_klines(symbol: str, cache: Dict[str, List[Tuple[int, float]]]) -> List[Tuple[int, float]]:
    """按 symbol 取一次 5m K线并按时间升序缓存为 (ts, close)。

    结算是逐行进行的，若每行都回库取 500 根 K线，一次积压回填会放大成百万级
    行读取。这里按 symbol 缓存，使单次 settle_pending 内每个币只查一次。
    结算价必须取自实际成交所（曾硬编码 hyperliquid 导致非 hyperliquid 币整批
    no_price），取法见 _fetch_settle_klines。
    """
    key = (symbol or "").upper()
    if key in cache:
        return cache[key]
    rows: List[Tuple[int, float]] = []
    try:
        raw = _fetch_settle_klines(key, _kline_lookback())
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


def _load_ohlc(
    symbol: str, cache: Dict[str, List[Tuple[int, float, float, float]]],
) -> List[Tuple[int, float, float, float]]:
    """按 symbol 取一次 5m K线，缓存为 (ts, high, low, close) 升序。

    [2026-09-02 P1.1] 与 `_load_klines` 的区别：triple-barrier 必须看**路径**
    （最高/最低价是否触及轨道），只有收盘价无法判断 TP/SL 谁先到。
    """
    key = (symbol or "").upper()
    if key in cache:
        return cache[key]
    rows: List[Tuple[int, float, float, float]] = []
    try:
        from backend.services.data_quality_gate import (
            DEFAULT_MAX_GAP_PCT, is_plausible_kline, is_plausible_ts, normalize_epoch_ms,
        )

        raw = _fetch_settle_klines(key, _kline_lookback())
        _prev_close: Optional[float] = None
        _dropped = 0
        for r in raw:
            try:
                # [F55] 注意：本模块时间戳沿用 _fetch_settle_klines 的原始单位（秒），
                # 与 signal_ts（int(time.time())）比较。此处只做**合理性校验**，
                # 绝不改写单位，否则 triple_barrier_outcome 的时间窗会整体错位。
                _ts = int(r.get("timestamp", 0) or 0)
                _hi = float(r.get("high") or 0)
                _lo = float(r.get("low") or 0)
                _cl = float(r.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if _ts <= 0 or not is_plausible_ts(normalize_epoch_ms(_ts)):
                continue
            # [F55 2026-09-09] 数据质量闸：脏 bar（跳空 >10% / 高低倒挂 / 非正价）
            # 会污染三屏障结算（实测库中存在单根 25025% 跳空的坏 K 线）。
            # 用开盘价近似为前收（本函数只有 h/l/c），跳空按 |open/prev_close-1| 判定。
            _open = float(r.get("open") or _prev_close or _cl or 0)
            _ok, _why = is_plausible_kline(
                _open, _hi, _lo, _cl, prev_close=_prev_close,
                max_gap_pct=DEFAULT_MAX_GAP_PCT,
            )
            if not _ok:
                _dropped += 1
                continue
            rows.append((_ts, _hi, _lo, _cl))
            _prev_close = _cl
        rows.sort()
        if _dropped:
            logger.info("[ScalpSignalLog] %s 剔除脏 K 线 %d 根（数据质量闸）", key, _dropped)
    except Exception as e:
        logger.debug(f"[ScalpSignalLog] 取OHLC失败({key}): {e}")
    cache[key] = rows
    return rows


def triple_barrier_outcome(
    rows: List[Tuple[int, float, float, float]],
    *,
    start_ts: int,
    entry: float,
    direction: str,
    tp_pct: float,
    sl_pct: float,
    max_hold_sec: int,
    now_ts: int,
) -> Optional[Dict[str, Any]]:
    """判定 triple-barrier 结果：TP / SL / 垂直轨(timeout) 谁先被触及。

    返回 None 表示"还没到期且未触轨"，调用方应继续等待。

    口径与保守假设
    --------------
    - 轨道按信号方向摆放：long 的 TP 在上、SL 在下；short 相反。
    - **同一根 5m K 线内两轨都被触及时，判 SL**。5m 粒度下无法知道盘中先后，
      判 TP 会系统性高估策略表现（把"先跌穿止损再反弹"当成盈利），标签一旦
      乐观偏移，模型会学出"敢扛"的错误倾向。宁可低估。
    - 垂直轨用截止时刻前最后一根收盘价，与实盘 max_hold_timeout 的行为一致。
    """
    if entry <= 0 or tp_pct <= 0 or sl_pct <= 0 or not rows:
        return None
    _long = str(direction or "").lower() == "long"
    tp_price = entry * (1.0 + tp_pct) if _long else entry * (1.0 - tp_pct)
    sl_price = entry * (1.0 - sl_pct) if _long else entry * (1.0 + sl_pct)
    deadline = int(start_ts) + int(max_hold_sec)

    # 只扫 [start_ts, deadline] 窗口内的 K 线
    idx = bisect.bisect_left(rows, (int(start_ts), float("-inf"),
                                    float("-inf"), float("-inf")))
    last_close: Optional[Tuple[int, float]] = None
    for i in range(idx, len(rows)):
        ts, hi, lo, cl = rows[i]
        if ts > deadline:
            break
        last_close = (ts, cl)
        if _long:
            hit_sl, hit_tp = (lo <= sl_price), (hi >= tp_price)
        else:
            hit_sl, hit_tp = (hi >= sl_price), (lo <= tp_price)
        if hit_sl:   # 保守：同根内 SL 优先，见 docstring
            return {
                "kind": "sl", "hold_sec": max(0, ts - int(start_ts)),
                "fwd_ret": -float(sl_pct), "exit_price": sl_price,
            }
        if hit_tp:
            return {
                "kind": "tp", "hold_sec": max(0, ts - int(start_ts)),
                "fwd_ret": float(tp_pct), "exit_price": tp_price,
            }

    if now_ts < deadline:
        return None  # 窗口未走完，继续等
    if last_close is None:
        return None  # 窗口内无 K 线数据，交由调用方按 no_price 处理
    _ts, _cl = last_close
    _raw = _cl / entry - 1.0
    return {
        "kind": "timeout", "hold_sec": int(max_hold_sec),
        "fwd_ret": float(_raw if _long else -_raw), "exit_price": _cl,
    }


def settle_triple_barrier(limit: int = 500) -> Dict[str, int]:
    """按 triple-barrier 口径回填 tb_* 标签（与旧 net_ret/win 并行，互不影响）。

    [2026-09-02 P1.1] 为什么需要它：旧标签是"固定 30 分钟后的收盘收益"，而实盘
    盈亏由 TP/SL 谁先触及决定，短线持仓上限 7200s、中位持仓 46 分钟、止盈命中率
    仅 13%。标签与执行错配时，模型即使把标签预测得很准，也预测不了那一笔的实际
    输赢 —— 这是学习闭环的根因缺陷，而非模型容量问题。
    """
    if not _enabled():
        return {"checked": 0, "settled": 0}
    stats = {"checked": 0, "settled": 0, "tp": 0, "sl": 0,
             "timeout": 0, "wins": 0, "skipped": 0, "waiting": 0}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ScalpSignalLog

        now = int(time.time())
        cost = _round_trip_cost()
        bf_tp, bf_sl = _tb_backfill_tpsl()
        ohlc_cache: Dict[str, List[Tuple[int, float, float, float]]] = {}
        db = SessionLocal()
        try:
            pend = (db.query(ScalpSignalLog)
                    .filter(ScalpSignalLog.tb_settled.isnot(True))
                    .filter(ScalpSignalLog.direction.in_(("long", "short")))
                    .order_by(ScalpSignalLog.signal_ts.asc())
                    .limit(limit).all())
            for r in pend:
                stats["checked"] += 1
                entry = float(r.entry_price or 0)
                if entry <= 0:
                    r.tb_settled = True
                    r.tb_kind = "none"
                    stats["skipped"] += 1
                    continue
                _approx = not (r.tb_tp_pct and r.tb_sl_pct)
                tp_pct = float(r.tb_tp_pct or bf_tp)
                sl_pct = float(r.tb_sl_pct or bf_sl)
                max_hold = int(r.tb_max_hold_sec or _tb_max_hold_sec())
                start_ts = int(r.signal_ts or 0)

                out = triple_barrier_outcome(
                    _load_ohlc(r.symbol, ohlc_cache),
                    start_ts=start_ts, entry=entry, direction=str(r.direction),
                    tp_pct=tp_pct, sl_pct=sl_pct,
                    max_hold_sec=max_hold, now_ts=now,
                )
                if out is None:
                    # 到期很久仍拿不到 K 线才放弃，避免永远卡住
                    if now - (start_ts + max_hold) > max_hold * 4:
                        r.tb_settled = True
                        r.tb_kind = "none"
                        stats["skipped"] += 1
                    else:
                        stats["waiting"] += 1
                    continue

                _net = float(out["fwd_ret"]) - cost
                r.tb_kind = str(out["kind"])
                r.tb_hold_sec = int(out["hold_sec"])
                r.tb_fwd_ret = float(out["fwd_ret"])
                r.tb_net_ret = _net
                r.tb_win = bool(_net > 0)
                r.tb_tp_pct = tp_pct
                r.tb_sl_pct = sl_pct
                r.tb_max_hold_sec = max_hold
                r.tb_settled = True
                if _approx:
                    r.settle_note = ((r.settle_note or "") + "|tb_approx")[:64]
                stats["settled"] += 1
                stats[str(out["kind"])] = stats.get(str(out["kind"]), 0) + 1
                if r.tb_win:
                    stats["wins"] += 1
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[ScalpSignalLog] settle_triple_barrier 失败: {e}")
    if stats["settled"]:
        wr = stats["wins"] / stats["settled"]
        logger.info(
            "[ScalpSignalLog][TB] 结算 %d 条，胜率 %.1f%% "
            "(tp=%d sl=%d timeout=%d 等待=%d)",
            stats["settled"], wr * 100, stats["tp"], stats["sl"],
            stats["timeout"], stats["waiting"],
        )
    return stats


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
