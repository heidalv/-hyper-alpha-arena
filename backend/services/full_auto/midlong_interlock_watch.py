# -*- coding: utf-8 -*-
"""[2026-09-18 根因修复·F] 中线「两闸互斥」可观测告警。

## 为什么需要（本次事故的教训）

2026-09-18 的事故是"**两个方向同时没有合法路径**"：日线 `up` ⇒ 空头被
`midlong_short_regime_block` 全拒；盘中 `ranging` + 全市场 24h 分位 80–98% ⇒
多头被追高天花板硬否决。结果是 mid 几乎无单可开，而**系统里没有任何一处会告诉你
"这是互锁"** —— 只能靠人肉把两边的日志凑起来看（本次就是这么查出来的）。

本模块提供一个**极轻量的互锁探测器**：谁被哪个方向拦了、什么时候拦的；
若同一标的在窗口内**两个方向都被拦过**，就产生一条**带节流**的告警文本，
由调用方落日志。它**不改变任何交易判定**（纯观察）。

口径：
- 判定方向用的分类规则与 `midlong_executor._dir_from_reason` **同源**（懒导入复用）；
- 拦不到方向（文本推断不出）的拦截**不计入**（保持"方向不可知"，绝不猜）；
- 窗口 `WINDOW_SEC`（默认 900s）内两侧都出现 ⇒ 互锁；
- 每标的 `ALERT_THROTTLE_SEC`（默认 1800s）最多告警一次，避免刷日志。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: symbol -> {"long": ts, "short": ts, "alerted_at": ts}
_STATE: Dict[str, Dict[str, float]] = {}
_STATE_MAX = 512


def _f(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)) or default)
    except (TypeError, ValueError):
        return default


def window_sec() -> float:
    return max(60.0, _f("MIDLONG_INTERLOCK_WINDOW_SEC", 900.0))


def throttle_sec() -> float:
    return max(0.0, _f("MIDLONG_INTERLOCK_ALERT_THROTTLE_SEC", 1800.0))


def alert_enabled() -> bool:
    return str(os.getenv("MIDLONG_INTERLOCK_ALERT", "true")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def classify_direction(reason: str) -> str:
    """从拦截原因推断方向（long/short/""）。与执行器同源；推断不出返回 ""。"""
    try:
        from backend.services.full_auto.midlong_executor import _dir_from_reason
        d = str(_dir_from_reason(reason) or "").strip().lower()
        if d in ("long", "short"):
            return d
    except Exception:  # noqa: BLE001 — 执行器不可导入时退回本地规则
        pass
    r = str(reason or "").lower()
    if "short_regime" in r or "空头" in r or "追空" in r:
        return "short"
    if "long_regime" in r or "多头" in r or "追多" in r:
        return "long"
    return ""


def note_block(symbol: str, reason: str, *, now: Optional[float] = None) -> Optional[str]:
    """登记一次拦截。返回**应当落日志的告警文本**（含节流判定），否则 None。

    纯观察：不改变任何交易判定，也不抛异常（调用方用 try/except 包着更好）。
    """
    if not alert_enabled():
        return None
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    d = classify_direction(reason)
    if not d:
        return None  # 方向不可知 ⇒ 不参与互锁判定（绝不猜）
    t = float(now if now is not None else time.time())
    if len(_STATE) >= _STATE_MAX and sym not in _STATE:
        _STATE.clear()
    st = _STATE.setdefault(sym, {})
    st[d] = t
    _win = window_sec()
    other = "short" if d == "long" else "long"
    t_other = float(st.get(other) or 0.0)
    if not t_other or (t - t_other) > _win:
        return None
    last_alert = float(st.get("alerted_at") or 0.0)
    if last_alert and (t - last_alert) < throttle_sec():
        return None
    st["alerted_at"] = t
    age = int(t - t_other)
    return (
        f"[MidLongInterlock] ⚠️ {sym} 两方向同时被否（互锁）："
        f"{other} 侧 {age}s 前被拦、{d} 侧刚被拦 —— "
        f"该标的在当前 regime/位置下**没有合法开仓方向**。"
        f"（原因：{str(reason)[:80]}）"
    )


def snapshot(*, now: Optional[float] = None) -> Dict[str, Any]:
    """当前互锁台账（供看板/自检；只读）。

    `now` 可注入 —— 否则测试只能用真实时钟，无法确定性地验证窗口/节流
    （我第一版就因此出现"告警已触发但台账说没互锁"的自相矛盾）。
    """
    _win = window_sec()
    _now = float(now if now is not None else time.time())
    out: Dict[str, Any] = {}
    for sym, st in _STATE.items():
        t_l, t_s = float(st.get("long") or 0.0), float(st.get("short") or 0.0)
        both = bool(t_l and t_s and abs(t_l - t_s) <= _win and (_now - min(t_l, t_s)) <= _win)
        out[sym] = {
            "long_blocked_age_s": (int(_now - t_l) if t_l else None),
            "short_blocked_age_s": (int(_now - t_s) if t_s else None),
            "interlocked": both,
        }
    return out


def interlocked_symbols(*, now: Optional[float] = None) -> Tuple[str, ...]:
    return tuple(sorted(k for k, v in snapshot(now=now).items() if v.get("interlocked")))


def reset() -> None:
    """清空台账（测试/人工复位用）。"""
    _STATE.clear()


def format_summary(*, now: Optional[float] = None) -> Optional[str]:
    """若存在互锁标的，返回一行汇总（供周期任务/自检打印），否则 None。"""
    syms = interlocked_symbols(now=now)
    if not syms:
        return None
    return (f"[MidLongInterlock] 当前 {len(syms)} 个标的两方向互锁："
            f"{', '.join(syms[:12])}{' …' if len(syms) > 12 else ''}")
