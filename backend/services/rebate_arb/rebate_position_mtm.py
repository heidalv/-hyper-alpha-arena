"""
Paper 积分仓位 Mark-to-Market — 真实市价、资金费、积分累计与 DB 同步。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .models import RebatePosition, RebatePositionStatus
from .rebate_paper_market import calc_funding_pnl, resolve_paper_market
from .rebate_paper_simulator import PaperLegFill, calc_unrealized_leg_pnl

logger = logging.getLogger(__name__)

# S8 Rh：持仓 ≥60min 触发 2x 时间加成（与策略一致）
S8_RH_TIME_BONUS_HOURS = 1.0
S8_RH_TIME_BONUS_MULT = 2.0
S8_HOLD_TARGET_SECONDS = 3900  # 60min + 5min buffer


def _s8_rh_display_fields(position: RebatePosition, meta: Dict[str, Any]) -> Dict[str, Any]:
    """S8 积分进度展示字段。"""
    if position.strategy_type.value != "S8":
        return {}
    notional = float(position.side_a_size or 0)
    sym_boost = float(meta.get("symbol_boost") or (meta.get("multiplier_stack") or {}).get("symbol_boost") or 1.0)
    stack = meta.get("multiplier_stack") if isinstance(meta.get("multiplier_stack"), dict) else {}
    hold_target = float(meta.get("hold_target_time") or 0)
    hold_start = float(meta.get("hold_start_time") or position.entry_time or 0)
    now = time.time()
    target_sec = float(
        (meta.get("hold_phase") or {}).get("total_seconds")
        or S8_HOLD_TARGET_SECONDS
    )
    elapsed = max(now - hold_start, 0.0) if hold_start else position.hold_duration_hours * 3600
    remaining_sec = max(hold_target - now, 0.0) if hold_target else max(target_sec - elapsed, 0.0)
    time_bonus_active = position.hold_duration_hours >= S8_RH_TIME_BONUS_HOURS
    est_round = float(meta.get("estimated_round_rh") or 0)
    if est_round <= 0 and notional > 0:
        est_round = round(notional * 2 * 0.0001 * 80 * sym_boost, 2)
    progress_pct = min(100.0, (elapsed / target_sec * 100) if target_sec > 0 else 0.0)
    return {
        "margin_usd": meta.get("margin_usd"),
        "symbol_boost": sym_boost,
        "rh_multiplier_stack": stack,
        "rh_target_hours": round(target_sec / 3600, 2),
        "rh_hold_remaining_minutes": round(remaining_sec / 60, 1),
        "rh_time_bonus_active": time_bonus_active,
        "rh_hold_progress_pct": round(progress_pct, 1),
        "estimated_round_rh": est_round,
        "points_maximization_mode": meta.get("points_maximization_mode"),
    }


def _s8_target_hold_hours(meta: Dict[str, Any]) -> float:
    """S8 单轮目标持仓时长（小时），与 Rh 进度列一致。"""
    target_h = float(meta.get("rh_target_hours") or 0)
    if target_h > 0:
        return target_h
    total_sec = float((meta.get("hold_phase") or {}).get("total_seconds") or 0)
    if total_sec > 0:
        return total_sec / 3600.0
    return S8_HOLD_TARGET_SECONDS / 3600.0


def _estimate_paper_points(position: RebatePosition, meta: Dict[str, Any]) -> float:
    """按策略规则 + 激励缓存估算 Paper 积分（展示用，贴近真实规则）。"""
    hours = max(position.hold_duration_hours, 0.0)
    notional = float(position.side_a_size or 0)
    # 仅用开仓快照作基数；勿把 accumulated_points 当 base（MTM 每 tick 会重写该字段，会重复累加）
    base = float(meta.get("points_at_open") or 0)
    if notional <= 0:
        return round(base, 2)

    sid = position.strategy_type.value
    ex = (position.source_exchange or "").lower()

    daily_rate = 0.0
    multiplier = 1.0
    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator

        summary = (incentive_aggregator.get_latest() or {}).get(ex)
        if summary and summary.points:
            daily_rate = float(summary.points.daily_points_rate or 0)
            multiplier = float(summary.points.points_multiplier or 1.0)
    except Exception:
        pass

    # S8 Rh：持仓中按「整轮预估 Rh × 时间进度」线性展示，与 Rh 进度列口径一致
    if sid == "S8":
        sym_boost = float(meta.get("symbol_boost") or (meta.get("multiplier_stack") or {}).get("symbol_boost") or 1.0)
        est_round = float(meta.get("estimated_round_rh") or 0)
        if est_round <= 0:
            est_round = notional * 2 * 0.0001 * 80 * sym_boost
        target_h = _s8_target_hold_hours(meta)
        progress = min(hours / max(target_h, 0.01), 1.0)
        return round(base + est_round * progress, 2)

    if daily_rate > 0:
        return round(base + daily_rate * (hours / 24.0) * multiplier, 2)

    if sid == "S3":
        return round(base + (notional / 1000.0) * hours * 0.4, 2)
    return round(base + notional * 0.0001 * hours, 2)


def persist_position_mtm_db(position: RebatePosition) -> None:
    """将 MTM 结果写回 DB，避免重启后丢失浮盈快照。"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        db = rebate_arb_engine._get_db_session()
        if not db:
            return
        try:
            from backend.database.models import RebatePositionDB
            from backend.database.connection import sqlite_write_commit

            row = db.query(RebatePositionDB).filter(
                RebatePositionDB.position_id == position.position_id
            ).first()
            if not row:
                return
            row.current_pnl = float(position.current_pnl or 0)
            row.accumulated_rebate = float(position.accumulated_rebate or 0)
            row.accumulated_points = float(position.accumulated_points or 0)
            row.metadata_json = json.dumps(position.metadata or {}, default=str)
            sqlite_write_commit(db, label="rebate_position_mtm")
        except Exception as exc:
            logger.debug("[RebateMTM] persist failed %s: %s", position.position_id, exc)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        pass


def refresh_position_mtm(position: RebatePosition) -> bool:
    """刷新单仓未实现盈亏、资金费、积分与展示字段。"""
    if not position.paper_mode or position.status != RebatePositionStatus.ACTIVE:
        return False

    symbol = position.symbol or (position.metadata or {}).get("side_a", {}).get("symbol", "")
    exchange = position.source_exchange or ""
    quote = resolve_paper_market(symbol, exchange)
    if not quote or quote.mid <= 0:
        logger.debug("[RebateMTM] 无市价 %s@%s pos=%s", symbol, exchange, position.position_id)
        return False

    mark_price = float(quote.mark or quote.mid)

    meta = dict(position.metadata or {})
    entry_fills_raw = meta.get("paper_entry_fills") or {}
    legs: List[PaperLegFill] = []

    for fill_dict in entry_fills_raw.values():
        if isinstance(fill_dict, dict):
            try:
                legs.append(PaperLegFill(**fill_dict))
            except Exception:
                continue

    if not legs and position.entry_price_a and position.entry_price_a > 0:
        side = (meta.get("side_a") or {}).get("side", "buy")
        size_usd = float(position.side_a_size or 0)
        legs.append(
            PaperLegFill(
                exchange=exchange,
                side=side,
                order_type="market",
                size_usd=size_usd,
                ref_price=position.entry_price_a,
                filled_price=position.entry_price_a,
                size_coins=size_usd / position.entry_price_a,
                slippage_rate=0.0,
                slippage_cost_usd=0.0,
                fee_rate=0.0,
                fee_paid=0.0,
                rebate_rate=0.0,
                rebate_received=0.0,
                is_maker=False,
            )
        )

    if not legs:
        return False

    total_net = 0.0
    total_gross = 0.0
    open_rebate = 0.0
    open_fees = 0.0
    leg_views: List[Dict[str, Any]] = []

    for leg in legs:
        u = calc_unrealized_leg_pnl(leg, mark_price)
        total_net += u["net_pnl"]
        total_gross += u["gross_pnl"]
        open_rebate += leg.rebate_received
        open_fees += leg.fee_paid
        leg_views.append({
            "side": leg.side,
            "entry_price": round(leg.filled_price, 6),
            "mark_price": round(mark_price, 6),
            "size_coins": round(leg.size_coins, 6),
            "unrealized_pnl": round(u["net_pnl"], 4),
            "price_source": leg.price_source,
        })

    hours = max(position.hold_duration_hours, 0.0)
    notional = float(position.side_a_size or 0)
    funding_rate = float(
        meta.get("funding_rate_at_entry")
        or legs[0].funding_rate
        or quote.funding_rate
        or 0
    )
    primary_side = legs[0].side
    funding_pnl = calc_funding_pnl(primary_side, notional, funding_rate, hours)
    total_net += funding_pnl

    position.current_pnl = round(total_net, 4)
    if open_rebate > 0 and float(position.accumulated_rebate or 0) < open_rebate:
        position.accumulated_rebate = round(open_rebate, 4)
    position.accumulated_points = _estimate_paper_points(position, meta)

    primary = legs[0]
    if primary.size_coins > 0:
        meta["size_coins"] = round(primary.size_coins, 8)
        base_sym = (position.symbol or "").split("/")[0]
        if base_sym:
            meta["size_coins_display"] = f"{primary.size_coins:.6f} {base_sym}"
    lev = (meta.get("side_a") or {}).get("leverage")
    if lev:
        meta["leverage"] = lev
    meta["mark_price"] = round(mark_price, 6)
    meta["entry_price"] = round(primary.filled_price, 6)
    meta["side"] = primary.side
    meta["bid"] = round(quote.bid, 6)
    meta["ask"] = round(quote.ask, 6)
    meta["spread_bps"] = quote.spread_bps
    meta["unrealized_gross_pnl"] = round(total_gross, 4)
    meta["funding_pnl"] = round(funding_pnl, 4)
    meta["funding_rate"] = funding_rate
    meta["open_fees_paid"] = round(open_fees, 6)
    meta["price_source"] = quote.source
    meta["quote_exchange"] = quote.price_exchange
    meta["mtm_updated_at"] = time.time()
    meta["mtm_legs"] = leg_views
    meta["mtm_quote"] = quote.to_dict()
    position.metadata = meta

    persist_position_mtm_db(position)
    return True


def refresh_all_paper_positions_mtm() -> int:
    """刷新所有 Paper 活跃仓位的 MTM，返回成功刷新数量。"""
    try:
        from backend.services.rebate_arb.position_monitor import rebate_position_monitor
    except Exception:
        return 0

    updated = 0
    for pos in rebate_position_monitor.get_active_positions():
        if refresh_position_mtm(pos):
            updated += 1
    if updated:
        logger.debug("[RebateMTM] refreshed %s paper positions", updated)
    return updated


def serialize_position_for_api(position: RebatePosition) -> Dict[str, Any]:
    """API / Dashboard 统一仓位序列化（含真实成交与成本明细）。"""
    meta = position.metadata if isinstance(position.metadata, dict) else {}
    mark = float(meta.get("mark_price") or 0)
    entry = float(meta.get("entry_price") or position.entry_price_a or 0)
    side = meta.get("side") or (meta.get("side_a") or {}).get("side") or ""
    notional = float(position.side_a_size or 0)
    pnl_pct = (position.current_pnl / notional * 100) if notional > 0 else 0.0
    open_cost = meta.get("paper_open_cost") if isinstance(meta.get("paper_open_cost"), dict) else {}
    size_coins = meta.get("size_coins")
    if size_coins is None and meta.get("mtm_legs"):
        legs = meta.get("mtm_legs") or []
        if legs and isinstance(legs[0], dict):
            size_coins = legs[0].get("size_coins")
    leverage = meta.get("leverage") or (meta.get("side_a") or {}).get("leverage")
    base = (position.symbol or "").split("/")[0] if position.symbol else ""

    return {
        "position_id": position.position_id,
        "strategy_type": position.strategy_type.value,
        "source_exchange": position.source_exchange,
        "target_exchange": position.target_exchange,
        "symbol": position.symbol,
        "side": side,
        "size_coins": round(float(size_coins), 8) if size_coins else None,
        "size_coins_display": meta.get("size_coins_display") or (
            f"{float(size_coins):.6f} {base}" if size_coins and base else None
        ),
        "leverage": leverage,
        "side_a_size": round(notional, 2),
        "side_b_size": round(float(position.side_b_size or 0), 2),
        "entry_price": round(entry, 6) if entry else None,
        "mark_price": round(mark, 6) if mark else None,
        "bid": meta.get("bid"),
        "ask": meta.get("ask"),
        "spread_bps": meta.get("spread_bps"),
        "current_pnl": round(float(position.current_pnl or 0), 2),
        "pnl_pct": round(pnl_pct, 2),
        "funding_pnl": round(float(meta.get("funding_pnl") or 0), 4),
        "funding_rate": meta.get("funding_rate"),
        "accumulated_rebate": round(float(position.accumulated_rebate or 0), 4),
        "accumulated_points": round(float(position.accumulated_points or 0), 2),
        "hold_duration_hours": round(float(position.hold_duration_hours or 0), 2),
        "status": position.status.value,
        "paper_mode": position.paper_mode,
        "entry_time": position.entry_time,
        "mtm_updated_at": meta.get("mtm_updated_at"),
        "open_cost": open_cost,
        "open_fees_paid": meta.get("open_fees_paid"),
        "price_source": meta.get("price_source"),
        "quote_exchange": meta.get("quote_exchange"),
        "execution_phase": meta.get("execution_phase"),
        "rh_optimization_mode": meta.get("rh_optimization_mode"),
        "rh_optimizer": meta.get("rh_optimizer"),
        "rh_metrics": meta.get("rh_metrics"),
        "paper_ab_test_matrix": meta.get("paper_ab_test_matrix"),
        "wash_trade_check": meta.get("wash_trade_check"),
        **_s8_rh_display_fields(position, meta),
    }
