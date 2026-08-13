"""Open gate — 风险底线门控（阶段3b 精简版）。

历史：曾含 readiness 硬地板 / reviews 最低数 / pre_screener / 方向一致性投票
等多重闸门，实测导致 AI 决策几乎无法穿透到执行（中长线长期开不出单）。
阶段3b 按 plan §5.3 + decision 9 精简为 5 条风险底线：AI should_open 默认放行，
仅在 direction≠neutral / 数据完整 / 固定交易对 / recommend_open / hub action
合法这 5 条底线上否决。杠杆/破产红线、最大同向持仓由执行层 place_order 兜底。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from backend.config import settings
from backend.services.mlto.types import HubDecision, PerceptionPacket, ThesisDTO

logger = logging.getLogger(__name__)


def _thresholds(tier: str) -> tuple:
    """阈值仅用于 describe_gate_status 展示（allow 不再依赖 readiness/reviews）。"""
    min_readiness = (
        int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_LONG", 78) or 78)
        if tier == "long"
        else int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_MID", 72) or 72)
    )
    min_reviews = int(getattr(settings, "MIDLONG_THESIS_MIN_REVIEWS", 3) or 3)
    # stable_sec / persist_ticks / stale_max 历史上用于已删除的 persistence/新鲜度闸门，
    # 此处保留返回结构以维持向后兼容（describe 仍读 min_readiness/min_reviews）。
    stable_sec = (
        int(getattr(settings, "MIDLONG_THESIS_STABLE_MIN_SEC_LONG", 7200) or 7200)
        if tier == "long"
        else int(getattr(settings, "MIDLONG_THESIS_STABLE_MIN_SEC_MID", 1800) or 1800)
    )
    persist_ticks = max(1, int(getattr(settings, "MIDLONG_PERSISTENCE_TICKS", 2) or 2))
    stale_max = int(getattr(settings, "MIDLONG_THESIS_STALE_MAX_SEC", 120) or 120)
    return min_readiness, min_reviews, stable_sec, persist_ticks, stale_max


def describe_gate_status(
    thesis: ThesisDTO,
    hub: HubDecision,
    packet: PerceptionPacket,
    persistence_state: dict | None = None,
) -> Dict[str, Any]:
    """UI/API：开单门控分解（只读，不修改 persistence_state）。

    [阶段3b] 与 allow() 对齐——只展示 5 条风险底线的状态。
    """
    tier = packet.tier
    min_readiness, min_reviews, _stable_sec, _persist_ticks, _stale_max = _thresholds(tier)
    checks: List[Dict[str, Any]] = []

    # 底线 1：方向
    checks.append({
        "key": "direction",
        "ok": hub.direction != "neutral",
        "label": f"方向 {hub.direction}",
    })

    # 底线 2：数据完整性
    data_missing = _critical_data_missing(packet)
    checks.append({
        "key": "data_complete",
        "ok": not data_missing,
        "label": "数据完整" if not data_missing else f"数据缺失: {data_missing}",
    })

    # 底线 3：固定交易对边界
    _auto_coin = packet.tier == "long" and _is_auto_coin(packet)
    checks.append({
        "key": "fixed_symbol",
        "ok": not _auto_coin,
        "label": "固定交易对" if not _auto_coin else f"auto-coin {packet.symbol} 长线拦截",
    })

    # 底线 4：LLM recommend_open
    _rec = thesis.recommend_open
    if _rec is False:
        _rec_ok, _rec_label = False, "LLM recommend_open=False"
    elif _rec is True:
        _rec_ok, _rec_label = True, "LLM recommend_open=True"
    else:
        _rec_ok, _rec_label = True, "LLM 未明确 recommend_open（放行）"
    checks.append({"key": "recommend_open", "ok": _rec_ok, "label": _rec_label})

    # 底线 5：hub action
    _hub_ok = hub.action in ("BUILD", "NIBBLE", "WAIT")
    checks.append({
        "key": "hub_action",
        "ok": _hub_ok,
        "label": f"Hub 动作 {hub.action}",
    })

    pending = [c["label"] for c in checks if not c["ok"]]
    can_open = all(c["ok"] for c in checks) and getattr(settings, "MIDLONG_THESIS_OPEN_GATE", True)
    summary = "已达开单门控" if can_open else ("还需: " + " · ".join(pending[:4]))
    return {
        "can_open": can_open,
        "checks": checks,
        "summary": summary,
        "min_readiness": min_readiness,
        "min_reviews": min_reviews,
    }


def allow(
    thesis: ThesisDTO,
    hub: HubDecision,
    packet: PerceptionPacket,
    persistence_state: dict,
) -> Tuple[bool, str]:
    """[阶段3b] 开单门控 = 5 条风险底线。

    旧版多闸（readiness 硬地板 / reviews 最低数 / pre_screener / 方向一致性投票）
    实测导致 AI 决策几乎无法穿透到执行。按计划 §5.3 + decision 9 精简：
    AI 的 should_open（hub action / recommend_open）默认驱动开仓，本函数仅在
    以下 5 条风险底线上否决：

      1. direction != neutral（不开中性方向）
      2. 数据完整性（关键市场证据缺失 → hold）
      3. 固定交易对边界（auto-coin 绝不开长线；与 midlong_helpers 守卫纵深防御）
      4. recommend_open 尊重（LLM 明确说不建议开仓 → 拦截）
      5. hub action 合法（WAIT/NIBBLE/BUILD；neutral 已由 #1 兜底）

    杠杆/破产红线、最大同向持仓数等执行层硬限由 place_order / execute_proposal
    兜底，不在本函数重复（避免规则分层混乱）。
    """
    if not getattr(settings, "MIDLONG_THESIS_OPEN_GATE", True):
        # 总开关关闭：仍守住"不开中性"底线。
        if hub.direction == "neutral":
            return False, "direction neutral"
        return hub.action in ("BUILD", "NIBBLE", "WAIT"), "open_gate disabled"

    _soft = ""

    # ── 底线 1：方向不能是 neutral ──
    if hub.direction == "neutral":
        return False, "direction neutral"

    # ── 底线 2：数据完整性（关键市场证据缺失 → hold）──
    data_missing = _critical_data_missing(packet)
    if data_missing:
        return False, f"data incomplete: {data_missing}"

    # ── 底线 3：固定交易对边界（auto-coin 绝不进长线）──
    # 与 midlong_helpers.try_execute_independent_agent_open 的守卫纵深防御：
    # 那里是开仓执行终点守卫，这里是 MLTO 门控前置守卫，两层任一拦截即可。
    if packet.tier == "long" and _is_auto_coin(packet):
        return False, "auto-coin rejected by fixed-symbol floor"

    # ── 底线 4：尊重 LLM 的 recommend_open=False（AI 自己说不建议开仓）──
    # thesis.recommend_open：None=LLM 未明确给出（放行）；False=LLM 明确拒绝。
    # Paper 探针例外：Hub 已 NIBBLE/BUILD 且有方向时，recommend_open=False 只 soft
    # （否则审计大量 NIBBLE/long 被本门一票否决，探针永远进不了 Writer）。
    if thesis.recommend_open is False:
        _probe_soft_rec = False
        try:
            from backend.config.settings import MIDLONG_NIBBLE_PROBE_ENABLED
            _mode = str(getattr(packet, "trading_mode", "") or "paper").lower()
            if (
                bool(MIDLONG_NIBBLE_PROBE_ENABLED)
                and _mode != "live"
                and hub.action in ("NIBBLE", "BUILD")
                and hub.direction in ("long", "short")
            ):
                _probe_soft_rec = True
        except Exception:
            _probe_soft_rec = False
        if _probe_soft_rec:
            logger.info(
                "[OpenGate] recommend_open=False soft_pass (nibble_probe) %s %s/%s",
                packet.symbol, hub.action, hub.direction,
            )
            _soft = ((_soft + " | ") if _soft else "") + "recommend_open_false_soft"
        else:
            return False, "llm recommend_open=False"

    # ── 底线 5：hub action 合法（非空、非 hold-only）──
    if hub.action not in ("BUILD", "NIBBLE", "WAIT"):
        return False, f"hub_action={hub.action}"

    # ── P1 底线 6（v6 松绑）：chop 不得否决/翻转 AI 方向 —— 仅 soft_warning ──
    if packet.tier == "long":
        try:
            from backend.services.mlto.midlong_trade_design import is_chop_regime
            _chop, _chop_why = is_chop_regime(
                packet.market_summary_sym or {},
                packet.orchestrator or {},
            )
            if _chop:
                _soft = ((_soft + " | ") if _soft else "") + f"chop_soft:{_chop_why}"
                logger.info(
                    "[OpenGate] chop soft_warning (no veto) %s: %s",
                    packet.symbol, _chop_why,
                )
        except Exception as _chop_err:
            logger.debug("[OpenGate] chop check skip: %s", _chop_err)

    # ── v3.1.0 短线遵循软约束：short_overlay 强冲突（conf≥0.6 且 2h 内）
    # BUILD → NIBBLE 降档（只缩仓位，不否决 AI 方向），reasoning 由 LLM 侧说明。
    if hub.action == "BUILD" and hub.direction in ("long", "short"):
        try:
            _ms = packet.market_summary_sym or {}
            _ov = _ms.get("short_overlay")
            if isinstance(_ov, dict) and _ov.get("direction"):
                _ov_dir = str(_ov.get("direction")).lower()
                _ov_conf = float(_ov.get("confidence") or 0)
                _ov_age = float(_ov.get("age_sec") or 0)
                _opp = {"long": "short", "short": "long"}.get(hub.direction)
                if _ov_dir == _opp and _ov_conf >= 0.6 and _ov_age <= 7200:
                    hub.action = "NIBBLE"
                    _soft = (
                        f"short_overlay_conflict:BUILD->NIBBLE("
                        f"{_ov_dir} conf={_ov_conf:.2f} age={int(_ov_age)}s)"
                    )
                    logger.info(
                        "[OpenGate] short_overlay 冲突降档 %s: %s",
                        packet.symbol, _soft,
                    )
        except Exception as _ov_err:
            logger.debug("[OpenGate] short_overlay check skip: %s", _ov_err)

    # ── P1 底线 7：资金费率清算级净 RR（物理风险，仍可硬拒）──
    if packet.tier == "long" and hub.action in ("NIBBLE", "BUILD") and hub.direction in ("long", "short"):
        try:
            from backend.services.mlto.midlong_trade_design import funding_net_rr_ok
            ms = packet.market_summary_sym or {}
            atr = None
            try:
                from backend.services.mlto.midlong_trade_design import estimate_atr_1d_pct
                atr = estimate_atr_1d_pct(ms)
            except Exception:
                atr = None
            _sl = float(atr or 0.03) * 1.5
            _tp = max(_sl * 2.0, float(atr or 0.03) * 3.0)
            _act = "buy" if hub.direction == "long" else "sell"
            _ok, _nrr, _why = funding_net_rr_ok(
                action=_act,
                tp_pct=_tp,
                sl_pct=_sl,
                funding_rate=ms.get("funding_rate"),
            )
            if not _ok:
                return False, f"funding_rr: {_why}"
        except Exception as _fr_err:
            logger.debug("[OpenGate] funding check skip: %s", _fr_err)

    return True, (_soft or "ok")


def _critical_data_missing(packet: PerceptionPacket) -> str:
    """关键市场证据缺失判定。返回缺失项描述；无缺失返回空串。

    判定标准（保守、只挡真正开不了单的情况）：
      - symbol 为空（thesis 本身不合法）
      - market_summary_sym 完全为空（连价格都没有）
      - price <= 0（无法计算 SL/TP/仓位）
    orchestrator/quant_brief 等可降级字段不视为关键缺失（LLM 可在缺数据时
    自行 hold，不需要门控强制）。
    """
    if not (packet.symbol or "").strip():
        return "symbol empty"
    ms = packet.market_summary_sym or {}
    if not isinstance(ms, dict) or not ms:
        return "market_summary empty"
    if float(getattr(packet, "price", 0) or 0) <= 0:
        return "price<=0"
    return ""


def _is_auto_coin(packet: PerceptionPacket) -> bool:
    """符号是否为 AI 选币（非长线白名单）。

    复用 auto_coin_selector.get_fixed_symbols_for_session 正向白名单：
    tier=long 且 symbol 不在固定白名单 → 视为 auto-coin → 拦截。
    任何异常（查询失败/无 session）都放行（容错优先，由执行层守卫兜底）。
    """
    try:
        from backend.services.auto_coin_selector import get_fixed_symbols_for_session
        _session_id = getattr(packet, "session_id", None)
        if not _session_id:
            return False
        _fixed = get_fixed_symbols_for_session(_session_id, tier="long")
        if not _fixed:
            # 白名单为空（未配置/查询异常）→ 不拦截，交给执行层守卫。
            return False
        return (packet.symbol or "").upper() not in _fixed
    except Exception as exc:
        logger.debug("[OpenGate] fixed-symbol 检查异常跳过 %s: %s", packet.symbol, exc)
        return False
