"""UnifiedExitExecutor — 分 Tier 统一 AI 退出门控与执行（P1，2026-06）。

Tier 0: PEO / SL / TP / max_hold_timeout — 不经本模块（规则直通）
Tier 1: trend_review / hold_timeout / scalp_fast — 轻量 Agent 门控
Tier 2: master_running* / ai_take_profit / defensive — 完整 P3 + SL v6 + 保护期
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# [阶段3f] Hardfat Shadow 并行（决策13）
# 阶段3e 让 AI invalidation 驱动退出,hardfact 降为底线。灰度发布期间
# 两者并行:hardfat 仍 EVALUATE+LOG,但不拦截;AI invalidation 驱动实际退出。
# 验证后(retreat to底线)再决定是否把 hardfact 完全退到底线。
#   false(默认,当前行为) = enforce:hardfact 拦截 AI close/reduce
#   true = shadow:hardfat 只记日志不拦,AI exit 照常执行
# 运行时读取(非 import 期),便于测试与灰度切换。
# ─────────────────────────────────────────────────────────────────────
def _hardfat_shadow_enabled() -> bool:
    return os.getenv("RISK_P3_HARDFAT_SHADOW", "false").lower() in ("1", "true", "yes")


@dataclass
class ExitGateResult:
    blocked: bool
    event_type: str = ""
    detail: str = ""
    convert_to_set_sl: bool = False
    emergency_sl: float = 0.0


@dataclass
class ExitExecuteRequest:
    db: Any
    account_id: int
    symbol: str
    action: str  # close | reduce
    pos: dict
    exit_channel: str
    reason: str = ""
    reasoning: str = ""
    confidence: Optional[float] = None
    reduce_ratio: float = 0.5
    reduce_qty: Optional[float] = None
    mode: str = "running"
    tier_level: Optional[int] = None
    session: Any = None
    append_event: Optional[Callable] = None
    get_risk_score: Optional[Callable] = None
    tier_protection: Optional[dict] = None


class UnifiedExitExecutor:
    """统一退出执行器。"""

    def resolve_tier(self, exit_channel: str, explicit: Optional[int] = None) -> int:
        if explicit is not None:
            return int(explicit)
        from backend.services.master_close_guard import route_exit_tier
        return route_exit_tier(exit_channel)

    def check_position_protection(
        self,
        pos: dict,
        action: str,
        tier_protection: dict,
        append_event: Optional[Callable] = None,
        session: Any = None,
        sym: str = "",
    ) -> ExitGateResult:
        """Layer A: 新仓保护期（tier 1/2 共用）。"""
        tier = pos.get("timeframe_tier", "mid")
        tier_cfg = tier_protection.get(tier) or tier_protection.get("mid") or {
            "protect_min": 30, "emergency_pct": -5.0,
        }
        protect_min = tier_cfg.get("protect_min", 30)
        emergency_pct = tier_cfg.get("emergency_pct", -5.0)
        tier_label = {"short": "短线", "mid": "中线", "long": "长线"}.get(tier, "中线")

        opened_at_str = pos.get("opened_at") or ""
        if not opened_at_str:
            return ExitGateResult(blocked=False)

        try:
            opened_at = datetime.fromisoformat(str(opened_at_str).replace("Z", "+00:00"))
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
        except (ValueError, TypeError):
            return ExitGateResult(blocked=False)

        if age_minutes >= protect_min:
            return ExitGateResult(blocked=False)

        margin = float(pos.get("margin", 0) or 0)
        upnl = float(pos.get("unrealized_pnl", 0) or 0)
        pnl_pct = (upnl / margin * 100) if margin > 0 else 0

        if pnl_pct <= emergency_pct:
            logger.warning(
                "[UnifiedExit] %s 保护期内紧急放行 %s: age=%.0fmin loss=%.1f%%",
                sym, action, age_minutes, pnl_pct,
            )
            return ExitGateResult(blocked=False)

        detail = (
            f"🛡️ {tier_label}保护 {sym}: 开仓{age_minutes:.0f}分钟"
            f"(保护期{protect_min}分钟)，忽略{action} | "
            f"浮盈亏{pnl_pct:+.1f}%未触及紧急阈值{emergency_pct}%"
        )
        if append_event and session:
            append_event(session, "position_protected", detail)
        return ExitGateResult(blocked=True, event_type="position_protected", detail=detail)

    def check_sl_v6_hard_block(
        self,
        pos: dict,
        action: str,
        sym: str = "",
    ) -> ExitGateResult:
        """Tier 2 only: SL 未深度穿透且小亏时禁止 AI close。"""
        if action != "close":
            return ExitGateResult(blocked=False)

        _has_sl = bool(pos.get("sl_price") or pos.get("stop_loss"))
        _sl_price = float(pos.get("sl_price") or pos.get("stop_loss") or 0)
        _entry = float(pos.get("entry_price", 0) or 0)
        _mark = float(pos.get("mark_price", 0) or _entry)
        _margin = float(pos.get("margin", 0) or 1)
        _upnl = float(pos.get("unrealized_pnl", 0) or 0)
        _loss_pct = abs(_upnl / _margin * 100) if _margin > 0 else 0
        _is_losing = _upnl < 0

        _sl_breach_ratio = 0.0
        if _has_sl and _entry > 0 and _sl_price > 0 and _is_losing:
            sl_distance = abs(_entry - _sl_price)
            cur_distance = abs(_entry - _mark)
            if sl_distance > 0:
                _sl_breach_ratio = cur_distance / sl_distance

        _sl_hard_block = _has_sl and _sl_breach_ratio < 0.5 and _loss_pct < 2.5
        if _sl_hard_block:
            detail = (
                f"🔒 {sym} 有SL保护(穿透{_sl_breach_ratio:.2f}<0.5)，AI禁止close，"
                f"让SL管理 | 亏{_loss_pct:.1f}%"
            )
            return ExitGateResult(
                blocked=True, event_type="close_blocked_by_sl", detail=detail,
            )

        if not _has_sl and _is_losing and _loss_pct < 15:
            return ExitGateResult(
                blocked=True,
                event_type="close_to_sl",
                detail=f"{sym} 无SL亏{_loss_pct:.1f}%, 应设紧急SL代替平仓",
                convert_to_set_sl=True,
            )

        return ExitGateResult(blocked=False)

    def check_hardfact_gate(
        self,
        req: ExitExecuteRequest,
        tier_level: int,
    ) -> ExitGateResult:
        pos = req.pos
        _pos_tier = (pos.get("timeframe_tier") or pos.get("tier") or "mid").strip().lower()
        _entry_p = float(pos.get("entry_price", 0) or 0)
        _mark_p = float(pos.get("mark_price", 0) or _entry_p)
        _sl_p = pos.get("sl_price") or pos.get("stop_loss")
        _sl_p_f = float(_sl_p) if _sl_p else None
        _upnl = float(pos.get("unrealized_pnl", 0) or 0)
        _margin = float(pos.get("margin", 0) or 0)
        _risk = 50.0
        if req.get_risk_score:
            try:
                _risk = float(req.get_risk_score(req.account_id))
            except Exception:
                pass

        reason_hint = f"{req.exit_channel} {req.reason} {req.reasoning}"

        if tier_level == 1:
            from backend.services.master_close_guard import check_agent_exit_hardfact
            hf = check_agent_exit_hardfact(
                tier=_pos_tier, action=req.action,
                entry_price=_entry_p, mark_price=_mark_p,
                sl_price=_sl_p_f, unrealized_pnl=_upnl, margin=_margin,
                risk_score=_risk, reason_hint=reason_hint,
                exit_channel=req.exit_channel,
                opened_at=pos.get("opened_at"),
            )
            if hf.allow:
                return ExitGateResult(blocked=False, detail=hf.detail)
            # [阶段3f] hardfat shadow:AI invalidation 驱动退出,hardfat 降底线
            if _hardfat_shadow_enabled():
                logger.info(
                    "[HardfatShadow] Tier1 hardfat would block %s %s[%s] %s: %s — shadow passthrough",
                    req.symbol, req.action, _pos_tier, req.exit_channel, hf.detail[:120],
                )
                return ExitGateResult(
                    blocked=False,
                    event_type="hardfat_shadow_passthrough",
                    detail=f"👁️ shadow:hardfat 意见=拦截({hf.detail[:60]}),已放行 AI exit",
                )
            return ExitGateResult(
                blocked=True,
                event_type="agent_exit_blocked",
                detail=f"🛡️ Tier1 {req.symbol}[{_pos_tier}] {req.action}: {hf.detail}",
            )

        from backend.config.settings import RISK_P3_ENABLED, RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT
        from backend.services.master_close_guard import (
            check_master_close_hardfact, decide_by_flag,
        )

        if not RISK_P3_ENABLED or RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT == "off":
            return ExitGateResult(blocked=False)

        from backend.services.master_close_guard import check_master_min_hold_block

        _mh = check_master_min_hold_block(
            tier=_pos_tier,
            opened_at=pos.get("opened_at"),
            margin=_margin,
            unrealized_pnl=_upnl,
            action=req.action,
        )
        if not _mh.allow:
            return ExitGateResult(
                blocked=True,
                event_type="min_hold_protection",
                detail=f"🛡️ {_mh.detail}",
            )

        _eff_flag = RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT
        try:
            from backend.services.paper_pace_controller import paper_pace_controller
            _pace = paper_pace_controller.get_knobs().master_close_mode
            if "shadow" in (_eff_flag, _pace):
                _eff_flag = "shadow"
        except Exception:
            pass

        # [2026-07-12 修复] turbo/warm 档 master_close_mode=shadow 时，reduce 硬事实
        # 只记日志不拦截 → 总控 AI 每 30s 就能对小亏短线仓减仓，减仓后碎仓清理再触发
        # 短线重开，形成用户看到的「不停开仓平仓」循环。短线 reduce 历史胜率 ~5%，
        # 必须强制 enforce，不受 pace shadow 降级影响。
        if req.action == "reduce" and _pos_tier == "short":
            _eff_flag = "enforce"

        # [三周期持仓时间收敛 2026-08-13] mid/long 的 master hardfact 从 shadow
        # 收敛为 enforce（短线保持 shadow 灰度，仅 reduce 已有 enforce 先例）。
        # 根因: turbo 档 pace.master_close_mode=shadow 会把 _eff_flag 降为 shadow，
        # swing 仓 12h min_hold 过后 hardfact 拦截意见仍不生效。注意:
        # RISK_P3_HARDFAT_SHADOW=true 灰度期间 Tier2 hardfact 意见仍只记日志
        # （见下方 _hardfat_shadow_enabled 分支），此收敛保证灰度结束后立即生效。
        if _pos_tier in ("mid", "long"):
            _eff_flag = "enforce"

        hf = check_master_close_hardfact(
            tier=_pos_tier, action=req.action,
            entry_price=_entry_p, mark_price=_mark_p,
            sl_price=_sl_p_f, unrealized_pnl=_upnl, margin=_margin,
            risk_score=_risk, reason_hint=reason_hint,
        )
        should_block, audit_tag = decide_by_flag(hf, _eff_flag)

        if should_block and req.confidence is not None:
            try:
                from backend.services.close_guard_calibrator import high_conf_close_bypass
                if high_conf_close_bypass(req.confidence):
                    should_block = False
                    audit_tag = "master_close_high_conf_bypass"
            except Exception:
                pass

        if should_block:
            # [阶段3f] hardfat shadow:灰度期间 hardfat 只记日志不拦截,
            # AI invalidation(阶段3e)驱动实际退出,便于对比两者触发分布。
            if _hardfat_shadow_enabled():
                logger.info(
                    "[HardfatShadow] Tier2 hardfat would block %s %s[%s] %s (%s): %s — shadow passthrough",
                    req.symbol, req.action, _pos_tier, req.exit_channel,
                    audit_tag or "", hf.detail[:120],
                )
                return ExitGateResult(
                    blocked=False,
                    event_type="hardfat_shadow_passthrough",
                    detail=f"👁️ shadow:hardfat 意见=拦截({hf.detail[:60]}),已放行 AI exit",
                )
            evt = audit_tag or "master_close_blocked_no_hardfact"
            return ExitGateResult(
                blocked=True,
                event_type=evt,
                detail=f"🛡️ P3.M1 {req.symbol}[{_pos_tier}] {req.action}: {hf.detail}",
            )
        return ExitGateResult(blocked=False, detail=hf.detail)

    def should_block(self, req: ExitExecuteRequest) -> ExitGateResult:
        # S0-6：mid/long 对 Master 软退出免疫（此前函数写了但未接线）
        try:
            _pos_tier = (
                (req.pos or {}).get("timeframe_tier")
                or (req.pos or {}).get("tier")
                or ""
            )
            _pos_tier = str(_pos_tier).strip().lower()
            if _pos_tier in ("mid", "long"):
                from backend.services.risk_band_resolver import (
                    is_close_reason_blocked_for_midlong,
                )
                _candidates = [
                    str(req.exit_channel or "").strip().lower(),
                    str(req.reason or "").strip().lower(),
                ]
                if req.action == "close":
                    _candidates.append("master_running_close")
                    _candidates.append("master_running")
                if req.action == "reduce":
                    _candidates.append("master_running_reduce")
                    _candidates.append("master_defensive_reduce")
                for _r in _candidates:
                    if not _r:
                        continue
                    if is_close_reason_blocked_for_midlong(_r, _pos_tier):
                        return ExitGateResult(
                            blocked=True,
                            event_type="midlong_soft_exit_immune",
                            detail=(
                                f"🛡️ {_pos_tier} 免疫软退出 channel={req.exit_channel} "
                                f"reason={_r}"
                            ),
                        )
        except Exception as _imm_err:
            logger.debug("[UnifiedExit] midlong immune check skip: %s", _imm_err)

        tier = self.resolve_tier(req.exit_channel, req.tier_level)
        if tier == 0:
            return ExitGateResult(blocked=False)

        tp = req.tier_protection or {}
        prot = self.check_position_protection(
            req.pos, req.action, tp,
            append_event=req.append_event, session=req.session, sym=req.symbol,
        )
        if prot.blocked:
            return prot

        hf = self.check_hardfact_gate(req, tier)
        if hf.blocked:
            return hf

        if tier == 2:
            sl = self.check_sl_v6_hard_block(req.pos, req.action, req.symbol)
            if sl.blocked:
                return sl

        return ExitGateResult(blocked=False)

    def execute(self, req: ExitExecuteRequest) -> Optional[dict]:
        """门控 + 执行 close/reduce。blocked 时返回 None。"""
        try:
            from backend.config.settings import UNIFIED_EXIT_EXECUTOR_ENABLED
            if not UNIFIED_EXIT_EXECUTOR_ENABLED:
                return self._execute_raw(req)
        except Exception:
            pass

        gate = self.should_block(req)
        if gate.blocked:
            if req.append_event and req.session and gate.event_type:
                req.append_event(req.session, gate.event_type, gate.detail)
            logger.info(
                "[UnifiedExit] blocked %s %s channel=%s: %s",
                req.symbol, req.action, req.exit_channel, gate.detail[:120],
            )
            if gate.convert_to_set_sl:
                self._set_emergency_sl(req)
            return None

        return self._execute_raw(req)

    def _set_emergency_sl(self, req: ExitExecuteRequest) -> None:
        pos = req.pos
        sym = req.symbol
        entry_p = float(pos.get("entry_price", 0) or 0)
        if entry_p <= 0:
            return
        side = pos.get("side", "long")
        _emerg_sl_dist = 0.05
        if side in ("long", "buy"):
            emergency_sl = round(entry_p * (1 - _emerg_sl_dist), 6)
        else:
            emergency_sl = round(entry_p * (1 + _emerg_sl_dist), 6)
        try:
            from backend.services.paper_trading_engine import paper_engine
            pos_id = pos.get("id")
            if pos_id:
                paper_engine.update_position_tp_sl(
                    req.db, pos_id, sl_price=emergency_sl,
                )
                if req.append_event and req.session:
                    req.append_event(
                        req.session, "close_to_sl",
                        f"🔧 {sym}: 无SL，设紧急SL=${emergency_sl:.2f} 代替平仓",
                    )
        except Exception as e:
            logger.warning("[UnifiedExit] 设紧急SL失败 %s: %s", sym, e)

    def _execute_raw(self, req: ExitExecuteRequest) -> Optional[dict]:
        from backend.services.paper_trading_engine import paper_engine

        pos = req.pos
        sym = req.symbol
        side = pos.get("side", "long")
        account_id = req.account_id
        strategy_id = pos.get("strategy_id")
        mark = float(pos.get("mark_price", 0) or pos.get("entry_price", 0) or 0)

        if req.action == "close":
            _reason = req.exit_channel or req.reason
            _inv = (pos.get("metadata") or pos.get("position_metadata") or {})
            if isinstance(_inv, dict):
                _ae = _inv.get("agent_envelope") or {}
                if req.reason and "invalidation" in str(req.reason).lower():
                    _reason = f"thesis_invalidation:{_reason}"
                elif isinstance(_ae, dict) and _ae.get("thesis_id") and "invalidation" in str(req.reasoning or "").lower():
                    _reason = f"thesis_invalidation:{_reason}"
            return paper_engine.close_position(
                req.db, account_id, sym, side,
                reason=_reason,
                strategy_id=strategy_id,
                fill_price_override=mark if mark > 0 else None,
            )

        if req.action == "reduce":
            size = float(pos.get("size", 0) or pos.get("quantity", 0) or 0)
            qty = req.reduce_qty
            if qty is None:
                qty = size * float(req.reduce_ratio or 0.5)
            if qty <= 0:
                return None
            return paper_engine.close_position(
                req.db, account_id, sym, side,
                quantity=qty,
                reason=req.exit_channel or req.reason,
                strategy_id=strategy_id,
                fill_price_override=mark if mark > 0 else None,
            )

        return None


unified_exit_executor = UnifiedExitExecutor()
