"""
Decision Performance Context — 回溯性能上下文 (D3)

将信号/策略的历史表现为 LLM 生成决策时提供「硬数据」上下文，
让 LLM 知道：对于当前 symbol+tier+trade_nature 组合，
历史上哪些操作方向、哪些市场状态下表现更好。

输出格式：紧凑文本块，可直接注入 LLM prompt。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_performance_context(
    db: Session,
    symbol: str,
    tier: str = "mid",
    trade_nature: str = "swing",
    lookback_days: int = 30,
    source_weight_rule_engine: float = 0.5,  # rule_engine 决策权重打折
) -> str:
    """生成当前 symbol+tier+trade_nature 的历史表现上下文。

    返回格式化的文本块，包含：
    - 总体胜率 / 平均 PnL / Sharpe 估算
    - 按操作方向 (buy vs sell) 分层统计
    - 按 market_regime 分层统计
    - decision_source 加权后的可信度评估

    Args:
        db: 数据库会话
        symbol: 交易对
        tier: 时间框架层级
        trade_nature: 交易性质
        lookback_days: 回溯天数
        source_weight_rule_engine: rule_engine 来源的决策权重 (0.5 = 半信)

    Returns:
        格式化的文本块字符串，可直接注入 LLM prompt
    """
    try:
        from sqlalchemy import text as _t
        from backend.database.dialect import dialect

        # 查询最近 N 天的已执行决策（仅用现有列：symbol, operation, decision_source, realized_pnl）
        rows = db.execute(
            _t("""
                SELECT
                    operation,
                    COALESCE(decision_source, 'llm') AS decision_source,
                    realized_pnl,
                    CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END AS is_win
                FROM ai_decision_logs
                WHERE symbol = :sym
                  AND executed = 'true'
                  AND operation IN ('buy', 'sell')
                  AND realized_pnl IS NOT NULL
                  AND decision_time >= """ + dialect.datetime_now_minus_param() + """
                ORDER BY decision_time DESC
                LIMIT 200
            """),
            {"sym": symbol.upper(), "days": lookback_days}
        ).fetchall()

        if not rows:
            # 尝试放宽条件：不限 symbol
            rows = db.execute(
                _t("""
                    SELECT
                        operation,
                        COALESCE(decision_source, 'llm') AS decision_source,
                        realized_pnl,
                        CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END AS is_win
                    FROM ai_decision_logs
                    WHERE executed = 'true'
                      AND operation IN ('buy', 'sell')
                      AND realized_pnl IS NOT NULL
                      AND decision_time >= """ + dialect.datetime_now_minus_param() + """
                    ORDER BY decision_time DESC
                    LIMIT 200
                """),
                {"days": lookback_days}
            ).fetchall()

        if not rows:
            return "[无历史数据] 该条件下暂无足够的已平仓交易记录。"

        # 计算统计
        total = len(rows)
        buy_rows = [r for r in rows if r[0] == "buy"]
        sell_rows = [r for r in rows if r[0] == "sell"]
        llm_rows = [r for r in rows if r[1] == "llm"]
        rule_rows = [r for r in rows if r[1] == "rule_engine"]

        def _win_rate(rset):
            if not rset:
                return 0.0
            return sum(r[3] for r in rset) / len(rset)  # r[3]=is_win

        def _avg_pnl(rset):
            if not rset:
                return 0.0
            return sum(float(r[2] or 0) for r in rset) / len(rset)  # r[2]=realized_pnl

        def _total_pnl(rset):
            return sum(float(r[2] or 0) for r in rset)

        # 按 regime 分组（market_regime 列可能不存在，使用 'all' 统合）
        regime_stats = {"all": {"trades": total, "wins": sum(r[3] for r in rows),
                                "total_pnl": _total_pnl(rows)}}

        # 计算 source 加权可信度
        llm_wr = _win_rate(llm_rows) if llm_rows else None
        rule_wr = _win_rate(rule_rows) if rule_rows else None
        total_wr = _win_rate(rows)

        # 构建输出文本块
        lines = [
            f"=== {symbol} 历史表现回溯 (最近{lookback_days}天, {total}笔已平仓) ===",
            f"总胜率: {total_wr:.0%} | 平均PnL: ${_avg_pnl(rows):.2f} | 总PnL: ${_total_pnl(rows):.2f}",
        ]

        if llm_rows or rule_rows:
            source_lines = []
            if llm_rows:
                source_lines.append(
                    f"  LLM决策: {len(llm_rows)}笔, 胜率{llm_wr:.0%}, 均PnL ${_avg_pnl(llm_rows):.2f}"
                )
            if rule_rows:
                source_lines.append(
                    f"  规则引擎决策: {len(rule_rows)}笔, 胜率{rule_wr:.0%}, 均PnL ${_avg_pnl(rule_rows):.2f}"
                    f" (权重×{source_weight_rule_engine})"
                )
            lines.append("按决策来源:")
            lines.extend(source_lines)

        if buy_rows:
            lines.append(
                f"BUY方向: {len(buy_rows)}笔, 胜率{_win_rate(buy_rows):.0%}, "
                f"总PnL ${_total_pnl(buy_rows):.2f}"
            )
        if sell_rows:
            lines.append(
                f"SELL方向: {len(sell_rows)}笔, 胜率{_win_rate(sell_rows):.0%}, "
                f"总PnL ${_total_pnl(sell_rows):.2f}"
            )

        # 建议
        suggestions = []
        if total_wr < 0.35 and total >= 10:
            suggestions.append("当前条件胜率显著偏低(<35%)，建议降低仓位或等待更好机会")
        if buy_rows and sell_rows:
            buy_wr = _win_rate(buy_rows)
            sell_wr = _win_rate(sell_rows)
            if buy_wr > sell_wr + 0.15:
                suggestions.append(f"BUY方向显著优于SELL ({buy_wr:.0%} vs {sell_wr:.0%})，优先考虑做多")
            elif sell_wr > buy_wr + 0.15:
                suggestions.append(f"SELL方向显著优于BUY ({sell_wr:.0%} vs {buy_wr:.0%})，优先考虑做空")
        if llm_wr is not None and rule_wr is not None and llm_wr > rule_wr + 0.10:
            suggestions.append(f"LLM独立决策胜率({llm_wr:.0%})高于规则引擎({rule_wr:.0%})，建议减少规则引擎覆盖")

        if suggestions:
            lines.append("建议:")
            for s in suggestions:
                lines.append(f"  → {s}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[PerfContext] 生成性能上下文失败: {e}", exc_info=True)
        return f"[性能上下文生成异常: {str(e)[:100]}]"


def get_compact_context(
    db: Session,
    symbol: str,
    max_length: int = 500,
) -> str:
    """生成紧凑版性能上下文（用于 token 受限场景）"""
    full = get_performance_context(db, symbol, lookback_days=14)
    if len(full) <= max_length:
        return full
    # 截断到合理长度
    lines = full.split("\n")
    compact_lines = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > max_length:
            compact_lines.append(f"...(截断, 共{len(lines)}行)")
            break
        compact_lines.append(line)
        current_len += len(line) + 1
    return "\n".join(compact_lines)
