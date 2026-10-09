# -*- coding: utf-8 -*-
"""[h624] 成交后 markout 与盈亏平衡门禁（纯函数，无 IO）。

设计：`研究结论/高频做市全面升级设计_h624_20260929.md` §4.1–4.2。

markout_bp(τ) = side_sign × (mid(t+τ) − fill_px) / fill_px × 1e4
  买：价格涨为正；卖：价格跌为正。逆选择 ⇒ 为负。

门禁：滚动 E[markout] + E[capture] < halt_bp ⇒ 停加仓（减仓豁免）。
盈亏平衡：expected_capture < break_even ⇒ 停加仓。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def markout_bp(*, side: str, fill_px: float, mid_later: float) -> float:
    """单笔成交相对后续中价的 markout（bp）。非法输入 → 0。"""
    px = float(fill_px or 0.0)
    mid = float(mid_later or 0.0)
    if px <= 0 or mid <= 0:
        return 0.0
    side_l = str(side or "").lower()
    is_buy = side_l in ("buy", "long", "b")
    raw = (mid - px) / px * 1e4
    return float(raw if is_buy else -raw)


def rolling_markout_stats(
    samples: Sequence[Dict[str, float]],
    *,
    min_n: int = 10,
) -> Dict[str, float]:
    """对样本算名义加权 markout 与捕获。

    每条样本：`{"markout_bp", "capture_bp", "notional"}`。
    样本不足 ⇒ n=0、均值 0（调用方不得触发门禁）。
    """
    rows = [s for s in samples if float(s.get("notional") or 0.0) > 0]
    if len(rows) < max(1, int(min_n or 0)):
        return {"n": float(len(rows)), "markout_bp": 0.0, "capture_bp": 0.0,
                "notional": 0.0}
    w = sum(float(s["notional"]) for s in rows)
    if w <= 0:
        return {"n": float(len(rows)), "markout_bp": 0.0, "capture_bp": 0.0,
                "notional": 0.0}
    m = sum(float(s.get("markout_bp") or 0.0) * float(s["notional"]) for s in rows) / w
    c = sum(float(s.get("capture_bp") or 0.0) * float(s["notional"]) for s in rows) / w
    return {"n": float(len(rows)), "markout_bp": float(m), "capture_bp": float(c),
            "notional": float(w)}


def markout_halts_adds(
    *,
    markout_bp: float,
    capture_bp: float,
    n: float,
    min_n: int,
    halt_thresh_bp: float,
) -> bool:
    """m + capture < thresh ⇒ 停加仓。thresh 语义：启用时常用 0（和为负就停）。

    `halt_thresh_bp` 为 None 或哨兵：调用方用「字段未启用」时不要调本函数。
    这里：min_n 不足 ⇒ False；否则 markout+capture < halt_thresh_bp。
    """
    if int(n or 0) < max(1, int(min_n or 0)):
        return False
    return (float(markout_bp) + float(capture_bp)) < float(halt_thresh_bp)


def break_even_bp(
    *,
    rolling_markout_bp: float,
    stop_share: float,
    taker_fee_bp: float = 4.0,
) -> float:
    """简化盈亏平衡：逆选择下界 + 止损吃单期望费。"""
    adv = max(0.0, -float(rolling_markout_bp or 0.0))
    share = min(max(0.0, float(stop_share or 0.0)), 0.15)
    return adv + float(taker_fee_bp) * share


def break_even_blocks_add(
    *,
    expected_capture_bp: float,
    be_bp: float,
    be_mult: float,
) -> bool:
    """捕获 < 平衡×倍数 ⇒ 停加仓。be_mult<=0 ⇒ 关闭。"""
    if float(be_mult or 0.0) <= 0:
        return False
    return float(expected_capture_bp) < float(be_bp) * float(be_mult)


def jump_pause_hit(
    mid_hist: Sequence[float],
    *,
    thresh_bp: float,
    lookback: int = 1,
) -> bool:
    """近 lookback 期净移动绝对值 ≥ 阈值 ⇒ 跳变。thresh<=0 ⇒ False。"""
    if float(thresh_bp or 0.0) <= 0:
        return False
    h = list(mid_hist or [])
    k = max(1, int(lookback or 1))
    if len(h) < k + 1:
        return False
    a, b = float(h[-1 - k]), float(h[-1])
    if a <= 0 or b <= 0:
        return False
    return abs(b - a) / a * 1e4 >= float(thresh_bp)
