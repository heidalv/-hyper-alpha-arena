"""Arbitrage Paper account service.

This is separate from the generic AI-strategy PaperTrading account so Rebate/V3
arbitrage can model total capital, exchange sub-accounts, reserves and ledgers.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from backend.database.connection import sqlite_write_commit
from backend.database.models import (
    Account,
    ArbitragePaperAccountDB,
    ArbitragePaperAllocationPresetDB,
    ArbitragePaperExchangeBalanceDB,
    ArbitragePaperLedgerDB,
    ArbitrageProfileDB,
    RuleChangeEventDB,
)
from backend.services.rebate_arb.schema import ensure_rebate_schema
from backend.services.rebate_arb.trader_llm_resolver import resolve_trader_llm_pair


EXCHANGES = ["asterdex", "hyperliquid", "binance", "okx", "bybit", "gateio", "reserve"]
# M4: S1/S5 已下线，不再出现在策略分账列表（历史数据仍可按旧 ID 查询）
STRATEGY_IDS = ["S2", "S3", "S4", "S6", "S7", "S8"]

SYSTEM_PRESETS: Dict[str, Dict[str, Any]] = {
    "small_300u_standard": {
        "name": "300U 小资金标准版",
        "description": "适合先验证 S3/S8，保留 10% reserve。",
        "risk_profile": "balanced",
        "total_equity_hint": 300.0,
        "exchange_ratios": {
            "asterdex": 0.45,
            "hyperliquid": 0.25,
            "binance": 0.20,
            "reserve": 0.10,
        },
        "strategy_limits": {"S3": 0.25, "S8": 0.45, "S7": 0.0},
    },
    "balanced_1000u": {
        "name": "1000U 均衡版",
        "description": "适合多所观察，OKX/Bybit/Gate 首期偏告警与小额验证。",
        "risk_profile": "balanced",
        "total_equity_hint": 1000.0,
        "exchange_ratios": {
            "asterdex": 0.35,
            "hyperliquid": 0.25,
            "binance": 0.20,
            "okx": 0.04,
            "bybit": 0.03,
            "gateio": 0.03,
            "reserve": 0.10,
        },
        # M4: S1/S5 下线、S6 关闭，配额集中到 S3/S8
        "strategy_limits": {"S3": 0.35, "S8": 0.45},
    },
    "s8_rh_focus": {
        "name": "S8 Rh 专用版",
        "description": "Asterdex 权重最高，用于 Rh/ASTER 纸面验证。",
        "risk_profile": "aggressive",
        "total_equity_hint": 300.0,
        "exchange_ratios": {
            "asterdex": 0.70,
            "binance": 0.10,
            "hyperliquid": 0.10,
            "reserve": 0.10,
        },
        "strategy_limits": {"S8": 0.70, "S7": 0.0},
    },
    "conservative_reserve": {
        "name": "风险保守版",
        "description": "Reserve 25%，单交易所默认不超过 40%。",
        "risk_profile": "conservative",
        "total_equity_hint": 300.0,
        "exchange_ratios": {
            "asterdex": 0.35,
            "hyperliquid": 0.25,
            "binance": 0.15,
            "reserve": 0.25,
        },
        "strategy_limits": {"S3": 0.20, "S8": 0.25},
    },
    "single_asterdex_s8": {
        "name": "单所集中 · Asterdex (S8)",
        "description": "全部资金集中到 Asterdex，适合只跑 S8/Rh 验证。",
        "risk_profile": "aggressive",
        "total_equity_hint": 300.0,
        "exchange_ratios": {
            "asterdex": 1.0,
        },
        "strategy_limits": {"S8": 1.0},
    },
}


def _loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default


class ArbitragePaperAccountService:
    def ensure_schema(self) -> None:
        ensure_rebate_schema()

    def seed_presets(self, db: Session) -> None:
        self.ensure_schema()
        changed = False
        for preset_id, preset in SYSTEM_PRESETS.items():
            row = (
                db.query(ArbitragePaperAllocationPresetDB)
                .filter(ArbitragePaperAllocationPresetDB.preset_id == preset_id)
                .first()
            )
            if not row:
                row = ArbitragePaperAllocationPresetDB(preset_id=preset_id)
                db.add(row)
                changed = True
            row.name = preset["name"]
            row.description = preset["description"]
            row.risk_profile = preset["risk_profile"]
            row.total_equity_hint = preset.get("total_equity_hint")
            row.exchange_ratios_json = json.dumps(preset["exchange_ratios"], ensure_ascii=False)
            row.strategy_limits_json = json.dumps(preset.get("strategy_limits", {}), ensure_ascii=False)
        if changed:
            sqlite_write_commit(db, label="seed_arbitrage_paper_presets")

    def list_presets(self, db: Session) -> List[Dict[str, Any]]:
        self.seed_presets(db)
        rows = db.query(ArbitragePaperAllocationPresetDB).order_by(ArbitragePaperAllocationPresetDB.id.asc()).all()
        return [self._preset_to_dict(row) for row in rows]

    def create_account(
        self,
        db: Session,
        *,
        name: str,
        total_equity: float,
        owner_account_id: Optional[int] = None,
        preset_id: str = "small_300u_standard",
        risk_profile: str = "balanced",
    ) -> Dict[str, Any]:
        self.seed_presets(db)
        if owner_account_id:
            owner = db.query(Account).filter(Account.id == owner_account_id).first()
            if not owner:
                raise ValueError("交易员账户不存在")
        total = max(float(total_equity or 0), 1.0)
        account = ArbitragePaperAccountDB(
            name=name.strip() or "套利 Paper 账户",
            owner_account_id=owner_account_id,
            total_equity=total,
            available_balance=total,
            frozen_balance=0.0,
            risk_profile=risk_profile or "balanced",
            allocation_preset=preset_id,
        )
        db.add(account)
        db.flush()
        self._apply_preset_rows(db, account, preset_id, total)
        self._ledger(db, account.id, None, "create_account", total, total, note=f"创建套利 Paper 账户: {account.name}")
        sqlite_write_commit(db, label="create_arbitrage_paper_account")
        db.refresh(account)
        return self.get_account(db, account.id)

    def list_accounts(self, db: Session, owner_account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        self.ensure_schema()
        query = db.query(ArbitragePaperAccountDB)
        if owner_account_id:
            query = query.filter(ArbitragePaperAccountDB.owner_account_id == owner_account_id)
        rows = query.order_by(ArbitragePaperAccountDB.id.desc()).all()
        return [self._account_to_dict(db, row) for row in rows]

    def get_account(self, db: Session, account_id: int) -> Dict[str, Any]:
        self.ensure_schema()
        row = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not row:
            raise ValueError("套利 Paper 账户不存在")
        return self._account_to_dict(db, row, include_ledger=True)

    def update_account(
        self,
        db: Session,
        account_id: int,
        *,
        name: Optional[str] = None,
        risk_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """修改套利 Paper 账户基础信息，不改变资金和流水。"""
        self.ensure_schema()
        account = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not account:
            raise ValueError("套利 Paper 账户不存在")
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("账户名称不能为空")
            account.name = clean_name[:128]
        if risk_profile is not None:
            clean_risk = (risk_profile or "balanced").strip() or "balanced"
            account.risk_profile = clean_risk[:32]
        self._ledger(
            db,
            account_id,
            None,
            "update_account",
            0.0,
            float(account.total_equity or 0),
            note=f"修改账户信息: {account.name}",
            metadata={"name": account.name, "risk_profile": account.risk_profile},
        )
        sqlite_write_commit(db, label="update_arbitrage_paper_account")
        return self.get_account(db, account_id)

    def reset_account(
        self,
        db: Session,
        account_id: int,
        *,
        total_equity: float,
        preset_id: str = "small_300u_standard",
        clear_ledger: bool = True,
    ) -> Dict[str, Any]:
        """重置套利 Paper 账户资金与统计，保留账户 ID 和交易员绑定。"""
        self.ensure_schema()
        account = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not account:
            raise ValueError("套利 Paper 账户不存在")
        if account.status == "running":
            raise ValueError("账户正在运行，请先停止套利验证再重置")
        total = max(float(total_equity or 0), 1.0)
        preset = preset_id if preset_id in SYSTEM_PRESETS else (account.allocation_preset or "small_300u_standard")
        if clear_ledger:
            db.query(ArbitragePaperLedgerDB).filter(ArbitragePaperLedgerDB.account_id == account_id).delete(
                synchronize_session=False
            )
        account.total_equity = total
        account.available_balance = total
        account.frozen_balance = 0.0
        account.realized_pnl = 0.0
        account.estimated_points_value = 0.0
        account.allocation_preset = preset
        account.status = "active"
        meta = _loads(account.metadata_json, {})
        meta["last_reset"] = {"reset_at": time.time(), "total_equity": total, "preset_id": preset}
        account.metadata_json = json.dumps(meta, ensure_ascii=False, default=str)
        self._apply_preset_rows(db, account, preset, total)
        self._ledger(
            db,
            account_id,
            None,
            "reset_account",
            total,
            total,
            note=f"重置账户资金为 ${total:.2f}，模板 {preset}",
            metadata={"total_equity": total, "preset_id": preset, "clear_ledger": clear_ledger},
        )
        sqlite_write_commit(db, label="reset_arbitrage_paper_account")
        return self.get_account(db, account_id)

    def delete_account(self, db: Session, account_id: int) -> Dict[str, Any]:
        """删除套利 Paper 账户及其余额/流水，并解除交易员绑定。"""
        self.ensure_schema()
        account = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not account:
            raise ValueError("套利 Paper 账户不存在")
        if account.status == "running":
            raise ValueError("账户正在运行，请先停止套利验证再删除")
        for profile in db.query(ArbitrageProfileDB).filter(
            ArbitrageProfileDB.arbitrage_paper_account_id == account_id
        ).all():
            profile.arbitrage_paper_account_id = None
        db.query(ArbitragePaperLedgerDB).filter(ArbitragePaperLedgerDB.account_id == account_id).delete(
            synchronize_session=False
        )
        db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id
        ).delete(synchronize_session=False)
        db.delete(account)
        sqlite_write_commit(db, label="delete_arbitrage_paper_account")
        return {"success": True, "deleted_account_id": account_id}

    def _normalize_balance_payload(self, balances: Dict[str, float]) -> Dict[str, float]:
        """补齐全部交易所键；未提交的交易所视为 0，避免旧配额残留。"""
        clean = {
            exchange: max(float(balances.get(exchange, balances.get(exchange.upper(), 0)) or 0), 0.0)
            for exchange in EXCHANGES
        }
        for key, value in balances.items():
            ex = str(key or "").lower()
            if ex in EXCHANGES:
                clean[ex] = max(float(value or 0), 0.0)
        return clean

    def update_balances(self, db: Session, account_id: int, balances: Dict[str, float]) -> Dict[str, Any]:
        self.ensure_schema()
        account = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not account:
            raise ValueError("套利 Paper 账户不存在")
        if account.status == "running":
            raise ValueError("账户正在运行，请先停止套利验证再调整配额")
        clean = self._normalize_balance_payload(balances)
        total = sum(clean.values())
        if total <= 0:
            raise ValueError("分账户金额合计必须大于 0")
        existing = {
            row.exchange: row
            for row in db.query(ArbitragePaperExchangeBalanceDB).filter(
                ArbitragePaperExchangeBalanceDB.account_id == account_id
            ).all()
        }
        for exchange in EXCHANGES:
            amount = clean[exchange]
            row = existing.get(exchange)
            if not row:
                row = ArbitragePaperExchangeBalanceDB(account_id=account_id, exchange=exchange)
                db.add(row)
                existing[exchange] = row
            used = max(float(row.allocated_usd or 0) - float(row.available_usd or 0), 0.0)
            row.allocated_usd = amount
            row.available_usd = max(amount - used, 0.0)
            row.frozen_usd = min(float(row.frozen_usd or 0), amount)
            self._ledger(db, account_id, exchange, "rebalance_exchange", amount, row.available_usd, note="手动调整交易所分账户")
        account.total_equity = total
        account.available_balance = sum(max(row.available_usd or 0, 0) for row in existing.values()) or total
        account.allocation_preset = "custom"
        sqlite_write_commit(db, label="update_arbitrage_paper_balances")
        return self.get_account(db, account_id)

    def apply_preset(self, db: Session, account_id: int, preset_id: str, total_equity: Optional[float] = None) -> Dict[str, Any]:
        self.seed_presets(db)
        account = db.query(ArbitragePaperAccountDB).filter(ArbitragePaperAccountDB.id == account_id).first()
        if not account:
            raise ValueError("套利 Paper 账户不存在")
        total = max(float(total_equity or account.total_equity or 300), 1.0)
        account.total_equity = total
        account.available_balance = total
        account.frozen_balance = 0.0
        account.allocation_preset = preset_id
        self._apply_preset_rows(db, account, preset_id, total)
        self._ledger(db, account_id, None, "apply_preset", total, total, note=f"套用配额模板 {preset_id}")
        sqlite_write_commit(db, label="apply_arbitrage_paper_preset")
        return self.get_account(db, account_id)

    def recompute_realized_pnl_from_ledger(self, db: Session, account_id: int) -> float:
        """
        从 paper_pnl 流水重算已实现盈亏。
        历史 bug 会对同一仓位记两次平仓盈亏（close + close_a），此处每仓位只计最早一条。
        """
        rows = (
            db.query(ArbitragePaperLedgerDB)
            .filter(
                ArbitragePaperLedgerDB.account_id == account_id,
                ArbitragePaperLedgerDB.action == "paper_pnl",
            )
            .order_by(ArbitragePaperLedgerDB.id.asc())
            .all()
        )
        seen_positions: set = set()
        total = 0.0
        for row in rows:
            meta = _loads(row.metadata_json, {})
            pid = row.related_position_id or meta.get("position_id")
            if pid:
                if pid in seen_positions:
                    continue
                seen_positions.add(pid)
            total += float(row.amount_usd or 0)
        return round(total, 4)

    def _collect_account_position_ids(
        self, db: Session, account_id: int, extra_positions: Optional[List[Dict[str, Any]]] = None
    ) -> List[str]:
        """从流水 metadata、related_position_id 与当前活跃仓收集 position_id。"""
        pids: set = set()
        rows = (
            db.query(ArbitragePaperLedgerDB)
            .filter(ArbitragePaperLedgerDB.account_id == account_id)
            .order_by(ArbitragePaperLedgerDB.id.desc())
            .limit(500)
            .all()
        )
        for row in rows:
            if row.related_position_id:
                pids.add(str(row.related_position_id))
            meta = _loads(row.metadata_json, {})
            if meta.get("position_id"):
                pids.add(str(meta["position_id"]))
        if extra_positions:
            for p in extra_positions:
                pid = p.get("position_id") if isinstance(p, dict) else getattr(p, "position_id", None)
                if pid:
                    pids.add(str(pid))
        return sorted(pids)

    def _trade_record_from_snapshot(
        self,
        snap: Dict[str, Any],
        *,
        fees_usd: float = 0.0,
        perf: Any = None,
    ) -> Dict[str, Any]:
        """从 monitor 序列化仓位生成一条交易记录。"""
        pid = str(snap.get("position_id") or "")
        side_raw = str(snap.get("side") or "")
        side_label = "多" if side_raw == "buy" else "空" if side_raw == "sell" else "—"
        lev = float(snap.get("leverage") or 10)
        notional = round(float(snap.get("side_a_size") or 0), 2)
        margin = float(snap.get("margin_usd") or 0)
        if margin <= 0 and notional > 0:
            margin = notional / max(lev, 1)

        raw_status = str(snap.get("status") or "active").lower()
        is_open = raw_status in ("active", "holding")
        entry_time = float(snap.get("entry_time") or 0) or None
        close_time = float(snap["close_time"]) if snap.get("close_time") else None

        if perf is not None:
            points = float(getattr(perf, "total_points", 0) or 0)
            pnl = float(getattr(perf, "total_pnl", 0) or 0)
            hold_h = round(float(getattr(perf, "hold_hours", 0) or 0), 2)
            close_reason = getattr(perf, "close_reason", None)
        else:
            points = float(snap.get("accumulated_points") or 0)
            pnl = float(snap.get("current_pnl") or 0)
            hold_h = float(snap.get("hold_duration_hours") or 0) if is_open else None
            if close_time and entry_time:
                hold_h = round((close_time - entry_time) / 3600.0, 2)
            close_reason = None

        if close_reason == "hold_phase_complete":
            close_reason = "持仓到期平仓"
        elif close_reason == "reconcile_orphan_duplicate":
            close_reason = "对账关闭重复仓"
        elif close_reason == "manual":
            close_reason = "手动平仓"

        estimated_rh = snap.get("estimated_round_rh")
        rh_metrics = snap.get("rh_metrics")
        if estimated_rh is None and isinstance(rh_metrics, dict):
            estimated_rh = rh_metrics.get("estimated_rh")

        return {
            "position_id": pid,
            "symbol": snap.get("symbol") or "—",
            "strategy_type": snap.get("strategy_type") or "S8",
            "exchange": snap.get("source_exchange") or "asterdex",
            "side": side_label,
            "leverage": round(lev, 1),
            "margin_usd": round(margin, 2),
            "notional_usd": notional,
            "status": "持仓中" if is_open else "已平仓",
            "opened_at": entry_time,
            "closed_at": close_time,
            "hold_hours": hold_h,
            "realized_pnl": round(pnl, 4),
            "fees_usd": round(fees_usd, 4),
            "points_earned": round(points, 2),
            "estimated_round_rh": round(float(estimated_rh), 2) if estimated_rh is not None else None,
            "close_reason": close_reason,
        }

    def _collect_position_ids_from_ledger_entries(
        self, ledger_entries: Optional[List[Dict[str, Any]]]
    ) -> List[str]:
        """从已 enrich 的 ledger 条目收集 position_id（不依赖 DB 表）。"""
        pids: set = set()
        for item in ledger_entries or []:
            if item.get("related_position_id"):
                pids.add(str(item["related_position_id"]))
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if meta.get("position_id"):
                pids.add(str(meta["position_id"]))
            details = item.get("position_details") or {}
            if details.get("position_id"):
                pids.add(str(details["position_id"]))
        return sorted(pids)

    def _trade_record_from_ledger_details(
        self,
        details: Dict[str, Any],
        *,
        pnl: Optional[float] = None,
        fees_usd: float = 0.0,
        close_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """从 ledger.position_details 生成已平仓交易记录。"""
        side_raw = str(details.get("side") or "")
        side_label = "多" if side_raw == "buy" else "空" if side_raw == "sell" else "—"
        lev = float(details.get("leverage") or 10)
        notional = round(float(details.get("side_a_size") or 0), 2)
        margin = float(details.get("margin_usd") or 0)
        if margin <= 0 and notional > 0:
            margin = notional / max(lev, 1)
        entry_time = float(details.get("entry_time") or 0) or None
        close_time = float(details["close_time"]) if details.get("close_time") else None
        hold_h = details.get("hold_hours")
        if hold_h is None and close_time and entry_time:
            hold_h = round((close_time - entry_time) / 3600.0, 2)
        points = float(
            details.get("total_points")
            or details.get("rh_earned")
            or 0
        )
        realized = pnl if pnl is not None else float(details.get("total_pnl") or 0)
        estimated_rh = details.get("estimated_round_rh")
        if estimated_rh is None and isinstance(details.get("rh_metrics"), dict):
            estimated_rh = details["rh_metrics"].get("estimated_rh")

        if close_reason == "hold_phase_complete":
            close_reason = "持仓到期平仓"
        elif close_reason == "reconcile_orphan_duplicate":
            close_reason = "对账关闭重复仓"
        elif close_reason == "manual":
            close_reason = "手动平仓"

        return {
            "position_id": str(details.get("position_id") or ""),
            "symbol": details.get("symbol") or "—",
            "strategy_type": details.get("strategy_type") or "S8",
            "exchange": details.get("source_exchange") or "asterdex",
            "side": side_label,
            "leverage": round(lev, 1),
            "margin_usd": round(margin, 2),
            "notional_usd": notional,
            "status": "已平仓",
            "opened_at": entry_time,
            "closed_at": close_time,
            "hold_hours": hold_h,
            "realized_pnl": round(realized, 4),
            "fees_usd": round(fees_usd, 4),
            "points_earned": round(points, 2),
            "estimated_round_rh": round(float(estimated_rh), 2) if estimated_rh is not None else None,
            "close_reason": close_reason or details.get("close_reason"),
        }

    def build_trade_records(
        self,
        db: Session,
        account_id: int,
        limit: int = 40,
        *,
        paper_positions: Optional[List[Dict[str, Any]]] = None,
        ledger_entries: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """按仓位合并为一条交易记录。活跃仓优先从 monitor 快照生成，历史仓从 DB/流水补全。"""
        from backend.database.models import RebatePerformanceLogDB, RebatePositionDB
        from backend.services.rebate_arb.points_aggregation import dedupe_performance_logs

        records_map: Dict[str, Dict[str, Any]] = {}
        perf_by_pid: Dict[str, Any] = {}

        # ── 1) 当前活跃仓：与上方「Paper 积分仓位」表同源，保证必有记录 ──
        for snap in paper_positions or []:
            if not isinstance(snap, dict):
                continue
            pid = str(snap.get("position_id") or "").strip()
            if not pid:
                continue
            records_map[pid] = self._trade_record_from_snapshot(snap)

        # ── 2) 已 enrich 的 ledger（get_account 已加载，含 position_details）──
        ledger_details_by_pid: Dict[str, Dict[str, Any]] = {}
        pnl_by_ledger: Dict[str, float] = {}
        fees_by_ledger: Dict[str, float] = {}
        for item in ledger_entries or []:
            pid = str(
                item.get("related_position_id")
                or (item.get("metadata") or {}).get("position_id")
                or (item.get("position_details") or {}).get("position_id")
                or ""
            ).strip()
            if not pid:
                continue
            details = item.get("position_details") or {}
            if details.get("position_id"):
                ledger_details_by_pid[pid] = details
            action = item.get("action")
            if action == "paper_pnl" and pid not in pnl_by_ledger:
                pnl_by_ledger[pid] = float(item.get("amount_usd") or 0)
            elif action == "paper_fee":
                fees_by_ledger[pid] = fees_by_ledger.get(pid, 0.0) + abs(float(item.get("amount_usd") or 0))

        for pid, details in ledger_details_by_pid.items():
            if pid in records_map and records_map[pid].get("status") == "持仓中":
                continue
            perf_cr = details.get("close_reason")
            records_map[pid] = self._trade_record_from_ledger_details(
                details,
                pnl=pnl_by_ledger.get(pid),
                fees_usd=fees_by_ledger.get(pid, 0.0),
                close_reason=perf_cr,
            )

        # ── 3) 历史仓：DB 查询补全 ──
        try:
            pids = self._collect_account_position_ids(db, account_id, paper_positions)
        except Exception:
            pids = self._collect_position_ids_from_ledger_entries(ledger_entries)

        if not pids:
            pids = sorted(set(records_map.keys()) | set(ledger_details_by_pid.keys()))

        if pids:
            positions = {
                p.position_id: p
                for p in db.query(RebatePositionDB).filter(RebatePositionDB.position_id.in_(pids)).all()
            }
            for log in dedupe_performance_logs(
                db.query(RebatePerformanceLogDB)
                .filter(RebatePerformanceLogDB.position_id.in_(pids))
                .all()
            ):
                perf_by_pid[str(log.position_id)] = log

            ledger_rows = []
            try:
                ledger_rows = (
                    db.query(ArbitragePaperLedgerDB)
                    .filter(ArbitragePaperLedgerDB.account_id == account_id)
                    .order_by(ArbitragePaperLedgerDB.id.asc())
                    .all()
                )
            except Exception:
                ledger_rows = []

            fees_by_pid: Dict[str, float] = dict(fees_by_ledger)
            pnl_by_pid: Dict[str, float] = dict(pnl_by_ledger)
            pid_set = set(pids) | set(records_map.keys())
            for row in ledger_rows:
                meta_row = _loads(row.metadata_json, {})
                pid = str(row.related_position_id or meta_row.get("position_id") or "")
                if not pid or pid not in pid_set:
                    continue
                if row.action == "paper_fee":
                    fees_by_pid[pid] = fees_by_pid.get(pid, 0.0) + abs(float(row.amount_usd or 0))
                elif row.action == "paper_pnl" and pid not in pnl_by_pid:
                    pnl_by_pid[pid] = float(row.amount_usd or 0)

            paper_by_pid = {
                str(p.get("position_id")): p
                for p in (paper_positions or [])
                if isinstance(p, dict) and p.get("position_id")
            }

            for pid in pids:
                if pid in records_map and records_map[pid].get("status") == "持仓中":
                    rec = records_map[pid]
                    rec["fees_usd"] = round(fees_by_pid.get(pid, rec.get("fees_usd") or 0.0), 4)
                    perf = perf_by_pid.get(pid)
                    if perf is not None:
                        rec["points_earned"] = round(float(perf.total_points or rec.get("points_earned") or 0), 2)
                    continue

                if pid in records_map and records_map[pid].get("status") == "已平仓":
                    rec = records_map[pid]
                    if pid in pnl_by_pid:
                        rec["realized_pnl"] = round(pnl_by_pid[pid], 4)
                    if pid in fees_by_pid:
                        rec["fees_usd"] = round(fees_by_pid[pid], 4)
                    perf = perf_by_pid.get(pid)
                    if perf is not None:
                        rec["points_earned"] = round(float(perf.total_points or rec.get("points_earned") or 0), 2)
                        if not rec.get("close_reason"):
                            cr = perf.close_reason
                            if cr == "hold_phase_complete":
                                cr = "持仓到期平仓"
                            rec["close_reason"] = cr
                    continue

                pos = positions.get(pid)
                snap = paper_by_pid.get(pid) or {}
                perf = perf_by_pid.get(pid)

                if snap:
                    rec = self._trade_record_from_snapshot(
                        snap, fees_usd=fees_by_pid.get(pid, 0.0), perf=perf
                    )
                elif pos is not None:
                    meta = _loads(pos.metadata_json, {})
                    side_raw = (meta.get("side_a") or {}).get("side") or meta.get("side") or ""
                    fake_snap = {
                        "position_id": pid,
                        "symbol": pos.symbol,
                        "strategy_type": pos.strategy_type,
                        "source_exchange": pos.source_exchange,
                        "side": side_raw,
                        "leverage": meta.get("leverage"),
                        "margin_usd": meta.get("margin_usd"),
                        "side_a_size": pos.side_a_size,
                        "status": pos.status,
                        "entry_time": pos.entry_time,
                        "close_time": pos.close_time,
                        "accumulated_points": pos.accumulated_points,
                        "current_pnl": pnl_by_pid.get(pid, pos.current_pnl),
                        "estimated_round_rh": meta.get("estimated_round_rh"),
                        "rh_metrics": meta.get("rh_metrics"),
                    }
                    rec = self._trade_record_from_snapshot(
                        fake_snap, fees_usd=fees_by_pid.get(pid, 0.0), perf=perf
                    )
                else:
                    continue

                if pid in pnl_by_pid:
                    rec["realized_pnl"] = round(pnl_by_pid[pid], 4)
                records_map[pid] = rec

        records = sorted(
            records_map.values(),
            key=lambda r: float(r.get("opened_at") or r.get("closed_at") or 0),
            reverse=True,
        )
        return records[:limit]

    def get_dashboard(self, db: Session, account_id: int) -> Dict[str, Any]:
        """聚合账户、各交易所资金/积分/仓位与流水，供模拟账户仪表盘使用。"""
        account = self.get_account(db, account_id)

        paper_positions: List[Dict[str, Any]] = []
        try:
            from backend.services.rebate_arb.position_monitor import rebate_position_monitor
            from backend.services.rebate_arb.rebate_position_mtm import (
                refresh_all_paper_positions_mtm,
                serialize_position_for_api,
            )

            refresh_all_paper_positions_mtm()

            for p in rebate_position_monitor.get_active_positions():
                if not p.paper_mode:
                    continue
                paper_positions.append(serialize_position_for_api(p))
        except Exception:
            pass

        by_exchange: Dict[str, List[Dict[str, Any]]] = {}
        seen_pairs: set = set()
        for pos in paper_positions:
            pid = pos.get("position_id")
            for ex in (pos.get("source_exchange"), pos.get("target_exchange")):
                if not ex:
                    continue
                key = str(ex).lower()
                pair = (key, pid)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                by_exchange.setdefault(key, []).append(pos)

        ledger = account.get("ledger") or []
        total_fees = sum(abs(float(x.get("amount_usd") or 0)) for x in ledger if x.get("action") == "paper_fee")
        total_rebates = sum(float(x.get("amount_usd") or 0) for x in ledger if x.get("action") == "paper_rebate")
        total_slippage = sum(abs(float(x.get("amount_usd") or 0)) for x in ledger if x.get("action") == "paper_slippage")

        ledger_by_ex: Dict[str, List[Dict[str, Any]]] = {}
        for item in ledger:
            ex = (item.get("exchange") or "_global").lower()
            ledger_by_ex.setdefault(ex, []).append(item)

        points_by_exchange: Dict[str, Dict[str, Any]] = {}
        try:
            from backend.database.models import RebatePerformanceLogDB, RebatePositionDB
            from backend.services.rebate_arb.points_aggregation import (
                aggregate_points_and_pnl,
                build_exchange_points_payload,
                points_to_usd,
            )

            active_paper = db.query(RebatePositionDB).filter(
                RebatePositionDB.status.in_(["active", "holding"]),
                RebatePositionDB.paper_mode.is_(True),
            ).all()
            all_positions = db.query(RebatePositionDB).filter(
                RebatePositionDB.paper_mode.is_(True),
            ).all()
            logs = db.query(RebatePerformanceLogDB).all()

            exchange_stats, _, total_pts, _ = aggregate_points_and_pnl(
                active_paper,
                logs,
                pos_lookup=all_positions,
                paper_only=False,
            )
            points_by_exchange = build_exchange_points_payload(exchange_stats)
        except Exception:
            pass

        exchange_rows: List[Dict[str, Any]] = []
        for ex_key, bal in (account.get("exchange_balances") or {}).items():
            pos_list = by_exchange.get(ex_key, [])
            pos_notional = sum(float(p.get("side_a_size") or 0) for p in pos_list)
            pos_pnl = sum(float(p.get("current_pnl") or 0) for p in pos_list)
            pos_points = sum(float(p.get("accumulated_points") or 0) for p in pos_list)
            allocated = float(bal.get("allocated_usd") or 0)
            available = float(bal.get("available_usd") or 0)
            frozen = float(bal.get("frozen_usd") or 0)
            used = max(allocated - available, 0.0)
            utilization = used / allocated if allocated > 0 else 0.0
            pts = points_by_exchange.get(ex_key) or points_by_exchange.get(ex_key.upper()) or {}

            if pos_list:
                ex_status = "trading"
            elif allocated > 0:
                ex_status = "ready"
            else:
                ex_status = "empty"

            exchange_rows.append({
                "exchange": ex_key,
                "allocated_usd": round(allocated, 2),
                "available_usd": round(available, 2),
                "frozen_usd": round(frozen, 2),
                "used_usd": round(used, 2),
                "utilization_pct": round(utilization * 100, 1),
                "strategy_limits": bal.get("strategy_limits") or {},
                "active_positions": len(pos_list),
                "position_notional_usd": round(pos_notional, 2),
                "unrealized_pnl": round(pos_pnl, 2),
                "accumulated_points": round(pos_points, 2),
                "points_earned_total": float(pts.get("points_earned_total") or 0),
                "estimated_value_usd": float(pts.get("estimated_value_usd") or 0),
                "risk_status": pts.get("risk_status") or "healthy",
                "recent_ledger": [],
                "status": ex_status,
            })

        exchange_rows.sort(key=lambda x: (x["exchange"] == "reserve", -x["allocated_usd"]))

        session: Dict[str, Any] = {"running": False}
        try:
            from backend.services.rebate_arb.arbitrage_paper_session_runner import (
                arbitrage_paper_session_runner,
            )
            sess = arbitrage_paper_session_runner.get_status()
            if sess.get("running") and int(sess.get("account_id") or 0) == int(account_id):
                session = sess
        except Exception:
            pass

        s8_report: Dict[str, Any] = {
            "active": False,
            "mode": "safe",
            "recommendation": "等待下一轮信号",
            "positions": [],
            "last_closed": [],
            "wash_safety": {},
        }
        try:
            from backend.services.rebate_arb.wash_trade_avoider import wash_trade_avoider
            from backend.database.models import RebatePerformanceLogDB

            s8_positions = [p for p in paper_positions if p.get("strategy_type") == "S8"]
            s8_report["active"] = bool(s8_positions)
            s8_report["positions"] = [
                {
                    "position_id": p.get("position_id"),
                    "symbol": p.get("symbol"),
                    "mode": p.get("rh_optimization_mode") or "safe",
                    "estimated_round_rh": p.get("estimated_round_rh"),
                    "rh_metrics": p.get("rh_metrics") or {},
                    "paper_ab_test_matrix": p.get("paper_ab_test_matrix") or [],
                }
                for p in s8_positions
            ]
            if s8_positions:
                first = s8_positions[0]
                metrics = first.get("rh_metrics") or {}
                s8_report["mode"] = first.get("rh_optimization_mode") or "safe"
                s8_report["recommendation"] = (
                    "已有 S8 仓位，等待持仓完成"
                    if first.get("execution_phase") == "holding"
                    else "观察平仓/结算状态"
                )
                next_round_volume = float(metrics.get("round_volume_usd") or 0)
            else:
                next_round_volume = 0.0
            s8_report["wash_safety"] = wash_trade_avoider.get_s8_safety_snapshot(
                exchange="asterdex",
                account_equity=float(account.get("total_equity") or 0),
                next_round_volume_usd=next_round_volume,
            )
            logs = (
                db.query(RebatePerformanceLogDB)
                .filter(RebatePerformanceLogDB.strategy_type == "S8")
                .order_by(RebatePerformanceLogDB.id.desc())
                .limit(5)
                .all()
            )
            from backend.services.rebate_arb.points_aggregation import is_trade_performance_log, points_to_usd

            for row in logs:
                if not is_trade_performance_log(row):
                    continue
                hold_h = float(row.hold_hours or 0)
                pts = float(row.total_points or 0)
                s8_report["last_closed"].append({
                    "position_id": row.position_id,
                    "points": round(pts, 2),
                    "pnl": round(float(row.total_pnl or 0), 4),
                    "rebate": round(float(row.total_rebate or 0), 4),
                    "hold_hours": round(hold_h, 3),
                    "rh_per_hold_hour": round(pts / max(hold_h, 0.01), 3),
                    "close_reason": row.close_reason,
                })
            s8_cumulative_pts = sum(
                float(r.total_points or 0)
                for r in db.query(RebatePerformanceLogDB)
                .filter(RebatePerformanceLogDB.strategy_type == "S8")
                .all()
                if is_trade_performance_log(r)
            )
            for p in s8_positions:
                s8_cumulative_pts += float(p.get("accumulated_points") or 0)
            s8_report["cumulative_points"] = round(s8_cumulative_pts, 2)
            s8_report["cumulative_points_value_usd"] = round(points_to_usd(s8_cumulative_pts), 4)
            try:
                from backend.services.rebate_arb.s8_param_learner import get_learning_gate
                from backend.services.rebate_arb.rule_registry import STAGE6_POINT_MODEL

                gate = get_learning_gate(paper_mode=True)
                val = STAGE6_POINT_MODEL.get("point_valuation") or {}
                usd_per_pt = float(val.get("usd_per_point_estimate") or 0.01)
                learned_disc = float(gate.get("speculative_discount") or 0.5)
                realized_cash = float(account.get("realized_pnl") or 0)
                pts_val_learned = s8_cumulative_pts * usd_per_pt * learned_disc
                net_experiment = realized_cash + pts_val_learned
                s8_report["experiment_metrics"] = {
                    "cash_per_point": gate.get("cash_per_point"),
                    "samples": gate.get("samples"),
                    "recovery_mode": bool(gate.get("recovery_mode")),
                    "paper_advisory": bool(gate.get("paper_advisory")),
                    "paper_blocks_open": False,
                    "recommended_mode": gate.get("recommended_mode"),
                    "speculative_discount_learned": learned_disc,
                    "realized_cash_pnl": round(realized_cash, 4),
                    "points_value_learned_discount": round(pts_val_learned, 4),
                    "net_experiment_pnl": round(net_experiment, 4),
                    "paper_stop_loss_notional_pct": gate.get("paper_stop_loss_notional_pct"),
                }
                if gate.get("paper_advisory"):
                    s8_report["mode"] = s8_report.get("mode") or "stage6_optimal"
                    s8_report["recommendation"] = (
                        f"Paper 继续收样本（{gate.get('samples')} 轮）；"
                        f"cash/pt={float(gate.get('cash_per_point') or 0):.4f} 为负仅告警，"
                        f"净实验 ${net_experiment:.2f}"
                    )
                elif gate.get("recovery_mode"):
                    s8_report["mode"] = gate.get("recommended_mode") or "paper_experiment"
                    s8_report["recommendation"] = (
                        f"学习门禁：cash/pt={float(gate.get('cash_per_point') or 0):.4f}，"
                        f"短持低杠杆快迭代（净实验收益 ${net_experiment:.2f}）"
                    )
                elif not s8_report["active"] and s8_cumulative_pts > 0:
                    s8_report["recommendation"] = (
                        f"已结算 {s8_report['cumulative_points']:.0f} 积分，"
                        f"净实验收益 ${net_experiment:.2f}，等待悲观 EV>0 信号"
                    )
                from backend.services.rebate_arb.s8_param_learner import build_learning_memory
                s8_report["learning_memory"] = build_learning_memory(limit_rounds=10)
            except Exception:
                if not s8_report["active"] and s8_cumulative_pts > 0:
                    s8_report["recommendation"] = (
                        f"已结算 {s8_report['cumulative_points']:.0f} 积分，等待下一轮开仓信号"
                    )
        except Exception:
            pass

        total_points_earned = round(
            sum(float(v.get("points_earned_total") or 0) for v in points_by_exchange.values()), 2
        )
        total_points_value_usd = round(
            sum(float(v.get("estimated_value_usd") or 0) for v in points_by_exchange.values()), 2
        )

        ledger_realized_pnl = self.recompute_realized_pnl_from_ledger(db, account_id)
        stored_realized = float(account.get("realized_pnl") or 0)
        if abs(ledger_realized_pnl - stored_realized) > 0.02:
            try:
                acct_row = db.query(ArbitragePaperAccountDB).filter(
                    ArbitragePaperAccountDB.id == account_id
                ).first()
                if acct_row:
                    acct_row.realized_pnl = ledger_realized_pnl
                    from backend.database.connection import sqlite_write_commit
                    sqlite_write_commit(db, label="reconcile_realized_pnl")
                    account["realized_pnl"] = ledger_realized_pnl
            except Exception:
                account["realized_pnl"] = ledger_realized_pnl
        else:
            account["realized_pnl"] = ledger_realized_pnl

        experiment_summary: Dict[str, Any] = {}
        try:
            experiment_summary = dict((s8_report or {}).get("experiment_metrics") or {})
        except Exception:
            experiment_summary = {}

        return {
            "account_id": account_id,
            "account": account,
            "session": session,
            "summary": {
                "total_equity": float(account.get("total_equity") or 0),
                "available_balance": float(account.get("available_balance") or 0),
                "frozen_balance": float(account.get("frozen_balance") or 0),
                "realized_pnl": float(account.get("realized_pnl") or 0),
                "estimated_points_value": total_points_value_usd,
                "net_experiment_pnl": float(experiment_summary.get("net_experiment_pnl") or 0),
                "cash_per_point": experiment_summary.get("cash_per_point"),
                "recovery_mode": bool(experiment_summary.get("recovery_mode")),
                "total_points_earned": total_points_earned,
                "active_positions": len(paper_positions),
                "position_notional_usd": round(
                    sum(float(p.get("side_a_size") or 0) for p in paper_positions), 2
                ),
                "unrealized_pnl": round(
                    sum(float(p.get("current_pnl") or 0) for p in paper_positions), 2
                ),
                "total_fees_paid": round(total_fees, 4),
                "total_rebates_received": round(total_rebates, 4),
                "total_slippage_cost": round(total_slippage, 4),
                "status": account.get("status") or "active",
            },
            "exchanges": exchange_rows,
            "positions": paper_positions,
            "s8_report": s8_report,
            "trade_records": self.build_trade_records(
                db, account_id, limit=50, paper_positions=paper_positions, ledger_entries=ledger
            ),
            "ledger": [],
        }

    def _find_trader_arbitrage_profile(
        self, db: Session, account_id: int
    ) -> Optional[Dict[str, Any]]:
        """查找绑定到当前套利 Paper 账户的、已启用的交易员专用套利档案。"""
        profile = db.query(ArbitrageProfileDB).filter(
            ArbitrageProfileDB.arbitrage_paper_account_id == account_id,
            ArbitrageProfileDB.enabled.is_(True),
            ArbitrageProfileDB.paper_account_mode == "dedicated_arbitrage_paper",
        ).first()
        if not profile:
            paper = db.query(ArbitragePaperAccountDB).filter(
                ArbitragePaperAccountDB.id == account_id
            ).first()
            if paper and paper.owner_account_id:
                profile = db.query(ArbitrageProfileDB).filter(
                    ArbitrageProfileDB.account_id == paper.owner_account_id,
                    ArbitrageProfileDB.enabled.is_(True),
                    ArbitrageProfileDB.paper_account_mode == "dedicated_arbitrage_paper",
                    ArbitrageProfileDB.arbitrage_paper_account_id == account_id,
                ).first()
        if not profile:
            return None

        owner = db.query(Account).filter(Account.id == profile.account_id).first()
        enabled_strategies = [str(s).upper() for s in _loads(profile.enabled_strategies_json, [])]
        strategy_llm, execution_llm = resolve_trader_llm_pair(owner, profile)
        return {
            "profile_id": profile.id,
            "trader_account_id": profile.account_id,
            "account_name": getattr(owner, "name", None) or f"交易员#{profile.account_id}",
            "enabled": bool(profile.enabled),
            "linked_llm_config_id": profile.linked_llm_config_id,
            "strategy_llm_config_id": strategy_llm,
            "execution_llm_config_id": execution_llm,
            "enabled_strategies": enabled_strategies,
            "wash_trade_profile": profile.wash_trade_profile,
            "mode": profile.mode,
            "paper_account_mode": profile.paper_account_mode,
        }

    def list_bindable_traders(self, db: Session, paper_account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """列出可绑定的专用套利交易员（已开启专用套利 + 双模型已配置）。"""
        rows = db.query(ArbitrageProfileDB).filter(ArbitrageProfileDB.enabled.is_(True)).all()
        result: List[Dict[str, Any]] = []
        for profile in rows:
            if profile.paper_account_mode != "dedicated_arbitrage_paper":
                continue
            owner = db.query(Account).filter(Account.id == profile.account_id).first()
            strategy_llm, execution_llm = resolve_trader_llm_pair(owner, profile)
            if not strategy_llm or not execution_llm:
                continue
            bound_paper = profile.arbitrage_paper_account_id
            result.append({
                "trader_account_id": profile.account_id,
                "trader_name": getattr(owner, "name", None) or f"交易员#{profile.account_id}",
                "profile_id": profile.id,
                "enabled_strategies": [str(s).upper() for s in _loads(profile.enabled_strategies_json, [])],
                "arbitrage_paper_account_id": bound_paper,
                "strategy_llm_config_id": strategy_llm,
                "execution_llm_config_id": execution_llm,
                "available": bound_paper is None or bound_paper == paper_account_id,
                "bound_to_this_account": bound_paper == paper_account_id if paper_account_id else False,
            })
        return result

    def bind_trader(self, db: Session, account_id: int, trader_account_id: int) -> Dict[str, Any]:
        """将套利 Paper 账户显式绑定到已开启专用套利的交易员。"""
        paper = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.id == account_id
        ).first()
        if not paper:
            raise ValueError("套利 Paper 账户不存在")

        trader = db.query(Account).filter(Account.id == trader_account_id).first()
        if not trader:
            raise ValueError("交易员账户不存在")

        profile = db.query(ArbitrageProfileDB).filter(
            ArbitrageProfileDB.account_id == trader_account_id
        ).first()
        if not profile or not profile.enabled:
            raise ValueError(
                "该交易员未开启积分套利。请在 AI 交易员编辑里勾选「可用于积分套利」。"
            )
        if profile.paper_account_mode != "dedicated_arbitrage_paper":
            raise ValueError("请将该交易员专用套利模式设为「套利专用 Paper 账户」")

        strategy_llm, execution_llm = resolve_trader_llm_pair(trader, profile)
        if not strategy_llm or not execution_llm:
            raise ValueError("该交易员须在 AI 交易员里配置「分析模型」和「执行模型」后才能绑定")
        # 分析与执行允许共用同一个模型（单模型部署是常态）

        if (
            profile.arbitrage_paper_account_id
            and int(profile.arbitrage_paper_account_id) != int(account_id)
        ):
            raise ValueError(
                f"该交易员已绑定套利 Paper 账户 #{profile.arbitrage_paper_account_id}，请先解绑"
            )

        other = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.owner_account_id == trader_account_id,
            ArbitragePaperAccountDB.id != account_id,
        ).first()
        if other:
            raise ValueError(
                f"该交易员已绑定其他套利 Paper 账户「{other.name}」(#{other.id})"
            )

        paper.owner_account_id = trader_account_id
        profile.arbitrage_paper_account_id = account_id

        self._ledger(
            db,
            account_id,
            None,
            "bind_trader",
            0.0,
            float(paper.total_equity or 0),
            note=f"绑定专用套利交易员: {trader.name} (#{trader_account_id})",
            metadata={
                "trader_account_id": trader_account_id,
                "profile_id": profile.id,
                "strategy_llm_config_id": strategy_llm,
                "execution_llm_config_id": execution_llm,
            },
        )
        sqlite_write_commit(db, label="bind_arbitrage_trader")
        account = self.get_account(db, account_id)
        account["trader_profile"] = self._find_trader_arbitrage_profile(db, account_id)
        return {"success": True, "account": account, "trader_profile": account["trader_profile"]}

    def unbind_trader(self, db: Session, account_id: int) -> Dict[str, Any]:
        paper = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.id == account_id
        ).first()
        if not paper:
            raise ValueError("套利 Paper 账户不存在")
        if not paper.owner_account_id:
            return {"success": True, "account": self.get_account(db, account_id)}

        profile = db.query(ArbitrageProfileDB).filter(
            ArbitrageProfileDB.account_id == paper.owner_account_id
        ).first()
        if profile and profile.arbitrage_paper_account_id == account_id:
            profile.arbitrage_paper_account_id = None

        trader_id = paper.owner_account_id
        paper.owner_account_id = None
        self._ledger(
            db, account_id, None, "unbind_trader", 0.0,
            float(paper.total_equity or 0),
            note=f"解绑专用套利交易员 #{trader_id}",
        )
        sqlite_write_commit(db, label="unbind_arbitrage_trader")
        return {"success": True, "account": self.get_account(db, account_id)}

    def _validate_strategy_runtime(
        self,
        db: Session,
        strategy_id: str,
        *,
        total_equity: float,
        balances: Dict[str, Any],
        trader_profile: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        from backend.services.rebate_arb.strategy_runtime_registry import (
            check_ai_signal_available,
            get_runtime_spec,
        )

        sid = strategy_id.upper()
        spec = get_runtime_spec(sid)
        if not spec:
            return self._check(sid, False, "", f"未知策略 {sid}")

        missing_ex = [
            ex for ex in spec.required_exchanges
            if float(balances.get(ex, {}).get("available_usd", 0)) <= 0
        ]
        if missing_ex:
            return self._check(
                f"{sid} 交易所",
                False,
                "",
                f"{spec.name} 需要 {', '.join(spec.required_exchanges)}，缺少: {', '.join(missing_ex)}",
            )

        if total_equity < spec.min_equity_usd:
            return self._check(
                f"{sid} 最低资金",
                False,
                "",
                f"{spec.name} 建议至少 ${spec.min_equity_usd:.0f}，当前 ${total_equity:.2f}",
            )

        if not spec.paper_auto_executable:
            return self._check(
                f"{sid} 执行引擎",
                False,
                "",
                spec.not_ready_reason or f"{spec.name} 未接入 Paper 自动执行",
            )

        if spec.requires_trader_profile:
            if not trader_profile:
                return self._check(
                    f"{sid} 交易员档案",
                    False,
                    "",
                    f"{spec.name} 需先在「AI 交易员 → 专用套利」启用并绑定本套利 Paper 账户",
                )
            if sid not in trader_profile.get("enabled_strategies", []):
                return self._check(
                    f"{sid} 策略授权",
                    False,
                    "",
                    f"交易员 {trader_profile['account_name']} 的专用套利档案未勾选 {sid}",
                )

        if spec.requires_ai_signal:
            probe = check_ai_signal_available("ETH")
            if not probe.get("ok"):
                sig = probe.get("signal") or {}
                reason = sig.get("reasoning") or probe.get("error") or "AI 信号不可用"
                return self._check(
                    f"{sid} AI 信号",
                    False,
                    "",
                    f"{spec.name} 需要有效 AI 信号才能开方向仓：{reason}",
                )
            sig = probe.get("signal") or {}
            return self._check(
                f"{sid} AI 信号",
                True,
                f"方向={sig.get('direction')} 置信度={sig.get('confidence')}%",
                "",
            )

        return self._check(
            f"{sid} 运行规格",
            True,
            f"{spec.name} · {spec.execution_mode} · {spec.direction_rule}",
            "",
        )

    def validate_start(
        self,
        db: Session,
        account_id: int,
        strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        account = self.get_account(db, account_id)
        strategies = [s.upper() for s in (strategies or ["S8"])]
        balances = account["exchange_balances"]
        total = max(float(account["total_equity"]), 1.0)
        reserve = float(balances.get("reserve", {}).get("allocated_usd", 0))
        max_exchange = max(
            (float(v.get("allocated_usd", 0)) for k, v in balances.items() if k != "reserve"),
            default=0.0,
        )
        allocation_preset = (account.get("allocation_preset") or "").strip()
        non_zero_trading = [
            k for k, v in balances.items()
            if k != "reserve" and float(v.get("allocated_usd", 0)) > 0
        ]
        is_manual_like = (
            allocation_preset in ("custom", "single_asterdex_s8")
            or len(non_zero_trading) <= 1
        )

        checks = []
        checks.append(self._check(
            "账户状态",
            account["status"] in ("active", "running"),
            "套利 Paper 账户可用",
            "账户未启用",
        ))
        checks.append(self._check("总资金", total >= 50, f"总资金 ${total:.2f}", "总资金低于 50U"))
        if is_manual_like:
            checks.append(self._check(
                "Reserve",
                True,
                f"手动/单所配额 · Reserve ${reserve:.2f}（{reserve / total:.0%}）",
                "",
            ))
            checks.append(self._check(
                "单交易所占比",
                True,
                f"手动/单所配额 · 最大占比 {max_exchange / total:.0%}",
                "",
            ))
        else:
            checks.append(self._check("Reserve", reserve / total >= 0.08, f"Reserve ${reserve:.2f}", "Reserve 建议至少 8%"))
            checks.append(self._check("单交易所占比", max_exchange / total <= 0.75, f"最大占比 {max_exchange / total:.0%}", "单交易所占比过高"))
        checks.append(self._check("S7 monitor_only", "S7" not in strategies, "S7 未参与 Paper 自动执行", "S7 仅监控，不能加入 Paper 验证"))

        trader_profile = self._find_trader_arbitrage_profile(db, account_id)
        from backend.services.rebate_arb.strategy_runtime_registry import get_runtime_spec

        needs_profile = any(
            (spec := get_runtime_spec(s)) and spec.requires_trader_profile
            for s in strategies
        )
        if needs_profile:
            checks.append(self._check(
                "专用套利交易员绑定",
                bool(trader_profile),
                (
                    f"{trader_profile['account_name']} · 已开启专用套利 · "
                    f"分析#{trader_profile.get('strategy_llm_config_id')} · "
                    f"执行#{trader_profile.get('execution_llm_config_id')}"
                    if trader_profile else ""
                ),
                "须选择并绑定一个已开启「专用套利」的交易员：勾选策略、配置策略分析模型与执行规划模型、绑定本 Paper 账户。",
            ))
            if trader_profile and not trader_profile.get("enabled"):
                checks.append(self._check(
                    "专用套利开关",
                    False,
                    "",
                    "绑定交易员未开启「专用套利」选项，不能作为套利用交易员。",
                ))
            if trader_profile and (
                not trader_profile.get("strategy_llm_config_id")
                or not trader_profile.get("execution_llm_config_id")
            ):
                checks.append(self._check(
                    "分析/执行模型",
                    False,
                    "",
                    "交易员须在 AI 交易员里配置「分析模型」与「执行模型」（套利共用，无需另配）。",
                ))
            elif trader_profile and int(trader_profile.get("strategy_llm_config_id") or 0) == int(
                trader_profile.get("execution_llm_config_id") or 0
            ):
                # 允许同一个模型同时承担分析与执行（单模型部署是常态），仅提示
                checks.append(self._check(
                    "分析/执行模型",
                    True,
                    f"分析与执行共用模型 #{trader_profile.get('strategy_llm_config_id')}（允许；如需分开调参可配置两个模型）",
                    "",
                ))

        strategy_runtime: List[Dict[str, Any]] = []
        for sid in strategies:
            if sid == "S7":
                continue
            check = self._validate_strategy_runtime(
                db,
                sid,
                total_equity=total,
                balances=balances,
                trader_profile=trader_profile,
            )
            checks.append(check)
            spec = get_runtime_spec(sid)
            if spec:
                strategy_runtime.append({
                    "strategy_id": sid,
                    "name": spec.name,
                    "execution_mode": spec.execution_mode,
                    "direction_rule": spec.direction_rule,
                    "hold_model": spec.hold_model,
                    "summary": spec.summary,
                    "paper_auto_executable": spec.paper_auto_executable,
                    "passed": check["passed"],
                    "message": check["message"],
                })

        try:
            from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
            gate = rule_sync_gate.get_state()
            paused = bool(gate.get("is_rebate_paused") or gate.get("is_v3_paused"))
            checks.append(self._check("规则闸门", not paused, "规则闸门正常", gate.get("pause_reason") or "规则闸门暂停"))
        except Exception as exc:
            checks.append(self._check("规则闸门", False, "", f"无法读取规则闸门: {exc}"))

        pending_critical = (
            db.query(RuleChangeEventDB)
            .filter(RuleChangeEventDB.status.in_(["pending", "analyzed"]))
            .filter(RuleChangeEventDB.severity.in_(["L3", "L4"]))
            .count()
        )
        checks.append(self._check("L3/L4 规则事件", pending_critical == 0, "无待处理严重规则事件", f"{pending_critical} 个严重规则事件待处理"))

        required = self._required_exchanges(strategies)
        missing = [ex for ex in required if float(balances.get(ex, {}).get("available_usd", 0)) <= 0]
        checks.append(self._check("交易所分账户", not missing, f"已覆盖 {', '.join(required)}", f"缺少余额: {', '.join(missing)}"))

        passed = all(c["passed"] for c in checks)
        return {
            "success": True,
            "passed": passed,
            "checks": checks,
            "strategies": strategies,
            "account": account,
            "trader_profile": trader_profile,
            "strategy_runtime": strategy_runtime,
        }

    def start_paper_verification(
        self,
        db: Session,
        account_id: int,
        strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """启动套利 Paper 验证：校验 → 绑定资金池 → 启用策略 → 触发扫描。"""
        validation = self.validate_start(db, account_id, strategies)
        if not validation.get("passed"):
            return {
                "success": False,
                "passed": False,
                "error": "启动前检查未通过，请先处理红色项",
                "checks": validation.get("checks", []),
                "strategies": validation.get("strategies", []),
            }

        selected = [s.upper() for s in (strategies or validation.get("strategies") or ["S8"])]
        account_row = (
            db.query(ArbitragePaperAccountDB)
            .filter(ArbitragePaperAccountDB.id == account_id)
            .first()
        )
        if not account_row:
            raise ValueError("套利 Paper 账户不存在")

        meta = _loads(account_row.metadata_json, {})
        meta["last_start"] = {
            "strategies": selected,
            "started_at": time.time(),
            "trader_profile": validation.get("trader_profile"),
            "strategy_runtime": validation.get("strategy_runtime"),
        }
        account_row.metadata_json = json.dumps(meta, ensure_ascii=False, default=str)
        account_row.status = "running"

        self._ledger(
            db,
            account_id,
            None,
            "paper_start",
            0.0,
            float(account_row.total_equity or 0),
            note=f"Paper 验证启动: {', '.join(selected)}",
            metadata={
                "strategies": selected,
                "trader_profile": validation.get("trader_profile"),
                "strategy_runtime": validation.get("strategy_runtime"),
            },
        )
        sqlite_write_commit(db, label="start_arbitrage_paper_verification")

        from backend.services.rebate_arb.arbitrage_paper_session_runner import (
            arbitrage_paper_session_runner,
        )

        # 其它 running 账户标记为 active（全局仅一个后台 tick 会话）
        others = (
            db.query(ArbitragePaperAccountDB)
            .filter(ArbitragePaperAccountDB.status == "running")
            .filter(ArbitragePaperAccountDB.id != account_id)
            .all()
        )
        for other in others:
            other.status = "active"
        if others:
            sqlite_write_commit(db, label="stop_other_arbitrage_paper_sessions")

        trader_profile = validation.get("trader_profile")
        runner_result = arbitrage_paper_session_runner.start_session(
            account_id, selected, trader_profile=trader_profile
        )
        session = runner_result.get("session") or {}
        first_tick = runner_result.get("first_tick") or {}

        return {
            "success": True,
            "passed": True,
            "account_id": account_id,
            "strategies": selected,
            "status": "running",
            "session": session,
            "scan": {
                "triggered": True,
                "total_evaluated": first_tick.get("total_evaluated", 0),
                "viable_count": first_tick.get("viable_count", 0),
                "auto_executed": first_tick.get("auto_executed", False),
                "account_equity": first_tick.get("account_equity"),
                "top_strategy": first_tick.get("top_strategy"),
                "auto_exec_error": (first_tick.get("auto_exec_result") or {}).get("error")
                    or (session.get("last_tick") or {}).get("auto_exec_error"),
            },
            "checks": validation.get("checks", []),
            "account": self.get_account(db, account_id),
        }

    def stop_paper_verification(self, db: Session, account_id: int) -> Dict[str, Any]:
        from backend.services.rebate_arb.arbitrage_paper_session_runner import (
            arbitrage_paper_session_runner,
        )

        account_row = (
            db.query(ArbitragePaperAccountDB)
            .filter(ArbitragePaperAccountDB.id == account_id)
            .first()
        )
        if not account_row:
            raise ValueError("套利 Paper 账户不存在")

        stop_result = arbitrage_paper_session_runner.stop_session(account_id)
        if not stop_result.get("success"):
            return stop_result

        account_row.status = "active"
        self._ledger(
            db,
            account_id,
            None,
            "paper_stop",
            0.0,
            float(account_row.total_equity or 0),
            note="Paper 验证已停止",
        )
        sqlite_write_commit(db, label="stop_arbitrage_paper_verification")
        return {
            "success": True,
            "account_id": account_id,
            "status": "active",
            "account": self.get_account(db, account_id),
        }

    def _apply_preset_rows(self, db: Session, account: ArbitragePaperAccountDB, preset_id: str, total: float) -> None:
        preset = SYSTEM_PRESETS.get(preset_id) or SYSTEM_PRESETS["small_300u_standard"]
        existing = {
            row.exchange: row
            for row in db.query(ArbitragePaperExchangeBalanceDB).filter(
                ArbitragePaperExchangeBalanceDB.account_id == account.id
            ).all()
        }
        for exchange in EXCHANGES:
            ratio = float(preset["exchange_ratios"].get(exchange, 0.0))
            amount = round(total * ratio, 6)
            row = existing.get(exchange)
            if not row:
                row = ArbitragePaperExchangeBalanceDB(account_id=account.id, exchange=exchange)
                db.add(row)
            row.allocated_usd = amount
            row.available_usd = amount
            row.frozen_usd = 0.0
            row.asset_balances_json = json.dumps({"USDT": amount}, ensure_ascii=False)
            row.strategy_limits_json = json.dumps(preset.get("strategy_limits", {}), ensure_ascii=False)

    def _account_to_dict(self, db: Session, row: ArbitragePaperAccountDB, include_ledger: bool = False) -> Dict[str, Any]:
        balances = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == row.id
        ).order_by(ArbitragePaperExchangeBalanceDB.exchange.asc()).all()
        data = {
            "id": row.id,
            "name": row.name,
            "owner_account_id": row.owner_account_id,
            "total_equity": float(row.total_equity or 0),
            "available_balance": float(row.available_balance or 0),
            "frozen_balance": float(row.frozen_balance or 0),
            "realized_pnl": float(row.realized_pnl or 0),
            "estimated_points_value": float(row.estimated_points_value or 0),
            "risk_profile": row.risk_profile,
            "allocation_preset": row.allocation_preset,
            "status": row.status,
            "metadata": _loads(row.metadata_json, {}),
            "exchange_balances": {
                b.exchange: {
                    "exchange": b.exchange,
                    "allocated_usd": float(b.allocated_usd or 0),
                    "available_usd": float(b.available_usd or 0),
                    "frozen_usd": float(b.frozen_usd or 0),
                    "asset_balances": _loads(b.asset_balances_json, {}),
                    "strategy_limits": _loads(b.strategy_limits_json, {}),
                }
                for b in balances
            },
        }
        if include_ledger:
            ledgers = db.query(ArbitragePaperLedgerDB).filter(
                ArbitragePaperLedgerDB.account_id == row.id
            ).order_by(ArbitragePaperLedgerDB.id.desc()).limit(50).all()
            data["ledger"] = self._enrich_ledger_display_details([self._ledger_to_dict(x, db) for x in ledgers])
        data["trader_profile"] = self._find_trader_arbitrage_profile(db, row.id)
        if row.owner_account_id:
            owner = db.query(Account).filter(Account.id == row.owner_account_id).first()
            data["owner_account_name"] = getattr(owner, "name", None) if owner else None
        return data

    def _preset_to_dict(self, row: ArbitragePaperAllocationPresetDB) -> Dict[str, Any]:
        return {
            "preset_id": row.preset_id,
            "name": row.name,
            "description": row.description,
            "risk_profile": row.risk_profile,
            "total_equity_hint": row.total_equity_hint,
            "exchange_ratios": _loads(row.exchange_ratios_json, {}),
            "strategy_limits": _loads(row.strategy_limits_json, {}),
            "is_system": bool(row.is_system),
        }

    def _ledger_position_details(self, db: Session, position_id: Optional[str]) -> Dict[str, Any]:
        if not position_id:
            return {}
        try:
            from backend.database.models import RebatePerformanceLogDB, RebatePositionDB

            pos = db.query(RebatePositionDB).filter(RebatePositionDB.position_id == position_id).first()
            perf = (
                db.query(RebatePerformanceLogDB)
                .filter(RebatePerformanceLogDB.position_id == position_id)
                .order_by(RebatePerformanceLogDB.id.desc())
                .first()
            )
            meta = _loads(pos.metadata_json, {}) if pos else {}
            entry_time = float(pos.entry_time or 0) if pos else 0.0
            close_time = float(pos.close_time or 0) if pos and pos.close_time else None
            hold_seconds = (
                max(float(close_time) - entry_time, 0.0)
                if close_time and entry_time
                else float(perf.hold_hours or 0) * 3600 if perf else None
            )
            return {
                "position_id": position_id,
                "symbol": getattr(pos, "symbol", None),
                "strategy_type": getattr(pos, "strategy_type", None),
                "source_exchange": getattr(pos, "source_exchange", None),
                "entry_time": entry_time or None,
                "close_time": close_time,
                "hold_seconds": round(hold_seconds, 1) if hold_seconds is not None else None,
                "hold_hours": round(hold_seconds / 3600, 3) if hold_seconds is not None else None,
                "side_a_size": round(float(getattr(pos, "side_a_size", 0) or 0), 2) if pos else None,
                "margin_usd": meta.get("margin_usd"),
                "leverage": meta.get("leverage") or (meta.get("side_a") or {}).get("leverage"),
                "side": (meta.get("side_a") or {}).get("side") or meta.get("side"),
                "rh_earned": meta.get("rh_earned_this_round") or (float(perf.total_points or 0) if perf else None),
                "estimated_round_rh": meta.get("estimated_round_rh"),
                "symbol_boost": meta.get("symbol_boost"),
                "rh_optimization_mode": meta.get("rh_optimization_mode"),
                "rh_metrics": meta.get("rh_metrics") if isinstance(meta.get("rh_metrics"), dict) else {},
                "total_pnl": float(perf.total_pnl or 0) if perf else getattr(pos, "current_pnl", None),
                "total_rebate": float(perf.total_rebate or 0) if perf else getattr(pos, "accumulated_rebate", None),
                "total_points": float(perf.total_points or 0) if perf else getattr(pos, "accumulated_points", None),
                "close_reason": getattr(perf, "close_reason", None) if perf else None,
            }
        except Exception:
            return {}

    def _enrich_ledger_display_details(self, ledgers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """历史 fee/slippage 流水可能没有 position_id，展示时继承同一平仓组的仓位摘要。"""
        last_details_by_scope: Dict[str, Dict[str, Any]] = {}
        detail_actions = {"paper_margin_release", "paper_pnl", "paper_fee", "paper_slippage", "paper_rebate"}
        for item in ledgers:
            scope = f"{item.get('exchange') or ''}:{item.get('strategy_type') or ''}"
            details = item.get("position_details") or {}
            if details.get("position_id"):
                last_details_by_scope[scope] = details
                continue
            if item.get("action") not in detail_actions:
                continue
            note = str(item.get("note") or "").lower()
            if "平仓" not in note and "close" not in note:
                continue
            inherited = last_details_by_scope.get(scope)
            if inherited:
                item["position_details"] = inherited
                item["related_position_id"] = inherited.get("position_id")
        return ledgers

    def _ledger_to_dict(self, row: ArbitragePaperLedgerDB, db: Optional[Session] = None) -> Dict[str, Any]:
        meta = _loads(row.metadata_json, {})
        position_id = row.related_position_id or meta.get("position_id")
        details = self._ledger_position_details(db, position_id) if db is not None else {}
        return {
            "id": row.id,
            "exchange": row.exchange,
            "action": row.action,
            "amount_usd": float(row.amount_usd or 0),
            "balance_after": row.balance_after,
            "strategy_type": row.strategy_type,
            "related_position_id": position_id,
            "note": row.note,
            "metadata": meta,
            "position_details": details,
            "created_at": str(row.created_at) if row.created_at else None,
        }

    def get_active_paper_exposure(
        self,
        exchange: str,
        strategy_type: str,
    ) -> float:
        """统计某交易所某策略当前 Paper 活跃名义占用。"""
        try:
            from backend.services.rebate_arb.position_monitor import rebate_position_monitor

            ex = (exchange or "").lower()
            sid = (strategy_type or "").upper()
            total = 0.0
            for p in rebate_position_monitor.get_active_positions():
                if not p.paper_mode:
                    continue
                if (p.source_exchange or "").lower() != ex:
                    continue
                if p.strategy_type.value.upper() != sid:
                    continue
                notional = float(p.side_a_size or 0) + float(p.side_b_size or 0)
                if sid == "S8":
                    meta = p.metadata if isinstance(p.metadata, dict) else {}
                    margin = float(meta.get("margin_usd") or 0)
                    if margin <= 0 and notional > 0:
                        lev = float(
                            meta.get("leverage")
                            or (meta.get("side_a") or {}).get("leverage")
                            or 10
                        )
                        margin = notional / max(lev, 1)
                    total += margin
                else:
                    total += notional
            return round(total, 2)
        except Exception:
            return 0.0

    def has_active_directional_hold(
        self,
        exchange: str,
        strategy_type: str,
        symbol: str = "",
    ) -> bool:
        """S8 等方向策略：同所已有持仓则不再开新仓（内存 + DB）。"""
        ex = (exchange or "").lower()
        sid = (strategy_type or "").upper()
        sym = (symbol or "").upper()
        try:
            from backend.services.rebate_arb.position_monitor import rebate_position_monitor
            from backend.services.rebate_arb.models import RebatePositionStatus

            for p in rebate_position_monitor.get_active_positions():
                if not p.paper_mode or p.status != RebatePositionStatus.ACTIVE:
                    continue
                if (p.source_exchange or "").lower() != ex:
                    continue
                if p.strategy_type.value.upper() != sid:
                    continue
                if sym and (p.symbol or "").upper() != sym:
                    continue
                if float(p.side_a_size or 0) <= 0:
                    continue
                return True
        except Exception:
            pass

        try:
            from backend.database.models import RebatePositionDB
            from backend.database.connection import SessionLocal

            db = SessionLocal()
            try:
                q = db.query(RebatePositionDB).filter(
                    RebatePositionDB.status == "active",
                    RebatePositionDB.paper_mode.is_(True),
                    RebatePositionDB.source_exchange == ex,
                    RebatePositionDB.strategy_type == sid,
                )
                if sym:
                    q = q.filter(RebatePositionDB.symbol == sym)
                row = q.order_by(RebatePositionDB.id.desc()).first()
                if row and float(row.side_a_size or 0) > 0:
                    return True
            finally:
                db.close()
        except Exception:
            pass
        return False

    def reconcile_orphan_s8_positions(self, db: Session, account_id: int) -> Dict[str, Any]:
        """
        对账：同所 S8 只保留最新一条 active 仓，其余标记 closed 并释放误冻保证金。
        解决重启后内存丢失、DB 仍 active、重复开仓/重复冻结的问题。
        """
        import time as _time
        from backend.database.models import RebatePositionDB, RebatePerformanceLogDB
        from backend.database.connection import sqlite_write_commit

        rows = (
            db.query(RebatePositionDB)
            .filter(
                RebatePositionDB.status == "active",
                RebatePositionDB.strategy_type == "S8",
                RebatePositionDB.paper_mode.is_(True),
                RebatePositionDB.source_exchange == "asterdex",
            )
            .order_by(RebatePositionDB.id.desc())
            .all()
        )
        if len(rows) <= 1:
            return {"kept": rows[0].position_id if rows else None, "closed": 0}

        keeper = rows[0]
        closed = 0
        for row in rows[1:]:
            meta = _loads(row.metadata_json, {})
            margin = float(meta.get("margin_usd") or 0)
            if margin <= 0:
                lev = float(
                    meta.get("leverage")
                    or (meta.get("side_a") or {}).get("leverage")
                    or 10
                )
                margin = float(row.side_a_size or 0) / max(lev, 1)
            row.status = "closed"
            row.close_time = _time.time()
            if margin > 0:
                self.release_paper_margin(
                    db,
                    account_id,
                    "asterdex",
                    margin,
                    position_id=row.position_id,
                    strategy_type="S8",
                    note=f"对账关闭重复仓 {row.symbol}",
                )
            # 写入结算日志，避免 MTM 已累计积分从汇总中消失
            pts = float(row.accumulated_points or 0)
            pnl = float(row.current_pnl or 0)
            if pts > 0 or abs(pnl) > 1e-6:
                hold_h = max((_time.time() - float(row.entry_time or _time.time())) / 3600.0, 0.0)
                db.add(RebatePerformanceLogDB(
                    position_id=row.position_id,
                    strategy_type=row.strategy_type or "S8",
                    total_pnl=pnl,
                    total_rebate=float(row.accumulated_rebate or 0),
                    total_points=pts,
                    hold_hours=hold_h,
                    close_reason="reconcile_orphan_duplicate",
                ))
            closed += 1
        sqlite_write_commit(db, label="reconcile_orphan_s8")
        logger.info(
            "[PaperAccount] S8 对账: 保留 %s, 关闭重复 %s 条",
            keeper.position_id,
            closed,
        )
        return {"kept": keeper.position_id, "closed": closed}

    def compute_max_open_size(
        self,
        db: Session,
        account_id: int,
        exchange: str,
        strategy_type: str,
        requested_usd: float,
    ) -> Dict[str, Any]:
        """
        按交易所分账户配额 + 策略子限额计算可冻结保证金。
        开仓名义价值由上层按 margin × leverage 得到；这里不直接代表下单名义。
        例：Asterdex 135U × S8 45% = 60.75U 保证金上限。
        """
        ex = (exchange or "").lower()
        sid = (strategy_type or "").upper()
        req = max(float(requested_usd or 0), 0.0)
        result: Dict[str, Any] = {
            "allowed_usd": 0.0,
            "requested_usd": req,
            "exchange_cap_usd": 0.0,
            "strategy_cap_usd": 0.0,
            "exposure_usd": 0.0,
            "available_usd": 0.0,
            "reason": "",
        }
        if req <= 0:
            result["reason"] = "invalid_size"
            return result

        bal_row = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id,
            ArbitragePaperExchangeBalanceDB.exchange == ex,
        ).first()
        if not bal_row:
            result["reason"] = f"no_exchange_balance:{ex}"
            return result

        allocated = float(bal_row.allocated_usd or 0)
        available = float(bal_row.available_usd or 0)
        limits = _loads(getattr(bal_row, "strategy_limits_json", None), {})
        if not limits and hasattr(bal_row, "strategy_limits"):
            limits = bal_row.strategy_limits if isinstance(bal_row.strategy_limits, dict) else {}
        pct = float(limits.get(sid) or limits.get(sid.lower()) or 0)
        strategy_cap = allocated * pct if pct > 0 else allocated
        exposure = self.get_active_paper_exposure(ex, sid)
        headroom = max(strategy_cap - exposure, 0.0)
        allowed = min(req, headroom, available, allocated)

        result.update({
            "allowed_usd": round(max(allowed, 0.0), 2),
            "exchange_cap_usd": round(allocated, 2),
            "strategy_cap_usd": round(strategy_cap, 2),
            "exposure_usd": round(exposure, 2),
            "available_usd": round(available, 2),
        })
        if allowed <= 0:
            result["reason"] = (
                f"配额不足 {ex}/{sid}: 策略上限${strategy_cap:.2f} "
                f"已用${exposure:.2f} 可用${available:.2f}"
            )
        elif allowed < req - 0.01:
            result["reason"] = f"capped_from_{req:.2f}_to_{allowed:.2f}"
        return result

    def freeze_paper_margin(
        self,
        db: Session,
        account_id: int,
        exchange: str,
        amount_usd: float,
        *,
        position_id: str,
        strategy_type: str,
        note: str = "",
    ) -> None:
        """开仓冻结名义保证金到分所 frozen。"""
        amt = max(float(amount_usd or 0), 0.0)
        if amt <= 0:
            return
        ex = (exchange or "").lower()
        account = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.id == account_id
        ).first()
        bal_row = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id,
            ArbitragePaperExchangeBalanceDB.exchange == ex,
        ).first()
        if not account or not bal_row:
            return
        bal_row.available_usd = max(float(bal_row.available_usd or 0) - amt, 0.0)
        bal_row.frozen_usd = float(bal_row.frozen_usd or 0) + amt
        account.frozen_balance = float(account.frozen_balance or 0) + amt
        account.available_balance = max(float(account.available_balance or 0) - amt, 0.0)
        self._ledger(
            db,
            account_id,
            ex,
            "paper_margin_freeze",
            -amt,
            float(bal_row.available_usd),
            strategy_type=strategy_type,
            related_position_id=position_id,
            note=note or f"开仓冻结 ${amt:.2f}",
            metadata={"frozen_usd": amt, "position_id": position_id},
        )

    def release_paper_margin(
        self,
        db: Session,
        account_id: int,
        exchange: str,
        amount_usd: float,
        *,
        position_id: str,
        strategy_type: str,
        pnl_delta: float = 0.0,
        note: str = "",
    ) -> None:
        """平仓释放冻结保证金，并计入已实现盈亏。"""
        amt = max(float(amount_usd or 0), 0.0)
        if amt <= 0:
            return
        ex = (exchange or "").lower()
        account = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.id == account_id
        ).first()
        bal_row = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id,
            ArbitragePaperExchangeBalanceDB.exchange == ex,
        ).first()
        if not account or not bal_row:
            return
        release = min(amt, float(bal_row.frozen_usd or 0))
        bal_row.frozen_usd = max(float(bal_row.frozen_usd or 0) - release, 0.0)
        bal_row.available_usd = float(bal_row.available_usd or 0) + release + float(pnl_delta or 0)
        account.frozen_balance = max(float(account.frozen_balance or 0) - release, 0.0)
        account.available_balance = float(account.available_balance or 0) + release + float(pnl_delta or 0)
        account.realized_pnl = float(account.realized_pnl or 0) + float(pnl_delta or 0)
        self._ledger(
            db,
            account_id,
            ex,
            "paper_margin_release",
            release + float(pnl_delta or 0),
            float(bal_row.available_usd),
            strategy_type=strategy_type,
            related_position_id=position_id,
            note=note or f"平仓释放 ${release:.2f}",
            metadata={"released_usd": release, "pnl_delta": pnl_delta, "position_id": position_id},
        )

    def record_paper_leg_fill(
        self,
        db: Session,
        account_id: int,
        exchange: str,
        *,
        position_id: str,
        strategy_type: str,
        phase: str,
        fee_paid: float,
        rebate_received: float,
        slippage_cost: float,
        pnl_delta: float = 0.0,
        note: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录 Paper 成交成本/盈亏，同步分所余额与总账户。"""
        ex = (exchange or "").lower().strip()
        account = db.query(ArbitragePaperAccountDB).filter(
            ArbitragePaperAccountDB.id == account_id
        ).first()
        if not account:
            return

        bal_row = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id,
            ArbitragePaperExchangeBalanceDB.exchange == ex,
        ).first()
        if not bal_row:
            bal_row = ArbitragePaperExchangeBalanceDB(
                account_id=account_id,
                exchange=ex,
                allocated_usd=0.0,
                available_usd=0.0,
            )
            db.add(bal_row)

        meta = metadata or {}
        net_fee = max(float(fee_paid or 0) - float(rebate_received or 0), 0.0)

        if net_fee > 0:
            bal_row.available_usd = max(float(bal_row.available_usd or 0) - net_fee, 0.0)
            self._ledger(
                db,
                account_id,
                ex,
                "paper_fee",
                -net_fee,
                float(bal_row.available_usd),
                strategy_type=strategy_type,
                related_position_id=position_id,
                note=note or f"{phase} 手续费",
                metadata={**meta, "position_id": position_id, "fee_paid": fee_paid, "rebate_received": rebate_received, "phase": phase},
            )

        if float(rebate_received or 0) > 0 and float(fee_paid or 0) <= float(rebate_received or 0):
            # 净返佣场景（maker rebate > fee）
            credit = float(rebate_received or 0) - float(fee_paid or 0)
            if credit > 0:
                bal_row.available_usd = float(bal_row.available_usd or 0) + credit
                self._ledger(
                    db,
                    account_id,
                    ex,
                    "paper_rebate",
                    credit,
                    float(bal_row.available_usd),
                    strategy_type=strategy_type,
                    related_position_id=position_id,
                    note=note or f"{phase} 返佣",
                    metadata={**meta, "position_id": position_id, "phase": phase},
                )

        if float(slippage_cost or 0) > 0:
            self._ledger(
                db,
                account_id,
                ex,
                "paper_slippage",
                -float(slippage_cost),
                float(bal_row.available_usd),
                strategy_type=strategy_type,
                related_position_id=position_id,
                note=note or f"{phase} 滑点成本（已体现在成交价）",
                metadata={**meta, "position_id": position_id, "slippage_cost": slippage_cost, "phase": phase},
            )

        if abs(float(pnl_delta or 0)) > 1e-9:
            bal_row.available_usd = float(bal_row.available_usd or 0) + float(pnl_delta)
            account.realized_pnl = float(account.realized_pnl or 0) + float(pnl_delta)
            self._ledger(
                db,
                account_id,
                ex,
                "paper_pnl",
                float(pnl_delta),
                float(bal_row.available_usd),
                strategy_type=strategy_type,
                related_position_id=position_id,
                note=note or f"{phase} 已实现盈亏",
                metadata={**meta, "position_id": position_id, "phase": phase},
            )

        # 同步总账户权益
        all_bals = db.query(ArbitragePaperExchangeBalanceDB).filter(
            ArbitragePaperExchangeBalanceDB.account_id == account_id
        ).all()
        total_available = sum(float(b.available_usd or 0) for b in all_bals)
        account.available_balance = total_available
        account.total_equity = total_available + float(account.frozen_balance or 0)

    def _ledger(
        self,
        db: Session,
        account_id: int,
        exchange: Optional[str],
        action: str,
        amount: float,
        balance_after: Optional[float],
        *,
        note: str = "",
        strategy_type: Optional[str] = None,
        related_position_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        db.add(ArbitragePaperLedgerDB(
            account_id=account_id,
            exchange=exchange,
            action=action,
            amount_usd=float(amount or 0),
            balance_after=balance_after,
            strategy_type=strategy_type,
            related_position_id=related_position_id,
            note=note,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
        ))

    def _check(self, name: str, passed: bool, ok: str, fail: str) -> Dict[str, Any]:
        return {"name": name, "passed": bool(passed), "message": ok if passed else fail}

    def _required_exchanges(self, strategies: List[str]) -> List[str]:
        mapping = {
            "S1": ["asterdex", "binance"],
            "S2": ["okx"],
            "S3": ["hyperliquid"],
            "S4": ["okx", "bybit", "gateio"],
            "S5": ["hyperliquid"],
            "S6": ["asterdex", "binance"],
            "S7": ["binance"],
            "S8": ["asterdex"],
        }
        result: List[str] = []
        for sid in strategies:
            for ex in mapping.get(sid, []):
                if ex not in result:
                    result.append(ex)
        return result


arbitrage_paper_account_service = ArbitragePaperAccountService()
