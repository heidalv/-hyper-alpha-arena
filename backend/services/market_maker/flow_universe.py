# -*- coding: utf-8 -*-
"""主动流的两层名单。做市的价差门槛、反转槽和形态矩阵不在这里。

观察池：有成交额、买一卖一都在、整段价差窄于止损的一半。
交易名单：观察池里样本外挂单盈亏为正、独立样本够 30 的币，最多 4 个，可以留空。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.market_maker.flow_rules import STOP_CAP_BP, bucket_tradable

POOL_CAP = 80
SLOT_CAP = 4
DWELL_SEC = 2 * 3600
TRIAL_JITTER_SEC = 4 * 3600
CLEAR_EDGE_BP = 1.0
FILL_RATE_MIN = 0.05
EXCLUDE_BARE = {"BTCDOM", "DEFI", "1000BONK"}
EXCLUDE_SUFFIX = ("USD1", "USDC", "BUSD", "DAI", "FDUSD", "TUSD")


def _bare(symbol: str) -> str:
    raw = str(symbol or "").upper()
    if raw.endswith("USDT"):
        raw = raw[:-4]
    return raw


def excluded_symbol(symbol: str) -> bool:
    bare = _bare(symbol)
    return bare in EXCLUDE_BARE or bare.endswith(EXCLUDE_SUFFIX)


def spread_blocks_pool(spread_bp: float, stop_bp: float = STOP_CAP_BP) -> bool:
    """整段价差达到止损的一半，止损落在价差里面，不进观察池。"""
    if float(spread_bp or 0.0) <= 0.0:
        return True
    return float(spread_bp) >= float(stop_bp) / 2.0


def in_watch_pool(row: Dict[str, Any], stop_bp: float = STOP_CAP_BP) -> bool:
    """有人成交、盘口完整、价差窄于止损的一半。"""
    if excluded_symbol(str(row.get("symbol") or "")):
        return False
    try:
        volume = float(row.get("quote_volume_usd") or 0.0)
    except (TypeError, ValueError):
        return False
    if volume <= 0.0:
        return False
    if spread_blocks_pool(float(row.get("spread_bp") or 0.0), stop_bp):
        return False
    if row.get("trades_15m") is not None:
        tape = int(row.get("trades_15m") or 0)
    else:
        tape = int(row.get("trades_24h") or 0)
    return tape >= 1


def ranked_watch_pool(rows: Sequence[Dict[str, Any]], stop_bp: float = STOP_CAP_BP,
                      cap: int = POOL_CAP) -> List[str]:
    """成交额从高到低，最多 cap 个。价差宽不是加分项。"""
    ok = [r for r in rows if in_watch_pool(r, stop_bp)]
    ok.sort(key=lambda r: -float(r.get("quote_volume_usd") or 0.0))
    out: List[str] = []
    seen = set()
    for row in ok:
        sym = _bare(str(row.get("symbol") or ""))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= int(cap):
            break
    return out


def _oos(gate: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    oos = (gate or {}).get("oos") or {}
    try:
        mean_y = float(oos.get("mean_y"))
    except (TypeError, ValueError):
        mean_y = None
    try:
        n_eff = float(oos.get("n_eff"))
    except (TypeError, ValueError):
        n_eff = None
    fill_rate = oos.get("fill_rate")
    try:
        fill_rate = None if fill_rate is None else float(fill_rate)
    except (TypeError, ValueError):
        fill_rate = None
    return mean_y, n_eff, fill_rate


def membership(symbol: str, gate: Optional[Dict[str, Any]], now_ts: float,
               admitted_at: Optional[Dict[str, float]]) -> str:
    """返回 eligible / keep / watch / drop。

    样本外盈亏小于等于 0、近端盈亏小于等于 0、或出现强平：立刻 drop。
    成交率抖动且入池不足 4 小时、样本外仍为正：keep，不因抖动踢出。
    """
    mean_y, n_eff, fill_rate = _oos(gate)
    if int((gate or {}).get("liquidations") or 0) > 0:
        return "drop"
    recent = (gate or {}).get("recent_mean_y")
    try:
        if recent is not None and float(recent) <= 0.0:
            return "drop"
    except (TypeError, ValueError):
        pass
    if mean_y is None or (n_eff is not None and n_eff < 1.0):
        return "watch"
    # 考卷写了「不过」，即使平均被少数大赚拉成正数，也不给位子。
    if (gate or {}).get("allow") is False:
        return "drop"
    if mean_y <= 0.0:
        return "drop"
    if not bucket_tradable(mean_y, n_eff):
        return "watch"
    age = float(now_ts) - float((admitted_at or {}).get(symbol) or 0.0)
    if fill_rate is not None and fill_rate < FILL_RATE_MIN:
        if 0.0 < age < TRIAL_JITTER_SEC:
            return "keep"
        return "watch"
    return "eligible"


def select_trading_slots(
    pool: Sequence[str],
    gates: Optional[Dict[str, Any]],
    current: Sequence[str],
    admitted_at: Optional[Dict[str, float]],
    now_ts: float,
    slot_cap: int = SLOT_CAP,
) -> Tuple[List[str], List[str]]:
    """返回 (交易名单, 本轮新进)。不够格的位子留空。不做反转/动量分槽。"""
    gates = gates or {}
    admitted_at = admitted_at or {}
    pool_set = {_bare(s) for s in pool}

    def edge(sym: str) -> float:
        mean_y, _, _ = _oos(gates.get(sym) or {})
        return -1e9 if mean_y is None else mean_y

    kept: List[str] = []
    for raw in current:
        sym = _bare(raw)
        state = membership(sym, gates.get(sym), now_ts, admitted_at)
        if state == "drop":
            continue
        if sym not in pool_set:
            # 观察池这一轮没覆盖到、但也没有负证据：先留着，避免把正在跑的名单清空。
            if state in ("watch", "keep", "eligible"):
                kept.append(sym)
            continue
        if state in ("eligible", "keep", "watch"):
            kept.append(sym)
    kept.sort(key=edge, reverse=True)
    kept = kept[: int(slot_cap)]

    challengers = []
    for raw in pool:
        sym = _bare(raw)
        if membership(sym, gates.get(sym), now_ts, admitted_at) == "eligible":
            challengers.append(sym)
    challengers.sort(key=edge, reverse=True)

    new_in: List[str] = []
    for sym in challengers:
        if sym in kept:
            continue
        if len(kept) < int(slot_cap):
            kept.append(sym)
            new_in.append(sym)
            continue
        victim = min(kept, key=edge)
        victim_age = float(now_ts) - float(admitted_at.get(victim) or 0.0)
        victim_state = membership(victim, gates.get(victim), now_ts, admitted_at)
        if victim_state != "watch" and victim_age < DWELL_SEC:
            continue
        if edge(sym) < edge(victim) + CLEAR_EDGE_BP:
            continue
        kept.remove(victim)
        kept.append(sym)
        new_in.append(sym)
    kept.sort(key=edge, reverse=True)
    return kept, new_in


def exit_only_symbols(trading: Sequence[str], positions: Dict[str, float]) -> List[str]:
    """不在交易名单里、但仍有仓的币。它们只走出场，不再开新仓。"""
    names = {_bare(s) for s in trading}
    out = []
    for sym, qty in (positions or {}).items():
        bare = _bare(sym)
        if bare in names:
            continue
        try:
            if abs(float(qty)) > 1e-12:
                out.append(bare)
        except (TypeError, ValueError):
            continue
    return out


# ══════════════════════════════════════════════════════════════════════
# [2026-10-09 重复来回做市] PP 口径选币：按 ping-pong 往返账 rt_bp 判留去，
# 不再按方向模型的 mean_y/n_eff。判据与记分板裁决同源：
#   样本够 + 胜率跌破打平线 或 赚的幅度 ≤ 亏的幅度 ⇒ drop；
#   样本够且两关都过 ⇒ eligible；样本不足 ⇒ 冷启动照跑攒数（watch/keep）。
# ══════════════════════════════════════════════════════════════════════
PP_MIN_N = 20.0
PP_EDGE_MIN_BP = 1.0   # 换位需要的新币领先幅度（rt_bp 口径）


def pp_breakeven(avg_win_bp: float, avg_loss_bp: float) -> float:
    w = float(avg_win_bp or 0.0)
    l = abs(float(avg_loss_bp or 0.0))
    if w <= 0 or (w + l) <= 0:
        return 1.0
    return l / (w + l)


def pp_membership(symbol: str, stats: Optional[Dict[str, Any]], now_ts: float,
                  admitted_at: Optional[Dict[str, float]],
                  min_n: float = PP_MIN_N) -> str:
    """返回 eligible / keep / watch / drop（rt_bp 口径）。"""
    del symbol, now_ts, admitted_at
    if not isinstance(stats, dict):
        return "watch"
    try:
        n = float(stats.get("n") or 0.0)
    except (TypeError, ValueError):
        return "watch"
    if n + 1e-9 < float(min_n):
        # 样本不足：冷启动照跑攒数（watch 仍占位，有仓只减不加照常）
        return "watch"
    try:
        wr = float(stats.get("win_rate") or 0.0)
        aw = float(stats.get("avg_win_bp") or 0.0)
        al = float(stats.get("avg_loss_bp") or 0.0)
    except (TypeError, ValueError):
        return "watch"
    if wr + 1e-9 < pp_breakeven(aw, al) or aw <= abs(al):
        return "drop"
    return "eligible"


def select_pp_slots(
    pool: Sequence[str],
    pp_stats: Optional[Dict[str, Any]],
    current: Sequence[str],
    admitted_at: Optional[Dict[str, float]],
    now_ts: float,
    slot_cap: int = SLOT_CAP,
) -> Tuple[List[str], List[str]]:
    """按 PP 往返账选交易位：不够格的位子留空。与 select_trading_slots 同骨架，
    只是把证据换成 rt_bp 统计。"""
    coins = ((pp_stats or {}).get("coins") or {}) if isinstance(pp_stats, dict) else {}
    admitted_at = admitted_at or {}
    pool_set = {_bare(s) for s in pool}

    def stats_of(sym: str) -> Optional[Dict[str, Any]]:
        s = coins.get(sym)
        return s if isinstance(s, dict) else None

    def edge(sym: str) -> float:
        s = stats_of(sym)
        try:
            return float(s.get("avg_bp") or 0.0) if s else -1e9
        except (TypeError, ValueError):
            return -1e9

    kept: List[str] = []
    for raw in current:
        sym = _bare(raw)
        state = pp_membership(sym, stats_of(sym), now_ts, admitted_at)
        if state == "drop":
            continue
        if sym not in pool_set:
            if state in ("watch", "keep", "eligible"):
                kept.append(sym)
            continue
        if state in ("eligible", "keep", "watch"):
            kept.append(sym)
    kept.sort(key=edge, reverse=True)
    kept = kept[: int(slot_cap)]

    challengers = []
    for raw in pool:
        sym = _bare(raw)
        if pp_membership(sym, stats_of(sym), now_ts, admitted_at) == "eligible":
            challengers.append(sym)
    challengers.sort(key=edge, reverse=True)

    new_in: List[str] = []
    for sym in challengers:
        if sym in kept:
            continue
        if len(kept) < int(slot_cap):
            kept.append(sym)
            new_in.append(sym)
            continue
        victim = min(kept, key=edge)
        victim_age = float(now_ts) - float(admitted_at.get(victim) or 0.0)
        victim_state = pp_membership(victim, stats_of(victim), now_ts, admitted_at)
        if victim_state != "watch" and victim_age < DWELL_SEC:
            continue
        if edge(sym) < edge(victim) + PP_EDGE_MIN_BP:
            continue
        kept.remove(victim)
        kept.append(sym)
        new_in.append(sym)
    kept.sort(key=edge, reverse=True)
    return kept, new_in


def touch_admitted(admitted_at: Optional[Dict[str, float]], trading: Sequence[str],
                   now_ts: float) -> Dict[str, float]:
    """新进的币记下入池时间。已经不在交易名单里的时间戳删掉。"""
    out = { _bare(k): float(v) for k, v in (admitted_at or {}).items() }
    keep = {_bare(s) for s in trading}
    for sym in list(out):
        if sym not in keep:
            out.pop(sym, None)
    for sym in keep:
        out.setdefault(sym, float(now_ts))
    return out
