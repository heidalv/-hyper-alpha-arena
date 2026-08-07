"""
QAA RuleRouter — 规则路由引擎 (替代每 tick LLM 规划)

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §3.5

核心思想:
- 90% tick 由规则引擎确定调用链 (毫秒级)
- 异常场景才 fallback 到 LLM 规划 (额外 30-60s)
- 规则可审计可回测, 输出确定性强

路由决策基于 MarketSnapshot 中的字段:
- volatility_regime: NORMAL / HIGH / EXTREME
- has_major_news: bool (来自 IntelSignal)
- has_active_positions: bool (来自 PositionTracker)
- position_health: healthy / warning / danger (来自 PositionAnalyst)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.services.qaa.models import AgentCall

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  MarketSnapshot — 路由输入
# ══════════════════════════════════════════════════


@dataclass
class MarketSnapshot:
    """市场快照 — 路由决策的输入 (由 MarketDataAgent 填充)"""
    volatility_regime: str = "NORMAL"        # NORMAL / HIGH / EXTREME
    has_major_news: bool = False             # 有重大新闻/事件
    has_active_positions: bool = False       # 有活跃持仓
    position_health: str = "healthy"         # healthy / warning / danger
    symbols: List[str] = field(default_factory=list)
    funding_rate_extreme: bool = False       # funding rate 极端值
    volume_spike: bool = False               # 成交量异常
    regime_changed: bool = False             # 市场体制变化
    # 元数据
    account_equity: float = 0.0
    daily_pnl_pct: float = 0.0
    open_position_count: int = 0


# ══════════════════════════════════════════════════
#  RuleRouter — 规则路由引擎
# ══════════════════════════════════════════════════


class RuleRouter:
    """基于市场状态的规则路由, 替代每 tick LLM 规划

    延迟: <1ms (vs LLM 规划 30-60s)
    成本: 0 (vs LLM token 费用)
    确定性: 规则可审计可回测

    路由优先级:
    0 = 必须 (每个 tick 都执行)
    1 = 重要 (条件满足时执行)
    2 = 可选 (时间允许时执行)
    """

    def route(self, snapshot: MarketSnapshot) -> List[AgentCall]:
        """根据市场快照生成调用计划"""
        calls: List[AgentCall] = []
        # 去重: 已添加的 (agent_id, action) 集合
        seen: set = set()

        def _add(agent_id: str, action: str, timeout_ms: float = 30000,
                 priority: int = 0, payload: Optional[Dict[str, Any]] = None):
            key = (agent_id, action)
            if key not in seen:
                seen.add(key)
                calls.append(AgentCall(
                    agent_id=agent_id, action=action,
                    timeout_ms=timeout_ms, priority=priority,
                    payload=payload or {},
                ))

        # ── Priority 0: 必须 (每个 tick 都执行) ──
        _add("market_data", "get_snapshot", 10000, 0)
        _add("risk_control", "check", 5000, 0)

        # ── Priority 1: 条件调用 ──

        # 统一信号总线 — 每次重要 tick 获取融合信号
        _add("signal_bus", "get_unified_signal", 10000, 1, {})

        vol = snapshot.volatility_regime

        # 高波动 → 完整因子 + MT编排器
        if vol in ("HIGH", "EXTREME"):
            _add("factor_engine", "compute_full", 20000, 1, {"detail_level": "full"})
            _add("mt_orchestrator", "evaluate_portfolio", 30000, 1)
        elif vol == "NORMAL":
            _add("factor_engine", "compute_basic", 15000, 1, {"detail_level": "basic"})

        # 重大新闻 → 情报信号注入
        if snapshot.has_major_news:
            _add("intel_signal", "get_signals", 10000, 1, {"focus": "news_impact"})

        # Funding rate 极端 → 情报信号
        if snapshot.funding_rate_extreme:
            _add("intel_signal", "get_signals", 10000, 1, {"focus": "funding"})

        # 成交量异常 → 新旧合并因子 + 情报
        if snapshot.volume_spike:
            _add("factor_engine", "compute_unified", 25000, 1, {"detail_level": "full", "reason": "volume_spike"})
            # 仅在尚未添加情报信号时才添加
            if not snapshot.has_major_news and not snapshot.funding_rate_extreme:
                _add("intel_signal", "get_signals", 10000, 1, {"focus": "volume"})

        # 体制变化 → MT编排器重新评估
        if snapshot.regime_changed:
            _add("mt_orchestrator", "evaluate_portfolio", 30000, 1, {"reason": "regime_change"})

        # ── Priority 1: master_controller ALWAYS runs ──
        # Even without positions, it evaluates anomaly scores and
        # orchestrator signals for potential trade entries.
        _add("master_controller", "synthesize", 60000, 1, {"reason": "always_evaluate"})

        # ── Priority 2: 可选 (时间允许时执行) ──

        return calls

    def route_simple(self, snapshot: MarketSnapshot) -> List[AgentCall]:
        """简化路由 — 仅必须调用 + 基础因子 (用于维护 tick)"""
        return [
            AgentCall(agent_id="market_data", action="get_snapshot", timeout_ms=10000, priority=0),
            AgentCall(agent_id="risk_control", action="check", timeout_ms=5000, priority=0),
        ]


# 模块级单例
rule_router = RuleRouter()
