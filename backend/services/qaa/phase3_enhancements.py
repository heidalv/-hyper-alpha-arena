"""
QAA Phase 3 增强: LLM Fallback + 漂移监控 + Agent 健康监控

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §3.5 + §3.8.6

1. LLM 规划 Fallback — 规则路由无法判断时, 由 LLM 规划调用链
2. 延迟漂移监控 — 各阶段 P99 耗时超预算 120% 告警
3. Agent 健康监控 — Agent 状态/熔断/审计的 API 端点
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  3.3: LLM 规划 Fallback
# ══════════════════════════════════════════════════


class LLMPlannerFallback:
    """LLM 规划 Fallback — 规则路由无法判断时使用

    触发条件 (由 RuleRouter 返回空列表或特殊标记):
    - 市场状态异常 (不在 NORMAL/HIGH/EXTREME 范围内)
    - 多个矛盾信号同时出现
    - 系统从故障恢复中

    策略: 使用 QUICK 级别 LLM 做简单判断, 不用 DEEP (避免延迟)
    """

    def __init__(self):
        self._call_count = 0
        self._last_fallback_time: float = 0

    def should_fallback(self, snapshot_data: Dict[str, Any]) -> bool:
        """判断是否需要 LLM fallback

        Args:
            snapshot_data: 市场快照数据

        Returns:
            True=需要 fallback 到 LLM 规划
        """
        # 检查路由结果是否为空或异常
        if not snapshot_data:
            return True

        # 检查市场状态是否在已知范围外
        vol_regime = snapshot_data.get("volatility_regime", "")
        if vol_regime not in ("NORMAL", "HIGH", "EXTREME", ""):
            return True

        # 冷却: 5 分钟内不重复 fallback
        if time.time() - self._last_fallback_time < 300:
            return False

        return False

    def plan(self, snapshot_data: Dict[str, Any],
             call_llm_fn: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """使用 LLM 规划调用链

        Args:
            snapshot_data: 市场快照
            call_llm_fn: LLM 调用函数 (action, payload) -> response

        Returns:
            AgentCall 列表 (与 RuleRouter.route() 相同格式)
        """
        self._call_count += 1
        self._last_fallback_time = time.time()

        # 默认安全调用链 (不调 LLM, 直接返回基础计划)
        safe_plan = [
            {"agent_id": "market_data", "action": "get_snapshot", "timeout_ms": 10000, "priority": 0},
            {"agent_id": "risk_control", "action": "check", "timeout_ms": 5000, "priority": 0},
            {"agent_id": "master_controller", "action": "synthesize", "timeout_ms": 60000, "priority": 2,
             "payload": {"reason": "llm_fallback_safe_plan"}},
        ]

        # 如果有 LLM 函数, 尝试规划
        if call_llm_fn:
            try:
                prompt = (
                    f"市场状态: {snapshot_data.get('volatility_regime', 'UNKNOWN')}\n"
                    f"有持仓: {snapshot_data.get('has_active_positions', False)}\n"
                    f"重大新闻: {snapshot_data.get('has_major_news', False)}\n"
                    f"成交量异常: {snapshot_data.get('volume_spike', False)}\n\n"
                    f"基于以上信息, 选择交易策略:\n"
                    f"A) 仅基础分析 + 风控 (保守)\n"
                    f"B) 完整因子 + 编排器 + 情报 (积极)\n"
                    f"C) 持仓监控 + HOLD (防御)\n\n"
                    f"回复一个字母 (A/B/C)。"
                )
                response = call_llm_fn("quick_plan", {"prompt": prompt})
                if response:
                    choice = str(response).strip().upper()[:1]
                    if choice == "B":
                        return [
                            {"agent_id": "market_data", "action": "get_snapshot", "timeout_ms": 10000, "priority": 0},
                            {"agent_id": "risk_control", "action": "check", "timeout_ms": 5000, "priority": 0},
                            {"agent_id": "factor_engine", "action": "compute_full", "timeout_ms": 20000, "priority": 1},
                            {"agent_id": "mt_orchestrator", "action": "evaluate_portfolio", "timeout_ms": 30000, "priority": 1},
                            {"agent_id": "intel_signal", "action": "get_signals", "timeout_ms": 10000, "priority": 1},
                            {"agent_id": "master_controller", "action": "synthesize", "timeout_ms": 60000, "priority": 2},
                        ]
                    elif choice == "C":
                        return [
                            {"agent_id": "market_data", "action": "get_snapshot", "timeout_ms": 10000, "priority": 0},
                            {"agent_id": "risk_control", "action": "check", "timeout_ms": 5000, "priority": 0},
                        ]
            except Exception as e:
                logger.warning(f"[LLMPlannerFallback] LLM 规划失败, 使用安全计划: {e}")

        return safe_plan

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "fallback_count": self._call_count,
            "last_fallback_ago_sec": time.time() - self._last_fallback_time if self._last_fallback_time else 0,
        }


# ══════════════════════════════════════════════════
#  3.4: 延迟漂移监控
# ══════════════════════════════════════════════════

# 延迟预算 (设计文档 §3.8.6)
LATENCY_BUDGETS: Dict[str, float] = {
    "market_data.get_snapshot": 10.0,
    "risk_control.check": 5.0,
    "factor_engine.compute_full": 20.0,
    "factor_engine.compute_basic": 15.0,
    "mt_orchestrator.evaluate_portfolio": 30.0,
    "intel_signal.get_signals": 10.0,
    "master_controller.synthesize": 60.0,
    "trade_execution.place_order": 5.0,
    "qaa_tick_total": 90.0,
}


@dataclass
class LatencyRecord:
    """单次延迟记录"""
    agent_action: str               # "agent_id.action"
    elapsed_ms: float
    timestamp: float = field(default_factory=time.time)
    status: str = "ok"              # ok / timeout / error


class LatencyMonitor:
    """延迟漂移监控 — 各阶段 P99 耗时超预算 120% 告警

    工作流:
    1. 每次 Agent 调用记录耗时
    2. 按阶段计算 P50/P95/P99
    3. P99 超预算 120% 时打印告警
    4. 提供 API 端点查询当前状态
    """

    def __init__(self, max_records_per_action: int = 200):
        self._max = max_records_per_action
        self._records: Dict[str, List[LatencyRecord]] = defaultdict(list)
        self._lock = threading.Lock()
        self._alerts: List[str] = []

    def record(self, agent_id: str, action: str, elapsed_ms: float,
               status: str = "ok"):
        """记录一次 Agent 调用的延迟"""
        key = f"{agent_id}.{action}"
        rec = LatencyRecord(
            agent_action=key,
            elapsed_ms=elapsed_ms,
            status=status,
        )

        with self._lock:
            self._records[key].append(rec)
            if len(self._records[key]) > self._max:
                self._records[key] = self._records[key][-self._max // 2:]

            # 检查是否超预算
            budget = LATENCY_BUDGETS.get(key, 0)
            if budget > 0:
                budget_ms = budget * 1000
                if elapsed_ms > budget_ms * 1.2:
                    alert = (
                        f"[LatencyMonitor] 漂移告警: {key} "
                        f"耗时 {elapsed_ms:.0f}ms > 预算 {budget_ms:.0f}ms × 1.2"
                    )
                    self._alerts.append(f"{time.time():.0f}: {alert}")
                    if len(self._alerts) > 50:
                        self._alerts = self._alerts[-40:]
                    logger.warning(alert)

    def get_percentiles(self, agent_action: str = "") -> Dict[str, Dict[str, float]]:
        """获取延迟百分位数

        Args:
            agent_action: 特定 agent.action (空=全部)

        Returns:
            {agent_action: {p50, p95, p99, count, avg}}
        """
        with self._lock:
            result = {}
            keys = [agent_action] if agent_action else list(self._records.keys())
            for key in keys:
                recs = self._records.get(key, [])
                if not recs:
                    continue
                elapseds = sorted(r.elapsed_ms for r in recs if r.status == "ok")
                if not elapseds:
                    continue
                n = len(elapseds)
                result[key] = {
                    "p50": elapseds[int(n * 0.5)],
                    "p95": elapseds[int(n * 0.95)] if n > 1 else elapseds[0],
                    "p99": elapseds[int(n * 0.99)] if n > 1 else elapseds[0],
                    "avg": sum(elapseds) / n,
                    "count": n,
                    "budget_ms": LATENCY_BUDGETS.get(key, 0) * 1000,
                }
            return result

    def get_alerts(self, n: int = 10) -> List[str]:
        """获取最近的漂移告警"""
        with self._lock:
            return list(self._alerts[-n:])

    @property
    def summary(self) -> Dict[str, Any]:
        """监控摘要"""
        percentiles = self.get_percentiles()
        return {
            "monitored_actions": list(percentiles.keys()),
            "percentiles": percentiles,
            "recent_alerts": self.get_alerts(5),
            "latency_budgets": LATENCY_BUDGETS,
        }


# ══════════════════════════════════════════════════
#  3.5: Agent 健康监控
# ══════════════════════════════════════════════════


class AgentHealthMonitor:
    """Agent 健康监控 — 聚合 EventBus/CircuitBreaker/Latency 状态

    提供 API 端点查询:
    - /api/qaa/health — 全局健康状态
    - /api/qaa/agents — 各 Agent 状态
    - /api/qaa/latency — 延迟监控
    - /api/qaa/alerts — 漂移告警
    """

    def __init__(self):
        self._start_time = time.time()

    def get_health(self) -> Dict[str, Any]:
        """获取全局健康状态"""
        from backend.services.event_bus import event_bus
        from backend.services.qaa.state_layers import (
            deterministic_state, episodic_memory,
        )

        qaa_stats = event_bus.qaa_stats
        latency_summary = latency_monitor.summary
        memory_stats = episodic_memory.stats

        return {
            "status": "healthy",
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "qaa_mode": _get_qaa_mode(),
            "agents": qaa_stats.get("registered_agents", []),
            "circuit_breakers": qaa_stats.get("circuit_breakers", {}),
            "latency_summary": {
                k: {kk: vv for kk, vv in v.items() if kk != "budget_ms"}
                for k, v in latency_summary.get("percentiles", {}).items()
            },
            "memory": memory_stats,
            "alerts": latency_summary.get("recent_alerts", []),
            "event_bus": event_bus.stats,
        }

    def get_agent_detail(self, agent_id: str) -> Dict[str, Any]:
        """获取特定 Agent 的详细状态"""
        from backend.services.event_bus import event_bus

        qaa_stats = event_bus.qaa_stats
        cb_info = qaa_stats.get("circuit_breakers", {}).get(agent_id, {})
        latency = latency_monitor.get_percentiles()

        # 过滤该 agent 的延迟
        agent_latency = {
            k: v for k, v in latency.items()
            if k.startswith(agent_id + ".")
        }

        return {
            "agent_id": agent_id,
            "circuit_breaker": cb_info,
            "latency": agent_latency,
        }


def _get_qaa_mode() -> str:
    try:
        from backend.config.settings import QAA_MODE
        return QAA_MODE
    except Exception:
        return "unknown"


# ══════════════════════════════════════════════════
#  模块级单例
# ══════════════════════════════════════════════════

llm_planner_fallback = LLMPlannerFallback()
latency_monitor = LatencyMonitor()
agent_health_monitor = AgentHealthMonitor()
