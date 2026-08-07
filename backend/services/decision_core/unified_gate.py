"""UnifiedDecisionGate — V5 统一开仓门控。

把此前散落 7 处的门控（短线门槛、费用门槛、置信度门槛、日交易上限、
盈亏比检查、市场状态约束）合并到一个入口，每次拦截输出结构化日志：

    [V5Gate] BLOCK symbol=SOL rule=daily_cap detail=...

运行时可调参数从 data/v5_runtime_gates.json 读取（反馈闭环 M3 写入），
环境变量为基准默认值。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_RUNTIME_GATES_FILE = os.path.join("data", "v5_runtime_gates.json")
_runtime_cache: dict = {"ts": 0.0, "data": {}}


def _runtime_overrides() -> dict:
    """读取反馈闭环写入的运行时门槛（带 60s 缓存与边界保护）。"""
    try:
        from backend.services.runtime_tuning_store import runtime_gates_compat
        return runtime_gates_compat()
    except Exception:
        pass
    import time

    now = time.time()
    if now - _runtime_cache["ts"] < 60:
        return _runtime_cache["data"]
    data: dict = {}
    try:
        if os.path.exists(_RUNTIME_GATES_FILE):
            with open(_RUNTIME_GATES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            # 边界保护：反馈闭环只能在安全区间内调整
            if "max_daily_trades" in raw:
                data["max_daily_trades"] = max(3, min(20, int(raw["max_daily_trades"])))
            if "scalp_min_confidence" in raw:
                data["scalp_min_confidence"] = max(60, min(90, int(raw["scalp_min_confidence"])))
            if "min_risk_reward" in raw:
                try:
                    from backend.config.settings import V5_MAX_RUNTIME_MIN_RR
                    _rr_cap = float(V5_MAX_RUNTIME_MIN_RR)
                except Exception:
                    _rr_cap = 2.5
                data["min_risk_reward"] = max(1.2, min(_rr_cap, float(raw["min_risk_reward"])))
            if "disabled_natures" in raw and isinstance(raw["disabled_natures"], list):
                data["disabled_natures"] = [str(n).lower() for n in raw["disabled_natures"]][:3]
    except Exception as err:
        logger.warning("[V5Gate] runtime gates 读取失败: %s", err)
    _runtime_cache["ts"] = now
    _runtime_cache["data"] = data
    return data


@dataclass
class GateResult:
    allowed: bool
    rule: str = ""
    reason: str = ""
    # 给执行层的修正建议（如收紧后的门槛）
    adjustments: dict = field(default_factory=dict)


# M4: 最近拦截记录（进程内环形缓冲），用于下一轮回灌 LLM prompt —
# 让模型知道「上一轮哪些决策为什么没执行」，避免反复提交同样会被拦的单
_RECENT_BLOCKS_MAX = 40
_recent_blocks: list = []


def _record_block(symbol: str, action: str, rule: str, reason: str) -> None:
    import time

    _recent_blocks.append({
        "ts": time.time(),
        "symbol": symbol,
        "action": action,
        "rule": rule,
        "reason": reason,
    })
    if len(_recent_blocks) > _RECENT_BLOCKS_MAX:
        del _recent_blocks[: len(_recent_blocks) - _RECENT_BLOCKS_MAX]


def record_block_event(symbol: str, action: str, rule: str, reason: str) -> None:
    """供外部闸门（如因子否决）登记拦截事件，统一进入 M4 回灌通道。"""
    _record_block(symbol, action, rule, reason)


def get_recent_blocks(max_age_seconds: float = 900) -> list:
    """返回最近 max_age_seconds 内被闸门拦截的决策（最新在前）。"""
    import time

    cutoff = time.time() - max_age_seconds
    return [b for b in reversed(_recent_blocks) if b["ts"] >= cutoff]


def build_block_feedback_section(max_age_seconds: float = 900) -> str:
    """构建「上一轮被拦截决策」prompt 段（M4 回灌）。"""
    blocks = get_recent_blocks(max_age_seconds)
    if not blocks:
        return ""
    lines = [
        "### 🚧 你最近被执行闸门拦截的决策（这些单没有真正执行 — 不要原样重复提交）",
    ]
    for b in blocks[:10]:
        lines.append(
            f"  - {b['symbol']} {b['action']} 被拦 [{b['rule']}]: {b['reason']}"
        )
    lines.append(
        "📌 对策：confidence 不足被拦 → 除非有新证据将置信度真实提高到门槛以上，"
        "否则直接给 hold；盈亏比被拦 → 重新设计 TP/SL 结构；额度被拦 → 今日停止该类开仓。"
    )
    return "\n".join(lines)


def _block(symbol: str, action: str, rule: str, reason: str) -> GateResult:
    logger.info(
        "[V5Gate] BLOCK symbol=%s action=%s rule=%s detail=%s",
        symbol, action, rule, reason,
    )
    _record_block(symbol, action, rule, reason)
    return GateResult(allowed=False, rule=rule, reason=reason)


# ── P1-1 修复：V5 门内 nature 归一 ──────────────────────────────────
# 设计承诺（MIDLONG_V2 §6）：任何默认 swing 的遗留路径在进 V5 前归一；
# 若无法判断 → hold + [MidLong] nature_ambiguous。
# 此前归一只做在执行层（midlong_helpers / midlong_executor），unified_gate 自身
# 不做归一 → _TIER_CAP 无 swing/position 键 → daily_cap=0 硬拦（master_execution:2054
# 非 fast-lane 分支把原始 swing 直传 evaluate_midlong_open → 撞 daily_cap=0）。
# 这里在门内统一归一：scalp/intraday 保持；swing/position/mid/long → trend_follow；
# 未知 nature → 'nature_ambiguous'（由调用方转 hold，见 evaluate_entry）。
_MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position", "mid", "long"})
_MIDLONG_NORMALIZED = "trend_follow"


def normalize_v5_nature(raw: Optional[str]) -> str:
    """把 trade_nature 归一到 V5 门认识的周期：trend_follow（中长线一体）。

    返回 'nature_ambiguous' 表示无法判断（不应继续走开仓门控，应转 hold）。
    """
    n = (raw or "").strip().lower()
    if n in ("scalp", "intraday"):
        return n
    if n in _MIDLONG_NATURES:
        return _MIDLONG_NORMALIZED
    if not n:
        # 空 nature：默认 swing 语义 → 归一为 trend_follow（与执行层默认一致）
        return _MIDLONG_NORMALIZED
    return "nature_ambiguous"


def evaluate_entry(
    *,
    db,
    account_id: int,
    symbol: str,
    action: str,
    confidence: float,
    tier: str,
    trade_nature: str,
    tp_pct: Optional[float],
    sl_pct: Optional[float],
    market_data: Optional[dict] = None,
    base_entry_threshold: int = 50,
    is_auto_coin: bool = False,
    mode: str = "paper",
    thesis_id: str = "",
    open_readiness: Optional[int] = None,
    hub_composite: Optional[float] = None,
    hub_adjusted: Optional[float] = None,
) -> GateResult:
    """新开仓统一检查（buy/sell/pyramid/dca 且无对应持仓时调用）。

    tp_pct / sl_pct 传入 AI 决策值；为空时由调用方先用 tier 默认值填充。
    """
    from backend.config.settings import (
        AUTO_COIN_V5_CONF_PENALTY,
        AUTO_COIN_V5_CONF_RELIEF,
        AUTO_COIN_V5_MIN_RR,
        PAPER_AUTO_COIN_V5_CONF_PENALTY,
        PAPER_AUTO_COIN_V5_MIN_RR,
        PAPER_RELAX_AUTO_COIN_V5,
        V5_DECISION_CORE_ENABLED,
        V5_HIGH_CONF_CONF_RELIEF,
        V5_HIGH_CONF_MIN_RR,
        V5_HIGH_CONF_THRESHOLD,
        V5_MAX_RUNTIME_MIN_RR,
        V5_MIN_RISK_REWARD,
        V5_MIN_TP_PCT,
        V5_SCALP_MIN_CONFIDENCE,
        V5_SCALP_MIN_RR,
        V5_SCALP_MIN_RR_PAPER,
        V5_SCALP_MIN_TP_PCT,
        V5_SCALP_MIN_TP_PCT_PAPER,
        V5_TREND_FOLLOW_MIN_CONFIDENCE,
        V5_TREND_MIN_RR,
        V5_TREND_MIN_RR_PAPER,
    )

    if not V5_DECISION_CORE_ENABLED:
        # 终态：总闸关闭时不再无条件放行——Live 模式下这等价于对真实资金
        # 关闭全部门禁，必须显式启动断言拦住，逼迫运维人工确认后才能继续。
        # Paper 模式保留原有"一键放行"行为（训练/回滚场景合理，不受影响）。
        if (mode or "paper").strip().lower() != "paper":
            raise RuntimeError(
                "V5_DECISION_CORE_ENABLED=false 禁止在 Live 模式下启动：该开关关闭时 "
                "evaluate_entry 会直接放行所有开仓请求，等价于对真实资金关闭全部门禁。"
                "请显式设置 V5_DECISION_CORE_ENABLED=true 后再启动 Live 交易。"
            )
        return GateResult(allowed=True, rule="disabled")

    action_l = (action or "").lower()
    if action_l in ("buy", "sell", "pyramid", "dca") and thesis_id:
        logger.info(
            "[V5Gate] AUDIT symbol=%s tier=%s thesis_id=%s readiness=%s hub_adj=%s hub_comp=%s",
            symbol,
            tier,
            thesis_id[:12],
            open_readiness if open_readiness is not None else "-",
            f"{hub_adjusted:.2f}" if hub_adjusted is not None else "-",
            f"{hub_composite:.2f}" if hub_composite is not None else "-",
        )

    if action_l not in ("buy", "sell", "pyramid", "dca"):
        return GateResult(allowed=True, rule="not_entry")

    # ── P1-1 修复：V5 门内 nature 归一 ──
    # 原始 nature 保留用于读取 by_nature 运行时配置（swing/trend_follow 各有独立表）；
    # 归一后的 nature 用于 fee_ctx、置信度门槛等。
    raw_nature_l = (trade_nature or "swing").lower()
    nature_l = normalize_v5_nature(raw_nature_l)
    if nature_l == "nature_ambiguous":
        # 设计承诺：无法判断的 nature → hold + [MidLong] nature_ambiguous，
        # 绝不让未知 nature 绕过 V5 门进入开仓。
        _msg = f"trade_nature={raw_nature_l!r} 无法归一到已知周期 → hold"
        logger.warning("[MidLong] nature_ambiguous symbol=%s action=%s %s", symbol, action_l, _msg)
        return _block(symbol, action_l, "nature_ambiguous", _msg)
    overrides = _runtime_overrides()
    by_nature = overrides.get("by_nature") or {}
    nature_cfg = by_nature.get(raw_nature_l) or by_nature.get("swing" if raw_nature_l == "swing" else "trend_follow") or {}

    # ── 2026-07-04: paper 门槛与 Agent/Swing 代码对齐（不再硬编码 68/50）──
    _is_paper = (mode or "paper").strip().lower() == "paper"
    _paper_floor = 45 if _is_paper else 40
    _paper_scalp_gate = 65 if _is_paper else int(overrides.get("scalp_min_confidence", V5_SCALP_MIN_CONFIDENCE))
    # 修正死三元表达式（此前 paper/live 两分支完全相同，paper 未获得任何放宽，
    # 与 3 天 long tier 零成交现象方向吻合）：paper 比 live 低 12 分，但不低于 30 的
    # 合理下限，避免长线置信度门槛在 paper 模式下被压到毫无意义的水平。
    _paper_trend_gate = (
        max(30, int(V5_TREND_FOLLOW_MIN_CONFIDENCE) - 12) if _is_paper
        else int(V5_TREND_FOLLOW_MIN_CONFIDENCE)
    )
    # Paper/Live × nature：短线用更低 RR/TP（加密剥头皮）；中长线一体用 trend 标准。
    # runtime min_risk_reward 仅作中长线/全局上调，不再用 1.5 地板压死短线。
    _is_scalp_like = nature_l in ("scalp", "intraday")
    _is_midlong = nature_l in ("trend_follow", "position", "swing")
    if _is_scalp_like:
        _paper_min_rr = float(V5_SCALP_MIN_RR_PAPER if _is_paper else V5_SCALP_MIN_RR)
        _paper_min_tp = float(V5_SCALP_MIN_TP_PCT_PAPER if _is_paper else V5_SCALP_MIN_TP_PCT)
    elif _is_midlong:
        # ── P1-2 修复：Paper RR 公式 ──
        # 原公式把 env 默认值 V5_MIN_RISK_REWARD(=1.8) 当 runtime override 参与计算：
        #   paper = max(1.6, min(1.8, 1.8)) = 1.8 → V5_TREND_MIN_RR_PAPER(1.6) 永不生效
        #   live  = max(1.8, 1.8) = 1.8
        # 正确语义：runtime min_risk_reward 仅当运维显式写入时才作「上调」，
        # 未配置时 paper/live 各用各自基准门槛（paper 松 1.6 / live 严 1.8）。
        _runtime_rr_raw = overrides.get("min_risk_reward")
        if _runtime_rr_raw is None:
            _paper_min_rr = float(V5_TREND_MIN_RR_PAPER if _is_paper else V5_TREND_MIN_RR)
        else:
            _rr_up = float(_runtime_rr_raw)
            _paper_min_rr = (
                max(float(V5_TREND_MIN_RR_PAPER), _rr_up)
                if _is_paper
                else max(float(V5_TREND_MIN_RR), _rr_up)
            )
        _paper_min_tp = 0.008 if _is_paper else float(V5_MIN_TP_PCT)
    else:
        _paper_min_rr = (
            max(1.5, float(overrides.get("min_risk_reward", 1.5))) if _is_paper
            else float(overrides.get("min_risk_reward", V5_MIN_RISK_REWARD))
        )
        _paper_min_tp = 0.008 if _is_paper else V5_MIN_TP_PCT
    # 震荡均值回归模式（2026-07-09）：MR 单靠小止盈+高胜率赚钱，止盈天然只有 0.6%~1.2%，
    # 会被默认 min_tp/min_rr 冤杀。故仅对 ranging_mr 单换用 MR 专用下限。
    _is_ranging_mr = bool(isinstance(market_data, dict) and market_data.get("ranging_mr"))
    _mr_min_rr = 1.0
    _mr_min_tp = 0.006
    if _is_ranging_mr:
        try:
            from backend.config.settings import SCALP_MR_MIN_RR, SCALP_MR_MIN_TP
            _mr_min_rr = float(SCALP_MR_MIN_RR)
            _mr_min_tp = float(SCALP_MR_MIN_TP)
        except Exception:
            pass
        _paper_min_rr = _mr_min_rr
        _paper_min_tp = _mr_min_tp
    if nature_cfg.get("min_score"):
        _paper_trend_gate = max(_paper_trend_gate, int(nature_cfg["min_score"]))
    if nature_cfg.get("min_confidence") and raw_nature_l == "swing":
        _paper_trend_gate = max(_paper_trend_gate, int(nature_cfg["min_confidence"]))
    # by_nature.min_risk_reward：短线允许下调到 nature 表；中长线只上调
    if nature_cfg.get("min_risk_reward") and not _is_ranging_mr:
        _nrr = float(nature_cfg["min_risk_reward"])
        if _is_scalp_like:
            _paper_min_rr = _nrr
        else:
            _paper_min_rr = max(_paper_min_rr, _nrr)

    # ── 0. 反馈闭环禁用的 nature ──
    disabled = overrides.get("disabled_natures") or []
    if nature_l in disabled:
        return _block(symbol, action_l, "nature_disabled",
                      f"nature={nature_l} 已被反馈闭环禁用")

    # ── 0.5 多频率约束（改为柔性：长线定方向，中短线择时） ──
    # P0 修复（2026-07-13）：原实现对中长线硬拦多频率冲突（1h≠4h → block），
    # 但正确逻辑是"长线趋势确定大方向，中短线找入场时机"。
    # - scalp：已解禁（短线本质是逆势抓反抽）
    # - swing/trend_follow：不硬拦多频率冲突，改为缩仓（size_multiplier 降到 0.6）
    #   只在极端背离（所有周期一致反向）时才 block。
    size_multiplier = 1.0  # 提前初始化（multi_freq 段可能缩仓）
    if isinstance(market_data, dict) and market_data.get("constraint_violated"):
        from backend.config.settings import SCALP_ALLOW_COUNTER_TREND

        _constraint_reason = market_data.get("constraint_reason") or ""

        if SCALP_ALLOW_COUNTER_TREND and nature_l == "scalp":
            logger.info(
                "[V5Gate] 短线放行逆势 symbol=%s action=%s（跳过 multi_freq_constraint: %s）",
                symbol, action_l, _constraint_reason,
            )
        elif nature_l in ("swing", "trend_follow"):
            # 中长线：多频率冲突不硬拦，改为缩仓。
            # 长线趋势确定方向，中短线择时入场是正常的多时间框架分析。
            # 只有"全部短周期一致强反向"才是真正的方向错误，此时 block。
            _is_extreme_reversal = "一致" in _constraint_reason or "all" in _constraint_reason.lower()
            if _is_extreme_reversal:
                return _block(
                    symbol, action_l, "multi_freq_constraint",
                    f"极端方向背离: {_constraint_reason}",
                )
            # 非极端冲突：缩仓放行（大方向对，小周期分歧→减仓控制风险）
            size_multiplier = min(size_multiplier, 0.6)
            logger.info(
                "[V5Gate] %s 放行多频率分歧 symbol=%s action=%s（缩仓至%.0f%%: %s）",
                nature_l, symbol, action_l, size_multiplier * 100, _constraint_reason,
            )
        else:
            return _block(
                symbol, action_l, "multi_freq_constraint",
                f"多频率硬约束(H1-H5)违反: {_constraint_reason}",
            )

    # ── 1. 市场状态：极端态禁止新开仓；震荡态缩仓不 block ──
    regime_adjust = 0
    if market_data is not None:
        from backend.services.decision_core.regime_agent import classify_regime

        regime = classify_regime(market_data)
        if not regime.allow_open:
            return _block(symbol, action_l, "regime_extreme",
                          f"极端行情禁止开仓 ({regime.detail})")
        regime_adjust = regime.gate_adjust
        size_multiplier = float(getattr(regime, "size_multiplier", 1.0) or 1.0)

    # ── 2. 交易成本上下文（费用教育用） ──
    # 2026-08-05 移除：日开仓配额（各周期独立）不再按 opens_today 计数拦截，
    # 开仓频率改由置信度门槛 / 盈亏比 / regime 极端行情 / 风控规则把关。
    from backend.services.decision_core.fee_context import build_fee_context

    fee_ctx = build_fee_context(db, account_id, daily_cap=0)



    # ── 3. 置信度（统一解析器：收敛多道门 + 成熟度松紧 + 可解释） ──
    from backend.services.decision_core.threshold_resolver import (
        normalize_confidence_pct,
        resolve_effective_entry_threshold,
    )

    conf = normalize_confidence_pct(confidence)
    high_conviction = conf >= V5_HIGH_CONF_THRESHOLD
    _scalp_gate = int(overrides.get("scalp_min_confidence", V5_SCALP_MIN_CONFIDENCE))

    mode_l = (mode or "paper").strip().lower()
    _auto_penalty = AUTO_COIN_V5_CONF_PENALTY
    _auto_min_rr = AUTO_COIN_V5_MIN_RR
    if mode_l == "paper" and PAPER_RELAX_AUTO_COIN_V5:
        _auto_penalty = PAPER_AUTO_COIN_V5_CONF_PENALTY
        _auto_min_rr = PAPER_AUTO_COIN_V5_MIN_RR

    _eff = resolve_effective_entry_threshold(
        base_threshold=base_entry_threshold,
        regime_adjust=regime_adjust,
        nature=nature_l,
        tier=tier,
        symbol=symbol,
        side=action_l,
        scalp_gate=_paper_scalp_gate,  # paper: 50, live: 70
        trend_gate=_paper_trend_gate,   # paper: 55, live: 72
        is_auto_coin=is_auto_coin,
        high_conviction=high_conviction,
        auto_relief=AUTO_COIN_V5_CONF_RELIEF,
        auto_penalty=_auto_penalty,
        high_relief=V5_HIGH_CONF_CONF_RELIEF,
        mode=mode,
        floor=_paper_floor,  # paper: 30, live: 40
    )
    effective_threshold = _eff.effective
    if conf < effective_threshold:
        return _block(symbol, action_l, "confidence",
                      f"置信度 {conf:.0f}% < {_eff.explain()}")

    # ── 3.5 周期方向概率门禁（cycle_direction_probability 引擎）──
    # 数据锚定的方向先验：当开仓方向与概率引擎"明显反向"时拦截。关键是**校准感知**——
    # 只有该 tier 历史校准质量达标才硬拦截，否则仅观察记日志（防止弱信号误杀机会）。
    # fail-open：本闸是增益型信号，异常时不拦截（与 short_tier 那种"堵漏洞"闸的
    # fail-closed 取向不同——概率闸缺失只是少一个先验，不构成裸奔风险）。
    if action_l in ("buy", "sell"):
        try:
            from backend.config.settings import (
                CYCLE_PROB_GATE_ENABLED,
                CYCLE_PROB_GATE_MARGIN,
                CYCLE_PROB_GATE_MIN_CALIBRATION,
                CYCLE_PROB_GATE_PAPER_SIZE_MULT,
            )
            if CYCLE_PROB_GATE_ENABLED and isinstance(market_data, dict):
                from backend.services.cycle_direction_probability import (
                    cycle_probability_engine,
                    extract_features_from_indicators,
                    TIER_PRIMARY,
                )
                _tier = tier if tier in TIER_PRIMARY else "mid"
                _tf = TIER_PRIMARY[_tier]
                _ind = market_data.get(f"indicators_{_tf}")
                if not isinstance(_ind, dict):
                    _ind = market_data  # 退回扁平 indicators
                _feats = extract_features_from_indicators(_ind)
                _res = cycle_probability_engine.estimate(_tier, _feats)
                if _res.available:
                    _intended_up = action_l == "buy"
                    _p_intended = _res.prob_up if _intended_up else _res.prob_down
                    _p_opposite = _res.prob_down if _intended_up else _res.prob_up
                    _conflict = (_p_opposite - _p_intended) >= CYCLE_PROB_GATE_MARGIN
                    _calibrated = _res.calibration_quality >= CYCLE_PROB_GATE_MIN_CALIBRATION
                    if _conflict and _calibrated and not _is_paper:
                        return _block(
                            symbol, action_l, "cycle_prob_conflict",
                            f"{_tier}周期概率引擎反向: 意图{'涨' if _intended_up else '跌'} "
                            f"p={_p_intended:.0%} vs 反向 p={_p_opposite:.0%} "
                            f"(校准质量 {_res.calibration_quality:.2f}≥{CYCLE_PROB_GATE_MIN_CALIBRATION})",
                        )
                    if _conflict and _calibrated and _is_paper:
                        # Paper：软缩仓，不 block（保留样本积累）
                        size_multiplier *= float(CYCLE_PROB_GATE_PAPER_SIZE_MULT)
                        logger.info(
                            "[V5Gate] CYCLE_PROB paper缩仓 symbol=%s tier=%s 反向 p_opp=%.2f p_int=%.2f ×%.2f",
                            symbol, _tier, _p_opposite, _p_intended, CYCLE_PROB_GATE_PAPER_SIZE_MULT,
                        )
                    elif _conflict and not _calibrated:
                        # 校准不足：仅观察，不改变决策
                        logger.info(
                            "[V5Gate] CYCLE_PROB 观察(校准不足 q=%.2f<%.2f) symbol=%s tier=%s 反向 p_opp=%.2f p_int=%.2f",
                            _res.calibration_quality, CYCLE_PROB_GATE_MIN_CALIBRATION,
                            symbol, _tier, _p_opposite, _p_intended,
                        )
        except Exception as _cp_err:
            logger.debug("[V5Gate] cycle_prob 门禁跳过(fail-open): %s", _cp_err)

    # ── 4. 短线 tier 既有硬门槛（置信度加点 + 同向冷却），保持兼容 ──
    # Fix 1: paper 模式也走 short_tier 硬门。
    # 旧逻辑"探索期不该被短线冷却卡死"导致 paper 短线完全裸奔，
    # 大量噪音信号污染学习数据（DB 证实短线近20笔赢6输14）。
    try:
        from backend.services.short_tier_entry_gate import check_short_tier_entry

        st = check_short_tier_entry(
            account_id=account_id,
            symbol=symbol,
            side=action_l,
            action=action_l,
            confidence=conf,
            tier=tier,
            trade_nature=nature_l,
            base_entry_threshold=base_entry_threshold,
            mode=mode,
        )
        if not st.allowed:
            return _block(symbol, action_l, "short_tier", st.reason)
    except Exception as err:
        # fail-closed：short_tier 门存在的理由就是堵短线裸奔漏洞，异常时"跳过放行"
        # 等价于把这道闸变成可被任何异常绕过的旁路，与主路径 fail-closed 纪律不一致。
        logger.warning("[V5Gate] short_tier 检查异常，fail-closed 拦截: %s", err)
        return _block(symbol, action_l, "short_tier_error", f"short_tier 检查异常: {err}")

    # ── 5. 盈亏比与最小止盈距离（经济学核心）──
    # 2026-06-18: paper 模式放宽 min_tp（0.6%），live 保持严格
    min_rr = _paper_min_rr
    min_rr = min(min_rr, V5_MAX_RUNTIME_MIN_RR)
    # 终态：删除"paper 模式 min(min_rr, 1.3)"的强行压制。旧逻辑会把反馈闭环/
    # by_nature 调高后的 min_rr（哪怕调到 2.0）系统性压回 1.3，导致 paper 环境
    # 永远无法验证"提高盈亏比门槛是否真的改善盈亏"这一根因假设。paper 现在
    # 只受自己的独立基线 1.5（见 _paper_min_rr）与 runtime 覆盖约束，不再有额外上限。
    if not _is_paper:
        if is_auto_coin:
            min_rr = max(min_rr, _auto_min_rr)
        elif high_conviction:
            min_rr = min(min_rr, V5_HIGH_CONF_MIN_RR)
        if _eff.maturity_stage == "warmup":
            min_rr = min(min_rr, 1.5)
        elif _eff.maturity_stage == "growth":
            min_rr = min(min_rr, 1.6)

    tp = float(tp_pct or 0)
    sl = float(sl_pct or 0)
    if tp <= 0 or sl <= 0:
        # 终态：TP/SL 缺失不再直接跳过 RR/最小止盈检查（旧逻辑等价于放行未经
        # 校验的负期望交易）。先用 tier 默认值兜底重算，兜底后仍缺失才 block。
        try:
            from backend.services.decision_core.pipeline import _tier_tp_sl_defaults

            _tp_sl_defaults = _tier_tp_sl_defaults(tier)
        except Exception as _default_err:
            logger.debug("[V5Gate] tier 默认 TP/SL 兜底失败: %s", _default_err)
            _tp_sl_defaults = {}
        if tp <= 0:
            tp = float(_tp_sl_defaults.get("tp_pct") or 0)
        if sl <= 0:
            sl = float(_tp_sl_defaults.get("sl_pct") or 0)
        if tp <= 0 or sl <= 0:
            return _block(
                symbol, action_l, "tp_sl_missing",
                f"TP/SL 缺失(tp={tp_pct},sl={sl_pct})，tier={tier} 默认值兜底后仍无法确定，"
                "禁止放行未经盈亏比校验的交易",
            )

    rr = tp / sl
    if rr < min_rr:
        return _block(symbol, action_l, "risk_reward",
                      f"盈亏比 {rr:.2f} < 最低 {min_rr}（TP {tp:.1%} / SL {sl:.1%}）")
    if tp < _paper_min_tp:
        return _block(symbol, action_l, "min_tp",
                      f"止盈距离 {tp:.2%} < 最低 {_paper_min_tp:.1%}（paper放宽,"
                      f"扣除往返成本 {fee_ctx.roundtrip_cost_pct:.2%} 后无数学期望）")

    _pass_tags = []
    if is_auto_coin:
        _pass_tags.append("AI选币严选")
    if high_conviction and not is_auto_coin:
        _pass_tags.append("高置信放宽")
    _pass_tag = f" [{'/'.join(_pass_tags)}]" if _pass_tags else ""
    logger.info(
        "[V5Gate] PASS symbol=%s action=%s conf=%.0f%% nature=%s rr=%s%s",
        symbol, action_l, conf, nature_l,
        f"{tp / sl:.2f}" if (tp > 0 and sl > 0) else "n/a",
        _pass_tag,
    )
    return GateResult(
        allowed=True,
        rule="pass",
        adjustments={
            "effective_threshold": effective_threshold,
            "size_multiplier": size_multiplier,
        },
    )
