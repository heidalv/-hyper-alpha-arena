"""
Aster 实盘「真实收入账本」—— 替代已失效的 Stage 6 Rh 积分面板。

[2026-09-03] 背景
  实盘交易页此前展示的「Rh 积分 / 空投预估 / 返佣预期」全部建立在
  已结束的 Stage 6 积分赛季与不存在的 /fapi/v1/rh/points 端点之上，
  数值要么恒 0、要么是"7日交易量 × 0.001"这类臆造公式。

  本模块只汇总**交易所真实返回**的数据，并按 Trade & Earn（进行中）的
  官方规则计算门槛进度；任何"参考年化"都显式标注 reference=True。

数据来源（全部为 Aster 币安兼容真实端点，见 AsterdexAdapter）
  /fapi/v1/multiAssetsMargin   多资产模式（USDF/asBNB 作保证金的前提）
  /fapi/v2/account.assets      逐资产保证金余额 → USDF 抵押占比
  /fapi/v1/commissionRate      账户真实 maker/taker 费率
  /fapi/v1/income              手续费 / 资金费 / 已实现盈亏 / 奖励入账流水
  /fapi/v1/userTrades          成交明细 → maker 占比、本周交易量、活跃天数

输出结构见 build_live_income_ledger() 文档字符串。所有子步骤独立容错：
任一接口失败只在 errors[] 里记一条，不影响其它板块。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.services.rebate_arb.rule_registry import (
    STAGE6_POINT_MODEL,
    TRADE_AND_EARN_PROGRAM,
)

logger = logging.getLogger(__name__)

# income 类型分类（币安兼容口径；Aster 奖励入账可能用自定义类型 → 归入 reward）
_TRADING_INCOME_TYPES = {"COMMISSION", "FUNDING_FEE", "REALIZED_PNL", "INSURANCE_CLEAR"}
_TRANSFER_INCOME_TYPES = {
    "TRANSFER", "INTERNAL_TRANSFER", "CROSS_COLLATERAL_TRANSFER",
    "COIN_SWAP_DEPOSIT", "COIN_SWAP_WITHDRAW", "AUTO_EXCHANGE",
}

_PER_CALL_TIMEOUT_S = 8.0
_MAX_TRADE_SYMBOLS = 12
_CACHE_TTL_S = 55.0

_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _week_start_utc(now: datetime) -> datetime:
    """Trade & Earn 结算周起点：周四 00:00 UTC（week_start_weekday=3，周一=0）。"""
    wsd = int(TRADE_AND_EARN_PROGRAM.get("week_start_weekday", 3))
    delta_days = (now.weekday() - wsd) % 7
    start = (now - timedelta(days=delta_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def _call(coro, label: str, errors: List[str], default):
    """带超时的单步调用；失败记 errors 并返回 default（不抛）。"""
    try:
        return await asyncio.wait_for(coro, timeout=_PER_CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        errors.append(f"{label}: timeout>{_PER_CALL_TIMEOUT_S:.0f}s")
    except Exception as e:  # noqa: BLE001
        errors.append(f"{label}: {str(e)[:120]}")
    return default


def _symbol_base(sym: str) -> str:
    s = str(sym or "").upper()
    for suf in ("USDT", "USD1"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s.split("/")[0]


async def build_live_income_ledger(client: Any, *, days: int = 7, cache_key: Optional[str] = None) -> Dict[str, Any]:
    """汇总 Aster 实盘账户的真实收入账本。

    返回:
    {
      "window_days": 7,
      "account": {
        "multi_assets_mode": bool|None, "total_margin_usd": float,
        "usdf_balance": float, "usdf_share": float(0-1), "eligible_collateral_usd": float,
        "assets": [{"asset","wallet_balance","available","collateral_ratio","margin_available"}]
      },
      "fees": {
        "maker_rate": float, "taker_rate": float, "rate_source": "api"|"schedule",
        "commission_paid_usd": float(>=0), "funding_fee_usd": float(净，正=收到),
        "realized_pnl_usd": float
      },
      "execution": {
        "trades": int, "notional_usd": float, "maker_notional_usd": float,
        "maker_ratio": float|None, "maker_ratio_count": float|None,
        "fee_saved_est_usd": float, "symbols": [str], "symbols_truncated": bool
      },
      "trade_and_earn": {
        "active": True, "week_start": iso, "week_end": iso,
        "volume_usd": float, "volume_threshold_usd": float, "volume_progress": float(0-1),
        "active_days": int, "active_days_threshold": int,
        "eligible_now": bool, "blockers": [str],
        "usdf_counted_usd": float, "usdf_cap_usd": float,
        "reference_weekly_reward_usd": float, "reference_apy": {...}, "reference": True,
        "requires_multi_assets_mode": True, "source_url": str
      },
      "rewards": {
        "usdf_received": float, "by_type": {type: {asset: amount}}, "count": int, "note": str
      },
      "programs": {"stage6": {"status": "ended", "note": str}},
      "errors": [str]
    }
    """
    if cache_key:
        with _cache_lock:
            hit = _cache.get(cache_key)
        if hit and time.time() - hit[0] < _CACHE_TTL_S:
            return dict(hit[1])

    errors: List[str] = []
    now = datetime.now(timezone.utc)
    days = max(1, min(int(days or 7), 60))
    window_start = now - timedelta(days=days)
    week_start = _week_start_utc(now)
    week_end = week_start + timedelta(days=7) - timedelta(seconds=1)
    since = min(window_start, week_start)
    since_ms = int(since.timestamp() * 1000)

    # ── 1) 账户：多资产模式 + 逐资产保证金 ──────────────────────────
    multi_mode = await _call(client.get_multi_assets_mode(), "multiAssetsMargin", errors, None)
    assets_raw = await _call(client.get_margin_assets(), "account.assets", errors, [])
    total_margin = 0.0
    usdf_bal = 0.0
    eligible_collateral = 0.0
    eligible_set = {str(a).upper() for a in (TRADE_AND_EARN_PROGRAM.get("eligible_collateral") or [])}
    assets_out: List[Dict[str, Any]] = []
    for a in assets_raw or []:
        asset = str(a.get("asset") or "").upper()
        wb = _f(a.get("wallet_balance"))
        ratio = a.get("collateral_ratio")
        usd = wb * (float(ratio) if ratio is not None else 0.0)
        if a.get("margin_available", True):
            total_margin += usd
        if asset == "USDF":
            usdf_bal += wb
        if asset in eligible_set or asset.upper() == "ASBNB":
            eligible_collateral += usd
        assets_out.append({
            "asset": a.get("asset"),
            "wallet_balance": round(wb, 6),
            "available": round(_f(a.get("available")), 6),
            "collateral_ratio": ratio,
            "margin_available": bool(a.get("margin_available", True)),
            "usd_value": round(usd, 4),
        })
    usdf_share = (usdf_bal * float(TRADE_AND_EARN_PROGRAM["collateral_ratio"].get("USDF", 0.9999)) / total_margin) if total_margin > 0 else 0.0

    # ── 2) 费率：真实账户费率，失败回退单一来源费率表 ─────────────────
    fee_sched = (TRADE_AND_EARN_PROGRAM.get("fee_schedule") or {}).get("usdt_perp") or {"maker": 0.0, "taker": 0.0004}
    comm = await _call(client.get_commission_rate("BTCUSDT"), "commissionRate", errors, None)
    if comm:
        maker_rate, taker_rate, rate_source = float(comm["maker"]), float(comm["taker"]), "api"
    else:
        maker_rate, taker_rate, rate_source = float(fee_sched["maker"]), float(fee_sched["taker"]), "schedule"

    # ── 3) 资金流水：手续费 / 资金费 / 已实现盈亏 / 奖励入账 ──────────
    income = await _call(client.get_income_history(since_ms), "income", errors, [])
    commission_paid = 0.0
    funding_net = 0.0
    realized = 0.0
    rewards_by_type: Dict[str, Dict[str, float]] = {}
    rewards_usdf = 0.0
    rewards_count = 0
    traded_symbols: Dict[str, int] = {}
    for row in income or []:
        ts = _f(row.get("time"))
        if ts and ts < window_start.timestamp() * 1000:
            # 周窗口可能早于统计窗口：流水只按统计窗口计
            continue
        itype = str(row.get("incomeType") or "").upper()
        asset = str(row.get("asset") or "").upper() or "?"
        amt = _f(row.get("income"))
        sym = str(row.get("symbol") or "").upper()
        if itype == "COMMISSION":
            commission_paid += -amt  # 手续费为负数流水
            if sym:
                traded_symbols[sym] = traded_symbols.get(sym, 0) + 1
        elif itype == "FUNDING_FEE":
            funding_net += amt
        elif itype == "REALIZED_PNL":
            realized += amt
            if sym:
                traded_symbols[sym] = traded_symbols.get(sym, 0) + 1
        elif itype in _TRADING_INCOME_TYPES or itype in _TRANSFER_INCOME_TYPES:
            continue
        else:
            # 非交易、非转账 → 奖励类（推荐返佣 / 活动奖励 / Trade&Earn USDF 等）
            rewards_by_type.setdefault(itype or "UNKNOWN", {})
            rewards_by_type[itype or "UNKNOWN"][asset] = rewards_by_type[itype or "UNKNOWN"].get(asset, 0.0) + amt
            rewards_count += 1
            if asset == "USDF":
                rewards_usdf += amt

    # ── 4) 成交明细：maker 占比 + 本周交易量 / 活跃天数 ────────────────
    sym_list = [s for s, _ in sorted(traded_symbols.items(), key=lambda kv: -kv[1])]
    truncated = len(sym_list) > _MAX_TRADE_SYMBOLS
    sym_list = sym_list[:_MAX_TRADE_SYMBOLS]
    trades_all: List[Dict[str, Any]] = []
    if sym_list:
        results = await asyncio.gather(
            *[_call(client.get_user_trades(s, since_ms), f"userTrades:{s}", errors, []) for s in sym_list],
            return_exceptions=False,
        )
        for r in results:
            trades_all.extend(r or [])

    n_trades = 0
    notional = 0.0
    maker_notional = 0.0
    maker_count = 0
    week_volume = 0.0
    week_days: set = set()
    for t in trades_all:
        ts = _f(t.get("time"))
        q = _f(t.get("quoteQty"))
        if q <= 0:
            q = _f(t.get("price")) * _f(t.get("qty"))
        is_maker = str(t.get("maker")).lower() == "true" or t.get("maker") is True
        if ts >= week_start.timestamp() * 1000:
            week_volume += q
            week_days.add(datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat())
        if ts and ts < window_start.timestamp() * 1000:
            continue
        n_trades += 1
        notional += q
        if is_maker:
            maker_notional += q
            maker_count += 1
    maker_ratio = (maker_notional / notional) if notional > 0 else None
    maker_ratio_count = (maker_count / n_trades) if n_trades > 0 else None
    fee_saved = maker_notional * max(taker_rate - maker_rate, 0.0)

    # ── 5) Trade & Earn 门槛进度（官方规则单一来源） ──────────────────
    vol_thr = float(TRADE_AND_EARN_PROGRAM.get("weekly_volume_threshold_usd", 50_000))
    days_thr = int(TRADE_AND_EARN_PROGRAM.get("weekly_active_days_threshold", 2))
    usdf_cap = float(TRADE_AND_EARN_PROGRAM.get("usdf_counted_cap", 100_000))
    apy = TRADE_AND_EARN_PROGRAM.get("reference_apy") or {}
    usdf_counted = min(usdf_bal, usdf_cap)
    blockers: List[str] = []
    if multi_mode is False:
        blockers.append("未开启多资产模式（USDF/asBNB 不能作保证金）")
    if usdf_bal <= 0:
        blockers.append("账户无 USDF 抵押品（奖励按 USDF 抵押计）")
    if week_volume < vol_thr:
        blockers.append(f"本周交易量 ${week_volume:,.0f} < ${vol_thr:,.0f}（交易奖励门槛）")
    if len(week_days) < days_thr:
        blockers.append(f"本周活跃 {len(week_days)} 天 < {days_thr} 天")
    trading_eligible = week_volume >= vol_thr and len(week_days) >= days_thr and usdf_bal > 0 and multi_mode is not False
    ref_apy_total = float(apy.get("deposit", 0.0)) + (float(apy.get("trading", 0.0)) if trading_eligible else 0.0)
    ref_weekly = usdf_counted * ref_apy_total / 52.0 if usdf_counted > 0 else 0.0

    out: Dict[str, Any] = {
        "window_days": days,
        "account": {
            "multi_assets_mode": multi_mode,
            "total_margin_usd": round(total_margin, 4),
            "usdf_balance": round(usdf_bal, 6),
            "usdf_share": round(usdf_share, 4),
            "eligible_collateral_usd": round(eligible_collateral, 4),
            "assets": assets_out,
        },
        "fees": {
            "maker_rate": maker_rate,
            "taker_rate": taker_rate,
            "rate_source": rate_source,
            "commission_paid_usd": round(commission_paid, 6),
            "funding_fee_usd": round(funding_net, 6),
            "realized_pnl_usd": round(realized, 6),
        },
        "execution": {
            "trades": n_trades,
            "notional_usd": round(notional, 2),
            "maker_notional_usd": round(maker_notional, 2),
            "maker_ratio": round(maker_ratio, 4) if maker_ratio is not None else None,
            "maker_ratio_count": round(maker_ratio_count, 4) if maker_ratio_count is not None else None,
            "fee_saved_est_usd": round(fee_saved, 6),
            "symbols": [_symbol_base(s) for s in sym_list],
            "symbols_truncated": truncated,
        },
        "trade_and_earn": {
            "active": bool(TRADE_AND_EARN_PROGRAM.get("active", True)),
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "volume_usd": round(week_volume, 2),
            "volume_threshold_usd": vol_thr,
            "volume_progress": round(min(week_volume / vol_thr, 1.0), 4) if vol_thr > 0 else 1.0,
            "active_days": len(week_days),
            "active_days_threshold": days_thr,
            "eligible_now": bool(trading_eligible),
            "blockers": blockers,
            "usdf_counted_usd": round(usdf_counted, 4),
            "usdf_cap_usd": usdf_cap,
            "reference_weekly_reward_usd": round(ref_weekly, 4),
            "reference_apy": apy,
            "reference": True,
            "requires_multi_assets_mode": bool(TRADE_AND_EARN_PROGRAM.get("requires_multi_assets_mode", True)),
            "source_url": TRADE_AND_EARN_PROGRAM.get("source_url"),
        },
        "rewards": {
            "usdf_received": round(rewards_usdf, 6),
            "by_type": {k: {a: round(v, 6) for a, v in d.items()} for k, d in rewards_by_type.items()},
            "count": rewards_count,
            "note": "统计窗口内非交易/非转账类入账（推荐返佣、活动奖励、Trade & Earn USDF 等），以交易所 income 流水为准",
        },
        "programs": {
            "stage6": {
                "status": STAGE6_POINT_MODEL.get("stage_status", "ended"),
                "note": STAGE6_POINT_MODEL.get("stage_note", ""),
            },
        },
        "errors": errors,
    }
    if cache_key:
        with _cache_lock:
            _cache[cache_key] = (time.time(), dict(out))
    return out


def invalidate_ledger_cache(cache_key: Optional[str] = None) -> None:
    with _cache_lock:
        if cache_key is None:
            _cache.clear()
        else:
            _cache.pop(cache_key, None)
