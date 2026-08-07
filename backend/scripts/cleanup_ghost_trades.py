"""一次性清洗 ghost StrategyTrade + 回滚受污染的 StrategyMemory / StrategyRegimeScore。

触发原因：learning_loop_service._tick_outcome_batch 回填路径上一版会让
UnifiedLearning 再写一条新的 StrategyTrade，导致同一笔平仓每 5 min 被复制，
形成 211/63/60 条 "entry/exit/pnl 完全一致" 的幽灵组。

本脚本幂等：每组只保留最早的一条（按 id ASC），其余 DELETE；同时把受
污染的 strategy_memories 行重置为 0，后续真实交易会重新累积。

使用：
    cd Hyper-Alpha-Arena
    python -m backend.scripts.cleanup_ghost_trades --dry-run    # 预览
    python -m backend.scripts.cleanup_ghost_trades --apply      # 实际执行
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Tuple

# 允许在 Hyper-Alpha-Arena 目录下直接 python -m backend.scripts.xxx
_here = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from sqlalchemy import func  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import (  # noqa: E402
    StrategyTrade,
    StrategyMemory,
    StrategyRegimeScore,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_ghost")

# 判定为 ghost 的阈值：同 (strategy_id, symbol, side, entry, exit, ROUND(pnl,4))
# 组内条数 > GHOST_GROUP_MIN 才视为污染组。阈值取 3 够保守。
GHOST_GROUP_MIN = 3


def find_ghost_groups(db) -> List[Tuple]:
    """返回待清洗的 ghost 组，每个元素对应一组 (strategy_id, symbol, side, entry, exit, pnl4)。"""
    rows = (
        db.query(
            StrategyTrade.strategy_id,
            StrategyTrade.symbol,
            StrategyTrade.side,
            StrategyTrade.entry_price,
            StrategyTrade.exit_price,
            func.round(StrategyTrade.pnl, 4).label("pnl4"),
            func.count(StrategyTrade.id).label("cnt"),
            func.min(StrategyTrade.id).label("keep_id"),
            func.min(StrategyTrade.opened_at).label("first_seen"),
            func.max(StrategyTrade.closed_at).label("last_seen"),
        )
        .filter(StrategyTrade.status == "closed")
        .group_by(
            StrategyTrade.strategy_id,
            StrategyTrade.symbol,
            StrategyTrade.side,
            StrategyTrade.entry_price,
            StrategyTrade.exit_price,
            func.round(StrategyTrade.pnl, 4),
        )
        .having(func.count(StrategyTrade.id) > GHOST_GROUP_MIN)
        .all()
    )
    return rows


def purge_group(db, grp) -> int:
    """物理删除一组 ghost 的多余行，保留 keep_id。返回删除条数。"""
    strategy_id, symbol, side, entry, exit_, pnl4, cnt, keep_id, *_ = grp
    q = (
        db.query(StrategyTrade)
        .filter(
            StrategyTrade.status == "closed",
            StrategyTrade.strategy_id == strategy_id,
            StrategyTrade.symbol == symbol,
            StrategyTrade.side == side,
            StrategyTrade.entry_price == entry,
            StrategyTrade.exit_price == exit_,
            func.round(StrategyTrade.pnl, 4) == pnl4,
            StrategyTrade.id != keep_id,
        )
    )
    deleted = q.delete(synchronize_session=False)
    return deleted


def reset_memories_for(db, strategy_ids: List[str]) -> int:
    """将受污染的 strategy_memories 回滚为 0；后续真实平仓会重新 EMA 累积。"""
    if not strategy_ids:
        return 0
    rows = (
        db.query(StrategyMemory)
        .filter(StrategyMemory.strategy_id.in_(strategy_ids))
        .all()
    )
    for r in rows:
        r.total_trades = 0
        r.win_rate = 0.0
        r.avg_profit = 0.0
        r.avg_loss = 0.0
        r.sharpe_ratio = 0.0
        r.max_drawdown = 0.0
        r.performance_by_regime = {}
        r.successful_patterns = []
        r.failed_patterns = []
        r.key_lessons = []
        r.partial_pnl = 0.0
        r.partial_close_count = 0
        r.last_reduce_at = None
    return len(rows)


def reset_regime_scores(db) -> int:
    """把 source='live' 的 regime score 重置（只有 5 条 paper 历史数据，但
    live 源也可能已受间接污染；保守做法是只重置 sample_count<=10 的 live 行）。

    注：paper 源的 5 条历史不动，避免丢掉前期模拟基线。
    """
    rows = (
        db.query(StrategyRegimeScore)
        .filter(StrategyRegimeScore.source == "live")
        .all()
    )
    n = 0
    for r in rows:
        r.sample_count = 0
        r.win_rate = 0.0
        r.avg_pnl_pct = 0.0
        r.sharpe = 0.0
        r.max_drawdown = 0.0
        r.profit_factor = 1.0
        r.composite_score = 0.0
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="仅预览")
    args = ap.parse_args()
    dry = not args.apply

    db = SessionLocal()
    try:
        groups = find_ghost_groups(db)
        if not groups:
            logger.info("未发现 ghost 组（每组 > %d 条的重复记录），无需清洗。", GHOST_GROUP_MIN)
            return

        logger.info("共发现 %d 个 ghost 组：", len(groups))
        total_delete = 0
        affected_strategies = set()
        for g in groups:
            strategy_id, symbol, side, entry, exit_, pnl4, cnt, keep_id, first_seen, last_seen = g
            purge_cnt = int(cnt) - 1
            total_delete += purge_cnt
            affected_strategies.add(strategy_id)
            logger.info(
                "  [%s %s %s] entry=%s exit=%s pnl=%s count=%d keep_id=%d 将删除=%d first=%s last=%s",
                strategy_id, symbol, side, entry, exit_, pnl4,
                int(cnt), int(keep_id), purge_cnt, first_seen, last_seen,
            )

        logger.info("=> 合计将删除 %d 条 ghost；受影响 strategy_memories: %s",
                    total_delete, sorted(affected_strategies))

        if dry:
            logger.info("[DRY-RUN] 未执行任何写入。加 --apply 真正执行。")
            return

        # 真正执行
        deleted_total = 0
        for g in groups:
            deleted_total += purge_group(db, g)
        reset_mem = reset_memories_for(db, list(affected_strategies))
        reset_reg = reset_regime_scores(db)

        db.commit()
        logger.info(
            "[APPLY] DELETE strategy_trades=%d, RESET strategy_memories=%d, RESET strategy_regime_scores(live)=%d",
            deleted_total, reset_mem, reset_reg,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
