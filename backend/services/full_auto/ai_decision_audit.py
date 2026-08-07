"""AI 决策审核 — 从 monolith _validate_ai_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"hold", "buy", "sell", "close", "reduce", "pyramid", "dca"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass
class AiDecisionAuditHost:
    nature_to_tier_map: Dict[str, str]
    health_status: Dict[str, Any]
    last_unified_snapshot: Any = None
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    event_scope_label: Callable = field(repr=False, default=lambda *a, **k: "")


def build_ai_decision_audit_host(svc) -> AiDecisionAuditHost:
    return AiDecisionAuditHost(
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        health_status=svc._health_status,
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        append_event=svc._append_event,
        event_scope_label=svc._event_scope_label,
    )


def validate_ai_decisions(
    session,
    master_result: Dict,
    session_symbols: List[str],
    positions_list: List[Dict],
    host: AiDecisionAuditHost,
) -> Dict:
    if not master_result:
        return master_result

    audit_warnings = []
    audit_rejects = []

    # ── 1. 顶层结构完整性 ──
    overall = master_result.get("overall_assessment")
    risk_level = master_result.get("risk_level")
    decisions = master_result.get("decisions")

    if not overall or not isinstance(overall, str):
        audit_warnings.append("overall_assessment 缺失或非字符串")
        master_result["overall_assessment"] = master_result.get(
            "overall_assessment", "审核补充：AI 未提供市场总评")

    if risk_level not in VALID_RISK_LEVELS:
        audit_warnings.append(
            f"risk_level 值异常: '{risk_level}'，已修正为 medium")
        master_result["risk_level"] = "medium"

    if not decisions or not isinstance(decisions, list):
        audit_rejects.append("decisions 字段缺失或不是数组")
        host.append_event(session, "ai_audit_reject",
            f"🚫 AI 决策审核不通过：decisions 缺失，整体降级为 hold",
            severity="critical")
        master_result["decisions"] = [
            {"symbol": s, "action": "hold", "confidence": 0,
             "reasoning": "[审核拒绝] AI 输出结构异常，降级观望"}
            for s in session_symbols
        ]
        return master_result

    # ── 2. 逐条决策审核 ──
    valid_symbols_upper = {s.upper() for s in session_symbols}
    pos_map = {}
    for p in (positions_list or []):
        key = (p.get("symbol", "").upper(), p.get("timeframe_tier", "mid"))
        pos_map[key] = p

    cleaned_decisions = []

    # V3 §6.1: 构建编排器状态映射表（用于硬约束检查）
    _orch_action_map: Dict[str, str] = {}
    try:
        _lms = getattr(session, 'last_market_summary', None) or {}
        for _s, _info in _lms.items():
            if isinstance(_info, dict):
                _orch_a = (_info.get('orchestrator') or {}).get('action', '')
                if _orch_a:
                    _orch_action_map[_s.upper()] = _orch_a
    except Exception:
        pass

    for i, dec in enumerate(decisions):
        dec_issues = []

        # 字段存在性
        sym = dec.get("symbol", "")
        action = dec.get("action", "")
        confidence = dec.get("confidence")
        reasoning = dec.get("reasoning", "")

        # === V3 §6.1: 编排器硬约束层 ===
        _orch_act = _orch_action_map.get(sym.upper(), '') if sym else ''
        if _orch_act == 'frozen' and action in ('enter', 'buy', 'sell', 'open'):
            logger.warning(
                f"[V3硬约束] {sym} 编排器状态=frozen，拒绝AI的{action}决策")
            continue  # 跳过该决策，不加入 cleaned_decisions
        if _orch_act == 'wait' and action in ('enter', 'buy', 'sell', 'open'):
            _conf_val = confidence if confidence is not None else 0
            try:
                _conf_val = int(_conf_val)
            except (ValueError, TypeError):
                _conf_val = 0
            from backend.config.settings import ORCHESTRATOR_WAIT_OVERRIDE_CONF as _WAIT_OVR
            if _conf_val < _WAIT_OVR:
                logger.warning(
                    f"[V3硬约束] {sym} 编排器状态=wait，AI confidence={_conf_val}%<{_WAIT_OVR}%，拒绝开仓")
                continue
            else:
                logger.info(
                    f"[V3硬约束] {sym} 编排器wait但AI高信心({_conf_val}%≥{_WAIT_OVR}%)，允许覆盖")

        if not sym:
            dec_issues.append("symbol 缺失")
        elif sym.upper() not in valid_symbols_upper:
            dec_issues.append(f"symbol '{sym}' 不在会话交易对列表中")

        if action not in VALID_ACTIONS:
            dec_issues.append(f"action '{action}' 不合法")

        # 数值合理性
        if confidence is None:
            dec_issues.append("confidence 缺失")
            confidence = 0
        else:
            try:
                confidence = int(confidence)
            except (ValueError, TypeError):
                dec_issues.append(f"confidence '{confidence}' 无法解析为整数")
                confidence = 0

        if confidence < 0 or confidence > 100:
            dec_issues.append(f"confidence={confidence} 超出 [0,100] 范围")
            confidence = max(0, min(100, confidence))
        dec["confidence"] = confidence  # 确保 clamp 后的值写回

        if not reasoning or len(reasoning.strip()) < 5:
            dec_issues.append("reasoning 过短或缺失（AI 可能未认真分析）")

        # 逻辑一致性检查（持仓键与执行层一致：trade_nature → tier，.symbol 可兜底）
        trade_nature = dec.get("trade_nature") or "swing"
        tier_eff = host.nature_to_tier_map.get(
            trade_nature, dec.get("tier", "mid"))
        if isinstance(tier_eff, str):
            tier_eff = (tier_eff or "mid").strip().lower()
        else:
            tier_eff = "mid"
        pos_key = (sym.upper(), tier_eff)
        has_position = pos_key in pos_map
        if not has_position and sym:
            for (pk_sym, pk_tier), _ in pos_map.items():
                if pk_sym == sym.upper():
                    pos_key = (pk_sym, pk_tier)
                    has_position = True
                    break
        scope_lbl = host.event_scope_label(trade_nature, tier_eff)

        if action in ("close", "reduce", "pyramid", "dca") and not has_position:
            dec_issues.append(
                f"action='{action}' 但 {sym}[{scope_lbl}] 无对应持仓")
            action = "hold"
            dec["action"] = "hold"
            dec["confidence"] = 0

        if action in ("buy", "sell") and confidence > 90:
            dec_issues.append(
                f"新开仓 confidence={confidence} 异常高(>90)，可能过度自信")

        # 部分平仓比例检查
        partial_pct = dec.get("partial_close_pct")
        if partial_pct is not None:
            try:
                partial_pct = int(partial_pct)
                if partial_pct < 0 or partial_pct > 100:
                    dec_issues.append(
                        f"partial_close_pct={partial_pct} 超出范围")
            except (ValueError, TypeError):
                dec_issues.append(
                    f"partial_close_pct 无法解析: {partial_pct}")

        # TP/SL 合理性
        adj_tp = dec.get("adjust_tp")
        adj_sl = dec.get("adjust_sl")
        if has_position and adj_tp is not None:
            try:
                tp_val = float(adj_tp)
                pos = pos_map[pos_key]
                entry = float(pos.get("entry_price", 0))
                if entry > 0 and tp_val > 0:
                    tp_dist = abs(tp_val - entry) / entry
                    if tp_dist > 0.5:
                        dec_issues.append(
                            f"adjust_tp={tp_val} 距离入场价>{50}%，可能不合理")
            except (ValueError, TypeError):
                pass
        if has_position and adj_sl is not None:
            try:
                sl_val = float(adj_sl)
                pos = pos_map[pos_key]
                entry = float(pos.get("entry_price", 0))
                if entry > 0 and sl_val > 0:
                    sl_dist = abs(sl_val - entry) / entry
                    if sl_dist > 0.5:
                        dec_issues.append(
                            f"adjust_sl={sl_val} 距离入场价>{50}%，可能不合理")
            except (ValueError, TypeError):
                pass

        # 汇总
        if dec_issues:
            severity = "warning"
            # 严重问题导致降级
            if any(kw in " ".join(dec_issues) for kw in
                   ["缺失", "不合法", "不在会话"]):
                severity = "critical"
                dec["action"] = "hold"
                dec["confidence"] = 0
                dec["reasoning"] = (
                    f"[审核降级] {'; '.join(dec_issues)} | 原: {reasoning[:60]}")
                audit_rejects.append(f"{sym}[{scope_lbl}]: {'; '.join(dec_issues)}")
            else:
                audit_warnings.append(f"{sym}[{scope_lbl}]: {'; '.join(dec_issues)}")
        else:
            dec["confidence"] = confidence
            dec["action"] = action

        cleaned_decisions.append(dec)

    # ── 3. 异常模式检测 ──
    if len(cleaned_decisions) >= 2:
        actions = [d.get("action") for d in cleaned_decisions]
        confs = [d.get("confidence", 0) for d in cleaned_decisions]

        # 全部同方向开仓（不太合理）
        buy_count = sum(1 for a in actions if a in ("buy", "pyramid"))
        sell_count = sum(1 for a in actions if a in ("sell",))
        if buy_count == len(actions) and buy_count >= 3:
            audit_warnings.append(
                f"所有{buy_count}个决策都是买入方向，可能缺乏独立思考")
        if sell_count == len(actions) and sell_count >= 3:
            audit_warnings.append(
                f"所有{sell_count}个决策都是卖出方向，可能缺乏独立思考")

        # 置信度全部相同（复制粘贴嫌疑）
        if len(set(confs)) == 1 and len(confs) >= 3 and confs[0] != 0:
            audit_warnings.append(
                f"所有决策置信度完全相同({confs[0]})，AI 可能未逐个分析")

    # ── 3.5 按 (symbol, trade_nature) 去重：同一 symbol 不同 nature 可并存 ──
    _sym_best: Dict[tuple, dict] = {}
    for dec in cleaned_decisions:
        _nature = (dec.get("trade_nature") or "").strip().lower() or "swing"
        _dk = (dec.get("symbol", "").upper(), _nature)
        _existing = _sym_best.get(_dk)
        if not _existing:
            _sym_best[_dk] = dec
        else:
            _ec = _existing.get("confidence", 0)
            _nc = dec.get("confidence", 0)
            if _nc > _ec:
                _sym_best[_dk] = dec
    if len(_sym_best) < len(cleaned_decisions):
        _dup_count = len(cleaned_decisions) - len(_sym_best)
        audit_warnings.append(
            f"去重: {_dup_count}条重复(symbol,nature)决策被合并(保留最高置信度)")
        cleaned_decisions = list(_sym_best.values())

    master_result["decisions"] = cleaned_decisions

    # ── 4. 发出审核事件 ──
    if audit_rejects:
        host.append_event(session, "ai_audit_reject",
            f"🚫 AI 决策审核拦截({len(audit_rejects)}条): "
            + " | ".join(audit_rejects[:3]),
            severity="critical")
        logger.warning(
            f"[FullAuto] AI 审核拦截 {len(audit_rejects)}条: "
            + "; ".join(audit_rejects))

    if audit_warnings:
        host.append_event(session, "ai_audit_warning",
            f"⚠️ AI 决策审核提醒({len(audit_warnings)}条): "
            + " | ".join(audit_warnings[:3]),
            severity="warning")
        logger.info(
            f"[FullAuto] AI 审核提醒 {len(audit_warnings)}条: "
            + "; ".join(audit_warnings))

    # 存入健康状态供外部查询
    host.health_status["rejected_decisions"] = audit_rejects[-10:]

    total_issues = len(audit_rejects) + len(audit_warnings)
    if total_issues == 0:
        host.append_event(session, "ai_audit_pass",
            f"✅ AI 决策审核通过({len(cleaned_decisions)}条决策，结构完整/数值合理)")

    try:
        from backend.config.settings import STRICT_DATA_GATE
        if STRICT_DATA_GATE:
            from backend.services.data_readiness_gate import strip_open_actions
            _ms_audit = getattr(session, "last_market_summary", None) or {}
            master_result["decisions"] = strip_open_actions(
                cleaned_decisions,
                snapshot=getattr(self, "_last_unified_snapshot", None),
                market_summary=_ms_audit if isinstance(_ms_audit, dict) else {},
                reason_prefix="审核后数据门控",
            )
        else:
            master_result["decisions"] = cleaned_decisions
    except Exception:
        master_result["decisions"] = cleaned_decisions

    return master_result

    # ══════════════════════════════════════════════════
    #  Phase 1D: QAA 多智能体调度方法
    # ══════════════════════════════════════════════════

    _qaa_agents_registered: bool = False
