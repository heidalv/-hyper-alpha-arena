"""MidLong v2 — Single Writer 开仓入口与 nature 归一。

设计见 docs/MIDLONG_V2_ARCHITECTURE_DESIGN_2026-08-02.md：
- 同一时刻仅一个 authority（trend | mlto）可发中长线新开
- mid → trade_nature=swing（AI 中线槽位/绩效独立）；long → trend_follow
- V5 日配额仍由 unified_gate.normalize_v5_nature 把 swing 映射到 trend_follow 配额
- Phase2：Regime 路由接到 fuse；Trend hint 供 Hub bonus
"""
from __future__ import annotations

import logging
import os
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


def authority_allows_open(authority: str, source: str, trading_mode: Optional[str] = None) -> bool:
    """source: trend | mlto | factor_route。仅当与当前 Single Writer 一致才允许开仓。

    [2026-08-15 因子化] authority=mlto（paper 默认）时同时放行 factor_route 中线新开：
    中线入场已由活跃因子信号驱动，与长线 mlto thesis 共用同一 Single Writer 配额/冷却。
    authority=trend（live 保守）时仍只放行 trend。

    [验收轮3 2026-09-14] 脑开启时的 factor_route 放行：M5 A/B 车道（
    MIDLONG_MID_FACTOR_ROUTE_AB=true + paper）下允许 factor_route 与脑并行开仓
    （entry_source 独立记账）；此前脑开启恒拦 factor_route（实测 09:37 每轮
    `authority_block`，A/B 车道白开）。live 与 AB=false 保持旧行为。
    """
    auth = (authority or get_midlong_exec_authority()).strip().lower()
    src = (source or "").strip().lower()
    if auth == "trend":
        return src == "trend"
    if auth == "mlto":
        try:
            from backend.config.settings import midlong_brain_enabled
            if midlong_brain_enabled():
                if src == "mlto":
                    return True
                if src == "factor_route" and (trading_mode or "paper").strip().lower() == "paper":
                    import os as _os_ab
                    return (_os_ab.getenv("MIDLONG_MID_FACTOR_ROUTE_AB", "true") or "true"
                            ).strip().lower() in ("1", "true", "yes", "on")
                return False
        except Exception:
            pass
        return src in ("mlto", "factor_route")
    return False


def _swing_consensus_gate(
    symbol: str,
    action: str,
    market_summary: Optional[dict],
) -> Tuple[bool, str, float]:
    """swing 入场多周期共识闸（2026-09-01 逆势止血）。

    中线波段近 14 天 -11.5（胜率 31%），平仓原因大头是 trend_broken /
    bias_reversal——与 4h+1d 双周期共识反向开仓、随后被趋势碾过。持仓侧已有
    _rule_direction（双周期反向才平），但那是事后；这里用**同一套 _tf_vote**
    前置到入场：
      - 4h 与 1d 双周期共识同时反向 → 禁开（这是"逆着趋势抄底摸顶"，不是波段）
      - 单周期反向 → 缩仓 ×SWING_COUNTER_ONE_TF_SIZE_MULT（默认 0.5；
        保留"回调顺大周期买入"这类合法波段形态）
      - 数据缺失/票数平 → fail-open 放行（与其余闸门一致，不误杀）
    SWING_COUNTER_TREND_GATE=false 关闭。仅作用于 nature=swing；
    trend_follow/position 是当前正盈利引擎，不受影响。
    """
    try:
        _enabled = (os.getenv("SWING_COUNTER_TREND_GATE", "true") or "true").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if not _enabled:
            return True, "swing_ct_gate_off", 1.0
        from backend.services.full_auto.midlong_position_manager import _tf_vote
        side = "short" if (action or "").lower() == "sell" else "long"
        pos_dir = 1 if side == "long" else -1
        sym_u = str(symbol or "").upper()
        ms = {}
        if isinstance(market_summary, dict):
            ms = market_summary.get(sym_u) or market_summary.get(symbol) or {}
            if not isinstance(ms, dict):
                ms = {}
        i4 = ms.get("indicators_4h") if isinstance(ms.get("indicators_4h"), dict) else {}
        i1 = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
        orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
        tf4 = _tf_vote(orch.get("mid_bias"), i4.get("macd"), i4.get("trend"))
        tf1 = _tf_vote(orch.get("long_bias") or orch.get("mid_bias"), i1.get("macd"), i1.get("trend"))
        opp4 = (tf4 == "bearish" and pos_dir > 0) or (tf4 == "bullish" and pos_dir < 0)
        opp1 = (tf1 == "bearish" and pos_dir > 0) or (tf1 == "bullish" and pos_dir < 0)
        if opp4 and opp1:
            return False, f"swing_consensus_oppose(4h={tf4} 1d={tf1} vs {side})", 0.0
        if opp4 or opp1:
            try:
                _mult = float(os.getenv("SWING_COUNTER_ONE_TF_SIZE_MULT", "0.5") or 0.5)
            except Exception:
                _mult = 0.5
            return True, f"swing_single_tf_oppose(4h={tf4} 1d={tf1} vs {side})", max(0.0, min(1.0, _mult))
        return True, f"swing_consensus_ok(4h={tf4} 1d={tf1})", 1.0
    except Exception as e:
        logger.warning("[MidLong] swing 共识闸异常(fail-open): %s", e)
        return True, "swing_ct_gate_error", 1.0


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
        # [M6 2026-08-21] 探针即便显式开启也要求因子弹药充足：活跃因子数低于
        # FACTOR_ROUTE_MIN_ACTIVE_FACTORS 时 ranging 禁开（原只要 env 开就放）。
        if allow_range:
            try:
                from backend.services.factor_engine.midlong_active_factor_set import (
                    midlong_active_factor_set,
                )
                from backend.config.settings import FACTOR_ROUTE_MIN_ACTIVE_FACTORS
                _af_n = len(midlong_active_factor_set.get_active_factors())
                if _af_n < int(FACTOR_ROUTE_MIN_ACTIVE_FACTORS):
                    allow_range = False
                    logger.info(
                        "[MidLong] stage=fuse symbol=%s 探针关闭：活跃因子%d<%d",
                        sym_u, _af_n, int(FACTOR_ROUTE_MIN_ACTIVE_FACTORS),
                    )
            except Exception as _af_err:
                logger.debug("[MidLong] 探针因子数检查跳过: %s", _af_err)
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
        # [2026-09-18 解冻·选项E] 跨层登记否决原因，供 brain 的 open_execute_false 事件消费。
        # 此前该事件无 reason 字段 ⇒ 台账里查不到"为什么被否"（48h 内 321 条全部无因）。
        # 只写审计侧标记，**不改变任何交易行为**。
        try:
            from backend.services.mlto.open_block_reason import (
                mark_open_block,
                remember_open_block,
            )
            mark_open_block(str(_reason or "writer_block"), layer="midlong_executor")
            remember_open_block(sym_u, str(tier or ""), str(_reason or "writer_block"),
                                layer="midlong_executor")
        except Exception:
            pass
        # [2026-09-18 根因修复·F] 互锁可观测：同一标的两个方向都被拦过 ⇒ 落一条带节流的告警。
        # 本次事故（日线 up 禁空 + 追高天花板禁多）之所以拖了很久，就是因为**没有地方说"这是互锁"**。
        # 纯观察，不改变任何判定。
        try:
            from backend.services.full_auto.midlong_interlock_watch import note_block
            _il = note_block(sym_u, str(_reason or ""))
            if _il:
                logger.warning(_il)
        except Exception:
            pass
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
                # [调研轮32/33] 方向性闸（*=regime_block / 追多追空）本身不设 hub_dir
                # ⇒ 用 reason 文本补方向，否则事后无法做反事实复盘（详见文件末尾注释）
                direction=(hub_dir or _dir_from_reason(_reason)),
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

    if not authority_allows_open(auth, source, trading_mode=trading_mode):
        logger.info(
            "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
            "reason=authority_block (writer=%s)",
            sym_u, auth, source, auth,
        )
        _record_fail(f"authority_block writer={auth}")
        return False

    try:
        from backend.config.settings import midlong_new_open_halted
        if midlong_new_open_halted():
            logger.info(
                "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
                "reason=midlong_open_halted",
                sym_u, auth, source,
            )
            _record_fail("midlong_open_halted")
            return False
    except Exception:
        pass

    # [轮114 2026-09-19 **撤回轮111 的重复闸**]
    # 轮111 我在这里加了一道"同币 2h 冷却"，实测发现**重复了既有机制**：
    # `midlong_helpers.try_execute_independent_agent_open` 早已调用
    # `reentry_cooldown.reopen_blocked(account, symbol, action, tier)`（24h 拦截榜首，
    # 439 次），而那个模块比我写的更完备：按 tier/account 隔离、**连亏倍率**、
    # close_reason 感知（TP 地板 / 亏损 4h / SL 2h）、还有 DB 耐久冷却。
    # 我那道闸还更靠前 ⇒ 会把既有模块更具体的原因（如"刚sl平long仓…"）挡在审计之外。
    # 按本文件自己的原则（"复用现有 reentry_cooldown 而非新建独立冷却模块"）撤回，
    # 改为**把既有 mid 冷却基准从 30min 提到 2h**：`.env` `TIER_MID_COOLDOWN_SEC=7200`
    # （改的是配置，不是代码；见 reports 轮114 §12）。
    # 实测依据不变（158 笔中线已平仓）：距上次同币平仓 0–2h 那档 n=40、均值 −0.389%、
    # 胜率 0.375；去掉后 n=118、均值 −0.027%、胜率 0.500。

    if margin <= 0:
        logger.info(
            "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
            "reason=margin_zero",
            sym_u, auth, source,
        )
        _record_fail("margin_zero")
        return False

    # [2026-08-29 全面修复 P1.4/P1.5] mid 层熔断闸：连亏熔断 + 单 symbol 日亏
    # 上限 + mid 空头默认停开。依据：VELVET 两天 54 笔空 -410（无熔断裸奔）、
    # mid 空头 30 天 -601.73 最差象限。fail-open（闸自身异常不拦交易）。
    # [M4 2026-09-14] learned 窄带 paper 探针：reason 带 `paper_probe×` 标记时
    # 放行并乘 margin（缩仓探针，收集当前策略样本）；live 仍硬拦。
    try:
        from backend.services.full_auto.midlong_circuit_gate import check_midlong_entry
        _acct_ml = getattr(session, "paper_account_id", None) or getattr(
            session, "account_id", None
        )
        _ml_ok, _ml_reason = check_midlong_entry(
            _acct_ml, sym_u, side=("short" if act == "sell" else "long"),
            tier=str(tier or "mid"),
            market_summary=market_summary,
        )
        if not _ml_ok:
            logger.info(
                "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "
                "reason=%s",
                sym_u, auth, source, _ml_reason,
            )
            _record_fail(_ml_reason[:60] or "midlong_circuit")
            return False
        if _ml_ok and "paper_probe×" in str(_ml_reason):
            try:
                _pm = float(str(_ml_reason).split("paper_probe×", 1)[1].split(":", 1)[0])
                _pm = max(0.05, min(1.0, _pm))
                margin = margin * _pm
                logger.info(
                    "[MidLong] stage=fuse symbol=%s 熔断窄带 paper 缩仓×%.2f（%s）",
                    sym_u, _pm, str(_ml_reason)[:90],
                )
            except (ValueError, IndexError):
                pass
    except Exception as _ml_gate_err:
        logger.warning("[MidLong] 熔断闸检查跳过(fail-open): %s", _ml_gate_err)

    # [2026-09-05 多模态图审闸] 间隔扫描的 trend_chart_review 共识信号（双票+仲裁）对
    # 开仓方向的三类否决：position_advice 禁令 / 强反向 / 亏损后同向再开需图审同意。
    # 直接针对 2026-08-31 周报的 swing 失血点（亏损后同向再开率 70%）。fail-open。
    try:
        from backend.services.full_auto.midlong_chart_gate import chart_gate_check
        _cg_ok, _cg_reason, _cg_detail = chart_gate_check(
            sym_u, act, tier=str(tier or ""), trade_nature=str(trade_nature or ""),
        )
        if not _cg_ok:
            logger.info(
                "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold reason=%s",
                sym_u, auth, source, _cg_reason,
            )
            _record_fail(_cg_reason[:80] or "chart_gate_veto")
            return False
    except Exception as _cg_err:
        logger.warning("[MidLong] 图审闸检查跳过(fail-open): %s", _cg_err)

    # [2026-09-09 位置闸] 实测（_audit_ml/21_timing.py，99 笔真实 mid/long）：
    # 入场价在 24h 区间 80-100% 分位的 33 笔，入场后 24h 均值 -3.89%、胜率 0.152；
    # 60-80% 分位 -3.21%/0.231；而 0-20% 分位 +1.86%/0.857。61% 的开仓落在上半区。
    # 另：24h 已跌 >5% 时做多，24h 均值 -4.41%/胜率 0.167（接飞刀）。
    # 闸只对 mid/long + ranging/unknown regime 生效，数据缺失 fail-open。
    # [M4 2026-09-14] paper 下命中否决 → 缩仓×0.25 放行（收集当前策略新样本），
    # live 仍硬 veto；detail.paper_shrink_mult 乘到 margin。
    try:
        from backend.services.full_auto.midlong_location_gate import location_gate_check
        # regime 优先取缓存（apply_regime_to_open 每轮写入），缓存空时现场判一次，
        # 避免首个 tick 因缓存未就绪而 fail-open 放行。
        _reg_now = get_cached_regime(sym_u) or ""
        if not _reg_now:
            try:
                from backend.services.decision_core.regime_agent import classify_regime
                _ms_lg = {}
                if isinstance(market_summary, dict):
                    _ms_lg = market_summary.get(sym_u) or market_summary.get(symbol) or {}
                _reg_now = str(classify_regime(_ms_lg if isinstance(_ms_lg, dict) else {}).regime or "")
            except Exception:
                _reg_now = ""
        _tm_lg = str(trading_mode or "paper")
        if getattr(session, "trading_mode", None):
            _tm_lg = str(getattr(session, "trading_mode") or _tm_lg)
        _lg_ok, _lg_reason, _lg_detail = location_gate_check(
            sym_u, act, tier=str(tier or ""), regime=_reg_now,
            market_summary=market_summary,
            paper_mode=(_tm_lg.strip().lower() == "paper"),
        )
        if not _lg_ok:
            logger.info(
                "[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold reason=%s",
                sym_u, auth, source, _lg_reason,
            )
            _record_fail(_lg_reason[:80] or "location_gate_veto", _reg_now)
            return False
        if isinstance(_lg_detail, dict) and _lg_detail.get("paper_shrink_mult"):
            _shrink = float(_lg_detail["paper_shrink_mult"])
            margin = margin * _shrink
            logger.info(
                "[MidLong] stage=fuse symbol=%s 位置闸 paper 缩仓×%.2f（%s）",
                sym_u, _shrink,
                (_lg_detail.get("paper_shrink_veto_reason") or "")[:90],
            )
    except Exception as _lg_err:
        logger.warning("[MidLong] 位置闸检查跳过(fail-open): %s", _lg_err)

    # [2026-08-16 long_trend_v2 入场闸] tier=long 时要求 L1=up（多头单边，禁做空）。
    # 默认 LONG_TREND_V2 关 = 无影响；开=长线开仓只认趋势判定器。
    if (tier or "").lower() == "long":
        try:
            from backend.services.long_trend_v2 import entry_gate
            _v2_ok, _v2_reason = entry_gate(sym_u, act, market_summary)
            if not _v2_ok:
                logger.info(
                    "[MidLong] stage=fuse symbol=%s tier=long 拦截: %s",
                    sym_u, _v2_reason,
                )
                try:
                    from backend.services.period_daily_report import log_long_action
                    log_long_action(sym_u, "entry_blocked", _v2_reason)
                except Exception:
                    pass
                _record_fail(_v2_reason)
                return False
        except Exception as _v2_err:
            logger.debug("[MidLong] long_trend_v2 入场闸跳过: %s", _v2_err)

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
    # [2026-09-08] 保留 intraday（日内波段）：normalize 已保留，此处不再抹成
    # trend_follow——否则日内仓被错当长线，exec_tier 错落 long 撞 E1 独占闸。
    if nature not in ("trend_follow", "position", "swing", "intraday", "scalp"):
        nature = "trend_follow"
    # [2026-09-01 逆势止血] swing 多周期共识闸（前置版 trend_broken 防护）：
    # 双周期反向禁开 / 单周期反向半仓。trend_follow/position 不动。
    if nature == "swing":
        _ct_ok, _ct_reason, _ct_mult = _swing_consensus_gate(sym_u, act, market_summary)
        if not _ct_ok:
            logger.info(
                "[MidLong] stage=fuse symbol=%s nature=swing action=hold reason=%s",
                sym_u, _ct_reason,
            )
            _record_fail(_ct_reason)
            return False
        if _ct_mult < 1.0:
            margin = margin * _ct_mult
            logger.info(
                "[MidLong] stage=fuse symbol=%s nature=swing 单周期反向 缩仓×%.2f (%s)",
                sym_u, _ct_mult, _ct_reason,
            )
    # AI 中线单保留 tier=mid（分通道计数/槽位/风控）
    # [2026-09-08] exec_tier 三档：short/intraday → short（日内波段），
    # 不再错落 long（错落会撞 E1 长线独占闸 → 日内仓永远 writer_blocked）。
    if (tier or "").lower() == "mid" or nature == "swing":
        exec_tier = "mid"
    elif (tier or "").lower() == "short" or nature in ("intraday", "scalp"):
        exec_tier = "short"
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
            # [M1-A 2026-08-21] 入场来源落库（factor_route/trend/mlto），
            # 写入持仓 exit_state_json["entry_source"]，出场分流据此识别因子仓。
            entry_source=str(source or ""),
        )
    )


# ══════════════════════════════════════════════════════════════════════
# [调研轮32/33 2026-09-17] 审计方向补全：让"方向性闸"的拦截可复盘
#
# 缺陷（轮31 实测 96h）：`midlong_long_regime_block` 276 行、`midlong_short_regime_block`
# 537 行的审计 `direction` **全为空**（写入点取 `hub_dir`，而这两个闸不设它），
# 但它们本质是方向性闸（一个只拦多头、一个只拦空头，reason 文本里就写着"多头/空头"）。
# 后果：`backend/scripts/audit_block_counterfactual.py` 因"方向不可知"**拒绝猜测**（这是对的设计），
# 于是"这道闸拦得对不对"永远无法复盘 —— long 车道为何 0/8 空仓也就无法回答。
#
# 口径：只做**文本显式方向**的映射；推断不出返回空串（保持"方向不可知"，绝不猜）。
# ══════════════════════════════════════════════════════════════════════
def _dir_from_reason(reason: str) -> str:
    """从拦截原因文本推断方向：long_regime/多头/追多 → long；short_regime/空头/追空 → short。

    [2026-09-18 根因修复] 补两类**本次事故里实际出现、却判不出方向**的文案：
      · `location_gate_veto: 24h区间分位95%≥追高天花板70% 硬否决` —— 既有"追高"无"追多"，
        旧规则判空串 ⇒ 互锁探测器/方向审计都看不见它（而它正是"多头被拒"的主因）；
      · `…24h区间分位95%≥40% 高位追多` —— 已含"追多"，原本可判。
    只做**文本显式方向**的映射；推不出仍返回空串（保持"方向不可知"，绝不猜）。
    """
    r = str(reason or "")
    if ("long_regime_block" in r or "多头" in r or "追多" in r
            or "追高天花板" in r or "追高" in r or "高位追多" in r):
        return "long"
    if ("short_regime_block" in r or "空头" in r or "追空" in r
            or "低位追空" in r):
        return "short"
    return ""
