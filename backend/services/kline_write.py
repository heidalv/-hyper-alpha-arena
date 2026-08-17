"""
K 线统一写入口 — 全项目 crypto_klines 的唯一写入通道。

[2026-08-15 P0-5 修复]
    此前存在两条写路径且语义相反：
      - kline_data_service._insert_kline_data_sync：insert_on_conflict_do_nothing（首写者胜）
      - repositories/kline_repo.save_kline_data：insert_on_conflict_do_update（后写者胜）
    且写前无 NaN 清洗、无时间戳校验、写失败静默丢数据。
    本模块收敛为单一 upsert_klines()：
      - ON CONFLICT DO UPDATE（后写者胜）——成形 bar 滚动校正与 kline_quality_repair
        收盘后校正都需要覆盖语义；
      - 写前清洗：OHLC 必须有限且 >0、timestamp 必须为合法 epoch 秒（1e9 < ts < now+24h）；
      - 批量 executemany；统计 rejected 数量并打 error 日志（供监控）。
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.database.dialect import dialect

logger = logging.getLogger(__name__)

# crypto_klines 唯一约束列
UNIQUE_COLS = "exchange, symbol, market, period, timestamp, environment"

ALL_COLUMNS = (
    "exchange, symbol, market, period, timestamp, datetime_str, environment, "
    "open_price, high_price, low_price, close_price, volume, amount, change, percent"
)
ALL_PLACEHOLDERS = (
    ":exchange, :symbol, :market, :period, :timestamp, :datetime_str, :environment, "
    ":open_price, :high_price, :low_price, :close_price, :volume, :amount, :change, :percent"
)
UPDATE_COLS = (
    "datetime_str, open_price, high_price, low_price, close_price, "
    "volume, amount, change, percent"
)


def _finite_or_none(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def sanitize_kline_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """校验并清洗一行 K 线；非法返回 None（调用方计数并告警）。

    规则：
      - timestamp 必须为合法 epoch 秒（1e9 < ts < now + 24h），非法（毫秒/负数/未来）拒绝；
      - OHLC 四项必须有限且 > 0，否则拒绝；
      - volume/amount/change/percent 非法值置 None（字段可空，不因此丢整行）。
    """
    ts = row.get("timestamp")
    if ts is None:
        return None
    try:
        ts_i = int(ts)
    except (TypeError, ValueError):
        return None
    now = time.time()
    if not (1e9 < ts_i < now + 86400):
        logger.warning(
            "[KlineWrite] 非法时间戳拒绝写入: %s ts=%r", row.get("symbol"), ts,
        )
        return None

    o = _finite_or_none(row.get("open_price"))
    h = _finite_or_none(row.get("high_price"))
    l = _finite_or_none(row.get("low_price"))
    c = _finite_or_none(row.get("close_price"))
    if o is None or h is None or l is None or c is None or o <= 0 or h <= 0 or l <= 0 or c <= 0:
        logger.warning(
            "[KlineWrite] OHLC 非法拒绝写入: %s ts=%s o=%r h=%r l=%r c=%r",
            row.get("symbol"), ts_i, row.get("open_price"), row.get("high_price"),
            row.get("low_price"), row.get("close_price"),
        )
        return None

    ts_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts_i))
    return {
        "exchange": str(row.get("exchange") or ""),
        "symbol": str(row.get("symbol") or "").upper(),
        "market": str(row.get("market") or "CRYPTO"),
        "period": str(row.get("period") or ""),
        "timestamp": ts_i,
        "datetime_str": row.get("datetime_str") or ts_dt,
        "environment": str(row.get("environment") or "mainnet"),
        "open_price": o,
        "high_price": h,
        "low_price": l,
        "close_price": c,
        "volume": _finite_or_none(row.get("volume")),
        "amount": _finite_or_none(row.get("amount")),
        "change": _finite_or_none(row.get("change")),
        "percent": _finite_or_none(row.get("percent")),
    }


def upsert_klines(db, rows: List[Dict[str, Any]], batch_size: int = 500) -> Dict[str, int]:
    """统一 K 线写入：清洗 → 批量 ON CONFLICT DO UPDATE（后写者胜）。

    Returns: {"total": 传入行数, "written": 成功写入, "rejected": 清洗拒绝数}
    写失败向上抛异常（由调用方决定回滚/重试），不再静默丢数据。
    """
    total = len(rows or [])
    clean: List[Dict[str, Any]] = []
    rejected = 0
    for row in rows or []:
        s = sanitize_kline_row(row)
        if s is None:
            rejected += 1
            continue
        clean.append(s)
    if rejected:
        logger.error("[KlineWrite] 本批拒绝 %d/%d 行（NaN/非法时间戳）", rejected, total)

    if not clean:
        return {"total": total, "written": 0, "rejected": rejected}

    upsert_sql = text(dialect.insert_on_conflict_do_update(
        "crypto_klines", ALL_COLUMNS, ALL_PLACEHOLDERS, UNIQUE_COLS, UPDATE_COLS,
    ))

    written = 0
    for start in range(0, len(clean), batch_size):
        batch = clean[start:start + batch_size]
        db.execute(upsert_sql, batch)
        written += len(batch)
    return {"total": total, "written": written, "rejected": rejected}
