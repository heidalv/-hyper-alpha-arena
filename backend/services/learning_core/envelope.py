"""统一进化学习内核 — 交互协议 EvolutionEnvelope

这是"策略假设引擎 / Hermes 进化 / 智能学习中心"三模块合并后的**统一血缘协议**。
所有域（假设生成、回测验证、GA 进化、在线学习、RL 决策、部署观测、反馈）都产出/消费
同一个 EvolutionEnvelope，通过 lineage_id + parent_id 构成一棵可追溯的血缘树。

设计目标（对应方案需求 2「链路串联 / 可追溯」）：
  - 单一数据结构贯穿全链路，任何一个阶段都能回放"从一个假设到最终部署"的完整路径。
  - 纯数据类，无副作用；持久化与广播由 LearningLedger 负责，避免耦合。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ── 阶段枚举（贯穿全链路的有序阶段名）──
STAGE_HYPOTHESIS = "hypothesis"   # 假设生成
STAGE_VALIDATE = "validate"       # 快速回测验证
STAGE_EVOLVE = "evolve"           # GA/NSGA-II 进化
STAGE_LEARN = "learn"             # 在线学习 / 因子 IC 反馈
STAGE_RL_DECIDE = "rl_decide"     # RL 决策（影子）
STAGE_DEPLOY = "deploy"           # 部署 / 晋升
STAGE_OBSERVE = "observe"         # 运行观测
STAGE_FEEDBACK = "feedback"       # 结果反馈回灌

VALID_STAGES = {
    STAGE_HYPOTHESIS,
    STAGE_VALIDATE,
    STAGE_EVOLVE,
    STAGE_LEARN,
    STAGE_RL_DECIDE,
    STAGE_DEPLOY,
    STAGE_OBSERVE,
    STAGE_FEEDBACK,
}

# ── 状态枚举 ──
STATUS_PENDING = "pending"
STATUS_PASSED = "passed"
STATUS_REJECTED = "rejected"
STATUS_DEPLOYED = "deployed"
STATUS_ROLLED_BACK = "rolled_back"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_PASSED,
    STATUS_REJECTED,
    STATUS_DEPLOYED,
    STATUS_ROLLED_BACK,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_lineage_id() -> str:
    """生成一条全新血缘链路 ID（贯穿假设->进化->学习->RL->部署）。"""
    return f"lin_{uuid.uuid4().hex[:16]}"


@dataclass
class EvolutionEnvelope:
    """统一进化学习信封 — 全链路可追溯的最小单元。

    Attributes:
        lineage_id: 全链路可追溯 ID。同一条演化链路的所有 envelope 共享该 ID。
        stage:      当前阶段（见 VALID_STAGES）。
        source:     模块来源，如 "hypothesis_engine" / "strategy_evolver" / "rl_core"。
        envelope_id: 本条 envelope 唯一 ID（自动生成）。
        parent_id:  上游 envelope_id，用于构成血缘树；根节点为 None。
        symbol:     交易标的（可空，全局事件时为 None）。
        payload:    阶段业务数据（假设内容 / 基因 / 因子权重 / 回测结果 / RL 动作 …）。
        metrics:    量化指标（sharpe / win_rate / max_dd / ic / reward …）。
        status:     状态（见 VALID_STATUSES）。
        created_at: ISO8601 UTC 时间戳。
    """

    lineage_id: str
    stage: str
    source: str
    envelope_id: str = field(default_factory=lambda: f"env_{uuid.uuid4().hex[:16]}")
    parent_id: Optional[str] = None
    symbol: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=_now_iso)
    # ── v3 新增（整改#21 PBO-aware）：默认值向后兼容，旧调用无需改动 ──
    cumulative_trial_count: int = 0          # 该 lineage 累计试验数（跨代）
    is_oos: bool = False                     # 样本内/外标记
    selection_rank: Optional[int] = None     # 在当代的选择排名（供 PBO）

    def __post_init__(self) -> None:
        if self.stage not in VALID_STAGES:
            raise ValueError(f"非法 stage: {self.stage!r}，合法值: {sorted(VALID_STAGES)}")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"非法 status: {self.status!r}，合法值: {sorted(VALID_STATUSES)}")

    # ── 工厂方法 ──

    @classmethod
    def root(
        cls,
        *,
        stage: str,
        source: str,
        symbol: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        status: str = STATUS_PENDING,
        lineage_id: Optional[str] = None,
    ) -> "EvolutionEnvelope":
        """创建一条新血缘链路的根 envelope。"""
        return cls(
            lineage_id=lineage_id or new_lineage_id(),
            stage=stage,
            source=source,
            symbol=symbol,
            payload=payload or {},
            metrics=metrics or {},
            status=status,
        )

    def child(
        self,
        *,
        stage: str,
        source: str,
        payload: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        status: str = STATUS_PENDING,
    ) -> "EvolutionEnvelope":
        """基于当前 envelope 派生下游 envelope，自动继承 lineage_id / symbol 并建立 parent 链接。"""
        return EvolutionEnvelope(
            lineage_id=self.lineage_id,
            stage=stage,
            source=source,
            parent_id=self.envelope_id,
            symbol=self.symbol,
            payload=payload or {},
            metrics=metrics or {},
            status=status,
        )

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvolutionEnvelope":
        return cls(
            lineage_id=d["lineage_id"],
            stage=d["stage"],
            source=d["source"],
            envelope_id=d.get("envelope_id") or f"env_{uuid.uuid4().hex[:16]}",
            parent_id=d.get("parent_id"),
            symbol=d.get("symbol"),
            payload=d.get("payload") or {},
            metrics=d.get("metrics") or {},
            status=d.get("status") or STATUS_PENDING,
            created_at=d.get("created_at") or _now_iso(),
            cumulative_trial_count=int(d.get("cumulative_trial_count") or 0),
            is_oos=bool(d.get("is_oos") or False),
            selection_rank=d.get("selection_rank"),
        )
