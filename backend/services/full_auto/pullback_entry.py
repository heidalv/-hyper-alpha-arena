# -*- coding: utf-8 -*-
"""[调研轮19 2026-09-17] **回踩入场**：同一批信号，等一个小回撤再成交（不加过滤、不丢单）。

## 数据依据（近 7 天 35 笔开仓，15m K 线，`audit_entry_execution` 口径）

   入场后 1h 内回踩深度：均值 +1.11%、中位 +0.88%、**80% 的单出现过回踩**
   若挂 entry×(1−x) 限价：x=0.3% → **74% 成交、平均改善 0.30%**；x=0.5% → 69%、0.50%
   入场后前 4h 路径：**MFE 均值 +0.24% vs MAE 均值 −1.68%**（31% 深于 2%）

⇒ 系统在"买局部高点"：市价成交后立刻逆行。等一个小回撤再成交，
**不减少任何一笔交易**（超时即市价兜底），只把成交价改善 0.3%~0.5%，
同时把 MAE/止损压力同比降低。

## 语义（关键：这不是门禁）

* 首次看到某 (会话, 币, 层, 方向) 的入场信号 → 登记 pending（目标价 = 信号价 ∓ pct，
  截止 = now + timeout），本轮**不成交但不丢弃**；
* 之后每 tick 复核：`价格达到目标` 或 `超过截止` ⇒ 返回"不等待"，
  成交路径照原样执行（**超时=市价兜底，交易照做**）；
* 只对**同一 key** 生效，互不影响；异常一律 fail-open（不等待 = 旧行为）。

## 回滚

`MIDLONG_PULLBACK_ENTRY_ENABLED=false`（默认 true）；`MIDLONG_PULLBACK_ENTRY_PCT=0`
等价于关闭。超时 `MIDLONG_PULLBACK_ENTRY_TIMEOUT_S`（默认 1800s）。

## 已知边界（写清楚，避免误解）

若信号在超时窗口内**再也没出现**（该 symbol 不再进扫描/论题失效），则这笔不会成交 ——
这与"信号本身消失"同义，不属于被本机制拦下；真正的兜底是超时后的下一次信号。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: key -> {"target": float, "deadline": float, "side": str, "created": float, "waited": int}
_PENDING: Dict[str, Dict[str, float]] = {}


def _cfg_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw == "":
        return bool(default)
    if raw in ("1", "true", "yes", "on", "y", "t"):
        return True
    if raw in ("0", "false", "no", "off", "n", "f"):
        return False
    logger.warning("[PullbackEntry] %s=%r 无法识别，按默认 %s", name, raw, default)
    return bool(default)


def pullback_config() -> Dict[str, float]:
    """读配置（env 为准，默认值即生产口径）。"""
    try:
        pct = float(os.getenv("MIDLONG_PULLBACK_ENTRY_PCT", "0.003") or 0.003)
    except (TypeError, ValueError):
        pct = 0.003
    try:
        ttl = float(os.getenv("MIDLONG_PULLBACK_ENTRY_TIMEOUT_S", "1800") or 1800)
    except (TypeError, ValueError):
        ttl = 1800.0
    return {
        "enabled": _cfg_bool("MIDLONG_PULLBACK_ENTRY_ENABLED", True),
        "pct": max(0.0, pct),
        "timeout_s": max(0.0, ttl),
    }


def evaluate(
    *,
    key: str,
    side: str,
    price: float,
    now: Optional[float] = None,
    enabled: Optional[bool] = None,
    pct: Optional[float] = None,
    timeout_s: Optional[float] = None,
) -> Tuple[bool, str]:
    """返回 `(是否等待, 原因)`。纯逻辑，便于契约测试。

    * 返回 True ⇒ 本轮不成交（登记/继续等待），**信号保留**；
    * 返回 False ⇒ 走原成交路径（市价）。
    """
    cfg = pullback_config()
    _on = cfg["enabled"] if enabled is None else bool(enabled)
    _pct = cfg["pct"] if pct is None else float(pct)
    _ttl = cfg["timeout_s"] if timeout_s is None else float(timeout_s)
    if not _on or _pct <= 0 or _ttl <= 0:
        _PENDING.pop(key, None)
        return False, "pullback_off"
    try:
        px = float(price or 0)
    except (TypeError, ValueError):
        return False, "bad_price"
    if px <= 0:
        return False, "bad_price"
    _now = float(now if now is not None else time.time())
    _side = str(side or "").lower()
    is_long = _side in ("buy", "long")

    st = _PENDING.get(key)
    if st is None:
        target = px * (1.0 - _pct) if is_long else px * (1.0 + _pct)
        _PENDING[key] = {
            "target": target, "deadline": _now + _ttl, "created": _now,
            "signal_px": px, "side": 1.0 if is_long else -1.0, "waited": 0,
        }
        return True, f"登记回踩 target={target:.6f}（信号价 {px:.6f}，−{_pct*100:.2f}%）"

    st["waited"] = float(st.get("waited", 0)) + 1
    target = float(st["target"])
    hit = (px <= target) if is_long else (px >= target)
    if hit:
        _PENDING.pop(key, None)
        return False, f"回踩到位 {px:.6f}（目标 {target:.6f}）⇒ 成交"
    if _now >= float(st["deadline"]):
        _PENDING.pop(key, None)
        return False, f"回踩超时（等待 {_now - float(st['created']):.0f}s）⇒ 市价兜底"
    return True, (f"等待回踩 目标{target:.6f} 现价{px:.6f} "
                  f"剩余{float(st['deadline']) - _now:.0f}s")


def pending_snapshot() -> Dict[str, Dict[str, float]]:
    """当前挂起表（只读，供诊断/测试）。"""
    return {k: dict(v) for k, v in _PENDING.items()}


def reset() -> None:
    """清空挂起表（测试/紧急复位用）。"""
    _PENDING.clear()
