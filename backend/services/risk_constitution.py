"""宪法级风控常量（Constitutional Risk）— 2026-09-07。

对标 Quant Nanggroe AI 的 Constitutional Risk 设计：这些值是 **Python 常量**，
不读 env、不读 config、不读 DB，任何 agent / 配置 / 环境变量都**无法在运行时
覆盖**。要改只能改代码、走 code review——这就是「宪法」的含义。

与 position_memory_manager 的 MAX_DAILY_LOSS_PCT（账户状态机，可随人格调整）
不同，这里是**全系统最后红线**：无论上层策略/LLM/熔断如何判断，只要触碰
这些线，开仓一律 VETO。宁可误杀，不可放过。

用法：
    from backend.services.risk_constitution import constitutional_veto
    reason = constitutional_veto(account_id=acct, symbol=sym, side=side,
                                 margin_usd=planned_margin, equity_usd=equity)
    if reason:
        return f"constitution:{reason}"
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 宪法常量 —— 不可覆盖（故意不提供 env 读取）
# ═══════════════════════════════════════════════════════════════════════════

#: 单笔开仓保证金占净值上限（超过即 VETO）。12% 与主脑 margin 默认对齐，
#: 但这里是**硬顶**：即使有人把 MIDLONG_BRAIN_OPEN_MARGIN_PCT 调到 0.5 也无效。
MAX_SINGLE_TRADE_MARGIN_PCT: float = 0.20

#: 单日累计净亏损占净值上限 → 当日禁止一切新开仓（kill switch）。
#: 比账户状态机的 8% frozen 更严：这是**开仓侧**硬停，不依赖状态机在线。
MAX_DAILY_LOSS_PCT_HARD: float = 0.06

#: 单币种方向性敞口占净值上限（同一 symbol 多仓叠加保证金）。
MAX_SYMBOL_MARGIN_PCT: float = 0.30

#: 全账户总保证金占用上限（所有未平仓仓位保证金之和 / 净值）。
MAX_TOTAL_MARGIN_PCT: float = 0.60

#: 单笔止损幅度下限/上限（防止 LLM 给出贴脸止损或形同虚设的止损）。
MIN_SL_PCT: float = 0.005
MAX_SL_PCT: float = 0.50


class ConstitutionalBreach(Exception):
    """触碰宪法红线。上层应捕获并转为拦截原因，不得静默放过。"""


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def constitutional_veto(
    *,
    account_id: Optional[int],
    symbol: str,
    side: str,
    margin_usd: float = 0.0,
    equity_usd: float = 0.0,
    sl_pct: float = 0.0,
) -> Optional[str]:
    """宪法级开仓否决。返回 None=放行；返回原因码=VETO。

    所有数据缺失时 fail-closed 程度按项区分：
    - equity/margin 缺失 → 跳过比例检查（拿不到数据不该误杀，由下游资金费/敞口闸兜底）
    - sl_pct 提供但越界 → 硬 VETO（这是 LLM 输出的明确错误）
    """
    symbol = str(symbol or "").upper()
    side = str(side or "").lower()

    # 1) 止损幅度硬校验（LLM 输出错误的最后防线）
    if sl_pct and sl_pct > 0:
        if sl_pct < MIN_SL_PCT:
            return f"sl_too_tight:{sl_pct:.4f}<{MIN_SL_PCT}"
        if sl_pct > MAX_SL_PCT:
            return f"sl_too_wide:{sl_pct:.4f}>{MAX_SL_PCT}"

    equity = _safe_float(equity_usd)
    margin = _safe_float(margin_usd)

    # 2) 单笔保证金占比硬顶
    if equity > 0 and margin > 0:
        single_pct = margin / equity
        if single_pct > MAX_SINGLE_TRADE_MARGIN_PCT:
            logger.warning(
                "[Constitution] VETO %s %s 单笔保证金 %.1f%% > %.0f%%",
                symbol, side, single_pct * 100, MAX_SINGLE_TRADE_MARGIN_PCT * 100,
            )
            return f"single_margin:{single_pct:.3f}>{MAX_SINGLE_TRADE_MARGIN_PCT}"

    # 3) 日亏损硬停 + 敞口检查（需要 DB，失败跳过不阻断）
    if account_id is not None and equity > 0:
        try:
            from backend.services.risk_constitution_db import daily_loss_pct, margin_usage
            dl = daily_loss_pct(account_id)
            if dl is not None and dl >= MAX_DAILY_LOSS_PCT_HARD:
                logger.warning(
                    "[Constitution] VETO 日亏损 %.1f%% ≥ %.0f%%（硬停新开仓）",
                    dl * 100, MAX_DAILY_LOSS_PCT_HARD * 100,
                )
                return f"daily_loss_hard:{dl:.3f}>={MAX_DAILY_LOSS_PCT_HARD}"
            usage = margin_usage(account_id, symbol=symbol)
            if usage:
                sym_pct = _safe_float(usage.get("symbol_margin_pct"))
                tot_pct = _safe_float(usage.get("total_margin_pct"))
                if sym_pct and sym_pct > MAX_SYMBOL_MARGIN_PCT:
                    return f"symbol_margin:{sym_pct:.3f}>{MAX_SYMBOL_MARGIN_PCT}"
                if tot_pct and tot_pct > MAX_TOTAL_MARGIN_PCT:
                    return f"total_margin:{tot_pct:.3f}>{MAX_TOTAL_MARGIN_PCT}"
        except Exception as exc:
            # [§59 修复] 宪法级检查被跳过 = 最严的红线没跑，必须可见
            logger.warning("[Constitution] DB 检查跳过(fail-open，未做宪法检查即放行): %s", exc)

    return None
