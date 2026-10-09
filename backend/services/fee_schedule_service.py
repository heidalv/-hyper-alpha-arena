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
    fee = get_fee_rate("asterdex", is_maker=True)  # → 0.0（Aster USDT 永续 maker 免费）
    fee = get_fee_rate("asterdex", is_maker=False)  # → 0.0004（taker 0.04%）

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
        手续费率（如 asterdex maker=0.0（USDT 永续 maker 免费）、taker=0.0004；
        binance maker=0.0002/taker=0.0004，见 paper_exchange_simulator.DEFAULT_EXCHANGE_RULES）
    """
    rules = get_exchange_rules(exchange)
    return float(rules.maker_fee_rate) if is_maker else float(rules.taker_fee_rate)


def get_min_notional(exchange: Optional[str]) -> float:
    """取最小名义价值（美元）。"""
    return float(get_exchange_rules(exchange).min_notional_usd)


def get_quantity_step(exchange: Optional[str]) -> float:
    """取数量步长。"""
    return float(get_exchange_rules(exchange).quantity_step)


# ══════════════════════════════════════════════════════════════
# [统一成本口径 2026-10-05] 缺口补齐：滑点 / 资金费 / 往返成本 / 盈亏平衡
#
# 背景（整顿轮实测）：全仓有 ≥8 处各自硬编码 `TAKER_FEE_BP = 4.0`，
# 而滑点与资金费散落在 fee_guard / cost_model / funding_history 三处，
# 导致"这笔交易到底要动多少才不亏"没有唯一答案。
#
# 本段把**成本四要素**（手续费 + 滑点 + 资金费 + 往返倍数）收敛到本模块，
# 与本文件既有的 get_fee_rate 一起构成全系统唯一的成本真相源。
#
# 规矩：任何模块需要成本数字，一律调本模块，**禁止再硬编码**。
# ══════════════════════════════════════════════════════════════

# 滑点：基点 5bp/边（与 fee_guard.SLIPPAGE_BASE 同源，此处为唯一声明）
SLIPPAGE_BASE_RATE = 0.0005
# 止损出场的滑点倍数：止损是"追价出"，实测比正常出场贵一倍（fee_guard 同口径）
SL_SLIPPAGE_MULT = 2.0
# 资金费：默认 1bp / 8h（与 backtest_engine.funding_history 同源）
FUNDING_RATE_PER_8H = 0.0001
FUNDING_SETTLE_HOURS = 8.0


def get_slippage_rate(is_stop_loss: bool = False) -> float:
    """单边滑点率。`is_stop_loss=True` 时按 SLIPPAGE_MULT 加倍（追价出场）。"""
    base = SLIPPAGE_BASE_RATE
    return base * (SL_SLIPPAGE_MULT if is_stop_loss else 1.0)


def get_funding_cost_rate(hold_seconds: float, rate_per_8h: Optional[float] = None) -> float:
    """持仓 `hold_seconds` 的资金费成本率（按 8h 结算周期的线性近似）。

    真实结算只在资金费时刻发生；这里给出**期望成本**，用于开仓前的期望判断。
    """
    r = FUNDING_RATE_PER_8H if rate_per_8h is None else float(rate_per_8h)
    if hold_seconds <= 0:
        return 0.0
    periods = float(hold_seconds) / (FUNDING_SETTLE_HOURS * 3600.0)
    return abs(r) * periods


def round_trip_cost_rate(
    exchange: Optional[str],
    *,
    entry_is_maker: bool = False,
    exit_is_maker: bool = True,
    hold_seconds: float = 0.0,
    is_stop_loss: bool = False,
) -> float:
    """一次完整往返的**总成本率**（占名义比例）。这是"盈亏平衡移动"的唯一算法。

    组成：
      1. 入场手续费 + 出场手续费（分别按 maker/taker 取真实费率）
      2. 入场滑点 + 出场滑点（止损出场滑点加倍）
      3. 持仓期资金费（期望值）

    例：asterdex 全挂单、秒级持仓 → 0.0（maker 免费 + 滑点按 0 计）
        事实是挂单不付滑点，只有主动成交才付。`entry_is_maker/exit_is_maker`
        同时决定滑点是否计入——挂单成交不承担滑点。
    """
    maker_in = bool(entry_is_maker)
    maker_out = bool(exit_is_maker)
    fee_in = get_fee_rate(exchange, is_maker=maker_in)
    fee_out = get_fee_rate(exchange, is_maker=maker_out)
    slip_in = 0.0 if maker_in else get_slippage_rate(False)
    slip_out = 0.0 if maker_out else get_slippage_rate(is_stop_loss)
    funding = get_funding_cost_rate(hold_seconds)
    return float(fee_in + fee_out + slip_in + slip_out + funding)


def break_even_move_rate(
    exchange: Optional[str],
    *,
    entry_is_maker: bool = False,
    exit_is_maker: bool = True,
    hold_seconds: float = 0.0,
    is_stop_loss: bool = False,
) -> float:
    """盈亏平衡所需的价格有利移动比例（= `round_trip_cost_rate`）。

    单独命名是为了让调用点读起来就是"我要动多少才不亏"。"""
    return round_trip_cost_rate(
        exchange,
        entry_is_maker=entry_is_maker,
        exit_is_maker=exit_is_maker,
        hold_seconds=hold_seconds,
        is_stop_loss=is_stop_loss,
    )


def break_even_move_bp(exchange: Optional[str], **kwargs) -> float:
    """同 `break_even_move_rate`，单位换成 bp。"""
    return break_even_move_rate(exchange, **kwargs) * 10000.0


# 成本假设的最保守上界：用于"跨交易所/跨场所"的成本比较与门限。
# 取全部场所中最贵的 taker，避免"用便宜场所的费率给贵场所的仓算盈亏"。
def worst_case_taker_rate() -> float:
    """所有已登记场所中最高的 taker 费率（保守口径）。"""
    rates = [float(r.taker_fee_rate) for r in DEFAULT_EXCHANGE_RULES.values()]
    return max(rates) if rates else 0.0005


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
