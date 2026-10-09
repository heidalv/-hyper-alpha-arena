"""[2026-09-23 设计1] 长线出场**影子模式**（只记录，不改任何交易决策）。

依据：`docs/长线出场方向_实施设计_20260923.md` §1 —— V1=D1b（部分止盈 50%@+5%、25%@+8%，余仓追踪 3.0/1.5）、
V2=D2b（全仓追踪 3.0/1.5）在回测 E1 子样本上双源为正，但样本全落在趋势段 ⇒ 先攒"震荡段"证据再谈晋升。

挂点：`paper_trading_engine._run_v2_protection`（30s 慢 tick，`_sync_peak_state` 之后）与 `close_position` 落账处。
开关：`LONG_EXIT_SHADOW`（默认 false）。落盘：`data/long_exit_shadow.jsonl`（追加，幂等，只写不改）。
写盘策略：状态变化才写 + 每 900s 心跳一行，避免 5 仓×30s 刷屏。
口径：peak 用引擎已记录的 `pos.peak_pnl_pct`（30s 采样，与真实追踪同精度源），虚拟线只算不挂。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ENV = "LONG_EXIT_SHADOW"
_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "data", "long_exit_shadow.jsonl")
# pos_id -> {"v1_pt1": bool, "v1_pt2": bool, "last_sig": str, "last_write": ts, "last_peak": float}
_STATE: Dict[int, Dict[str, Any]] = {}


def enabled() -> bool:
    return (os.getenv(_ENV, "false") or "false").strip().lower() in ("1", "true", "yes", "on")


def _append(line: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_OUT), exist_ok=True)
        with open(_OUT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("[LongExitShadow] 写盘失败: %s", e)


def _sig(pos, current_price: float) -> str:
    """V1/V2 当前虚拟信号摘要（用于状态变化判定，纯字符串，不算价格）。"""
    entry = float(getattr(pos, "entry_price", 0) or 0)
    peak = float(getattr(pos, "peak_pnl_pct", 0) or 0) * 100.0
    st0 = _STATE.get(int(pos.id)) or {}
    v1 = []
    if peak >= 5.0 and not st0.get("v1_pt1"):
        v1.append("V1减半@5%")
    if peak >= 8.0 and not st0.get("v1_pt2"):
        v1.append("V1再减25%@8%")
    v2 = "V2追踪线=%.4f" % (entry * (1 + max(0.0, peak - 1.5) / 100.0)) if peak >= 3.0 else "V2未激活"
    v1t = "V1追踪线=%.4f" % (entry * (1 + max(0.0, peak - 1.5) / 100.0)) if peak >= 3.0 else "V1未激活"
    return "|".join([v2, v1t] + v1) + "|mark=%.6f|peak=%.3f" % (current_price, peak)


def shadow_tick(pos, current_price: float) -> None:
    """30s 慢 tick 调用；只记录。"""
    if not enabled():
        return
    try:
        tier = str(getattr(pos, "timeframe_tier", "") or "")
        if tier != "long":
            return
        st0 = _STATE.setdefault(int(pos.id), {"v1_pt1": False, "v1_pt2": False,
                                               "last_sig": "", "last_write": 0.0, "last_peak": -1.0})
        peak = float(getattr(pos, "peak_pnl_pct", 0) or 0) * 100.0
        if peak >= 5.0:
            st0["v1_pt1"] = True
        if peak >= 8.0:
            st0["v1_pt2"] = True
        sig = _sig(pos, current_price)
        now = time.time()
        peak_moved = abs(peak - float(st0.get("last_peak") or -1.0)) >= 0.05
        if sig != st0.get("last_sig") or peak_moved or (now - float(st0.get("last_write") or 0.0)) >= 900.0:
            st0["last_sig"], st0["last_write"], st0["last_peak"] = sig, now, peak
            _append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "tick",
                     "position_id": int(pos.id), "symbol": getattr(pos, "symbol", ""),
                     "side": getattr(pos, "side", ""), "entry": float(getattr(pos, "entry_price", 0) or 0),
                     "mark": float(current_price or 0), "peak_pct": round(peak, 3),
                     "v1_partial_fired": [x for x in ("50%@5%", "25%@8%") if st0["v1_pt1"] or st0["v1_pt2"]][:1]
                     if (st0["v1_pt1"] or st0["v1_pt2"]) else [],
                     "signal": sig})
    except Exception as e:  # noqa: BLE001 —— 影子必须 fail-open，绝不干扰交易
        logger.debug("[LongExitShadow] tick 异常: %s", e)


def shadow_close(pos) -> None:
    """close_position 落账后调用：记录实际出场 vs 虚拟状态对照。"""
    if not enabled():
        return
    try:
        if str(getattr(pos, "timeframe_tier", "") or "") != "long":
            return
        st0 = _STATE.pop(int(pos.id), None)
        peak = float(getattr(pos, "peak_pnl_pct", 0) or 0) * 100.0
        _append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "close",
                 "position_id": int(pos.id), "symbol": getattr(pos, "symbol", ""),
                 "side": getattr(pos, "side", ""), "entry": float(getattr(pos, "entry_price", 0) or 0),
                 "close_price": float(getattr(pos, "close_price", 0) or 0),
                 "close_reason": str(getattr(pos, "close_reason", "") or ""),
                 "peak_pct": round(peak, 3),
                 "virtual_at_close": st0.get("last_sig", "") if st0 else ""})
    except Exception as e:  # noqa: BLE001
        logger.debug("[LongExitShadow] close 异常: %s", e)
