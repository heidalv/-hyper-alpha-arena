# -*- coding: utf-8 -*-
"""[§78 执行 2026-09-11 / 决策 P19-B] 出场通道熔断的**共享闸**（统一出口全接）。

背景（§77.4）：`_breaker_shadow`（`tier|通道` 滚动窗胜率 < 阈值 ⇒ 熔断该通道）此前只有
2 个查询点，15 条合格通道里绝大多数绕行。用户选择 **P19-B「统一出口全接」**。

本模块提供**唯一判定入口**，供两条真实出场路径共用：
  * `services/unified_exit_executor.py`（Master 路径：`master_execution` / `hold_timeout_trend_review`）
  * `services/full_auto/midlong_position_manager.py::_exec_close`（MLTO 路径：review / 论题哨兵 / 兜底）

**安全边界（硬约束，不可配置放宽到"连止损都抑制"）**：
  保护性通道**永不抑制** —— 止损/止盈/强平/紧急回撤/硬事实/保证金/日亏/浮盈保护/移动止盈/
  保本/超时/尘仓清理。这些通道的语义是"降低风险"，抑制它们等于把风险敞口留给运气。
  只有"叙事/系统裁量型"通道（thesis_*、midlong、trend_broken、exit_policy…）参与熔断。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Tuple

logger = logging.getLogger(__name__)

#: 保护性通道（**永不**由本闸抑制）：命中即放行，不再看熔断表
PROTECTED_CHANNEL_MARKERS = (
    "sl", "stop_loss", "止损", "tp", "take_profit", "止盈",
    "liquidation", "强平", "爆仓", "margin_call",
    "emergency", "紧急", "hardfact", "hard_fact",
    "daily_loss", "day_loss", "日亏",
    "profit_drawdown", "profit_lock", "浮盈保护",
    "breakeven", "保本", "保盈",
    "trailing", "移动止盈", "移动止损",
    "max_hold_timeout", "超时",
    "dust_cleanup", "尘仓",
    "liq_magnet_reversal",
    # [§82 执行 2026-09-11 / 决策 P24-A] 两条**语义偏硬**的通道加入白名单：
    #   * `thesis_invalidation`（论题已死）—— 30 天 4 笔 −$115.71、0% 胜、单笔 −$28.93
    #     为全表最差；抑制它＝让"论题已死"的仓位继续挂着等兜底，语义上属降险；
    #   * `symbol_removed`（选币撤币）—— 操作性离场，标的可能已不在跟踪宇宙里。
    # 二者一旦被熔断挡住，仓位只能靠 sl/tp/超时/尘仓兜底 ⇒ 按"降险优先"处理。
    "thesis_invalidation", "symbol_removed",
)


def channel_of(reason: str) -> str:
    """从 `close_reason` 里取通道键：`"trend_broken: xxx" → "trend_broken"`；
    `"[no_progress] ..." → "no_progress"`；无分隔则原样返回（截断）。"""
    r = str(reason or "").strip()
    if not r:
        return ""
    m = re.match(r"^\[([^\]]+)\]", r)          # [no_progress] ...
    if m:
        return m.group(1).strip().lower()[:48]
    head = r.split(":", 1)[0].strip().lower()  # trend_broken: ...
    return head[:48] if head else r.lower()[:48]


def is_protected(channel: str) -> bool:
    c = str(channel or "").strip().lower()
    if not c:
        return True  # 取不到通道 ⇒ 保守放行（不抑制）
    return any(mk in c for mk in PROTECTED_CHANNEL_MARKERS)


def unified_enabled() -> bool:
    """总开关 `EXIT_CHANNEL_BREAKER_UNIFIED`（默认 true；false = 回滚到局部查询点）。"""
    try:
        from backend.config.settings import EXIT_CHANNEL_BREAKER_UNIFIED as _v
        return bool(_v)
    except Exception:
        return os.environ.get("EXIT_CHANNEL_BREAKER_UNIFIED", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )


def should_suppress(reason: str, tier: str) -> Tuple[bool, str]:
    """是否应**抑制**这次出场？返回 `(suppress, 说明)`。

    口径：`tier ∈ {short,mid,long}` 且通道**非保护性** 且
    `attribution.exit_channel_shadow(通道, tier)` 为真 ⇒ 抑制。
    任何异常 ⇒ fail-open（不抑制）但**必须可见**（WARNING）。
    """
    try:
        if not unified_enabled():
            return False, "switch_off"
        t = str(tier or "").strip().lower()
        if t == "scalp":
            t = "short"
        if t not in ("short", "mid", "long"):
            return False, f"tier_skip:{t or '?'}"
        ch = channel_of(reason)
        if not ch or is_protected(ch):
            return False, f"protected_or_empty:{ch or '?'}"
        from backend.services.source_attribution import attribution
        if attribution.exit_channel_shadow(ch, t):
            # [§84 执行 2026-09-11 / 决策 P27-A / 缺陷 #69] **证据新鲜度约束**：
            # 抑制发生在记账之前 ⇒ 被抑制的通道不再产生样本 ⇒ 胜率永久冻结。
            # 实测 `mid|trend_broken` 的窗口最新样本已 14.1 天却仍会抑制下一次离场，
            # 因此"用过期证据抑制"必须降级为**只记录不抑制**（`0`=关闭该约束）。
            fresh, age_days, limit = attribution.exit_channel_evidence_fresh(ch, t)
            if not fresh:
                age_txt = "无时间戳" if age_days is None else f"{age_days:.1f} 天"
                logger.warning(
                    "[ExitChannelBreaker] 证据过期 ⇒ 本次不抑制（P27-A）：%s|%s 最新样本 %s > %.0f 天",
                    t, ch, age_txt, limit,
                )
                return False, f"stale_evidence:{t}|{ch}({age_txt}>{limit:.0f}d)"
            # [§95 2026-09-11 / 目标③「不产生悬挂仓位」] 可选**抑制上限**：
            # 同一通道在窗口内被反复抑制时，超过 `EXIT_SUPPRESS_MAX_COUNT` 即放行本次离场，
            # 保证仓位不会被"永久压住"（默认 0 = 关闭该约束，行为与旧版一致）。
            try:
                _max = float(os.environ.get("EXIT_SUPPRESS_MAX_COUNT", "0") or 0)
            except (TypeError, ValueError):
                _max = 0.0
            if _max > 0:
                cnt = attribution.suppression_count(ch, t)
                if cnt >= int(_max):
                    logger.warning(
                        "[ExitChannelBreaker] 抑制已达上限 ⇒ 本次放行（P24-C）：%s|%s 窗口内已抑制 %d 次（≥%.0f）",
                        t, ch, cnt, _max,
                    )
                    return False, f"suppress_cap_reached:{t}|{ch}({cnt}>={int(_max)})"
            attribution.note_suppression(ch, t)
            return True, f"{t}|{ch}"
        return False, f"not_shadowed:{t}|{ch}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ExitChannelBreaker] 判定异常(fail-open，本次不抑制): %s", exc)
        return False, f"error:{type(exc).__name__}"
