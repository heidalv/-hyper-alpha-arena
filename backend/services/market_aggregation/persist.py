# -*- coding: utf-8 -*-
"""归一化落盘：全市场采集器结果 → 数据中心仓库 (alpha_market)。

设计目标：所有市场数据集中到数据中心仓库，中台 API 统一从仓库读取；
采集器只负责“抓取并写入”，内存缓存仅作瞬时兜底。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from sqlalchemy import text as _sa_text

from backend.database.connection import MarketSessionLocal

logger = logging.getLogger(__name__)


def _now_sec() -> int:
    return int(time.time())


def _touch_heartbeat(period: str, symbols_ok: int, symbols_fail: int, meta: Dict[str, Any]) -> None:
    """统一心跳：kline_sync_heartbeat 中 exchange='aggregate' 的采集周期记录。"""
    try:
        with MarketSessionLocal() as db:
            db.execute(
                _sa_text(
                    """
                    INSERT INTO kline_sync_heartbeat
                        (exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json, updated_at)
                    VALUES
                        ('aggregate', :period, 'agg', now(), :ok, :fail, :meta, now())
                    ON CONFLICT (exchange, period, pool) DO UPDATE SET
                        last_success_at = now(),
                        symbols_ok = EXCLUDED.symbols_ok,
                        symbols_fail = EXCLUDED.symbols_fail,
                        meta_json = EXCLUDED.meta_json,
                        updated_at = now()
                    """
                ),
                {"period": period, "ok": symbols_ok, "fail": symbols_fail, "meta": json.dumps(meta, ensure_ascii=False)},
            )
            db.commit()
    except Exception as e:
        logger.warning(f"[Persist] 心跳更新失败({period}): {e}")


def persist_orderbook(result: Dict[str, Any], symbols: List[str]) -> None:
    """盘口快照 → market_orderbook_snapshots（exchange/symbol/timestamp 幂等 upsert）。"""
    now = _now_sec()
    rows = []
    ok = 0
    for sym in symbols:
        item = result.get(sym) or {}
        for venue, v in (item.get("venues") or {}).items():
            if not v or not v.get("available"):
                continue
            bb = v.get("best_bid")
            ba = v.get("best_ask")
            rows.append(
                (
                    venue, sym, now, bb, ba,
                    (ba - bb) if (bb is not None and ba is not None) else None,
                    v.get("bid_volume", 0) or 0,
                    v.get("ask_volume", 0) or 0,
                    v.get("bid_volume", 0) or 0,
                    v.get("ask_volume", 0) or 0,
                    0, 0,
                    json.dumps({"source": venue, "fetched_at": item.get("fetched_at")}),
                )
            )
            ok += 1
    if not rows:
        _touch_heartbeat("orderbook", 0, len(symbols), {"sample": symbols[:8]})
        return
    try:
        with MarketSessionLocal() as db:
            db.execute(
                _sa_text(
                    """
                    INSERT INTO market_orderbook_snapshots
                        (exchange, symbol, timestamp, best_bid, best_ask, spread,
                         bid_depth_5, ask_depth_5, bid_depth_10, ask_depth_10,
                         bid_orders_count, ask_orders_count, raw_levels)
                    VALUES
                        (:ex, :sym, :ts, :bb, :ba, :sp, :bd5, :ad5, :bd10, :ad10, 0, 0, :raw)
                    ON CONFLICT (exchange, symbol, timestamp) DO UPDATE SET
                        best_bid = EXCLUDED.best_bid,
                        best_ask = EXCLUDED.best_ask,
                        spread = EXCLUDED.spread,
                        bid_depth_5 = EXCLUDED.bid_depth_5,
                        ask_depth_5 = EXCLUDED.ask_depth_5,
                        bid_depth_10 = EXCLUDED.bid_depth_10,
                        ask_depth_10 = EXCLUDED.ask_depth_10,
                        raw_levels = EXCLUDED.raw_levels
                    """
                ),
                [
                    {
                        "ex": r[0], "sym": r[1], "ts": r[2], "bb": r[3], "ba": r[4],
                        "sp": r[5], "bd5": r[6], "ad5": r[7], "bd10": r[8], "ad10": r[9],
                        "raw": r[11],
                    }
                    for r in rows
                ],
            )
            db.commit()
        _touch_heartbeat("orderbook", ok, len(symbols) * 3 - ok, {"sample": symbols[:8]})
    except Exception as e:
        logger.warning(f"[Persist] 盘口落盘失败: {e}")


def persist_market(result: Dict[str, Any], symbols: List[str]) -> None:
    """OI/费率 → market_asset_metrics（exchange/symbol/timestamp 幂等 upsert）。"""
    now = _now_sec()
    rows = []
    ok = 0
    for sym in symbols:
        item = result.get(sym) or {}
        for venue, v in (item.get("venues") or {}).items():
            if not v or not v.get("available"):
                continue
            rows.append(
                (
                    venue, sym, now, v.get("open_interest"), v.get("funding_rate"),
                    v.get("price"), None, v.get("price"), None, None,
                )
            )
            ok += 1
    if not rows:
        _touch_heartbeat("market", 0, len(symbols), {"sample": symbols[:8]})
        return
    try:
        with MarketSessionLocal() as db:
            db.execute(
                _sa_text(
                    """
                    INSERT INTO market_asset_metrics
                        (exchange, symbol, timestamp, open_interest, funding_rate,
                         mark_price, oracle_price, mid_price, premium, day_notional_volume)
                    VALUES
                        (:ex, :sym, :ts, :oi, :fr, :mp, NULL, :mid, NULL, NULL)
                    ON CONFLICT (exchange, symbol, timestamp) DO UPDATE SET
                        open_interest = EXCLUDED.open_interest,
                        funding_rate = EXCLUDED.funding_rate,
                        mark_price = EXCLUDED.mark_price,
                        mid_price = EXCLUDED.mid_price
                    """
                ),
                [
                    {"ex": r[0], "sym": r[1], "ts": r[2], "oi": r[3], "fr": r[4], "mp": r[5], "mid": r[6]}
                    for r in rows
                ],
            )
            db.commit()
        _touch_heartbeat("market", ok, len(symbols) * 3 - ok, {"sample": symbols[:8]})
    except Exception as e:
        logger.warning(f"[Persist] OI/费率落盘失败: {e}")


def persist_whale(result: Dict[str, Any], symbols: List[str]) -> None:
    """鲸鱼/大单 → whale_activities（每 venue/symbol 一行，写入后按天清理旧数据）。"""
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    ok = 0
    for sym in symbols:
        item = result.get(sym) or {}
        for venue, v in (item.get("venues") or {}).items():
            if not v or not v.get("available"):
                continue
            buy = v.get("whale_buy_usd", 0) or 0
            sell = v.get("whale_sell_usd", 0) or 0
            direction = "buy" if buy >= sell else "sell"
            rows.append(
                (
                    "aggregate_whale", sym, direction, (buy + sell) or 0,
                    venue, venue, "cex", None,
                    item.get("direction"),
                    now_ts,
                )
            )
            ok += 1
    try:
        with MarketSessionLocal() as db:
            if rows:
                db.execute(
                    _sa_text(
                        """
                        INSERT INTO whale_activities
                            (activity_type, symbol, direction, amount_usd,
                             from_entity, to_entity, blockchain, tx_hash,
                             signal_direction, timestamp)
                        VALUES
                            (:at, :sym, :dir, :amt, :fr, :to, :chain, NULL, :sd, :ts)
                        """
                    ),
                    [
                        {"at": r[0], "sym": r[1], "dir": r[2], "amt": r[3],
                         "fr": r[4], "to": r[5], "chain": r[6], "sd": r[8], "ts": r[9]}
                        for r in rows
                    ],
                )
            # 清理 7 天前的聚合鲸鱼记录，防止仓库无限膨胀
            db.execute(
                _sa_text(
                    "DELETE FROM whale_activities WHERE activity_type='aggregate_whale' "
                    "AND timestamp < now() - interval '7 days'"
                )
            )
            db.commit()
        _touch_heartbeat("whale", ok, len(symbols) * 3 - ok, {"sample": symbols[:8]})
    except Exception as e:
        logger.warning(f"[Persist] 鲸鱼落盘失败: {e}")
