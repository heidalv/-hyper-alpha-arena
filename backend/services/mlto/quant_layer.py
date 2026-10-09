"""Deterministic quant signals (85% weight)."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from backend.services.mlto.types import PerceptionPacket, Signal, ThesisDTO

logger = logging.getLogger(__name__)

#: [F366 2026-09-18] 是否让 MLTO 量化层**服从**因子衰减/退役治理。**默认关**。
#:
#: 为什么需要：本次复查 §15.1 查实一处**单向街**——中长线把复检 IC 写进衰减监视器
#: （`midlong_active_factor_set.py:344` `record_ic`），却**从不读**惩罚
#: （`get_factor_weight_penalty`）⇒ "中长线喂养治理，却不服从治理"。
#: 设计文档 §15 的最小接线③也点名要把惩罚接进 `mlto/quant_layer.py`。
#: 置 1 后：4h 因子锚按各因子惩罚系数加权（与被惩罚的因子治理口径一致，
#: 与 `factor_evaluation_pipeline.py:220` 同源同义）；未开启时**逐字节保持旧行为**。
_ENV_DECAY = "MLTO_DECAY_PENALTY_ENABLED"
_DECAY_LOGGED: set = set()


#: [2026-09-18 根因修复] 不可执行的方向是否仍参与投票。默认 **0=不参与**（新行为）。
#: 置 1 即回滚到旧行为（不可执行的 short 照旧按 ≤0.45 投票）。
_ENV_UNEXEC_VOTE = "MLTO_LLM_UNEXEC_VOTE"


def _short_direction_unexecutable(symbol: str, tier: str) -> bool:
    """该标的的**空头**方向在当前的 regime 闸下是否必然被拒？

    与 `full_auto/midlong_circuit_gate` 的判定**同源**（`_short_mode()` + `_daily_regime()`），
    而不是另写一套口径：
      · `short` tier 走自己的路径，不受本闸约束 ⇒ 返回 False；
      · `MIDLONG_SHORT_MODE=regime_gated`（默认）且日线 regime ≠ down（含 unknown）⇒ 必被拒；
      · 其它模式（off/conditional）或取不到判定 ⇒ 返回 False（**不改变**旧行为）。

    fail-safe 方向：任何异常都返回 False（宁可照旧投票，也不静默改变评分口径）。
    """
    if str(os.getenv(_ENV_UNEXEC_VOTE, "0")).strip().lower() in ("1", "true", "yes", "on"):
        return False  # 回滚开关
    try:
        _t = str(tier or "").strip().lower()
        if _t in ("short", "scalp", "intraday"):
            return False
        from backend.services.full_auto.midlong_circuit_gate import (
            _daily_regime,
            _short_mode,
        )
        if str(_short_mode()) != "regime_gated":
            return False
        _reg = str(_daily_regime(str(symbol or "").upper()) or "").strip().lower()
        return _reg != "down"
    except Exception:  # noqa: BLE001
        return False


def _decay_enabled() -> bool:
    return str(os.getenv(_ENV_DECAY, "0")).strip().lower() in ("1", "true", "yes", "on")


def _decay_penalty_fn() -> Callable[[str], float]:
    """返回 `factor_id -> 惩罚系数`。未开启 ⇒ 恒 1.0（旧行为）。

    fail-safe（**不是** fail-open 到"满权"）：取不到惩罚时保持 1.0 不动该因子，
    但只对**异常**如此；正常路径下 retire→0.0、reduce→≤0.3 会真实生效。
    """
    if not _decay_enabled():
        return lambda _fid: 1.0

    def _pen(fid: str) -> float:
        if not fid:
            return 1.0
        try:
            from backend.services.factor_engine.factor_decay_monitor import decay_monitor
            v = float(decay_monitor.get_factor_weight_penalty(fid))
            if v != v or v < 0.0:      # NaN / 负数 ⇒ 不动
                return 1.0
            return v
        except Exception:
            return 1.0

    return _pen


def _log_decay_applied_once(symbol: str, period: str, n_penalized: int, n_total: int) -> None:
    """惩罚真正生效时留一条日志（同 symbol 一次），避免"生效了但没人知道"。"""
    key = str(symbol or "-")
    if key in _DECAY_LOGGED:
        return
    _DECAY_LOGGED.add(key)
    logger.info(
        "[MLTOQuant] 因子衰减惩罚已作用于 %s 因子锚: %s %d/%d 个因子被降权/退役",
        period, key, n_penalized, n_total,
    )


#: [F367 2026-09-18] 因子锚的周期。**原实现写死 "4h"**，造成两处错位：
#:   - `TIER_CONFIG` 真值是 short=15m / **mid=1h** / **long=4h**，而锚点分支在 tier
#:     判断**之外** ⇒ mid 车道被喂了不属于它的 4h 锚；long 车道也永远只有 4h，
#:     **拿不到 1d/1w**（而中长线活跃因子集里确有 `@1d` 因子，如 `obv@1d`）。
#:   - 信号名因此写死 `factor_anchor_4h`，`decision_hub.WEIGHTS_MID/LONG` 里都没有这个名字
#:     ⇒ 落到 `weights.get(s.name, 0.01)` 的 **0.01 兜底**（×置信度 0.5 ≈ 0.5% 权重），
#:     即"打开开关也几乎不影响 composite"。
#: 现改为**按 tier 取该 tier 的默认周期**（可用 `MLTO_FACTOR_ANCHOR_PERIOD` 覆盖）。
#: 该分支默认关（`FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED=false`）⇒ 今日行为零变化。
def _anchor_period(tier: str) -> str:
    v = str(os.getenv("MLTO_FACTOR_ANCHOR_PERIOD", "") or "").strip().lower()
    if v:
        return v
    try:
        from backend.services.strategy_params_registry import TIER_CONFIG
        return str((TIER_CONFIG.get(str(tier or ""), {}) or {}).get("default_timeframe") or "4h")
    except Exception:
        return "4h"


def _bias_value(bias: str) -> float:
    b = (bias or "neutral").lower()
    if b in ("bullish", "long", "buy"):
        return 1.0
    if b in ("bearish", "short", "sell"):
        return 0.0
    return 0.5


def compute(packet: PerceptionPacket, thesis: ThesisDTO, db=None) -> List[Signal]:
    signals: List[Signal] = []
    orch = packet.orchestrator or {}
    qb = packet.quant_brief or {}
    tier = packet.tier

    if tier == "mid":
        conf = float(orch.get("mid_confidence") or orch.get("mid_conf") or 0)
        signals.append(
            Signal("orch_mid_bias", _bias_value(orch.get("mid_bias")), min(1.0, conf + 0.2), "framework")
        )
    else:
        conf = float(orch.get("long_confidence") or orch.get("long_conf") or 0)
        signals.append(
            Signal("orch_long_bias", _bias_value(orch.get("long_bias")), min(1.0, conf + 0.2), "framework")
        )

    align = int(qb.get("alignment_score") or 0)
    signals.append(Signal("quant_alignment", min(1.0, align / 15.0), 0.9, "framework"))

    timing = _entry_timing(packet.market_summary_sym)
    signals.append(Signal("entry_timing", timing, 0.9, "framework"))

    health = _thesis_health(db, packet.symbol, tier)
    signals.append(Signal("thesis_health", health, 0.85, "framework"))

    consensus = _analyst_consensus(packet.analyst_reports, packet.symbol)
    signals.append(Signal("analyst_consensus", consensus, 0.7, "framework"))

    fb = _feedback_loop(thesis)
    signals.append(Signal("feedback_loop", fb, 0.8, "framework"))

    # [P5-修复] LLM 方向语义化映射：llm_conviction 的 clamp(0,100) 使 0 与
    # "从未评级"不可区分——LLM 持续 neutral 时 conviction_delta=0 → conviction 恒 0，
    # 但 review_count 每次 LLM 调用都 +1（含失败空响应），旧判据
    # `review_count<=0 and llm_conviction==0` 永假 → 中性被映射成 0.0（极度看空）
    # → ai_governed 下 direction 恒 short、composite 被拉低 → 长线 0 开仓。
    # 现在以 thesis.direction（orchestrator 在 quant 之前已写入 LLM 最新方向）为
    # 真语义：neutral → 0.5（不贡献方向，落入 _orch_bias_direction 兜底）；
    # long/short → 映射后夹取到方向侧，conviction=0 不再反噬方向。
    # 夹取边界 0.55/0.45 与 decision_hub._derive_direction 的 ai_governed 映射精确对齐。
    _d = (thesis.direction or "neutral").lower()
    # [2026-09-18 根因修复·不可执行方向不投票]
    # 实测（近 3h，133 条主脑决策）：short 100 / long 25 / neutral 8，且单一标的
    # VIRTUAL 占 80 条 —— 它们**必然**被 midlong_short_regime_block 拒（日线非 down）。
    # 但旧逻辑仍把 short 夹到 llm_qual≤0.45 ⇒ 在 ai_governed（权重 0.60）下持续
    # **压低 composite** ⇒ 该币连合规的多头提案也被拖成 WAIT。
    # 口径：**方向不可执行时，其观点不参与投票**（等价中性），而不是"投反对票"。
    # 注意与上一条修复的分工：提示词负责"别写不可执行的方向"（治生成），
    # 这里负责"即便写了也不让它绑架评分"（治耦合）。回滚：MLTO_LLM_UNEXEC_VOTE=1。
    if _d == "short" and _short_direction_unexecutable(packet.symbol, tier):
        _d = "neutral"
    if _d == "neutral":
        llm_v = 0.5
    else:
        llm_v = 0.5 + (thesis.llm_conviction - 50) / 100.0
        llm_v = max(llm_v, 0.55) if _d == "long" else min(llm_v, 0.45)
    # [阶段3b] confidence 0.5→0.85：让 LLM 全权重（0.30）真正生效。
    # 旧值 0.5 使有效权重=0.30×0.5=0.15（半折），LLM 研判被系统性低估。
    # LLM thesis 是经过推理的研判，0.85 的"信号可靠度"合理（vs 规则信号 0.8-0.9）。
    signals.append(Signal("llm_qual", max(0.0, min(1.0, llm_v)), 0.85, "llm"))

    # [阶段2] 中周期择时信号（来自长线 thesis 嵌入的 mid_view 子结构）。
    # 仅当 mid_view 存在且 timing_score>0 时产出；权重在阶段3 decision_hub 配置。
    # backward-compatible: mid_view=None → 不产出此信号（现状）。
    # [M13 2026-08-21] DEPRECATED：mid_view 生产端停用后此信号不再新增产出，
    # 仅为存量 thesis 数据保留读取路径。
    if thesis.mid_view and thesis.mid_view.timing_score:
        signals.append(
            Signal(
                "mid_timing",
                thesis.mid_view.timing_score / 100.0,
                0.8,
                "framework",
            )
        )

    # M9 中长线因子锚：4h 因子暴露矩阵修正（开关默认关）
    import os as _os
    if _os.getenv("FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    ):
        try:
            from backend.services.factor_engine.exposure_service import (
                factor_exposure_service,
            )
            # [F367] 周期按 tier 取（mid→1h / long→4h），不再写死 4h
            _period = _anchor_period(packet.tier)
            _exp = factor_exposure_service.exposure(packet.symbol, _period, 200)
            if _exp:
                # [F366] 惩罚按**因子粒度**作用于其 alpha 贡献（未开启时 _pen 恒 1.0 ⇒ 与旧式
                # `sum(expected_alpha)` 完全等价；开启后 retired 因子贡献 0、reduce 因子降权）。
                _pen = _decay_penalty_fn()
                _alpha = 0.0
                _n_pen = 0
                for e in _exp:
                    _p = _pen(str(e.get("factor_id") or ""))
                    if _p != 1.0:
                        _n_pen += 1
                    _alpha += _p * float(e.get("expected_alpha") or 0)
                if _n_pen:
                    _log_decay_applied_once(packet.symbol, _period, _n_pen, len(_exp))
                _anchor_v = max(-1.0, min(1.0, _alpha * 20.0))
                # [F367] 信号名随周期变化；`decision_hub.fuse_signals` 已按
                # `factor_anchor_` 前缀识别权重（否则会落到 0.01 兜底）。
                signals.append(Signal(f"factor_anchor_{_period}", _anchor_v, 0.5, "quant"))
        except Exception:
            pass

    return signals


def _entry_timing(ms: Dict[str, Any]) -> float:
    ind = ms.get("indicators_4h") if isinstance(ms.get("indicators_4h"), dict) else {}
    if not ind or ind.get("rsi") is None:
        ind = ms.get("indicators_1h") if isinstance(ms.get("indicators_1h"), dict) else {}
    rsi = ind.get("rsi")
    if rsi is None:
        return 0.5
    rsi = float(rsi)
    if rsi < 35:
        return 0.85
    if rsi < 45:
        return 0.65
    if rsi > 70:
        return 0.25
    if rsi > 60:
        return 0.4
    return 0.55


def _thesis_health(db, symbol: str, tier: str) -> float:
    if db is None:
        return 0.5
    try:
        from backend.database.models import StrategyTrade
        from backend.services.unified_learning_service import TIER_TO_NATURE
        nature = TIER_TO_NATURE.get(tier, "swing")
        rows = (
            db.query(StrategyTrade)
            .filter(StrategyTrade.symbol == symbol.upper())
            .order_by(StrategyTrade.closed_at.desc().nullslast())
            .limit(20)
            .all()
        )
        if not rows:
            return 0.5
        wins = sum(1 for r in rows if float(r.pnl or 0) > 0)
        return wins / len(rows)
    except Exception:
        return 0.5


def _analyst_consensus(reports: Dict[str, Any], symbol: str) -> float:
    if not isinstance(reports, dict):
        return 0.5
    votes = []
    for key in ("technical", "risk", "macro"):
        rep = reports.get(key)
        if not isinstance(rep, dict):
            continue
        sym_data = rep.get(symbol) or rep.get(symbol.upper()) or rep
        if isinstance(sym_data, dict):
            bias = str(sym_data.get("bias") or sym_data.get("direction") or "neutral").lower()
            votes.append(_bias_value(bias))
        elif isinstance(rep, dict) and rep.get("overall_bias"):
            votes.append(_bias_value(rep.get("overall_bias")))
    if not votes:
        return 0.5
    return sum(votes) / len(votes)


def _feedback_loop(thesis: ThesisDTO) -> float:
    if not thesis.owm_weights:
        return 0.5
    return min(1.0, max(0.0, sum(thesis.owm_weights.values()) / max(len(thesis.owm_weights), 1) / 1.2))
