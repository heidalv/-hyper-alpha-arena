"""
Trade Memory Miner — 交易记忆挖掘 (D4)

从 TradeMemoryRecord 表中挖掘可操作的交易模式：
1. 盈利模式：什么 (regime, confidence_range, hold_time) 组合胜率高
2. 亏损模式：什么条件下频繁亏损
3. 最佳退出策略：什么 close_reason 效果最好
4. 最优持仓时长：按 regime+trade_nature 分层的最佳持有时间

输出：格式化的文本摘要，可注入 LLM prompt 或写回 StrategyMemory.key_lessons。
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def mine_trade_patterns(
    db: Session,
    symbol: Optional[str] = None,
    account_id: Optional[int] = None,
    lookback_days: int = 60,
    min_samples: int = 5,
    trade_nature: Optional[str] = None,
) -> Dict[str, Any]:
    """从 TradeMemoryRecord 挖掘交易模式。

    Returns:
        {
            "profitable_patterns": [...],
            "losing_patterns": [...],
            "best_close_reasons": [...],
            "optimal_hold_ranges": [...],
            "summary_text": "..."  # 可注入 LLM 的文本摘要
        }
    """
    try:
        from sqlalchemy import text as _t
        from backend.database.dialect import dialect

        conditions = ["1=1"]
        params = {"days": lookback_days, "min_samples": min_samples}

        if symbol:
            conditions.append("symbol = :symbol")
            params["symbol"] = symbol.upper()
        if account_id is not None:
            conditions.append("account_id = :aid")
            params["aid"] = account_id

        where_clause = " AND ".join(conditions)

        # 只读查询前先清场：若上游把 db session 污染成 aborted 状态（前一个
        # 写操作失败未 rollback），直接执行新 SELECT 会抛 InFailedSqlTransaction。
        # rollback 一个无未提交写操作的 session 是安全的，能让 aborted session 复活。
        try:
            db.rollback()
        except Exception:
            pass

        rows = db.execute(
            _t(f"""
                SELECT
                    symbol, side, market_regime, signal_source,
                    ROUND(confidence_at_entry * 20) * 5 AS confidence_bucket,
                    CASE
                        WHEN hold_seconds < 3600 THEN '<1h'
                        WHEN hold_seconds < 14400 THEN '1-4h'
                        WHEN hold_seconds < 43200 THEN '4-12h'
                        WHEN hold_seconds < 86400 THEN '12-24h'
                        ELSE '>24h'
                    END AS hold_bucket,
                    close_reason,
                    pnl, pnl_pct,
                    CASE WHEN pnl > 0 THEN 1 ELSE 0 END AS is_win
                FROM trade_memory_records
                WHERE {where_clause}
                  AND closed_at >= """ + dialect.datetime_now_minus_param() + """
                ORDER BY closed_at DESC
                LIMIT 500
            """),
            params,
        ).fetchall()

        if trade_nature:
            from backend.services.trade_memory_context import NATURE_FILTER_GROUPS
            _n_key = trade_nature.lower()
            if _n_key in NATURE_FILTER_GROUPS:
                if _n_key == "swing":
                    rows = [r for r in rows if r[5] in ("4-12h", "12-24h", ">24h")]
                elif _n_key in ("trend", "trend_follow"):
                    rows = [r for r in rows if r[5] in ("12-24h", ">24h")]

        if not rows:
            return {
                "profitable_patterns": [],
                "losing_patterns": [],
                "best_close_reasons": [],
                "optimal_hold_ranges": [],
                "summary_text": "[无数据] 暂无足够的交易记忆记录可供挖掘。",
                "total_records": 0,
            }

        # ── 按维度分组统计 ──
        def _group_by(rows, keys):
            groups = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
            for r in rows:
                k = tuple(r[keys.index(k)] if isinstance(keys, list) else r[keys] for k in keys) if isinstance(keys, list) else getattr(r, keys, str(r))
                if isinstance(keys, list):
                    k = tuple(r[i] for i in range(len(keys)))
                else:
                    k = r[keys]
                groups[k]["trades"] += 1
                groups[k]["wins"] += r[-1]
                groups[k]["total_pnl"] += float(r[-2] or 0)
            return groups

        # 手动实现分组（使用索引）
        # rows: symbol(0), side(1), market_regime(2), signal_source(3),
        #       confidence_bucket(4), hold_bucket(5), close_reason(6),
        #       pnl(7), pnl_pct(8), is_win(9)

        # 1. 按 (side, market_regime, confidence_bucket) 分组
        pattern_groups = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
        for r in rows:
            key = (r[1], r[2] or "unknown", r[4] or 50)  # side, regime, confidence_bucket
            pattern_groups[key]["trades"] += 1
            pattern_groups[key]["wins"] += r[9]
            pattern_groups[key]["total_pnl"] += float(r[7] or 0)

        # 2. 按 close_reason 分组
        close_groups = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
        for r in rows:
            reason = r[6] or "unknown"
            close_groups[reason]["trades"] += 1
            close_groups[reason]["wins"] += r[9]
            close_groups[reason]["total_pnl"] += float(r[7] or 0)

        # 3. 按 hold_bucket + side 分组
        hold_groups = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
        for r in rows:
            key = (r[5] or "?", r[1])  # hold_bucket, side
            hold_groups[key]["trades"] += 1
            hold_groups[key]["wins"] += r[9]
            hold_groups[key]["total_pnl"] += float(r[7] or 0)

        def _wr(g):
            return g["wins"] / g["trades"] if g["trades"] > 0 else 0

        def _avg_pnl(g):
            return g["total_pnl"] / g["trades"] if g["trades"] > 0 else 0

        # 筛选盈利模式 (WR > 50%, trades >= min_samples)
        profitable = []
        for (side, regime, conf_bucket), g in pattern_groups.items():
            if g["trades"] >= min_samples and _wr(g) >= 0.50:
                profitable.append({
                    "side": side,
                    "regime": regime,
                    "confidence_range": f"{conf_bucket}%",
                    "trades": g["trades"],
                    "win_rate": round(_wr(g), 3),
                    "avg_pnl": round(_avg_pnl(g), 4),
                    "total_pnl": round(g["total_pnl"], 2),
                })
        profitable.sort(key=lambda x: x["win_rate"] * x["trades"], reverse=True)

        # 筛选亏损模式 (WR < 35%, trades >= min_samples)
        losing = []
        for (side, regime, conf_bucket), g in pattern_groups.items():
            if g["trades"] >= min_samples and _wr(g) < 0.35:
                losing.append({
                    "side": side,
                    "regime": regime,
                    "confidence_range": f"{conf_bucket}%",
                    "trades": g["trades"],
                    "win_rate": round(_wr(g), 3),
                    "avg_pnl": round(_avg_pnl(g), 4),
                    "total_pnl": round(g["total_pnl"], 2),
                })
        losing.sort(key=lambda x: _wr(pattern_groups.get((x["side"], x["regime"], int(x["confidence_range"].rstrip("%"))), {"wins": 0})))

        # 最佳退出原因
        best_close = []
        for reason, g in close_groups.items():
            if g["trades"] >= 3:
                best_close.append({
                    "close_reason": reason,
                    "trades": g["trades"],
                    "win_rate": round(_wr(g), 3),
                    "avg_pnl": round(_avg_pnl(g), 4),
                })
        best_close.sort(key=lambda x: x["win_rate"], reverse=True)

        # 最佳持仓时长
        optimal_hold = []
        for (hold_bucket, side), g in hold_groups.items():
            if g["trades"] >= 3:
                optimal_hold.append({
                    "hold_range": hold_bucket,
                    "side": side,
                    "trades": g["trades"],
                    "win_rate": round(_wr(g), 3),
                    "avg_pnl": round(_avg_pnl(g), 4),
                })
        optimal_hold.sort(key=lambda x: x["win_rate"] * x["trades"], reverse=True)

        # ── 生成文本摘要 ──
        total = len(rows)
        total_wins = sum(r[9] for r in rows)
        overall_wr = total_wins / total if total > 0 else 0

        lines = [
            f"=== 交易记忆挖掘报告 ({symbol or '全币种'}, 最近{lookback_days}天, {total}笔) ===",
            f"总体胜率: {overall_wr:.0%} | 共计{total}笔",
            "",
        ]

        if profitable:
            lines.append(f"【盈利模式】(WR≥50%, ≥{min_samples}笔):")
            for p in profitable[:5]:
                lines.append(
                    f"  ✓ {p['side'].upper()} | {p['regime']} | conf~{p['confidence_range']} | "
                    f"{p['trades']}笔 WR={p['win_rate']:.0%} 均PnL=${p['avg_pnl']:.2f}"
                )

        if losing:
            lines.append(f"\n【亏损模式】(WR<35%, ≥{min_samples}笔) — 应避免:")
            for p in losing[:5]:
                lines.append(
                    f"  ✗ {p['side'].upper()} | {p['regime']} | conf~{p['confidence_range']} | "
                    f"{p['trades']}笔 WR={p['win_rate']:.0%} 均PnL=${p['avg_pnl']:.2f}"
                )

        if best_close:
            lines.append(f"\n【最佳退出方式】:")
            for c in best_close[:5]:
                lines.append(
                    f"  {c['close_reason']}: {c['trades']}笔 WR={c['win_rate']:.0%} "
                    f"均PnL=${c['avg_pnl']:.2f}"
                )

        if optimal_hold:
            lines.append(f"\n【最优持仓时长】:")
            for h in optimal_hold[:5]:
                lines.append(
                    f"  {h['hold_range']} ({h['side']}): {h['trades']}笔 "
                    f"WR={h['win_rate']:.0%} 均PnL=${h['avg_pnl']:.2f}"
                )

        summary = "\n".join(lines)

        return {
            "profitable_patterns": profitable[:10],
            "losing_patterns": losing[:10],
            "best_close_reasons": best_close[:5],
            "optimal_hold_ranges": optimal_hold[:5],
            "summary_text": summary,
            "total_records": total,
            "overall_win_rate": round(overall_wr, 3),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"[TradeMemoryMiner] 挖掘失败: {e}", exc_info=True)
        # 必须回滚：本函数虽只读，但 db 可能是上游复用的长生命周期 session，
        # 不 rollback 会让该 session 一直停在 aborted 状态，污染后续所有查询。
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "profitable_patterns": [],
            "losing_patterns": [],
            "best_close_reasons": [],
            "optimal_hold_ranges": [],
            "summary_text": f"[挖掘异常: {str(e)[:100]}]",
            "total_records": 0,
        }


def inject_patterns_to_memory(
    db: Session,
    strategy_id: str,
    symbol: Optional[str] = None,
) -> bool:
    """将挖掘出的模式写回 StrategyMemory.key_lessons (D4→F1-1 联动)

    2026-06-11: 按策略所属 account_id 过滤挖掘范围，
    避免把其他策略/账户的交易模式注入本策略的 key_lessons（跨策略污染）。
    """
    try:
        from backend.database.models import StrategyMemory

        # 解析策略所属账户，限定挖掘范围
        account_id = None
        try:
            from backend.database.models import AIStrategy
            strat = db.query(AIStrategy.account_id).filter(
                AIStrategy.strategy_id == str(strategy_id)
            ).first()
            if strat is not None:
                account_id = strat[0]
        except Exception as _aid_err:
            logger.debug(f"[TradeMiner] account_id 解析失败(全局挖掘): {_aid_err}")

        result = mine_trade_patterns(db, symbol=symbol, account_id=account_id)
        if not result["total_records"]:
            return False

        memory = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id
        ).first()
        if not memory:
            return False

        lessons = memory.key_lessons or []

        # 写入盈利模式
        for p in result["profitable_patterns"][:3]:
            lessons.append({
                "type": "profitable_pattern",
                "ts": datetime.now(timezone.utc).isoformat(),  # [P1-12] 统一时间戳（decay 主键）
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "side": p["side"],
                "regime": p["regime"],
                "confidence_range": p["confidence_range"],
                "win_rate": p["win_rate"],
                "trades": p["trades"],
                "source": "trade_memory_miner",
            })

        # 写入亏损模式
        for p in result["losing_patterns"][:3]:
            lessons.append({
                "type": "pattern_to_avoid",
                "ts": datetime.now(timezone.utc).isoformat(),  # [P1-12] 统一时间戳
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "side": p["side"],
                "regime": p["regime"],
                "confidence_range": p["confidence_range"],
                "win_rate": p["win_rate"],
                "trades": p["trades"],
                "source": "trade_memory_miner",
            })

        memory.key_lessons = lessons[-50:]  # 保留最近50条
        memory.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            f"[TradeMemoryMiner] 已将 {len(result['profitable_patterns'])} 个盈利模式 + "
            f"{len(result['losing_patterns'])} 个亏损模式写入 {strategy_id}"
        )
        return True

    except Exception as e:
        logger.error(f"[TradeMemoryMiner] 注入失败: {e}", exc_info=True)
        # 关键：任何写操作（db.query(StrategyMemory) / db.commit）失败都需 rollback，
        # 否则 session 进入 aborted 状态，传回调用方后会污染后续所有查询
        # （这正是日志里 trade_memory_miner InFailedSqlTransaction 的根因）。
        try:
            db.rollback()
        except Exception:
            pass
        return False
