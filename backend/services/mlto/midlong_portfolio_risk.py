"""中长线组合风控（P2，2026-07-31）。

虚拟币永续：BTC/ETH/SOL 高度相关，多笔同向 ≈ 一笔大方向赌。
1. 净方向敞口上限（相对权益）
2. 相关簇同向持仓上限
3. 无进展超时离场（持仓过久且峰值未达 0.5R）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position"})
_MIDLONG_TIERS = frozenset({"mid", "long"})


def _cfg_bool(name: str, default: bool = True) -> bool:
    try:
        from backend.config import settings
        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        from backend.config import settings
        return float(getattr(settings, name, default) or default)
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    try:
        from backend.config import settings
        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


def _parse_cluster_symbols() -> List[str]:
    try:
        from backend.config import settings
        raw = getattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "BTC,ETH,SOL") or "BTC,ETH,SOL"
    except Exception:
        raw = "BTC,ETH,SOL"
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]


def _is_midlong_pos(pos: Dict[str, Any]) -> bool:
    nature = str(pos.get("trade_nature") or "").lower()
    tier = str(pos.get("timeframe_tier") or "").lower()
    return nature in _MIDLONG_NATURES or tier in _MIDLONG_TIERS


def _pos_dir(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _action_dir(action: str) -> str:
    a = (action or "").lower()
    if a in ("buy", "long"):
        return "long"
    if a in ("sell", "short"):
        return "short"
    return ""


def _notional(pos: Dict[str, Any]) -> float:
    try:
        size = float(pos.get("size") or pos.get("quantity") or 0)
        px = float(
            pos.get("mark_price")
            or pos.get("entry_price")
            or pos.get("current_price")
            or 0
        )
        if size > 0 and px > 0:
            return abs(size * px)
        margin = float(pos.get("margin") or 0)
        lev = float(pos.get("leverage") or 1) or 1
        if margin > 0:
            return abs(margin * lev)
    except Exception:
        return 0.0
    return 0.0


def _equity_from_portfolio(portfolio: Optional[Dict[str, Any]]) -> float:
    if not isinstance(portfolio, dict):
        return 0.0
    bal = portfolio.get("balance") if isinstance(portfolio.get("balance"), dict) else {}
    for k in ("total_equity", "equity", "balance", "available"):
        try:
            v = float(bal.get(k) or portfolio.get(k) or 0)
            if v > 0:
                return v
        except Exception:
            continue
    return 0.0


def collect_midlong_positions(
    portfolio: Optional[Dict[str, Any]] = None,
    positions: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if positions is not None:
        src = list(positions)
    elif isinstance(portfolio, dict):
        src = list(portfolio.get("positions") or portfolio.get("open_positions") or [])
    else:
        src = []
    return [p for p in src if isinstance(p, dict) and _is_midlong_pos(p)]


def estimate_open_notional(
    *,
    equity: float,
    margin_frac: float,
    leverage: float = 10.0,
    sl_pct: float = 0.0,
    risk_pct: float = 0.01,
) -> float:
    """估计本笔开仓名义，口径对齐真实成交。

    MLTO 的 tranche_margin_pct 是「占权益的保证金比例」（如 NIBBLE 0.15、BUILD 0.30，
    探针再 ×0.5）。真实名义 ≈ equity × margin_frac × leverage。

    旧口径用 equity×risk_pct/SL×tranche，会把 $350 的成交估成 ~$6，导致闸口形同虚设。
    当 margin_frac≈1.0（非 MLTO「不缩仓」默认）时退回风险预算公式，避免当成 100% 保证金。
    """
    eq = float(equity or 0)
    if eq <= 0:
        return 0.0
    try:
        mf = float(margin_frac)
    except (TypeError, ValueError):
        mf = 0.0
    if mf != mf or mf < 0:  # NaN / neg
        mf = 0.0
    lev = max(1.0, float(leverage or 10.0))
    if 0.0 < mf < 0.99:
        return abs(eq * mf * lev)
    sl = max(float(sl_pct or 0), 0.01)
    rp = float(risk_pct or 0.01)
    mult = 1.0 if mf <= 0 else min(1.0, mf)
    return abs(eq * rp / sl * mult)


def check_portfolio_open_allowed(
    *,
    symbol: str,
    action: str,
    portfolio: Optional[Dict[str, Any]] = None,
    positions: Optional[Sequence[Dict[str, Any]]] = None,
    new_notional: float = 0.0,
    max_net_pct: Optional[float] = None,
    is_probe: bool = False,
) -> Tuple[bool, str]:
    """开仓前组合闸：净方向敞口 + 相关簇同向数量。"""
    if not _cfg_bool("MIDLONG_PORTFOLIO_GATE_ENABLED", True):
        return True, "portfolio_gate_off"

    sym = str(symbol or "").upper()
    direction = _action_dir(action)
    if not direction:
        return True, "no_direction"

    mids = collect_midlong_positions(portfolio, positions)
    equity = _equity_from_portfolio(portfolio)

    # ── 净方向敞口 ──
    signed = 0.0
    for p in mids:
        d = _pos_dir(p.get("side"))
        n = _notional(p)
        if d == "long":
            signed += n
        elif d == "short":
            signed -= n
    add = abs(float(new_notional or 0))
    if direction == "long":
        signed_after = signed + add
    else:
        signed_after = signed - add

    if max_net_pct is not None:
        try:
            cap = float(max_net_pct)
        except (TypeError, ValueError):
            cap = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)
    elif is_probe:
        # 探针可单独更宽，避免首笔试探锁死全通道
        base = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)
        cap = _cfg_float("MIDLONG_NIBBLE_NET_EXPOSURE_PCT", max(base, 2.0))
    else:
        cap = _cfg_float("MIDLONG_MAX_NET_EXPOSURE_PCT", 1.5)

    if equity > 0 and abs(signed_after) / equity > cap:
        before_pct = abs(signed) / equity
        after_pct = abs(signed_after) / equity
        return (
            False,
            f"net_exposure {after_pct:.0%}>{cap:.0%} "
            f"(after {sym} {direction}; before={before_pct:.0%} est=${add:.0f})",
        )

    # ── 相关簇同向上限 ──
    cluster = set(_parse_cluster_symbols())
    if sym in cluster:
        same_dir = 0
        for p in mids:
            ps = str(p.get("symbol") or "").upper()
            if ps not in cluster:
                continue
            if _pos_dir(p.get("side")) == direction:
                same_dir += 1
        cap_n = _cfg_int("MIDLONG_CORR_CLUSTER_MAX", 2)
        if same_dir >= cap_n:
            return (
                False,
                f"corr_cluster {direction} count={same_dir}>={cap_n} "
                f"({','.join(sorted(cluster))})",
            )

    # ── 全局中长线并发上限 ──
    max_pos = _cfg_int("MIDLONG_MAX_OPEN_POSITIONS", 4)
    if max_pos > 0 and len(mids) >= max_pos:
        return False, f"midlong_open_positions {len(mids)}>={max_pos}"

    return True, "ok"


@dataclass
class NoProgressDecision:
    action: str = "hold"  # hold / close
    reason: str = ""
    peak_r: float = 0.0
    hold_hours: float = 0.0


def _held_hours(position: Dict[str, Any]) -> float:
    try:
        age = position.get("hold_age_hours")
        if age is not None and float(age) >= 0:
            return float(age)
    except Exception:
        pass
    for key in ("opened_at", "created_at", "entry_time", "open_time"):
        val = position.get(key)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return max(0.0, (time.time() - float(val)) / 3600.0)
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0,
            )
        except Exception:
            continue
    hs = position.get("hold_seconds")
    try:
        if hs is not None:
            return max(0.0, float(hs) / 3600.0)
    except Exception:
        pass
    return 0.0


def _pct_to_fraction(v: Any, *, assume_percent_points: bool = False) -> float:
    """兼容 DB 小数(0.03) 与 get_positions 导出百分数(3.0)。"""
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    if assume_percent_points or abs(x) > 1.0:
        return x / 100.0
    return x


def _r_multiple(position: Dict[str, Any]) -> Tuple[float, float]:
    """返回 (peak_R, current_R)；R 以入场到止损的价格距离为单位。"""
    try:
        entry = float(position.get("entry_price") or 0)
        sl = float(position.get("sl_price") or 0)
        side = _pos_dir(position.get("side"))
        if entry <= 0 or sl <= 0 or not side:
            risk = 0.03
        else:
            risk = abs(entry - sl) / entry
            if risk <= 1e-8:
                risk = 0.03

        # paper_engine._position_to_dict 把 peak 乘了 100；原始 ORM/单测用小数
        from_dict = any(
            k in position for k in ("hold_age_hours", "pnl_pct", "peak_unrealized_pnl")
        )
        peak_frac = _pct_to_fraction(
            position.get("peak_pnl_pct"), assume_percent_points=from_dict,
        )

        mark = float(position.get("mark_price") or position.get("current_price") or 0)
        if entry > 0 and mark > 0 and side:
            if side == "long":
                cur_frac = (mark - entry) / entry
            else:
                cur_frac = (entry - mark) / entry
        else:
            lev = float(position.get("leverage") or 1) or 1
            raw = position.get("unrealized_pnl_pct")
            if raw is None:
                raw = position.get("pnl_pct")
                cur_frac = _pct_to_fraction(raw, assume_percent_points=True) / lev
            else:
                cur_frac = _pct_to_fraction(raw, assume_percent_points=from_dict)

        return peak_frac / risk, cur_frac / risk
    except Exception:
        return 0.0, 0.0


def evaluate_no_progress_exit(position: Dict[str, Any]) -> NoProgressDecision:
    """持仓过久且峰值未达 0.5R → 主动离场。"""
    if not _cfg_bool("MIDLONG_NO_PROGRESS_EXIT_ENABLED", True):
        return NoProgressDecision()
    if not _is_midlong_pos(position):
        return NoProgressDecision()

    tier = str(position.get("timeframe_tier") or "").lower()
    nature = str(position.get("trade_nature") or "").lower()
    if tier == "long" or nature in ("trend_follow", "position"):
        max_h = _cfg_float("MIDLONG_NO_PROGRESS_HOURS_LONG", 72.0)
    else:
        max_h = _cfg_float("MIDLONG_NO_PROGRESS_HOURS_MID", 18.0)

    hold_h = _held_hours(position)
    if hold_h < max_h:
        return NoProgressDecision(hold_hours=hold_h)

    min_peak_r = _cfg_float("MIDLONG_NO_PROGRESS_MIN_PEAK_R", 0.5)
    peak_r, cur_r = _r_multiple(position)
    if peak_r >= min_peak_r:
        return NoProgressDecision(peak_r=peak_r, hold_hours=hold_h)

    return NoProgressDecision(
        action="close",
        reason=(
            f"[no_progress] hold={hold_h:.1f}h≥{max_h:.0f}h "
            f"peak_R={peak_r:.2f}<{min_peak_r:.2f} cur_R={cur_r:.2f}"
        ),
        peak_r=peak_r,
        hold_hours=hold_h,
    )


def parse_core_basket() -> List[str]:
    try:
        from backend.config import settings
        raw = getattr(settings, "MIDLONG_CORE_BASKET", "") or ""
    except Exception:
        raw = ""
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()]
