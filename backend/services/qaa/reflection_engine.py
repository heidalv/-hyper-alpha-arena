"""
QAA ReflectionEngine — 反思反馈闭环 (Phase 3)

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §3.8.4

借鉴 TradingAgents 反思机制:
- 每笔交易结束后异步评估决策质量
- 生成反思文本注入下次 MasterController 的 prompt
- 正确决策 → 强化理由, 错误决策 → 提取教训

与现有系统集成:
- LearningBus.dispatch() 触发反思
- 结果写入 EpisodicMemory (Layer C)
- 教训注入 MasterController prompt
"""

from __future__ import annotations

import logging
import uuid
import warnings
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

warnings.warn(
    "qaa.reflection_engine 已弃用，功能已由 UnifiedLearningService._generate_key_lessons() "
    "+ StrategyMemory.key_lessons 替代。请勿在新代码中使用此模块。",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeOutcome:
    """交易结果"""
    symbol: str
    action: str                     # "buy" / "sell"
    tier: str = "mid"
    entry_price: float = 0
    exit_price: float = 0
    realized_pnl: float = 0
    realized_pnl_pct: float = 0
    hold_duration_sec: float = 0
    exit_reason: str = ""           # "tp" / "sl" / "ai_close" / "manual"
    market_regime_at_exit: str = ""


class ReflectionEngine:
    """反思引擎 — 每笔交易结束后异步评估决策质量

    工作流:
    1. 交易结束 → 接收 TradeOutcome
    2. 评估决策质量 (正确/错误/部分正确)
    3. 生成反思文本
    4. 写入 EpisodicMemory (Layer C)
    5. 下次 tick 时注入 MasterController prompt

    借鉴 TradingAgents:
    - 正确决策: "Previous BUY was CORRECT. Reason: {reasoning}. Result: +{pnl}%"
    - 错误决策: "Previous BUY was WRONG. Expected: {reasoning}. Actual: -{pnl}%.
                 Consider: {market_condition_at_exit}"
    """

    def reflect(self, outcome: TradeOutcome, decision_reasoning: str = "",
                market_context_at_entry: str = "") -> str:
        """生成反思文本

        Args:
            outcome: 交易结果
            decision_reasoning: 开仓时的决策理由
            market_context_at_entry: 开仓时的市场环境

        Returns:
            反思文本 (注入下次 prompt)
        """
        symbol = outcome.symbol
        action = outcome.action
        pnl_pct = outcome.realized_pnl_pct

        if pnl_pct > 0:
            # 正确决策
            reflection = (
                f"[反思] {symbol} {action} 正确 "
                f"(盈利 {pnl_pct:+.1%}, 持仓 {outcome.hold_duration_sec/3600:.1f}h, "
                f"退出原因: {outcome.exit_reason})"
            )
            if decision_reasoning:
                reflection += f" — 决策理由有效: {decision_reasoning[:100]}"
        elif pnl_pct < -0.005:
            # 错误决策 (亏 > 0.5%)
            reflection = (
                f"[反思] {symbol} {action} 错误 "
                f"(亏损 {pnl_pct:.1%}, 持仓 {outcome.hold_duration_sec/3600:.1f}h, "
                f"退出原因: {outcome.exit_reason})"
            )
            if decision_reasoning:
                reflection += f" — 预期: {decision_reasoning[:80]}"
            if market_context_at_entry:
                reflection += f" — 入场环境: {market_context_at_entry[:80]}"
            if outcome.market_regime_at_exit:
                reflection += f" — 退出环境: {outcome.market_regime_at_exit}"

            # 提取教训
            lesson = self._extract_lesson(outcome)
            if lesson:
                reflection += f" | 教训: {lesson}"
        else:
            # 微亏/持平
            reflection = (
                f"[反思] {symbol} {action} 中性 "
                f"(PnL {pnl_pct:+.1%}, 退出: {outcome.exit_reason})"
            )

        return reflection

    def _extract_lesson(self, outcome: TradeOutcome) -> str:
        """从失败交易中提取教训"""
        lessons = []

        # SL 被扫出
        if outcome.exit_reason == "sl":
            hold_h = outcome.hold_duration_sec / 3600
            if hold_h < 0.5:
                lessons.append("开仓后很快被止损, 可能入场时机不佳或 SL 太紧")
            elif hold_h < 2:
                lessons.append("短期被止损, 注意入场价位和趋势确认")

        # AI 主动平仓
        elif outcome.exit_reason in ("ai_close", "ai_reverse"):
            lessons.append("AI 主动平仓, 可能市场环境与开仓时已变化")

        # 持仓时间过长
        if outcome.hold_duration_sec > 48 * 3600 and outcome.realized_pnl_pct < 0:
            lessons.append("长期持仓仍亏损, 应更早止损或避免此类入场")

        # 亏损过大
        if outcome.realized_pnl_pct < -0.05:
            lessons.append("单笔亏损 > 5%, 检查仓位大小和止损距离")

        return "; ".join(lessons) if lessons else ""

    def record_and_reflect(
        self,
        outcome: TradeOutcome,
        decision_reasoning: str = "",
        market_context_at_entry: str = "",
        tick_id: int = 0,
    ) -> str:
        """记录交易结果 + 生成反思 + 写入 EpisodicMemory

        Args:
            outcome: 交易结果
            decision_reasoning: 开仓时的决策理由
            market_context_at_entry: 开仓时的市场环境
            tick_id: 当前 tick 编号

        Returns:
            反思文本
        """
        from backend.services.qaa.state_layers import (
            episodic_memory, Episode,
        )

        # 生成反思
        reflection = self.reflect(outcome, decision_reasoning, market_context_at_entry)

        # 创建情景并填充结果
        episode_id = uuid.uuid4().hex[:12]
        episode = Episode(
            episode_id=episode_id,
            symbol=outcome.symbol,
            action=outcome.action,
            tier=outcome.tier,
            decision_reasoning=decision_reasoning,
            market_context=market_context_at_entry,
            tick_id=tick_id,
        )
        episodic_memory.store(episode)

        # 立即填充结果 (因为交易已结束, 结果是已知的)
        result = "profit" if outcome.realized_pnl_pct > 0 else (
            "loss" if outcome.realized_pnl_pct < -0.005 else "breakeven"
        )
        episodic_memory.fill_outcome(
            episode_id=episode_id,
            outcome=result,
            realized_pnl_pct=outcome.realized_pnl_pct,
            details=reflection,
        )

        logger.info(
            f"[ReflectionEngine] {outcome.symbol} {outcome.action} "
            f"→ {result} ({outcome.realized_pnl_pct:+.1%}): {reflection[:80]}"
        )

        return reflection

    def get_reflections_for_prompt(self, symbol: str = "", n: int = 3) -> str:
        """获取反思文本用于注入 MasterController prompt

        Args:
            symbol: 按交易对过滤 (空=所有)
            n: 最多返回 N 条

        Returns:
            反思文本段落
        """
        from backend.services.qaa.state_layers import episodic_memory

        lessons = episodic_memory.get_recent_lessons(symbol=symbol, n=n)
        if lessons:
            return "## 近期交易反思\n" + "\n".join(f"- {l}" for l in lessons)
        return ""


# 模块级单例
reflection_engine = ReflectionEngine()
