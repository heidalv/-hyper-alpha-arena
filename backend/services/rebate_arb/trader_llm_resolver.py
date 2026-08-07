"""交易员 LLM 统一解析：分析模型 + 执行模型，方向交易与积分套利共用。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def resolve_trader_llm_pair(account: Any, profile: Any = None) -> Tuple[Optional[int], Optional[int]]:
    """
    分析模型 = llm_config_id_deep
    执行模型 = llm_config_id

    解析顺序：档案显式指定（strategy/execution/linked）→ 账户绑定 → 无。
    档案字段仅在显式指定时优先；账户作为兜底，保证旧数据/未配置档案行为不变。
    """
    strategy: Optional[int] = None
    execution: Optional[int] = None

    if profile is not None:
        strategy = getattr(profile, "strategy_llm_config_id", None) or getattr(profile, "linked_llm_config_id", None)
        execution = getattr(profile, "execution_llm_config_id", None) or getattr(profile, "linked_llm_config_id", None)

    if account is not None:
        if not strategy:
            strategy = getattr(account, "llm_config_id_deep", None) or getattr(account, "llm_config_id", None)
        if not execution:
            execution = getattr(account, "llm_config_id", None) or getattr(account, "llm_config_id_deep", None)

    if strategy is not None:
        strategy = int(strategy)
    if execution is not None:
        execution = int(execution)

    return strategy, execution


def resolve_trader_llm_from_profile_id(profile_id: Optional[int]) -> dict[str, Any]:
    """按套利档案 ID 解析双模型，供 QAA handler 使用。"""
    if not profile_id:
        return {}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import Account, ArbitrageProfileDB

        db = SessionLocal()
        try:
            profile = db.query(ArbitrageProfileDB).filter(ArbitrageProfileDB.id == int(profile_id)).first()
            if not profile:
                return {}
            account = db.query(Account).filter(Account.id == profile.account_id).first()
            strategy, execution = resolve_trader_llm_pair(account, profile)
            return {
                "strategy_llm_config_id": strategy,
                "execution_llm_config_id": execution,
                "profile_id": profile.id,
                "account_id": profile.account_id,
            }
        finally:
            db.close()
    except Exception:
        return {}


def sync_profile_llm_from_account(profile: Any, account: Any) -> None:
    """用账户绑定回填空字段，绝不覆盖档案里已显式指定的 LLM。"""
    strategy, execution = resolve_trader_llm_pair(account)
    if not getattr(profile, "strategy_llm_config_id", None) and strategy:
        profile.strategy_llm_config_id = strategy
    if not getattr(profile, "execution_llm_config_id", None) and execution:
        profile.execution_llm_config_id = execution
    if strategy and not getattr(profile, "linked_llm_config_id", None):
        profile.linked_llm_config_id = strategy


def _normalize_strategies(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return [raw.upper()] if raw else []
    return [str(s).upper() for s in raw if s]


def resolve_rebate_tick_params(
    *,
    trader_account_id: Optional[int] = None,
    trader_profile_id: Optional[int] = None,
    arbitrage_paper_account_id: Optional[int] = None,
    profile_snapshot: Optional[Dict[str, Any]] = None,
    enabled_strategies: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    FullAuto / 套利 Paper / QAA tick 共用的 rebate 运行参数。

    LLM 双模型优先来自 Account；profile 仅提供策略授权与 Paper 绑定。
    """
    if profile_snapshot:
        strategies = _normalize_strategies(
            enabled_strategies or profile_snapshot.get("enabled_strategies")
        )
        profile_id = profile_snapshot.get("profile_id") or trader_profile_id
        out: Dict[str, Any] = {
            "trader_profile_id": int(profile_id) if profile_id else None,
            "trader_account_id": profile_snapshot.get("trader_account_id") or trader_account_id,
            "enabled_strategies": strategies,
            "mode": profile_snapshot.get("mode"),
            "paper_account_mode": profile_snapshot.get("paper_account_mode"),
            "arbitrage_paper_account_id": profile_snapshot.get("arbitrage_paper_account_id"),
            "wash_trade_profile": profile_snapshot.get("wash_trade_profile"),
        }
        if profile_id:
            llm = resolve_trader_llm_from_profile_id(int(profile_id))
            out.update(llm)
        return out

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import Account, ArbitrageProfileDB

        db = SessionLocal()
        try:
            profile = None
            if trader_profile_id:
                profile = db.query(ArbitrageProfileDB).filter(
                    ArbitrageProfileDB.id == int(trader_profile_id)
                ).first()
            elif trader_account_id:
                profile = db.query(ArbitrageProfileDB).filter(
                    ArbitrageProfileDB.account_id == int(trader_account_id)
                ).first()
            elif arbitrage_paper_account_id:
                from backend.services.rebate_arb.arbitrage_paper_account_service import (
                    arbitrage_paper_account_service,
                )

                bound = arbitrage_paper_account_service._find_trader_arbitrage_profile(
                    db, int(arbitrage_paper_account_id)
                )
                if bound:
                    return resolve_rebate_tick_params(
                        profile_snapshot=bound,
                        enabled_strategies=enabled_strategies,
                    )

            if not profile:
                return {
                    "trader_profile_id": trader_profile_id,
                    "trader_account_id": trader_account_id,
                    "enabled_strategies": _normalize_strategies(enabled_strategies),
                }

            owner = db.query(Account).filter(Account.id == profile.account_id).first()
            strategy_llm, execution_llm = resolve_trader_llm_pair(owner, profile)
            strategies = _normalize_strategies(
                enabled_strategies or profile.enabled_strategies_json
            )
            return {
                "trader_profile_id": profile.id,
                "trader_account_id": profile.account_id,
                "account_name": getattr(owner, "name", None),
                "enabled_strategies": strategies,
                "strategy_llm_config_id": strategy_llm,
                "execution_llm_config_id": execution_llm,
                "linked_llm_config_id": profile.linked_llm_config_id,
                "mode": profile.mode,
                "paper_account_mode": profile.paper_account_mode,
                "arbitrage_paper_account_id": profile.arbitrage_paper_account_id,
                "wash_trade_profile": profile.wash_trade_profile,
                "enabled": bool(profile.enabled),
            }
        finally:
            db.close()
    except Exception:
        return {
            "trader_profile_id": trader_profile_id,
            "trader_account_id": trader_account_id,
            "enabled_strategies": _normalize_strategies(enabled_strategies),
        }
