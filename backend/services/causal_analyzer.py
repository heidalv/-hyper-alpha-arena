"""
Causal Analyzer — 亏损因果分析引擎 (F3-2)

诊断每笔亏损的根因，区分三种情况：
1. "策略错误" — 市场状态与策略 best_regime 匹配但仍亏损
2. "市场不可交易" — 市场状态与策略 avoid_regime 匹配
3. "未知风险" — 市场状态超出策略预设的所有类型

输出：根因分类 + 可执行的改进建议
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LossRootCause(Enum):
    """亏损根因分类"""
    STRATEGY_ERROR = "strategy_error"          # 策略本身问题
    UNTRADABLE_MARKET = "untradable_market"     # 市场不可交易
    UNKNOWN_RISK = "unknown_risk"               # 未预见风险
    ADVERSE_SLIPPAGE = "adverse_slippage"       # 滑点/执行问题
    REGIME_SHIFT = "regime_shift"               # 市场状态突变
    OVER_TRADING = "over_trading"               # 过度交易
    INSUFFICIENT_DATA = "insufficient_data"     # 数据不足无法判断


@dataclass
class LossDiagnosis:
    """单笔亏损诊断结果"""
    trade_id: str = ""
    symbol: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0

    # 市场上下文
    regime_at_entry: str = "unknown"
    regime_at_exit: str = "unknown"
    adx_at_entry: float = 0.0
    volatility_ratio: float = 1.0

    # 策略上下文
    strategy_best_regimes: List[str] = field(default_factory=list)
    strategy_avoid_regimes: List[str] = field(default_factory=list)

    # 诊断结果
    root_cause: LossRootCause = LossRootCause.INSUFFICIENT_DATA
    confidence: float = 0.0
    explanation: str = ""
    suggestions: List[str] = field(default_factory=list)

    diagnosed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class BatchDiagnosis:
    """批量诊断汇总"""
    total_losses: int = 0
    total_pnl: float = 0.0
    by_cause: Dict[str, int] = field(default_factory=dict)
    by_cause_pnl: Dict[str, float] = field(default_factory=dict)
    top_suggestions: List[str] = field(default_factory=list)
    worst_regimes: List[Tuple[str, int]] = field(default_factory=list)
    diagnoses: List[LossDiagnosis] = field(default_factory=list)


class CausalAnalyzer:
    """亏损因果分析引擎

    分析逻辑:
    1. 市场状态与策略 best_regime 匹配但仍亏损 → STRATEGY_ERROR
    2. 市场状态与策略 avoid_regime 匹配 → UNTRADABLE_MARKET
    3. 市场状态不在策略任何预设类型中 → UNKNOWN_RISK
    4. ADX>40 且波动率突增 → REGIME_SHIFT
    5. 同币种当日已多次交易 → OVER_TRADING
    """

    # 阈值
    HIGH_ADX_THRESHOLD = 40
    REGIME_SHIFT_VOL_RATIO = 2.0
    OVER_TRADING_DAILY_THRESHOLD = 5

    def diagnose_loss(
        self,
        db: Session,
        trade: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None,
    ) -> LossDiagnosis:
        """诊断单笔亏损的根因"""
        pnl = float(trade.get("pnl", trade.get("realized_pnl", 0)))
        symbol = str(trade.get("symbol", "?"))
        strategy_id = str(trade.get("strategy_id", ""))

        diagnosis = LossDiagnosis(
            trade_id=str(trade.get("id", trade.get("decision_id", ""))),
            symbol=symbol,
            pnl=pnl,
            pnl_pct=float(trade.get("pnl_pct", 0)),
            regime_at_entry=str(
                trade.get("regime_at_entry")
                or trade.get("market_regime")
                or "unknown"
            ),
            regime_at_exit=str(
                trade.get("regime_at_exit")
                or market_context.get("regime", "unknown")
                if market_context
                else "unknown"
            ),
        )

        # 获取策略记忆中的最佳/应避免状态
        try:
            from backend.database.models import StrategyMemory
            memory = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if memory:
                if memory.successful_patterns:
                    regimes = Counter(
                        p.get("regime")
                        for p in memory.successful_patterns
                        if p.get("regime")
                    )
                    diagnosis.strategy_best_regimes = [
                        r for r, _ in regimes.most_common(3)
                    ]
                if memory.failed_patterns:
                    regimes = Counter(
                        p.get("regime")
                        for p in memory.failed_patterns
                        if p.get("regime")
                    )
                    diagnosis.strategy_avoid_regimes = [
                        r for r, _ in regimes.most_common(3)
                    ]
        except Exception as e:
            logger.debug(f"[CausalAnalyzer] 获取策略记忆失败 {strategy_id}: {e}")

        # 获取市场上下文
        ctx = market_context or {}
        vol_ratio = float(ctx.get("volatility_ratio", 1.0))
        adx = float(ctx.get("adx", 0))
        diagnosis.adx_at_entry = adx
        diagnosis.volatility_ratio = vol_ratio

        entry_regime = diagnosis.regime_at_entry

        # ── 根因判定 ──

        # Rule 1: 市场突变检测
        if adx >= self.HIGH_ADX_THRESHOLD and vol_ratio >= self.REGIME_SHIFT_VOL_RATIO:
            diagnosis.root_cause = LossRootCause.REGIME_SHIFT
            diagnosis.confidence = 0.85
            diagnosis.explanation = (
                f"高ADX({adx:.0f}) + 波动率突增({vol_ratio:.1f}x中位数) "
                f"→ 市场状态突变导致策略失效"
            )
            diagnosis.suggestions = [
                f"ADX>{self.HIGH_ADX_THRESHOLD}时暂停{diagnosis.symbol}趋势类策略",
                f"波动率超{self.REGIME_SHIFT_VOL_RATIO}x时仓位降至50%",
            ]

        # Rule 2: 已知不可交易状态
        elif entry_regime in diagnosis.strategy_avoid_regimes:
            diagnosis.root_cause = LossRootCause.UNTRADABLE_MARKET
            diagnosis.confidence = 0.75
            diagnosis.explanation = (
                f"市场状态 '{entry_regime}' 是策略已知的 avoid_regime "
                f"→ 市场不可交易期"
            )
            diagnosis.suggestions = [
                f"在 {entry_regime} 状态下完全暂停 {symbol} 交易",
                f"将 {entry_regime} 加入策略的硬过滤条件",
            ]

        # Rule 3: 过度交易
        elif self._count_daily_trades_for_symbol(db, symbol) >= self.OVER_TRADING_DAILY_THRESHOLD:
            diagnosis.root_cause = LossRootCause.OVER_TRADING
            diagnosis.confidence = 0.70
            diagnosis.explanation = (
                f"{symbol} 当日已交易 ≥{self.OVER_TRADING_DAILY_THRESHOLD}次 "
                f"→ 过度交易导致决策质量下降"
            )
            diagnosis.suggestions = [
                f"限制 {symbol} 日交易次数 ≤{self.OVER_TRADING_DAILY_THRESHOLD - 1}",
                "提高入场置信度门槛（conf≥0.60）",
            ]

        # Rule 4: 策略在最佳状态仍亏损 → 策略问题
        elif entry_regime in diagnosis.strategy_best_regimes:
            diagnosis.root_cause = LossRootCause.STRATEGY_ERROR
            diagnosis.confidence = 0.65
            diagnosis.explanation = (
                f"市场状态 '{entry_regime}' 是策略的 best_regime 但仍亏损 "
                f"→ 策略参数或逻辑需调整"
            )
            diagnosis.suggestions = [
                f"检查 {symbol} 策略在 {entry_regime} 状态下的参数配置",
                "触发该策略的局部回测（最近30天）",
                "考虑提高 TP/SL 比或调整入场延迟",
            ]

        # Rule 5: 未知风险
        else:
            diagnosis.root_cause = LossRootCause.UNKNOWN_RISK
            diagnosis.confidence = 0.4
            diagnosis.explanation = (
                f"市场状态 '{entry_regime}' 不在策略预设类型中 "
                f"(best={diagnosis.strategy_best_regimes}, "
                f"avoid={diagnosis.strategy_avoid_regimes})"
            )
            diagnosis.suggestions = [
                f"记录 {entry_regime} 状态特征用于未来模式识别",
                "收集更多该状态下的交易样本",
            ]

        logger.info(
            f"[CausalAnalyzer] {diagnosis.symbol} pnl={pnl:.2f} "
            f"cause={diagnosis.root_cause.value} "
            f"conf={diagnosis.confidence:.0%} regime={entry_regime}"
        )
        return diagnosis

    def diagnose_batch(
        self,
        db: Session,
        recent_losses: List[Dict[str, Any]],
        market_contexts: Optional[Dict[str, Dict]] = None,
    ) -> BatchDiagnosis:
        """批量诊断最近的亏损交易，输出汇总报告"""
        summary = BatchDiagnosis()
        summary.total_losses = len(recent_losses)

        for trade in recent_losses:
            pnl = float(trade.get("pnl", trade.get("realized_pnl", 0)))
            summary.total_pnl += pnl

            ctx = None
            if market_contexts:
                symbol = str(trade.get("symbol", ""))
                ctx = market_contexts.get(symbol)

            diagnosis = self.diagnose_loss(db, trade, ctx)
            summary.diagnoses.append(diagnosis)

            cause = diagnosis.root_cause.value
            summary.by_cause[cause] = summary.by_cause.get(cause, 0) + 1
            summary.by_cause_pnl[cause] = (
                summary.by_cause_pnl.get(cause, 0.0) + pnl
            )

        # 收集最频繁的亏损市场状态
        regime_losses = Counter(
            d.regime_at_entry for d in summary.diagnoses
            if d.regime_at_entry != "unknown"
        )
        summary.worst_regimes = regime_losses.most_common(5)

        # 汇总 top 建议
        all_suggestions: List[str] = []
        for d in summary.diagnoses:
            all_suggestions.extend(d.suggestions)
        suggestion_counts = Counter(all_suggestions)
        summary.top_suggestions = [
            s for s, _ in suggestion_counts.most_common(5)
        ]

        logger.info(
            f"[CausalAnalyzer] 批量诊断完成: {summary.total_losses}笔亏损 "
            f"总计${summary.total_pnl:.2f} "
            f"主要根因: {summary.by_cause}"
        )
        return summary

    def _count_daily_trades_for_symbol(self, db: Session, symbol: str) -> int:
        """统计某币种今日交易次数"""
        try:
            from sqlalchemy import text as _t
            result = db.execute(
                _t("""
                    SELECT COUNT(*) FROM ai_decision_logs
                    WHERE symbol = :sym
                      AND executed = 'true'
                      AND operation IN ('buy', 'sell')
                      AND decision_time >= CURRENT_DATE
                """),
                {"sym": symbol}
            )
            row = result.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0


# 模块级单例
causal_analyzer = CausalAnalyzer()
