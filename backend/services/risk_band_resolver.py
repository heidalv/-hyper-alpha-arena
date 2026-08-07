"""Stage E 风控参数解析器（纯函数模块） — 对齐 docs/research/decisions.md + cross_review.md.

本模块的每个函数：
- **都是纯函数**（不读 DB、不依赖 service 单例），方便单测和回归测。
- 每个函数都标注来源决策编号（D1..D9）+ Stage G 补丁号（P1..P11）。
- 未命中时**必须**给出明确兜底 + 结构化日志告警，不得返回 None（P3 规范）。

调用方约定：
- 所有调用方在调用前必须自检 `RISK_STAGE_E_ENABLED` 为 true，否则走旧路径。
- 本模块不查 feature flag；feature flag 由上游决定是否进入本模块。
- 出现 KeyError/ValueError 一律冒泡给调用方，不在这里"安静吞掉"。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("risk_band_resolver")


# ════════════════════════════════════════════════════════════════════
# D3 + P3 — 波动带解析
# ════════════════════════════════════════════════════════════════════
def get_vol_band(symbol: str, *, use_x_high: bool = True) -> str:
    """解析 symbol 所属波动带（D3）。

    Args:
        symbol: 交易对代码，'BTC' / 'btc' / 'BTCUSDT' 都接受
        use_x_high: RISK_USE_VOL_BAND_X_HIGH 开关；false 时 x-high 退化为 high

    Returns:
        'low' | 'mid' | 'high' | 'x-high' 四选一；未知 symbol 返回 unknown_fallback (默认 'mid') 并打 red alarm (P3)

    Raises:
        ValueError: 如果配置表里 unknown_fallback 指向非法值，冒泡让启动期发现
    """
    from backend.config.settings import DEFENSIVE_VOLATILITY_TIERS_V2 as _V2

    base = _normalize_symbol(symbol)
    sym_map = _V2.get("symbol_vol_map", {})
    band = sym_map.get(base)
    if band is None:
        fallback = _V2.get("unknown_fallback", "mid")
        logger.warning(
            "[risk_band_resolver][P3_ALARM] unknown symbol=%s fallback_band=%s",
            symbol, fallback,
        )
        band = fallback

    legal = {"low", "mid", "high", "x-high"}
    if band not in legal:
        raise ValueError(f"DEFENSIVE_VOLATILITY_TIERS_V2 产生非法 band={band} (symbol={symbol})")

    if band == "x-high" and not use_x_high:
        band = "high"

    return band


def _normalize_symbol(s: str) -> str:
    """归一为配置表里的 key（小写、去 USDT / USDC / USD 后缀）."""
    if not s:
        return ""
    u = s.strip().lower()
    for suf in ("usdt", "usdc", "usd"):
        if u.endswith(suf):
            u = u[: -len(suf)]
            break
    return u


def invalidate_manual_trading_symbols_cache() -> None:
    """兼容旧调用 — 转发到全局 trading_pairs 缓存。"""
    from backend.services.trading_pairs_config import invalidate_trading_pairs_cache
    invalidate_trading_pairs_cache()


def get_manual_trading_symbols(*, force_refresh: bool = False) -> frozenset[str]:
    """全局 user_trading_pairs（手动配置交易对）。"""
    from backend.services.trading_pairs_config import get_user_trading_pairs_set
    return get_user_trading_pairs_set(force_refresh=force_refresh)


def is_manual_configured_symbol(symbol: str) -> bool:
    """是否为全局手动配置的交易对（非 AI 自动选币池）。"""
    from backend.services.trading_pairs_config import is_user_configured_symbol
    return is_user_configured_symbol(symbol)


# ════════════════════════════════════════════════════════════════════
# D1 — 默认 TP/SL 解析
# ════════════════════════════════════════════════════════════════════
def get_tp_sl_defaults(band: str, tier: str) -> dict:
    """取 (band, tier) 的 TP/SL 默认 pct（D1）。"""
    from backend.config.settings import TIER_TP_SL_DEFAULTS_BY_VOL_BAND as _M

    band_cfg = _M.get(band)
    if band_cfg is None:
        logger.warning("[risk_band_resolver] unknown band=%s in TP_SL defaults, fallback=mid", band)
        band_cfg = _M["mid"]

    tier_cfg = band_cfg.get(tier)
    if tier_cfg is None:
        logger.warning("[risk_band_resolver] unknown tier=%s in band=%s, fallback=short", tier, band)
        tier_cfg = band_cfg["short"]
    return dict(tier_cfg)


# ════════════════════════════════════════════════════════════════════
# D2 — ATR 倍数解析
# ════════════════════════════════════════════════════════════════════
def get_atr_multiplier(band: str, tier: str) -> float:
    """取 (band, tier) 的 ATR 倍数（D2）。"""
    from backend.config.settings import TIER_ATR_MULTIPLIER_BY_VOL_BAND as _M

    band_cfg = _M.get(band, _M["mid"])
    return float(band_cfg.get(tier, band_cfg.get("short", 3.0)))


# ════════════════════════════════════════════════════════════════════
# D4 + P1 + P4 + P6 — 杠杆上限（带 bucket 稀释的纯函数）
# ════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LeverageCapContext:
    """用于 resolve_leverage() 的上下文，避免 kwargs 漂移."""
    ai_override: Optional[float] = None
    nature: Optional[str] = None
    count_same_bucket_open: int = 0
    legacy_env_cap: Optional[float] = None  # LEGACY_MAX_LEVERAGE_20X 等
    tier: Optional[str] = None  # D12: short/mid/long 专属 cap


def resolve_leverage(symbol: str, ctx: LeverageCapContext) -> tuple[int, str]:
    """按 P4 顺序解析最终杠杆：ai_override → band_cap → nature_cap → tier_cap → legacy_env。

    P1: band_cap 上还要套 effective = band_cap / sqrt(count_same_bucket_open + 1)。
    D12: 加上 tier_cap 一层，short/mid/long 独立上限。

    Returns:
        (final_leverage, reason) — reason 解释是哪一层触发的 cap，用于日志和 Stage F 监控。
    """
    from backend.config.settings import (
        LEVERAGE_CAP_BY_VOL_BAND,
        LEVERAGE_CAP_BY_NATURE,
        DYNAMIC_LEVERAGE_ENABLED,
        DYNAMIC_LEVERAGE_MAX,
        MANUAL_SYMBOL_LEVERAGE_CAP,
        LEVERAGE_CAP_BY_TIER,
    )

    band = get_vol_band(symbol)
    raw_band_cap = float(LEVERAGE_CAP_BY_VOL_BAND.get(band, 10))
    _manual_symbol = is_manual_configured_symbol(symbol)
    if _manual_symbol:
        # 波动带分级(D3)不变；仅手动配置币种的杠杆 band 上限放宽至 20x
        raw_band_cap = min(float(MANUAL_SYMBOL_LEVERAGE_CAP), float(DYNAMIC_LEVERAGE_MAX))

    # P1: bucket 稀释
    dilution = math.sqrt(max(1, ctx.count_same_bucket_open + 1))
    effective_band_cap = max(1.0, raw_band_cap / dilution)

    nature_cap = float(LEVERAGE_CAP_BY_NATURE.get(ctx.nature, LEVERAGE_CAP_BY_NATURE[None]))

    # D12 修复 (2026-07-17)：此前"动态杠杆上限替代固定 tier cap"是 if/elif 互斥关系，
    # DYNAMIC_LEVERAGE_ENABLED=true（默认）时 tier_cap 直接退化成全局 5-20x，完全忽略
    # ctx.tier——导致 long tier 仓位也能摸到 20x 上限，与 TIER_PROMPT_HINTS 里明确告诉
    # AI 的"长线杠杆建议≤8x（长线偏低）"自相矛盾。现场表现：同一 long tier 内不同symbol
    # 杠杆在 5x~20x 间随市场因子乱跳，用户反馈"杠杆倍数乱了"。
    # 改为 dynamic cap 与 tier cap 取交集（min）而非互斥：
    #   short/mid 的 tier cap(20x) == dynamic max(20x)，行为不变；
    #   long 的 tier cap(12x) < dynamic max(20x)，long tier 杠杆被真正收紧到 ≤12x。
    tier_cap = float("inf")
    if DYNAMIC_LEVERAGE_ENABLED:
        tier_cap = float(DYNAMIC_LEVERAGE_MAX)
    if ctx.tier:
        tier_cap = min(tier_cap, float(LEVERAGE_CAP_BY_TIER.get(ctx.tier, 20)))

    base_cap = min(effective_band_cap, nature_cap, tier_cap)
    if ctx.legacy_env_cap is not None:
        base_cap = min(base_cap, float(ctx.legacy_env_cap))

    # P4 顺序 ①: AI override 先走（但不得超过 base_cap）
    if ctx.ai_override is not None and ctx.ai_override > 0:
        final = min(float(ctx.ai_override), base_cap)
        if final < ctx.ai_override:
            if _manual_symbol and effective_band_cap <= nature_cap and effective_band_cap <= tier_cap:
                reason = "manual_symbol_cap"
            elif tier_cap <= nature_cap and tier_cap <= effective_band_cap:
                reason = f"tier_cap_{int(tier_cap)}x"
            elif effective_band_cap <= nature_cap:
                reason = "band_cap" if not _manual_symbol else "manual_symbol_cap"
            else:
                reason = "nature_cap"
        else:
            reason = "ai_override"
    else:
        final = base_cap
        if _manual_symbol and effective_band_cap <= nature_cap and effective_band_cap <= tier_cap:
            reason = "manual_symbol_cap"
        elif tier_cap <= nature_cap and tier_cap <= effective_band_cap:
            reason = f"tier_cap_{int(tier_cap)}x"
        elif effective_band_cap <= nature_cap:
            reason = "band_cap" if not _manual_symbol else "manual_symbol_cap"
        else:
            reason = "nature_cap"

    return (max(1, int(round(final))), reason)


# ════════════════════════════════════════════════════════════════════
# D13 — long tier 出场免疫判定
# ════════════════════════════════════════════════════════════════════
def is_close_reason_blocked_for_long(close_reason: str) -> bool:
    """D13: 判断某个 close_reason 是否被 long tier 屏蔽（当 flag on 时）。

    SL/TP/emergency_drawdown/profit_lock/manual 等硬退出**不**屏蔽；
    master_running_reduce / master_defensive_reduce / ai_reverse 等软退出屏蔽。
    """
    from backend.config.settings import LONG_TIER_PROTECTED_FROM, RISK_USE_LONG_TIER_IMMUNE
    if not RISK_USE_LONG_TIER_IMMUNE:
        return False
    return close_reason in LONG_TIER_PROTECTED_FROM


# ════════════════════════════════════════════════════════════════════
# S0-6 — mid/long tier 出场免疫判定（2026-07-19，对应 04 综合方案 §3.2）
#
# 扩展 D13 到 mid tier：审计报告发现 master_running_close 在 mid/long 占比 33%
# 且胜率仅 16%——LONG_TIER_PROTECTED_FROM 只保护 long，mid 无对应保护。
# 本函数同时覆盖 mid 和 long，供 master_execution.py 在 close/reduce 前调用。
# ════════════════════════════════════════════════════════════════════
def is_close_reason_blocked_for_midlong(close_reason: str, tier: str) -> bool:
    """S0-6: 判断某个 close_reason 是否被 mid/long tier 屏蔽（当 flag on 时）。

    Args:
        close_reason: 平仓原因（master_running_close / ai_reverse / sl / tp 等）
        tier: 持仓周期（short/mid/long）—— short 不屏蔽，mid/long 按 flag 屏蔽软退出

    Returns:
        True 表示该 close_reason 应被拦截（不允许此原因平仓）
    """
    _tier = (tier or "").strip().lower()
    if _tier not in ("mid", "long"):
        return False  # short tier 不屏蔽

    _reason = (close_reason or "").strip().lower()
    if not _reason:
        return False

    from backend.config.settings import (
        LONG_TIER_PROTECTED_FROM,
        MID_TIER_PROTECTED_FROM,
        RISK_USE_LONG_TIER_IMMUNE,
        RISK_USE_MID_TIER_IMMUNE,
    )

    if _tier == "long":
        if not RISK_USE_LONG_TIER_IMMUNE:
            return False
        return _reason in LONG_TIER_PROTECTED_FROM
    else:  # mid
        if not RISK_USE_MID_TIER_IMMUNE:
            return False
        return _reason in MID_TIER_PROTECTED_FROM


# ════════════════════════════════════════════════════════════════════
# D15 — 按 tier 取 prompt hint（trading_analysts 导入点）
# ════════════════════════════════════════════════════════════════════
def get_tier_prompt_hint(tier: str) -> str:
    """D15: 返回 tier 对应的 prompt 片段；flag off 时返回空串（不影响旧 prompt）."""
    from backend.config.settings import TIER_PROMPT_HINTS, RISK_USE_TIER_PROMPT_HINTS
    if not RISK_USE_TIER_PROMPT_HINTS:
        return ""
    return TIER_PROMPT_HINTS.get(tier, "")


# ════════════════════════════════════════════════════════════════════
# D5 + P9 — 相关性桶
# ════════════════════════════════════════════════════════════════════
def get_correlation_bucket(symbol: str) -> Optional[dict]:
    """取 symbol 所属桶的配置（D5）。未命中返回 None，由调用方决定是否落单独桶。"""
    from backend.config.settings import CORRELATION_BUCKETS

    base = symbol.strip().upper()
    for bucket in CORRELATION_BUCKETS:
        if base in set(bucket.get("symbols", [])):
            return dict(bucket)
    logger.info("[risk_band_resolver] %s 未命中任何 correlation bucket，按独立桶处理", symbol)
    return None


def check_bucket_can_open(
    symbol: str,
    open_positions_by_bucket: dict,
) -> tuple[bool, str]:
    """判断新开仓是否违反桶级并发上限（D5）。

    Args:
        symbol: 待开仓 symbol
        open_positions_by_bucket: {"majors": 2, "indep": 1, ...}

    Returns:
        (allowed, reason)
    """
    bucket = get_correlation_bucket(symbol)
    if bucket is None:
        return (True, "no_bucket")

    name = bucket["name"]
    current = int(open_positions_by_bucket.get(name, 0))
    cap = int(bucket["max_concurrent_positions"])
    if current >= cap:
        return (False, f"bucket_{name}_at_cap_{cap}")
    return (True, f"bucket_{name}_ok_{current}/{cap}")


# ════════════════════════════════════════════════════════════════════
# D9 + P2 — 样本不足币种的仓位打折
# ════════════════════════════════════════════════════════════════════
def get_sample_insufficient_scale(symbol: str, n_daily_bars: Optional[int] = None) -> float:
    """D9: 返回该币种的仓位缩放系数，满足 P2 的 sqrt(n/min) 公式。

    Args:
        symbol: 交易对
        n_daily_bars: 当前可用的 1d K 线根数；为 None 时退回 bootstrap_scale

    Returns:
        [0.5, 1.0] 之间的浮点数；不在 SAMPLE_INSUFFICIENT_SYMBOLS 里的币返回 1.0
    """
    from backend.config.settings import SAMPLE_INSUFFICIENT_SYMBOLS

    base = symbol.strip().upper()
    cfg = SAMPLE_INSUFFICIENT_SYMBOLS.get(base)
    if cfg is None:
        return 1.0

    min_bars = int(cfg.get("min_daily_bars", 365))
    if n_daily_bars is None:
        scale = float(cfg.get("bootstrap_scale", 0.7))
    else:
        scale = math.sqrt(min(1.0, max(0.0, float(n_daily_bars) / max(1, min_bars))))

    return max(0.5, min(1.0, scale))


# ════════════════════════════════════════════════════════════════════
# D8 + P8 — trade_nature 兜底
# ════════════════════════════════════════════════════════════════════
def resolve_trade_nature(
    raw_nature: Optional[str],
    expected_hold_hours: Optional[float] = None,
) -> tuple[str, bool]:
    """D8: 解析有效的 trade_nature。

    Returns:
        (nature, was_filled_by_default)
        - was_filled_by_default=True 时，调用方应该打 red alarm（P8 规定）
    """
    from backend.config.settings import (
        DEFAULT_TRADE_NATURE_FOR_MISSING,
        TRADE_NATURE_BY_HOLD_HOURS,
        LOG_ALARM_ON_MISSING_NATURE,
    )

    if raw_nature and raw_nature.strip():
        return (raw_nature.strip(), False)

    if expected_hold_hours is not None and expected_hold_hours > 0:
        for hour_cap, nat in TRADE_NATURE_BY_HOLD_HOURS:
            if expected_hold_hours <= hour_cap:
                if LOG_ALARM_ON_MISSING_NATURE:
                    logger.warning(
                        "[risk_band_resolver][P8_ALARM] trade_nature missing, inferred_by_hold=%.1fh → %s",
                        expected_hold_hours, nat,
                    )
                return (nat, True)

    if LOG_ALARM_ON_MISSING_NATURE:
        logger.warning(
            "[risk_band_resolver][P8_ALARM] trade_nature missing + no expected_hold, fallback=%s",
            DEFAULT_TRADE_NATURE_FOR_MISSING,
        )
    return (DEFAULT_TRADE_NATURE_FOR_MISSING, True)


# ════════════════════════════════════════════════════════════════════
# 调度方便函数
# ════════════════════════════════════════════════════════════════════
def stage_e_active() -> bool:
    """上层调用统一的 Stage E 总开关 + 硬回滚联动。

    - RISK_STAGE_E_ENABLED=false   → 不走 Stage E
    - LEGACY_RISK_HARD_ROLLBACK=true → Stage F 熔断已触发，强制回旧路径
    """
    from backend.config.settings import RISK_STAGE_E_ENABLED, LEGACY_RISK_HARD_ROLLBACK
    return bool(RISK_STAGE_E_ENABLED) and not bool(LEGACY_RISK_HARD_ROLLBACK)
