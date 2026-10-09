# -*- coding: utf-8 -*-
"""[2026-10-09 重复来回做市] 桶级状态门：按「价差 × 前档量」给进场侧放行/休息。

与旧情境表的本质区别：旧表分桶后问「这个桶该买还是该卖」（方向口径），
新机器不猜方向，只问「**这种盘口状态下，这一侧该不该挂**」（做/歇口径）。
判据是 ping-pong 往返账的 rt_bp：桶内 n 足够且
`胜率 < 打平线` 或 `赚的幅度 ≤ 亏的幅度` ⇒ 该桶为负 ⇒ 该侧这一拍休息。

fail-open：文件缺失/过期/桶样本不足 ⇒ 一律照挂（攒样本），绝不因为
学习系统缺数据把车道停掉。回滚：`MM_PP_BUCKET_GATE=0`（调用方读开关）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend.services.market_maker.flow_rules import (
    AHEAD_EDGES_USD,
    SPREAD_EDGES_BP,
)

# 情形表新鲜度上限（秒）。超过视为过期 ⇒ fail-open。
MAX_AGE_SEC = 6 * 3600.0


def bucket_key(spread_bp: float, ahead_usd: float) -> str:
    """(价差档, 前档量档) → "s{a}_a{a}" 字符串键。"""
    s = 0
    for edge in SPREAD_EDGES_BP:
        if float(spread_bp or 0.0) < float(edge):
            break
        s += 1
    a = 0
    for edge in AHEAD_EDGES_USD:
        if float(ahead_usd or 0.0) < float(edge):
            break
        a += 1
    return f"s{s}_a{a}"


def breakeven_win_rate(avg_win_bp: float, avg_loss_bp: float) -> float:
    """打平线胜率 = |亏| / (赚 + |亏|)。赚 ≤ 亏 ⇒ 打平线 ≥ 0.5 起跳。"""
    w = float(avg_win_bp or 0.0)
    l = abs(float(avg_loss_bp or 0.0))
    if w <= 0 or (w + l) <= 0:
        return 1.0
    return l / (w + l)


def bucket_negative(row: Optional[Dict[str, Any]], min_n: float = 20.0) -> bool:
    """单桶证据判定：样本够且该桶为负 ⇒ True（该侧休息）。"""
    if not isinstance(row, dict):
        return False
    try:
        n = float(row.get("n") or 0.0)
    except (TypeError, ValueError):
        return False
    if n + 1e-9 < float(min_n or 0.0):
        return False
    try:
        win_rate = float(row.get("win_rate") or 0.0)
        avg_win = float(row.get("avg_win_bp") or 0.0)
        avg_loss = float(row.get("avg_loss_bp") or 0.0)
    except (TypeError, ValueError):
        return False
    if avg_win <= 0 and abs(avg_loss) <= 0:
        return False
    if win_rate + 1e-9 < breakeven_win_rate(avg_win, avg_loss):
        return True
    return avg_win <= abs(avg_loss)


def load_doc(root) -> Optional[Dict[str, Any]]:
    try:
        p = root / "data" / "pp_situation_last.json"
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def side_blocked(doc: Optional[Dict[str, Any]], symbol: str, now_ts: float,
                 spread_bp: float, ahead_usd: float, side: str,
                 min_n: float = 20.0, max_age: float = MAX_AGE_SEC) -> bool:
    """该侧这一拍是否休息（桶证据为负）。缺文件/过期/样本不足 ⇒ False。"""
    if not isinstance(doc, dict):
        return False
    try:
        if (float(now_ts or 0.0) - float(doc.get("ts") or 0.0)) >= float(max_age):
            return False
    except (TypeError, ValueError):
        return False
    coin = (doc.get("coins") or {}).get(str(symbol or "").upper())
    if not isinstance(coin, dict):
        return False
    rows = coin.get(str(side or ""))
    if not isinstance(rows, dict):
        return False
    key = bucket_key(spread_bp, ahead_usd)
    return bucket_negative(rows.get(key), min_n=min_n)
