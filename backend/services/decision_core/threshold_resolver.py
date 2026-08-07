"""
统一置信度门槛解析器 — 收敛此前散落 4 处叠加的置信度门。

历史问题：开仓置信度门槛在 4 个地方各自计算并叠加（基础 entry_threshold、
unified_gate V5 scalp/trend、_calibrate_confidence、position_memory_manager
min_conf），互相不知道对方，导致「到底哪道门把这单拦下了」无法解释，且各处
只会单向收紧。

本解析器把「取最严门槛 + 记录生效门 + 乘 MaturityController 松紧系数」统一到
一个函数 resolve_effective_entry_threshold()，所有门控调用它，输出可解释的
EffectiveThreshold，含：
  - effective：最终有效门槛（已扣除静态放宽与成熟度松紧）
  - governing_gate：是哪一道门产生了最严基准（base+regime / scalp / trend）
  - maturity_stage / maturity_relief：成熟度阶段与本次松紧系数（正=放宽）

口径：所有门槛、置信度统一用 0~100 百分制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def normalize_confidence_pct(raw: Any) -> float:
    """统一为 0~100 百分制。

    - 0 < v < 1：视为小数分数（0.01→1%, 0.45→45%）
    - v >= 1：视为百分制整数（1→1%, 45→45%, 100→100%）

    修复旧逻辑 ``v <= 1 则 ×100`` 把 1% 误判为 100% 的问题。
    """
    try:
        c = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    if c < 0:
        return 0.0
    if 0 < c < 1.0:
        return c * 100.0
    return min(c, 100.0)


@dataclass
class EffectiveThreshold:
    effective: float
    governing_gate: str
    components: Dict[str, float] = field(default_factory=dict)
    static_relief: float = 0.0
    maturity_relief: float = 0.0
    maturity_stage: str = "mature"
    maturity_driver: str = "none"
    floor: float = 40.0

    def explain(self) -> str:
        penalty = float(self.components.get("auto_coin_penalty", 0) or 0)
        return (
            f"有效门槛 {self.effective:.0f}%"
            f"(生效门={self.governing_gate}"
            f" 成熟度={self.maturity_stage} 松紧{self.maturity_relief:+.0f}"
            f"{f' 静态放宽-{self.static_relief:.0f}' if self.static_relief else ''}"
            f"{f' 选币加严+{penalty:.0f}' if penalty else ''})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effective": round(self.effective, 1),
            "governing_gate": self.governing_gate,
            "components": self.components,
            "static_relief": self.static_relief,
            "maturity_relief": self.maturity_relief,
            "maturity_stage": self.maturity_stage,
            "maturity_driver": self.maturity_driver,
            "floor": self.floor,
        }


def resolve_effective_entry_threshold(
    *,
    base_threshold: float,
    regime_adjust: float = 0.0,
    nature: str = "swing",
    tier: str = "",
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    scalp_gate: Optional[float] = None,
    trend_gate: Optional[float] = None,
    is_auto_coin: bool = False,
    high_conviction: bool = False,
    auto_relief: float = 0.0,
    auto_penalty: float = 0.0,
    high_relief: float = 0.0,
    mode: str = "paper",
    floor: float = 40.0,
) -> EffectiveThreshold:
    """收敛多道置信度门为单一有效门槛。

    1. 取最严基准：max(base+regime, scalp_gate?, trend_gate?)，记录生效门。
    2. 静态调整：AI 自动选币加严（+penalty）；高置信非选币币可放宽（-relief）。
    3. 扣成熟度松紧：MaturityController.resolve_relief（warmup 放宽，
       mature 期按胜率上下浮动；live 强制 0）。
    4. 不破下限 floor。
    """
    nature_l = (nature or "swing").lower()
    comps: Dict[str, float] = {"base": float(base_threshold), "regime": float(regime_adjust)}
    strict = float(base_threshold) + float(regime_adjust)
    governing = "base+regime"

    if nature_l == "scalp" and scalp_gate is not None:
        comps["scalp"] = float(scalp_gate)
        if float(scalp_gate) > strict:
            strict = float(scalp_gate)
            governing = "scalp"
    if nature_l in ("trend_follow", "position") and trend_gate is not None:
        comps["trend"] = float(trend_gate)
        if float(trend_gate) > strict:
            strict = float(trend_gate)
            governing = "trend"

    auto_penalty_applied = float(auto_penalty) if is_auto_coin else 0.0
    auto_relief_applied = float(auto_relief) if is_auto_coin else 0.0
    high_relief_applied = float(high_relief) if high_conviction and not is_auto_coin else 0.0
    static_relief = auto_relief_applied + high_relief_applied

    maturity_relief = 0.0
    maturity_stage = "mature"
    maturity_driver = "none"
    try:
        from backend.services.maturity_controller import resolve_relief

        mat = resolve_relief(
            symbol=symbol, side=side, nature=nature_l, tier=tier, mode=mode,
        )
        maturity_relief = float(mat.get("relief", 0.0))
        maturity_stage = str(mat.get("stage", "mature"))
        maturity_driver = str(mat.get("driver", "none"))
    except Exception as exc:
        logger.debug("[ThresholdResolver] 成熟度松紧读取失败: %s", exc)

    # Paper 中线/长线探索期：禁止成熟度负向收紧（与 Phase0 攒样本目标一致）
    if (mode or "").lower() == "paper" and nature_l in ("swing", "trend_follow", "position"):
        if maturity_relief < 0:
            maturity_relief = 0.0

    if auto_penalty_applied:
        comps["auto_coin_penalty"] = auto_penalty_applied

    effective = strict + auto_penalty_applied - static_relief - maturity_relief
    effective = max(float(floor), effective)

    return EffectiveThreshold(
        effective=effective,
        governing_gate=governing,
        components=comps,
        static_relief=static_relief,
        maturity_relief=maturity_relief,
        maturity_stage=maturity_stage,
        maturity_driver=maturity_driver,
        floor=float(floor),
    )
