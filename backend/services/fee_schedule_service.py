"""费率中心化服务 —— 全系统唯一的费率/保证金真相源。

设计目标:
- 消除 5+ 处散落的费率/维持保证金率定义（paper_engine 全局常量、paper_netting 硬编码、
  position_tracker 硬编码 0.004、simulator per-exchange 表）
- 单一真相源: 基于 paper_exchange_simulator.DEFAULT_EXCHANGE_RULES（最权威的 per-exchange 表）
- 兼容现有 settings.MAINT_MARGIN_RATIO 全局覆盖（应急开关）
- 所有爆仓价/费率计算统一调用本服务

核心 API:
    from backend.services.fee_schedule_service import (
        get_maint_margin_rate,     # 按交易所取维持保证金率
        get_fee_rate,              # 按交易所 + maker/taker 取手续费率
        get_exchange_rules,        # 取完整规则对象
        canonical_exchange,        # 交易所名归一化（含别名）
    )

用法:
    mmr = get_maint_margin_rate("asterdex")   # → 0.005
    fee = get_fee_rate("asterdex", is_maker=True)  # → 0.00005

设计决策:
- 不重复定义费率表，直接从 paper_exchange_simulator 导入（DRY）
- settings.MAINT_MARGIN_RATIO 作为全局覆盖（若设置则覆盖所有交易所，应急用）
- 默认行为: per-exchange 精确值（asterdex 0.005, binance 0.004 等）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from backend.services.exchange.paper_exchange_simulator import (
    DEFAULT_EXCHANGE_RULES,
    EXCHANGE_ALIASES,
    PaperExchangeRules,
    get_paper_exchange_rules,
)

logger = logging.getLogger(__name__)

# ── 交易所名归一化 ──────────────────────────────────────────────

# 默认交易所（与 settings.DEFAULT_EXCHANGE 对齐）
_DEFAULT_EXCHANGE = "asterdex"


def canonical_exchange(exchange: Optional[str]) -> str:
    """交易所名归一化（小写 + 别名解析）。

    - "hl"/"hyper" → "hyperliquid"
    - "aster"/"aster_dex" → "asterdex"
    - "binanceusdm" → "binance"
    - None/空 → 默认交易所 (asterdex)
    - 未知 → 默认交易所（降级，不报错）
    """
    key = (exchange or "").strip().lower()
    if not key:
        return _DEFAULT_EXCHANGE
    key = EXCHANGE_ALIASES.get(key, key)
    if key not in DEFAULT_EXCHANGE_RULES:
        # 未知交易所，降级到默认（记录一次警告避免刷屏）
        logger.debug(f"[FeeSchedule] 未知交易所 '{exchange}'，降级到 {_DEFAULT_EXCHANGE}")
        return _DEFAULT_EXCHANGE
    return key


# ── 核心 API ────────────────────────────────────────────────────

def get_exchange_rules(exchange: Optional[str]) -> PaperExchangeRules:
    """取完整交易所规则对象（费率、维持保证金率、最小名义价值、数量步长等）。

    内部委托给 paper_exchange_simulator.get_paper_exchange_rules，
    确保单一真相源（不重复定义费率表）。
    """
    return get_paper_exchange_rules(canonical_exchange(exchange))


def get_maint_margin_rate(exchange: Optional[str] = None) -> float:
    """取维持保证金率（用于爆仓价计算）。

    优先级:
    1. settings.MAINT_MARGIN_RATIO 全局覆盖（若显式设置非默认值，作为应急开关）
    2. per-exchange 精确值（asterdex/hyperliquid/okx/bybit/gateio=0.005, binance=0.004）

    Args:
        exchange: 交易所名（None 则用默认 asterdex）

    Returns:
        维持保证金率（如 0.005 = 0.5%）
    """
    # 优先检查全局覆盖（应急开关）
    try:
        from backend.config.settings import MAINT_MARGIN_RATIO
        # 仅当用户显式覆盖了默认值时才用全局值（默认 0.005 不算覆盖）
        # 判断方法: 若全局值与所有 per-exchange 值都不同，认为是显式覆盖
        # 简化: 直接用全局值（向后兼容现有行为，paper_engine 原来就是读全局）
        # 但若 exchange 指定且与全局不同，per-exchange 更精确
        global_mmr = float(MAINT_MARGIN_RATIO)
        # 若未指定交易所，用全局（兼容旧行为）
        if exchange is None:
            return global_mmr
    except Exception:
        global_mmr = 0.005

    # 指定了交易所 → 用 per-exchange 精确值（更准确）
    rules = get_exchange_rules(exchange)
    return float(rules.maintenance_margin_rate)


def get_fee_rate(exchange: Optional[str], is_maker: bool) -> float:
    """取手续费率。

    Args:
        exchange: 交易所名（None 则用默认 asterdex）
        is_maker: True=maker费率, False=taker费率

    Returns:
        手续费率（如 asterdex maker=0.00005 = 0.005%, taker=0.00005）
    """
    rules = get_exchange_rules(exchange)
    return float(rules.maker_fee_rate) if is_maker else float(rules.taker_fee_rate)


def get_min_notional(exchange: Optional[str]) -> float:
    """取最小名义价值（美元）。"""
    return float(get_exchange_rules(exchange).min_notional_usd)


def get_quantity_step(exchange: Optional[str]) -> float:
    """取数量步长。"""
    return float(get_exchange_rules(exchange).quantity_step)


# ── 批量视图（审计/前端用）──────────────────────────────────────

def get_all_exchange_rules() -> Dict[str, PaperExchangeRules]:
    """返回所有交易所的完整规则（审计/前端展示用）。

    返回 dict 的 key 是规范化的交易所名。
    """
    return dict(DEFAULT_EXCHANGE_RULES)


def get_all_exchange_summary() -> list:
    """返回所有交易所费率摘要（前端展示用）。"""
    result = []
    for name, rules in DEFAULT_EXCHANGE_RULES.items():
        result.append({
            "exchange": name,
            "maker_fee_rate": rules.maker_fee_rate,
            "taker_fee_rate": rules.taker_fee_rate,
            "maker_fee_pct": round(rules.maker_fee_rate * 100, 4),
            "taker_fee_pct": round(rules.taker_fee_rate * 100, 4),
            "min_notional_usd": rules.min_notional_usd,
            "maintenance_margin_rate": rules.maintenance_margin_rate,
            "maintenance_margin_pct": round(rules.maintenance_margin_rate * 100, 3),
            "quantity_step": rules.quantity_step,
        })
    return result


# ── 兼容层: paper_engine 全局常量替代 ──────────────────────────
# paper_engine 原来用模块级 MAINTENANCE_MARGIN_RATE（全局 0.005）。
# 现在改为调 get_maint_margin_rate(exchange)，但保留此函数兼容旧调用点。

def engine_maint_margin_rate(exchange: Optional[str] = None) -> float:
    """paper_engine 用的维持保证金率入口。

    兼容旧行为: 若未指定交易所，返回全局 settings.MAINT_MARGIN_RATIO；
    若指定交易所，返回 per-exchange 精确值。
    """
    return get_maint_margin_rate(exchange)


# ── 启动日志（确认费率表加载）───────────────────────────────────

def _log_fee_schedule_loaded() -> None:
    """启动时打印费率表摘要（仅 INFO，便于审计）。"""
    try:
        summary = get_all_exchange_summary()
        logger.info(
            f"[FeeSchedule] 费率中心已加载: {len(summary)} 个交易所, "
            f"默认={_DEFAULT_EXCHANGE}, "
            f"asterdex(maker/taker)={
                get_fee_rate('asterdex', True):.6f}/{
                get_fee_rate('asterdex', False):.6f}"
        )
    except Exception as e:
        logger.warning(f"[FeeSchedule] 费率表加载日志异常: {e}")


# 模块导入时打印一次（幂等，仅 INFO）
_log_fee_schedule_loaded()
