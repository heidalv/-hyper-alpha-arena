"""
M1 — MasterController close/reduce 硬事实门控（P3，2026-04-22）

## 为什么需要
DB 证据（7 天）:
  - master_running_reduce  20 次 胜率 5%   (1 赢 19 输) 总亏 -12.11U
  - master_running         5 次  胜率 0%   (0 赢 5 输)  总亏 -21.61U
结论: MasterController 主动发起的 close/reduce 几乎全是错的。
根因: LLM 把轻微波动解读成"风险升高 → 建议减仓"，执行层照单全收，
     没有"现实核对"——它不看当前仓位是不是真的亏到阈值、SL 有没有被穿。

## 做什么
本模块提供一个**纯函数** `check_master_close_hardfact`：
只有满足以下任一"硬事实"时，才允许 LLM 的 close/reduce 通过：

  ① 当前浮亏 ≥ MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER[tier]
  ② SL 穿透率 ≥ MASTER_CLOSE_SL_BREACH_THRESHOLD（1.5x, SL 已失效）
  ③ 风险分 risk_score > 80（外部强风险信号）
  ④ 已有硬事件（sl/tp/liquidation/emergency/manual 等入 reason 白名单）

都不满足 → 拦截；由调用方根据 flag (shadow/enforce) 决定是否真拦还是只记。

## 集成点
`full_auto_trading_service._execute_master_decisions`
  - `action == "close"`  分支（约 4151 行之前）
  - `action == "reduce"` 分支（约 4231 行之前）

## 回滚
flag `RISK_P3_MASTER_CLOSE_REQUIRES_HARDFACT`:
  - "off"     → 永远放行
  - "shadow"  → 检查 + 记日志，但 allow=True
  - "enforce" → 检查 + 记日志 + 真拦截

## 非目标
- 不动 `tp`/`profit_lock_*`/`tp_target`/`sl`/`liquidation` 等"被动路径"
- 不替代 SubPositionManager.review_reduce / reduce_global_cd
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Agent 复查专用 reason 白名单（Tier 1 退出）
# [2026-08-14 F6 整改] 移除 trend_review/trend_review_close/trend_review_reduce：
# 该通道历史 12 笔 0% 胜率 -44.90（orders 权威账本），纳入与 master 同标准硬事实门。
_AGENT_EXIT_REASON_WHITELIST = frozenset({
    "hold_timeout", "hold_timeout_review", "scalp_fast_review",
    "trend_reversal", "trend_weakening", "structure_break",
})

# 常量同步自 settings（避免循环导入 —— 延迟 import）
_HARD_REASON_WHITELIST = frozenset({
    "sl", "breakeven_sl", "tp", "tp_target", "liquidation",
    "emergency", "emergency_drawdown", "drawdown_protection",
    "manual", "profit_lock", "profit_lock_1", "profit_lock_2", "profit_lock_3",
    "trailing", "trailing_hit", "safety_tp",
    "tp_staged", "tp_staged_1", "tp_staged_2", "tp_staged_3",
    "nature_tp_staged", "nature_tp_staged_1", "nature_tp_staged_2", "nature_tp_staged_3",
    "nature_trailing_hit", "health_force_reduce",
    "stop_loss", "stop loss", "margin_call",
    # [2026-07-07] 清算磁吸反转硬退出：链上原始清算数据显示 high severity 磁吸
    # 与当前持仓方向相反（例如持有空单，但上方出现大额空头清算磁吸），这是比
    # LLM 主观判断更硬的事实证据，允许直接放行 close/reduce。详见
    # full_auto_trading_service._run_scalp_independent 中的清算磁吸反转退出逻辑。
    "liq_magnet_reversal",
})


@dataclass(frozen=True)
class HardfactResult:
    """决策结果."""
    allow: bool               # True 放行，False 建议拦截
    matched_rule: str         # 命中的硬事实名字；拦截时为空
    detail: str               # 人类可读详情（用于日志 / event）


def _tier_min_loss_pct(tier: str) -> float:
    try:
        from backend.services.runtime_tuning_store import get_tier_value
        return get_tier_value("master_close_min_loss_pct_by_tier", tier, 0.04)
    except Exception:
        try:
            from backend.config.settings import MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER
            return float(MASTER_CLOSE_MIN_LOSS_PCT_BY_TIER.get(tier, 0.04))
        except Exception:
            return {"short": 0.02, "mid": 0.04, "long": 0.07}.get(tier, 0.04)


def _sl_breach_threshold() -> float:
    try:
        from backend.config.settings import MASTER_CLOSE_SL_BREACH_THRESHOLD
        return float(MASTER_CLOSE_SL_BREACH_THRESHOLD)
    except Exception:
        return 1.5


def check_master_close_hardfact(
    *,
    tier: str,
    action: str,                 # "close" | "reduce"
    entry_price: float,
    mark_price: float,
    sl_price: Optional[float],
    unrealized_pnl: float,
    margin: float,
    risk_score: Optional[float] = None,
    reason_hint: str = "",       # LLM 自报的 reason/reasoning 文本，用白名单匹配
) -> HardfactResult:
    """
    纯函数；只根据事实判断，不读任何全局状态。

    Returns:
        HardfactResult(allow, matched_rule, detail)
    """
    tier_norm = (tier or "mid").strip().lower()
    if tier_norm not in ("short", "mid", "long"):
        tier_norm = "mid"

    loss_pct = 0.0
    if margin > 0 and unrealized_pnl < 0:
        loss_pct = abs(unrealized_pnl) / margin

    # ── 规则 ⑤: 盈利仓禁止 Master reduce（7日 reduce 胜率 16.7%）──
    if action == "reduce" and unrealized_pnl > 0:
        return HardfactResult(
            allow=False, matched_rule="",
            detail=f"reduce blocked: position in profit (upnl={unrealized_pnl:.2f})",
        )

    # ── 规则 ⑥: SL 逼近度 reduce 门控（V5.2）──
    # 根因: MASTER_REDUCE_MIN_LOSS_PCT=5% 是保证金比例，杠杆越高越易触发
    #   8x杠杆下价格跌0.625%→保证金亏5%，这在加密市场是噪音级别
    #   实际案例 ASTER: 价格跌0.92%，8x杠杆下保证金亏7.37%，被误判为"需要减仓"
    # 修复: reduce 仅在价格逼近 SL（≥60% SL 距离）或保证金亏损≥安全地板时允许
    #   这样即使杠杆放大亏损感知，只要价格还没到 SL 的 60%，就不减仓
    if action == "reduce" and unrealized_pnl < 0:
        # SL 逼近度：当前价距entry / SL距entry（0=刚开仓，1=已穿SL）
        sl_breach_early = 0.0
        if sl_price and sl_price > 0 and entry_price > 0 and mark_price > 0:
            sl_dist = abs(entry_price - sl_price)
            cur_dist = abs(entry_price - mark_price)
            if sl_dist > 0:
                sl_breach_early = cur_dist / sl_dist

        # 安全地板：极端杠杆下SL可能极紧，保留保证金亏损硬底线
        try:
            from backend.services.runtime_tuning_store import get_tuning_float
            _reduce_floor = get_tuning_float("master_reduce_min_loss_pct", 0.10)
        except Exception:
            try:
                from backend.config.settings import MASTER_REDUCE_MIN_LOSS_PCT
                _reduce_floor = float(MASTER_REDUCE_MIN_LOSS_PCT)
            except Exception:
                _reduce_floor = 0.10

        # 短线/scalp：保证金浮亏未达 20% 不允许 AI 减仓（与 master_execution _legacy 路径一致）
        if tier_norm == "short":
            _reduce_floor = max(_reduce_floor, 0.20)

        # 门控: SL逼近度<60% 且 保证金亏损<安全地板 → 拦截(价格噪音，交给SL)
        _REDUCE_SL_PROXIMITY_THRESHOLD = 0.60
        if sl_breach_early < _REDUCE_SL_PROXIMITY_THRESHOLD and loss_pct < _reduce_floor:
            return HardfactResult(
                allow=False, matched_rule="",
                detail=(
                    f"reduce blocked: sl_proximity={sl_breach_early:.0%}"
                    f"<{_REDUCE_SL_PROXIMITY_THRESHOLD:.0%} "
                    f"and margin_loss={loss_pct:.1%}<{_reduce_floor:.0%} "
                    f"(price noise, let SL handle)"
                ),
            )

    # ── 规则 ④: reason 白名单（硬事件，优先于小盈拦截）──
    if reason_hint:
        rh = reason_hint.strip().lower()
        for kw in _HARD_REASON_WHITELIST:
            if kw in rh:
                return HardfactResult(
                    allow=True, matched_rule=f"hard_reason:{kw}",
                    detail=f"hard reason hit: {kw}",
                )

    # ── 规则 ③: risk_score > 80 ──
    if risk_score is not None and risk_score > 80:
        return HardfactResult(
            allow=True, matched_rule="risk_score>80",
            detail=f"risk_score={risk_score:.0f}>80",
        )

    # ── 规则 ②: SL 穿透 ──
    sl_breach = 0.0
    if sl_price and sl_price > 0 and entry_price > 0 and mark_price > 0:
        sl_dist = abs(entry_price - sl_price)
        cur_dist = abs(entry_price - mark_price)
        if sl_dist > 0:
            sl_breach = cur_dist / sl_dist
    threshold = _sl_breach_threshold()
    if sl_breach >= threshold:
        return HardfactResult(
            allow=True, matched_rule=f"sl_breach>={threshold}",
            detail=f"sl_breach={sl_breach:.2f}>={threshold}",
        )

    # ── 规则 ①: 浮亏达到 tier 阈值 ──
    min_loss = _tier_min_loss_pct(tier_norm)
    if loss_pct >= min_loss:
        return HardfactResult(
            allow=True, matched_rule=f"loss_pct>={min_loss:.1%}",
            detail=f"tier={tier_norm} loss_pct={loss_pct:.2%}>={min_loss:.1%}",
        )

    # ── YAML 规则引擎（Tier A，data/decision_policies）──
    try:
        from backend.services.decision_policy_engine import evaluate as policy_eval
        pol = policy_eval("master_close", {
            "action": action,
            "tier": tier_norm,
            "floating_loss_pct": loss_pct,
            "risk_score": risk_score or 0,
            "sl_breach_ratio": sl_breach,
        })
        if pol.effect == "block":
            return HardfactResult(allow=False, matched_rule=pol.rule_id or "policy_block", detail=pol.reason or "policy block")
        if pol.effect == "allow" and pol.rule_id:
            return HardfactResult(allow=True, matched_rule=f"policy:{pol.rule_id}", detail=pol.reason or pol.rule_id)
    except Exception:
        pass

    # ── 规则 ⑦: 小盈禁止 Master close（硬事实均不满足时）──
    if action == "close" and unrealized_pnl > 0 and margin > 0:
        try:
            from backend.config.settings import MASTER_CLOSE_MIN_PROFIT_PCT
            min_prof = float(MASTER_CLOSE_MIN_PROFIT_PCT)
        except Exception:
            min_prof = 0.03
        prof_pct = unrealized_pnl / margin
        if prof_pct < min_prof:
            return HardfactResult(
                allow=False, matched_rule="",
                detail=f"close blocked: profit {prof_pct:.2%} < {min_prof:.0%}",
            )

    # 全部不满足 → 建议拦截
    return HardfactResult(
        allow=False, matched_rule="",
        detail=(
            f"no hardfact matched: tier={tier_norm} "
            f"loss_pct={loss_pct:.2%}<{min_loss:.1%} "
            f"sl_breach={sl_breach:.2f}<{threshold} "
            f"risk_score={risk_score or 'n/a'} "
            f"reason_hint={reason_hint[:40]!r}"
        ),
    )


def check_master_min_hold_block(
    *,
    tier: str,
    opened_at: Any = None,
    margin: float = 0.0,
    unrealized_pnl: float = 0.0,
    action: str = "close",
) -> HardfactResult:
    """中线/长线 min_hold 保护期内禁止 Master close/reduce（除非紧急亏损）。"""
    tier_norm = (tier or "mid").strip().lower()
    if tier_norm not in ("mid", "long"):
        return HardfactResult(allow=True, matched_rule="min_hold_skip_short", detail="")

    if not opened_at:
        return HardfactResult(allow=True, matched_rule="min_hold_no_opened_at", detail="")

    try:
        from datetime import datetime, timezone
        from backend.config.settings import TIER_PROTECTION_PARAMS

        if isinstance(opened_at, str):
            _opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        else:
            _opened = opened_at
        if _opened.tzinfo is None:
            _opened = _opened.replace(tzinfo=timezone.utc)
        elapsed_sec = (datetime.now(timezone.utc) - _opened).total_seconds()
        tier_cfg = TIER_PROTECTION_PARAMS.get(tier_norm, {})
        min_hold_sec = int(tier_cfg.get("min_hold_sec", 0) or 0)
        if min_hold_sec <= 0 or elapsed_sec >= min_hold_sec:
            return HardfactResult(
                allow=True,
                matched_rule="min_hold_elapsed",
                detail=f"held {elapsed_sec/3600:.1f}h>={min_hold_sec/3600:.1f}h",
            )

        emerg_pct = float(tier_cfg.get("min_hold_emergency_loss_pct", 6) or 6) / 100.0
        loss_pct = abs(unrealized_pnl) / margin if margin > 0 and unrealized_pnl < 0 else 0.0
        if loss_pct >= emerg_pct:
            return HardfactResult(
                allow=True,
                matched_rule="min_hold_emergency_loss",
                detail=f"emergency loss {loss_pct:.1%}>={emerg_pct:.1%}",
            )

        return HardfactResult(
            allow=False,
            matched_rule="min_hold_protection",
            detail=(
                f"{tier_norm} {action} blocked: held {elapsed_sec/60:.0f}min "
                f"< min_hold {min_hold_sec/60:.0f}min, loss {loss_pct:.1%} "
                f"< emergency {emerg_pct:.1%}"
            ),
        )
    except Exception as exc:
        return HardfactResult(allow=True, matched_rule="min_hold_error", detail=str(exc))


def decide_by_flag(
    result: HardfactResult, flag_value: str,
) -> tuple[bool, str]:
    """
    根据 flag 把 HardfactResult 翻译成"实际是否拦截"。

    Args:
        flag_value: "off" | "shadow" | "enforce"

    Returns:
        (should_block, audit_tag)
        should_block=True → 调用方应 `continue` 跳过本次 close/reduce
        audit_tag → 用于 _append_event 的事件 tag
    """
    fv = (flag_value or "off").strip().lower()
    if fv == "off":
        return False, ""
    if result.allow:
        return False, ""
    if fv == "shadow":
        return False, "master_close_would_block_shadow"
    if fv == "enforce":
        return True, "master_close_blocked_no_hardfact"
    return False, ""


def check_agent_exit_hardfact(
    *,
    tier: str,
    action: str,
    entry_price: float,
    mark_price: float,
    sl_price: Optional[float],
    unrealized_pnl: float,
    margin: float,
    risk_score: Optional[float] = None,
    reason_hint: str = "",
    exit_channel: str = "",
    opened_at: Any = None,
) -> HardfactResult:
    """Tier 1 轻量门控：Agent 复查专用（trend_review / hold_timeout / scalp_fast）。

    相对 master 全量门控：
      - 浮亏阈值为 tier 默认值的一半
      - exit_channel / reason 命中 Agent 白名单即放行
      - 盈利仓禁止 reduce
      - 不使用 v6 SL 硬拦（趋势反转常发生在小亏区间）
    """
    tier_norm = (tier or "mid").strip().lower()
    if tier_norm not in ("short", "mid", "long"):
        tier_norm = "mid"

    # ── min_hold 前置（[三周期持仓时间收敛 2026-08-13]）──
    # 根因: 下方 Agent 白名单命中即放行，trend_review_close 在 long 72h 保护期内
    # 以小亏提前平掉仓位（12 笔全部 -0.3%~-3.5%、时长 3.1h~10.3h）。
    # Layer A check_position_protection 理论上先拦，但存在历史未接线/穿透窗口，
    # 此处复用 Tier2 的 min_hold 硬拦作为 Tier1 兜底：long 72h（紧急 5%）/
    # mid 12h（紧急 6%）内非紧急亏损不得放行；short 自动跳过（allow=True）。
    _min_hold_gate = check_master_min_hold_block(
        tier=tier_norm,
        opened_at=opened_at,
        margin=margin,
        unrealized_pnl=unrealized_pnl,
        action=action,
    )
    if not _min_hold_gate.allow:
        return _min_hold_gate

    ch = (exit_channel or "").strip().lower()
    rh = (reason_hint or "").strip().lower()
    for kw in _AGENT_EXIT_REASON_WHITELIST:
        if kw in ch or kw in rh:
            return HardfactResult(
                allow=True,
                matched_rule=f"agent_channel:{kw}",
                detail=f"agent exit whitelist: {kw}",
            )

    if action == "reduce" and unrealized_pnl > 0:
        return HardfactResult(
            allow=False, matched_rule="",
            detail=f"agent reduce blocked: position in profit (upnl={unrealized_pnl:.2f})",
        )

    min_loss_full = _tier_min_loss_pct(tier_norm)
    min_loss = min_loss_full * 0.5

    loss_pct = 0.0
    if margin > 0 and unrealized_pnl < 0:
        loss_pct = abs(unrealized_pnl) / margin

    if risk_score is not None and risk_score > 80:
        return HardfactResult(
            allow=True, matched_rule="risk_score>80",
            detail=f"risk_score={risk_score:.0f}>80",
        )

    sl_breach = 0.0
    if sl_price and sl_price > 0 and entry_price > 0 and mark_price > 0:
        sl_dist = abs(entry_price - sl_price)
        cur_dist = abs(entry_price - mark_price)
        if sl_dist > 0:
            sl_breach = cur_dist / sl_dist
    threshold = _sl_breach_threshold()
    if sl_breach >= threshold:
        return HardfactResult(
            allow=True, matched_rule=f"sl_breach>={threshold}",
            detail=f"sl_breach={sl_breach:.2f}>={threshold}",
        )

    if loss_pct >= min_loss:
        return HardfactResult(
            allow=True, matched_rule=f"loss_pct>={min_loss:.1%}",
            detail=f"tier={tier_norm} agent loss_pct={loss_pct:.2%}>={min_loss:.1%}",
        )

    # [2026-08-14 F6 整改] 删除 trend_review* close 的显式放行分支——
    # 该通道纳入与 master 同标准硬事实门（历史 12 笔 0% 胜率 -44.90）。

    return HardfactResult(
        allow=False, matched_rule="",
        detail=(
            f"agent gate blocked: tier={tier_norm} "
            f"loss_pct={loss_pct:.2%}<{min_loss:.1%} "
            f"channel={exit_channel!r}"
        ),
    )


def route_exit_tier(exit_channel: str) -> int:
    """按 exit_channel 路由 Tier：0=规则直通, 1=Agent复查, 2=Master/AI主动。"""
    ch = (exit_channel or "").strip().lower()
    if not ch:
        return 2
    tier0_prefixes = (
        "sl", "tp", "liquidation", "max_hold_timeout", "nature_tp_staged",
        "nature_trailing", "tp_staged", "trailing_hit", "profit_lock",
        "breakeven", "emergency_drawdown", "manual", "liq_magnet_reversal",
    )
    if any(ch == p or ch.startswith(p) for p in tier0_prefixes):
        return 0
    tier1_prefixes = (
        "trend_review", "hold_timeout", "scalp_fast_review",
    )
    if any(ch.startswith(p) for p in tier1_prefixes):
        return 1
    return 2
