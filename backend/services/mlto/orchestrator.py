"""MLTO tick orchestrator."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from backend.config import settings
from backend.services.mlto import (
    debate_layer,
    decision_hub,
    evidence_ingest,
    layered_memory,
    open_gate,
    qual_layer,
    quant_layer,
    thesis_store,
    tranche_gate,
)
from backend.services.mlto.types import MltoTickResult, PerceptionPacket, ThesisDTO

logger = logging.getLogger(__name__)


class MltoOrchestrator:
    def __init__(self, persistence_state: Optional[dict] = None):
        self.persistence_state = persistence_state if persistence_state is not None else {}

    def run_tick(
        self,
        packet: PerceptionPacket,
        db=None,
        analyst_reports: Optional[dict] = None,
        portfolio: Optional[dict] = None,
    ) -> MltoTickResult:
        if not getattr(settings, "MIDLONG_THESIS_LEDGER_ENABLED", True):
            return MltoTickResult(action="hold", reason="MLTO disabled")

        thesis = thesis_store.get_or_create(
            packet.session_id, packet.symbol, packet.tier, packet.regime_hash, db=db,
        )
        if db is not None:
            from backend.services.mlto.learning_bridge import load_owm_weights
            thesis.owm_weights = load_owm_weights(packet.session_id, packet.tier, db)
        if thesis.regime_hash and packet.regime_hash and thesis.regime_hash != packet.regime_hash:
            if getattr(settings, "MIDLONG_THESIS_REGIME_RESET", True):
                thesis_store.apply_regime_reset(thesis, packet.regime_hash, db=db)

        new_events = evidence_ingest.ingest_tick(packet, thesis, db=db)
        # [中长线合并修复] LLM 长调用（90s+）前先提交 ingest 事务：连接若带着
        # 空闲事务进入 LLM 阶段，会被 PostgreSQL idle_in_transaction 超时掐断，
        # 导致 thesis 落库失败（"server closed the connection unexpectedly"）。
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        memory_events = layered_memory.retrieve(
            thesis.thesis_id, packet.tier, thesis.thesis_summary, k=8, db=db,
        )

        from backend.config.settings import MIDLONG_AI_MANDATORY

        _should_llm = packet.tier in ("mid", "long") and MIDLONG_AI_MANDATORY
        if not _should_llm:
            _should_llm = (
                bool(new_events)
                or (packet.slot_action or "") in ("create", "enter", "add", "observe")
                or int(thesis.review_count or 0) > 0
                or bool(thesis.thesis_summary)
            )
        if _should_llm:
            logger.info("[MLTO] LLM thesis_update %s %s slot=%s", packet.symbol, packet.tier, packet.slot_action)
            qual = qual_layer.update_thesis(packet, thesis, memory_events, new_events, db=db)
            if not qual.thesis_summary and (new_events or memory_events):
                qual.thesis_summary = _evidence_fallback_summary(packet, new_events, qual)
            if qual.thesis_summary or qual.direction:
                thesis_store.apply_llm_update(thesis, qual, db=db)
                thesis_store.append_event(
                    thesis.thesis_id,
                    "thesis_update",
                    {
                        "direction": qual.direction,
                        "conviction_delta": qual.conviction_delta,
                        "summary": (qual.thesis_summary or "")[:200],
                        "cited": qual.cited_event_ids[:8],
                    },
                    db=db,
                )
        else:
            logger.debug("[MLTO] skip LLM %s %s slot=%s", packet.symbol, packet.tier, packet.slot_action)

        quant_signals = quant_layer.compute(packet, thesis, db=db)
        debate_sig = None
        pre_hub = decision_hub.fuse_signals(
            quant_signals, packet.tier,
            owm_weights=thesis.owm_weights,
            mode=packet.trading_mode,
        )
        if debate_layer.should_debate(pre_hub.adjusted, packet.tier) and getattr(
            settings, "MIDLONG_THESIS_DEBATE_ENABLED", True
        ):
            debate_sig = debate_layer.run_debate(packet, memory_events, pre_hub.adjusted)
            if db is not None:
                debate_layer.persist_debate_log(
                    thesis.thesis_id, packet, memory_events, pre_hub.adjusted, debate_sig, db=db,
                )

        # MidLong v2：注入 Trend hint + regime，供 Hub bonus / unknown 收紧
        _trend_hint = None
        _regime_name = None
        try:
            from backend.services.full_auto.midlong_executor import (
                get_cached_regime,
                get_trend_hint,
            )
            _trend_hint = get_trend_hint(packet.symbol)
            _regime_name = get_cached_regime(packet.symbol) or None
            if not _regime_name:
                from backend.services.decision_core.regime_agent import classify_regime
                _ms = getattr(packet, "market_summary_sym", None) or {}
                if isinstance(_ms, dict) and _ms:
                    _regime_name = classify_regime(_ms).regime
        except Exception:
            pass
        hub = decision_hub.fuse_signals(
            quant_signals,
            packet.tier,
            debate_signal=debate_sig,
            owm_weights=thesis.owm_weights,
            trend_hint=_trend_hint,
            regime_name=_regime_name,
            mode=packet.trading_mode,
        )
        thesis_store.update_hub(thesis, hub, db=db)
        thesis_store.append_event(
            thesis.thesis_id, "hub_decision",
            {"hub": hub.reason_text, "adjusted": hub.adjusted, "action": hub.action},
            db=db,
        )

        has_pos = _has_position(packet, portfolio, db=db)

        # ── [Phase D 修复 Bug3] tranche 在外部平仓后从锁死档复位 ──
        # 背景：reset_tranche 此前只在 invalidation/should_close（本函数 close 分支）
        # 触发。但中长线仓位绝大多数通过 SL/TP/staged-TP/max_hold 在别处平仓
        #（master_execution / _run_midlong_active_exit），这些路径不感知 thesis，
        # 也不复位 tranche。后果：仓位平掉后 thesis.tranche_stage 停在 3，
        # compute_margin_pct 返回 0% → 永久锁死，再也无法重开。
        # 修复：当 stage≥3（耗尽档，配合 Fix2 只能由 3 次确认开仓到达）且当前无持仓时，
        # 判定仓位已被外部平掉，复位 tranche 使下一轮能重新分档建仓。
        # 为什么只在 stage≥3 复位、不在 stage 1/2 复位：
        #   - stage 1/2 时 has_pos=False 无法区分「仓位被平」和「开仓尚未确认/被拒」，
        #     贸然复位会打断正在进行的分档建仓（Fix2 之后 hold 不再推进，但开仓与持仓
        #     落库之间天然有 1 tick 延迟，中间 tick 会看到 stage=1 & 无仓）；
        #   - stage 1/2 下 compute_margin_pct 返回 30%/20%（非 0），本来就能继续开，
        #     不存在锁死，无需复位；
        #   - 真正的锁死只发生在 stage≥3（margin=0%），那里复位即可消除永久锁。
        if not has_pos and thesis.tranche_stage >= 3:
            logger.info(
                "[MLTO] tranche 复位 %s %s: stage %d → 0（stage≥3 且无持仓：仓位已被外部平仓 SL/TP/staged）",
                packet.symbol, packet.tier, thesis.tranche_stage,
            )
            thesis_store.append_event(
                thesis.thesis_id, "tranche_reset_on_close",
                {"prev_stage": thesis.tranche_stage, "reason": "external_close_no_position"},
                db=db,
            )
            tranche_gate.reset_tranche(thesis)
            if db is not None:
                from backend.services.mlto import thesis_store as _ts
                _ts._persist(db, thesis)

        # ── Step 9.5 (阶段3e + Phase A): invalidation 检查 → close ──
        # 两类触发源，都必须 has_pos=True 才发 close（避免幽灵平仓）：
        #   1. 价格类 invalidation: {"price", "operator"} → 机器每 tick 校验
        #   2. [Phase A 修复 Bug2] LLM should_close=true: thesis 完全失效（含叙事类
        #      invalidation，原来永远不触发 close，现由 LLM 在 thesis_update 驱动）。
        _close_trigger = None  # None | "price" | "should_close"
        if has_pos:
            if thesis.invalidation and _invalidation_triggered(thesis.invalidation, packet.price):
                _close_trigger = "price"
            elif getattr(thesis, "should_close", False):
                _close_trigger = "should_close"
        if _close_trigger:
            if _close_trigger == "price":
                _close_reason = (
                    f"invalidation_triggered: "
                    f"{thesis.invalidation.get('condition', '') or thesis.invalidation}"
                )
            else:
                _close_reason = "should_close: LLM 判定 thesis 完全失效"
            tranche_gate.reset_tranche(thesis)
            thesis_store.append_event(
                thesis.thesis_id, "invalidation_close",
                {"reason": _close_reason, "price": packet.price,
                 "trigger": _close_trigger,
                 "invalidation": thesis.invalidation},
                db=db,
            )
            # should_close 是一次性信号，触发后立即复位，避免下个 tick 重复平仓
            if _close_trigger == "should_close":
                thesis.should_close = False
            if db is not None:
                from backend.services.mlto import thesis_store as _ts
                _ts._persist(db, thesis)
            logger.info(
                "[MLTO] invalidation close %s %s @ %s | trigger=%s | %s",
                packet.symbol, packet.tier, packet.price, _close_trigger,
                thesis.invalidation.get("condition", "") if _close_trigger == "price" else "should_close",
            )
            return MltoTickResult(
                action="close",
                reason=f"[MLTO] {_close_reason}",
                thesis=thesis,
                hub=hub,
                confidence=thesis.open_readiness,
            )

        ok, gate_reason = open_gate.allow(thesis, hub, packet, self.persistence_state)
        if not ok:
            reason = f"[MLTO] {hub.reason_text} | gate: {gate_reason} | {thesis.thesis_summary[:80]}"
            thesis_store.append_event(thesis.thesis_id, "thesis_update", {"hold": gate_reason}, db=db)
            try:
                from backend.services.mlto.midlong_direction_audit import (
                    record_decision_audit,
                )
                record_decision_audit(
                    outcome="skip",
                    stage="gate",
                    symbol=packet.symbol,
                    reason=f"gate:{gate_reason}",
                    session_id=getattr(packet, "session_id", "") or "",
                    tier=packet.tier,
                    source="mlto",
                    action="hold",
                    hub_action=getattr(hub, "action", "") or "",
                    direction=getattr(hub, "direction", "") or "",
                    score=getattr(hub, "adjusted", None),
                    mode=getattr(hub, "mode", "") or "",
                    extra={"hub_reason": (getattr(hub, "reason_text", "") or "")[:120]},
                )
            except Exception:
                pass
            return MltoTickResult(
                action="hold",
                reason=reason,
                thesis=thesis,
                hub=hub,
                confidence=thesis.open_readiness,
            )

        margin_pct = tranche_gate.compute_margin_pct(thesis, hub, has_pos)
        action = hub.direction_to_action()
        mem_ids = [e.event_id for e in memory_events[:8]]

        # Paper NIBBLE 探针：方向由 hub soft lean 给出时，保证金再打折
        _dir_src = str(getattr(hub, "dir_src", "") or "")
        if _dir_src.startswith("nibble_probe") and action in ("buy", "sell"):
            try:
                from backend.config.settings import MIDLONG_NIBBLE_PROBE_MARGIN_MULT
                _pm = float(MIDLONG_NIBBLE_PROBE_MARGIN_MULT or 0.5)
            except Exception:
                _pm = 0.5
            _pm = max(0.15, min(1.0, _pm))
            margin_pct = float(margin_pct or 0) * _pm
            logger.info(
                "[MLTO] NIBBLE probe size×%.2f → margin=%.1f%% dir_src=%s %s",
                _pm, margin_pct * 100, _dir_src, packet.symbol,
            )

        # [2026-08-05 v6 6.3 第3项] LLM 止损参数直通：优先用 thesis.sl_pct
        #（LLM exit_plan，ATR 下限硬校验），structure_stops 降级为兜底。
        sl_pct, tp_pct = _llm_stops(thesis, packet, action)
        # [Phase D 修复 Bug2] tranche 只在「真正发出 buy/sell」时推进。
        # 此前 advance_tranche 在 open_gate 通过后无条件调用，但 gate 通过 ≠ 订单成交
        #（下游 budget/V5Gate/fixed-symbol/decision-price 门禁仍可拒单），且 hub 方向
        # 为 neutral 时 direction_to_action() 返回 "hold"——这种"过了门但没下单"的
        # tick 也会把 tranche 推到下一档。3 次拒单 → stage≥3 → compute_margin_pct=0%
        # → 永久锁死。改为仅在 action != "hold"（即真正尝试 buy/sell）时推进。
        if action != "hold":
            tranche_gate.advance_tranche(thesis)
        if db is not None:
            from backend.services.mlto import thesis_store as _ts
            _ts._persist(db, thesis)

        reason = f"[MLTO] {hub.action} {hub.direction} adj={hub.adjusted:.2f} tranche={thesis.tranche_stage} | {thesis.thesis_summary[:60]}"
        if _dir_src.startswith("nibble_probe"):
            reason = f"[MLTO][nibble_probe] {hub.action} {hub.direction} adj={hub.adjusted:.2f} | {thesis.thesis_summary[:50]}"
        thesis_store.append_event(
            thesis.thesis_id, "open_attempt",
            {"action": action, "margin_pct": margin_pct, "hub": hub.adjusted,
             "dir_src": _dir_src},
            db=db,
        )
        try:
            from backend.services.mlto.midlong_direction_audit import (
                record_decision_audit,
            )
            _oa_outcome = "open_attempt" if action in ("buy", "sell") else "skip"
            _oa_reason = (
                f"hub:{hub.action}"
                if action in ("buy", "sell")
                else f"hub_wait_or_neutral:{hub.action}:{hub.direction}"
            )
            if _dir_src.startswith("nibble_probe"):
                _oa_reason = f"nibble_probe_applied:{hub.action}:{hub.direction}"
            record_decision_audit(
                outcome=_oa_outcome,
                stage="hub",
                symbol=packet.symbol,
                reason=_oa_reason,
                session_id=getattr(packet, "session_id", "") or "",
                tier=packet.tier,
                source="mlto",
                action=action,
                hub_action=getattr(hub, "action", "") or "",
                direction=getattr(hub, "direction", "") or "",
                score=getattr(hub, "adjusted", None),
                mode=getattr(hub, "mode", "") or "",
                extra={
                    "margin_pct": margin_pct,
                    "tranche": thesis.tranche_stage,
                    "dir_src": _dir_src,
                },
            )
        except Exception:
            pass
        logger.info("[MLTO] %s %s %s → %s margin=%.0f%%", packet.symbol, packet.tier, hub.action, action, margin_pct * 100)

        return MltoTickResult(
            action=action,
            reason=reason,
            thesis=thesis,
            hub=hub,
            tranche_margin_pct=margin_pct,
            memory_event_ids=mem_ids,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            confidence=max(thesis.open_readiness, thesis.llm_conviction),
        )


def run_mlto_tick(
    session_id: str,
    symbol: str,
    tier: str,
    market_summary: dict,
    analyst_reports: dict,
    session,
    db=None,
    portfolio: Optional[dict] = None,
    persistence_state: Optional[dict] = None,
    slot_action: str = "",
    trading_mode: str = "paper",
) -> MltoTickResult:
    # ── 防御 Guard（2026-07-20 / 2026-07-23 统一守卫）──
    # 后端三处入口（mlto_cycle / master_execution / orch_background）均已做过滤，
    # 此处作为最后一道防线，确保 mid tier 和 AI 选币不会创建 thesis。
    from backend.config.settings import MIDLONG_MID_VIA_MLTO as _MID_VIA_MLTO
    if tier == "mid" and not _MID_VIA_MLTO:
        logger.info("[MLTO] skip mid tier thesis (MIDLONG_MID_VIA_MLTO=False) %s", symbol)
        return MltoTickResult(action="hold", reason="mid tier skipped")
    # [2026-07-23 修复] 替换 session.auto_coin_symbols 反向排除法（stale ORM 快照
    # 窗口期导致已退役 AI 选币漏进长线）。统一用 is_long_allowed 正向白名单。
    if tier == "long":
        from backend.services.auto_coin_selector import is_long_allowed
        _sid = getattr(session, "session_id", "") or ""
        if _sid and not is_long_allowed(symbol, _sid, db=db):
            logger.info("[MLTO] skip non-fixed symbol long thesis %s", symbol)
            return MltoTickResult(action="hold", reason="non-fixed symbol skipped for long")

    ms_sym = (market_summary or {}).get(symbol) or {}
    orch = (ms_sym.get("orchestrator") if isinstance(ms_sym, dict) else {}) or {}
    regime_hash = evidence_ingest.build_regime_hash(ms_sym if isinstance(ms_sym, dict) else {})

    pre_passed = True
    pre_reason = ""
    from backend.config.settings import MIDLONG_AI_MANDATORY
    if not MIDLONG_AI_MANDATORY:
        try:
            from backend.services.signal_pre_screener import SignalPreScreener
            scr = SignalPreScreener().screen_batch({symbol: ms_sym}, tier=tier)
            ps = (scr.results or {}).get(symbol)
            if ps and not ps.passed:
                pre_passed = False
                pre_reason = ps.trigger_reason or "no signal"
        except Exception:
            pass

    qb = {}
    try:
        from backend.services.mid_long_quant_brief import mid_long_quant_brief_builder
        from backend.services.swing_agent import derive_swing_side
        from backend.services.trend_agent import derive_trend_side
        side = derive_swing_side(symbol, market_summary) if tier == "mid" else derive_trend_side(symbol, market_summary)
        qb = mid_long_quant_brief_builder.build(symbol, ms_sym, orch, side).to_dict()
    except Exception:
        pass

    packet = PerceptionPacket(
        symbol=symbol,
        tier=tier,
        session_id=session_id,
        ts=time.time(),
        price=float(ms_sym.get("current_price") or ms_sym.get("price") or 0),
        market_summary_sym=ms_sym if isinstance(ms_sym, dict) else {},
        orchestrator=orch,
        quant_brief=qb,
        analyst_reports=analyst_reports or {},
        pre_screener_passed=pre_passed,
        pre_screener_reason=pre_reason,
        regime_hash=regime_hash,
        slot_action=slot_action,
        portfolio=portfolio or {},
        trading_mode=trading_mode,
        # [P2-1 修复] 模拟盘持仓实际挂在 paper_account_id 下（资金池账户），
        # 实盘才用 account_id。此前传 session.account_id → _has_position 查不到
        # open 仓位 → invalidation 平仓漏触发、tranche 可能误复位。
        account_id=(
            getattr(session, "paper_account_id", None)
            or getattr(session, "account_id", None)
        ),
    )
    orch_obj = MltoOrchestrator(persistence_state=persistence_state)
    return orch_obj.run_tick(packet, db=db, analyst_reports=analyst_reports, portfolio=portfolio)


def _evidence_fallback_summary(packet: PerceptionPacket, new_events, qual) -> str:
    """LLM 不可用时的规则摘要，避免面板长期空白。"""
    hints: list = []
    orch = packet.orchestrator or {}
    if packet.tier == "mid" and orch.get("mid_bias"):
        hints.append(f"编排器中向 {orch.get('mid_bias')}")
    elif packet.tier == "long" and orch.get("long_bias"):
        hints.append(f"编排器长向 {orch.get('long_bias')}")
    qb = packet.quant_brief or {}
    if qb.get("alignment_score") is not None:
        hints.append(f"QuantBrief 对齐 {qb.get('alignment_score')}/15")
    for ev in (new_events or [])[:2]:
        hints.append(getattr(ev, "summary", "")[:48])
    body = "；".join(x for x in hints if x) or "已摄入本轮市场证据"
    direction = getattr(qual, "direction", None) or "neutral"
    return f"[规则摘要·待 LLM 确认] {direction}：{body}"


def _has_position(packet: PerceptionPacket, portfolio: Optional[dict], db=None) -> bool:
    """检查当前 symbol 是否有未平仓持仓。

    [Phase A 修复 Bug1] 原实现只读 portfolio dict，但 portfolio 经常为 {} 或缺失
    该 symbol → has_pos=False → invalidation 静默跳过，即便 DB 里确有 open 仓位。
    现改为：优先查 DB（与 PositionCoordinator 同源），portfolio 仅作 DB 不可用时的兜底。
    """
    sym = packet.symbol.upper()
    # ── 主路径：直接查 DB 的 PaperPosition（不依赖 portfolio dict）──
    if db is not None:
        try:
            from backend.database.models import PaperPosition
            account_id = getattr(packet, "account_id", None)
            # account_id 缺失时无法精确查询，回退到 portfolio
            if account_id is not None:
                existing = db.query(PaperPosition).filter(
                    PaperPosition.account_id == account_id,
                    PaperPosition.symbol == sym,
                    PaperPosition.status == "open",
                ).first()
                return existing is not None
        except Exception:
            # DB 查询失败不阻塞决策，回退到 portfolio 兜底
            pass
    # ── 兜底：DB 不可用 / account_id 缺失 → 读 portfolio dict ──
    if not portfolio:
        return False
    positions = portfolio.get("positions") or portfolio.get("open_positions") or []
    for p in positions:
        if isinstance(p, dict) and str(p.get("symbol", "")).upper() == sym:
            return True
    return False


def _invalidation_triggered(invalidation: Optional[dict], current_price: float) -> bool:
    """[阶段3e] 价格类 invalidation 自动校验。

    两类 invalidation（决策3）:
    - 价格类: {"price": 60000, "operator": "<", "condition": "..."} → 机器每tick校验
      operator "<": 当前价 < price 触发；">": 当前价 > price 触发。
    - 叙事类: {"narrative": "趋势结构破坏"} → 返回 False, 由 LLM 在下个 tick 的
      thesis_update 中复评（qual_layer 每 tick 都会跑 LLM, 会重判 direction/conviction）。

    缺失 price / operator / price<=0 / current_price<=0 均视为无法机器判定 → False。
    """
    if not isinstance(invalidation, dict) or not invalidation:
        return False
    try:
        level = float(invalidation.get("price") or 0)
    except (TypeError, ValueError):
        return False
    if level <= 0:
        return False
    try:
        price = float(current_price or 0)
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    operator = str(invalidation.get("operator") or "").strip()
    if operator == "<":
        return price < level
    if operator == ">":
        return price > level
    if operator in ("<=", "≤"):
        return price <= level
    if operator in (">=", "≥"):
        return price >= level
    # operator 缺失 → 无法机器判定（可能是 narrative-only invalidation）
    return False


def _structure_stops(packet: PerceptionPacket, action: str) -> tuple:
    if action not in ("buy", "sell"):
        return 0.0, 0.0
    try:
        from backend.services.mid_long_structure_stop import mid_long_structure_stop
        side = "long" if action == "buy" else "short"
        entry = packet.price
        src = "swing_agent" if packet.tier == "mid" else "trend_agent"
        sl, tp, _, _, _ = mid_long_structure_stop.compute(
            symbol=packet.symbol,
            market_data=packet.market_summary_sym,
            side=side,
            entry=entry,
            agent_source=src,
        )
        return float(sl or 0), float(tp or 0)
    except Exception:
        return 0.08 if packet.tier == "mid" else 0.12, 0.20 if packet.tier == "mid" else 0.35


def _llm_stops(
    thesis: Optional[ThesisDTO], packet: PerceptionPacket, action: str
) -> tuple:
    """[2026-08-05 v6 6.3 第3项] LLM 止损参数直通（开仓用）。

    1. 优先：thesis.sl_pct（LLM exit_plan 解析落库）→ ATR 下限硬校验
       （apply_structure_atr_floor：SL 至少覆盖 ATR×1.5，防日噪音波扫）；
       TP 至少 2×SL（RR 兜底，与 midlong_helpers 对齐）。
    2. [v6 S2-7] regime 参数建议通道：校验后的 sl_multiplier 在 ATR floor 之后
       应用（≥1 放宽/≤1 收紧），最终仍 clamp 物理界限；trailing/addon_rhythm
       随 thesis.regime_suggestion 落库供执行层读取。
    3. 兜底：LLM 未提供（sl_pct=0）→ _structure_stops（结构止损）。
    4. 校验异常时降级为结构止损，绝不让非法 SL 直通执行层。
    """
    if action not in ("buy", "sell"):
        return 0.0, 0.0
    llm_sl = float(getattr(thesis, "sl_pct", 0) or 0)
    if llm_sl <= 0:
        return _structure_stops(packet, action)
    llm_tp = float(getattr(thesis, "tp_pct", 0) or 0)
    try:
        from backend.services.mlto.midlong_trade_design import (
            apply_structure_atr_floor,
            estimate_atr_1d_pct,
        )
        _ms = getattr(packet, "market_summary_sym", None) or {}
        if not isinstance(_ms, dict):
            _ms = {}
        _atr = estimate_atr_1d_pct(_ms)
        sl, why = apply_structure_atr_floor(sl_pct=llm_sl, atr_1d_pct=_atr)
        if "→" in str(why):
            logger.info(
                "[MLTO-LLMSL] %s %s %s", packet.symbol, packet.tier, why
            )
        # [v6 S2-7] regime sl_multiplier 应用（规则校验后，物理界限内）
        rs = getattr(thesis, "regime_suggestion", None)
        if isinstance(rs, dict) and rs.get("sl_multiplier"):
            try:
                from backend.services.mlto.regime_suggestion import (
                    apply_regime_params,
                    consume_sl_multiplier,
                )
                _ms2 = apply_regime_params(_ms, {"applied": rs})
                _sl2 = consume_sl_multiplier(_ms2, sl)
                if abs(_sl2 - sl) > 1e-9:
                    logger.info(
                        "[MLTO-S2-7] %s %s regime sl_multiplier=%.2f → SL %.4f→%.4f",
                        packet.symbol, packet.tier, rs.get("sl_multiplier"), sl, _sl2,
                    )
                    sl = _sl2
            except Exception as _rs_err:
                logger.debug("[MLTO-S2-7] %s sl_multiplier 应用失败: %s",
                             packet.symbol, _rs_err)
        tp = max(llm_tp, sl * 2.0)  # RR 兜底：TP 至少 2×SL
        # [v6 S2-7 接入] tp_trigger：TP 触发阈值（ATR 倍数），只允许把 TP 抬得
        # 更高，绝不低于 2×SL 的 RR 底线。
        _rs2 = getattr(thesis, "regime_suggestion", None)
        if isinstance(_rs2, dict) and _rs2.get("tp_trigger") and _atr:
            try:
                _tp_trig = float(_rs2.get("tp_trigger") or 0)
                if _tp_trig >= 1.0:
                    _tp_atr = float(_atr) * _tp_trig
                    if _tp_atr > tp:
                        logger.info(
                            "[MLTO-S2-7] %s %s regime tp_trigger=%.2f → TP %.4f→%.4f",
                            packet.symbol, packet.tier, _tp_trig, tp, _tp_atr,
                        )
                        tp = _tp_atr
            except Exception as _tp_err:
                logger.debug(
                    "[MLTO-S2-7] %s tp_trigger 应用失败: %s",
                    packet.symbol, _tp_err,
                )
        return sl, tp
    except Exception as _llm_sl_err:
        logger.warning(
            "[MLTO-LLMSL] %s %s 校验失败降级结构止损: %s",
            packet.symbol, packet.tier, _llm_sl_err,
        )
        return _structure_stops(packet, action)
