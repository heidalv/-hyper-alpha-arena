"""
CapitalAllocationCoordinator — 资金分配协调器

跨策略资金互斥，防止多策略同时争夺同一资金。
默认分配:
  funding_rate_arb:  40%
  cross_exchange_spread: 25%
  rebate_points_arb: 25%
  emergency_reserve: 10%

资金分配写入 Layer A 确定性状态，LLM 不可覆盖。
跨池操作使用互斥锁保证原子性。

Phase D Enhancement:
- 从 YAML 配置加载分配比例
- 状态通过 DB 持久化（重启后恢复）
- 定期同步到 DB
"""

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .models import CapitalAllocation

logger = logging.getLogger(__name__)


# 默认资金分配比例
DEFAULT_ALLOCATION = {
    "funding_rate_arb": 0.40,
    "cross_exchange_spread": 0.25,
    "rebate_points_arb": 0.25,
    "emergency_reserve": 0.10,
}


class CapitalAllocationCoordinator:
    """资金分配协调器 — 跨池互斥 + DB 持久化 + Paper 模式支持"""

    # DB 持久化使用的特殊 position_id
    _DB_STATE_KEY = "__capital_allocation_state__"

    def __init__(self, allocation_config: Optional[Dict[str, float]] = None):
        self._config = allocation_config or DEFAULT_ALLOCATION.copy()
        self._allocation = CapitalAllocation()
        self._lock = threading.Lock()
        self._history: List[Dict[str, Any]] = []
        self._dirty = False  # 标记是否有未持久化的变更
        self._last_persist_time = 0.0

        # Paper 模式状态
        self._paper_mode = False
        self._paper_account_id: Optional[int] = None
        self._arbitrage_paper_account_id: Optional[int] = None
        self._strategy_sub_pools: Dict[str, float] = {}
        self._strategy_used: Dict[str, float] = {}

        self._load_config()

    def _load_config(self):
        """从 YAML 加载资金分配配置"""
        try:
            from backend.config.rebate_config_loader import rebate_config
            if rebate_config:
                cfg = rebate_config.capital_allocation
                self._config = {
                    "funding_rate_arb": cfg.funding_rate_arb,
                    "cross_exchange_spread": cfg.cross_exchange_spread,
                    "rebate_points_arb": cfg.rebate_points_arb,
                    "emergency_reserve": cfg.emergency_reserve,
                }
                subs = getattr(cfg, "strategy_sub_pools", None) or {}
                if isinstance(subs, dict) and subs:
                    self._strategy_sub_pools = {k.upper(): float(v) for k, v in subs.items()}
        except Exception as e:
            logger.debug(f"[CapitalCoordinator] Config load fallback: {e}")

    def initialize(self, total_equity: float, *, force_reset: bool = False) -> CapitalAllocation:
        """根据总权益初始化各池资金（优先从 DB 恢复，同步全局协调器）"""
        try:
            from backend.services.arbitrage.global_capital_coordinator import (
                global_capital_coordinator,
            )
            if force_reset:
                global_capital_coordinator.reset_pools(total_equity)
            else:
                global_capital_coordinator.update_equity(total_equity)
        except Exception:
            pass

        # 尝试从 DB 恢复（Paper 验证启动时可 force_reset 避免历史占用卡住）
        if not force_reset:
            restored = self._load_from_db()
            if restored and abs(restored.total_equity - total_equity) / max(total_equity, 1) < 0.05:
                # DB 状态有效且权益偏差 < 5%，直接使用
                with self._lock:
                    self._allocation = restored
                logger.info(
                    f"[CapitalCoordinator] 从DB恢复: "
                    f"总权益=${restored.total_equity:,.0f}, "
                    f"返利池已用=${restored.used.get('rebate_points_arb', 0):,.0f}"
                )
                return self._allocation

        # 全新初始化
        with self._lock:
            self._allocation = CapitalAllocation(
                total_equity=total_equity,
                allocations={
                    pool: total_equity * ratio
                    for pool, ratio in self._config.items()
                },
                used={pool: 0.0 for pool in self._config},
                locked=False,
            )
            self._strategy_used = {}
            self._dirty = True
            logger.info(
                f"[CapitalCoordinator] 初始化完成: "
                f"总权益=${total_equity:,.0f}, "
                f"返利池=${self._allocation.allocations.get('rebate_points_arb', 0):,.0f}"
                f"{' (force_reset)' if force_reset else ''}"
            )
        self._maybe_persist()
        return self._allocation

    def request_capital(
        self,
        pool: str,
        amount_usd: float,
        strategy_id: str = "",
    ) -> Dict[str, Any]:
        """
        请求分配资金

        Args:
            pool: 资金池名称
            amount_usd: 请求金额
            strategy_id: 策略标识

        Returns:
            {"granted": bool, "amount": float, "remaining": float}
        """
        with self._lock:
            allocated = self._allocation.allocations.get(pool, 0.0)
            used = self._allocation.used.get(pool, 0.0)
            available = allocated - used

            # 全局协调器二次校验（防止 V3 + Rebate 超额分配）
            try:
                from backend.services.arbitrage.global_capital_coordinator import (
                    global_capital_coordinator,
                )
                global_available = global_capital_coordinator.get_pool_available(pool)
                available = min(available, global_available)
            except Exception:
                pass

            if amount_usd > available:
                logger.warning(
                    f"[CapitalCoordinator] 资金不足: {pool} 可用=${available:,.0f}, "
                    f"请求=${amount_usd:,.0f}"
                )
                return {"granted": False, "amount": 0.0, "remaining": available}

            sid = (strategy_id or "").upper()
            if sid and pool == "rebate_points_arb" and self._strategy_sub_pools:
                sub_avail = self._get_strategy_sub_available(sid)
                if amount_usd > sub_avail:
                    logger.warning(
                        f"[CapitalCoordinator] 策略子池不足: {sid} 可用=${sub_avail:,.0f}, "
                        f"请求=${amount_usd:,.0f}"
                    )
                    return {"granted": False, "amount": 0.0, "remaining": sub_avail}

            self._allocation.used[pool] = used + amount_usd
            if sid and pool == "rebate_points_arb":
                self._strategy_used[sid] = self._strategy_used.get(sid, 0.0) + amount_usd
            self._dirty = True
            self._history.append({
                "action": "allocate",
                "pool": pool,
                "amount": amount_usd,
                "strategy_id": strategy_id,
                "ts": time.time(),
            })

            # 同步全局协调器
            try:
                from backend.services.arbitrage.global_capital_coordinator import (
                    global_capital_coordinator,
                )
                global_capital_coordinator.request(pool, amount_usd, strategy_id)
            except Exception:
                pass

            remaining = self._allocation.allocations.get(pool, 0.0) - self._allocation.used.get(pool, 0.0)
            logger.info(
                f"[CapitalCoordinator] 分配 ${amount_usd:,.0f} → {pool} ({strategy_id}), "
                f"剩余=${remaining:,.0f}"
            )
        self._maybe_persist()
        return {"granted": True, "amount": amount_usd, "remaining": remaining}

    def release_capital(
        self,
        pool: str,
        amount_usd: float,
        strategy_id: str = "",
    ) -> Dict[str, Any]:
        """释放资金回池"""
        with self._lock:
            used = self._allocation.used.get(pool, 0.0)
            released = min(amount_usd, used)
            self._allocation.used[pool] = used - released
            sid = (strategy_id or "").upper()
            if sid and sid in self._strategy_used:
                self._strategy_used[sid] = max(0.0, self._strategy_used[sid] - released)
            self._dirty = True

            self._history.append({
                "action": "release",
                "pool": pool,
                "amount": released,
                "strategy_id": strategy_id,
                "ts": time.time(),
            })

        try:
            from backend.services.arbitrage.global_capital_coordinator import (
                global_capital_coordinator,
            )
            global_capital_coordinator.release(pool, released, strategy_id)
        except Exception:
            pass

        self._maybe_persist()
        return {"released": released, "pool": pool}

    def get_status(self) -> CapitalAllocation:
        """获取当前资金分配状态"""
        with self._lock:
            return CapitalAllocation(
                total_equity=self._allocation.total_equity,
                allocations=self._allocation.allocations.copy(),
                used=self._allocation.used.copy(),
                locked=self._allocation.locked,
            )

    def update_equity(self, new_equity: float) -> None:
        """更新总权益并重新分配"""
        with self._lock:
            # Paper 模式下从 PaperBalance 读取权益（查询放在锁内避免 TOCTOU）
            if self._paper_mode and self._arbitrage_paper_account_id:
                paper_equity = self._get_arbitrage_paper_equity()
                if paper_equity > 0:
                    new_equity = paper_equity
            elif self._paper_mode and self._paper_account_id:
                paper_equity = self._get_paper_equity()
                if paper_equity > 0:
                    new_equity = paper_equity

            old_equity = self._allocation.total_equity
            if abs(new_equity - old_equity) / max(old_equity, 1.0) < 0.01:
                return  # 变化 <1%，不调整

            # 保持已用比例不变，调整分配额度
            for pool in self._config:
                old_alloc = self._allocation.allocations.get(pool, 0.0)
                old_used = self._allocation.used.get(pool, 0.0)
                new_alloc = new_equity * self._config[pool]
                # 如果已用超新分配，截断
                self._allocation.allocations[pool] = new_alloc
                self._allocation.used[pool] = min(old_used, new_alloc)

            self._allocation.total_equity = new_equity
            self._dirty = True
            logger.info(
                f"[CapitalCoordinator] 权益更新: ${old_equity:,.0f} → ${new_equity:,.0f}"
            )
        self._maybe_persist()

    def get_rebate_available(self) -> float:
        """获取返利池可用资金"""
        with self._lock:
            return self._allocation.available_for_rebate

    def get_all_utilization(self) -> Dict[str, float]:
        """获取各池利用率"""
        with self._lock:
            result = {}
            for pool in self._allocation.allocations:
                alloc = self._allocation.allocations[pool]
                used = self._allocation.used.get(pool, 0.0)
                result[pool] = used / max(alloc, 1.0)
            return result

    # ── DB 持久化 ──

    def _maybe_persist(self):
        """节流持久化（最多每 30 秒一次）"""
        if not self._dirty:
            return
        now = time.time()
        if now - self._last_persist_time < 30:
            return  # 30 秒内不重复写
        self._persist_to_db()

    def _persist_to_db(self):
        """将资金分配状态写入 DB"""
        try:
            from backend.database.connection import SessionLocal, sqlite_write_commit
            from backend.database.models import RebatePerformanceLogDB

            state = {
                "total_equity": self._allocation.total_equity,
                "allocations": self._allocation.allocations,
                "used": self._allocation.used,
                "config": self._config,
            }

            db = SessionLocal()
            try:
                db.query(RebatePerformanceLogDB).filter(
                    RebatePerformanceLogDB.position_id == self._DB_STATE_KEY
                ).delete()

                entry = RebatePerformanceLogDB(
                    position_id=self._DB_STATE_KEY,
                    strategy_type="CAP",
                    total_pnl=self._allocation.total_equity,
                    total_rebate=self._allocation.used.get("rebate_points_arb", 0.0),
                    total_points=0.0,
                    hold_hours=0.0,
                    close_reason=json.dumps(state, default=str),
                )
                db.add(entry)
                sqlite_write_commit(db, label="capital_state_persist")
                self._dirty = False
                self._last_persist_time = time.time()
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CapitalCoordinator] 持久化失败: {e}")

    def _load_from_db(self) -> Optional[CapitalAllocation]:
        """从 DB 恢复资金分配状态"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import RebatePerformanceLogDB

            db = SessionLocal()
            try:
                row = db.query(RebatePerformanceLogDB).filter(
                    RebatePerformanceLogDB.position_id == self._DB_STATE_KEY
                ).order_by(RebatePerformanceLogDB.id.desc()).first()

                if row and row.close_reason:
                    state = json.loads(row.close_reason)
                    return CapitalAllocation(
                        total_equity=state.get("total_equity", 0.0),
                        allocations=state.get("allocations", {}),
                        used=state.get("used", {}),
                        locked=False,
                    )
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CapitalCoordinator] DB恢复失败: {e}")

        return None

    def force_persist(self):
        """强制立即持久化（用于关机前调用）"""
        self._dirty = True
        self._last_persist_time = 0
        self._persist_to_db()

    # ── Paper 模式 ──

    def set_paper_mode(self, is_paper: bool, paper_account_id: Optional[int] = None) -> None:
        """切换 paper/live 模式"""
        self._paper_mode = is_paper
        self._paper_account_id = paper_account_id if is_paper else None
        if not is_paper:
            self._arbitrage_paper_account_id = None
        if is_paper and paper_account_id:
            # Paper 模式激活时，立即用 PaperBalance 权益初始化
            paper_equity = self._get_paper_equity()
            if paper_equity > 0:
                self.initialize(paper_equity)
            logger.info(
                f"[CapitalCoordinator] Paper 模式: account_id={paper_account_id}, "
                f"equity=${paper_equity:,.0f}"
            )
        else:
            logger.info("[CapitalCoordinator] Live 模式")

    def set_arbitrage_paper_account(self, account_id: Optional[int]) -> None:
        """绑定套利专用 Paper 账户；优先级高于旧 AI PaperBalance。"""
        self._paper_mode = bool(account_id)
        self._arbitrage_paper_account_id = account_id
        if account_id:
            paper_equity = self._get_arbitrage_paper_equity()
            if paper_equity > 0:
                self.initialize(paper_equity, force_reset=True)
            logger.info(
                f"[CapitalCoordinator] 套利专用 Paper 模式: account_id={account_id}, "
                f"equity=${paper_equity:,.0f}"
            )

    def get_arbitrage_paper_account_id(self) -> Optional[int]:
        return self._arbitrage_paper_account_id

    def get_strategy_sub_available(self, strategy_id: str) -> float:
        """策略子池可用额度（供 coordinator 计算下单规模）。"""
        with self._lock:
            return self._get_strategy_sub_available(strategy_id)

    def _get_strategy_sub_available(self, strategy_id: str) -> float:
        """rebate_points_arb 池内按 S1–S8 子配额可用额度。"""
        sid = (strategy_id or "").upper()
        pct = self._strategy_sub_pools.get(sid)
        if pct is None or pct <= 0:
            return self.get_rebate_available()
        rebate_pool = self._allocation.allocations.get("rebate_points_arb", 0.0)
        cap = rebate_pool * pct
        used = self._strategy_used.get(sid, 0.0)
        return max(0.0, cap - used)

    def get_strategy_sub_pool_status(self) -> Dict[str, Any]:
        """返回各策略子池占用（供 Hub / QAA 只读）。"""
        rebate_pool = self._allocation.allocations.get("rebate_points_arb", 0.0)
        out: Dict[str, Any] = {}
        for sid, pct in self._strategy_sub_pools.items():
            cap = rebate_pool * pct
            used = self._strategy_used.get(sid, 0.0)
            out[sid] = {"cap_usd": round(cap, 2), "used_usd": round(used, 2), "pct": pct}
        return out

    def is_paper_mode(self) -> bool:
        """是否处于 paper 模式"""
        return self._paper_mode

    def get_paper_account_id(self) -> Optional[int]:
        """获取关联的 paper 账户 ID"""
        return self._paper_account_id

    def _get_paper_equity(self) -> float:
        """从 PaperBalance 读取虚拟权益"""
        if not self._paper_account_id:
            return 0.0
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import PaperBalance
            db = SessionLocal()
            try:
                pb = db.query(PaperBalance).filter(
                    PaperBalance.account_id == self._paper_account_id
                ).first()
                return pb.total_equity if pb else 0.0
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CapitalCoordinator] PaperBalance 读取失败: {e}")
            return 0.0

    def _get_arbitrage_paper_equity(self) -> float:
        """从套利专用 Paper 总账户读取权益。"""
        if not self._arbitrage_paper_account_id:
            return 0.0
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import ArbitragePaperAccountDB
            db = SessionLocal()
            try:
                row = db.query(ArbitragePaperAccountDB).filter(
                    ArbitragePaperAccountDB.id == self._arbitrage_paper_account_id
                ).first()
                return float(row.total_equity or 0.0) if row else 0.0
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[CapitalCoordinator] 套利Paper账户读取失败: {e}")
            return 0.0


# 模块级单例
capital_coordinator = CapitalAllocationCoordinator()
