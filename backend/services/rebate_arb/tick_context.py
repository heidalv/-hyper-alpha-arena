"""
Rebate tick 共享上下文 — FullAuto / API / ExecutionAuthority 共用

消除 incentive_data / funding_rates / account_equity 采集重复。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _summary_to_dict(summary: Any, key: str) -> Dict[str, Any]:
    """ExchangeIncentiveSummary 或类似对象 → 策略 evaluate() 期望的扁平 schema

    字段与 incentive_aggregator.get_latest_as_dict() 保持一致（单一 schema），
    包含策略代码实际读取的扁平键：rebate_rate / volume_30d / daily_points_rate /
    tier_name / points_balance / rh_points / rh_per_1k_usd / alpha_daily_rate 等。
    """
    if summary is None:
        return {}
    exchange_name = getattr(summary, "exchange", key)
    fee_tier = getattr(summary, "fee_tier", None)
    points = getattr(summary, "points", None)
    rebate = getattr(summary, "rebate", None)
    entry: Dict[str, Any] = {
        "fee_tier": getattr(fee_tier, "tier_name", "") if fee_tier else "",
        "tier_name": getattr(fee_tier, "tier_name", "") if fee_tier else "",
        "maker_rate": getattr(fee_tier, "maker_rate", 0) if fee_tier else 0,
        "taker_rate": getattr(fee_tier, "taker_rate", 0) if fee_tier else 0,
        "rebate_rate": getattr(fee_tier, "rebate_rate", 0) if fee_tier else 0,
        "volume_30d": getattr(fee_tier, "volume_30d_usd", 0) if fee_tier else 0,
        "next_tier_volume": getattr(fee_tier, "next_tier_volume", 0) if fee_tier else 0,
        "points_balance": getattr(points, "points_balance", 0) if points else 0,
        "points_multiplier": getattr(points, "points_multiplier", 1.0) if points else 1.0,
        "daily_points_rate": getattr(points, "daily_points_rate", 0) if points else 0,
        "airdrop_eligible": getattr(points, "airdrop_eligible", False) if points else False,
        "estimated_airdrop_value": getattr(points, "estimated_airdrop_value", 0) if points else 0,
        "base_rebate_rate": getattr(rebate, "base_rebate_rate", 0) if rebate else 0,
        "current_rebate_rate": getattr(rebate, "current_rebate_rate", 0) if rebate else 0,
        "stacked_multiplier": getattr(rebate, "stacked_multiplier", 1.0) if rebate else 1.0,
        "rh_points": (getattr(points, "points_balance", 0) if points else 0) if exchange_name == "asterdex" else 0,
        "alpha_daily_rate": (getattr(points, "daily_points_rate", 0) if points else 0) if exchange_name == "binance" else 0,
    }
    if exchange_name == "asterdex":
        entry["rh_per_1k_usd"] = float(getattr(points, "rh_per_1k_usd", 0.0) or 0.1) if points else 0.1
    if exchange_name == "binance":
        entry["alpha_points_balance"] = getattr(points, "points_balance", 0) if points else 0
    return {exchange_name: entry}


def resolve_account_equity(
    snapshot: Any = None,
    explicit_equity: Optional[float] = None,
) -> float:
    if explicit_equity is not None and explicit_equity > 0:
        return float(explicit_equity)
    try:
        from backend.services.rebate_arb.capital_coordinator import capital_coordinator

        arb_id = capital_coordinator.get_arbitrage_paper_account_id()
        if arb_id:
            status = capital_coordinator.get_status()
            if status.total_equity > 0:
                return float(status.total_equity)
        if capital_coordinator.is_paper_mode():
            status = capital_coordinator.get_status()
            if status.total_equity > 0:
                return float(status.total_equity)
    except Exception as e:
        logger.debug("[TickContext] capital equity: %s", e)
    if snapshot is not None:
        eq = getattr(snapshot, "total_equity", 0) or getattr(snapshot, "account_equity", 0)
        if eq and float(eq) > 0:
            return float(eq)
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager

        mgr = get_exchange_manager()
        if mgr:
            total = 0.0
            for client in mgr.get_all_clients().values():
                try:
                    bal = client.get_balance()
                    total += float(getattr(bal, "total_equity", 0) or 0)
                except Exception:
                    pass
            if total > 0:
                return total
    except Exception:
        pass
    return 0.0


def fetch_incentive_data() -> Dict[str, Any]:
    """优先 IncentiveAggregator 缓存（含 active_campaigns），降级 ExchangeManager REST"""
    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator

        # 单一 schema 来源：聚合器的扁平 dict（策略 evaluate() 期望格式）
        data = incentive_aggregator.get_latest_as_dict()
        # 至少包含一个交易所数据才算有效（active_campaigns 键恒存在）
        if any(k != "active_campaigns" for k in data):
            return data
    except Exception as e:
        logger.debug("[TickContext] incentive_aggregator: %s", e)

    data = {}
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        from backend.services.arbitrage.async_bridge import run_async_safe

        mgr = get_exchange_manager()
        clients = mgr.get_all_clients() if mgr else {}
        for key, client in (clients.items() if isinstance(clients, dict) else []):
            try:
                summary = run_async_safe(client.get_incentive_summary())
                if summary:
                    data.update(_summary_to_dict(summary, key))
            except Exception:
                pass
    except Exception as e:
        logger.debug("[TickContext] exchange incentive fetch: %s", e)

    data.setdefault("active_campaigns", [])
    return data


def fetch_funding_rates(symbols: Optional[List[str]] = None) -> Dict[str, float]:
    symbols = symbols or []
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        from backend.services.arbitrage.async_bridge import run_async_safe

        mgr = get_exchange_manager()
        if mgr and symbols:
            return run_async_safe(
                mgr.get_cross_exchange_funding_rates(symbols),
                default={},
            ) or {}
    except Exception as e:
        logger.debug("[TickContext] funding rates: %s", e)

    try:
        from backend.services.arbitrage.opportunity_scanner import opportunity_scanner

        rates: Dict[str, float] = {}
        for o in opportunity_scanner.get_active_opportunities():
            if o.funding_snapshot:
                rates[o.symbol] = o.funding_snapshot.current_rate
        return rates
    except Exception:
        return {}


def build_rebate_tick_context(
    symbols: Optional[List[str]] = None,
    snapshot: Any = None,
    account_equity: Optional[float] = None,
) -> Dict[str, Any]:
    """构建 rebate tick 所需完整上下文"""
    equity = resolve_account_equity(snapshot, account_equity)
    incentive_data = fetch_incentive_data()
    funding_rates = fetch_funding_rates(symbols)

    if equity > 0:
        try:
            from backend.services.rebate_arb.capital_coordinator import capital_coordinator

            capital_coordinator.update_equity(equity)
        except Exception as e:
            logger.debug("[TickContext] capital update: %s", e)

    return {
        "account_equity": equity,
        "incentive_data": incentive_data,
        "funding_rates": funding_rates,
        "symbols": symbols or [],
    }


_last_rebate_arb_context: Dict[str, Any] = {}


def get_last_rebate_arb_context() -> Dict[str, Any]:
    """最近一次 rebate tick 的 prompt 友好上下文（供方向交易 Master 注入）。"""
    return dict(_last_rebate_arb_context)


def _format_rebate_summary_text(
    profile_params: Dict[str, Any],
    ctx_data: Dict[str, Any],
    opportunities: Optional[List[Dict[str, Any]]] = None,
) -> str:
    enabled = profile_params.get("enabled_strategies") or []
    equity = float(ctx_data.get("account_equity") or 0)
    lines = ["积分/返利套利上下文（与方向交易共用同一 AI 交易员双模型）:"]
    if enabled:
        lines.append(f"- 已授权策略: {', '.join(enabled)}")
    else:
        lines.append("- 已授权策略: （未指定，使用引擎默认）")
    if profile_params.get("account_name"):
        lines.append(f"- 绑定交易员: {profile_params['account_name']}")
    if equity > 0:
        lines.append(f"- 套利账户权益: ${equity:,.0f}")

    viable = [o for o in (opportunities or []) if isinstance(o, dict) and o.get("is_viable", True)]
    if viable:
        lines.append("- 当前可行机会:")
        for opp in viable[:5]:
            sid = opp.get("strategy_type") or opp.get("strategy") or "?"
            monthly = float(opp.get("expected_monthly_value") or 0)
            lines.append(f"  · {sid}: 月预期 ${monthly:,.0f}")
    else:
        lines.append("- 当前可行机会: 暂无或未扫描")

    lines.append(
        "- 说明: 方向仓与套利仓独立资金池；套利执行由 rebate_arb 域 Agent 负责，"
        "此处仅供 Master 感知整体风险与机会。"
    )
    return "\n".join(lines)


def build_rebate_arb_context(
    ctx_data: Dict[str, Any],
    profile_params: Dict[str, Any],
    opportunities: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构建 rebate_arb_context — QAA input + 方向交易 prompt 共用。"""
    global _last_rebate_arb_context
    summary_text = _format_rebate_summary_text(profile_params, ctx_data, opportunities)
    structured = {
        "enabled_strategies": profile_params.get("enabled_strategies") or [],
        "trader_profile_id": profile_params.get("trader_profile_id"),
        "trader_account_id": profile_params.get("trader_account_id"),
        "account_equity": float(ctx_data.get("account_equity") or 0),
        "strategy_llm_config_id": profile_params.get("strategy_llm_config_id"),
        "execution_llm_config_id": profile_params.get("execution_llm_config_id"),
        "top_opportunities": [
            {
                "strategy_type": o.get("strategy_type") or o.get("strategy"),
                "expected_monthly_value": o.get("expected_monthly_value"),
                "risk_score": o.get("risk_score"),
                "confidence": o.get("confidence"),
            }
            for o in (opportunities or [])[:5]
            if isinstance(o, dict)
        ],
        "summary_text": summary_text,
    }
    _last_rebate_arb_context = structured
    return structured
