"""
QAA 三层状态架构 (Phase 3)

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §5

Layer A: 确定性状态 — 持仓/余额/风控限额, LLM 只读
Layer B: 生成式上下文 — LLM 上下文窗口, 内存 LRU
Layer C: 检索增强记忆 — 情景记忆 + 结果禁令 (防前瞻偏差)

关键规则: Layer A 覆盖一切。当 LLM (Layer B) 的建议与 Layer A 冲突时, Layer A 优先。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  Layer A: 确定性状态 (审计真相)
# ══════════════════════════════════════════════════


@dataclass
class PositionState:
    """单个持仓的确定性状态"""
    symbol: str
    side: str                       # "long" / "short"
    size: float                     # 仓位数
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    margin_used: float = 0.0
    leverage: int = 1
    stop_loss: float = 0.0
    take_profit: float = 0.0
    opened_at: Optional[str] = None
    tier: str = "mid"


@dataclass
class RiskLimits:
    """风控限额 — 确定性, 不受 LLM 影响"""
    max_position_pct: float = 0.25      # 单仓最大占 equity %
    max_leverage: int = 8               # 最大杠杆
    daily_loss_limit_pct: float = 0.05  # 日亏损限额
    max_open_positions: int = 5         # 最大持仓数
    min_margin_buffer_pct: float = 0.10 # 最低保证金缓冲


class DeterministicState:
    """Layer A — 确定性状态 (审计真相)

    仅由 TradeExecutionAgent 写入, 所有 Agent 只读。
    当 LLM 建议与 Layer A 冲突时, Layer A 为真。

    数据来源:
    - 持仓: position_tracker_service / hyperliquid_cache
    - 余额: hyperliquid_snapshot_service
    - 风控: settings.py 中的常量
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._positions: Dict[str, PositionState] = {}
        self._balance: float = 0.0
        self._equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_pnl_pct: float = 0.0
        self._risk_limits = RiskLimits()
        self._last_updated: float = 0.0

    def update_positions(self, positions: Dict[str, Any]):
        """更新持仓状态 (由 TradeExecutionAgent 调用)"""
        with self._lock:
            self._positions.clear()
            for sym, data in positions.items():
                if isinstance(data, dict):
                    self._positions[sym] = PositionState(
                        symbol=sym,
                        side=data.get("side", ""),
                        size=float(data.get("size", 0)),
                        entry_price=float(data.get("entry_price", 0)),
                        current_price=float(data.get("current_price", 0)),
                        unrealized_pnl=float(data.get("unrealized_pnl", 0)),
                        unrealized_pnl_pct=float(data.get("unrealized_pnl_pct", 0)),
                        margin_used=float(data.get("margin_used", 0)),
                        leverage=int(data.get("leverage", 1)),
                        stop_loss=float(data.get("stop_loss", 0)),
                        take_profit=float(data.get("take_profit", 0)),
                        tier=data.get("tier", "mid"),
                    )
            self._last_updated = time.time()

    def update_balance(self, balance: float, equity: float, daily_pnl: float):
        """更新余额状态"""
        with self._lock:
            self._balance = balance
            self._equity = equity
            self._daily_pnl = daily_pnl
            self._daily_pnl_pct = daily_pnl / max(equity, 1) if equity > 0 else 0
            self._last_updated = time.time()

    def get_positions(self) -> List[PositionState]:
        """返回当前持仓 — 不受 LLM 幻觉影响"""
        with self._lock:
            return list(self._positions.values())

    def get_position(self, symbol: str) -> Optional[PositionState]:
        """获取特定交易对的持仓"""
        with self._lock:
            return self._positions.get(symbol)

    def get_balance(self) -> float:
        """返回真实余额"""
        with self._lock:
            return self._balance

    def get_equity(self) -> float:
        """返回净值"""
        with self._lock:
            return self._equity

    def check_risk_limits(self, new_symbol: str = "", new_size_pct: float = 0) -> Dict[str, Any]:
        """检查风控限制 — 独立于 LLM 判断

        Args:
            new_symbol: 拟开仓的交易对
            new_size_pct: 拟开仓占 equity 百分比

        Returns:
            {"allowed": bool, "reasons": [str], "warnings": [str]}
        """
        with self._lock:
            result = {"allowed": True, "reasons": [], "warnings": []}

            # 检查持仓数
            if new_symbol and new_symbol not in self._positions:
                if len(self._positions) >= self._risk_limits.max_open_positions:
                    result["allowed"] = False
                    result["reasons"].append(
                        f"持仓数已达上限 {self._risk_limits.max_open_positions}"
                    )

            # 检查日亏损限额
            if self._daily_pnl_pct < -self._risk_limits.daily_loss_limit_pct:
                result["allowed"] = False
                result["reasons"].append(
                    f"日亏损 {self._daily_pnl_pct:.1%} 超过限额 {self._risk_limits.daily_loss_limit_pct:.1%}"
                )

            # 检查保证金
            total_margin = sum(p.margin_used for p in self._positions.values())
            margin_pct = total_margin / max(self._equity, 1)
            if margin_pct > (1 - self._risk_limits.min_margin_buffer_pct):
                result["allowed"] = False
                result["reasons"].append(
                    f"保证金使用 {margin_pct:.1%} 超过安全线"
                )

            # 检查同 symbol 反向
            if new_symbol in self._positions:
                result["warnings"].append(
                    f"{new_symbol} 已有持仓"
                )

            return result

    def get_snapshot(self) -> Dict[str, Any]:
        """获取完整状态快照 (用于 Layer B 注入 LLM)"""
        with self._lock:
            return {
                "balance": self._balance,
                "equity": self._equity,
                "daily_pnl": self._daily_pnl,
                "daily_pnl_pct": self._daily_pnl_pct,
                "positions": [
                    {
                        "symbol": p.symbol, "side": p.side,
                        "size": p.size, "entry_price": p.entry_price,
                        "unrealized_pnl": p.unrealized_pnl,
                        "unrealized_pnl_pct": p.unrealized_pnl_pct,
                        "leverage": p.leverage, "tier": p.tier,
                    }
                    for p in self._positions.values()
                ],
                "position_count": len(self._positions),
                "last_updated": self._last_updated,
            }


# ══════════════════════════════════════════════════
#  Layer B: 生成式上下文 (LLM 活动状态)
# ══════════════════════════════════════════════════


class GenerativeContext:
    """Layer B — LLM 上下文窗口 (最近 N 轮决策)

    瞬态存储, 受 LLM 注意力驱逐影响。
    使用 LRU 缓存保留最近的决策上下文。
    """

    def __init__(self, max_entries: int = 100):
        self._max = max_entries
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def store(self, key: str, context: Dict[str, Any]):
        """存储决策上下文"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._max:
                    self._cache.popitem(last=False)
            self._cache[key] = {
                **context,
                "stored_at": time.time(),
            }

    def retrieve(self, key: str) -> Optional[Dict[str, Any]]:
        """检索决策上下文"""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return dict(self._cache[key])
            return None

    def get_recent(self, n: int = 5, symbol: str = "") -> List[Dict[str, Any]]:
        """获取最近 N 条上下文 (可选按 symbol 过滤)"""
        with self._lock:
            items = list(self._cache.values())
            if symbol:
                items = [i for i in items if i.get("symbol") == symbol]
            return [dict(i) for i in items[-n:]]

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# ══════════════════════════════════════════════════
#  Layer C: 检索增强记忆 (情景记忆 + 结果禁令)
# ══════════════════════════════════════════════════


@dataclass
class Episode:
    """交易情景 — Layer C 的存储单元"""
    episode_id: str
    symbol: str
    action: str                     # "buy" / "sell" / "hold"
    tier: str = "mid"
    # 决策时信息
    decision_reasoning: str = ""
    market_context: str = ""        # 市场状态描述
    confidence: int = 0
    # 结果 (延迟填充)
    outcome: Optional[str] = None   # "profit" / "loss" / "breakeven"
    realized_pnl_pct: Optional[float] = None
    outcome_details: str = ""
    # 元数据
    tick_id: int = 0
    created_at: float = field(default_factory=time.time)
    outcome_filled_at: Optional[float] = None


class EpisodicMemory:
    """Layer C — 情景记忆, 带结果禁令防护前瞻偏差

    借鉴学术论文 "Agentic Trading" 三层状态架构:
    - store(): 决策时存储情景 (outcome 为 None)
    - fill_outcome(): k 个 tick 后填充结果
    - retrieve(): 检索相关情景, outcome 受禁令保护

    存储: 内存 + 可选持久化到 SQLite
    """

    def __init__(self, outcome_delay_k: int = 3, max_episodes: int = 500):
        self._outcome_delay_k = outcome_delay_k
        self._max_episodes = max_episodes
        self._episodes: Dict[str, Episode] = {}
        self._lock = threading.Lock()
        self._tick_counter: int = 0

    def store(self, episode: Episode) -> str:
        """存储交易情景 (决策时调用, outcome 为 None)"""
        with self._lock:
            if len(self._episodes) >= self._max_episodes:
                # 驱逐最旧的 10%
                keys = sorted(self._episodes.keys(),
                              key=lambda k: self._episodes[k].created_at)
                for k in keys[:self._max_episodes // 10]:
                    del self._episodes[k]
            self._episodes[episode.episode_id] = episode
            return episode.episode_id

    def fill_outcome(self, episode_id: str, outcome: str,
                     realized_pnl_pct: float = 0, details: str = ""):
        """填充交易结果 (延迟 k 个 tick 后调用)

        Args:
            episode_id: 情景 ID
            outcome: "profit" / "loss" / "breakeven"
            realized_pnl_pct: 实际收益百分比
            details: 详细描述
        """
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep:
                ep.outcome = outcome
                ep.realized_pnl_pct = realized_pnl_pct
                ep.outcome_details = details
                ep.outcome_filled_at = time.time()

    def retrieve(self, symbol: str = "", tier: str = "",
                 current_tick: int = 0, top_k: int = 5) -> List[Episode]:
        """检索相关情景 — 结果禁令: outcome 字段仅在 t_now >= t + k 后可访问

        Args:
            symbol: 按交易对过滤
            tier: 按周期过滤
            current_tick: 当前 tick (用于结果禁令计算)
            top_k: 返回最多 k 条

        Returns:
            Episode 列表 (outcome 可能被禁令隐藏)
        """
        with self._lock:
            results = []
            for ep in sorted(self._episodes.values(),
                             key=lambda e: e.created_at, reverse=True):
                # 过滤
                if symbol and ep.symbol != symbol:
                    continue
                if tier and ep.tier != tier:
                    continue

                # 结果禁令: 如果 outcome 已填充但当前 tick 不够, 隐藏结果
                safe_ep = Episode(
                    episode_id=ep.episode_id,
                    symbol=ep.symbol,
                    action=ep.action,
                    tier=ep.tier,
                    decision_reasoning=ep.decision_reasoning,
                    market_context=ep.market_context,
                    confidence=ep.confidence,
                    tick_id=ep.tick_id,
                    created_at=ep.created_at,
                )

                # 只有在延迟窗口之后才暴露 outcome
                if ep.outcome_filled_at is not None:
                    ticks_since_decision = current_tick - ep.tick_id
                    if ticks_since_decision >= self._outcome_delay_k:
                        safe_ep.outcome = ep.outcome
                        safe_ep.realized_pnl_pct = ep.realized_pnl_pct
                        safe_ep.outcome_details = ep.outcome_details
                        safe_ep.outcome_filled_at = ep.outcome_filled_at

                results.append(safe_ep)
                if len(results) >= top_k:
                    break

            return results

    def advance_tick(self) -> int:
        """推进 tick 计数器"""
        with self._lock:
            self._tick_counter += 1
            return self._tick_counter

    def get_recent_lessons(self, symbol: str = "", n: int = 3) -> List[str]:
        """获取最近的教训 (用于注入 LLM prompt)

        Returns:
            教训文本列表, 格式: "上次 {symbol} {action} {outcome}: {details}"
        """
        episodes = self.retrieve(
            symbol=symbol,
            current_tick=self._tick_counter,
            top_k=10,
        )
        lessons = []
        for ep in episodes:
            if ep.outcome:
                if ep.outcome == "loss":
                    lessons.append(
                        f"上次 {ep.symbol} {ep.action} 亏损 ({ep.realized_pnl_pct:.1%}): "
                        f"{ep.outcome_details or ep.decision_reasoning}"
                    )
                elif ep.outcome == "profit":
                    lessons.append(
                        f"上次 {ep.symbol} {ep.action} 盈利 ({ep.realized_pnl_pct:+.1%}): "
                        f"{ep.outcome_details or ep.decision_reasoning}"
                    )
            if len(lessons) >= n:
                break
        return lessons

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._episodes)
            with_outcome = sum(1 for e in self._episodes.values() if e.outcome is not None)
            return {
                "total_episodes": total,
                "with_outcome": with_outcome,
                "pending_outcome": total - with_outcome,
                "tick_counter": self._tick_counter,
                "outcome_delay_k": self._outcome_delay_k,
            }


# ══════════════════════════════════════════════════
#  模块级单例
# ══════════════════════════════════════════════════

deterministic_state = DeterministicState()
generative_context = GenerativeContext(max_entries=100)
episodic_memory = EpisodicMemory(outcome_delay_k=3, max_episodes=500)
