# -*- coding: utf-8 -*-
"""BarrierLadder — §5.4 最优障碍出场（P1 出场手术，2026-09-29 全面执行）。

设计依据（docs/底层数学模型设计_20260929.md §5.4/§5.6 + 出场反事实回放）：
  回放实测：k=1.0·ATR(1h) 止损 + 1R 锁本 + 2R 放利 + Chandelier 尾随 + τ=24h
  是最优区域（k 越紧越好、τ/r2 平坦参数），mid 车道专用；long 车道不套用。

规则（long 为例，short 镜像）：
  d   = clamp(k × ATR(1h)/entry, SL_MIN_PCT, SL_MAX_PCT)      # 波动率定价止损，废除地板让位
  SL  = entry×(1 − d)；TP1 = entry×(1 + 1.0·d)；TP2 = entry×(1 + 2.0·d)
  阶段0 → TP1 触发：平 50%，SL 移至 entry（锁本）
  阶段1 → TP2 触发：平 30%，余 20% 跟 Chandelier（峰值 − 2×ATR）
  阶段0 且 elapsed ≥ τ：时间止损（市价全平）
  同 bar 内 SL 优先（保守；与回放口径一致）

本模块纯函数 + 可序列化状态；无 DB / 无 LLM。状态持久化在
exit_state_json["barrier_ladder"]（跨重启稳定）。

开关：EXIT_POLICY_MID_BARRIER_LADDER（默认 false = 旧栈不变；.env 开启后生效）。
回滚：置 false 即恢复旧出场栈。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 模块装载时刻：晚于此时开仓的 mid 仓按 k×ATR 初始化止损；早于的存量仓沿用现有 SL。
IMPORT_TS = __import__("time").time()


def enabled() -> bool:
    try:
        return str(os.getenv("EXIT_POLICY_MID_BARRIER_LADDER", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return False


def _env_f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key) or default)
    except (TypeError, ValueError):
        return default


@dataclass
class BarrierSpec:
    k: float = 1.0                 # SL = k × ATR(1h)
    sl_min_pct: float = 0.8        # SL 距离下限（价格口径 %）
    sl_max_pct: float = 8.0        # SL 距离上限
    tp1_r: float = 1.0             # TP1 = tp1_r × d
    tp2_r: float = 2.0             # TP2 = tp2_r × d
    tp1_frac: float = 0.50         # TP1 平仓比例
    tp2_frac: float = 0.30         # TP2 平仓比例（余下跟 Chandelier）
    chand_c: float = 2.0           # Chandelier 乘数（ATR 单位）
    tau_h: float = 24.0            # 时间止损（小时，仅阶段0）

    @classmethod
    def from_env(cls) -> "BarrierSpec":
        return cls(
            k=_env_f("EXIT_POLICY_MID_BARRIER_K", 1.0),
            sl_min_pct=_env_f("EXIT_POLICY_MID_BARRIER_SL_MIN_PCT", 0.8),
            sl_max_pct=_env_f("EXIT_POLICY_MID_BARRIER_SL_MAX_PCT", 8.0),
            tp1_r=_env_f("EXIT_POLICY_MID_BARRIER_TP1_R", 1.0),
            tp2_r=_env_f("EXIT_POLICY_MID_BARRIER_TP2_R", 2.0),
            tp1_frac=_env_f("EXIT_POLICY_MID_BARRIER_TP1_FRAC", 0.50),
            tp2_frac=_env_f("EXIT_POLICY_MID_BARRIER_TP2_FRAC", 0.30),
            chand_c=_env_f("EXIT_POLICY_MID_BARRIER_CHAND_C", 2.0),
            tau_h=_env_f("EXIT_POLICY_MID_BARRIER_TAU_H", 24.0),
        )


@dataclass
class BarrierState:
    """每仓的障碍状态（可 JSON 序列化，存 exit_state_json["barrier_ladder"]）。"""

    stage: int = 0                 # 0 初始 / 1 已锁本 / 2 尾随
    qty: float = 1.0               # 剩余仓位比例
    sl: Optional[float] = None     # 当前止损价
    trail_peak: Optional[float] = None  # 阶段2 峰值（盈利方向）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "BarrierState":
        if not isinstance(d, dict):
            return cls()
        return cls(
            stage=int(d.get("stage") or 0),
            qty=float(d.get("qty") if d.get("qty") is not None else 1.0),
            sl=(float(d["sl"]) if d.get("sl") else None),
            trail_peak=(float(d["trail_peak"]) if d.get("trail_peak") else None),
        )


@dataclass
class BarrierFill:
    px: float
    frac: float
    kind: str                      # sl | tp1 | tp2 | chand | time


@dataclass
class BarrierStep:
    fills: List[BarrierFill]
    state: BarrierState
    closed: bool                   # 是否全平
    reason: str = ""

    @property
    def reduced(self) -> bool:
        return bool(self.fills) and not self.closed


def initial_sl(entry: float, atr: float, spec: BarrierSpec, side: str) -> float:
    """开仓/初始化时的止损价（价格口径 d = clamp(k×atr/entry)）。"""
    e = float(entry)
    if e <= 0 or atr <= 0:
        return 0.0
    d = min(max(spec.k * float(atr) / e, spec.sl_min_pct / 100.0), spec.sl_max_pct / 100.0)
    if side == "long":
        return round(e * (1.0 - d), 8)
    return round(e * (1.0 + d), 8)


def _crossed_stop(side: str, price: float, level: Optional[float]) -> bool:
    if not level or level <= 0:
        return False
    return price <= level if side == "long" else price >= level


def _crossed_target(side: str, price: float, level: Optional[float]) -> bool:
    if not level or level <= 0:
        return False
    return price >= level if side == "long" else price <= level


def step(
    spec: BarrierSpec,
    state: BarrierState,
    side: str,
    entry: float,
    atr: float,
    price: float,
    elapsed_sec: float,
) -> BarrierStep:
    """推进一根 tick（保守：SL 优先于 TP；与回放口径一致）。"""
    s = "long" if str(side).lower() in ("long", "buy") else "short"
    d = min(max(spec.k * float(atr) / float(entry), spec.sl_min_pct / 100.0),
            spec.sl_max_pct / 100.0) if entry and atr > 0 else 0.0
    st = state
    fills: List[BarrierFill] = []

    if st.stage == 2:
        # Chandelier 尾随
        peak = float(st.trail_peak or entry)
        peak = max(peak, price) if s == "long" else min(peak, price)
        st.trail_peak = peak
        trail = peak - spec.chand_c * float(atr) if s == "long" else peak + spec.chand_c * float(atr)
        if _crossed_stop(s, price, trail):
            frac = st.qty
            fills.append(BarrierFill(px=trail, frac=frac, kind="chand"))
            st.qty -= frac
            return BarrierStep(fills, st, True, "chand")
        return BarrierStep([], st, False, "hold")

    # 阶段0/1：SL 优先
    if _crossed_stop(s, price, st.sl):
        frac = st.qty
        px = float(st.sl)
        fills.append(BarrierFill(px=px, frac=frac, kind="sl"))
        st.qty -= frac
        return BarrierStep(fills, st, True, "sl")

    tp1 = entry * (1.0 + spec.tp1_r * d) if s == "long" else entry * (1.0 - spec.tp1_r * d)
    tp2 = entry * (1.0 + spec.tp2_r * d) if s == "long" else entry * (1.0 - spec.tp2_r * d)

    if st.stage == 0 and _crossed_target(s, price, tp1):
        frac = min(spec.tp1_frac, st.qty)
        fills.append(BarrierFill(px=tp1, frac=frac, kind="tp1"))
        st.qty -= frac
        st.stage = 1
        st.sl = round(float(entry), 8)      # 锁本
        return BarrierStep(fills, st, st.qty <= 1e-12, "tp1")

    if st.stage == 1 and _crossed_target(s, price, tp2):
        frac = min(spec.tp2_frac, st.qty)
        fills.append(BarrierFill(px=tp2, frac=frac, kind="tp2"))
        st.qty -= frac
        st.stage = 2
        st.trail_peak = price
        return BarrierStep(fills, st, st.qty <= 1e-12, "tp2")

    if st.stage == 0 and elapsed_sec >= spec.tau_h * 3600.0:
        frac = st.qty
        fills.append(BarrierFill(px=price, frac=frac, kind="time"))
        st.qty -= frac
        return BarrierStep(fills, st, True, "time")

    return BarrierStep([], st, False, "hold")


def state_from_exit_state(exit_state_json: Any) -> BarrierState:
    """从 exit_state_json 恢复；没有则返回初始状态。"""
    try:
        es = exit_state_json
        if isinstance(es, str):
            import json as _json
            es = _json.loads(es) if es else {}
        if isinstance(es, dict) and isinstance(es.get("barrier_ladder"), dict):
            return BarrierState.from_dict(es["barrier_ladder"])
    except Exception as exc:
        logger.debug("[BarrierLadder] 状态解析失败，用初始状态: %s", exc)
    return BarrierState()


def store_state(exit_state_json: Any, state: BarrierState) -> Any:
    """把障碍状态写回 exit_state_json（dict 形态；调用方负责序列化落库）。"""
    import json as _json
    try:
        es = exit_state_json
        if isinstance(es, str):
            es = _json.loads(es) if es else {}
        if not isinstance(es, dict):
            es = {}
        es["barrier_ladder"] = state.to_dict()
        return es
    except Exception:
        return exit_state_json
