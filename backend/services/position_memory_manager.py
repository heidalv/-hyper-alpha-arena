"""
拟人仓位管理系统 — Position Memory Manager
=============================================

模拟专业合约交易员的仓位管理行为：
1. 记忆驱动：根据历史交易表现动态调仓
2. 心理状态机：aggressive → normal → cautious → frozen → cooldown
3. 风险守卫：硬性止损、日亏限制、持仓集中度、冷却期
4. 动态 TP/SL：根据波动率和持仓时间自适应调整
5. AI 分析：把记忆和状态作为上下文注入 LLM prompt

设计理念：
  一个好的交易员不是看到信号就开仓。
  他会先回忆"上次类似情况我赚了还是亏了？"
  他会看自己今天的状态："我已经连亏3笔了，缩小仓位"
  他会控制总风险："BTC 仓位已经很重了，不加了"
  他会设定合理目标："高波动就放宽止损，低波动就紧止损"
"""

import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, desc

logger = logging.getLogger(__name__)


def loss_protection_enabled(trading_mode: str = "paper") -> bool:
    try:
        from backend.services.lock_strength_service import get_lock_strength_service
        profile = get_lock_strength_service().get_profile(trading_mode)
        return bool(profile.consecutive_loss_protection)
    except Exception:
        try:
            from backend.config.settings import CONSECUTIVE_LOSS_PROTECTION_ENABLED
            return bool(CONSECUTIVE_LOSS_PROTECTION_ENABLED)
        except Exception:
            return True


def reset_loss_protection_state(db: Session, account_id: Optional[int] = None) -> int:
    """重置连亏保护：frozen/cautious → normal，清零连亏计数与冷却。"""
    from backend.database.models import TraderMentalState

    q = db.query(TraderMentalState)
    if account_id is not None:
        q = q.filter(TraderMentalState.account_id == account_id)
    n = 0
    for mental in q.all():
        changed = False
        if mental.state in ("frozen", "cautious", "cooldown", "tilted"):
            mental.state = "normal"
            changed = True
        if int(mental.consecutive_losses or 0) > 0:
            mental.consecutive_losses = 0
            changed = True
        if mental.cooldown_until is not None:
            mental.cooldown_until = None
            changed = True
        if float(mental.size_multiplier or 1.0) < 1.0:
            mental.size_multiplier = 1.0
            changed = True
        if float(mental.leverage_cap or 20) < 15:
            mental.leverage_cap = 20
            changed = True
        if changed:
            mental.state_reason = "loss_protection_reset"
            n += 1
    if n:
        try:
            db.commit()
        except Exception as exc:
            logger.warning("[PosMgr] reset_loss_protection commit failed: %s", exc)
            db.rollback()
    return n


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """统一把 DB 里的 naive/aware 时间转成 UTC aware，避免比较时报错。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ═══════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════

@dataclass
class PositionPlan:
    """仓位管理器输出的完整开仓计划"""
    action: str                  # "open" / "skip" / "close_and_open" / "close_only"
    symbol: str
    side: str                    # "buy" / "sell"
    leverage: int                # 5~20
    size_pct: float              # 目标名义仓位占可用余额比例 (0.01~0.25)
    notional_usd: float          # 名义价值 (USD)
    margin_usd: float            # 需要的保证金 (USD)
    stop_loss_price: float
    take_profit_price: float
    confidence: float            # 综合置信度 0~1

    # 为什么这么决策（给日志和前端看）
    reasoning: str = ""
    adjustments: Dict[str, float] = field(default_factory=dict)

    # 平仓前序（如果 action=close_and_open，先平哪个）
    close_opposite_side: str = ""


@dataclass
class MemoryInsight:
    """从交易记忆中提取的洞察"""
    symbol_win_rate: float = 0.5
    symbol_avg_pnl_pct: float = 0.0
    symbol_trade_count: int = 0
    regime_win_rate: float = 0.5
    regime_avg_pnl_pct: float = 0.0
    best_leverage_for_symbol: int = 10
    avg_hold_seconds: int = 0
    recommended_size_adj: float = 1.0  # 基于记忆的仓位调节因子

    # 环境表现矩阵（Phase 2.2）
    regime_total_trades: int = 0
    regime_best_symbols: List[str] = field(default_factory=list)
    regime_worst_symbols: List[str] = field(default_factory=list)
    regime_confidence_boost: float = 0.0
    btc_regime_avg_pnl: float = 0.0
    cross_symbol_discount: float = 1.0


# ═══════════════════════════════════════════════════
# 状态机常量
# ═══════════════════════════════════════════════════

MENTAL_STATES = {
    "aggressive": {
        "size_multiplier": 1.3,
        "leverage_cap": 20,
        "description": "连续盈利，适度放大仓位",
        "transition_to_normal": "single_loss",
    },
    "normal": {
        "size_multiplier": 1.0,
        "leverage_cap": 20,
        "description": "正常交易状态",
    },
    "cautious": {
        "size_multiplier": 0.8,
        "leverage_cap": 15,
        "description": "近期亏损，适度缩小仓位",
    },
    "frozen": {
        "size_multiplier": 0.0,
        "leverage_cap": 0,
        "description": "严重亏损，暂停交易",
    },
    "cooldown": {
        "size_multiplier": 0.5,
        "leverage_cap": 18,
        "description": "冷却恢复期，小仓位试水",
    },
}

# 状态转换规则
STATE_TRANSITIONS = {
    # (当前状态, 触发条件) → 新状态
    ("normal", "win_streak_3"): "aggressive",
    ("normal", "loss_streak_cautious"): "cautious",
    ("normal", "daily_loss_3pct"): "cautious",
    ("aggressive", "single_loss"): "normal",
    ("aggressive", "loss_streak_cautious"): "cautious",
    ("aggressive", "daily_loss_3pct"): "cautious",
    # cautious 恢复条件放宽：单胜即可恢复（旧值 win_streak_2 太难达到）
    ("cautious", "single_win"): "normal",
    ("cautious", "win_streak_2"): "normal",
    ("cautious", "loss_streak_frozen"): "frozen",
    ("cautious", "daily_loss_5pct"): "frozen",
    ("frozen", "cooldown_expired"): "cooldown",
    ("frozen", "single_win"): "cooldown",
    ("cooldown", "single_win"): "normal",
    ("cooldown", "win_streak_2"): "normal",
    ("cooldown", "single_loss"): "cautious",
}

# ═══ 三周期差异化风控配置 ═══
# [2026-07-22 修复] TIER_LEVERAGE 三档统一为 10（动态 5-10x 上限）。
# 原配置 scalp=20/swing=10/trend=5 三档不一致，但交易所（Binance/Hyperliquid/
# Aster）对同一 symbol 只支持一个 leverage（symbol 级别），三档差异导致：
# 1. scalp 先开 20x → 该 symbol leverage 被钉死在 20x
# 2. 后续 swing/trend 即使想用 10x/5x 也会被交易所覆盖成 20x
# 3. _unify_leverage_for_side 净额模式取 max，20x 污染所有 tier
# 修复：三档统一为 10x 上限，实际 leverage 由 calculate_dynamic_leverage
# 在 [DYNAMIC_LEVERAGE_MIN=5, DYNAMIC_LEVERAGE_MAX=10] 区间动态决定。
TIER_LEVERAGE = {
    "scalp": 10,        # 短线 10x 上限（动态 5-10x）
    "swing": 10,        # 中线 10x 上限（动态 5-10x）
    "trend_follow": 10, # 长线 10x 上限（动态 5-10x）
}
TIER_MARGIN_PCT = {
    # 单仓保证金占权益比例（直接金额控制，不再用名义百分比）
    "scalp": 0.03,      # 短线 3% ≈ $14（快进快出，小仓位高频）
    "swing": 0.08,      # 中线 8% ≈ $39（适中仓位）
    "trend_follow": 0.15, # 长线 15% ≈ $72（大仓位趋势跟踪）
}
TIER_RESERVE_MULT = {
    # 补仓/滚仓资金预留倍数（初始保证金 × 倍数 = 该仓位总资金需求）
    "scalp": 1.0,       # 短线不补仓
    "swing": 2.0,       # 中线预留 1 倍补仓资金
    "trend_follow": 3.0, # 长线预留 2 倍补仓+滚仓资金
}
TIER_MAX_POSITIONS = {
    # 各 tier 最大同时持仓数
    "scalp": 6,
    "swing": 3,
    "trend_follow": 3,
}

# 风控限制
MAX_DAILY_LOSS_PCT = 0.08           # 当日最大亏损 8% → frozen
CAUTIOUS_DAILY_LOSS_PCT = 0.03      # 当日亏损 3% → cautious
MAX_SINGLE_POSITION_PCT = 1.50      # 单个仓位名义不超过余额 150%
MAX_TOTAL_EXPOSURE_PCT = 0.85       # 总保证金占用不超过余额 85%
try:
    from backend.config.settings import MENTAL_FROZEN_COOLDOWN_MINUTES
    FROZEN_COOLDOWN_MINUTES = int(MENTAL_FROZEN_COOLDOWN_MINUTES)
except Exception:
    FROZEN_COOLDOWN_MINUTES = 10        # frozen 后冷却（可 env 配置，默认 10 分钟）
MEMORY_LOOKBACK_TRADES = 20         # 记忆回看最近 20 笔
EXCHANGE_MIN_MARGIN = 10.0          # 交易所单笔最低保证金 $10

# ── 动态交易频率控制（取代硬上限）──
# 不设固定每日上限，而是交易越多 → 置信度门槛越高 → 只有高质量信号能通过
# 当日交易亏损时摩擦力更大，盈利时摩擦力更小
TRADE_FREQUENCY_TIERS = [
    # (交易笔数阈值, 基础置信度加成)
    # [2026-08-16 用户反馈放宽] 旧档位 5/10/20/30 笔就 +5%~20%，400 笔交易日
    # 置信度门槛被抬到 +50%（亏损日 ×1.5 = +75%）→ 全天信号全部卡死。
    # 改为大档位、小加成：300 笔才 +10%，亏损日最多 +12%。
    (30,  0.03),   # 第 31 笔起 +3%
    (100, 0.06),   # 第 101 笔起 累计 +9%
    (300, 0.10),   # 第 301 笔起 累计 +19%？不——取最大值 +10%
]
# 当日亏损时的额外摩擦倍数
LOSING_DAY_FRICTION_MULT = 1.2
# 当日盈利时的摩擦折扣
WINNING_DAY_FRICTION_MULT = 0.6

# 手续费与反频繁交易
TAKER_FEE_RATE = 0.00035            # Taker 费率 0.035%
MIN_PROFIT_TO_FEE_RATIO = 2.5      # 预期利润须 ≥ 双边手续费的 2.5 倍
SYMBOL_COOLDOWN_MINUTES = 5         # 同一交易对冷却 5 分钟

# 日内翻转硬上限（同一标的每天最多翻转方向次数）
MAX_DAILY_DIRECTION_FLIPS = 3       # 超过后该标的只能 HOLD 或同向操作

# 波动率门禁
# 0.3% 对 BNB/ASTER 等波动率较低的币种过于严格，大量有效信号被误拦
VOL_TOO_LOW = 0.001                 # ATR% < 0.1% → 几乎无波动才拦截
VOL_TOO_HIGH = 0.08                 # ATR% > 8% → 极端行情，降级开仓
VOL_HIGH_SIZE_FACTOR = 0.5          # 高波动时仓位缩至 50%

# 方向集中度 — 动态评估，不设死上限
MAX_SAME_DIRECTION_POSITIONS_SAFETY_NET = 10
DIRECTION_CONCENTRATION_PENALTY_PER_POS = 0.05
DIRECTION_CONCENTRATION_START = 2

# ── 顺势加仓（金字塔）参数 ──
PYRAMID_MAX_ADDS = 2                            # 最多加仓 2 次（含原始共 3 层）— 被 TIER_PYRAMID_PARAMS 覆盖
PYRAMID_MIN_PROFIT_PCT = 0.015                  # 浮盈 >= 1.5% 才可加仓 — 被 TIER_PYRAMID_PARAMS 覆盖
PYRAMID_COOLDOWN_MINUTES = 60                   # 两次加仓间隔 >= 1 小时 — 被 TIER_PYRAMID_PARAMS 覆盖
PYRAMID_SIZE_RATIOS = [0.50, 0.25]              # 第 1 次加仓 50%、第 2 次 25% — 被 TIER_PYRAMID_PARAMS 覆盖
PYRAMID_MIN_CONFIDENCE = 0.35                   # 加仓需要至少 35% 置信度


def trend_pyramid_gate(
    symbol: str,
    side: str,
    add_count: int,
    pnl_pct: float,
    market_summary: dict,
    tier: str = "mid",
) -> tuple:
    """5 层滚仓门控纯函数 — 返回 (通过, 原因)"""
    try:
        from backend.config.settings import TIER_PYRAMID_PARAMS
    except Exception:
        return False, "TIER_PYRAMID_PARAMS 配置缺失"

    tier_cfg = TIER_PYRAMID_PARAMS.get(tier, {})

    # Layer 1 — tier 准入
    if not tier_cfg.get("enabled", False):
        return False, f"tier={tier} 禁止加仓"

    max_adds = tier_cfg.get("max_adds", 2)
    if add_count >= max_adds:
        return False, f"已加仓{add_count}次，达tier上限{max_adds}"

    # Layer 2 — 编排器一致性
    orch = market_summary.get("orchestrator", {})
    orch_action = orch.get("final_action", "wait")
    if orch_action in ("wait", "frozen"):
        return False, f"编排器 action={orch_action}，不宜加仓"

    side_normalized = "long" if side in ("buy", "long") else "short"

    # ── 编排器方向检查（tier 感知版）──
    # 全局 final_side 混入短/中线噪音，不应绑架长线持仓决策。
    # 各 tier 只检查自身周期的 bias 是否与持仓矛盾。
    long_bias = orch.get("long_view_bias", "neutral")
    mid_bias = orch.get("mid_view_bias", "neutral")
    short_bias = orch.get("short_view_bias", "neutral")
    tier_norm = (tier or "mid").lower()
    if tier_norm == "long":
        tier_bias = long_bias
    elif tier_norm == "mid":
        tier_bias = mid_bias
    else:
        tier_bias = short_bias
    if tier_bias in ("bullish", "bearish"):
        tier_side = "long" if tier_bias == "bullish" else "short"
        if tier_side != side_normalized:
            return False, f"{tier_norm}周期方向矛盾(tier_bias={tier_bias} vs 持仓={side_normalized})"

    # Layer 3 — ADX 趋势强度
    indicators = market_summary.get("indicators", {})
    sym_ind = indicators.get(symbol, {})
    min_adx = tier_cfg.get("min_adx", 20)
    # 优先看 4h ADX（中长周期更可靠）
    adx_val = sym_ind.get("adx_4h", sym_ind.get("adx", 0))
    if adx_val < min_adx:
        return False, f"ADX={adx_val:.0f}<门槛{min_adx}（趋势不够强）"

    # Layer 4 — 递减利润门槛
    min_profit_pcts = tier_cfg.get("min_profit_pcts", [0.015, 0.030])
    required_profit = min_profit_pcts[min(add_count, len(min_profit_pcts) - 1)]
    if pnl_pct < required_profit:
        return False, f"浮盈{pnl_pct:.1%}<第{add_count+1}次门槛{required_profit:.1%}"

    # Layer 5 — 冷却时间（在 evaluate_pyramid 内部已有冷却检查，此处做辅助提示）
    # 实际冷却由 evaluate_pyramid 的 last_add_at 校验执行

    return True, f"5层门控通过: tier={tier}, ADX={adx_val:.0f}, 浮盈={pnl_pct:.1%}"

# ── 逆势补仓（DCA）参数 ──
DCA_MAX_ADDS = 1                                # 最多补仓 1 次
DCA_MIN_LOSS_PCT = -0.02                        # 亏损 >= 2% 才考虑补仓
DCA_MAX_LOSS_PCT = -0.08                        # 亏损 > 8% 禁止补仓
DCA_SIZE_RATIO = 0.30                           # 补仓量 = 原始仓位的 30%
DCA_MAX_TOTAL_RATIO = 1.50                      # 补仓后总保证金 <= 原始的 150%
DCA_COOLDOWN_MINUTES = 120                      # 补仓冷却 2 小时
DCA_MIN_CONFIDENCE = 0.40                       # 补仓需要至少 40% 置信度
DCA_MAX_RISK_SCORE = 70                         # 风控分数 > 70 禁止补仓


# tier→nature 唯一权威(阶段 C §2:消除本类与 paper_trading_engine 双映射分歧)
# 模块级别名供 from-import 测试与跨模块一致性校验引用。
from backend.services.tp_sl_authority import TIER_TO_NATURE as _TIER_TO_NATURE  # noqa: E402


class PositionMemoryManager:
    """
    拟人仓位管理器 — 核心服务。

    使用方式：
        plan = position_manager.evaluate_trade(
            db, account_id, symbol, side, confidence,
            current_price, signal_source, market_regime, ...
        )
        # plan.action == "open" → 执行开仓
        # plan.action == "skip" → 跳过
    """

    _symbol_last_trade: Dict[str, float] = {}
    _daily_fees: float = 0.0
    _daily_fees_date: str = ""
    _daily_flip_counts: Dict[str, int] = {}   # key="date_account_symbol", value=flip count
    _last_trade_side: Dict[str, str] = {}     # key="account_symbol", value="buy"|"sell"
    _last_cache_cleanup: str = ""             # 上次缓存清理日期

    # tier→nature 唯一权威(阶段 C §2:统一为 short→scalp/long→trend_follow)
    # 绑定到模块级 _TIER_TO_NATURE(同对象),保留类属性访问 self._TIER_TO_NATURE。
    _TIER_TO_NATURE = _TIER_TO_NATURE

    def _load_personality(self, db: Session, account_id: int):
        """加载交易员性格，返回 TraderPersonality 或 None。"""
        from backend.database.models import TraderPersonality
        return db.query(TraderPersonality).filter(
            TraderPersonality.account_id == account_id
        ).first()

    def _personality_state_thresholds(self, personality) -> dict:
        """
        根据性格参数动态调整状态机转换阈值。
        返回: {loss_to_cautious, loss_to_frozen, win_to_aggressive, win_to_normal_from_cooldown}
        """
        if not personality:
            try:
                from backend.config.settings import (
                    MENTAL_LOSS_TO_CAUTIOUS,
                    MENTAL_LOSS_TO_FROZEN,
                )
                result = {
                    "loss_to_cautious": int(MENTAL_LOSS_TO_CAUTIOUS),
                    "loss_to_frozen": int(MENTAL_LOSS_TO_FROZEN),
                    "win_to_aggressive": 3,
                    "win_to_normal_from_cooldown": 2,
                }
            except Exception:
                result = {
                    "loss_to_cautious": 3,
                    "loss_to_frozen": 6,
                    "win_to_aggressive": 3,
                    "win_to_normal_from_cooldown": 2,
                }
            return self._apply_warmup_frozen_relax(result)

        lt = personality.loss_tolerance  # 1~10
        wa = personality.win_aggression  # 1~10

        # loss_tolerance 低 → 更快进入 cautious/frozen
        loss_to_cautious = max(1, round(lt / 3))       # lt=2→1, lt=5→2, lt=8→3
        loss_to_frozen = max(2, round(lt / 2))          # lt=2→1→2, lt=5→3, lt=8→4

        # win_aggression 高 → 更容易进入 aggressive
        win_to_aggressive = max(1, 4 - round(wa / 3))   # wa=8→1, wa=5→2, wa=2→3

        return self._apply_warmup_frozen_relax({
            "loss_to_cautious": loss_to_cautious,
            "loss_to_frozen": loss_to_frozen,
            "win_to_aggressive": win_to_aggressive,
            "win_to_normal_from_cooldown": max(1, 3 - round(wa / 5)),
        })

    @staticmethod
    def _apply_warmup_frozen_relax(thresholds: dict) -> dict:
        """warmup/growth 期上调连亏阈值，容忍更多连亏以累积数据。

        live 恒 mature 不放宽；保命硬门（日亏上限/熔断）不在此处。
        """
        try:
            from backend.services.maturity_controller import get_global_stage
            stage = get_global_stage("paper")
            if stage == "warmup":
                thresholds["loss_to_cautious"] = int(thresholds.get("loss_to_cautious", 3)) + 2
                thresholds["loss_to_frozen"] = int(thresholds.get("loss_to_frozen", 6)) + 3
            elif stage == "growth":
                thresholds["loss_to_cautious"] = int(thresholds.get("loss_to_cautious", 3)) + 1
                thresholds["loss_to_frozen"] = int(thresholds.get("loss_to_frozen", 6)) + 1
        except Exception:
            pass
        return thresholds

    def _personality_size_multiplier(self, personality, base_mult: float) -> float:
        """根据性格的 win_aggression 调整 aggressive 状态的仓位倍率。"""
        if not personality:
            return base_mult
        wa = personality.win_aggression
        if base_mult > 1.0:
            # aggressive 状态: wa 越高，倍率越大
            return 1.0 + (wa / 10) * 0.5  # wa=2→1.1, wa=5→1.25, wa=8→1.4, wa=10→1.5
        return base_mult

    def evaluate_trade(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,            # "buy" / "sell"
        ai_confidence: float,  # 0~1
        current_price: float,
        signal_source: str = "unknown",
        market_regime: str = "unknown",
        volatility_pct: float = 0.015,
        raw_leverage: int = 10,
        raw_position_pct: float = 0,
        raw_notional_usd: float = 0,
        raw_margin_usd: float = 0,
        # 2026-06-18: AI 主驾改造，默认 True —— 用上游 SizingPlan(position_sizing_agent)
        # 的 notional，本层只做安全裁剪（margin 上限），不再用心理态/记忆/波动重算仓位。
        respect_raw_sizing: bool = True,
        raw_tp_price: float = 0,
        raw_sl_price: float = 0,
        strategy_id: str = "",
        tier: str = "swing",
        trade_nature: str = "",
        orchestrator_context: Optional[dict] = None,
    ) -> PositionPlan:
        """评估是否应该开仓 + 如何开仓。返回完整执行计划。"""
        from backend.database.models import (
            PaperBalance, PaperPosition, TraderMentalState
        )

        adjustments = {}
        reasons = []

        # ── 0. 加载交易员性格 ──
        personality = self._load_personality(db, account_id)
        if personality:
            reasons.append(f"性格={personality.display_name or '自定义'}")

        # 性格参数（有性格用性格值，无性格用默认值）
        # 杠杆下限保护：防止旧 personality 数据（如 preferred=6, max=8）锁死杠杆
        p_min_conf = personality.min_confidence if personality else 0.05
        p_max_pos = personality.max_position_pct if personality else MAX_SINGLE_POSITION_PCT
        p_pref_lev = max(10, personality.preferred_leverage) if personality else 10
        p_max_lev = max(20, personality.max_leverage) if personality else 20

        # ── 0.5 置信度门槛检查 ──
        # 小资金账户（<$200）适当降低置信度门槛，避免完全无法交易
        # 门槛最低降至原始值的60%，绝对下限15%
        _effective_min_conf = p_min_conf
        try:
            _bal_check = db.query(PaperBalance).filter(
                PaperBalance.account_id == account_id).first()
            if _bal_check and float(_bal_check.total_equity) < 200:
                _equity_ratio = max(0.6, float(_bal_check.total_equity) / 200)
                _effective_min_conf = max(0.15, p_min_conf * _equity_ratio)
                if _effective_min_conf < p_min_conf:
                    reasons.append(f"小资金置信门槛{p_min_conf:.0%}→{_effective_min_conf:.0%}")
        except Exception:
            pass

        if ai_confidence < _effective_min_conf:
            return self._skip_plan(symbol, side,
                f"置信度{ai_confidence:.0%}<门槛{_effective_min_conf:.0%}"
                f"({'性格' if personality else '默认'})")

        # ── 1. 获取账户状态 ──
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == account_id
        ).first()
        if not bal or bal.total_equity <= 0:
            return self._skip_plan(symbol, side, "账户无余额")

        equity = float(bal.total_equity)
        available = float(bal.available_balance)
        frozen_margin = float(bal.frozen_margin)

        # ── 2. 获取/更新心理状态 ──
        mental = self._get_or_create_mental_state(db, account_id)
        self._normalize_mental_state(db, mental, personality)

        state_config = MENTAL_STATES.get(mental.state, MENTAL_STATES["normal"])
        size_mult = state_config["size_multiplier"]
        lev_cap = state_config["leverage_cap"]

        # 性格微调仓位倍率
        size_mult = self._personality_size_multiplier(personality, size_mult)

        # frozen → 默认拒绝；高置信可「试探仓」放行（连亏保护关闭时跳过）
        _frozen_probe = False
        if loss_protection_enabled() and mental.state == "frozen":
            now_utc = datetime.now(timezone.utc)
            cooldown_until = _as_utc(mental.cooldown_until)
            if cooldown_until and now_utc < cooldown_until:
                try:
                    from backend.config.settings import (
                        MENTAL_FROZEN_PROBE_SIZE_MULT,
                        MENTAL_HIGH_CONF_FROZEN_BYPASS,
                    )
                    if ai_confidence >= float(MENTAL_HIGH_CONF_FROZEN_BYPASS):
                        size_mult *= float(MENTAL_FROZEN_PROBE_SIZE_MULT)
                        lev_cap = min(lev_cap, 8)
                        _frozen_probe = True
                        reasons.append(
                            f"冻结期高置信试探(≥{MENTAL_HIGH_CONF_FROZEN_BYPASS:.0%}, "
                            f"size×{MENTAL_FROZEN_PROBE_SIZE_MULT})"
                        )
                        logger.info(
                            "[PosMgr] %s %s 冻结期高置信试探放行 conf=%.0f%%",
                            symbol, side, ai_confidence * 100,
                        )
                    else:
                        _block_reason = self._format_block_reason(
                            mental,
                            self._daily_drawdown_pct(db, mental),
                            (cooldown_until - now_utc).total_seconds() / 60.0,
                        )
                        _reason = (
                            f"交易冻结中，冷却剩余{(cooldown_until - now_utc).total_seconds() / 60:.0f}分钟 "
                            f"({_block_reason}，日PnL={mental.daily_pnl:+.2f}；"
                            f"需置信≥{MENTAL_HIGH_CONF_FROZEN_BYPASS:.0%}才可试探开仓)"
                        )
                        logger.info("[PosMgr][SKIP] %s %s: %s", symbol, side, _reason)
                        return self._skip_plan(symbol, side, _reason)
                except Exception:
                    _reason = (
                        f"交易冻结中，冷却剩余{(cooldown_until - now_utc).total_seconds() / 60:.0f}分钟 "
                        f"(连亏{mental.consecutive_losses}笔，日亏{mental.daily_pnl:+.2f})"
                    )
                    logger.info("[PosMgr][SKIP] %s %s: %s", symbol, side, _reason)
                    return self._skip_plan(symbol, side, _reason)
            else:
                self._transition_state(db, mental, "cooldown_expired", personality)
                state_config = MENTAL_STATES["cooldown"]
                size_mult = state_config["size_multiplier"]
                lev_cap = state_config["leverage_cap"]

        adjustments["mental_state"] = size_mult
        reasons.append(f"状态={mental.state}(×{size_mult:.1f})")

        # ── 3. 动态交易频率评估（取代硬上限）──
        # 交易越多 → 置信度门槛渐进提高；亏损天更严格，盈利天更宽松
        freq_penalty = 0.0
        for tier_count, tier_add in TRADE_FREQUENCY_TIERS:
            if mental.daily_trades >= tier_count:
                freq_penalty = tier_add
        if freq_penalty > 0:
            # [2026-08-16 用户反馈] 频率摩擦降级为「仅提示」：不再硬性拦截。
            # 原实现按 mental.daily_trades 硬性抬门槛——该计数器把套利腿/研究腿
            # 也计入（今日实际开仓仅 8 笔，计数器却是 400），400 笔 → 置信度
            # 门槛 +50%~75% → 全天信号全部被卡死（用户看到的「跑起来就卡死」）。
            # 现在只把摩擦信息写进上下文供 LLM 参考，绝不阻断交易。
            day_pnl_factor = (
                LOSING_DAY_FRICTION_MULT if mental.daily_pnl < 0
                else WINNING_DAY_FRICTION_MULT
            )
            effective_penalty = freq_penalty * day_pnl_factor
            required_conf = _effective_min_conf + effective_penalty
            reasons.append(
                f"频率摩擦(仅提示): 今日{mental.daily_trades}笔,"
                f"参考门槛+{effective_penalty:.0%}→{required_conf:.0%}"
            )

        daily_loss_pct = abs(mental.daily_pnl / equity) if equity > 0 and mental.daily_pnl < 0 else 0
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
            self._transition_state(db, mental, "daily_loss_5pct", personality)
            return self._skip_plan(symbol, side,
                f"日亏损{daily_loss_pct:.1%}≥{MAX_DAILY_LOSS_PCT:.0%}，进入冻结")

        # ── 3.1 同一交易对冷却（按 trade_nature 隔离，不同 nature 可独立开仓）──
        import time as _time
        cooldown_key = f"{account_id}_{symbol}_{side}_{trade_nature or tier}"
        last_ts = self.__class__._symbol_last_trade.get(cooldown_key, 0)
        elapsed_min = (_time.time() - last_ts) / 60
        if elapsed_min < SYMBOL_COOLDOWN_MINUTES:
            remaining = SYMBOL_COOLDOWN_MINUTES - elapsed_min
            return self._skip_plan(symbol, side,
                f"{symbol}[{trade_nature or tier}]交易冷却中(剩余{remaining:.0f}分钟)，防止频繁交易")

        # ── 3.15 日内翻转次数硬上限 ──
        # 2026-06-18: 统一用 UTC 日期，与 _check_daily_reset 的 UTC 基准一致。
        # 原用 date.today()(本地)导致北京 0-8 点 UTC 已新一天但本地还是旧一天，
        # 日重置和翻转计数不同步。
        from datetime import date as _date_cls, timezone as _tz_cls
        _today_str = (datetime.now(_tz_cls.utc)).date().isoformat()
        _flip_key = f"{_today_str}_{account_id}_{symbol}"
        _side_key = f"{account_id}_{symbol}"
        _last_side = self.__class__._last_trade_side.get(_side_key)
        if _last_side and _last_side != side:
            _cur_flips = self.__class__._daily_flip_counts.get(_flip_key, 0) + 1
            if _cur_flips > MAX_DAILY_DIRECTION_FLIPS:
                return self._skip_plan(symbol, side,
                    f"{symbol}今日已翻转方向{_cur_flips-1}次≥上限{MAX_DAILY_DIRECTION_FLIPS}次，"
                    f"禁止再次反向开仓（防止多空横跳损耗手续费）")

        # ── 3.2 手续费覆盖检查 ──
        tp_price = raw_tp_price
        if current_price and current_price > 0 and tp_price and tp_price > 0:
            tp_distance_pct = abs(tp_price - current_price) / current_price
            round_trip_fee_pct = TAKER_FEE_RATE * 2
            if tp_distance_pct < round_trip_fee_pct * MIN_PROFIT_TO_FEE_RATIO:
                return self._skip_plan(symbol, side,
                    f"预期利润{tp_distance_pct:.3%}不足以覆盖手续费"
                    f"(双边{round_trip_fee_pct:.3%}×{MIN_PROFIT_TO_FEE_RATIO}="
                    f"{round_trip_fee_pct * MIN_PROFIT_TO_FEE_RATIO:.3%})")

        # ── 3.5 波动率门禁 ──
        if volatility_pct < VOL_TOO_LOW:
            return self._skip_plan(symbol, side,
                f"波动率{volatility_pct:.2%}过低(<{VOL_TOO_LOW:.2%})，震荡区间不值得开仓")

        vol_size_penalty = 1.0
        if volatility_pct > VOL_TOO_HIGH:
            vol_size_penalty = VOL_HIGH_SIZE_FACTOR
            reasons.append(f"高波动({volatility_pct:.2%})，仓位降至{VOL_HIGH_SIZE_FACTOR:.0%}")

        # ── 3.6 方向集中度动态评估（不设死上限，置信度门槛渐进提高）──
        desired_pos_side_early = "long" if side == "buy" else "short"
        same_dir_count = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.side == desired_pos_side_early,
            PaperPosition.status == "open",
        ).count()

        # 极端安全网
        if same_dir_count >= MAX_SAME_DIRECTION_POSITIONS_SAFETY_NET:
            return self._skip_plan(symbol, side,
                f"同方向({desired_pos_side_early})已有{same_dir_count}个持仓"
                f"≥极端安全网{MAX_SAME_DIRECTION_POSITIONS_SAFETY_NET}，系统保护")

        # 动态惩罚：同方向持仓越多，要求越高置信度才能继续开仓
        if same_dir_count > DIRECTION_CONCENTRATION_START:
            extra_positions = same_dir_count - DIRECTION_CONCENTRATION_START
            concentration_penalty = extra_positions * DIRECTION_CONCENTRATION_PENALTY_PER_POS
            required_conf = _effective_min_conf + concentration_penalty
            if ai_confidence < required_conf:
                return self._skip_plan(symbol, side,
                    f"同方向({desired_pos_side_early})已有{same_dir_count}个持仓，"
                    f"集中度门槛需{required_conf:.0%}(当前{ai_confidence:.0%})，"
                    f"建议等待更强信号或分散方向")
            reasons.append(
                f"集中度: 同向{same_dir_count}个,"
                f"门槛+{concentration_penalty:.0%}→需{required_conf:.0%}"
            )

        # ── 3.7 成交量确认（量能衰竭检测）──
        vol_confirm_ok = True
        try:
            from backend.database.models import CryptoKline
            # M1 收口：统一 K 线查询门面（数据中心）
            from backend.services.kline_data_service import kline_service as _ks
            recent_5m = _ks.query_klines(symbol.upper(), "5m", limit=12, order="desc") or []
            if recent_5m and len(recent_5m) >= 6:
                vols = [float(k.get("volume") or 0) for k in reversed(recent_5m)]
                recent_avg = sum(vols[-3:]) / 3 if vols[-3:] else 0
                earlier_avg = sum(vols[:3]) / 3 if vols[:3] else 1
                if earlier_avg > 0:
                    vol_ratio = recent_avg / earlier_avg
                    # 反转做空：需要量能萎缩（反弹无量 → 更安全）
                    # 顺势做多：量能放大更好
                    if side == "sell" and vol_ratio > 2.0:
                        reasons.append(f"⚠️ 量能放大({vol_ratio:.1f}x)做空需谨慎")
                        vol_size_penalty *= 0.7
                    elif side == "buy" and vol_ratio < 0.3:
                        reasons.append(f"⚠️ 量能极度萎缩({vol_ratio:.1f}x)做多需谨慎")
                        vol_size_penalty *= 0.7
        except Exception as _vol_err:
            logger.debug(f"[PosMgr] 成交量检查异常(非致命): {_vol_err}")

        # ── 4. 查询交易记忆 ──
        memory = self._query_memory(db, account_id, symbol, market_regime)
        adjustments["memory_adj"] = memory.recommended_size_adj
        if memory.symbol_trade_count > 0:
            reasons.append(
                f"记忆: {symbol}胜率{memory.symbol_win_rate:.0%}"
                f"({memory.symbol_trade_count}笔), "
                f"环境[{market_regime}]胜率{memory.regime_win_rate:.0%}"
            )

        # ── 4.5 环境记忆矩阵检查 ──
        if memory.regime_confidence_boost > 0:
            boosted_threshold = _effective_min_conf + memory.regime_confidence_boost
            if ai_confidence < boosted_threshold:
                return self._skip_plan(symbol, side,
                    f"环境[{market_regime}]历史胜率低"
                    f"({memory.regime_total_trades}笔)，"
                    f"需置信度{boosted_threshold:.0%}(当前{ai_confidence:.0%})")
            reasons.append(
                f"环境摩擦: [{market_regime}]胜率偏低，"
                f"门槛+{memory.regime_confidence_boost:.0%}")

        if memory.cross_symbol_discount < 1.0:
            reasons.append(
                f"BTC环境PnL={memory.btc_regime_avg_pnl:+.1%}→"
                f"山寨折扣×{memory.cross_symbol_discount:.2f}")

        if memory.regime_best_symbols:
            reasons.append(
                f"环境最佳: {','.join(memory.regime_best_symbols[:3])}")

        # ── 5. 检查现有持仓（按 strategy_id + trade_nature 隔离，不同 nature 可独立开仓）──
        desired_pos_side = "long" if side == "buy" else "short"
        opposite_pos_side = "short" if side == "buy" else "long"

        same_query = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.side == desired_pos_side,
            PaperPosition.status == "open",
        )
        if strategy_id:
            same_query = same_query.filter(PaperPosition.strategy_id == strategy_id)
        # 按 trade_nature 隔离：不同 nature（如 swing/intraday/trend_follow）可独立开仓
        if trade_nature:
            same_query = same_query.filter(PaperPosition.trade_nature == trade_nature)
        existing_same = same_query.first()

        if existing_same and existing_same.size > 0:
            return self._skip_plan(symbol, side,
                f"已有{desired_pos_side}仓(策略={strategy_id or '未知'}, nature={trade_nature or '未知'}) "
                f"size={existing_same.size:.6f}")

        existing_opposite = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.side == opposite_pos_side,
            PaperPosition.status == "open",
        ).first()

        close_opposite = False
        close_reason = ""
        if existing_opposite and existing_opposite.size > 0:
            opp_pnl = float(existing_opposite.unrealized_pnl or 0)
            opp_margin = float(existing_opposite.margin or 1)
            opp_pnl_pct = opp_pnl / opp_margin if opp_margin > 0 else 0

            try:
                from backend.config.settings import (
                    RISK_AI_REVERSE_MIN_CONF,
                    RISK_AI_REVERSE_MICRO_LOSS_PCT,
                    RISK_AI_REVERSE_MICRO_LOSS_MIN_CONF,
                )
            except Exception:
                RISK_AI_REVERSE_MIN_CONF = 0.65
                RISK_AI_REVERSE_MICRO_LOSS_PCT = 0.03
                RISK_AI_REVERSE_MICRO_LOSS_MIN_CONF = 0.55

            if ai_confidence >= RISK_AI_REVERSE_MIN_CONF:
                close_opposite = True
                close_reason = f"强反转(置信{ai_confidence:.0%}≥{RISK_AI_REVERSE_MIN_CONF:.0%})"
            elif opp_pnl_pct <= -0.02:
                close_opposite = True
                close_reason = f"反向仓亏损({opp_pnl_pct:+.1%}≤-2%)"
            elif (
                opp_pnl_pct <= -RISK_AI_REVERSE_MICRO_LOSS_PCT
                and ai_confidence >= RISK_AI_REVERSE_MICRO_LOSS_MIN_CONF
            ):
                close_opposite = True
                close_reason = (
                    f"反向仓微亏({opp_pnl_pct:+.1%})+强信号({ai_confidence:.0%})"
                )
            else:
                return self._skip_plan(symbol, side,
                    f"有{opposite_pos_side}仓(PnL={opp_pnl:+.2f},{opp_pnl_pct:+.1%})，"
                    f"信号{ai_confidence:.0%}不足以反转")

            if close_opposite and orchestrator_context:
                try:
                    from backend.services.decision_core.direction_coherence import (
                        evaluate_direction_coherence,
                    )
                    _rev_action = "buy" if side == "buy" else "sell"
                    _dcp = evaluate_direction_coherence(
                        action=_rev_action,
                        confidence=ai_confidence * 100,
                        tier=tier or "mid",
                        trade_nature=trade_nature or "swing",
                        orchestrator=orchestrator_context,
                        symbol=symbol,
                    )
                    if not _dcp.allowed:
                        return self._skip_plan(
                            symbol, side,
                            f"DCP禁止翻仓: {_dcp.reason}",
                        )
                except Exception as _dcp_rev_err:
                    logger.debug(f"[PosMgr] ai_reverse DCP 检查异常(放行): {_dcp_rev_err}")

            # ══════════════════════════════════════════════════════════
            # P3 M2 — 同 symbol ai_reverse 冷却
            # 证据: 7 天 12 次 ai_reverse 胜率 17%，ASTER/BNB/XPL 均多次重复反向
            # 策略: 如果最近 cooldown_sec 内本 symbol 已 ai_reverse 过一次，
            #       直接降级为 "不反转"（skip_plan），不允许 close_and_open。
            # flag: RISK_P3_AI_REVERSE_COOLDOWN_SEC (0 = 禁用)
            # ══════════════════════════════════════════════════════════
            if close_opposite:
                try:
                    from backend.config.settings import (
                        RISK_P3_ENABLED, RISK_P3_AI_REVERSE_COOLDOWN_SEC,
                    )
                    from backend.services.reentry_cooldown import is_ai_reverse_blocked
                    if RISK_P3_ENABLED and RISK_P3_AI_REVERSE_COOLDOWN_SEC > 0:
                        _blocked, _why = is_ai_reverse_blocked(
                            account_id, symbol, RISK_P3_AI_REVERSE_COOLDOWN_SEC,
                        )
                        if _blocked:
                            # 写决策日志，便于事后统计拦下了多少
                            try:
                                from backend.services.decision_arbiter import (
                                    log_close_request, CloseRequest,
                                )
                                log_close_request(CloseRequest(
                                    symbol=symbol, source="ai_reverse",
                                    reason_intended="ai_reverse",
                                    pos_tier=(tier or "").strip().lower(),
                                    pos_side=opposite_pos_side,
                                    pnl_pct=opp_pnl_pct,
                                    confidence=ai_confidence,
                                    would_block=True,
                                    block_rule="p3_m2_ai_reverse_cooldown",
                                    extra={"cooldown_sec": RISK_P3_AI_REVERSE_COOLDOWN_SEC,
                                           "detail": _why},
                                ))
                            except Exception:
                                pass
                            logger.info(
                                f"[PosMgr][P3.M2] {symbol} ai_reverse 被冷却拦下: {_why}"
                            )
                            return self._skip_plan(symbol, side, f"P3.M2 ai_reverse 冷却: {_why}")
                except Exception as _e_p3m2:
                    logger.debug(f"[PosMgr][P3.M2] ai_reverse 冷却检查异常(放行): {_e_p3m2}")

        # ── 6. 总敞口检查 ──
        total_margin_after = frozen_margin
        if close_opposite and existing_opposite:
            total_margin_after -= float(existing_opposite.margin or 0)

        exposure_pct = total_margin_after / equity if equity > 0 else 0
        if exposure_pct >= MAX_TOTAL_EXPOSURE_PCT:
            return self._skip_plan(symbol, side,
                f"总敞口{exposure_pct:.0%}≥{MAX_TOTAL_EXPOSURE_PCT:.0%}上限")

        remaining_capacity = max(0, equity * MAX_TOTAL_EXPOSURE_PCT - total_margin_after)

        # ── 7. 计算最终杠杆（按 tier 差异化 + 性格偏好 + 上限覆盖）──
        # 三周期杠杆分层：短线 20x / 中线 10x / 长线 5x
        _tier_leverage = TIER_LEVERAGE.get(trade_nature, 10)
        effective_raw_lev = min(_tier_leverage, raw_leverage if raw_leverage > 0 else _tier_leverage)
        effective_lev_cap = min(lev_cap, p_max_lev, _tier_leverage)

        leverage = self._calc_leverage(
            effective_raw_lev, ai_confidence, volatility_pct,
            effective_lev_cap, memory
        )
        adjustments["leverage"] = leverage

        # ── 8. 计算仓位大小（按 tier 差异化保证金）──
        # 三周期保证金分层：短线 3% / 中线 8% / 长线 15%（占权益）
        # 补仓预留：中线预留 1x、长线预留 2x 初始保证金
        _tier_margin_pct = TIER_MARGIN_PCT.get(trade_nature, 0.05)
        _tier_reserve = TIER_RESERVE_MULT.get(trade_nature, 1.0)
        # 目标保证金 = 权益 × tier 比例（考虑补仓预留后不能超总敞口上限）
        _target_margin = equity * _tier_margin_pct
        _target_margin_with_reserve = _target_margin * _tier_reserve
        # 确保有足够剩余容量（含补仓预留）
        if _target_margin_with_reserve > remaining_capacity:
            _target_margin = remaining_capacity / _tier_reserve  # 缩减到预留后不超限
        _target_notional = _target_margin * leverage

        if respect_raw_sizing and raw_notional_usd and raw_notional_usd > 0:
            # 上游有 SizingPlan 时，取 min(上游, tier 目标)
            notional = min(float(raw_notional_usd), _target_notional, remaining_capacity * leverage)
            margin = notional / leverage if leverage > 0 else notional
            final_pct = notional / available if available > 0 else 0.0
            reasons.append(
                f"tier={trade_nature} lev={leverage}x: 名义=${notional:.0f} 保证金=${margin:.0f}"
                f" (目标${_target_margin:.0f}×{_tier_reserve}预留=${_target_margin_with_reserve:.0f})"
            )
        else:
            if raw_position_pct and raw_position_pct > 0:
                base_pct = max(0.08, min(2.0, float(raw_position_pct)))
                reasons.append(f"AI策略仓位={base_pct:.1%}")
            else:
                base_pct = self._calc_base_size_pct(ai_confidence, volatility_pct)
            final_pct = (
                base_pct
                * size_mult                        # 心理状态
                * memory.recommended_size_adj      # 记忆调节
                * vol_size_penalty                 # 波动率/量能惩罚
            )
            final_pct = min(final_pct, p_max_pos)

            # ── tier 差异化保证金覆盖 ──
            # 用 tier 配置的保证金比例直接计算，不走小资金提升逻辑
            notional = _target_notional
            max_notional_by_margin = remaining_capacity * leverage
            notional = min(notional, max_notional_by_margin)
            margin = notional / leverage if leverage > 0 else notional
            final_pct = notional / available if available > 0 else 0.0
            reasons.append(
                f"tier={trade_nature} lev={leverage}x: 名义=${notional:.0f} 保证金=${margin:.0f}"
                f" (目标${_target_margin:.0f}×{_tier_reserve}预留=${_target_margin_with_reserve:.0f})"
            )
            max_notional_by_margin = remaining_capacity * leverage
            notional = min(notional, max_notional_by_margin)
            margin = notional / leverage if leverage > 0 else notional

        adjustments["base_pct"] = round(base_pct, 4)
        adjustments["final_pct"] = round(final_pct, 4)
        if respect_raw_sizing:
            adjustments["sizing_source"] = "upstream_sizing_plan"

        if margin < EXCHANGE_MIN_MARGIN:
            return self._skip_plan(symbol, side,
                f"计算保证金=${margin:.2f}<${EXCHANGE_MIN_MARGIN}最低要求"
                f"(需余额≥${EXCHANGE_MIN_MARGIN})")

        # ── 9. 计算 TP/SL（按 tier 差异化：短线紧、长线宽）──
        tp, sl = self._calc_tp_sl(
            side, current_price, leverage, volatility_pct,
            raw_tp_price, raw_sl_price, memory, tier=tier
        )

        # ── 10. 构建执行计划 ──
        action = "close_and_open" if close_opposite else "open"
        reasoning = " | ".join(reasons)
        reasoning += f" | 杠杆={leverage}x, 名义仓位={final_pct:.1%}×余额"

        plan = PositionPlan(
            action=action,
            symbol=symbol,
            side=side,
            leverage=leverage,
            size_pct=final_pct,
            notional_usd=round(notional, 2),
            margin_usd=round(margin, 2),
            stop_loss_price=sl,
            take_profit_price=tp,
            confidence=ai_confidence,
            reasoning=reasoning,
            adjustments=adjustments,
            close_opposite_side=opposite_pos_side if close_opposite else "",
        )

        logger.info(
            f"[PosMgr] {symbol} {side}: action={action} "
            f"lev={leverage}x size={final_pct:.1%} "
            f"notional=${notional:.0f} margin=${margin:.0f} "
            f"TP=${tp:.2f} SL=${sl:.2f} | "
            f"state={mental.state} mem_adj={memory.recommended_size_adj:.2f}"
            f"{' personality=' + personality.display_name if personality else ''}"
        )

        return plan

    # ═══════════════════════════════════════════════════
    # 交易结果记录 + 状态更新（平仓后调用）
    # ═══════════════════════════════════════════════════

    def record_trade_result(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        size: float,
        leverage: float,
        pnl: float,
        fee: float,
        hold_seconds: int,
        margin_used: float,
        close_reason: str = "unknown",
        signal_source: str = "unknown",
        market_regime: str = "unknown",
        confidence_at_entry: float = 0,
        volatility_at_entry: float = 0,
    ):
        """记录一笔已平仓交易到记忆，并更新心理状态。"""
        from backend.database.models import TradeMemoryRecord

        pnl_pct = pnl / margin_used if margin_used > 0 else 0

        record = TradeMemoryRecord(
            account_id=account_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            leverage=leverage,
            margin_used=margin_used,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=fee,
            hold_seconds=hold_seconds,
            market_regime=market_regime,
            signal_source=signal_source,
            confidence_at_entry=confidence_at_entry,
            volatility_at_entry=volatility_at_entry,
            close_reason=close_reason,
            opened_at=datetime.now(timezone.utc) - timedelta(seconds=hold_seconds),
        )
        db.add(record)

        # 更新心理状态
        mental = self._get_or_create_mental_state(db, account_id)
        self._check_daily_reset(db, mental)
        is_win = pnl > 0

        if is_win:
            mental.consecutive_wins += 1
            mental.consecutive_losses = 0
        else:
            mental.consecutive_losses += 1
            mental.consecutive_wins = 0

        mental.streak_pnl += pnl
        # [2026-08-16 修复] 套利/研究腿（pair_research/rebate_arb 等）2 秒级
        # 高速开平不应计入「今日交易笔数」——旧实现把套利腿全部累计进来，
        # 一晚上 400 笔 → 频率摩擦把真实交易信号全部卡死。仅统计真实交易。
        _ss = str(signal_source or "").lower()
        _is_arb_like = any(k in _ss for k in ("arb", "pair", "research", "rebate"))
        if not _is_arb_like:
            mental.daily_trades += 1
        mental.daily_pnl += (pnl - fee)
        mental.last_trade_at = datetime.now(timezone.utc)

        import time as _time
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.__class__._daily_fees_date != today_str:
            self.__class__._daily_fees = 0.0
            self.__class__._daily_fees_date = today_str
            # 每日自动清理过期缓存，防止内存无限增长
            if self.__class__._last_cache_cleanup != today_str:
                self.__class__._last_cache_cleanup = today_str
                _old_keys = [k for k in self.__class__._daily_flip_counts
                             if not k.startswith(today_str)]
                for k in _old_keys:
                    del self.__class__._daily_flip_counts[k]
                _now = _time.time()
                _stale = [k for k, v in self.__class__._symbol_last_trade.items()
                          if _now - v > 86400]
                for k in _stale:
                    del self.__class__._symbol_last_trade[k]
                if _old_keys or _stale:
                    logger.info(f"[PosMgr] 缓存清理: flip_counts={len(_old_keys)} "
                                f"stale_trade_ts={len(_stale)}")
        self.__class__._daily_fees += fee
        for s in ("buy", "sell"):
            self.__class__._symbol_last_trade[f"{account_id}_{symbol}_{s}"] = _time.time()

        # 日内翻转计数
        # 2026-06-18: 统一 UTC 日期，与 _check_daily_reset 一致（见上方 3.15 说明）。
        _today2 = (datetime.now(timezone.utc)).date().isoformat()
        _side_key2 = f"{account_id}_{symbol}"
        _flip_key2 = f"{_today2}_{account_id}_{symbol}"
        _prev_side = self.__class__._last_trade_side.get(_side_key2)
        _trade_side = "buy" if side == "long" else "sell"
        if _prev_side and _prev_side != _trade_side:
            self.__class__._daily_flip_counts[_flip_key2] = (
                self.__class__._daily_flip_counts.get(_flip_key2, 0) + 1)
            logger.info(
                f"[PosMgr] {symbol} 方向翻转: {_prev_side}→{_trade_side}，"
                f"今日累计翻转{self.__class__._daily_flip_counts[_flip_key2]}次")
        self.__class__._last_trade_side[_side_key2] = _trade_side

        # 日最大回撤
        if mental.daily_pnl < 0:
            from backend.database.models import PaperBalance
            bal = db.query(PaperBalance).filter(
                PaperBalance.account_id == account_id
            ).first()
            if bal and bal.total_equity > 0:
                dd = abs(mental.daily_pnl) / bal.total_equity
                mental.daily_max_drawdown = max(mental.daily_max_drawdown, dd)

        # 状态转换（传入性格以动态调整阈值）
        personality = self._load_personality(db, account_id)
        self._evaluate_transitions(db, mental, personality)

        # 刷新记忆摘要
        self._refresh_memory_summary(db, mental, account_id)

        db.commit()

        logger.info(
            f"[PosMgr] 记录交易: {symbol} {side} PnL=${pnl:+.2f}({pnl_pct:+.1%}) "
            f"→ state={mental.state} "
            f"wins={mental.consecutive_wins} losses={mental.consecutive_losses} "
            f"daily_pnl=${mental.daily_pnl:+.2f}"
        )

    # ═══════════════════════════════════════════════════
    # 生成 AI prompt 上下文
    # ═══════════════════════════════════════════════════

    def get_ai_context(self, db: Session, account_id: int) -> str:
        """生成注入 LLM prompt 的仓位管理上下文（含性格档案）。"""
        from backend.database.models import TraderMentalState

        mental = db.query(TraderMentalState).filter(
            TraderMentalState.account_id == account_id
        ).first()

        if not mental:
            return ""

        state_desc = MENTAL_STATES.get(mental.state, {}).get("description", "")
        daily_fees = self.__class__._daily_fees

        # 计算当前频率摩擦状况
        freq_penalty = 0.0
        for tier_count, tier_add in TRADE_FREQUENCY_TIERS:
            if mental.daily_trades >= tier_count:
                freq_penalty = tier_add
        day_status = "盈利" if mental.daily_pnl >= 0 else "亏损"
        if freq_penalty > 0:
            friction_mult = (
                LOSING_DAY_FRICTION_MULT if mental.daily_pnl < 0
                else WINNING_DAY_FRICTION_MULT
            )
            effective_penalty = freq_penalty * friction_mult
            freq_note = (
                f"当前频率摩擦: +{effective_penalty:.0%}置信度门槛"
                f"({day_status}日×{friction_mult})"
            )
        else:
            freq_note = "频率摩擦: 无（交易量正常）"

        lines = [
            "=== 交易员状态（仓位管理器） ===",
            f"心理状态: {mental.state} — {state_desc}",
            f"仓位调节: ×{mental.size_multiplier:.1f}, 杠杆上限: {mental.leverage_cap}x",
            f"连胜: {mental.consecutive_wins} | 连亏: {mental.consecutive_losses}",
            f"当日: {mental.daily_trades}笔交易(无固定上限，由策略动态评估), "
            f"PnL=${mental.daily_pnl:+.2f}(含手续费), "
            f"累计手续费=${daily_fees:.2f}, "
            f"最大回撤={mental.daily_max_drawdown:.1%}",
            f"{freq_note}",
            f"近期胜率: {mental.recent_win_rate:.0%} | "
            f"近期平均收益: {mental.recent_avg_pnl_pct:+.1%}",
            f"⚠️ 手续费提醒: 开+平taker费率={TAKER_FEE_RATE*2:.3%}/"
            f"笔, 交易越频繁手续费越高, 只在高置信度时交易",
            f"💡 决策建议: 你需要综合今日{day_status}情况、交易笔数、"
            f"市场环境来判断是否值得继续开新仓位。"
            f"盈利好+市场有机会→可以继续；亏损多+信号弱→应该停下来。",
        ]

        if mental.recent_best_regime:
            lines.append(f"最佳环境: {mental.recent_best_regime} | "
                         f"最差环境: {mental.recent_worst_regime}")

        if mental.state in ("cautious", "frozen", "cooldown"):
            lines.append("⚠️ 当前处于防守状态，应优先保守操作，减小仓位或观望")

        # 环境记忆简报
        try:
            from backend.database.models import TradeMemoryRecord
            cur_regime = mental.recent_best_regime or "unknown"
            # 尝试从最近交易推断当前 regime
            last_trade = db.query(TradeMemoryRecord).filter(
                TradeMemoryRecord.account_id == account_id,
            ).order_by(desc(TradeMemoryRecord.closed_at)).first()
            if last_trade and last_trade.market_regime:
                cur_regime = last_trade.market_regime

            regime_all = db.query(TradeMemoryRecord).filter(
                TradeMemoryRecord.account_id == account_id,
                TradeMemoryRecord.market_regime == cur_regime,
            ).order_by(desc(TradeMemoryRecord.closed_at)).limit(60).all()

            if len(regime_all) >= 5:
                r_wins = sum(1 for t in regime_all if t.pnl > 0)
                r_wr = r_wins / len(regime_all)
                r_avg = sum(t.pnl_pct for t in regime_all) / len(regime_all)

                sym_stats: Dict[str, List[float]] = {}
                for t in regime_all:
                    sym_stats.setdefault(t.symbol, []).append(
                        1.0 if t.pnl > 0 else 0.0)
                sym_wr = {s: sum(v)/len(v) for s, v in sym_stats.items() if len(v) >= 3}
                best_3 = sorted(sym_wr.items(), key=lambda x: -x[1])[:3]
                worst_3 = sorted(sym_wr.items(), key=lambda x: x[1])[:3]

                lines.append("")
                lines.append("=== 环境记忆简报 ===")
                lines.append(f"当前环境: {cur_regime}")
                lines.append(
                    f"该环境历史: {len(regime_all)}笔, "
                    f"胜率{r_wr:.0%}, 平均PnL{r_avg:+.1%}")
                if best_3:
                    lines.append(
                        f"最佳币种: " +
                        " | ".join(f"{s}({w:.0%})" for s, w in best_3))
                if worst_3:
                    lines.append(
                        f"最差币种: " +
                        " | ".join(f"{s}({w:.0%})" for s, w in worst_3))
                if r_wr < 0.40:
                    lines.append(
                        "💡 该环境胜率偏低，建议提高开仓门槛或减小仓位")
                elif r_wr > 0.60:
                    lines.append(
                        "💡 该环境表现良好，可适度放大仓位")
        except Exception as e:
            logger.debug(f"[PosMgr] 环境简报生成异常(非致命): {e}")

        # 性格档案注入
        personality = self._load_personality(db, account_id)
        if personality:
            lines.append("")
            lines.append(f"=== 交易员性格档案 ===")
            if personality.benchmark_trader:
                lines.append(f"对标: {personality.benchmark_trader}")
            lines.append(f"风格: {personality.trading_style} | 周期: {personality.time_horizon}")
            lines.append(f"风险偏好: {personality.risk_appetite}/10 | "
                         f"亏损容忍: {personality.loss_tolerance}/10 | "
                         f"连赢激进度: {personality.win_aggression}/10")
            lines.append(f"单仓上限: {personality.max_position_pct:.0%} | "
                         f"偏好杠杆: {personality.preferred_leverage}x | "
                         f"杠杆上限: {personality.max_leverage}x")
            if personality.special_skills:
                lines.append(f"专属技能: {personality.special_skills}")

        return "\n".join(lines)

    def get_mental_status_snapshot(self, db: Session, account_id: int) -> dict:
        """供 API / 前端展示的交易员心理状态摘要。"""
        from backend.database.models import TraderMentalState

        if not loss_protection_enabled():
            mental = db.query(TraderMentalState).filter(
                TraderMentalState.account_id == account_id
            ).first()
            reset_loss_protection_state(db, account_id)
            return {
                "state": "normal",
                "description": "连亏保护已关闭",
                "hint": "连亏保护已关闭，可正常开仓",
                "block_reason": "",
                "blocks_new_opens": False,
                "consecutive_losses": int(mental.consecutive_losses or 0) if mental else 0,
                "consecutive_wins": int(mental.consecutive_wins or 0) if mental else 0,
                "daily_pnl": float(mental.daily_pnl or 0) if mental else 0.0,
                "daily_trades": int(mental.daily_trades or 0) if mental else 0,
                "cooldown_until": None,
                "cooldown_remaining_min": 0,
                "size_multiplier": 1.0,
                "leverage_cap": 20,
                "high_conf_bypass_threshold": 0.78,
                "loss_protection_enabled": False,
            }

        mental = db.query(TraderMentalState).filter(
            TraderMentalState.account_id == account_id
        ).first()
        if not mental:
            return {
                "state": "normal",
                "description": MENTAL_STATES["normal"]["description"],
                "hint": MENTAL_STATES["normal"]["description"],
                "block_reason": "",
                "blocks_new_opens": False,
                "consecutive_losses": 0,
                "consecutive_wins": 0,
                "daily_pnl": 0.0,
                "daily_trades": 0,
                "cooldown_until": None,
                "cooldown_remaining_min": 0,
                "size_multiplier": 1.0,
                "leverage_cap": 20,
                "high_conf_bypass_threshold": 0.78,
            }

        personality = self._load_personality(db, account_id)
        self._normalize_mental_state(db, mental, personality, persist=True)

        now_utc = datetime.now(timezone.utc)
        cooldown_until = _as_utc(mental.cooldown_until)
        cooldown_remaining = 0.0
        if cooldown_until and now_utc < cooldown_until:
            cooldown_remaining = (cooldown_until - now_utc).total_seconds() / 60.0

        state = mental.state or "normal"
        blocks = state == "frozen" and cooldown_remaining > 0
        desc = MENTAL_STATES.get(state, MENTAL_STATES["normal"]).get("description", "")
        daily_dd = self._daily_drawdown_pct(db, mental)
        block_reason = self._format_block_reason(mental, daily_dd, cooldown_remaining)

        try:
            from backend.config.settings import MENTAL_HIGH_CONF_FROZEN_BYPASS
            high_conf_bypass = float(MENTAL_HIGH_CONF_FROZEN_BYPASS)
        except Exception:
            high_conf_bypass = 0.78

        hint = desc
        if blocks:
            hint = (
                f"{block_reason} — 新开仓已暂停约 {cooldown_remaining:.0f} 分钟；"
                f"AI 置信度 ≥ {high_conf_bypass:.0%} 时可试探小仓"
            )
        elif state == "cautious":
            hint = f"{block_reason or desc} · 仓位系数 ×{float(mental.size_multiplier or 0.8):.2f}"

        return {
            "state": state,
            "description": desc,
            "hint": hint,
            "block_reason": block_reason,
            "blocks_new_opens": blocks,
            "consecutive_losses": int(mental.consecutive_losses or 0),
            "consecutive_wins": int(mental.consecutive_wins or 0),
            "daily_pnl": float(mental.daily_pnl or 0),
            "daily_trades": int(mental.daily_trades or 0),
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "cooldown_remaining_min": round(cooldown_remaining, 1),
            "size_multiplier": float(mental.size_multiplier or 1.0),
            "leverage_cap": float(mental.leverage_cap or 20),
            "high_conf_bypass_threshold": high_conf_bypass,
        }

    # ═══════════════════════════════════════════════════
    # 私有方法
    # ═══════════════════════════════════════════════════

    def _get_or_create_mental_state(self, db: Session, account_id: int):
        from backend.database.models import TraderMentalState

        mental = db.query(TraderMentalState).filter(
            TraderMentalState.account_id == account_id
        ).first()
        if not mental:
            mental = TraderMentalState(account_id=account_id)
            db.add(mental)
            db.flush()
        return mental

    def _daily_drawdown_pct(self, db: Session, mental) -> float:
        from backend.database.models import PaperBalance

        if float(mental.daily_pnl or 0) >= 0:
            return 0.0
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == mental.account_id
        ).first()
        equity = float(bal.total_equity or 0) if bal else 0.0
        if equity <= 0:
            return 0.0
        return abs(float(mental.daily_pnl)) / equity

    def _format_block_reason(self, mental, daily_dd: float, cooldown_remaining: float) -> str:
        """生成可读拦截原因，避免「连亏 0 笔仍拦截」的误导文案。"""
        losses = int(mental.consecutive_losses or 0)
        reason = str(mental.state_reason or "")

        if mental.state == "frozen" and cooldown_remaining > 0:
            if losses >= 1:
                return f"连续亏损 {losses} 笔"
            if daily_dd >= MAX_DAILY_LOSS_PCT:
                return f"当日亏损 {daily_dd:.1%} 达冻结线"
            if "daily_loss" in reason:
                return "当日亏损过大"
            if "loss_streak" in reason:
                return "此前连亏触发保护（已恢复计数）"
            return "风险保护冷却中"

        if mental.state == "cautious":
            if losses >= 1:
                return f"连续亏损 {losses} 笔，适度缩小仓位"
            if daily_dd >= CAUTIOUS_DAILY_LOSS_PCT:
                return f"当日亏损 {daily_dd:.1%}，进入谨慎模式"
            return MENTAL_STATES["cautious"]["description"]

        if losses >= 2:
            return f"近期连亏 {losses} 笔"
        return MENTAL_STATES.get(mental.state or "normal", MENTAL_STATES["normal"]).get(
            "description", ""
        )

    def _normalize_mental_state(
        self,
        db: Session,
        mental,
        personality=None,
        *,
        persist: bool = False,
    ) -> None:
        """修正卡死的 frozen：冷却过期、连亏已清零、日亏未达线时不应继续拦截。"""
        self._check_daily_reset(db, mental)
        if mental.state != "frozen":
            return

        now_utc = datetime.now(timezone.utc)
        cooldown_until = _as_utc(mental.cooldown_until)
        daily_dd = self._daily_drawdown_pct(db, mental)
        losses = int(mental.consecutive_losses or 0)
        changed = False

        if cooldown_until and now_utc >= cooldown_until:
            self._transition_state(db, mental, "cooldown_expired", personality)
            changed = True
        elif losses == 0 and daily_dd < MAX_DAILY_LOSS_PCT:
            old_state = mental.state
            mental.state = "normal"
            mental.cooldown_until = None
            cfg = MENTAL_STATES["normal"]
            mental.size_multiplier = cfg["size_multiplier"]
            mental.leverage_cap = cfg["leverage_cap"]
            mental.state_reason = (
                f"{old_state}→normal (stale_frozen: 连亏0, 日亏{daily_dd:.1%}未达冻结线)"
            )
            logger.info(
                "[PosMgr] 清除误冻结 account=%s reason=%s",
                mental.account_id,
                mental.state_reason,
            )
            changed = True

        if changed and persist:
            try:
                db.commit()
            except Exception as exc:
                logger.warning("[PosMgr] normalize mental state commit failed: %s", exc)
                db.rollback()

    def _check_daily_reset(self, db: Session, mental):
        """如果是新的一天，重置日统计并恢复交易状态。
        
        关键改进：cautious 也恢复到 normal，防止"连亏→cautious→更难盈利→永远 cautious"死循环。
        """
        if mental.last_trade_at:
            lt = mental.last_trade_at
            if lt.tzinfo is None:
                lt = lt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if lt.date() < now.date():
                mental.daily_trades = 0
                mental.daily_pnl = 0
                mental.daily_max_drawdown = 0
                mental.cooldown_until = None
                mental.streak_pnl = 0
                old_state = mental.state
                if mental.state in ("frozen", "cooldown", "cautious"):
                    mental.state = "normal"
                    mental.consecutive_losses = 0
                    mental.state_reason = f"新一天，从{old_state}恢复到正常"
                elif mental.state == "tilted":
                    mental.state = "normal"
                    mental.consecutive_losses = 0
                    mental.state_reason = "新一天，从倾斜恢复到正常"

    def _query_memory(
        self, db: Session, account_id: int, symbol: str, market_regime: str
    ) -> MemoryInsight:
        """从交易记忆中提取洞察（含环境表现矩阵）。"""
        from backend.database.models import TradeMemoryRecord

        insight = MemoryInsight()

        # ── 1. 该 symbol 最近 N 笔 ──
        recent = db.query(TradeMemoryRecord).filter(
            TradeMemoryRecord.account_id == account_id,
            TradeMemoryRecord.symbol == symbol,
        ).order_by(desc(TradeMemoryRecord.closed_at)).limit(
            MEMORY_LOOKBACK_TRADES
        ).all()

        if recent:
            insight.symbol_trade_count = len(recent)
            wins = [r for r in recent if r.pnl > 0]
            insight.symbol_win_rate = len(wins) / len(recent)
            insight.symbol_avg_pnl_pct = sum(r.pnl_pct for r in recent) / len(recent)
            if recent:
                insight.avg_hold_seconds = int(
                    sum(r.hold_seconds for r in recent) / len(recent)
                )
            if wins:
                insight.best_leverage_for_symbol = round(
                    sum(r.leverage for r in wins) / len(wins)
                )

        # ── 2. 该 regime 最近 N 笔（per-symbol 已有） ──
        regime_trades = db.query(TradeMemoryRecord).filter(
            TradeMemoryRecord.account_id == account_id,
            TradeMemoryRecord.market_regime == market_regime,
        ).order_by(desc(TradeMemoryRecord.closed_at)).limit(
            MEMORY_LOOKBACK_TRADES
        ).all()

        if regime_trades:
            regime_wins = [r for r in regime_trades if r.pnl > 0]
            insight.regime_win_rate = len(regime_wins) / len(regime_trades)
            insight.regime_avg_pnl_pct = sum(
                r.pnl_pct for r in regime_trades
            ) / len(regime_trades)

        # ── 3. 环境表现矩阵（跨币种汇总） ──
        try:
            regime_all = db.query(TradeMemoryRecord).filter(
                TradeMemoryRecord.account_id == account_id,
                TradeMemoryRecord.market_regime == market_regime,
            ).order_by(desc(TradeMemoryRecord.closed_at)).limit(
                MEMORY_LOOKBACK_TRADES * 3
            ).all()

            insight.regime_total_trades = len(regime_all)

            if len(regime_all) >= 5:
                # 按币种分组统计胜率
                sym_stats: Dict[str, List[float]] = {}
                for t in regime_all:
                    sym_stats.setdefault(t.symbol, []).append(
                        1.0 if t.pnl > 0 else 0.0)

                sym_wr = {
                    s: sum(v) / len(v)
                    for s, v in sym_stats.items() if len(v) >= 3
                }
                if sym_wr:
                    sorted_syms = sorted(sym_wr.items(), key=lambda x: x[1], reverse=True)
                    insight.regime_best_symbols = [
                        s for s, _ in sorted_syms[:3]]
                    insight.regime_worst_symbols = [
                        s for s, _ in sorted_syms[-3:] if sym_wr[s] < 0.45]

                # 如果环境胜率 < 35% 且样本充足，需要额外置信度门槛
                regime_overall_wr = sum(
                    1.0 for t in regime_all if t.pnl > 0
                ) / len(regime_all)
                if regime_overall_wr < 0.35 and len(regime_all) >= 10:
                    insight.regime_confidence_boost = 0.10
                elif regime_overall_wr < 0.40 and len(regime_all) >= 8:
                    insight.regime_confidence_boost = 0.05

                # BTC 在当前环境的表现 → 山寨币折扣
                btc_trades = [t for t in regime_all if t.symbol == "BTC"]
                if btc_trades:
                    insight.btc_regime_avg_pnl = sum(
                        t.pnl_pct for t in btc_trades) / len(btc_trades)
                    if insight.btc_regime_avg_pnl < -0.01 and symbol != "BTC":
                        insight.cross_symbol_discount = 0.7
                    elif insight.btc_regime_avg_pnl < -0.005 and symbol != "BTC":
                        insight.cross_symbol_discount = 0.85

        except Exception as e:
            logger.debug(f"[PosMgr] 环境矩阵查询异常(非致命): {e}")

        # ── 4. 计算推荐仓位调节系数 ──
        adj = 1.0
        if insight.symbol_trade_count >= 5:
            if insight.symbol_win_rate >= 0.6:
                adj *= 1.2
            elif insight.symbol_win_rate <= 0.35:
                adj *= 0.6

            if insight.symbol_avg_pnl_pct > 0.02:
                adj *= 1.1
            elif insight.symbol_avg_pnl_pct < -0.02:
                adj *= 0.7

        if regime_trades and len(regime_trades) >= 3:
            if insight.regime_win_rate <= 0.30:
                adj *= 0.5
            elif insight.regime_win_rate >= 0.65:
                adj *= 1.15

        # 跨币种折扣
        adj *= insight.cross_symbol_discount

        insight.recommended_size_adj = max(0.3, min(1.5, adj))
        return insight

    def _calc_leverage(
        self,
        raw_leverage: int,
        confidence: float,
        volatility_pct: float,
        leverage_cap: int,
        memory: MemoryInsight,
    ) -> int:
        """计算最终杠杆（AI 主驾改造）。

        2026-06-18: 原 _calc_leverage 按波动率/置信度/记忆多重 cap 重算杠杆，属于数值改写。
        现：杠杆用 AI 给的 raw_leverage，仅受硬上限约束：
        - leverage_cap（心理态/frozen 时为 0，正常时为 nature cap）—— 硬安全网
        - [5, 20] 全局硬上下限 —— 防爆仓
        不再按波动率/置信度/记忆数值改写（那些信息已在 AI prompt 里，AI 自行权衡）。
        """
        lev = raw_leverage
        _debug_steps = [f"raw(AI)={raw_leverage}"]

        # 状态上限（硬安全网：frozen/连亏时 leverage_cap 会很低或 0）
        prev = lev
        lev = min(lev, leverage_cap)
        if lev < prev:
            _debug_steps.append(f"state_cap={leverage_cap}(心理态硬上限)")

        # 全局硬上下限 [5, 20]
        # 注意：leverage_cap=0（frozen/禁止开仓）时直接返回 0，不用下限 5 抬起
        if lev <= 0:
            final = 0
            _debug_steps.append("frozen/禁开→final=0")
        else:
            final = max(5, min(20, lev))
            _debug_steps.append(f"hard[5,20]→final={final}")
        logger.debug(f"[PosMgr] _calc_leverage(AI主驾): {' → '.join(_debug_steps)}x")
        return final

    def _calc_base_size_pct(self, confidence: float, volatility_pct: float) -> float:
        """
        计算基础仓位百分比。

        规则：
          - 置信度越高 → 仓位越大
          - 波动率越高 → 仓位越小（反比关系）
          - 小资金账户（<$5000）提高基准，充分利用杠杆
          - 范围 8%~50%
        """
        # 置信度因子 (0.5~1.0)
        conf_factor = max(0.5, min(1.0, confidence * 1.8))

        # 波动率因子（加密货币天然高波动，放宽缩仓幅度）
        if volatility_pct <= 0.02:
            vol_factor = 1.0
        elif volatility_pct <= 0.04:
            vol_factor = 0.85
        elif volatility_pct <= 0.06:
            vol_factor = 0.7
        else:
            vol_factor = 0.6

        base = 0.35 * conf_factor * vol_factor  # 基准 35%（名义占余额比例）
        # 10x 杠杆下 35% 名义 = 3.5% 保证金，太小。
        # 乘以杠杆系数让保证金占用合理：目标保证金占余额 8%~20%
        return max(0.08, min(2.0, base * 4))  # 放大4倍，上限 200% 名义 = 20% 保证金(10x)

    # 按 trade_nature 区分的 TP/SL 参数
    # [2026-07-23 统一映射] short tier 现映射到 "scalp"(原 "intraday")。
    # 这意味着 short 仓位的 TP/SL 用本表 "scalp" 项(sl_base_min=0.025 等),
    # 比原 "intraday"(sl_base_min=0.035)更紧。"intraday"/"position" 项保留作
    # 向后兼容(直接传 nature 字符串的旧路径),但 tier 路径不再触达。
    # v5: SL 距离大幅放宽
    # 数据铁证: 之前 13/15 笔 SL 只有 1% 距离，加密市场日波 2-15%，1% SL=必死
    # sl_base_min 是绝对下限，sl_cap_by_lev 是杠杆约束上限
    _NATURE_TP_SL_CONFIG = {
        "scalp": {
            "sl_base_min": 0.025,
            "sl_vol_mult": 2.0,
            "sl_cap_by_lev": {12: 0.04, 8: 0.06, 4: 0.08},
            "rr_high_wr": 1.5,
            "rr_low_wr": 1.2,
            "min_sl_pct": 0.975,
            "min_tp_pct": 1.012,
        },
        "intraday": {
            "sl_base_min": 0.035,
            "sl_vol_mult": 2.5,
            "sl_cap_by_lev": {12: 0.05, 8: 0.07, 4: 0.10},
            "rr_high_wr": 1.8,
            "rr_low_wr": 1.5,
            "min_sl_pct": 0.965,
            "min_tp_pct": 1.02,
        },
        "swing": {
            "sl_base_min": 0.045,
            "sl_vol_mult": 3.0,
            "sl_cap_by_lev": {12: 0.07, 8: 0.09, 4: 0.12},
            "rr_high_wr": 2.0,
            "rr_low_wr": 1.5,
            "min_sl_pct": 0.955,
            "min_tp_pct": 1.03,
        },
        "position": {
            "sl_base_min": 0.055,
            "sl_vol_mult": 3.5,
            "sl_cap_by_lev": {12: 0.09, 8: 0.12, 4: 0.15},
            "rr_high_wr": 2.5,
            "rr_low_wr": 2.0,
            "min_sl_pct": 0.945,
            "min_tp_pct": 1.05,
        },
        "trend_follow": {
            "sl_base_min": 0.065,
            "sl_vol_mult": 4.0,
            "sl_cap_by_lev": {12: 0.12, 8: 0.15, 4: 0.20},
            "rr_high_wr": 3.0,
            "rr_low_wr": 2.5,
            "min_sl_pct": 0.935,
            "min_tp_pct": 1.06,
        },
    }

    def _calc_tp_sl(
        self,
        side: str,
        price: float,
        leverage: int,
        volatility_pct: float,
        raw_tp: float,
        raw_sl: float,
        memory: MemoryInsight,
        tier: str = "swing",
    ) -> Tuple[float, float]:
        """
        计算初始止盈止损价格（按 trade_nature 差异化）。

        scalp:        SL 极紧 + 低盈亏比（快进快出）
        intraday:     SL 紧 + 中低盈亏比
        swing:        SL 适中 + 中等盈亏比
        position:     SL 宽 + 高盈亏比
        trend_follow: SL 最宽 + 最高盈亏比（让利润奔跑）
        """
        nature = self._TIER_TO_NATURE.get(tier, tier)
        cfg = self._NATURE_TP_SL_CONFIG.get(nature, self._NATURE_TP_SL_CONFIG["swing"])

        sl_base = max(cfg["sl_base_min"], volatility_pct * cfg["sl_vol_mult"])
        for min_lev, max_sl in sorted(cfg["sl_cap_by_lev"].items(), reverse=True):
            if leverage >= min_lev:
                sl_base = min(sl_base, max_sl)
                break

        rr_ratio = cfg["rr_high_wr"] if memory.symbol_win_rate >= 0.5 else cfg["rr_low_wr"]
        tp_base = sl_base * rr_ratio

        # 2026-06-18: AI 主驾改造。原逻辑"不信任 AI 的 SL/TP"会在 AI 值比系统值窄时
        # 强制替换成系统值。现改为：AI 的 TP/SL 优先采纳，系统值降为硬下限安全网。
        # - AI 的 SL 距离 < min_sl_pct(硬下限，防爆仓) → 提升到硬下限
        # - AI 的 SL 距离 >= min_sl_pct → 直接用 AI 的值（不管是否比系统值窄）
        # - 保留 _ensure_sl_inside_liq（行 304）作为爆仓距离硬安全网
        # 旧注释提到的"13/15 笔 SL 被扫出"问题，现在由 min_sl_pct 硬下限兜底，
        # 而非全面替换 AI 的判断——AI 主驾的代价是偶尔被合理止损扫出，但方向判断归 AI。
        _min_sl_price_mult = cfg["min_sl_pct"]  # 多头: price * min_sl_pct（如 0.945）
        _min_tp_price_mult = cfg["min_tp_pct"]  # 多头: price * min_tp_pct（如 1.05）

        if side == "buy":
            # 多头：SL 在下方（< price），TP 在上方（> price）
            _hard_floor_sl = price * _min_sl_price_mult    # 硬下限 SL 价（如 94.5）
            _hard_floor_tp = price * _min_tp_price_mult    # 硬下限 TP 价（如 105）
            if raw_sl > 0:
                # AI 给了 SL：≥ 硬下限(更远离价)直接用；< 硬下限(太接近价)提升到硬下限防爆仓
                sl_price = raw_sl if raw_sl <= _hard_floor_sl else _hard_floor_sl
            else:
                sl_price = round(price * (1 - sl_base), 6)  # AI 没给，用系统基准
            if raw_tp > 0:
                tp_price = raw_tp if raw_tp >= _hard_floor_tp else _hard_floor_tp
            else:
                tp_price = round(price * (1 + tp_base), 6)
        else:
            # 空头：SL 在上方（> price），TP 在下方（< price）
            _hard_floor_sl = price * (2 - _min_sl_price_mult)
            _hard_floor_tp = price * (2 - _min_tp_price_mult)
            if raw_sl > 0:
                sl_price = raw_sl if raw_sl >= _hard_floor_sl else _hard_floor_sl
            else:
                sl_price = round(price * (1 + sl_base), 6)
            if raw_tp > 0:
                tp_price = raw_tp if raw_tp <= _hard_floor_tp else _hard_floor_tp
            else:
                tp_price = round(price * (1 - tp_base), 6)

        return tp_price, sl_price

    def _evaluate_transitions(self, db: Session, mental, personality=None):
        """根据当前统计触发状态转换（性格影响阈值）。"""
        thresholds = self._personality_state_thresholds(personality)
        triggers = []

        if mental.consecutive_wins >= thresholds["win_to_aggressive"]:
            triggers.append("win_streak_3")
        if mental.consecutive_wins >= thresholds["win_to_normal_from_cooldown"]:
            triggers.append("win_streak_2")
        if mental.consecutive_wins >= 1:
            triggers.append("single_win")
        if loss_protection_enabled():
            if mental.consecutive_losses >= thresholds["loss_to_frozen"]:
                triggers.append("loss_streak_frozen")
            if mental.consecutive_losses >= thresholds["loss_to_cautious"]:
                triggers.append("loss_streak_cautious")
            if mental.consecutive_losses >= 1 and mental.consecutive_wins == 0:
                triggers.append("single_loss")

        from backend.database.models import PaperBalance
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == mental.account_id
        ).first()
        if bal and bal.total_equity > 0 and mental.daily_pnl < 0:
            daily_dd = abs(mental.daily_pnl) / bal.total_equity
            if daily_dd >= MAX_DAILY_LOSS_PCT:
                triggers.append("daily_loss_5pct")
            elif daily_dd >= CAUTIOUS_DAILY_LOSS_PCT:
                triggers.append("daily_loss_3pct")

        for trigger in triggers:
            key = (mental.state, trigger)
            new_state = STATE_TRANSITIONS.get(key)
            if new_state and new_state != mental.state:
                self._transition_state(db, mental, trigger, personality)
                break

    def _transition_state(self, db: Session, mental, trigger: str, personality=None):
        key = (mental.state, trigger)
        new_state = STATE_TRANSITIONS.get(key)
        if not new_state:
            return

        old_state = mental.state
        mental.state = new_state
        mental.state_reason = f"{old_state}→{new_state} ({trigger})"

        config = MENTAL_STATES.get(new_state, MENTAL_STATES["normal"])
        mental.size_multiplier = config["size_multiplier"]
        mental.leverage_cap = config["leverage_cap"]

        if new_state == "frozen":
            mental.cooldown_until = datetime.now(timezone.utc) + timedelta(
                minutes=FROZEN_COOLDOWN_MINUTES
            )

        if new_state in ("normal", "aggressive"):
            mental.streak_pnl = 0

        logger.info(
            f"[PosMgr] 状态转换: {old_state} → {new_state} "
            f"(trigger={trigger}, mult={config['size_multiplier']}, "
            f"lev_cap={config['leverage_cap']})"
        )

    def _refresh_memory_summary(self, db: Session, mental, account_id: int):
        """刷新心理状态表中的记忆摘要字段。"""
        from backend.database.models import TradeMemoryRecord

        recent = db.query(TradeMemoryRecord).filter(
            TradeMemoryRecord.account_id == account_id,
        ).order_by(desc(TradeMemoryRecord.closed_at)).limit(
            MEMORY_LOOKBACK_TRADES
        ).all()

        if not recent:
            return

        wins = [r for r in recent if r.pnl > 0]
        mental.recent_win_rate = len(wins) / len(recent)
        mental.recent_avg_pnl_pct = sum(r.pnl_pct for r in recent) / len(recent)

        # 按 regime 分组统计
        regime_stats = {}
        for r in recent:
            rg = r.market_regime or "unknown"
            if rg not in regime_stats:
                regime_stats[rg] = {"pnl": 0, "count": 0}
            regime_stats[rg]["pnl"] += r.pnl
            regime_stats[rg]["count"] += 1

        if regime_stats:
            best = max(regime_stats.items(), key=lambda x: x[1]["pnl"])
            worst = min(regime_stats.items(), key=lambda x: x[1]["pnl"])
            mental.recent_best_regime = best[0]
            mental.recent_worst_regime = worst[0]

    # ══════════════════════════════════════════════════
    #  顺势加仓（金字塔）
    # ══════════════════════════════════════════════════

    def evaluate_pyramid(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,
        ai_confidence: float,
        current_price: float,
        existing_position: dict,
        volatility_pct: float = 0.015,
        raw_leverage: int = 10,
        market_regime: str = "unknown",
        tier: str = "swing",
        market_summary: dict = None,
    ) -> PositionPlan:
        """评估是否应该顺势加仓（升级版：接入 trend_pyramid_gate + tier 分档 + SL 锁利）。"""
        from backend.database.models import PaperBalance

        add_count = existing_position.get("add_count", 0) or 0
        margin = float(existing_position.get("margin", 0))
        upnl = float(existing_position.get("unrealized_pnl", 0))
        pnl_pct = upnl / margin if margin > 0 else 0

        # ── 趋势门控（5 层） ──
        if market_summary:
            gate_ok, gate_reason = trend_pyramid_gate(
                symbol, side, add_count, pnl_pct, market_summary, tier=tier
            )
            if not gate_ok:
                return self._skip_plan(symbol, side, f"滚仓门控拦截: {gate_reason}")

        # 读取 tier 分档参数（覆盖全局常量）
        try:
            from backend.config.settings import TIER_PYRAMID_PARAMS
            tier_cfg = TIER_PYRAMID_PARAMS.get(tier, {})
        except Exception:
            tier_cfg = {}

        max_adds = tier_cfg.get("max_adds", PYRAMID_MAX_ADDS)
        min_profit_pcts = tier_cfg.get("min_profit_pcts", [PYRAMID_MIN_PROFIT_PCT])
        size_ratios = tier_cfg.get("size_ratios", PYRAMID_SIZE_RATIOS)
        cooldown_min = tier_cfg.get("cooldown_min", PYRAMID_COOLDOWN_MINUTES)
        sl_lock_ratio = tier_cfg.get("sl_lock_ratio", 0.50)

        if add_count >= max_adds:
            return self._skip_plan(symbol, side,
                f"已加仓{add_count}次，达tier={tier}上限{max_adds}")

        if ai_confidence < PYRAMID_MIN_CONFIDENCE:
            return self._skip_plan(symbol, side,
                f"置信度{ai_confidence:.0%}<加仓门槛{PYRAMID_MIN_CONFIDENCE:.0%}")

        required_profit = min_profit_pcts[min(add_count, len(min_profit_pcts) - 1)]
        if pnl_pct < required_profit:
            return self._skip_plan(symbol, side,
                f"浮盈{pnl_pct:.1%}<第{add_count+1}次加仓门槛{required_profit:.1%}")

        # 加仓冷却
        from datetime import datetime as _dt, timezone as _tz
        last_add_str = existing_position.get("last_add_at") or ""
        if last_add_str:
            try:
                last_add = _dt.fromisoformat(str(last_add_str).replace("Z", "+00:00"))
                if last_add.tzinfo is None:
                    last_add = last_add.replace(tzinfo=_tz.utc)
                minutes_since = (_dt.now(_tz.utc) - last_add).total_seconds() / 60.0
                if minutes_since < cooldown_min:
                    remaining = cooldown_min - minutes_since
                    return self._skip_plan(symbol, side,
                        f"加仓冷却中(剩余{remaining:.0f}分钟, tier={tier}需{cooldown_min}min)")
            except (ValueError, TypeError):
                pass

        # 计算金字塔仓位：按原始名义仓位比例追加，保证金由 notional ÷ leverage 得出。
        leverage = float(existing_position.get("leverage", 10))
        original_margin = float(existing_position.get("original_margin", 0)) or margin
        original_notional = float(existing_position.get("original_size", 0) or 0) * float(
            existing_position.get("entry_price", current_price) or current_price
        )
        if original_notional <= 0:
            original_notional = original_margin * leverage
        pyramid_ratio = size_ratios[min(add_count, len(size_ratios) - 1)]
        notional = original_notional * pyramid_ratio
        add_margin = notional / leverage if leverage > 0 else notional

        # 余额检查
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == account_id
        ).first()
        if not bal or bal.available_balance < add_margin:
            return self._skip_plan(symbol, side,
                f"可用余额${bal.available_balance if bal else 0:.0f}"
                f"不足以加仓${add_margin:.0f}")

        # 总敞口检查
        equity = float(bal.total_equity) if bal else 0
        frozen = float(bal.frozen_margin) if bal else 0
        if equity > 0 and (frozen + add_margin) / equity >= MAX_TOTAL_EXPOSURE_PCT:
            return self._skip_plan(symbol, side,
                f"加仓后总敞口将超限")

        # TP/SL 重算：基于新均价 + SL 锁利
        entry_price = float(existing_position.get("entry_price", current_price))
        old_size = float(existing_position.get("size", 0))
        add_size = notional / current_price if current_price > 0 else 0
        if old_size + add_size > 0:
            new_avg = (entry_price * old_size + current_price * add_size) / (old_size + add_size)
        else:
            new_avg = current_price

        memory = self._query_memory(db, account_id, symbol, market_regime)
        tp, sl = self._calc_tp_sl(side, new_avg, int(leverage), volatility_pct, 0, 0, memory, tier=tier)

        # SL 锁利升级：推到浮盈的 40-50% 位（而非仅保本）
        side_norm = "long" if side in ("buy", "long") else "short"
        if pnl_pct > 0 and sl_lock_ratio > 0:
            if side_norm == "long":
                locked_sl = entry_price + (current_price - entry_price) * sl_lock_ratio
                sl = max(sl, round(locked_sl, 6))
            else:
                locked_sl = entry_price - (entry_price - current_price) * sl_lock_ratio
                sl = min(sl, round(locked_sl, 6)) if sl > 0 else round(locked_sl, 6)

        return PositionPlan(
            action="pyramid",
            symbol=symbol,
            side=side,
            leverage=int(leverage),
            size_pct=pyramid_ratio,
            notional_usd=round(notional, 2),
            margin_usd=round(add_margin, 2),
            stop_loss_price=sl,
            take_profit_price=tp,
            confidence=ai_confidence,
            reasoning=f"顺势加仓第{add_count + 1}次({pyramid_ratio:.0%}×原始仓位) "
                      f"浮盈{pnl_pct:.1%} tier={tier} SL锁利{sl_lock_ratio:.0%}",
        )

    # ══════════════════════════════════════════════════
    #  逆势补仓（DCA）
    # ══════════════════════════════════════════════════

    def evaluate_dca(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,
        ai_confidence: float,
        current_price: float,
        existing_position: dict,
        volatility_pct: float = 0.015,
        market_regime: str = "unknown",
        orchestrator_decision=None,
        risk_score: float = 50.0,
        tier: str = "swing",
    ) -> PositionPlan:
        """评估是否应该逆势补仓。existing_position 为 _position_to_dict 返回的 dict。"""
        from backend.database.models import PaperBalance

        dca_count = existing_position.get("dca_count", 0) or 0
        if dca_count >= DCA_MAX_ADDS:
            return self._skip_plan(symbol, side,
                f"已补仓{dca_count}次，达上限{DCA_MAX_ADDS}")

        if ai_confidence < DCA_MIN_CONFIDENCE:
            return self._skip_plan(symbol, side,
                f"置信度{ai_confidence:.0%}<补仓门槛{DCA_MIN_CONFIDENCE:.0%}")

        # 安全红线：风控分数过高
        if risk_score > DCA_MAX_RISK_SCORE:
            return self._skip_plan(symbol, side,
                f"风控分数{risk_score:.0f}>{DCA_MAX_RISK_SCORE}，禁止补仓")

        margin = float(existing_position.get("margin", 0))
        upnl = float(existing_position.get("unrealized_pnl", 0))
        pnl_pct = upnl / margin if margin > 0 else 0

        if pnl_pct > DCA_MIN_LOSS_PCT:
            return self._skip_plan(symbol, side,
                f"亏损{pnl_pct:.1%}未达补仓门槛{DCA_MIN_LOSS_PCT:.1%}")
        if pnl_pct < DCA_MAX_LOSS_PCT:
            return self._skip_plan(symbol, side,
                f"亏损{pnl_pct:.1%}过深(>{DCA_MAX_LOSS_PCT:.1%})，应止损而非补仓")

        # 方向确认：编排器中长线是否仍支持原方向
        if orchestrator_decision and isinstance(orchestrator_decision, dict):
            long_bias = orchestrator_decision.get("long_bias", "")
            mid_bias = orchestrator_decision.get("mid_bias", "")
            expected_bias = "bullish" if side == "buy" else "bearish"
            support = sum(1 for b in [long_bias, mid_bias] if b == expected_bias)
            if support < 1:
                return self._skip_plan(symbol, side,
                    f"中长线不再支持原方向({long_bias}/{mid_bias})，禁止补仓")
        elif orchestrator_decision and hasattr(orchestrator_decision, 'long_view'):
            long_bias = getattr(orchestrator_decision.long_view, 'bias', '')
            mid_bias = getattr(orchestrator_decision.mid_view, 'bias', '')
            expected_bias = "bullish" if side == "buy" else "bearish"
            support = sum(1 for b in [long_bias, mid_bias] if b == expected_bias)
            if support < 1:
                return self._skip_plan(symbol, side,
                    f"中长线不再支持原方向({long_bias}/{mid_bias})，禁止补仓")

        # 补仓冷却
        from datetime import datetime as _dt, timezone as _tz
        opened_at_str = existing_position.get("opened_at") or ""
        last_add_str = existing_position.get("last_add_at") or ""
        ref_time_str = last_add_str or opened_at_str
        if ref_time_str:
            try:
                ref_time = _dt.fromisoformat(str(ref_time_str).replace("Z", "+00:00"))
                if ref_time.tzinfo is None:
                    ref_time = ref_time.replace(tzinfo=_tz.utc)
                minutes_since = (_dt.now(_tz.utc) - ref_time).total_seconds() / 60.0
                if minutes_since < DCA_COOLDOWN_MINUTES:
                    remaining = DCA_COOLDOWN_MINUTES - minutes_since
                    return self._skip_plan(symbol, side,
                        f"补仓冷却中(剩余{remaining:.0f}分钟)")
            except (ValueError, TypeError):
                pass

        # 同方向总敞口检查
        from backend.database.models import PaperPosition
        pos_side = "long" if side == "buy" else "short"
        same_dir_margin = sum(
            float(p.margin or 0) for p in db.query(PaperPosition).filter(
                PaperPosition.account_id == account_id,
                PaperPosition.side == pos_side,
                PaperPosition.status == "open",
            ).all()
        )
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == account_id
        ).first()
        equity = float(bal.total_equity) if bal else 0
        if equity > 0 and same_dir_margin > equity * 0.50:
            return self._skip_plan(symbol, side,
                f"同方向总敞口${same_dir_margin:.0f}>权益50%，禁止补仓")

        # 计算补仓仓位：按名义仓位追加，保证金只由 notional ÷ leverage 得出。
        leverage = float(existing_position.get("leverage", 10))
        original_margin = float(existing_position.get("original_margin", 0)) or margin
        original_notional = float(existing_position.get("original_size", 0) or 0) * float(
            existing_position.get("entry_price", current_price) or current_price
        )
        if original_notional <= 0:
            original_notional = original_margin * leverage
        current_notional = float(existing_position.get("size", 0) or 0) * float(
            existing_position.get("entry_price", current_price) or current_price
        )
        notional = original_notional * DCA_SIZE_RATIO
        max_total_notional = original_notional * DCA_MAX_TOTAL_RATIO
        if (current_notional + notional) > max_total_notional:
            notional = max(0, max_total_notional - current_notional)
        dca_margin = notional / leverage if leverage > 0 else notional

        if dca_margin < EXCHANGE_MIN_MARGIN:
            return self._skip_plan(symbol, side,
                f"补仓保证金${dca_margin:.0f}<${EXCHANGE_MIN_MARGIN}最低要求")

        if not bal or bal.available_balance < dca_margin:
            return self._skip_plan(symbol, side,
                f"可用余额不足以补仓${dca_margin:.0f}")

        # TP/SL 重算
        entry_price = float(existing_position.get("entry_price", current_price))
        old_size = float(existing_position.get("size", 0))
        add_size = notional / current_price if current_price > 0 else 0
        if old_size + add_size > 0:
            new_avg = (entry_price * old_size + current_price * add_size) / (old_size + add_size)
        else:
            new_avg = current_price

        memory = self._query_memory(db, account_id, symbol, market_regime)
        tp, sl = self._calc_tp_sl(side, new_avg, int(leverage), volatility_pct, 0, 0, memory, tier=tier)

        # 2026-04-27: DCA 亏损地板 — 对标 pyramid 的 sl_lock_ratio，但反向
        # DCA 加仓后 SL 不得比原仓更差，防止"越补越亏"
        # long: 取更高的 SL (更靠近现价/更小亏损)；short: 取更低的 SL
        old_sl = float(existing_position.get("sl_price", 0) or 0)
        side_norm = "long" if side in ("buy", "long") else "short"
        if old_sl > 0:
            if side_norm == "long":
                sl = max(sl, old_sl)
            else:
                sl = min(sl, old_sl)

        return PositionPlan(
            action="dca",
            symbol=symbol,
            side=side,
            leverage=int(leverage),
            size_pct=DCA_SIZE_RATIO,
            notional_usd=round(notional, 2),
            margin_usd=round(dca_margin, 2),
            stop_loss_price=sl,
            take_profit_price=tp,
            confidence=ai_confidence,
            reasoning=f"逆势补仓(亏损{pnl_pct:.1%}) "
                      f"+${dca_margin:.0f}({DCA_SIZE_RATIO:.0%}×原始仓位) "
                      f"新均价${new_avg:.4f} SL地板${old_sl:.4f}",
        )

    @staticmethod
    def _skip_plan(symbol: str, side: str, reason: str) -> PositionPlan:
        logger.info("[PosMgr][SKIP] %s %s: %s", symbol, side, reason)
        return PositionPlan(
            action="skip",
            symbol=symbol,
            side=side,
            leverage=0,
            size_pct=0,
            notional_usd=0,
            margin_usd=0,
            stop_loss_price=0,
            take_profit_price=0,
            confidence=0,
            reasoning=reason,
        )


# ═══════════════════════════════════════════════════
# 整改项4: 策略记忆增量改进 — 独立函数
# ═══════════════════════════════════════════════════

def inherit_lessons(db, symbol: str, tier: str, limit: int = 10):
    """从历史同 symbol:tier 策略继承经验教训（兼容旧接口）。"""
    payload = inherit_strategy_memory(db, symbol, tier, limit=limit)
    return payload.get("key_lessons") or []


def inherit_strategy_memory(db, symbol: str, tier: str, limit: int = 10) -> dict:
    """从历史同 symbol:tier 策略继承 lessons + patterns + genome 核心字段。"""
    from backend.database.models import StrategyMemory, AIStrategy

    memories = db.query(StrategyMemory).join(
        AIStrategy, AIStrategy.strategy_id == StrategyMemory.strategy_id
    ).filter(
        AIStrategy.primary_symbol == symbol,
        AIStrategy.timeframe_tier == tier,
        StrategyMemory.total_trades > 0,
    ).order_by(StrategyMemory.total_trades.desc()).limit(3).all()

    all_lessons = []
    success_patterns = []
    failed_patterns = []
    genome_hints = {}

    for m in memories:
        if (m.total_trades or 0) < 2:
            continue
        if m.key_lessons and isinstance(m.key_lessons, list):
            all_lessons.extend(m.key_lessons)
        if m.successful_patterns and isinstance(m.successful_patterns, list):
            success_patterns.extend(m.successful_patterns[:3])
        if m.failed_patterns and isinstance(m.failed_patterns, list):
            failed_patterns.extend(m.failed_patterns[:3])
        if not genome_hints:
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == m.strategy_id
            ).first()
            if strat and strat.genome:
                g = strat.genome
                genome_hints = {
                    "trade_nature": g.get("trade_nature"),
                    "signal_params": g.get("signal_params"),
                    "direction": g.get("direction"),
                }

    return {
        "key_lessons": all_lessons[:limit],
        "successful_patterns": success_patterns[:6],
        "failed_patterns": failed_patterns[:6],
        "genome_hints": genome_hints,
    }


def update_partial_close_memory(db, strategy_id: str, symbol: str,
                                partial_pnl: float, reduce_ratio: float,
                                tier: str = "mid"):
    """减仓后更新策略记忆"""
    from backend.database.models import StrategyMemory
    import datetime

    try:
        memory = db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id == strategy_id
        ).first()

        if not memory:
            logger.warning(f"[MemoryManager] 策略 {strategy_id} 无记忆记录，跳过减仓记忆更新")
            return

        # 累加 partial_pnl
        current_partial_pnl = memory.partial_pnl or 0.0
        memory.partial_pnl = current_partial_pnl + partial_pnl

        # 累加 reduce_count
        current_count = memory.partial_close_count or 0
        memory.partial_close_count = current_count + 1

        # 更新最后减仓时间
        memory.last_reduce_at = datetime.datetime.now(timezone.utc)

        db.commit()
        logger.info(
            f"[MemoryManager] 策略 {strategy_id} 减仓记忆已更新: "
            f"partial_pnl={memory.partial_pnl:+.2f}, "
            f"reduce_count={memory.partial_close_count}"
        )
    except Exception as e:
        logger.error(f"[MemoryManager] 减仓记忆更新异常: {e}")
        db.rollback()


# 单例
position_manager = PositionMemoryManager()
