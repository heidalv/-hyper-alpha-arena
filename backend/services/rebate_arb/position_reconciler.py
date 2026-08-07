"""
PositionReconciler — 仓位对账器

核心职责：
1. 内存仓位 vs DB 仓位一致性校验
2. DB 仓位 vs 交易所真实持仓对账
3. 发现不一致时生成修复动作建议
4. 定期运行（由引擎或调度器触发）

使用：
    from backend.services.rebate_arb.position_reconciler import position_reconciler
    report = position_reconciler.reconcile(engine, exchange_getter)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationIssue:
    """单条对账问题"""
    severity: str  # "critical" / "warning" / "info"
    issue_type: str  # "orphan_memory", "orphan_db", "orphan_exchange", "state_mismatch", "size_mismatch"
    position_id: str = ""
    exchange: str = ""
    symbol: str = ""
    description: str = ""
    suggested_action: str = ""  # "close_memory", "close_db", "close_exchange", "sync_state", "manual_review"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReconciliationReport:
    """完整对账报告"""
    timestamp: float = 0.0
    duration_ms: float = 0.0
    memory_positions: int = 0
    db_positions: int = 0
    exchange_positions: int = 0
    issues: List[ReconciliationIssue] = field(default_factory=list)
    is_consistent: bool = True

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "memory_positions": self.memory_positions,
            "db_positions": self.db_positions,
            "exchange_positions": self.exchange_positions,
            "is_consistent": self.is_consistent,
            "issues_total": len(self.issues),
            "critical": self.critical_count,
            "warnings": self.warning_count,
            "issues": [
                {
                    "severity": i.severity,
                    "type": i.issue_type,
                    "position_id": i.position_id,
                    "exchange": i.exchange,
                    "description": i.description,
                    "action": i.suggested_action,
                }
                for i in self.issues
            ],
        }


class PositionReconciler:
    """
    仓位对账器 — 检测内存/DB/交易所三方不一致。

    调用模式:
        report = reconciler.reconcile(engine, exchange_getter)

    其中 exchange_getter: Callable[[str], Optional[BaseExchangeClient]]
    """

    # 仓位大小误差容忍（5%）
    SIZE_TOLERANCE_PCT = 0.05
    # 过期仓位阈值（超过 max_hold 120% 视为过期未清理）
    STALE_MULTIPLIER = 1.2

    def __init__(self):
        self._last_report: Optional[ReconciliationReport] = None
        self._reconcile_count = 0

    def reconcile(
        self,
        engine,
        exchange_getter: Optional[Callable] = None,
    ) -> ReconciliationReport:
        """
        执行完整对账流程。

        Step 1: 内存 vs DB
        Step 2: DB vs 交易所（如果 exchange_getter 可用）
        Step 3: 检测过期仓位

        Args:
            engine: RebateArbitrageEngine 实例
            exchange_getter: 可选，获取交易所客户端的回调

        Returns:
            ReconciliationReport
        """
        start = time.time()
        self._reconcile_count += 1
        report = ReconciliationReport(timestamp=start)

        # Step 1: 获取内存仓位
        memory_positions = self._get_memory_positions(engine)
        report.memory_positions = len(memory_positions)

        # Step 2: 获取 DB 仓位
        db_positions = self._get_db_positions()
        report.db_positions = len(db_positions)

        # Step 3: 内存 vs DB 对比
        self._reconcile_memory_vs_db(memory_positions, db_positions, report)

        # Step 4: DB vs 交易所对比（可选）
        if exchange_getter:
            exchange_positions = self._get_exchange_positions(exchange_getter, db_positions)
            report.exchange_positions = len(exchange_positions)
            self._reconcile_db_vs_exchange(db_positions, exchange_positions, report)

        # Step 5: 检测过期仓位
        self._check_stale_positions(memory_positions, report)

        report.is_consistent = len(report.issues) == 0
        report.duration_ms = (time.time() - start) * 1000

        self._last_report = report

        if report.issues:
            logger.warning(
                f"[Reconciler] 对账完成: {len(report.issues)} 个问题 "
                f"({report.critical_count} critical, {report.warning_count} warning)"
            )
        else:
            logger.info("[Reconciler] 对账完成: 一致")

        return report

    def get_last_report(self) -> Optional[ReconciliationReport]:
        """获取最近一次对账报告"""
        return self._last_report

    def auto_fix(self, engine, report: Optional[ReconciliationReport] = None) -> Dict[str, Any]:
        """
        自动修复低风险问题。

        仅处理:
        - orphan_memory (内存有DB无 → 从内存移除)
        - state_mismatch (状态不一致 → 以 DB 为准同步)

        不处理:
        - orphan_exchange (交易所有额外仓位 → 需要人工确认)
        - size_mismatch > 容忍值 → 需要人工确认

        Returns:
            {"fixed": int, "skipped": int, "details": [...]}
        """
        if report is None:
            report = self._last_report
        if report is None:
            return {"fixed": 0, "skipped": 0, "details": ["no_report_available"]}

        fixed = 0
        skipped = 0
        details = []

        for issue in report.issues:
            if issue.suggested_action == "close_memory" and issue.severity != "critical":
                # 从内存移除孤立仓位
                with engine._lock:
                    if issue.position_id in engine._active_positions:
                        del engine._active_positions[issue.position_id]
                        fixed += 1
                        details.append(f"removed_memory:{issue.position_id}")
            elif issue.suggested_action == "sync_state":
                # 同步状态（以 DB 为准）
                # 这里只更新内存状态
                with engine._lock:
                    pos = engine._active_positions.get(issue.position_id)
                    if pos and "db_status" in issue.details:
                        from .models import RebatePositionStatus
                        try:
                            pos.status = RebatePositionStatus(issue.details["db_status"])
                            fixed += 1
                            details.append(f"synced_state:{issue.position_id}")
                        except (ValueError, KeyError):
                            skipped += 1
            else:
                skipped += 1

        return {"fixed": fixed, "skipped": skipped, "details": details}

    # ── 内部方法 ──

    def _get_memory_positions(self, engine) -> Dict[str, Dict[str, Any]]:
        """从引擎获取内存仓位快照"""
        result = {}
        with engine._lock:
            for pid, pos in engine._active_positions.items():
                result[pid] = {
                    "position_id": pid,
                    "strategy_type": pos.strategy_type.value,
                    "source_exchange": pos.source_exchange,
                    "target_exchange": pos.target_exchange,
                    "symbol": pos.symbol,
                    "side_a_size": pos.side_a_size,
                    "side_b_size": pos.side_b_size,
                    "status": pos.status.value,
                    "entry_time": pos.entry_time,
                    "max_hold_seconds": pos.max_hold_seconds,
                    "paper_mode": pos.paper_mode,
                }
        return result

    def _get_db_positions(self) -> Dict[str, Dict[str, Any]]:
        """从数据库获取活跃仓位"""
        result = {}
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import RebatePositionDB

            db = SessionLocal()
            try:
                rows = db.query(RebatePositionDB).filter(
                    RebatePositionDB.status.in_(["active", "closing"])
                ).all()

                for row in rows:
                    result[row.position_id] = {
                        "position_id": row.position_id,
                        "strategy_type": row.strategy_type,
                        "source_exchange": row.source_exchange,
                        "target_exchange": row.target_exchange,
                        "symbol": row.symbol,
                        "side_a_size": row.side_a_size or 0.0,
                        "side_b_size": row.side_b_size or 0.0,
                        "status": row.status,
                        "entry_time": row.entry_time,
                        "paper_mode": row.paper_mode,
                    }
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[Reconciler] DB查询失败: {e}")

        return result

    def _get_exchange_positions(
        self, exchange_getter: Callable, db_positions: Dict
    ) -> Dict[str, List[Dict[str, Any]]]:
        """从交易所获取真实持仓（按交易所分组）"""
        from backend.services.arbitrage.async_bridge import run_async_safe

        # 收集涉及的交易所
        exchanges = set()
        for pos in db_positions.values():
            if not pos.get("paper_mode", True):
                exchanges.add(pos.get("source_exchange", ""))
                if pos.get("target_exchange"):
                    exchanges.add(pos["target_exchange"])

        exchanges.discard("")

        result: Dict[str, List[Dict]] = {}
        for exch_name in exchanges:
            client = exchange_getter(exch_name)
            if client is None:
                continue

            try:
                positions = run_async_safe(client.get_positions(), default=[])
                result[exch_name] = [
                    {
                        "symbol": p.symbol,
                        "side": p.side,
                        "size": p.size,
                        "entry_price": p.entry_price,
                        "unrealized_pnl": p.unrealized_pnl,
                    }
                    for p in (positions or [])
                ]
            except Exception as e:
                logger.debug(f"[Reconciler] 获取 {exch_name} 持仓失败: {e}")

        return result

    def _reconcile_memory_vs_db(
        self,
        memory: Dict[str, Dict],
        db: Dict[str, Dict],
        report: ReconciliationReport,
    ):
        """对比内存仓位与 DB 仓位"""
        memory_ids = set(memory.keys())
        db_ids = set(db.keys())

        # 内存有 DB 无 → 孤立内存仓位
        for pid in memory_ids - db_ids:
            pos = memory[pid]
            report.issues.append(ReconciliationIssue(
                severity="warning",
                issue_type="orphan_memory",
                position_id=pid,
                exchange=pos.get("source_exchange", ""),
                description=f"仓位仅在内存中，DB无记录 (strategy={pos.get('strategy_type')})",
                suggested_action="close_memory",
            ))

        # DB 有内存无 → 孤立 DB 仓位（需要恢复或清理）
        for pid in db_ids - memory_ids:
            pos = db[pid]
            report.issues.append(ReconciliationIssue(
                severity="warning",
                issue_type="orphan_db",
                position_id=pid,
                exchange=pos.get("source_exchange", ""),
                description=f"仓位仅在DB中，内存无记录 (strategy={pos.get('strategy_type')})",
                suggested_action="manual_review",
            ))

        # 两边都有 → 检查状态和大小一致性
        for pid in memory_ids & db_ids:
            mem_pos = memory[pid]
            db_pos = db[pid]

            # 状态不一致
            if mem_pos.get("status") != db_pos.get("status"):
                report.issues.append(ReconciliationIssue(
                    severity="warning",
                    issue_type="state_mismatch",
                    position_id=pid,
                    description=(
                        f"状态不一致: memory={mem_pos.get('status')}, "
                        f"db={db_pos.get('status')}"
                    ),
                    suggested_action="sync_state",
                    details={"db_status": db_pos.get("status")},
                ))

            # 大小不一致
            mem_size = mem_pos.get("side_a_size", 0)
            db_size = db_pos.get("side_a_size", 0)
            if mem_size > 0 and abs(mem_size - db_size) / mem_size > self.SIZE_TOLERANCE_PCT:
                report.issues.append(ReconciliationIssue(
                    severity="warning",
                    issue_type="size_mismatch",
                    position_id=pid,
                    description=(
                        f"A腿大小不一致: memory=${mem_size:.2f}, "
                        f"db=${db_size:.2f}"
                    ),
                    suggested_action="manual_review",
                    details={"memory_size": mem_size, "db_size": db_size},
                ))

    def _reconcile_db_vs_exchange(
        self,
        db_positions: Dict[str, Dict],
        exchange_positions: Dict[str, List[Dict]],
        report: ReconciliationReport,
    ):
        """对比 DB 仓位与交易所真实持仓"""
        # 只对非 paper 仓位做对比
        live_db_positions = {
            pid: pos for pid, pos in db_positions.items()
            if not pos.get("paper_mode", True)
        }

        if not live_db_positions:
            return

        # 检查 DB 中的 live 仓位是否在交易所有对应持仓
        for pid, pos in live_db_positions.items():
            exchange = pos.get("source_exchange", "")
            symbol = pos.get("symbol", "")

            if exchange not in exchange_positions:
                continue  # 无法获取该交易所数据，跳过

            exch_pos_list = exchange_positions[exchange]
            # 检查是否有对应 symbol 的持仓
            matching = [
                ep for ep in exch_pos_list
                if ep.get("symbol", "") == symbol
            ]

            if not matching:
                report.issues.append(ReconciliationIssue(
                    severity="critical",
                    issue_type="orphan_db",
                    position_id=pid,
                    exchange=exchange,
                    symbol=symbol,
                    description=(
                        f"DB记录仓位但交易所无持仓: "
                        f"{exchange}/{symbol} size=${pos.get('side_a_size', 0):.2f}"
                    ),
                    suggested_action="manual_review",
                    details={"db_size": pos.get("side_a_size", 0)},
                ))

    def _check_stale_positions(
        self, memory_positions: Dict[str, Dict], report: ReconciliationReport
    ):
        """检查过期未清理的仓位"""
        now = time.time()

        for pid, pos in memory_positions.items():
            entry_time = pos.get("entry_time", 0)
            max_hold = pos.get("max_hold_seconds", 86400 * 30)
            elapsed = now - entry_time

            if elapsed > max_hold * self.STALE_MULTIPLIER:
                report.issues.append(ReconciliationIssue(
                    severity="warning",
                    issue_type="stale_position",
                    position_id=pid,
                    exchange=pos.get("source_exchange", ""),
                    description=(
                        f"仓位已超过最大持仓时间 {self.STALE_MULTIPLIER:.0%}: "
                        f"{elapsed / 3600:.1f}h > {max_hold / 3600:.1f}h"
                    ),
                    suggested_action="close_memory",
                    details={
                        "elapsed_hours": elapsed / 3600,
                        "max_hours": max_hold / 3600,
                    },
                ))


# 模块级单例
position_reconciler = PositionReconciler()
