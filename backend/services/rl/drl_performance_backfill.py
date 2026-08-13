"""
DRL 影子预测结果回填 — 打通「预测 → 结果 → 准确率」闭环（2026-08-09）。

背景：2026-06-11 DRL 因「1722 条影子预测 is_correct 从未回填，准确率统计失真」
被下线。本模块补齐回填：影子预测（DRLPerformance.predicted_direction）在预测
时点后验证窗（默认 5 根 1h bar）落定后，用同期 K 线收益回填
actual_direction / actual_pnl / is_correct，供 `SystemCoordinator._should_retrain_drl`
恢复准确率判据使用。

回填规则：
- 预测使用 1h K 线（SystemCoordinator._build_observation 同源），验证窗 horizon=5
  根 1h（约 5 小时）后，取 close 相对预测时 close 的收益 fwd_ret。
- actual_direction = sign(fwd_ret)；|fwd_ret| 极小（<1e-9）视为无行情 → 跳过。
- is_correct = sign(predicted_direction) == sign(actual_direction)，仅当
  |predicted_direction| >= 0.2（与 get_drl_advice 的 hold 阈值对齐）才判定，
  否则视为 hold 预测，is_correct 置 NULL 但计入已回填（防 hold 稀释准确率）。
- actual_pnl = fwd_ret（参考值，未考虑手续费）。

零风险：DB 只更新 is_correct IS NULL 的行；异常只记日志不抛出。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 验证窗：预测后多少根 1h bar 判定结果（与 _build_observation 的 1h 周期对齐）
BACKFILL_HORIZON_BARS = 5
# 判定方向的最小预测强度（与 get_drl_advice hold 阈值对齐）
DIRECTION_MIN_ABS = 0.2
# 单批最大处理条数（避免一次 tick 拖太久）
BACKFILL_BATCH_LIMIT = 500


def _fwd_return_at(
    klines: List[Any],
    ts: datetime,
    horizon: int = BACKFILL_HORIZON_BARS,
) -> float:
    """在按时间升序的 1h K 线列表中，找 ts 之后第 horizon 根 close 相对 ts 时 close 的收益。

    ts 所在根为基准 close；若不足 horizon 根 → 返回 None 表示未到验证期。
    """
    ts_naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
    base_close = None
    idx_base = -1
    for i, k in enumerate(klines):
        kt = k[0]  # 已预取为 (timestamp, close_price) 元组
        # crypto_klines.timestamp 为 epoch 秒（Integer）；统一转 naive UTC 再比较
        if isinstance(kt, (int, float)):
            kt = datetime.fromtimestamp(kt, tz=timezone.utc).replace(tzinfo=None)
        elif kt.tzinfo is not None:
            kt = kt.replace(tzinfo=None)
        if kt >= ts_naive:
            base_close = float(k[1])
            idx_base = i
            break
    if base_close is None or idx_base < 0:
        return None
    if idx_base + horizon >= len(klines):
        return None
    fwd_close = float(klines[idx_base + horizon][1])
    if base_close <= 0:
        return None
    return fwd_close / base_close - 1.0


def backfill_pending(
    db,
    *,
    limit: int = BACKFILL_BATCH_LIMIT,
    horizon: int = BACKFILL_HORIZON_BARS,
) -> int:
    """回填未判定（is_correct IS NULL）的影子预测。返回成功回填条数。

    db 为主库（alpha_arena，drl_performance 所在）；K 线预取走市场库
    （alpha_market，crypto_klines 所在）独立连接——多库分层，避免跨库查询。
    """
    from backend.database.models import DRLPerformance

    rows = (
        db.query(DRLPerformance)
        .filter(
            DRLPerformance.is_correct.is_(None),
            # 已回填但无方向的 hold 记录（actual_direction 非空）不重复处理
            DRLPerformance.actual_direction.is_(None),
        )
        .order_by(DRLPerformance.timestamp.asc())
        .limit(limit)
        .all()
    )
    if not rows:
        return 0

    # 按 symbol 预取 1h K 线（升序，市场库独立连接，避免跨库），避免逐条查询
    symbols = sorted({r.symbol for r in rows})
    kline_cache: Dict[str, List[Any]] = {}
    market_db = None
    try:
        from backend.database.connection import MarketSessionLocal
        from backend.database.models import CryptoKline

        market_db = MarketSessionLocal()
        for sym in symbols:
            ks = (
                market_db.query(CryptoKline)
                .filter(CryptoKline.symbol == sym, CryptoKline.period == "1h")
                .order_by(CryptoKline.timestamp.asc())
                .all()
            )
            # 抽取 (timestamp, close_price) 元组，session 关闭后仍可安全访问
            kline_cache[sym] = [(float(k.timestamp), float(k.close_price)) for k in ks]
    except Exception as exc:
        logger.warning("[DRLBackfill] 市场库 K 线预取失败: %s", exc)
        return 0
    finally:
        if market_db is not None:
            market_db.close()

    backfilled = 0
    skipped_no_data = 0
    for r in rows:
        klines = kline_cache.get(r.symbol) or []
        if not klines:
            skipped_no_data += 1
            continue
        fwd = _fwd_return_at(klines, r.timestamp, horizon=horizon)
        if fwd is None:
            # 未到验证期，留待下一批
            continue
        if abs(fwd) < 1e-9:
            skipped_no_data += 1
            continue
        r.actual_direction = 1.0 if fwd > 0 else -1.0
        r.actual_pnl = float(fwd)
        pred = float(r.predicted_direction or 0.0)
        if abs(pred) >= DIRECTION_MIN_ABS:
            r.is_correct = (pred > 0) == (r.actual_direction > 0)
        else:
            # hold 预测不计入正确率（保留 NULL），但视为已回填
            r.is_correct = None
        backfilled += 1

    db.commit()
    if backfilled or skipped_no_data:
        logger.info(
            "[DRLBackfill] 回填 %d 条（未到验证期跳过 %d，无K线 %d）",
            backfilled,
            len(rows) - backfilled - skipped_no_data,
            skipped_no_data,
        )
    return backfilled


def accuracy_summary(db, days: int = 7) -> Dict[str, Any]:
    """统计近 days 天的 DRL 预测回填率与方向准确率。

    Returns:
        {
            "total": 预测总数,
            "backfilled": 已回填数,
            "backfill_rate": 回填率,
            "decided": 参与准确率判定的条数（|pred|>=0.2 且 is_correct 非空）,
            "correct": 判定正确数,
            "accuracy": 准确率（decided 内），无判定样本为 None,
        }
    """
    from backend.database.models import DRLPerformance

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(DRLPerformance)
        .filter(DRLPerformance.timestamp >= cutoff.replace(tzinfo=None))
        .all()
    )
    total = len(rows)
    backfilled = sum(1 for r in rows if r.actual_direction is not None)
    decided = [r for r in rows if r.is_correct is not None]
    correct = sum(1 for r in decided if r.is_correct)
    return {
        "total": total,
        "backfilled": backfilled,
        "backfill_rate": round(backfilled / total, 4) if total else 0.0,
        "decided": len(decided),
        "correct": correct,
        "accuracy": round(correct / len(decided), 4) if decided else None,
    }
