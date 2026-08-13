"""ThesisAgent-style deterministic Decision Hub."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np

from backend.services.mlto.types import HubDecision, Signal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# [阶段3a] AI-liberation：LLM 权重 0.04→0.30（生产目标）。
# 灰度发布：默认 0.30；运维可通过环境变量从 0.20 起步灰度调升。
# 例如 `export MLTO_LLM_WEIGHT_LONG=0.20` 即可降低 LLM 在长线权重表中
# 的占比，无需改代码。生产目标 = 0.30。
# ─────────────────────────────────────────────────────────────────────
_LLM_WEIGHT_LONG = float(os.getenv("MLTO_LLM_WEIGHT_LONG", "0.30"))
_LLM_WEIGHT_MID = float(os.getenv("MLTO_LLM_WEIGHT_MID", "0.30"))

# ─────────────────────────────────────────────────────────────────────
# [2026-08-05 v6 计划 6.3 第1/2项] ai_governed 独立模式（AI 提案 + 安全网否决）
# 区别于 MIDLONG_AI_MANDATORY(ai_first)：独立 env 开关，默认关，可一键回滚。
#   MLTO_AI_GOVERNED=1           开启
#   MLTO_AI_GOVERNED_WEIGHT=0.40 灰度权重阶梯 0.40 → 0.60 → 1.0
#     （0.40 档可先于 confidence 校准启动；0.60 档前必须完成校准器拟合）
# 模式下：
#   - composite 主由 llm_qual 决定（权重=灰度档位，最低 0.40）
#   - framework 仅作参照偏移（权重×0.4，只影响 NIBBLE/BUILD 档位、不改变方向）
#   - direction = llm_qual 单调映射（≥0.55 多 / ≤0.45 空）
#   - orch_bias 仅在 LLM 中性（0.45-0.55）时兜底，禁止覆盖 AI 方向
#   - consistency 惩罚删除（AI 与"自己看到的证据"不存在分歧惩罚）
# ─────────────────────────────────────────────────────────────────────
_AI_GOVERNED = os.getenv("MLTO_AI_GOVERNED", "0").strip().lower() in (
    "1", "true", "on", "yes"
)
try:
    # paper 起步默认 0.60（v6 松绑）；live 可用 env 降到 0.40
    _AI_GOVERNED_WEIGHT = float(os.getenv("MLTO_AI_GOVERNED_WEIGHT", "0.60"))
except (TypeError, ValueError):
    _AI_GOVERNED_WEIGHT = 0.60
_AI_GOVERNED_WEIGHT = max(0.40, min(1.0, _AI_GOVERNED_WEIGHT))
# framework 信号在 ai_governed 下作为"参照偏移"的权重上限（≤0.4）
_FW_REFERENCE_SCALE = 0.4

# ─────────────────────────────────────────────────────────────────────
# [v6 阶段2 S2-6] 灰度阶梯 0.60 档门禁：
#   计划 6.3 第 7 项要求“0.60 档前必须完成 confidence 校准器拟合”。
#   未显式确认（AI_GOVERNED_WEIGHT_CONFIRMED=1，校准完成/人工审批）时，
#   ≥0.60 的配置一律回退 0.40 档并告警，避免跳过校准直接加码 AI 权重。
#   回滚：MLTO_AI_GOVERNED=0 一键关闭；0.40 档可先于校准启动。
# ─────────────────────────────────────────────────────────────────────
_AI_GOVERNED_WEIGHT_CONFIRMED = os.getenv(
    "AI_GOVERNED_WEIGHT_CONFIRMED", "0"
).strip().lower() in ("1", "true", "on", "yes")


def resolve_governed_weight(weight: float, confirmed: bool) -> float:
    """灰度权重解析：≥0.60 且未确认 → 回退 0.40 档（校准门禁）。"""
    w = max(0.40, min(1.0, float(weight)))
    if w >= 0.60 and not confirmed:
        logger.warning(
            "[decision_hub] ai_governed 权重 %.2f 需 confidence 校准完成确认 "
            "(AI_GOVERNED_WEIGHT_CONFIRMED=1，见 S2-8)；回退 0.40 档",
            w,
        )
        return 0.40
    return w


_AI_GOVERNED_WEIGHT = resolve_governed_weight(
    _AI_GOVERNED_WEIGHT, _AI_GOVERNED_WEIGHT_CONFIRMED
)


def ai_governed_enabled() -> bool:
    """ai_governed 模式是否开启（供看板/日志显示）。"""
    return _AI_GOVERNED


def ai_governed_weight() -> float:
    """当前灰度权重档位（0.40 / 0.60 / 1.0）。"""
    return _AI_GOVERNED_WEIGHT

WEIGHTS_MID: Dict[str, float] = {
    "orch_mid_bias": 0.12,        # was 0.22（规则权威降级）
    "quant_alignment": 0.18,
    "entry_timing": 0.20,
    "thesis_health": 0.15,
    "analyst_consensus": 0.12,
    "feedback_loop": 0.08,
    "llm_qual": _LLM_WEIGHT_MID,  # was 0.03（AI 主导方向）
    "debate": 0.02,
    # mid_timing 在中周期不适用（mid 不再有子 mid_view），权重=0 即忽略。
    "mid_timing": 0.0,
}

WEIGHTS_LONG: Dict[str, float] = {
    "llm_qual": _LLM_WEIGHT_LONG,         # was 0.04（10x → AI 驱动方向；生产 0.30，灰度可降至 0.20）
    "mid_timing": 0.15,                   # NEW（中周期择时，来自 mid_view）
    "orch_long_bias": 0.12,               # was 0.24（规则权威降级）
    "quant_alignment": 0.12,
    "entry_timing": 0.08,
    "thesis_health": 0.05,
    "analyst_consensus": 0.05,
    "feedback_loop": 0.03,
    "debate": 0.02,
    # 合计 ≈ 0.92（fuse 通过 weighted_sum/total_w 归一化，无需精确为 1.0）
}

OPEN_THRESHOLDS = {
    "mid": {"WAIT": 0.40, "NIBBLE": 0.55, "BUILD": 0.70},
    "long": {"WAIT": 0.45, "NIBBLE": 0.60, "BUILD": 0.75},
}

OPEN_THRESHOLDS_AI_FIRST = {
    "mid": {"WAIT": 0.32, "NIBBLE": 0.46, "BUILD": 0.62},
    # [2026-07-31] long NIBBLE 0.50→0.42：补齐 macd/adx/trend_1w 后 hub adj 仍常在
    # 0.32–0.45；0.50 门槛导致近一周几乎只 WAIT、模拟盘长线零开仓。
    "long": {"WAIT": 0.30, "NIBBLE": 0.42, "BUILD": 0.62},
}

# MidLong v2 Phase2：Paper 默认更松（与 settings MIDLONG_HUB_*_PAPER 对齐）
OPEN_THRESHOLDS_PAPER_FAST = {
    "mid": {"WAIT": 0.26, "NIBBLE": 0.36, "BUILD": 0.55},
    "long": {"WAIT": 0.28, "NIBBLE": 0.36, "BUILD": 0.55},
}


def _use_paper_hub_thresholds(mode: Optional[str] = None) -> bool:
    """Paper / 快速试单用松门槛；显式 Live 保持 AI_FIRST。

    [P1-3 修复] 原实现只用进程级 PAPER_FAST_TRIAL / env TRADING_MODE 判断，
    `PAPER_FAST_TRIAL` 在 `FULLAUTO_FLOW_MODE=ai_first` 下恒为 true →
    Live 实盘 session 也误走 Paper 松门槛（NIBBLE 0.36 而非 0.42）。
    现在优先使用调用方传入的 session 真实 trading_mode（mode="live" 时绝不
    用 Paper 松门槛）；mode 未传入时才回退到旧逻辑（保持向后兼容）。
    """
    if mode is not None:
        return str(mode).strip().lower() != "live"
    try:
        from backend.config.settings import PAPER_FAST_TRIAL
        if PAPER_FAST_TRIAL:
            return True
    except Exception:
        pass
    return os.getenv("TRADING_MODE", "paper").strip().lower() != "live"


def _paper_hub_thresholds() -> Dict[str, float]:
    try:
        from backend.config.settings import (
            MIDLONG_HUB_BUILD_PAPER,
            MIDLONG_HUB_NIBBLE_PAPER,
            MIDLONG_HUB_WAIT_PAPER,
        )
        return {
            "WAIT": float(MIDLONG_HUB_WAIT_PAPER),
            "NIBBLE": float(MIDLONG_HUB_NIBBLE_PAPER),
            "BUILD": float(MIDLONG_HUB_BUILD_PAPER),
        }
    except Exception:
        return dict(OPEN_THRESHOLDS_PAPER_FAST["long"])


def _open_thresholds(tier: str, mode: Optional[str] = None) -> Dict[str, float]:
    try:
        from backend.config.settings import MIDLONG_AI_MANDATORY
        if _use_paper_hub_thresholds(mode):
            # mid/long Paper 共用可配门槛（中长线一体）
            return _paper_hub_thresholds()
        if MIDLONG_AI_MANDATORY:
            return OPEN_THRESHOLDS_AI_FIRST.get(tier, OPEN_THRESHOLDS_AI_FIRST["mid"])
    except Exception:
        pass
    return OPEN_THRESHOLDS.get(tier, OPEN_THRESHOLDS["mid"])


def fuse_signals(
    signals: List[Signal],
    tier: str,
    debate_signal: Optional[float] = None,
    owm_weights: Optional[Dict[str, float]] = None,
    trend_hint: Optional[Dict[str, Any]] = None,
    regime_name: Optional[str] = None,
    mode: Optional[str] = None,
) -> HubDecision:
    weights = WEIGHTS_LONG if tier == "long" else WEIGHTS_MID
    owm = owm_weights or {}

    # [2026-08-05 v6 6.3] ai_governed：llm_qual 权重升为灰度档位；
    # framework 系信号在加权时降为"参照偏移"（见下方 source 判断）
    ai_g = _AI_GOVERNED
    if ai_g:
        weights = dict(weights)
        weights["llm_qual"] = _AI_GOVERNED_WEIGHT

    if debate_signal is not None:
        signals = list(signals) + [
            Signal("debate", float(debate_signal), 0.3, "debate")
        ]

    total_w = 0.0
    weighted_sum = 0.0
    for s in signals:
        base_w = weights.get(s.name, 0.01)
        if ai_g and s.source == "framework":
            base_w = base_w * _FW_REFERENCE_SCALE  # 参照偏移，不改变方向
        owm_mult = float(owm.get(s.source, owm.get(s.name, 1.0)))
        w = base_w * s.confidence * max(0.5, min(1.5, owm_mult))
        weighted_sum += s.value * w
        total_w += w

    composite = weighted_sum / total_w if total_w > 0 else 0.5

    if ai_g:
        # [v6 6.3] consistency 惩罚删除：AI 与"自己看到的证据"不存在分歧惩罚。
        # adjusted = composite（无惩罚系数）；安全网仍由 thresholds 档位 + 组合
        # 预算（portfolio_budget）+ open_gate 物理底线承担否决。
        consistency = 1.0
        adjusted = composite
    else:
        fw = [s for s in signals if s.source == "framework"]
        llm = [s for s in signals if s.source in ("llm", "debate")]
        fw_vals = [s.value for s in fw] if fw else [composite]
        fw_std = float(np.std(fw_vals)) if len(fw_vals) > 1 else 0.0
        consistency = max(0.0, 1.0 - fw_std * 2.0)

        if llm and fw:
            fw_mean = float(np.mean(fw_vals))
            llm_mean = float(np.mean([s.value for s in llm]))
            # [阶段3a] 阈值 0.3→0.4：LLM 权重提升后允许更多 LLM-框架分歧再触发惩罚，
            # 避免 LLM 主导的决策被一致性惩罚过度压制。
            if abs(fw_mean - llm_mean) > 0.4:
                consistency *= 0.8

        # 震荡市 composite 常在 0.35–0.55；consistency 惩罚会把 adj 压到 ~0.29 永远过不了门控
        if 0.35 <= composite <= 0.55 and llm:
            consistency = max(consistency, 0.85)

        adjusted = composite * (0.7 + 0.3 * consistency)
    bonus_note = ""

    # MidLong v2：Trend 一致性 bonus（Merehead 轻量版）
    try:
        from backend.config.settings import MIDLONG_HUB_TREND_SIGNAL_BONUS
        _bonus = float(MIDLONG_HUB_TREND_SIGNAL_BONUS or 0)
    except Exception:
        _bonus = 0.05
    _hint = trend_hint if isinstance(trend_hint, dict) else None
    if _hint is None:
        try:
            from backend.services.full_auto.midlong_executor import get_trend_hint
            _sym = ""
            for s in signals:
                _sym = str(getattr(s, "symbol", "") or "")
                if _sym:
                    break
            if _sym:
                _hint = get_trend_hint(_sym)
        except Exception:
            _hint = None
    if _hint and _bonus > 0 and _hint.get("should_open"):
        _td = str(_hint.get("direction") or "").strip().lower()
        if _td in ("long", "short"):
            # 先估方向再比；最终 direction 在分档后派生，这里用初步方向
            _pre_dir, _ = _derive_direction(signals, adjusted, ai_governed=ai_g)
            if _pre_dir == _td or (_pre_dir == "neutral" and adjusted >= 0.28):
                # neutral 但 Trend 明确方向时也给一半 bonus，帮助越过 NIBBLE
                _add = _bonus if _pre_dir == _td else (_bonus * 0.5)
                adjusted = min(1.0, adjusted + _add)
                bonus_note = f"+trend_bonus={_add:.2f}"

    # Regime unknown：门槛等效收紧（adj 减 0.05，等价于门槛 +0.05）
    _reg = (regime_name or "").strip().lower()
    if not _reg:
        try:
            from backend.services.full_auto.midlong_executor import get_cached_regime
            _sym2 = ""
            for s in signals:
                _sym2 = str(getattr(s, "symbol", "") or "")
                if _sym2:
                    break
            if _sym2:
                _reg = get_cached_regime(_sym2) or ""
        except Exception:
            _reg = ""
    if _reg == "unknown":
        adjusted = max(0.0, adjusted - 0.05)
        bonus_note = (bonus_note + " regime_unknown-0.05").strip()

    thresholds = _open_thresholds(tier, mode=mode)

    if adjusted >= thresholds["BUILD"]:
        action = "BUILD"
    elif adjusted >= thresholds["NIBBLE"]:
        action = "NIBBLE"
    else:
        action = "WAIT"

    direction, dir_src = _derive_direction(signals, adjusted, ai_governed=ai_g)
    # Trend 方向明确且 Hub 中性时，对齐专家方向（仅影响标注，不开仓权威仍在 Writer）
    # ai_governed：框架永不改方向；Trend hint 仅作中性兜底标注（等同 orch 级证据）
    if (
        direction == "neutral"
        and _hint
        and _hint.get("should_open")
        and str(_hint.get("direction") or "").lower() in ("long", "short")
        and action in ("NIBBLE", "BUILD")
    ):
        direction = str(_hint.get("direction")).lower()
        dir_src = "trend_hint"

    # P1：Paper NIBBLE/BUILD 探针 — AI 中性带导致 action+neutral→gate 全拒
    # 用更软的 llm/orch/quant 偏向给方向；日限额由审计 JSONL 计数。
    if (
        direction == "neutral"
        and action in ("NIBBLE", "BUILD")
        and _use_paper_hub_thresholds(mode)
        and _nibble_probe_enabled()
    ):
        lean, lean_src = _nibble_probe_lean(signals)
        if lean in ("long", "short") and _nibble_probe_quota_remaining():
            direction = lean
            dir_src = lean_src
            bonus_note = (bonus_note + f" {lean_src}").strip()
            logger.info(
                "[decision_hub] %s probe lean=%s src=%s adj=%.2f",
                action, lean, lean_src, adjusted,
            )

    readiness = int(min(100, max(0, adjusted * 100)))

    _mode_tag = "ai_governed" if ai_g else "standard"
    reason = (
        f"MLTO hub adj={adjusted:.2f} cons={consistency:.2f} → {action}/{direction}"
        + f" [{_mode_tag} dir_src={dir_src or '-'}]"
        + (f" {bonus_note}" if bonus_note else "")
    )

    # M8 周期共振层：发布 mid/long 信号（PRL_ENABLED=false 时 no-op）
    try:
        from backend.services.portfolio.resonance_layer import (
            PeriodSignal,
            resonance_layer,
        )
        _sym0 = getattr(signals[0], "symbol", "") if signals else ""
        resonance_layer.publish(PeriodSignal(
            symbol=str(_sym0 or ""),
            tier=tier,
            direction=direction,
            confidence=float(abs(adjusted) * 100),
            source="mlto_decision_hub",
        ))
    except Exception:
        pass

    return HubDecision(
        action=action,
        direction=direction,
        composite=round(composite, 4),
        adjusted=round(adjusted, 4),
        consistency=round(consistency, 4),
        open_readiness=readiness,
        reason_text=reason,
        signals=signals,
        mode=("ai_governed" if ai_g else "standard"),
        ai_governed_weight=(_AI_GOVERNED_WEIGHT if ai_g else None),
        dir_src=dir_src or "",
    )


def _orch_bias_direction(signals: List[Signal], adjusted: float) -> str:
    """orch_*_bias 作为 LLM 中性时的回退方向来源（规则权威，仅兜底）。"""
    long_hints = []
    short_hints = []
    for s in signals:
        if s.name in ("orch_mid_bias", "orch_long_bias"):
            if s.value >= 0.65:
                long_hints.append(s.value)
            elif s.value <= 0.35:
                short_hints.append(1.0 - s.value)
    if long_hints and (not short_hints or np.mean(long_hints) > np.mean(short_hints)):
        if adjusted >= 0.32:
            return "long"
    if short_hints and (not long_hints or np.mean(short_hints) > np.mean(long_hints)):
        if adjusted >= 0.32:
            return "short"
    return "neutral"


def _derive_direction(
    signals: List[Signal], adjusted: float, *, ai_governed: bool = False
) -> tuple:
    """返回 (direction, dir_src)。

    ai_governed：direction = llm_qual 单调映射（≥0.55 多 / ≤0.45 空）；
    orch_bias 仅在 LLM 中性带兜底；framework 永不改方向。
    """
    llm_sig = next((s for s in signals if s.name == "llm_qual"), None)
    if llm_sig is not None:
        if ai_governed:
            if llm_sig.value >= 0.55:
                return "long", "llm_qual"
            if llm_sig.value <= 0.45:
                return "short", "llm_qual"
            # LLM 中性：仅 orch_bias 兜底（禁止 framework 翻向）
            _d = _orch_bias_direction(signals, adjusted)
            if _d != "neutral":
                return _d, "orch_bias"
            return "neutral", "llm_qual"
        if llm_sig.value >= 0.6:
            if adjusted >= 0.32:
                return "long", "llm_qual"
            return (
                ("long", "framework") if _fw_mean(signals) >= 0.55 else ("neutral", "llm_qual")
            )
        if llm_sig.value <= 0.4:
            if adjusted >= 0.32:
                return "short", "llm_qual"
            return (
                ("short", "framework") if _fw_mean(signals) <= 0.45 else ("neutral", "llm_qual")
            )

    _d = _orch_bias_direction(signals, adjusted)
    if _d != "neutral":
        return _d, "orch_bias"
    return "neutral", "framework"


def _nibble_probe_enabled() -> bool:
    try:
        from backend.config.settings import MIDLONG_NIBBLE_PROBE_ENABLED
        return bool(MIDLONG_NIBBLE_PROBE_ENABLED)
    except Exception:
        return os.getenv("MIDLONG_NIBBLE_PROBE_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )


def _nibble_probe_quota_remaining() -> bool:
    try:
        from backend.config.settings import MIDLONG_NIBBLE_PROBE_DAILY_MAX
        cap = int(MIDLONG_NIBBLE_PROBE_DAILY_MAX or 0)
    except Exception:
        try:
            cap = int(os.getenv("MIDLONG_NIBBLE_PROBE_DAILY_MAX", "2") or "2")
        except Exception:
            cap = 2
    if cap <= 0:
        return False
    try:
        from backend.services.mlto.midlong_direction_audit import count_nibble_probes_today
        used = int(count_nibble_probes_today() or 0)
    except Exception:
        used = 0
    return used < cap


def _nibble_probe_lean(signals: List[Signal]) -> tuple:
    """NIBBLE/BUILD 探针软方向。

    实盘审计：大量 adj≈0.50–0.56 却 dir=neutral（llm 卡在 0.45–0.55）。
    策略：
      1) llm 相对 0.5 的微小偏离（±0.01）即 lean
      2) orch 软门槛 0.55/0.45（正式 orch 兜底要 0.65/0.35）
      3) quant_alignment / entry_timing 同软门槛
    """
    llm_sig = next((s for s in signals if s.name == "llm_qual"), None)
    if llm_sig is not None:
        try:
            v = float(llm_sig.value)
        except (TypeError, ValueError):
            v = 0.5
        # 死区 (0.45, 0.55) 内：只要偏离中性 0.01 就给方向
        if v >= 0.51:
            return "long", "nibble_probe_llm"
        if v <= 0.49:
            return "short", "nibble_probe_llm"

    # orch 软门槛（探针专用，低于正式 0.65/0.35）
    for name in ("orch_long_bias", "orch_mid_bias"):
        s = next((x for x in signals if x.name == name), None)
        if s is None:
            continue
        try:
            ov = float(s.value)
        except (TypeError, ValueError):
            continue
        if ov >= 0.55:
            return "long", "nibble_probe_orch"
        if ov <= 0.45:
            return "short", "nibble_probe_orch"

    for name in ("quant_alignment", "entry_timing", "mid_timing"):
        s = next((x for x in signals if x.name == name), None)
        if s is None:
            continue
        try:
            qv = float(s.value)
        except (TypeError, ValueError):
            continue
        if qv >= 0.58:
            return "long", "nibble_probe_quant"
        if qv <= 0.42:
            return "short", "nibble_probe_quant"

    # 最后：llm 恰为 0.5 时，用 framework 均值微偏（仅探针，且幅度够大）
    try:
        fw = _fw_mean(signals)
        if fw >= 0.58:
            return "long", "nibble_probe_fw"
        if fw <= 0.42:
            return "short", "nibble_probe_fw"
    except Exception:
        pass
    return "neutral", ""


def _fw_mean(signals: List[Signal]) -> float:
    return float(np.mean([s.value for s in signals if s.source == "framework"] or [0.5]))
