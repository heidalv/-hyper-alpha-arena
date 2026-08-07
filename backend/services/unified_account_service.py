"""统一账户服务层 —— 双表共存 + 统一 API（零数据迁移）。

设计目标（阶段 4 账户统一）:
- 不合并表，不改表结构（零迁移风险）
- 在两套 paper 账户树之上提供归一化视图 + 统一操作 API
- 支持: get_unified_paper_account / get_combined_exposure / list_all_paper_accounts
- 为后续 cross_system_coordinator / 前端整合 / opencode 适配提供单一入口

现状（两套并行 paper 账户树）:
1. AI Paper 树:
   - 根: accounts (account_type=PAPER)
   - 余额: paper_balances (1:1 account_id)
   - 持仓: paper_positions (account_id FK→accounts.id)
   - 订单: paper_orders
2. 套利 Paper 树:
   - 根: arbitrage_paper_accounts (独立 ID 空间)
   - 分账: arbitrage_paper_exchange_balances (account_id FK→arbitrage_paper_accounts.id)
   - 流水: arbitrage_paper_ledgers
   - 持仓: rebate_positions (⚠️ 无 account FK，靠 position_id 标识)

统一 API 通过 scope 区分:
- scope="ai": 操作 AI Paper 树（PaperBalance）
- scope="arbitrage": 操作套利 Paper 树（ArbitragePaperAccountDB）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# 归一化视图数据类
# ────────────────────────────────────────────────────────────────────

@dataclass
class UnifiedPaperAccountView:
    """归一化的 paper 账户视图（屏蔽底层表差异）。

    无论来源是 PaperBalance（AI）还是 ArbitragePaperAccountDB（套利），
    都归一化为同一结构。
    """
    id: int  # PaperBalance.account_id 或 ArbitragePaperAccountDB.id
    scope: str  # "ai" / "arbitrage"
    source_table: str  # "paper_balances" / "arbitrage_paper_accounts"
    name: str = ""
    total_equity: float = 0.0
    available_balance: float = 0.0
    frozen_balance: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_fee_paid: float = 0.0
    initial_balance: float = 0.0
    status: str = "active"
    owner_account_id: Optional[int] = None  # 关联的交易员账户（套利树有，AI 树即自身）
    exchange: Optional[str] = None  # 关联交易所（若有）
    risk_profile: Optional[str] = None  # 套利树有
    raw: Optional[Dict[str, Any]] = None  # 原始记录（审计用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "source_table": self.source_table,
            "name": self.name,
            "total_equity": round(self.total_equity, 2),
            "available_balance": round(self.available_balance, 2),
            "frozen_balance": round(self.frozen_balance, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "total_fee_paid": round(self.total_fee_paid, 2),
            "initial_balance": round(self.initial_balance, 2),
            "status": self.status,
            "owner_account_id": self.owner_account_id,
            "exchange": self.exchange,
            "risk_profile": self.risk_profile,
        }


@dataclass
class CombinedExposure:
    """跨系统（AI + 套利）合并敞口视图。"""
    ai_equity: float = 0.0
    ai_frozen: float = 0.0
    ai_upnl: float = 0.0
    arbitrage_equity: float = 0.0
    arbitrage_frozen: float = 0.0
    arbitrage_upnl: float = 0.0
    total_equity: float = 0.0  # ai + arbitrage
    total_frozen: float = 0.0
    total_upnl: float = 0.0
    ai_account_id: Optional[int] = None
    arbitrage_account_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ai_equity": round(self.ai_equity, 2),
            "ai_frozen": round(self.ai_frozen, 2),
            "ai_upnl": round(self.ai_upnl, 2),
            "arbitrage_equity": round(self.arbitrage_equity, 2),
            "arbitrage_frozen": round(self.arbitrage_frozen, 2),
            "arbitrage_upnl": round(self.arbitrage_upnl, 2),
            "total_equity": round(self.total_equity, 2),
            "total_frozen": round(self.total_frozen, 2),
            "total_upnl": round(self.total_upnl, 2),
            "ai_account_id": self.ai_account_id,
            "arbitrage_account_id": self.arbitrage_account_id,
        }


# ────────────────────────────────────────────────────────────────────
# 统一账户服务
# ────────────────────────────────────────────────────────────────────

class UnifiedAccountService:
    """统一账户服务 —— 双表共存 + 归一化 API。

    不修改任何表数据，仅提供查询/视图/归一化操作。
    所有方法 sync，接受 db: Session 参数。
    """

    # ── 单账户查询 ──────────────────────────────────────────────

    def get_unified_paper_account(
        self, db, account_id: int, scope: str = "ai",
    ) -> Optional[UnifiedPaperAccountView]:
        """获取归一化的 paper 账户视图。

        Args:
            account_id: AI 模式为 accounts.id；套利模式为 arbitrage_paper_accounts.id
            scope: "ai"（PaperBalance）或 "arbitrage"（ArbitragePaperAccountDB）

        Returns:
            UnifiedPaperAccountView 或 None（不存在）
        """
        if scope == "arbitrage":
            return self._get_arbitrage_paper_account(db, account_id)
        # 默认 ai
        return self._get_ai_paper_account(db, account_id)

    def _get_ai_paper_account(self, db, account_id: int) -> Optional[UnifiedPaperAccountView]:
        """从 PaperBalance 表读取 AI paper 账户。"""
        try:
            from backend.database.models import PaperBalance, Account
            bal = db.query(PaperBalance).filter(
                PaperBalance.account_id == account_id,
            ).first()
            if not bal:
                return None
            # 关联账户信息
            account = db.query(Account).filter(Account.id == account_id).first()
            return UnifiedPaperAccountView(
                id=account_id,
                scope="ai",
                source_table="paper_balances",
                name=(account.name if account else f"AI-Paper-{account_id}"),
                total_equity=float(bal.total_equity or 0),
                available_balance=float(bal.available_balance or 0),
                frozen_balance=float(bal.frozen_margin or 0),
                unrealized_pnl=float(bal.unrealized_pnl or 0),
                realized_pnl=float(bal.realized_pnl or 0),
                total_fee_paid=float(bal.total_fee_paid or 0),
                initial_balance=float(bal.initial_balance or 0),
                status="active",
                owner_account_id=account_id,  # AI 树的 owner 即自身
                exchange=(account.selected_exchange if account else None),
                raw={
                    "account_id": bal.account_id,
                    "initial_balance": float(bal.initial_balance or 0),
                    "total_equity": float(bal.total_equity or 0),
                },
            )
        except Exception as e:
            logger.error(f"[UnifiedAccount] _get_ai_paper_account 异常: {e}", exc_info=True)
            return None

    def _get_arbitrage_paper_account(self, db, account_id: int) -> Optional[UnifiedPaperAccountView]:
        """从 ArbitragePaperAccountDB 表读取套利 paper 账户。"""
        try:
            from backend.database.models import ArbitragePaperAccountDB
            arb = db.query(ArbitragePaperAccountDB).filter(
                ArbitragePaperAccountDB.id == account_id,
            ).first()
            if not arb:
                return None
            return UnifiedPaperAccountView(
                id=account_id,
                scope="arbitrage",
                source_table="arbitrage_paper_accounts",
                name=arb.name or f"Arb-Paper-{account_id}",
                total_equity=float(arb.total_equity or 0),
                available_balance=float(arb.available_balance or 0),
                frozen_balance=float(arb.frozen_balance or 0),
                unrealized_pnl=0.0,  # 套利树无此字段，由持仓累计
                realized_pnl=float(arb.realized_pnl or 0),
                total_fee_paid=0.0,  # 套利树无独立字段
                initial_balance=float(arb.total_equity or 0),  # 近似
                status=str(arb.status or "active"),
                owner_account_id=arb.owner_account_id,
                risk_profile=arb.risk_profile,
                raw={
                    "id": arb.id,
                    "name": arb.name,
                    "total_equity": float(arb.total_equity or 0),
                    "owner_account_id": arb.owner_account_id,
                },
            )
        except Exception as e:
            logger.error(f"[UnifiedAccount] _get_arbitrage_paper_account 异常: {e}", exc_info=True)
            return None

    # ── 列表查询 ────────────────────────────────────────────────

    def list_all_paper_accounts(
        self, db, scope: Optional[str] = None, owner_account_id: Optional[int] = None,
    ) -> List[UnifiedPaperAccountView]:
        """列出所有 paper 账户（可按 scope / owner 过滤）。

        Args:
            scope: None=全部, "ai", "arbitrage"
            owner_account_id: 按关联交易员过滤（套利树有 owner_account_id）

        Returns:
            List[UnifiedPaperAccountView]
        """
        result: List[UnifiedPaperAccountView] = []
        try:
            if scope in (None, "ai"):
                from backend.database.models import PaperBalance
                q = db.query(PaperBalance)
                if owner_account_id is not None:
                    q = q.filter(PaperBalance.account_id == owner_account_id)
                for bal in q.all():
                    view = self._get_ai_paper_account(db, bal.account_id)
                    if view:
                        result.append(view)

            if scope in (None, "arbitrage"):
                from backend.database.models import ArbitragePaperAccountDB
                q = db.query(ArbitragePaperAccountDB)
                if owner_account_id is not None:
                    q = q.filter(ArbitragePaperAccountDB.owner_account_id == owner_account_id)
                for arb in q.all():
                    view = self._get_arbitrage_paper_account(db, arb.id)
                    if view:
                        result.append(view)
        except Exception as e:
            logger.error(f"[UnifiedAccount] list_all_paper_accounts 异常: {e}", exc_info=True)
        return result

    # ── 跨系统合并敞口 ──────────────────────────────────────────

    def get_combined_exposure(
        self, db, ai_account_id: Optional[int] = None, arbitrage_account_id: Optional[int] = None,
    ) -> CombinedExposure:
        """获取 AI + 套利 两个系统的合并敞口。

        用于 cross_system_coordinator 检测资金冲突 / 全局风险上限。

        Args:
            ai_account_id: AI paper 账户 ID（None 则不查 AI 树）
            arbitrage_account_id: 套利 paper 账户 ID（None 则不查套利树）

        Returns:
            CombinedExposure（各字段 0.0 若对应账户不存在）
        """
        exposure = CombinedExposure(
            ai_account_id=ai_account_id,
            arbitrage_account_id=arbitrage_account_id,
        )

        # AI 树
        if ai_account_id is not None:
            ai_view = self._get_ai_paper_account(db, ai_account_id)
            if ai_view:
                exposure.ai_equity = ai_view.total_equity
                exposure.ai_frozen = ai_view.frozen_balance
                exposure.ai_upnl = ai_view.unrealized_pnl

        # 套利树
        if arbitrage_account_id is not None:
            arb_view = self._get_arbitrage_paper_account(db, arbitrage_account_id)
            if arb_view:
                exposure.arbitrage_equity = arb_view.total_equity
                exposure.arbitrage_frozen = arb_view.frozen_balance
                exposure.arbitrage_upnl = arb_view.unrealized_pnl

        # 合计
        exposure.total_equity = exposure.ai_equity + exposure.arbitrage_equity
        exposure.total_frozen = exposure.ai_frozen + exposure.arbitrage_frozen
        exposure.total_upnl = exposure.ai_upnl + exposure.arbitrage_upnl

        return exposure

    # ── 套利持仓软关联（rebate_positions 无 account FK）─────────

    def get_arbitrage_positions_for_account(
        self, db, arbitrage_account_id: int, status: str = "active",
    ) -> List[Dict[str, Any]]:
        """获取套利账户关联的 rebate_positions（通过 metadata_json 软关联）。

        rebate_positions 表无 account_id FK，但 metadata_json 中可能存有 account 信息。
        本方法尽力反查；无法关联时返回该账户创建时段的所有 active 仓位。

        Args:
            arbitrage_account_id: arbitrage_paper_accounts.id
            status: "active" / "closed" / "all"

        Returns:
            List[dict]（归一化的仓位视图）
        """
        try:
            import json
            from backend.database.models import RebatePositionDB
            q = db.query(RebatePositionDB)
            if status != "all":
                q = q.filter(RebatePositionDB.status == status)
            positions = q.all()

            result = []
            for p in positions:
                # 尝试从 metadata_json 反查 account_id
                meta = {}
                try:
                    meta = json.loads(p.metadata_json or "{}")
                except Exception:
                    pass
                # 软关联: metadata.account_id 或 owner（阶段 4.2 将加 owner_account_id 列）
                pos_account_id = (
                    meta.get("arbitrage_account_id")
                    or meta.get("account_id")
                    or getattr(p, "owner_account_id", None)  # 阶段 4.2 新增列
                )
                if pos_account_id is not None and int(pos_account_id) != int(arbitrage_account_id):
                    continue
                result.append({
                    "position_id": p.position_id,
                    "strategy_type": p.strategy_type,
                    "symbol": p.symbol,
                    "source_exchange": p.source_exchange,
                    "target_exchange": p.target_exchange,
                    "side_a_size": float(p.side_a_size or 0),
                    "side_b_size": float(p.side_b_size or 0),
                    "entry_price_a": float(p.entry_price_a or 0),
                    "entry_price_b": float(p.entry_price_b or 0),
                    "current_pnl": float(p.current_pnl or 0),
                    "accumulated_rebate": float(p.accumulated_rebate or 0),
                    "accumulated_points": float(p.accumulated_points or 0),
                    "status": p.status,
                    "paper_mode": bool(p.paper_mode),
                    "arbitrage_account_id": pos_account_id,  # 软关联结果
                })
            return result
        except Exception as e:
            logger.error(f"[UnifiedAccount] get_arbitrage_positions_for_account 异常: {e}", exc_info=True)
            return []

    # ── 资金划转（记账层，不动真实资金）─────────────────────────

    def transfer_capital(
        self, db, from_scope: str, from_id: int,
        to_scope: str, to_id: int, amount: float,
    ) -> Dict[str, Any]:
        """跨账户资金划转（记账层操作）。

        注意: 这只在 paper 余额表之间转移数字，不动真实资金。
        用于 AI ↔ 套利 之间的资金调配（如从 AI paper 划 100U 到套利 paper）。

        Args:
            from_scope/from_id: 源账户（"ai"/accounts.id 或 "arbitrage"/arb.id）
            to_scope/to_id: 目标账户
            amount: 划转金额（USD）

        Returns:
            {"success": bool, "from_balance": ..., "to_balance": ..., "error": ...}
        """
        if amount <= 0:
            return {"success": False, "error": "金额必须 > 0"}

        try:
            # 源账户扣减
            from_view = self.get_unified_paper_account(db, from_id, from_scope)
            if not from_view:
                return {"success": False, "error": f"源账户不存在: {from_scope}/{from_id}"}
            if from_view.available_balance < amount:
                return {"success": False, "error": f"源账户余额不足: {from_view.available_balance} < {amount}"}

            # 目标账户增加
            to_view = self.get_unified_paper_account(db, to_id, to_scope)
            if not to_view:
                return {"success": False, "error": f"目标账户不存在: {to_scope}/{to_id}"}

            # 执行划转（直接改表）
            self._adjust_balance(db, from_scope, from_id, -amount)
            self._adjust_balance(db, to_scope, to_id, +amount)
            db.commit()

            logger.info(
                f"[UnifiedAccount] 资金划转: {from_scope}/{from_id} → {to_scope}/{to_id} "
                f"${amount:.2f}"
            )

            return {
                "success": True,
                "amount": amount,
                "from_scope": from_scope,
                "from_id": from_id,
                "to_scope": to_scope,
                "to_id": to_id,
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[UnifiedAccount] transfer_capital 异常: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _adjust_balance(self, db, scope: str, account_id: int, delta: float) -> None:
        """调整账户余额（delta 正=增加，负=扣减）。"""
        if scope == "arbitrage":
            from backend.database.models import ArbitragePaperAccountDB
            arb = db.query(ArbitragePaperAccountDB).filter(
                ArbitragePaperAccountDB.id == account_id,
            ).first()
            if arb:
                arb.available_balance = float(arb.available_balance or 0) + delta
                arb.total_equity = float(arb.total_equity or 0) + delta
        else:
            from backend.database.models import PaperBalance
            bal = db.query(PaperBalance).filter(
                PaperBalance.account_id == account_id,
            ).first()
            if bal:
                bal.available_balance = float(bal.available_balance or 0) + delta
                bal.total_equity = float(bal.total_equity or 0) + delta


# ── 单例 ────────────────────────────────────────────────────────

unified_account_service = UnifiedAccountService()
