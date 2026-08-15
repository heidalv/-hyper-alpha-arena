"""MidLong v2 — Single Writer 开仓入口与 nature 归一。

设计见 docs/MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md：
- 同一时刻仅一个 authority（trend | mlto）可发中长线新开
- mid → trade_nature=swing（AI 中线槽位/绩效独立）；long → trend_follow
- V5 日配额仍由 unified_gate.normalize_v5_nature 把 swing 映射到 trend_follow 配额
- Phase2：Regime 路由接到 fuse；Trend hint 供 Hub bonus
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position", "mid", "long"})

# symbol → {should_open, direction, score, ts}
_TREND_HINT_CACHE: Dict[str, Dict[str, Any]] = {}
# symbol → {regime, ts}
_REGIME_CACHE: Dict[str, Dict[str, Any]] = {}
_HINT_TTL_SEC = 900.0


def get_midlong_exec_authority(trading_mode: Optional[str] = None) -> str:
    """返回 trend | mlto。显式 MIDLONG_EXEC_AUTHORITY 优先，否则回退旧开关。

    v6 第六章：paper 默认 mlto（AI thesis 说了算）；live 未显式配置时仍保守 trend。
    """
    try:
        import os

        from backend.config.settings import (
            MIDLONG_EXEC_AUTHORITY,
            MIDLONG_MLTO_CONTROLS_EXEC,
            PAPER_FAST_TRIAL,
        )
        auth = (MIDLONG_EXEC_AUTHORITY or "").strip().lower()
        if auth in ("trend", "mlto"):
            return auth
        if MIDLONG_MLTO_CONTROLS_EXEC:
            return "mlto"
        mode = (trading_mode or os.getenv("TRADING_MODE", "paper") or "paper").strip().lower()
        # paper / 快速试单：默认交还 MLTO；live 未显式配置仍用 trend
        if mode != "live" or bool(PAPER_FAST_TRIAL):
            return "mlto"
        return "trend"
    except Exception:
        return "mlto"


def normalize_midlong_nature(raw: Optional[str], tier: Optional[str] = None) -> str:
    """中长线执行层 nature 归一（保留 mid/swing 通道语义）。

    - scalp/intraday 保持
    - tier=mid 或 raw∈(swing,mid) → swing（AI 中线≤3 / 绩效归因）
    - position 保留为长线子类
    - 其余长线 → trend_follow

    V5 daily_cap：仍由 unified_gate.normalize_v5_nature 把 swing 映射到
    trend_follow 配额，避免 daily_cap=0；此处不再把 mid 抹成 long。
    """
    n = (raw or "").strip().lower()
    t = (tier or "").strip().lower()
    if n in ("scalp", "intraday"):
        return n
    if n == "position":
        return "position"
    if t == "mid" or n in ("swing", "mid"):
        return "swing"
    if t == "long" or n in ("trend_follow", "long", ""):
        return "trend_follow"
    if n in MIDLONG_NATURES:
        return "trend_follow"
    return "trend_follow"


def is_midlong_nature(raw: Optional[str]) -> bool:
    n = (raw or "").strip().lower()
    return n in MIDLONG_NATURES or n in ("trend_follow", "position", "swing")


def set_trend_hint(
    symbol: str,
    *,
    should_open: bool,
    direction: str,
    score: int = 0,
) -> None:
    sym = str(symbol or "").upper()
    if not sym:
        return
    _TREND_HINT_CACHE[sym] = {
        "should_open": bool(should_open),
        "direction": str(direction or "neutral").lower(),
        "score": int(score or 0),
        "ts": time.time(),
    }


def get_trend_hint(symbol: str) -> Optional[Dict[str, Any]]:
    sym = str(symbol or "").upper()
    row = _TREND_HINT_CACHE.get(sym)
    if not row:
        return None
    if time.time() - float(row.get("ts") or 0) > _HINT_TTL_SEC:
        return None
    return row


def get_cached_regime(symbol: str) -> str:
    sym = str(symbol or "").upper()
    row = _REGIME_CACHE.get(sym)
    if not row:
        return ""
    if time.time() - float(row.get("ts") or 0) > _HINT_TTL_SEC:
        return ""
    return str(row.get("regime") or "")


@dataclass
class MidLongIntent:
    symbol: str
    action: str  # buy|sell|hold
    authority: str  # trend|mlto
    confidence: int
    tp_pct: float
    sl_pct: float
    nature: str = "trend_follow"
    tier: str = "long"
    tranche_margin_pct: float = 1.0
    reason: str = ""
    hub_action: str = ""
    trend_score: int = 0
    regime: str = ""


def authority_allows_open(authority: str, source: str) -> bool:
    """source: trend | mlto | factor_route。仅当与当前 Single Writer 一致才允许开仓。

    [2026-08-15 因子化] authority=mlto（paper 默认）时同时放行 factor_route 中线新开：
    中线入场已由活跃因子信号驱动，与长线 mlto thesis 共用同一 Single Writer 配额/冷却。
    authority=trend（live 保守）时仍只放行 trend。
    """
    auth = (authority or get_midlong_exec_authority()).strip().lower()
    src = (source or "").strip().lower()
    if auth == "trend":
        return src == "trend"
    if auth == "mlto":
        return src in ("mlto", "factor_route")
    return False


def apply_regime_to_open(
    *,
    symbol: str,
    action: str,
    market_summary: Optional[dict],
    trading_mode: str = "paper",
    tranche_margin_pct: float = 1.0,
) -> Tuple[str, float, str, str]:
    """Regime 路由（R5）：返回 (action, margin_pct, regime, reason)。

    trend → 允许，size×1.0
    ranging → 默认禁止；Paper + ALLOW_RANGE_PROBE 时 size×0.25
    extreme → 禁止新开
    unknown → size×0.5
    """
    act = (action or "hold").lower()
    try:
        margin = float(tranche_margin_pct)
    except (TypeError, ValueError):
        margin = 1.0
    if margin <= 0:
        return "hold", 0.0, "", "margin_zero"
    sym_u = str(symbol or "").upper()
    ms = {}
    if isinstance(market_summary, dict):
        ms = market_summary.get(sym_u) or market_summary.get(symbol) or {}
        if not isinstance(ms, dict):
            ms = {}

    try:
        from backend.services.decision_core.regime_agent import classify_regime
        reg = classify_regime(ms)
        regime = (reg.regime or "unknown").strip().lower()
        reg_size = float(getattr(reg, "size_multiplier", 1.0) or 1.0)
    except Exception as exc:
        logger.debug("[MidLong] regime classify fail %s: %s", sym_u, exc)
        regime = "unknown"
        reg_size = 1.0

    _REGIME_CACHE[sym_u] = {"regime": regime, "ts": time.time()}
    is_paper = (trading_mode or "paper").strip().lower() == "paper"
    try:
        from backend.config.settings import MIDLONG_ALLOW_RANGE_PROBE, PAPER_FAST_TRIAL
        allow_range = bool(MIDLONG_ALLOW_RANGE_PROBE) and (is_paper or PAPER_FAST_TRIAL)
    except Exception:
        allow_range = is_paper

    if act not in ("buy", "sell"):
        return act, margin, regime, "not_entry"

    if regime == "extreme":
        logger.info(
            "[MidLong] stage=fuse symbol=%s regime=extreme action=hold reason=regime_block",
            sym_u,
        )
        return "hold", 0.0, regime, "regime_extreme"

    if regime == "ranging":
        if not allow_range:
            logger.info(
                "[MidLong] stage=fuse symbol=%s regime=ranging action=hold "
                "reason=range_block (ALLOW_RANGE_PROBE=false or live)",
                sym_u,
            )
            return "hold", 0.0, regime, "regime_ranging_block"
        # [P2-8] 统一 regime→size 口径：以 regime_agent.classify_regime 的
        # size_multiplier（ranging=0.5）为唯一权威，Probe 在其基础上再折半，
        # 消除原硬编码 0.25 与 classify_regime 0.5 的「双口径」漂移。
        margin = margin * reg_size * 0.5
        logger.info(
            "[MidLong] stage=fuse symbol=%s regime=ranging action=%s size×%.2f (probe)",
            sym_u, act, reg_size * 0.5,
        )
        return act, margin, regime, "regime_ranging_probe"

    if regime == "unknown":
        # [P2-8] 同上：使用 classify_regime 的 size_multiplier（unknown=0.75）
        margin = margin * reg_size
        logger.info(
            "[MidLong] stage=fuse symbol=%s regime=unknown action=%s size×%.2f",
            sym_u, act, reg_size,
        )
        return act, margin, regime, "regime_unknown_scale"

    # trend
    return act, margin, regime, "regime_trend_ok"


def execute_midlong_open(
    *,
    host,
    db,
    session,
    source: str,
    symbol: str,
    action: str,
    confidence: int,
    sl_pct: float,
    tp_pct: float,
    market_summary: dict,
    session_mode: str = "running",
    tier: str = "long",
    trade_nature: str = "trend_follow",
    tranche_margin_pct: float = 1.0,
    tp_sl_proposal: Optional[Dict[str, Any]] = None,
    invalidation_condition: str = "",
    expected_hold_hours: float = 0.0,
    reason: str = "",
    trading_mode: str = "paper",
    skip_regime: bool = False,
    thesis_dir: str = "",
    hub_dir: str = "",
    hub_mode: str = "",
    dir_src: str = "",
) -> bool:
    """唯一中长线新开 Writer 包装：authority 门禁 + regime + nature 归一 + 统一日志。"""
    auth = get_midlong_exec_authority()
    act = (action or "hold").lower()
    sym_u = str(symbol or "").upper()
    # None/缺省→1.0；显式 0 保留（WAIT/耗尽档），不静默放大
    if tranche_margin_pct is None:
        margin = 1.0
    else:
        try:
            margin = float(tranche_margin_pct)
        except (TypeError, ValueError):
            margin = 1.0
        if margin < 0:
            margin = 0.0

    def _record_fail(_reason: str, _regime: str = "") -> None:
        _sid = str(getattr(session, "session_id", "") or "")
        try:
            from backend.services.mlto.midlong_belief_loop import record_failed_intent
            record_failed_intent(
                symbol=sym_u,
                reason=_reason,
                regime=_regime,
                score=int(confidence or 0),
                authority=auth,
                source=source,
                session_id=_sid,
            )
        except Exception:
            pass
        try:
            from backend.services.mlto.midlong_direction_audit import (
                record_decision_audit,
            )
            record_decision_audit(
                outcome="skip",
                stage="writer",
                symbol=sym_u,
                reason=str(_reason or "writer_block"),
                session_id=_sid,
                tier=str(tier or ""),
                source=str(source or ""),
                authority=auth,
                action="hold",
                direction=hub_dir or "",
                score=int(confidence or 0),
                regime=_regime,
                mode=hub_mode or "",
            )
        except Exception:
            pass

    if act not in ("buy", "sell"):
        logger.info(
            "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold reason=%s",
            sym_u, auth, source, reason or "not_entry",
        )
        _record_fail(reason or "not_entry")
        return False

    if not authority_allows_open(auth, source):
        logger.info(
            "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
            "reason=authority_block (writer=%s)",
            sym_u, auth, source, auth,
        )
        _record_fail(f"authority_block writer={auth}")
        return False

    if margin <= 0:
        logger.info(
            "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
            "reason=margin_zero",
            sym_u, auth, source,
        )
        _record_fail("margin_zero")
        return False

    regime = ""
    if not skip_regime:
        _tm = trading_mode or "paper"
        if getattr(session, "trading_mode", None):
            _tm = str(getattr(session, "trading_mode") or _tm)
        elif getattr(session, "paper_account_id", None):
            _tm = "paper"
        act, margin, regime, reg_reason = apply_regime_to_open(
            symbol=sym_u,
            action=act,
            market_summary=market_summary,
            trading_mode=_tm or "paper",
            tranche_margin_pct=margin,
        )
        if act not in ("buy", "sell"):
            logger.info(
                "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
                "regime=%s reason=%s",
                sym_u, auth, source, regime, reg_reason,
            )
            _record_fail(reg_reason or "regime_block", regime)
            return False

    nature = normalize_midlong_nature(trade_nature, tier)
    # [P1] 保留 swing；勿再把 mid 抹成 trend_follow
    if nature not in ("trend_follow", "position", "swing"):
        nature = "trend_follow"
    # AI 中线单保留 tier=mid（分通道计数/槽位/风控）
    if (tier or "").lower() == "mid" or nature == "swing":
        exec_tier = "mid"
    else:
        exec_tier = "long"
    logger.info(
        "[MidLong] stage=exec symbol=%s authority=%s source=%s action=%s "
        "conf=%d nature=%s regime=%s margin=%.3f rr_hint=%.2f reason=%s",
        sym_u, auth, source, act, int(confidence or 0), nature, regime or "-",
        margin,
        (float(tp_pct) / float(sl_pct)) if float(sl_pct or 0) > 0 else 0.0,
        (reason or "")[:80],
    )

    from backend.services.full_auto.midlong_helpers import try_execute_independent_agent_open

    return bool(
        try_execute_independent_agent_open(
            db=db,
            session=session,
            sym=sym_u,
            tier=exec_tier,
            action=act,
            confidence=int(confidence or 0),
            sl_pct=float(sl_pct or 0),
            tp_pct=float(tp_pct or 0),
            trade_nature=nature,
            market_summary=market_summary or {},
            session_mode=session_mode,
            host=host,
            tp_sl_proposal=tp_sl_proposal,
            invalidation_condition=invalidation_condition,
            expected_hold_hours=float(expected_hold_hours or 0),
            tranche_margin_pct=float(margin),
            thesis_dir=thesis_dir,
            hub_dir=hub_dir,
            hub_mode=hub_mode,
            dir_src=dir_src,
            authority=auth,
        )
    )
