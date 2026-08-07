"""Agent 运行时监控器 — 聚合9个Agent的运行状态、调用统计、LLM消耗、滚动日志。

数据来源：
  1. EventBus._audit() 镜像（record_call）— 每次 Agent 调用的 ts/status/elapsed_ms/error
  2. EventBus.qaa_stats — 熔断器状态 (closed/open/half_open) + failure_count
  3. 外部 LLM 调用方推送 token 消耗（record_llm_usage）
  4. 通用日志推送（record_log）— 供其他服务记录 Agent 级别日志

设计原则：
  - 纯内存 dataclass + deque，无数据库依赖
  - 被动观测模式，不侵入 Agent 执行线程
  - 线程安全（threading.Lock）
  - 自动通过 ws_broadcast_hub 推送 overview 更新（节流 1s）
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Agent 列表（与 ALL_CARDS 保持一致）
_KNOWN_AGENTS = [
    "market_data",
    "factor_engine",
    "intel_signal",
    "risk_control",
    "mt_orchestrator",
    "master_controller",
    "trade_execution",
    "signal_bus",
    "genetic_optimizer",
]

# Agent display_name 映射
_AGENT_DISPLAY_NAMES: Dict[str, str] = {
    "market_data": "市场数据 Agent",
    "factor_engine": "因子引擎 Agent",
    "intel_signal": "情报信号 Agent",
    "risk_control": "风控 Agent",
    "mt_orchestrator": "多周期编排 Agent",
    "master_controller": "总控决策 Agent",
    "trade_execution": "交易执行 Agent",
    "signal_bus": "统一信号总线",
    "genetic_optimizer": "遗传优化器",
}

# Agent LLM 级别
_AGENT_LLM_LEVELS: Dict[str, str] = {
    "market_data": "NONE",
    "factor_engine": "QUICK",
    "intel_signal": "QUICK",
    "risk_control": "NONE",
    "mt_orchestrator": "QUICK",
    "master_controller": "DEEP",
    "trade_execution": "NONE",
    "signal_bus": "NONE",
    "genetic_optimizer": "NONE",
}


@dataclass
class AgentRuntimeStats:
    """单个 Agent 的运行时统计。"""

    agent_id: str
    status: str = "idle"  # idle / running / error / stopped
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    circuit_open_count: int = 0
    last_exec_ts: float = 0.0
    last_exec_duration_ms: float = 0.0
    last_error: str = ""
    last_error_ts: float = 0.0
    # LLM（仅 QUICK/DEEP 级别 Agent）
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_last_latency_ms: float = 0.0
    # 滚动日志（每 Agent 200 条）
    logs: Deque[dict] = field(default_factory=lambda: deque(maxlen=200))
    # 小时级调用频次（保留 168 小时 = 7 天）
    # 每个元素: {"hour": "2026-07-16T10", "count": N}
    hourly_calls: Deque[dict] = field(default_factory=lambda: deque(maxlen=168))

    @property
    def success_rate(self) -> float:
        """成功率 (0.0-1.0)，无调用时返回 -1.0（表示无数据）。"""
        if self.call_count == 0:
            return -1.0
        return self.success_count / self.call_count

    def to_overview_dict(self) -> dict:
        """转为 overview 使用的轻量字典。"""
        return {
            "agent_id": self.agent_id,
            "display_name": _AGENT_DISPLAY_NAMES.get(self.agent_id, self.agent_id),
            "llm_level": _AGENT_LLM_LEVELS.get(self.agent_id, "NONE"),
            "status": self.status,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "timeout_count": self.timeout_count,
            "success_rate": round(self.success_rate, 4),
            "last_exec_ts": self.last_exec_ts,
            "last_exec_ago_sec": round(time.time() - self.last_exec_ts, 1) if self.last_exec_ts else None,
            "last_exec_duration_ms": round(self.last_exec_duration_ms, 1),
            "last_error": self.last_error[:200] if self.last_error else "",
            "last_error_ts": self.last_error_ts if self.last_error_ts else None,
            "health_score": 0,  # 由 monitor 填充
            # 监控面板扩展字段
            "log_count": len(self.logs),
            "llm_total_tokens": self.llm_total_tokens,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
        }


class AgentRuntimeMonitor:
    """Agent 运行时监控器 — 单例，聚合所有 Agent 的运行状态。"""

    def __init__(self):
        self._stats: Dict[str, AgentRuntimeStats] = {
            aid: AgentRuntimeStats(agent_id=aid) for aid in _KNOWN_AGENTS
        }
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._last_broadcast_ts: float = 0.0
        self._broadcast_min_interval: float = 1.0  # 1s 节流
        self._backfilled: bool = False

    def _backfill_from_event_bus(self) -> None:
        """从 EventBus 已有的审计日志回灌统计（首次访问时触发一次）。

        确保监控器初始化前已存在的审计记录也被纳入统计。
        """
        if self._backfilled:
            return
        self._backfilled = True
        try:
            from backend.services.event_bus import event_bus
            # 触发 EventBus QAA 懒初始化（访问 qaa_stats 属性即可）
            _ = event_bus.qaa_stats
            audit_log = getattr(event_bus, "_qaa_audit_log", None)
            audit_lock = getattr(eventbus := event_bus, "_qaa_audit_lock", None)
            if audit_log is None or audit_lock is None:
                return
            with audit_lock:
                existing = list(audit_log)
            for entry in existing:
                self.record_call(entry)
            if existing:
                logger.info(
                    f"[AgentRuntimeMonitor] Backfilled {len(existing)} audit entries from EventBus"
                )
        except Exception as e:
            logger.debug(f"[AgentRuntimeMonitor] backfill failed: {e}")

    # ─── 核心数据写入 ───

    def record_call(self, audit_entry: dict) -> None:
        """从 EventBus 审计日志镜像记录，更新 Agent 统计。

        Args:
            audit_entry: {"ts", "agent_id", "action", "caller_id", "status", "elapsed_ms", "error"}
        """
        agent_id = audit_entry.get("agent_id", "")
        if not agent_id or agent_id not in self._stats:
            return

        status = audit_entry.get("status", "ok")
        ts = audit_entry.get("ts", time.time())
        elapsed_ms = audit_entry.get("elapsed_ms", 0.0)
        error_msg = audit_entry.get("error", "")

        with self._lock:
            stats = self._stats[agent_id]
            stats.call_count += 1
            stats.last_exec_ts = ts
            stats.last_exec_duration_ms = elapsed_ms

            if status == "ok":
                stats.success_count += 1
                stats.status = "running"
            elif status == "timeout":
                stats.timeout_count += 1
                stats.failure_count += 1
                stats.status = "error"
                stats.last_error = f"Timeout: {error_msg}"
                stats.last_error_ts = ts
            elif status == "error":
                stats.failure_count += 1
                stats.status = "error"
                stats.last_error = error_msg or "Unknown error"
                stats.last_error_ts = ts
            elif status == "circuit_open":
                stats.circuit_open_count += 1
                stats.status = "error"

            # 更新小时频次
            hour_key = time.strftime("%Y-%m-%dT%H", time.gmtime(ts))
            if stats.hourly_calls and stats.hourly_calls[-1].get("hour") == hour_key:
                stats.hourly_calls[-1]["count"] += 1
            else:
                stats.hourly_calls.append({"hour": hour_key, "count": 1})

            # 推入日志
            log_level = "INFO" if status == "ok" else "WARN" if status == "timeout" else "ERROR"
            stats.logs.append({
                "ts": ts,
                "level": log_level,
                "agent_id": agent_id,
                "action": audit_entry.get("action", ""),
                "message": f"{audit_entry.get('action', '')} → {status} ({elapsed_ms:.0f}ms)" + (f" | {error_msg}" if error_msg else ""),
            })

        # 尝试广播
        self._try_broadcast()

    def record_llm_usage(
        self,
        agent_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> None:
        """记录 LLM 调用的 token 消耗和延迟（仅 QUICK/DEEP 级别 Agent）。"""
        if agent_id not in self._stats:
            return
        _now = time.time()
        with self._lock:
            stats = self._stats[agent_id]
            stats.llm_prompt_tokens += prompt_tokens
            stats.llm_completion_tokens += completion_tokens
            stats.llm_total_tokens += prompt_tokens + completion_tokens
            stats.llm_last_latency_ms = latency_ms
            # 更新活跃状态
            stats.last_exec_ts = _now
            if stats.status == "idle":
                stats.status = "running"
            # 更新小时频次
            hour_key = time.strftime("%Y-%m-%dT%H", time.gmtime(_now))
            if stats.hourly_calls and stats.hourly_calls[-1].get("hour") == hour_key:
                stats.hourly_calls[-1]["count"] += 1
            else:
                stats.hourly_calls.append({"hour": hour_key, "count": 1})
            # 推入日志
            stats.logs.append({
                "ts": _now,
                "level": "INFO",
                "agent_id": agent_id,
                "action": "llm_call",
                "message": f"LLM tokens: +{prompt_tokens}/{completion_tokens} (total={stats.llm_total_tokens}), latency={latency_ms:.0f}ms",
            })

        self._try_broadcast()

    def record_log(
        self,
        agent_id: str,
        level: str,
        message: str,
        details: Optional[dict] = None,
    ) -> None:
        """通用日志推送 — 供其他服务记录 Agent 级别日志。

        也会更新 Agent 的活跃状态（last_exec_ts / status）和小时频次。

        Args:
            agent_id: Agent ID（未知 Agent 会被自动创建）
            level: INFO / WARN / ERROR / DEBUG
            message: 日志消息
            details: 附加详情
        """
        if agent_id not in self._stats:
            # 未知 agent — 自动创建
            with self._lock:
                self._stats[agent_id] = AgentRuntimeStats(agent_id=agent_id)

        ts = time.time()
        with self._lock:
            stats = self._stats[agent_id]
            # 更新活跃状态
            stats.last_exec_ts = ts
            lvl = level.upper()
            if lvl == "ERROR":
                stats.status = "error"
                stats.last_error = message[:200]
                stats.last_error_ts = ts
            elif stats.status != "error":
                stats.status = "running"
            # 更新小时频次
            hour_key = time.strftime("%Y-%m-%dT%H", time.gmtime(ts))
            if stats.hourly_calls and stats.hourly_calls[-1].get("hour") == hour_key:
                stats.hourly_calls[-1]["count"] += 1
            else:
                stats.hourly_calls.append({"hour": hour_key, "count": 1})
            # 推入日志
            stats.logs.append({
                "ts": ts,
                "level": lvl,
                "agent_id": agent_id,
                "action": details.get("action", "") if details else "",
                "message": message,
            })

        self._try_broadcast()

    # ─── 健康度计算 ───

    def compute_health_score(self, agent_id: str) -> float:
        """综合健康度评分 (0-100)。

        权重：
          - 活跃度 40%（10分钟内有活动 → 40分，30分钟内 → 25分，更久 → 0分）
          - 错误状态 35%（无错误 → 35分，有失败/超时但<20% → 扣分，高错误率 → 0分）
          - 熔断器状态 25%（closed → 25分, half_open → 12.5分, open → 0分）
        """
        stats = self._stats.get(agent_id)
        if stats is None:
            return 0.0

        now = time.time()

        # 活跃度（40%）— 基于 last_exec_ts（来自 record_log / record_llm_usage / record_call）
        activity_score = 0.0
        if stats.last_exec_ts > 0:
            elapsed = now - stats.last_exec_ts
            if elapsed < 600:      # 10分钟内
                activity_score = 40.0
            elif elapsed < 1800:   # 30分钟内
                activity_score = 25.0
            elif elapsed < 3600:   # 1小时内
                activity_score = 10.0

        # 错误状态（35%）
        error_score = 35.0  # 默认无错误
        if stats.status == "error":
            error_score = 0.0
        elif stats.call_count > 0:
            fail_rate = (stats.failure_count + stats.timeout_count) / stats.call_count
            error_score = max(0.0, 35.0 * (1.0 - fail_rate))
        elif stats.failure_count > 0 or stats.timeout_count > 0:
            error_score = 15.0

        # 熔断器状态（25%）
        cb_score = 25.0  # 默认 closed
        try:
            from backend.services.event_bus import event_bus
            cb_info = event_bus.qaa_stats.get("circuit_breakers", {}).get(agent_id, {})
            cb_state = cb_info.get("state", "closed")
            if cb_state == "closed":
                cb_score = 25.0
            elif cb_state == "half_open":
                cb_score = 12.5
            elif cb_state == "open":
                cb_score = 0.0
        except Exception:
            pass

        return round(activity_score + error_score + cb_score, 1)

    # ─── 数据读取 ───

    def get_overview(self) -> List[dict]:
        """9个 Agent 的运行时状态总览。"""
        self._backfill_from_event_bus()
        with self._lock:
            results = []
            for aid in _KNOWN_AGENTS:
                stats = self._stats.get(aid)
                if stats is None:
                    continue
                entry = stats.to_overview_dict()
                entry["health_score"] = self.compute_health_score(aid)
                # 融合熔断器状态
                try:
                    from backend.services.event_bus import event_bus
                    cb_info = event_bus.qaa_stats.get("circuit_breakers", {}).get(aid, {})
                    entry["circuit_breaker_state"] = cb_info.get("state", "closed")
                    entry["circuit_breaker_failures"] = cb_info.get("failures", 0)
                except Exception:
                    entry["circuit_breaker_state"] = "unknown"
                    entry["circuit_breaker_failures"] = 0
                results.append(entry)
            return results

    def get_agent_detail(self, agent_id: str) -> Optional[dict]:
        """单 Agent 详情 + 最近日志 + 延迟百分位。"""
        with self._lock:
            stats = self._stats.get(agent_id)
            if stats is None:
                return None
            overview = stats.to_overview_dict()
            overview["health_score"] = self.compute_health_score(agent_id)

            # LLM 统计
            overview["llm"] = {
                "level": _AGENT_LLM_LEVELS.get(agent_id, "NONE"),
                "prompt_tokens": stats.llm_prompt_tokens,
                "completion_tokens": stats.llm_completion_tokens,
                "total_tokens": stats.llm_total_tokens,
                "last_latency_ms": round(stats.llm_last_latency_ms, 1),
            }

            # 熔断器详情
            try:
                from backend.services.event_bus import event_bus
                cb_info = event_bus.qaa_stats.get("circuit_breakers", {}).get(agent_id, {})
                overview["circuit_breaker"] = cb_info
            except Exception:
                overview["circuit_breaker"] = {}

            # 延迟百分位（从 LatencyMonitor 获取）
            try:
                from backend.services.qaa.phase3_enhancements import latency_monitor
                all_pct = latency_monitor.get_percentiles()
                agent_latency = {
                    k: v for k, v in all_pct.items() if k.startswith(agent_id + ".")
                }
                overview["latency"] = agent_latency
            except Exception:
                overview["latency"] = {}

            # 最近 50 条日志
            recent_logs = list(stats.logs)[-50:]
            overview["recent_logs"] = recent_logs

            # 小时频次
            overview["hourly_calls"] = list(stats.hourly_calls)

            return overview

    def get_logs(
        self,
        agent_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """日志查询，支持按 Agent 和级别过滤。

        Args:
            agent_id: 指定 Agent（None=全部）
            level: INFO / WARN / ERROR（None=全部）
            limit: 返回最大条数
        """
        results: List[dict] = []
        with self._lock:
            if agent_id and agent_id in self._stats:
                agents = [self._stats[agent_id]]
            elif agent_id and agent_id == "all":
                agents = list(self._stats.values())
            else:
                agents = list(self._stats.values())

            for stats in agents:
                for log in stats.logs:
                    if level and log.get("level", "").upper() != level.upper():
                        continue
                    results.append(log)

        # 按 ts 倒序
        results.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return results[:limit]

    def get_frequency_stats(self, hours: int = 24) -> dict:
        """按 Agent x Hour 的执行频次矩阵。

        Returns:
            {
                "agents": ["market_data", ...],
                "hours": ["2026-07-16T10", ...],  # 最近 N 小时
                "matrix": {"market_data": [0, 5, 3, ...], ...},
                "total_calls": {"market_data": 100, ...},
            }
        """
        # 生成最近 N 小时的标签
        now = time.time()
        hour_labels = []
        for h in range(hours - 1, -1, -1):
            ts = now - h * 3600
            hour_labels.append(time.strftime("%Y-%m-%dT%H", time.gmtime(ts)))

        hour_set = set(hour_labels)
        matrix: Dict[str, List[int]] = {}
        totals: Dict[str, int] = {}

        with self._lock:
            for aid in _KNOWN_AGENTS:
                stats = self._stats.get(aid)
                counts = {entry["hour"]: entry["count"] for entry in (stats.hourly_calls if stats else [])}
                row = []
                total = 0
                for hl in hour_labels:
                    c = counts.get(hl, 0)
                    row.append(c)
                    total += c
                matrix[aid] = row
                totals[aid] = total

        return {
            "agents": list(_KNOWN_AGENTS),
            "hours": hour_labels,
            "matrix": matrix,
            "total_calls": totals,
        }

    def get_all_agent_ids(self) -> List[str]:
        """返回所有已知 Agent ID。"""
        return list(_KNOWN_AGENTS)

    @property
    def uptime_seconds(self) -> float:
        return round(time.time() - self._start_time, 1)

    # ─── 广播 ───

    def _try_broadcast(self) -> None:
        """节流推送 overview 到 WebSocket。"""
        now = time.time()
        if now - self._last_broadcast_ts < self._broadcast_min_interval:
            return
        self._last_broadcast_ts = now
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            ws_broadcast_hub.broadcast_agent_update({
                "agents": self.get_overview(),
                "uptime_seconds": self.uptime_seconds,
            })
        except Exception as e:
            logger.debug(f"[AgentRuntimeMonitor] broadcast failed: {e}")

    def reset_agent(self, agent_id: str) -> bool:
        """重置 Agent 错误状态。"""
        with self._lock:
            stats = self._stats.get(agent_id)
            if stats is None:
                return False
            stats.failure_count = 0
            stats.timeout_count = 0
            stats.last_error = ""
            stats.last_error_ts = 0.0
            stats.status = "idle"
            return True


# ══════════════════════════════════════════════════
#  模块级单例
# ══════════════════════════════════════════════════

agent_runtime_monitor = AgentRuntimeMonitor()
