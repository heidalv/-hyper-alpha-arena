"""
Paper Trading Engine — 内置模拟交易引擎

使用真实市场价格 + 虚拟资金，完全本地执行，不依赖任何外部交易所 API。
支持：市价单/限价单、杠杆、保证金管理、止盈止损、爆仓检测、手续费模拟。
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# 动态杠杆：由 dynamic_leverage_calculator 统一计算，替代固定 LEVERAGE_CAP_BY_TIER
# 统一费率（Phase 3B §修复⑥）：与 backtest_engine.py 保持一致
# HyperLiquid taker 0.035% / maker 0.02%
from backend.services.backtest_engine.backtest_engine import TAKER_FEE as _TAKER_FEE, MAKER_FEE as _MAKER_FEE
TAKER_FEE_RATE = _TAKER_FEE    # 0.00035
MAKER_FEE_RATE = _MAKER_FEE    # 0.0002
# 滑点：动态计算，由 fee_guard.calc_slippage_rate 统一处理
# 保留常量供外部兼容引用，实际执行路径均走动态版本
SLIPPAGE_RATE = 0.0005     # 0.05% 兼容占位（已被 _calc_slip 替代）

def _calc_slip(
    notional_usd: float,
    trade_nature: str = "swing",
    is_sl: bool = False,
) -> float:
    """统一滑点入口：委托 fee_guard.calc_slippage_rate，保持单一来源。

    Args:
        notional_usd:  订单名义价值 (USD)
        trade_nature:  子仓类型 trend_follow/swing/intraday
        is_sl:         是否止损触发（快市额外放大 2x）

    Returns:
        单边滑点率
    """
    try:
        from backend.services.fee_guard import calc_slippage_rate
        return calc_slippage_rate(notional_usd, trade_nature, is_sl=is_sl)
    except Exception:
        return SLIPPAGE_RATE  # 降级到固定值
# 维持保证金率（用于爆仓计算）— 通过 fee_schedule_service 中心化
# 保留 MAINTENANCE_MARGIN_RATE 作为向后兼容的模块常量（无交易所上下文时的默认值）
# 有交易所上下文的调用点应直接用 fee_schedule_service.get_maint_margin_rate(exchange)
def _get_mm_rate():
    try:
        from backend.services.fee_schedule_service import engine_maint_margin_rate
        return engine_maint_margin_rate()  # 默认全局 settings.MAINT_MARGIN_RATIO
    except Exception:
        try:
            from backend.config.settings import MAINT_MARGIN_RATIO
            return MAINT_MARGIN_RATIO
        except Exception:
            return 0.005
MAINTENANCE_MARGIN_RATE = _get_mm_rate()


MIN_POSITION_NOTIONAL = 5.0  # 持仓名义价值低于 $5 时直接全平

# tier→nature 唯一权威(阶段 C §2:消除本类与 position_memory_manager 双映射分歧)
# 模块级别名供 from-import 测试与跨模块一致性校验引用。
from backend.services.tp_sl_authority import TIER_TO_NATURE as _TIER_TO_NATURE  # noqa: E402

# 杠杆 tier cap —— 唯一权威已迁至 leverage_authority(阶段 C),此处仅委托。
from backend.services.leverage_authority import resolve_leverage as _resolve_lev_authority  # noqa: E402


def _clamp_leverage_by_tier(leverage: float, tier) -> float:
    """按 tier cap 钳制杠杆(委托单一权威 leverage_authority,阶段 C)。

    新单不被旧仓位 max 污染(根因 2 修复)。tier=None 时权威按最高 cap
    处理并 floor 到 1.0,与历史行为等价。

    Args:
        leverage: 目标杠杆
        tier: 仓位档位 ("short"/"mid"/"long") 或 None

    Returns:
        钳制后的杠杆: 不低于 1,不高于该 tier 的 cap。
    """
    return _resolve_lev_authority(tier=tier, requested=leverage)


class PaperTradingEngine:
    """模拟交易引擎 — 单例，线程安全操作通过 DB session 隔离"""

    # ══════════════════════════════════════════════════════════════
    #  按 trade_nature 差异化的退出管理参数
    #  scalp → intraday → swing → position → trend_follow
    # ══════════════════════════════════════════════════════════════

    # tier → nature 唯一权威(阶段 C §2:消除与 position_memory_manager 的双映射分歧)
    # 绑定到模块级 _TIER_TO_NATURE(同对象),保留类属性访问 self._TIER_TO_NATURE。
    _TIER_TO_NATURE = _TIER_TO_NATURE

    # ── 止盈安全网（AI 遗漏时的最终保护）──
    #    { nature: { vol_class: pct } }
    #    [DEPRECATED Phase B+C] 利润上限安全网已统一收敛进 _run_v2_protection 的
    #    统一块 (RISK_V2_TP_SAFETY_NET_CAP, 默认 80%)。下表仅供 _run_v1_protection
    #    回退路径使用, Phase E 将随 v1 一起删除。新代码请勿读取此表。
    _TP_SAFETY_NET_BY_NATURE = {
        "scalp":        {"low": 0.04, "mid": 0.06, "high": 0.10},
        "intraday":     {"low": 0.08, "mid": 0.12, "high": 0.20},
        "swing":        {"low": 0.15, "mid": 0.25, "high": 0.40},
        "position":     {"low": 0.25, "mid": 0.40, "high": 0.60},
        "trend_follow": {"low": 0.35, "mid": 0.55, "high": 0.80},
    }

    # ── 硬性 SL 最小距离：任何机制都不能将 SL 推得比这更紧 ──
    # v5: 加密市场波动大，BTC日波2-3%、小币6-15%，SL必须给足空间
    # 震荡均值回归(scalp_mr_)专用最小 SL 距离：MR 本就是"贴区间边缘的小止损"打法
    # [2026-07-31 research] MR SL 下限对齐 ranging_mr._MR_SL_FLOOR=1.2%
    # （0.8% 仍落在 5m 噪音带，近7天大量 SL 贴 ≤0.85%）
    _MR_MIN_SL_DISTANCE = 0.012
    # [2026-07-30 crypto-native] scalp SL 下限 2.5%→1.0%（5m crypto 不需要这么宽，
    # 过宽 SL 导致亏损单被拖到大亏才止损）
    _MIN_SL_DISTANCE_BY_NATURE = {
        "scalp": 0.010, "intraday": 0.035, "swing": 0.045,
        "position": 0.055, "trend_follow": 0.065,
    }

    # ── 保本止损参数（盈利时自动推进 SL 到开仓价附近）──
    # v6: buffer 提高到至少 1x ATR (2.5-3%)，避免微利就触发保本SL被正常回调扫出
    #    [DEPRECATED Phase B+C] 保本推进已由统一块的 staged TP1 (SL→entry+ATR×0.3)
    #    接管。此表仅供 _run_v1_protection 回退使用, Phase E 随 v1 删除。
    _BREAKEVEN_BY_NATURE = {
        "scalp":        {"activation": 0.035, "buffer": 0.025},
        "intraday":     {"activation": 0.055, "buffer": 0.030},
        "swing":        {"activation": 0.08,  "buffer": 0.035},
        "position":     {"activation": 0.10,  "buffer": 0.040},
        "trend_follow": {"activation": 0.15,  "buffer": 0.050},
    }

    # ── 渐进式追踪止损 ──
    #    { nature: { vol_class: {activation, distance, tight_above, tight_dist} } }
    # v6: tight_dist 提高，避免盈利仓位被正常波动扫出
    #    [DEPRECATED Phase B+C] 追踪止损已由统一块的 TP3 后 ATR×trail_mult 追踪
    #    (REGIME_TP_PARAMS[*].trail_mult) 接管。此表仅供 _run_v1_protection 回退
    #    使用, Phase E 随 v1 删除。
    _TRAILING_BY_NATURE = {
        "scalp": {
            "low":  {"activation": 0.025, "distance": 0.015, "tight_above": 0.045, "tight_dist": 0.018},
            "mid":  {"activation": 0.030, "distance": 0.018, "tight_above": 0.050, "tight_dist": 0.022},
            "high": {"activation": 0.035, "distance": 0.022, "tight_above": 0.055, "tight_dist": 0.025},
        },
        "intraday": {
            "low":  {"activation": 0.025, "distance": 0.016, "tight_above": 0.050, "tight_dist": 0.018},
            "mid":  {"activation": 0.030, "distance": 0.022, "tight_above": 0.065, "tight_dist": 0.022},
            "high": {"activation": 0.038, "distance": 0.028, "tight_above": 0.080, "tight_dist": 0.025},
        },
        "swing": {
            "low":  {"activation": 0.045, "distance": 0.030, "tight_above": 0.10, "tight_dist": 0.020},
            "mid":  {"activation": 0.055, "distance": 0.038, "tight_above": 0.13, "tight_dist": 0.025},
            "high": {"activation": 0.070, "distance": 0.050, "tight_above": 0.16, "tight_dist": 0.032},
        },
        "position": {
            "low":  {"activation": 0.070, "distance": 0.045, "tight_above": 0.18, "tight_dist": 0.030},
            "mid":  {"activation": 0.090, "distance": 0.060, "tight_above": 0.25, "tight_dist": 0.040},
            "high": {"activation": 0.110, "distance": 0.080, "tight_above": 0.30, "tight_dist": 0.050},
        },
        "trend_follow": {
            "low":  {"activation": 0.100, "distance": 0.065, "tight_above": 0.25, "tight_dist": 0.040},
            "mid":  {"activation": 0.130, "distance": 0.090, "tight_above": 0.35, "tight_dist": 0.060},
            "high": {"activation": 0.160, "distance": 0.110, "tight_above": 0.45, "tight_dist": 0.075},
        },
    }

    # 向后兼容：无 nature / tier 时的默认值
    _TP_SAFETY_NET = _TP_SAFETY_NET_BY_NATURE["swing"]
    _BREAKEVEN_ACTIVATION = _BREAKEVEN_BY_NATURE["swing"]["activation"]
    _BREAKEVEN_BUFFER = _BREAKEVEN_BY_NATURE["swing"]["buffer"]
    _TRAILING_BY_VOL = _TRAILING_BY_NATURE["swing"]
    # 低波动币种（日波动通常 < 2%）
    _LOW_VOL_SYMBOLS = {"BTC", "ETH"}
    # 高波动币种（日波动通常 > 4%）
    _HIGH_VOL_SYMBOLS = {"VIRTUAL", "WIF", "PEPE", "DOGE", "TIA", "SEI"}

    # ════════════════════════════════════════════════════════════════════
    # Phase B+C: 统一分段止盈 + 利润回撤 + 追踪 + 止盈安全网 (ATR 自适应)
    # 设计来源: 研究文档 §2.3。所有阈值都是 ATR 倍数 (价格口径, 非杠杆 PnL%)。
    # 由 _run_v2_protection 内的统一块消费; 同一持仓的 PEO staged TP 在
    # RISK_V2_UNIFIED_STAGED_TP=true 时被旁路, 避免双触发。
    # ════════════════════════════════════════════════════════════════════
    REGIME_TP_PARAMS = {
        # [2026-07-30 crypto-native 适配] 传统参数 tp1_mult=1.5 导致 5m scalp 在 ~1% 微利
        # 就触发 TP1 + 保本 SL→entry+ATR×0.3(≈0.15%)，被正常波动击穿→breakeven_tp 100%。
        # 提升 tp1_mult 到 2.0+/2.5+/3.0，sl_mult 降低(不设过宽 SL)，trail_mult 降低(给呼吸空间)。
        "trending": {"sl_mult": 2.0, "tp1_mult": 2.0, "tp2_mult": 3.0, "tp3_mult": 5.0, "trail_mult": 2.0, "dd_hard": 4.0},
        "ranging":  {"sl_mult": 2.5, "tp1_mult": 2.5, "tp2_mult": 4.0, "tp3_mult": 6.0, "trail_mult": 2.5, "dd_hard": 4.0},
        "extreme":  {"sl_mult": 3.0, "tp1_mult": 3.0, "tp2_mult": 5.0, "tp3_mult": 8.0, "trail_mult": 3.0, "dd_hard": 4.0},
    }
    _UNIFIED_TP_DEFAULT_PARAMS = REGIME_TP_PARAMS["trending"]

    def __init__(self):
        self._tp_levels_cache: Dict[int, int] = {}
        self._peak_profit_cache: Dict[int, float] = {}  # pos_id -> peak unrealized PnL
        self._last_partial_close_at: Dict[int, datetime] = {}  # pos_id -> last partial close time
        try:
            from backend.services.profit_protection_manager import profit_manager
            self._profit_manager = profit_manager
        except Exception:
            self._profit_manager = None

    @staticmethod
    def normalize_close_reason(reason: str, pnl: float) -> str:
        """按实际盈亏修正平仓标签，避免移动止损/保本止损盈利出场仍显示「止损」。"""
        r = str(reason or "manual")
        if r == "ai_take_profit" and pnl < 0:
            return "ai_cut_loss"
        if r == "sl":
            if pnl > 0:
                return "breakeven_tp"
            if pnl >= 0:
                return "breakeven_sl"
            return "sl"
        if r == "breakeven_sl" and pnl > 0:
            return "breakeven_tp"
        return r

    @staticmethod
    def sl_reason_for_position(pos, mark_price: float) -> str:
        """SL 触发时的 reason：已推至盈利区则 breakeven_sl，否则 sl。"""
        pct = PaperTradingEngine._position_pnl_pct(pos, mark_price)
        return "breakeven_sl" if pct >= 0 else "sl"

    @staticmethod
    def _position_pnl_pct(pos, price: Optional[float] = None) -> float:
        """相对 entry 的未杠杆 PnL%，用于 staged/trailing 和退出质量口径。"""
        entry = float(getattr(pos, "entry_price", 0) or 0)
        mark = float(price if price is not None else (getattr(pos, "mark_price", 0) or 0))
        if entry <= 0 or mark <= 0:
            return 0.0
        if str(getattr(pos, "side", "")).lower() in ("long", "buy"):
            return (mark - entry) / entry
        return (entry - mark) / entry

    def _sync_peak_state(self, pos, current_upnl: float, current_price: Optional[float] = None) -> float:
        """把峰值利润写入内存和 DB 字段，避免服务重启后保护状态丢失。"""
        pos_id = int(getattr(pos, "id", 0) or 0)
        cached_peak = float(self._peak_profit_cache.get(pos_id, 0.0) or 0.0)
        db_peak = float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0)
        peak = max(cached_peak, db_peak, float(current_upnl or 0.0))
        if pos_id:
            self._peak_profit_cache[pos_id] = peak
        try:
            pos.peak_unrealized_pnl = peak
            pos.peak_pnl_pct = max(
                float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0),
                self._position_pnl_pct(pos, current_price),
            )
        except Exception:
            pass
        return peak

    # ── Phase B+C 统一保护: ATR/regime/peak-price 解析工具 ──────────────
    def _resolve_atr_pct(self, pos, entry: float, current_price: float) -> float:
        """解析 ATR(以价格的小数表示, e.g. 0.02 = 2% 价格波动)。

        口径: ATR 是价格波动幅度, 不是杠杆 PnL%。统一 TP 块全部以 ATR×mult 计算
        价格距离, 故此处返回 price_atr / price 的小数。
        优先级: UnifiedDataPool 实时 ATR → pos.atr_at_entry → 价格 2% 兜底。
        """
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            snap = UnifiedDataPool().get_snapshot(max_age=120)
            if snap and pos.symbol in snap.indicators:
                atr_1h = float(snap.indicators[pos.symbol].get("atr", 0) or 0)
                last_price = float(
                    snap.indicators[pos.symbol].get("last_price", 0)
                    or snap.indicators[pos.symbol].get("close", 0) or 0
                )
                if atr_1h > 0 and last_price > 0:
                    return atr_1h / last_price
        except Exception:
            pass
        _atr_entry = float(getattr(pos, "atr_at_entry", 0) or 0)
        if _atr_entry > 0 and entry > 0:
            return _atr_entry / entry
        # 兜底: 价格的 2%
        return 0.02

    def _resolve_regime(self, pos) -> str:
        """把持仓上的 regime 标签映射到 trending / ranging / extreme。

        来源: pos.health_regime (健康分系统) 或 exit_state_json.regime。
        未知/缺失 → "trending" (DEFAULT_PARAMS)。
        """
        _raw = (str(getattr(pos, "health_regime", "") or "").strip().lower())
        if not _raw:
            try:
                import json as _json
                _sd = _json.loads(getattr(pos, "exit_state_json", None) or "{}") or {}
                _raw = str(_sd.get("regime") or _sd.get("market_regime") or "").strip().lower()
            except Exception:
                _raw = ""
        if not _raw:
            return "trending"
        if "extreme" in _raw or "volatile" in _raw or "high_vol" in _raw:
            return "extreme"
        if "rang" in _raw or "chop" in _raw or "side" in _raw or "mean" in _raw:
            return "ranging"
        if "trend" in _raw:
            return "trending"
        return "trending"

    def _peak_price_from_pos(self, pos, entry: float) -> float:
        """由持久化的 peak_pnl_pct 反推峰值价格(跨重启稳定)。

        peak_pnl_pct 是未杠杆的价格 PnL%(见 _position_pnl_pct), 故:
          long  峰值价 = entry × (1 + peak_pnl_pct)
          short 峰值价 = entry × (1 - peak_pnl_pct)
        """
        peak_pct = float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0)
        if entry <= 0:
            return 0.0
        if str(getattr(pos, "side", "")).lower() in ("long", "buy"):
            return entry * (1.0 + peak_pct)
        return entry * (1.0 - peak_pct)

    def _record_exit_event(
        self,
        db,
        pos,
        *,
        event_type: str,
        price: Optional[float] = None,
        quantity: Optional[float] = None,
        pnl: Optional[float] = None,
        fee: Optional[float] = None,
        close_ratio: Optional[float] = None,
        exit_channel: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录退出事件流；失败不影响交易执行。"""
        try:
            import json
            from backend.database.models import PositionExitEvent

            peak_pnl = float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0)
            current_pnl = float(getattr(pos, "unrealized_pnl", 0.0) or 0.0)
            realized = float(pnl) if pnl is not None else current_pnl
            retention = None
            if peak_pnl > 0 and realized is not None:
                retention = max(-1.0, min(2.0, float(realized) / peak_pnl))

            event = PositionExitEvent(
                position_id=int(getattr(pos, "id", 0) or 0),
                account_id=int(getattr(pos, "account_id", 0) or 0),
                strategy_id=getattr(pos, "strategy_id", None),
                symbol=getattr(pos, "symbol", ""),
                side=getattr(pos, "side", ""),
                trade_nature=getattr(pos, "trade_nature", None),
                event_type=event_type,
                quantity=quantity,
                price=price,
                pnl=pnl,
                fee=fee,
                close_ratio=close_ratio,
                peak_pnl_at_event=peak_pnl,
                peak_pnl_pct_at_event=float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0),
                pnl_at_event=current_pnl,
                pnl_pct_at_event=self._position_pnl_pct(pos, price),
                retention_ratio=retention,
                health_score=getattr(pos, "health_score", None),
                health_regime=getattr(pos, "health_regime", None),
                reversal_level=(metadata or {}).get("reversal_level"),
                exit_channel=exit_channel,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            )
            db.add(event)
        except Exception as event_err:
            logger.debug(f"[Paper] 退出事件记录失败(非致命): {event_err}")

    @staticmethod
    def _enforce_min_sl(pos, entry: float, nature: str) -> None:
        """确保 SL 距离不低于硬性最小值（防止被保本/追踪压得太紧）。

        2026-04-27 修复：保本止损已将 SL 推到盈利侧（long: SL>entry, short: SL<entry）
        时不应被本函数拉回亏损侧，否则保本保护形同虚设。
        """
        min_dist = PaperTradingEngine._MIN_SL_DISTANCE_BY_NATURE.get(nature, 0.025)
        # 震荡均值回归单（scalp_mr_ 前缀）用专用小止损下限，避免被 2.5% 硬拉宽而破坏
        # "小止损小止盈"的正期望结构。
        if str(getattr(pos, "strategy_id", "") or "").startswith("scalp_mr_"):
            min_dist = PaperTradingEngine._MR_MIN_SL_DISTANCE
        if not pos.sl_price or entry <= 0:
            return
        sl = float(pos.sl_price)
        if pos.side == "long":
            # 保本/盈利保护：SL 已在 entry 上方 → 不得拉回亏损侧
            if sl > entry:
                return
            min_sl = round(entry * (1 - min_dist), 6)
            if sl > min_sl:
                pos.sl_price = min_sl
        else:
            # 保本/盈利保护：SL 已在 entry 下方 → 不得拉回亏损侧
            if sl < entry:
                return
            min_sl = round(entry * (1 + min_dist), 6)
            if sl < min_sl:
                pos.sl_price = min_sl

    # ══════════════════════════════════════════════════════════════════════
    # SL 必须在 liq 之"内"（离 entry 比 liq 更近）否则永远触发不了.
    #
    # 背景（2026-04-22 事故）:
    #     高杠杆（20x）short 仓，AI 设 sl=2411.75（距 entry +4.5%），
    #     实际 liq=2411.76（距 entry +4.5%，因为 liq = entry × (1 + 1/lev) 扣费后
    #     正好 ≈ 4.5%）。价格一路上涨穿过 sl 时 liq 同时成立，paper_engine 当时
    #     v2 代码里又把 liq 检查写在 SL 前面 → 直接以 liquidation 平仓，AI 设的
    #     sl 完全没作用，亏损放大。
    #
    # 防御:
    #     1. 开仓后、每次 price check 前，若 sl 和 liq 同向同侧的距离
    #        小于 entry × SAFETY_MARGIN（默认 0.5%），强制把 sl 向 entry 方向拉 0.5%，
    #        保证任何情况下 SL 都会先于 liq 触发。
    #     2. 调用点:  _run_v1_protection / _run_v2_protection 的 SL 检查之前。
    #
    # 副作用:
    #     若 AI / 策略主动设一个"比 liq 远"的 SL（例如 sl = 2500 > liq 2412），
    #     本函数会把 sl 拉回到 2412 - 0.5% × entry ≈ 2400.5，相当于收紧 SL。
    #     这是有意为之: 超出 liq 的 SL 本来就形同虚设。
    # ══════════════════════════════════════════════════════════════════════
    _SL_VS_LIQ_SAFETY_MARGIN = 0.005  # 默认 0.5% × entry 的安全边距

    @staticmethod
    def _ensure_sl_inside_liq(pos, safety_margin: Optional[float] = None) -> None:
        """确保 SL 位置比 liq 更靠近 entry，否则 SL 永远不会先触发."""
        if not pos.sl_price or not pos.liquidation_price or not pos.entry_price:
            return
        sl = float(pos.sl_price)
        liq = float(pos.liquidation_price)
        entry = float(pos.entry_price)
        if entry <= 0 or liq <= 0:
            return
        margin = safety_margin if safety_margin is not None else PaperTradingEngine._SL_VS_LIQ_SAFETY_MARGIN
        buffer = entry * margin

        if pos.side == "long":
            # long 仓: 价格下跌方向。sl > liq 才能先触发（两个都 < entry）
            # 要求 sl - liq >= buffer
            min_valid_sl = liq + buffer
            if sl < min_valid_sl:
                new_sl = round(min_valid_sl, 6)
                # [fix] 浮点判等：round 后 new_sl 可能 == sl（如 old=1531.988892 new=1531.988892）
                # 此时赋值无意义且每 tick 刷 WARNING，必须跳过
                if abs(new_sl - sl) < 1e-8:
                    return
                try:
                    logger.warning(
                        f"[Paper] SL 太接近 liq, 自动上抬: {pos.symbol} long "
                        f"old_sl={sl} liq={liq} → new_sl={new_sl} (buffer={margin:.2%})")
                except Exception:
                    pass
                pos.sl_price = new_sl
        else:
            # short 仓: 价格上涨方向。sl < liq 才能先触发（两个都 > entry）
            # 要求 liq - sl >= buffer
            max_valid_sl = liq - buffer
            if sl > max_valid_sl:
                new_sl = round(max_valid_sl, 6)
                # [fix] 同上：跳过无实际变化的调整
                if abs(new_sl - sl) < 1e-8:
                    return
                try:
                    logger.warning(
                        f"[Paper] SL 太接近 liq, 自动下压: {pos.symbol} short "
                        f"old_sl={sl} liq={liq} → new_sl={new_sl} (buffer={margin:.2%})")
                except Exception:
                    pass
                pos.sl_price = new_sl

    @staticmethod
    def _classify_volatility(symbol: str) -> str:
        """根据币种实际 ATR 动态分类波动率等级：low / mid / high
        优先使用实时 ATR 数据，无数据时 fallback 到硬编码列表。
        """
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            snap = UnifiedDataPool().get_snapshot(max_age=120)
            if snap and symbol in snap.indicators:
                atr_1h = snap.indicators[symbol].get("atr", 0)
                last_price = snap.indicators[symbol].get("last_price", 0) or snap.indicators[symbol].get("close", 0)
                if atr_1h > 0 and last_price > 0:
                    atr_pct = atr_1h / last_price
                    if atr_pct < 0.008:
                        return "low"
                    elif atr_pct > 0.025:
                        return "high"
                    return "mid"
        except Exception:
            pass
        if symbol in PaperTradingEngine._LOW_VOL_SYMBOLS:
            return "low"
        if symbol in PaperTradingEngine._HIGH_VOL_SYMBOLS:
            return "high"
        return "mid"

    @staticmethod
    def _utc_iso(dt) -> Optional[str]:
        from backend.utils.db_datetime import db_naive_to_utc_iso
        return db_naive_to_utc_iso(dt)

    # ── 价格获取 ──────────────────────────────────

    @staticmethod
    def _normalize_exchange(exchange: Optional[str]) -> str:
        from backend.services.exchange_config import get_active_exchange
        fallback = get_active_exchange() or "asterdex"
        return (exchange or fallback).strip().lower() or fallback

    def _resolve_account_exchange(self, db: Session, account_id: Optional[int] = None) -> str:
        """Resolve the paper exchange from the trader config, not a hardcoded venue."""
        if account_id:
            try:
                from backend.database.models import Account
                account = db.query(Account).filter(Account.id == account_id).first()
                selected = getattr(account, "selected_exchange", None) if account else None
                if selected:
                    return self._normalize_exchange(selected)
            except Exception as exc:
                logger.debug(f"[Paper] 读取账户交易所失败 account_id={account_id}: {exc}")
        try:
            from backend.services.exchange_config import get_active_exchange
            return self._normalize_exchange(get_active_exchange())
        except Exception:
            return "asterdex"

    def _resolve_order_exchange(self, db: Session, order) -> str:
        """Resolve exchange locked on the order, falling back to account config."""
        exchange = getattr(order, "exchange", None)
        if exchange:
            return self._normalize_exchange(exchange)
        return self._resolve_account_exchange(db, getattr(order, "account_id", None))

    @staticmethod
    def _get_current_price(symbol: str, exchange: Optional[str] = None) -> float:
        """获取当前价格。

        纸交易应跟随交易员配置的交易所行情盯市，避免用 A 交易所配置、
        B 交易所价格成交。
        """
        if not exchange:
            try:
                from backend.services.exchange_config import get_active_exchange
                exchange = get_active_exchange() or "asterdex"
            except Exception:
                exchange = "asterdex"
        exchange = exchange.strip().lower() or "asterdex"
        # 1) 统一行情服务：Hub → price_cache → 交易所 REST 单次
        try:
            from backend.services.market_price_service import get_price
            price = get_price(symbol, exchange)
            if price and price > 0:
                return float(price)
        except Exception:
            pass

        # 2) 尝试 price_cache（禁止静默跨所）
        try:
            from backend.services.price_cache import price_cache
            cached = price_cache.get(symbol, "CRYPTO", exchange)
            if cached and cached > 0:
                return float(cached)
        except Exception:
            pass

        # 3) 尝试 strategy_coordinator 的 robust 方法
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            price = StrategyCoordinator._get_realtime_price_robust(symbol, exchange)
            if price and price > 0:
                return float(price)
        except Exception:
            pass

        # 4) 最后才直接调用配置交易所的 ccxt，且加 timeout，避免定时任务卡死
        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止 ccxt 直连兜底
        #（纸交易盯市价格必须来自数据中心，避免绕过唯一数据源）。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.data_center import data_center
                price = data_center.get_price(symbol, exchange)
                if price and price > 0:
                    return float(price)
                raise RuntimeError(f"DC_ONLY 下数据中心无 {symbol} 价格")
        except Exception:
            pass
        try:
            import ccxt
            ccxt_exchange = "gateio" if exchange == "gate" else exchange
            if not hasattr(ccxt, ccxt_exchange):
                raise RuntimeError(f"ccxt 不支持交易所 {exchange}")
            opts = {"timeout": 3000, "enableRateLimit": True}
            if ccxt_exchange == "binance":
                opts["options"] = {"defaultType": "future"}
            ex = getattr(ccxt, ccxt_exchange)(opts)
            ticker = ex.fetch_ticker(f"{symbol}/USDT")
            if ticker and ticker.get("last"):
                return float(ticker["last"])
        except Exception:
            pass

        raise RuntimeError(f"无法获取 {symbol} 的实时价格")

    # ── 初始化 / 重置 ────────────────────────────

    def initialize_account(self, db: Session, account_id: int, initial_balance: float = 100.0) -> Dict:
        """确保模拟账户存在。如果已有余额记录则保持不动，只创建不重置。"""
        from backend.database.models import PaperBalance, Account

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise ValueError(f"Account {account_id} not found")

        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if bal:
            self._recalc_balance(db, bal)
            db.commit()
            logger.info(f"[Paper] 账户 {account_id} 已存在，保持余额不变 (equity={bal.total_equity:.2f})")
            return self._balance_to_dict(bal)

        import traceback
        logger.warning(f"[Paper] 创建PaperBalance account_id={account_id} balance={initial_balance} 调用栈:\n{''.join(traceback.format_stack())}")
        bal = PaperBalance(
            account_id=account_id,
            initial_balance=initial_balance,
            total_equity=initial_balance,
            available_balance=initial_balance,
        )
        db.add(bal)
        db.commit()
        db.refresh(bal)
        logger.info(f"[Paper] 账户 {account_id} 初始化完成, 初始资金={initial_balance} USDT")
        return self._balance_to_dict(bal)

    def reset_balance_only(self, db: Session, account_id: int) -> Dict:
        """软重置：仅重置钱包数字（余额/盈亏/手续费），保留持仓、订单和交易对配置"""
        from backend.database.models import PaperBalance, PaperPosition

        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            raise ValueError(f"Paper account {account_id} not found")

        # 计算 open positions 的 margin 和 unrealized PnL（保留）
        open_positions = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.status == "open",
        ).all()
        total_margin = sum(float(p.margin or 0) for p in open_positions)
        total_upnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)

        bal.realized_pnl = 0.0
        bal.total_fee_paid = 0.0
        bal.frozen_margin = total_margin
        bal.unrealized_pnl = total_upnl
        bal.available_balance = bal.initial_balance - total_margin
        bal.total_equity = bal.available_balance + total_margin + total_upnl
        bal.last_reset_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(bal)
        logger.info(f"[Paper] 账户 {account_id} 钱包已软重置 (仅余额/盈亏), 保留持仓")
        return self._balance_to_dict(bal)

    def set_initial_balance(self, db: Session, account_id: int, new_balance: float) -> Dict:
        """修改模拟账户初始金额。仅允许在无持仓时修改。"""
        from backend.database.models import PaperBalance, PaperPosition

        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            # 尚未初始化过 paper_balances 时，直接按目标金额创建（避免前端必须先点「初始化」才能改金额）
            return self.initialize_account(db, account_id, new_balance)

        # 检查是否有持仓
        open_count = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.status == "open",
        ).count()
        if open_count > 0:
            raise ValueError(f"Cannot change balance: account has {open_count} open positions. Close all positions first.")

        old_balance = bal.initial_balance
        bal.initial_balance = new_balance
        self._recalc_balance(db, bal)
        db.commit()
        db.refresh(bal)
        logger.info(f"[Paper] 账户 {account_id} 初始金额已修改: {old_balance} → {new_balance}")
        return self._balance_to_dict(bal)

    def reset_account(self, db: Session, account_id: int) -> Dict:
        """硬重置：清除所有持仓和订单，恢复初始资金。不会影响 FullAuto 交易对配置。"""

        from backend.database.models import PaperBalance, PaperPosition, PaperOrder

        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            raise ValueError(f"Paper account {account_id} not found")

        db.query(PaperPosition).filter(PaperPosition.account_id == account_id).delete()
        db.query(PaperOrder).filter(PaperOrder.account_id == account_id).delete()

        initial = bal.initial_balance
        bal.total_equity = initial
        bal.available_balance = initial
        bal.frozen_margin = 0.0
        bal.unrealized_pnl = 0.0
        bal.realized_pnl = 0.0
        bal.total_fee_paid = 0.0
        bal.last_reset_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(bal)
        logger.info(f"[Paper] 账户 {account_id} 已硬重置, 资金恢复到 {initial} USDT (持仓/订单已清除, 交易对配置不受影响)")
        return self._balance_to_dict(bal)

    # ── 下单 ──────────────────────────────────────

    def _record_es_event(self, event_type: str, aggregate_id: str, payload: dict) -> None:
        """整改#9 Phase 2/4：双写事件 + 同步内存投影（失败不影响交易）。"""
        try:
            from backend.services.event_sourcing.phase4 import record_event_first
            record_event_first(event_type, aggregate_id, payload)
        except Exception as _es_err:
            logger.debug(f"[EventSourcing#9] 记录失败（忽略）: {_es_err}")

    def place_order(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: Optional[float] = None,
        leverage: float = 1.0,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        strategy_id: Optional[str] = None,
        timeframe_tier: Optional[str] = None,
        add_type: Optional[str] = None,
        trade_nature: Optional[str] = None,
        expected_hold_hours: Optional[float] = None,
        position_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        下单入口。返回与真实交易兼容的 order_result dict，
        供 ai_strategy_engine 的 StrategyTrade 记录使用。
        """
        # === 单一闸(阶段 D):所有下单(主控+scalp)必经 ===
        # 职责:per-(account,symbol) 锁串行化 + 方向冲突拒 + 杠杆权威钳制
        from backend.services.trade_gate import trade_gate as _gate
        _gate.acquire(account_id, symbol)
        try:
            _g = _gate.check(
                db, account_id, symbol, side, leverage,
                tier=timeframe_tier, trade_nature=trade_nature,
            )
            if not _g.allowed:
                logger.warning(
                    "TradeGate rejected order: %s (acct=%s sym=%s side=%s)",
                    _g.reason, account_id, symbol, side)
                return None
            # 用闸的权威杠杆值覆盖入参(单一权威)
            leverage = _g.leverage
            from backend.database.models import PaperBalance, PaperOrder

            bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
            if not bal:
                raise ValueError(f"PaperBalance not found for account {account_id}. Please initialize the paper account first.")
            exchange = self._resolve_account_exchange(db, account_id)

            # ── 整改#5：引擎层硬风控（最外层、业务无关的最后一道防线）──
            # RISK_ENGINE_ENABLED=true 时生效；无 InstrumentSpec 登记则仅限流/重复/名义/保证金硬规则。
            # 任何异常/未启用均透传放行，绝不阻断主流程。
            try:
                from backend.services.exchange.risk_engine import get_risk_engine, OrderRequest as _EngOrder
                _eng = get_risk_engine()
                if _eng.enabled:
                    _rp = price if (price and price > 0) else self._get_current_price(symbol, exchange)
                    _denied = _eng.check_submit(
                        _EngOrder(
                            symbol=symbol, side=side, quantity=float(quantity or 0),
                            price=(float(_rp) if _rp else None),
                            notional=(float(quantity or 0) * float(_rp)) if _rp else None,
                            reduce_only=bool(add_type in ("reduce", "close")),
                        ),
                        account_state={
                            "free_balance": float(bal.available_balance or 0),
                        },
                    )
                    if _denied is not None:
                        logger.warning(f"[Paper][RiskEngine#5] 拦截 {symbol} {side} qty={quantity}: {_denied.reason_text}")
                        return {
                            "success": False, "blocked": True,
                            "blocked_layer": "engine_risk", "blocked_by": _denied.category.value,
                            "reason": _denied.reason_text, "reason_code": _denied.category.value,
                        }
            except Exception as _eng_err:
                logger.debug(f"[Paper][RiskEngine#5] 检查异常（放行）: {_eng_err}")

            # ── 风控（2026-05-08 v2 升级到 UnifiedRiskGate）──
            # 同时跑 DeterministicRiskGate（瞬时硬规则）+ RiskControlService（带状态规则，
            # 包含 daily_loss / consecutive_losses / max_symbol_entries_per_day 等）。
            # 紧急情况可设 PAPER_RISK_GATE_ENABLED=false 关闭。
            import os as _os
            from backend.services.lock_strength_service import get_lock_strength_service
            _paper_profile = get_lock_strength_service().get_profile("paper")
            _gate_on = _os.getenv("PAPER_RISK_GATE_ENABLED", "true").lower() in ("true", "1", "yes")
            if _gate_on and _paper_profile.paper_risk_gate and not _paper_profile.disable_loss_locks:
                try:
                    _ref_price = price if (price and price > 0) else self._get_current_price(symbol, exchange)
                    _notional = float(quantity) * float(_ref_price or 0)
                    _lev = float(leverage or 1.0)
                    _margin = _notional / max(_lev, 1.0)

                    from backend.services.unified_risk_gate import unified_check
                    from backend.database.models import PaperPosition as _PP

                    _equity = float(bal.total_equity or 0)
                    _avail = float(bal.available_balance or 0)
                    _frozen = float(bal.frozen_margin or 0)
                    _existing = db.query(_PP).filter(
                        _PP.account_id == account_id, _PP.status == "open"
                    ).all()
                    _positions = [
                        {
                            "symbol": p.symbol, "side": p.side,
                            "margin": float(p.margin or 0),
                            "notional": float(p.size or 0) * float(p.entry_price or 0),
                            "size": float(p.size or 0),
                            "leverage": float(p.leverage or 1),
                            # 净额视角: 带符号 size (long 正 / short 负)
                            "net_signed_size": (
                                float(p.size or 0) if str(p.side or "").lower() == "long"
                                else -float(p.size or 0)
                            ),
                        }
                        for p in _existing
                    ]
                    _margin_pct = (_frozen / _equity * 100.0) if _equity > 0 else 0.0
                    _ures = unified_check(
                        db=db,
                        account_id=account_id,
                        symbol=symbol, side=side,
                        notional=_notional, margin=_margin, leverage=_lev,
                        total_equity=_equity, available_balance=_avail, frozen_margin=_frozen,
                        realized_pnl_today=float(bal.realized_pnl or 0),
                        margin_usage_percent=_margin_pct,
                        existing_positions=_positions,
                        op_source="paper",
                    )
                    if not _ures.passed:
                        logger.warning(
                            f"[Paper] 风控拦截 {symbol} {side} qty={quantity} lev={_lev}: "
                            f"{_ures.reason_text} (layer={_ures.blocked_layer}, rule={_ures.blocked_rule})"
                        )
                        return {
                            "success": False,
                            "blocked": True,
                            "blocked_layer": _ures.blocked_layer,
                            "blocked_by": _ures.blocked_rule,
                            "reason": _ures.reason_text,
                            "reason_code": _ures.reason_code,
                        }
                    if _ures.warnings:
                        logger.info(
                            f"[Paper] 风控告警（不阻塞）{symbol} {side}: "
                            f"{[w['rule'] for w in _ures.warnings]}"
                        )
                except Exception as _rg_err:
                    # [fix] 风控检查异常时 rollback，避免 InFailedSqlTransaction 污染后续操作
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.warning(f"[Paper] 风控检查异常（放行）: {_rg_err}", exc_info=True)

            # ── 单向(One-Way)反手净额抵消（2026-07-03 修复：消除同层多空并存伪对冲）──
            # 历史逻辑 _fill_market_order 开仓时只查"同方向"持仓(side == pos_side)，反向单直接
            # 新开一行，导致 scalp/swing/trend 同层同币同时挂多单+空单（伪对冲）：白交两遍手续
            # 费、盈亏互相抵消、界面"短线全是多空对冲单"。真 One-Way 语义下反向单应先平/减已有
            # 反向仓，剩余量才翻新仓。开关 PAPER_ONE_WAY_REVERSE_NETTING 默认开，可 env 回退。
            try:
                from backend.config.settings import PAPER_ONE_WAY_REVERSE_NETTING as _RN_ON
            except Exception:
                _RN_ON = True
            if _RN_ON and float(quantity or 0) > 0 and add_type not in ("add", "dca"):
                from backend.database.models import PaperPosition as _PPRN
                _pos_side = "long" if side == "buy" else "short"
                _opp_side = "short" if _pos_side == "long" else "long"
                _nature_eff = trade_nature or "swing"
                _rev_rows = (
                    db.query(_PPRN)
                    .filter(
                        _PPRN.account_id == account_id,
                        _PPRN.symbol == symbol,
                        _PPRN.side == _opp_side,
                        _PPRN.status == "open",
                        _PPRN.trade_nature == _nature_eff,
                    )
                    .order_by(_PPRN.opened_at.asc())
                    .all()
                )
                _rev_total = sum(float(getattr(r, "size", 0) or 0) for r in _rev_rows)
                if _rev_total > 1e-9:
                    _offset = min(float(quantity), _rev_total)
                    _remaining = _offset
                    for _r in _rev_rows:
                        if _remaining <= 1e-9:
                            break
                        _rsize = float(getattr(_r, "size", 0) or 0)
                        if _rsize <= 0:
                            continue
                        _take = min(_rsize, _remaining)
                        try:
                            self.close_position(
                                db,
                                account_id,
                                symbol,
                                _opp_side,
                                reason="reverse_netting",
                                quantity=_take,
                                strategy_id=getattr(_r, "strategy_id", None),
                            )
                        except Exception as _rn_err:
                            logger.warning(f"[Paper] 反向净额平仓异常(放行剩余): {_rn_err}")
                        _remaining -= _take
                    logger.info(
                        f"[Paper] 单向反手净额: {symbol}[{_nature_eff}] {side} "
                        f"抵消反向仓 {_offset:.6f}/{_rev_total:.6f}"
                    )
                    quantity = float(quantity) - _offset
                    if quantity <= 1e-9:
                        # 本次订单被反向持仓完全抵消 → 纯减仓，不再新开同方向仓
                        # (close_position 已结算盈亏/释放保证金/重算余额并 commit)
                        return {
                            "order_id": f"paper_reduce_{symbol}_{datetime.now(timezone.utc).timestamp():.0f}",
                            "symbol": symbol,
                            "side": side,
                            "status": "filled",
                            "quantity": _offset,
                            "filled_quantity": _offset,
                            "reduce_only_result": True,
                            "reason": "reverse_netting_full_offset",
                            "realized_pnl": 0.0,
                        }

            order = PaperOrder(
                account_id=account_id,
                strategy_id=strategy_id,
                exchange=exchange,
                symbol=symbol,
                side=side,
                order_type=order_type,
                price=price,
                quantity=quantity,
                leverage=leverage,
                tp_price=tp_price,
                sl_price=sl_price,
                status="pending",
            )
            if hasattr(order, "trade_nature"):
                order.trade_nature = trade_nature or "swing"
            db.add(order)
            db.flush()

            if order_type == "market":
                result = self._fill_market_order(
                    db, order, bal,
                    timeframe_tier=timeframe_tier,
                    add_type=add_type,
                    trade_nature=trade_nature,
                    expected_hold_hours=expected_hold_hours,
                    position_metadata=position_metadata,
                )
                return result
            else:
                db.commit()
                logger.info(f"[Paper] 限价单已挂出: {symbol} {side} qty={quantity} @{price}")
                return {
                    "order_id": f"paper_{order.id}",
                    "symbol": symbol,
                    "side": side,
                    "status": "pending",
                    "price": price,
                    "quantity": quantity,
                }
        finally:
            _gate.release(account_id, symbol)

    def _fill_market_order(self, db: Session, order, bal,
                           timeframe_tier: Optional[str] = None,
                           add_type: Optional[str] = None,
                           trade_nature: Optional[str] = None,
                           expected_hold_hours: Optional[float] = None,
                           position_metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """以当前市价成交 market order（按 strategy_id 隔离仓位）"""
        from backend.database.models import PaperPosition

        self._recalc_balance(db, bal)

        try:
            exchange = self._resolve_order_exchange(db, order)
            current_price = self._get_current_price(order.symbol, exchange)
        except RuntimeError as e:
            # 回退到下单时传入的 price
            if order.price and order.price > 0:
                current_price = order.price
                logger.info(f"[Paper] 使用下单时传入价格: {current_price}")
            else:
                order.status = "cancelled"
                db.commit()
                logger.error(f"[Paper] 市价单取消，无法获取价格: {e}")
                return None

        # 动态滑点：按订单规模、子仓类型计算
        _open_notional_est = order.quantity * current_price
        _slip = _calc_slip(_open_notional_est, trade_nature or "swing", is_sl=False)
        sim_bid = current_price * (1 - _slip)
        sim_ask = current_price * (1 + _slip)

        # 杠杆由上游 AI/风控下单参数决定。这里不能再次用动态杠杆覆盖。
        try:
            order.leverage = max(1.0, float(order.leverage or 1.0))
        except Exception:
            order.leverage = 1.0

        from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
        from backend.services.exchange.paper_exchange_simulator import (
            PaperMarketState,
            simulate_exchange_order,
        )
        force_maker = bool(str(order.order_type or "").lower() == "limit")

        sim_order_type = OrderType.LIMIT if str(order.order_type or "").lower() == "limit" else OrderType.MARKET
        sim_result = simulate_exchange_order(
            exchange=exchange,
            order=ExchangeOrder(
                order_id=f"paper_{order.id}",
                symbol=order.symbol,
                side=OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
                order_type=sim_order_type,
                size=float(order.quantity or 0),
                price=float(order.price) if order.price else None,
                leverage=int(round(order.leverage)),
            ),
            market=PaperMarketState(
                symbol=order.symbol,
                mark_price=float(current_price),
                bid=float(sim_bid),
                ask=float(sim_ask),
            ),
            available_balance=float(bal.available_balance or 0),
            resting_limit=force_maker,
        )
        if sim_result.status.value != "filled":
            if sim_result.status.value == "open":
                order.status = "pending"
                db.commit()
                return {
                    "order_id": f"paper_{order.id}",
                    "symbol": order.symbol,
                    "side": order.side,
                    "status": "pending",
                    "price": order.price,
                    "quantity": order.quantity,
                }
            order.status = "rejected"
            order.close_reason = "rejected"
            db.commit()
            logger.warning(
                f"[Paper] 交易所仿真拒单: {order.symbol} {order.side} "
                f"qty={order.quantity} lev={order.leverage} reason={sim_result.reject_reason}"
            )
            return {
                "order_id": f"paper_{order.id}",
                "symbol": order.symbol,
                "side": order.side,
                "status": "rejected",
                "error": sim_result.reject_reason,
            }

        fill_price = sim_result.fill_price
        notional = sim_result.notional_usd
        margin_needed = sim_result.margin_usd
        fee = sim_result.fee_usd

        # ── 净额视角保证金检查 ──
        # PAPER_NETTING_MODE=true 时，反向对冲订单会释放已有净头寸的保证金，
        # 该释放量已反映在 bal.available_balance 中（_recalc_balance 已按净额重算）。
        # 这里仅记录净额增量审计信息，不改变检查逻辑。
        netting_on = False
        try:
            from backend.config.settings import PAPER_NETTING_MODE
            netting_on = bool(PAPER_NETTING_MODE)
        except Exception:
            netting_on = True

        if netting_on:
            try:
                from backend.services.paper_netting import (
                    compute_net_position, compute_margin_delta_for_order,
                )
                # per-exchange 维持保证金率（订单上下文可知交易所）
                try:
                    from backend.services.fee_schedule_service import get_maint_margin_rate
                    _order_mmr = get_maint_margin_rate(getattr(order, "exchange", None))
                except Exception:
                    _order_mmr = MAINTENANCE_MARGIN_RATE
                _cur_net = compute_net_position(
                    db, order.account_id, order.symbol, _order_mmr,
                )
                _delta, _scenario = compute_margin_delta_for_order(
                    _cur_net, order.side, float(order.quantity or 0),
                    float(fill_price or current_price), float(order.leverage or 1.0),
                )
                if _scenario != "add_same_side":
                    logger.info(
                        f"[Paper] 净额增量审计: {order.symbol} {order.side} "
                        f"qty={order.quantity} scenario={_scenario} "
                        f"cur_net={_cur_net.net_side}{_cur_net.net_size:.6f} "
                        f"margin_delta={_delta:.2f} (raw_margin_needed={margin_needed:.2f})"
                    )
            except Exception as _net_err:
                logger.warning(f"[Paper] 净额增量审计异常（放行）: {_net_err}")

        if margin_needed + fee > bal.available_balance:
            order.status = "rejected"
            order.close_reason = "rejected"
            db.commit()
            logger.warning(f"[Paper] 余额不足: 需要 {margin_needed + fee:.2f}, 可用 {bal.available_balance:.2f}")
            return {
                "order_id": f"paper_{order.id}",
                "symbol": order.symbol,
                "side": order.side,
                "status": "rejected",
                "error": f"余额不足: 需要${margin_needed + fee:.2f}, 可用${bal.available_balance:.2f}",
            }

        # 填充订单
        order.filled_price = fill_price
        order.filled_quantity = order.quantity
        order.fee = fee
        order.entry_price = float(fill_price)
        order.status = "filled"
        order.filled_at = datetime.now(timezone.utc)

        # 持仓方向映射
        pos_side = "long" if order.side == "buy" else "short"

        # ── 查找已有同 trade_nature 持仓（子仓追踪需要分离的仓位）──
        # 按 trade_nature 隔离：同 nature 内合并 (add/dca)，不同 nature 分仓追踪
        # 杠杆统一由后续 _unify_leverage_for_side 保证
        existing_query = db.query(PaperPosition).filter(
            PaperPosition.account_id == order.account_id,
            PaperPosition.symbol == order.symbol,
            PaperPosition.side == pos_side,
            PaperPosition.status == "open",
            PaperPosition.trade_nature == trade_nature,
        )
        existing = existing_query.first()

        if existing:
            # ── 同 trade_nature 内合并 (add/dca) ──
            total_size = existing.size + order.quantity
            existing.entry_price = (
                (existing.entry_price * existing.size + fill_price * order.quantity) / total_size
            )
            existing.size = total_size
            # 杠杆是交易指令，不从保证金反推。加仓/补仓后直接沿用本次订单杠杆，
            # 保证金按新的总名义价值 ÷ 订单杠杆重算。
            # [根因 2 修复] add/DCA 不用 max 提杠杆,按目标仓位 tier cap 钳制,
            # 避免新订单把既有低杠杆仓位强制提到高杠杆。
            existing.leverage = _clamp_leverage_by_tier(
                float(order.leverage or existing.leverage or 1.0),
                getattr(existing, "timeframe_tier", None),
            )
            existing.margin = (existing.size * existing.entry_price) / existing.leverage
            existing.mark_price = current_price
            existing.liquidation_price = self._calc_liquidation_price(
                existing.entry_price, existing.side, existing.leverage
            )
            if order.tp_price:
                existing.tp_price = order.tp_price
            if order.sl_price:
                existing.sl_price = order.sl_price
            existing.unrealized_pnl = self._calc_unrealized_pnl(
                existing.entry_price, current_price, existing.size, existing.side
            )
            if add_type == "dca":
                existing.dca_count = (getattr(existing, 'dca_count', None) or 0) + 1
                existing.dca_total_added = (getattr(existing, 'dca_total_added', None) or 0) + margin_needed
            else:
                existing.add_count = (getattr(existing, 'add_count', None) or 0) + 1
            existing.last_add_at = datetime.now(timezone.utc)

            # 2026-04-27: DCA/Pyramid 加仓后均价变化 → 重置保护状态
            # tp_level_reached 基于旧均价，新均价下需重新评估
            if hasattr(existing, 'tp_level_reached'):
                existing.tp_level_reached = 0
            # 清除 DSM 追踪止损内部状态（trailing_high/low, activation_hit）
            self._peak_profit_cache.pop(existing.id, None)
            try:
                from backend.services.adaptive_executor.dynamic_sl_tp import get_stop_manager
                get_stop_manager().reset_position_state(str(existing.id))
            except Exception as _dsm_err:
                logger.warning(f"[PaperEngine] reset_position_state 异常: {_dsm_err}")
            if order.strategy_id:
                existing.strategy_id = order.strategy_id
            if timeframe_tier:
                existing.timeframe_tier = timeframe_tier
            try:
                if expected_hold_hours and float(expected_hold_hours) > 0:
                    existing.expected_hold_hours = float(expected_hold_hours)
                elif not getattr(existing, "expected_hold_hours", None):
                    from backend.services.position_hold_time import resolve_initial_expected_hold_hours
                    _eh_nature = getattr(existing, "trade_nature", None) or (trade_nature or "swing")
                    _eh_tier = getattr(existing, "timeframe_tier", None) or timeframe_tier
                    existing.expected_hold_hours = resolve_initial_expected_hold_hours(
                        _eh_nature, _eh_tier
                    )
            except Exception as _crit_err:
                logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass
        else:
            liq_price = self._calc_liquidation_price(fill_price, pos_side, order.leverage)
            _pos_kwargs = dict(
                account_id=order.account_id,
                symbol=order.symbol,
                side=pos_side,
                size=order.quantity,
                original_size=order.quantity,
                entry_price=fill_price,
                mark_price=current_price,
                leverage=order.leverage,
                margin=margin_needed,
                original_margin=margin_needed,
                liquidation_price=liq_price,
                tp_price=order.tp_price,
                sl_price=order.sl_price,
                strategy_id=order.strategy_id,
                timeframe_tier=timeframe_tier,
                add_count=0,
                dca_count=0,
                dca_total_added=0.0,
            )
            _pos_kwargs["trade_nature"] = trade_nature or "swing"
            try:
                if expected_hold_hours and float(expected_hold_hours) > 0:
                    _pos_kwargs["expected_hold_hours"] = float(expected_hold_hours)
                else:
                    from backend.services.position_hold_time import resolve_initial_expected_hold_hours
                    _pos_kwargs["expected_hold_hours"] = resolve_initial_expected_hold_hours(
                        _pos_kwargs["trade_nature"], timeframe_tier
                    )
            except Exception as _crit_err:
                logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass
            if position_metadata:
                import json as _json
                _pos_kwargs["metadata_json"] = _json.dumps(position_metadata, ensure_ascii=False)
            pos = PaperPosition(**_pos_kwargs)
            db.add(pos)

        # ── 杠杆统一：所有同币种同方向仓位使用相同杠杆（最低 tier 上限）──
        # Hyperliquid 同币种同方向只有一个 net position，杠杆统一。
        # 子仓系统靠 trade_nature 分离追踪，但杠杆必须一致。
        self._unify_leverage_for_side(db, order.account_id, order.symbol, pos_side, order.leverage)
        self._sync_attached_orders(db, existing if existing else pos)

        self._recalc_balance(db, bal)

        # ── 整改#9 Phase4：event-first（flush 拿 id → 写事件 → commit）──
        target_pos = existing if existing else pos
        try:
            db.flush()
            _pre_commit_pos_id = getattr(target_pos, "id", None)
        except Exception:
            _pre_commit_pos_id = getattr(target_pos, "id", None)
        if _pre_commit_pos_id:
            self._record_es_event(
                "PositionOpened" if not existing else "PositionChanged",
                str(_pre_commit_pos_id),
                {
                    # [2026-07-11 修复] 本函数作用域内没有裸变量 account_id（只有
                    # order.account_id），此前直接引用会抛 NameError，被上层
                    # place_order 的 except 捕获后包装成"下单失败"——这是导致
                    # 短线所有下单（包括已通过EV/门控闸门的信号）100%失败、
                    # 长期"信号一堆但从来没有真实成交"的直接根因（已确认至少
                    # 从 2026-07-10 起就在报错，与本轮 EV/校准修复无关，是
                    # 独立的下单链路 P0 bug）。改用 order.account_id。
                    "account_id": int(order.account_id),
                    "symbol": order.symbol, "side": pos_side,
                    "size": float(getattr(target_pos, "size", 0) or 0),
                    "entry_price": float(getattr(target_pos, "entry_price", 0) or 0),
                    "leverage": float(order.leverage or 1), "fee": float(fee or 0),
                    "trade_nature": trade_nature or "swing",
                    # [2026-07-11 修复] 同上，本函数没有裸变量 strategy_id，只有
                    # order.strategy_id（见上方 account_id 同批修复的注释）。
                    "strategy_id": order.strategy_id,
                },
            )

        db.commit()

        actual_pos_id = existing.id if existing else pos.id

        logger.info(
            f"[Paper] 成交: {order.symbol} {order.side} qty={order.quantity} "
            f"@{fill_price:.2f} lev={order.leverage}x fee={fee:.4f} pos_id={actual_pos_id}"
        )

        # 开仓时记录信号快照（信号反馈闭环）
        try:
            from backend.services.signal_feedback_tracker import signal_feedback_tracker
            from backend.services.intelligence_signal_engine import IntelligenceSignalEngine
            _engine = IntelligenceSignalEngine()
            _sig = _engine.compute_trading_signal(order.symbol)
            _active_signals = {}
            if _sig.funding:
                _active_signals["funding"] = {"direction": _sig.funding.signal, "value": _sig.funding.rate}
            if _sig.oi:
                _active_signals["oi"] = {"direction": _sig.oi.signal, "value": _sig.oi.oi_change_pct}
            if _sig.liquidation:
                _active_signals["liquidation"] = {"direction": _sig.liquidation.signal, "value": 0}
            if abs(_sig.whale_direction) > 0.1:
                _active_signals["whale"] = {"direction": "bullish" if _sig.whale_direction > 0 else "bearish", "value": _sig.whale_direction}

            # V3: 因子快照（与情报信号独立 — 短线 scalp 也需要 IC 闭环样本）
            _factor_vals = None
            try:
                from backend.services.factor_engine import factor_engine as _fe
                from backend.services.kline_data_service import kline_service
                _period = "5m" if getattr(order, "trade_nature", None) == "scalp" else "15m"
                _raw = kline_service.get_klines_from_db(
                    order.symbol.upper(), _period, 100,
                )
                if _raw:
                    import pandas as _pd
                    _fv_df = _pd.DataFrame(_raw)
                    _fvals = _fe.compute_all_factors(_fv_df)
                    if _fvals:
                        _factor_vals = {
                            k: (v.value if hasattr(v, "value") else float(v))
                            for k, v in _fvals.items()
                        }
            except Exception as _fe_err:
                logger.debug(f"[Paper] 因子快照计算失败(非致命): {_fe_err}")

            if _active_signals or _factor_vals:
                signal_feedback_tracker.record_entry_signals(
                    db, order.account_id, actual_pos_id, order.symbol, pos_side,
                    _active_signals, factor_values=_factor_vals,
                )
                logger.debug(
                    f"[Paper] 信号快照已记录: pos_id={actual_pos_id} "
                    f"signals={len(_active_signals)} factors={len(_factor_vals or {})}"
                )
        except Exception as _sf_err:
            logger.debug(f"[Paper] 信号快照记录失败(非致命): {_sf_err}")

        return {
            "order_id": f"paper_{order.id}",
            "position_id": actual_pos_id,
            "symbol": order.symbol,
            "side": order.side,
            "price": fill_price,
            "quantity": order.quantity,
            "leverage": order.leverage,
            "fee": fee,
            "status": "filled",
            "paper": True,
        }

    @staticmethod
    def _stamp_order_from_position(order, pos) -> None:
        """平仓单复制持仓身份字段，保证 PaperOrder 归因与 StrategyMemory 一致。"""
        if hasattr(order, "trade_nature"):
            order.trade_nature = getattr(pos, "trade_nature", None) or "swing"
        sid = getattr(pos, "strategy_id", None)
        if sid and not getattr(order, "strategy_id", None):
            order.strategy_id = sid

    # ── 平仓 ──────────────────────────────────────

    def close_position(
        self, db: Session, account_id: int, symbol: str, side: str,
        reason: str = "manual", quantity: Optional[float] = None,
        strategy_id: Optional[str] = None,
        fill_price_override: Optional[float] = None,
        trigger_order_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """平掉指定持仓，支持部分平仓 (quantity=None 表示全部平仓)

        按 strategy_id 精确匹配子仓位；不传则匹配任意同币种同方向仓位。
        子仓系统靠 trade_nature 隔离，平仓时需要 strategy_id 精确定位。
        """
        from backend.database.models import PaperPosition, PaperBalance, PaperOrder

        pos_query = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.side == side,
            PaperPosition.status == "open",
        )
        if strategy_id:
            pos_query = pos_query.filter(PaperPosition.strategy_id == strategy_id)
        pos = pos_query.first()
        if not pos:
            logger.warning(f"[Paper] 无持仓可平: {symbol} {side}"
                           f"{' strategy=' + strategy_id if strategy_id else ''}")
            return None

        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            return None

        exchange = self._resolve_account_exchange(db, account_id)
        try:
            current_price = self._get_current_price(symbol, exchange)
        except RuntimeError:
            current_price = pos.mark_price

        close_side = "sell" if side == "long" else "buy"
        if quantity is not None and quantity <= 0:
            logger.warning(f"[Paper] 无效平仓数量: quantity={quantity}, 忽略")
            return None

        remaining_size = float(pos.size)
        is_partial = quantity is not None and 0 < quantity < float(pos.size)
        close_qty = float(quantity) if is_partial else remaining_size
        fill_price, close_fee = self._simulate_reduce_fill(
            exchange=exchange,
            pos=pos,
            close_side=close_side,
            quantity=close_qty,
            current_price=float(current_price or 0),
            reason=reason,
            fill_price_override=fill_price_override,
        )

        if is_partial:
            return self._partial_close(
                db, pos, bal, account_id, symbol, side, close_side,
                fill_price, quantity, reason, close_fee,
            )

        # ── 全部平仓 ──
        final_pnl = self._calc_unrealized_pnl(pos.entry_price, fill_price, remaining_size, pos.side)
        final_fee = close_fee

        partial_pnl_sum = float(pos.partial_realized_pnl or 0)
        partial_fee_sum = float(pos.partial_fee_paid or 0)
        total_pnl = final_pnl + partial_pnl_sum
        total_fee = final_fee + partial_fee_sum
        original_sz = float(pos.original_size or remaining_size)

        # 剩余仓位的亏损不应超过其剩余保证金（爆仓保护）
        remaining_margin = float(pos.margin or 0)
        if remaining_margin > 0 and final_pnl < -remaining_margin:
            logger.warning(
                f"[Paper] 剩余仓位亏损({final_pnl:.2f})超过剩余保证金({remaining_margin:.2f})，截断"
            )
            final_pnl = -remaining_margin
        total_pnl = final_pnl + partial_pnl_sum

        # 根据实际盈亏修正 reason 标签
        actual_reason = self.normalize_close_reason(reason, total_pnl)

        close_order = None
        if trigger_order_id:
            close_order = db.query(PaperOrder).filter(
                PaperOrder.id == trigger_order_id,
                PaperOrder.account_id == account_id,
                PaperOrder.status == "pending",
            ).first()

        if close_order is None:
            close_order = PaperOrder(
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                side=close_side,
                order_type="market",
                quantity=remaining_size,
                leverage=pos.leverage,
                strategy_id=getattr(pos, "strategy_id", None),
            )
            db.add(close_order)

        close_order.filled_quantity = remaining_size
        close_order.filled_price = fill_price
        close_order.leverage = pos.leverage
        close_order.fee = final_fee
        close_order.pnl = final_pnl
        close_order.entry_price = float(pos.entry_price or 0) or None
        close_order.close_reason = actual_reason
        close_order.status = "filled"
        close_order.filled_at = datetime.now(timezone.utc)
        self._stamp_order_from_position(close_order, pos)

        pos.status = "closed"
        pos.close_price = fill_price
        pos.close_reason = actual_reason
        pos.closed_at = datetime.now(timezone.utc)
        # 将已实现盈亏写入 unrealized_pnl 字段（closed 状态下复用为 realized_pnl 存档）
        # _recalc_balance 只查 open 仓位，不影响余额计算
        pos.unrealized_pnl = total_pnl

        # ── 整改#9 Phase4：event-first 平仓事件（commit 前）──
        self._record_es_event(
            "PositionClosed", str(getattr(pos, "id", "")),
            {
                "account_id": int(account_id),
                "symbol": pos.symbol, "side": pos.side,
                "exit_price": float(fill_price or 0),
                "realized_pnl": float(total_pnl or 0), "close_reason": actual_reason,
            },
        )
        self._record_exit_event(
            db, pos,
            event_type="final_trade_outcome",
            price=fill_price,
            quantity=remaining_size,
            pnl=total_pnl,
            fee=total_fee,
            close_ratio=1.0,
            exit_channel=actual_reason,
            metadata={"reason": actual_reason, "final_pnl": final_pnl, "partial_pnl": partial_pnl_sum},
        )

        self._recalc_balance(db, bal)
        self._cancel_attached_orders(
            db, pos,
            exclude_order_id=int(close_order.id) if getattr(close_order, "id", None) else None,
        )
        db.commit()

        self._tp_levels_cache.pop(pos.id, None)
        self._peak_profit_cache.pop(pos.id, None)

        # ── 记录再开仓冷却（防追踪止盈后立即同向重开）──
        try:
            from backend.services.reentry_cooldown import record_full_close
            from backend.services.sub_position_manager import NATURE_TO_TIER
            _nature = (getattr(pos, "trade_nature", None) or "").strip().lower()
            _close_tier = (
                getattr(pos, "timeframe_tier", None)
                or NATURE_TO_TIER.get(_nature, "mid")
                or "mid"
            )
            _is_master = actual_reason.startswith("master_") and "_reduce" not in actual_reason
            record_full_close(
                account_id, symbol, side, tier=_close_tier,
                is_master_close=_is_master, close_pnl=total_pnl,
                close_reason=actual_reason,
            )
        except Exception as _rc_err:
            logger.warning(f"[Paper] reentry_cooldown 记录失败: {_rc_err}", exc_info=True)

        # ── D7: 决策复盘 — 平仓时自动写复盘记录 ──
        try:
            self._write_retrospective(db, account_id, pos, fill_price, total_pnl, actual_reason)
        except Exception as _rp_err:
            # [fix] rollback 避免 InFailedSqlTransaction 污染后续操作
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"[Paper] 复盘记录失败: {_rp_err}", exc_info=True)

        logger.info(
            f"[Paper] 平仓: {symbol} {side} @{fill_price:.2f} "
            f"final_pnl={final_pnl:+.2f} + partial={partial_pnl_sum:+.2f} = total={total_pnl:+.2f} "
            f"fee={total_fee:.4f} reason={actual_reason}"
        )

        # ── 飞书通知：平仓事件 ──
        # M10 样本仓库：统一交易事实表
        try:
            self._write_trade_fact(
                account_id=account_id,
                position_id=str(getattr(pos, "id", "") or ""),
                symbol=symbol,
                tier=_close_tier,
                side=side,
                entry_price=float(getattr(pos, "entry_price", 0) or 0),
                exit_price=float(fill_price or 0),
                fees=float(total_fee or 0),
                pnl=float(total_pnl or 0),
                outcome=("win" if (total_pnl or 0) > 0
                         else ("loss" if (total_pnl or 0) < 0 else "scratch")),
                close_reason=actual_reason,
            )
        except Exception as _tf_err:
            logger.debug(f"[Paper] trade_fact 写入失败: {_tf_err}")

        try:
            import asyncio
            from backend.services.openclaw_notify import notify_trade_close, notify_tp_sl_trigger
            _hold_h = 0
            if pos.closed_at and pos.opened_at:
                _o = pos.opened_at
                _c = pos.closed_at
                if _o.tzinfo is None:
                    from datetime import timezone as _tz
                    _o = _o.replace(tzinfo=_tz.utc)
                if _c.tzinfo is None:
                    from datetime import timezone as _tz
                    _c = _c.replace(tzinfo=_tz.utc)
                _hold_h = max(0, (_c - _o).total_seconds() / 3600)

            if actual_reason in ("sl", "tp", "breakeven_sl", "breakeven_tp",
                                 "trailing", "trailing_stop", "safety_tp"):
                _coro = notify_tp_sl_trigger(
                    symbol=symbol, side=side, trigger_type=actual_reason,
                    price=fill_price, pnl=total_pnl,
                )
            else:
                _coro = notify_trade_close(
                    symbol=symbol, side=side, pnl=total_pnl,
                    reason=actual_reason, hold_hours=_hold_h,
                )
            try:
                from backend.services.arbitrage.async_bridge import run_async
                run_async(_coro)
            except Exception as _nf_run_err:
                logger.debug(f"[Paper] 平仓通知发送失败(async bridge): {_nf_run_err}")
        except Exception as _nf_err:
            logger.debug(f"[Paper] 平仓通知发送失败(非致命): {_nf_err}")

        # 学习系统和仓位记忆使用完整的 total_pnl（含分批利润）
        try:
            self._notify_learning_on_close(db, pos, fill_price, total_pnl, reason)
        except Exception as learn_err:
            # [fix] rollback 避免 InFailedSqlTransaction 污染后续操作
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"[Paper] 学习通知失败: {learn_err}", exc_info=True)

        try:
            from backend.services.position_memory_manager import position_manager
            hold_secs = 0
            if pos.closed_at and pos.opened_at:
                o = pos.opened_at
                c = pos.closed_at
                if o.tzinfo is None:
                    o = o.replace(tzinfo=timezone.utc)
                if c.tzinfo is None:
                    c = c.replace(tzinfo=timezone.utc)
                hold_secs = max(0, int((c - o).total_seconds()))
            original_margin = original_sz * float(pos.entry_price) / float(pos.leverage or 10)
            position_manager.record_trade_result(
                db=db,
                account_id=account_id,
                symbol=symbol,
                side=side,
                entry_price=float(pos.entry_price),
                exit_price=fill_price,
                size=original_sz,
                leverage=float(pos.leverage or 10),
                pnl=total_pnl,
                fee=total_fee,
                hold_seconds=hold_secs,
                margin_used=original_margin,
                close_reason=reason,
            )
        except Exception as mem_err:
            logger.warning(f"[Paper] 仓位记忆写入失败: {mem_err}", exc_info=True)

        return {
            "symbol": symbol,
            "side": close_side,
            "price": fill_price,
            "quantity": remaining_size,
            "entry_price": float(pos.entry_price),
            "leverage": float(pos.leverage or 10),
            "margin": float(pos.margin or 0),
            "pnl": total_pnl,
            "fee": total_fee,
            "reason": reason,
            "closed_fully": True,
        }

    def _partial_close(
        self, db, pos, bal, account_id, symbol, side, close_side,
        fill_price, close_qty, reason, fill_fee: Optional[float] = None,
    ) -> Dict[str, Any]:
        """手动部分平仓：减仓 close_qty，仓位保持 open"""
        from backend.database.models import PaperOrder

        pos_size = float(pos.size)
        close_ratio = close_qty / pos_size
        partial_pnl = self._calc_unrealized_pnl(pos.entry_price, fill_price, close_qty, pos.side)
        # 修复（2026-06-24）：兜底原用固定 TAKER_FEE_RATE(hyperliquid)，现按实际交易所费率。
        if fill_fee is not None:
            partial_fee = float(fill_fee)
        else:
            try:
                from backend.services.fee_schedule_service import get_fee_rate
                _p_ex = self._resolve_account_exchange(db, account_id)
                partial_fee = close_qty * fill_price * get_fee_rate(_p_ex, is_maker=False)
            except Exception:
                partial_fee = close_qty * fill_price * TAKER_FEE_RATE

        base_reason = reason if reason else "manual_partial"
        actual_reason = self.normalize_close_reason(base_reason, partial_pnl)
        if not reason:
            actual_reason = "manual_partial"
        partial_order = PaperOrder(
            account_id=account_id,
            exchange=self._resolve_account_exchange(db, account_id),
            symbol=symbol,
            side=close_side,
            order_type="market",
            quantity=close_qty,
            filled_quantity=close_qty,
            filled_price=fill_price,
            leverage=pos.leverage,
            fee=partial_fee,
            pnl=partial_pnl,
            entry_price=float(pos.entry_price or 0) or None,
            close_reason=actual_reason,
            status="filled",
            filled_at=datetime.now(timezone.utc),
            strategy_id=getattr(pos, "strategy_id", None),
        )
        self._stamp_order_from_position(partial_order, pos)
        db.add(partial_order)

        pos.partial_realized_pnl = float(pos.partial_realized_pnl or 0) + partial_pnl
        pos.partial_fee_paid = float(pos.partial_fee_paid or 0) + partial_fee
        remaining = pos_size - close_qty
        margin_release = float(pos.margin) * close_ratio
        pos.size = remaining
        pos.margin = float(pos.margin) - margin_release
        self._record_exit_event(
            db, pos,
            event_type="partial_exit_event",
            price=fill_price,
            quantity=close_qty,
            pnl=partial_pnl,
            fee=partial_fee,
            close_ratio=close_ratio,
            exit_channel=actual_reason,
            metadata={"reason": actual_reason, "remaining_size": remaining},
        )

        self._sync_attached_orders(db, pos)
        self._recalc_balance(db, bal)
        db.commit()

        try:
            self._notify_learning_on_close(
                db, pos, fill_price, partial_pnl, actual_reason,
                is_partial=True, learning_weight=0.5,
            )
        except Exception as _pl_err:
            logger.debug(f"[Paper] 部分平仓学习更新跳过: {_pl_err}")

        logger.info(
            f"[Paper] 手动部分平仓: {symbol} {side} 减仓 {close_qty:.6f} @{fill_price:.2f} "
            f"partial_pnl={partial_pnl:+.2f} fee={partial_fee:.4f} 剩余={remaining:.6f} "
            f"reason={reason}"
        )

        return {
            "symbol": symbol,
            "side": close_side,
            "price": fill_price,
            "quantity": close_qty,
            "entry_price": float(pos.entry_price),
            "leverage": float(pos.leverage or 10),
            "margin": float(pos.margin or 0),
            "pnl": partial_pnl,
            "fee": partial_fee,
            "reason": reason,
            "closed_fully": False,
            "remaining_size": remaining,
        }

    def _partial_close_by_pct(self, db, pos, pct: float, reason: str):
        """按比例分批平仓（v2 利润保护专用）"""
        close_qty = round(float(pos.size) * pct, 8)
        if close_qty < 1e-8:
            return None
        return self.close_position(
            db, pos.account_id, pos.symbol, pos.side,
            reason=reason, quantity=close_qty,
            strategy_id=getattr(pos, "strategy_id", None),
        )

    def _simulate_reduce_fill(
        self,
        *,
        exchange: str,
        pos,
        close_side: str,
        quantity: float,
        current_price: float,
        reason: str,
        fill_price_override: Optional[float] = None,
    ) -> tuple[float, float]:
        """Use the unified paper exchange layer for reduce-only close fills."""
        from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
        from backend.services.exchange.paper_exchange_simulator import (
            PaperMarketState,
            PaperOrderStatus,
            simulate_exchange_order,
        )

        qty = max(float(quantity or 0), 0.0)
        if qty <= 0:
            return 0.0, 0.0

        if fill_price_override is not None and float(fill_price_override) > 0:
            bid = ask = mark = float(fill_price_override)
        else:
            close_is_sl = reason in (
                "stop_loss", "sl", "liquidation", "force_close",
                "trailing_stop", "trailing", "safety_tp",
            )
            close_notional = qty * float(current_price or 0)
            close_nature = getattr(pos, "trade_nature", None) or "swing"
            close_slip = _calc_slip(close_notional, close_nature, is_sl=close_is_sl)
            mark = float(current_price or 0)
            bid = mark * (1 - close_slip)
            ask = mark * (1 + close_slip)

        sim = simulate_exchange_order(
            exchange=exchange,
            order=ExchangeOrder(
                order_id=f"paper_reduce_{getattr(pos, 'id', 0) or 0}",
                symbol=pos.symbol,
                side=OrderSide.BUY if close_side == "buy" else OrderSide.SELL,
                order_type=OrderType.MARKET,
                size=qty,
                leverage=int(round(float(getattr(pos, "leverage", 1) or 1))),
                reduce_only=True,
            ),
            market=PaperMarketState(
                symbol=pos.symbol,
                mark_price=mark,
                bid=bid,
                ask=ask,
            ),
        )
        if sim.status != PaperOrderStatus.FILLED:
            logger.warning(
                f"[Paper] reduce-only 平仓仿真拒单: {pos.symbol} {close_side} "
                f"qty={qty} exchange={exchange} reason={sim.reject_reason}"
            )
            # 修复（2026-06-24）：兜底原用固定 TAKER_FEE_RATE(hyperliquid 0.035%)，
            # 若账户是 asterdex(0.005%)/binance(0.04%) 则费率不符（最多偏差7倍）。
            # 现按实际交易所费率兜底。
            try:
                from backend.services.fee_schedule_service import get_fee_rate
                _fallback_rate = get_fee_rate(exchange, is_maker=False)
            except Exception:
                _fallback_rate = TAKER_FEE_RATE
            return mark, qty * mark * _fallback_rate
        return float(sim.fill_price), float(sim.fee_usd)

    def _attached_order_side(self, pos) -> str:
        return "sell" if str(pos.side or "").lower() == "long" else "buy"

    def _cancel_attached_orders(self, db: Session, pos, exclude_order_id: Optional[int] = None) -> None:
        from backend.database.models import PaperOrder

        q = db.query(PaperOrder).filter(
            PaperOrder.account_id == pos.account_id,
            PaperOrder.symbol == pos.symbol,
            PaperOrder.side == self._attached_order_side(pos),
            PaperOrder.status == "pending",
            PaperOrder.order_type.in_(("take_profit", "stop_loss")),
        )
        if getattr(pos, "strategy_id", None):
            q = q.filter(PaperOrder.strategy_id == pos.strategy_id)
        if exclude_order_id:
            q = q.filter(PaperOrder.id != exclude_order_id)
        for order in q.all():
            order.status = "cancelled"

    def _sync_attached_orders(self, db: Session, pos) -> None:
        """Create/update exchange-style pending TP/SL orders for an open position."""
        from backend.database.models import PaperOrder

        if str(getattr(pos, "status", "")) != "open":
            return
        close_side = self._attached_order_side(pos)
        exchange = self._resolve_account_exchange(db, getattr(pos, "account_id", None))

        def _pending(order_type: str):
            q = db.query(PaperOrder).filter(
                PaperOrder.account_id == pos.account_id,
                PaperOrder.symbol == pos.symbol,
                PaperOrder.side == close_side,
                PaperOrder.status == "pending",
                PaperOrder.order_type == order_type,
            )
            if getattr(pos, "strategy_id", None):
                q = q.filter(PaperOrder.strategy_id == pos.strategy_id)
            return q.order_by(PaperOrder.id.desc()).all()

        def _upsert(order_type: str, price: Optional[float], reason: str) -> None:
            orders = _pending(order_type)
            keep = orders[0] if orders else None
            for extra in orders[1:]:
                extra.status = "cancelled"
            if not price or float(price) <= 0:
                if keep:
                    keep.status = "cancelled"
                return
            if keep is None:
                keep = PaperOrder(
                    account_id=pos.account_id,
                    strategy_id=getattr(pos, "strategy_id", None),
                    exchange=exchange,
                    symbol=pos.symbol,
                    side=close_side,
                    order_type=order_type,
                    quantity=float(pos.size or 0),
                    leverage=float(pos.leverage or 1),
                    status="pending",
                    trade_nature=getattr(pos, "trade_nature", None),
                    close_reason=reason,
                )
                db.add(keep)
            keep.price = float(price)
            keep.exchange = exchange
            keep.quantity = float(pos.size or 0)
            keep.filled_quantity = 0.0
            keep.filled_price = None
            keep.leverage = float(pos.leverage or 1)
            keep.trade_nature = getattr(pos, "trade_nature", None)
            keep.close_reason = reason
            keep.entry_price = float(pos.entry_price or 0) or None

        _upsert("take_profit", pos.tp_price, "tp")
        _upsert("stop_loss", pos.sl_price, "sl")

    def _find_attached_order(self, db: Session, pos, order_type: str):
        from backend.database.models import PaperOrder

        q = db.query(PaperOrder).filter(
            PaperOrder.account_id == pos.account_id,
            PaperOrder.symbol == pos.symbol,
            PaperOrder.side == self._attached_order_side(pos),
            PaperOrder.status == "pending",
            PaperOrder.order_type == order_type,
        )
        if getattr(pos, "strategy_id", None):
            q = q.filter(PaperOrder.strategy_id == pos.strategy_id)
        return q.order_by(PaperOrder.id.desc()).first()

    def _apply_exchange_attached_orders(self, db: Session, pos, current_price: float) -> bool:
        """先执行交易所侧 TP/SL 条件单，再运行内部风控。

        真实交易中 TP/SL 挂在交易所，项目卡顿也应按触发价成交。
        Paper 也必须先模拟这个行为，避免超时或利润保护抢在止损/止盈前面。
        """
        from backend.services.exchange.paper_exchange_simulator import (
            PaperTriggerReason,
            evaluate_attached_tp_sl,
        )

        trigger = evaluate_attached_tp_sl(
            position_side=str(pos.side or ""),
            mark_price=float(current_price or 0),
            take_profit=float(pos.tp_price or 0) if pos.tp_price else None,
            stop_loss=float(pos.sl_price or 0) if pos.sl_price else None,
        )
        if trigger == PaperTriggerReason.STOP_LOSS and pos.sl_price:
            trigger_order = self._find_attached_order(db, pos, "stop_loss")
            sl_reason = self.sl_reason_for_position(pos, float(current_price or 0))
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason=sl_reason,
                strategy_id=getattr(pos, "strategy_id", None),
                fill_price_override=float(pos.sl_price),
                trigger_order_id=getattr(trigger_order, "id", None),
            )
            self._tp_levels_cache.pop(pos.id, None)
            return True
        if trigger == PaperTriggerReason.TAKE_PROFIT and pos.tp_price:
            trigger_order = self._find_attached_order(db, pos, "take_profit")
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason="tp",
                strategy_id=getattr(pos, "strategy_id", None),
                fill_price_override=float(pos.tp_price),
                trigger_order_id=getattr(trigger_order, "id", None),
            )
            self._tp_levels_cache.pop(pos.id, None)
            return True
        return False

    def _enforce_max_hold_timeout(self, db, pos) -> bool:
        """三周期持仓时限：登记 AI 复审；仅极端情况才规则兜底强平。"""
        try:
            current_price = float(getattr(pos, "mark_price", 0) or 0)
            if current_price > 0:
                if pos.sl_price:
                    sl_price = float(pos.sl_price)
                    hit_sl = (pos.side == "long" and current_price <= sl_price) or \
                             (pos.side == "short" and current_price >= sl_price)
                    if hit_sl:
                        logger.warning(
                            f"[Paper] 官方SL优先触发: {pos.symbol} {pos.side} "
                            f"mark={current_price} SL={sl_price}"
                        )
                        sl_reason = self.sl_reason_for_position(pos, current_price)
                        self.close_position(
                            db, pos.account_id, pos.symbol, pos.side,
                            reason=sl_reason,
                            strategy_id=getattr(pos, "strategy_id", None),
                            fill_price_override=sl_price,
                        )
                        return True

                if pos.tp_price:
                    tp_price = float(pos.tp_price)
                    hit_tp = (pos.side == "long" and current_price >= tp_price) or \
                             (pos.side == "short" and current_price <= tp_price)
                    if hit_tp:
                        logger.info(
                            f"[Paper] 官方TP优先触发: {pos.symbol} {pos.side} "
                            f"mark={current_price} TP={tp_price}"
                        )
                        self.close_position(
                            db, pos.account_id, pos.symbol, pos.side,
                            reason="tp",
                            strategy_id=getattr(pos, "strategy_id", None),
                            fill_price_override=tp_price,
                        )
                        return True

                if pos.liquidation_price:
                    liq_price = float(pos.liquidation_price)
                    hit_liq = (pos.side == "long" and current_price <= liq_price) or \
                              (pos.side == "short" and current_price >= liq_price)
                    if hit_liq:
                        logger.warning(
                            f"[Paper] 爆仓线触发: {pos.symbol} {pos.side} "
                            f"mark={current_price} liq={liq_price}"
                        )
                        self.close_position(
                            db, pos.account_id, pos.symbol, pos.side,
                            reason="liquidation",
                            strategy_id=getattr(pos, "strategy_id", None),
                            fill_price_override=liq_price,
                        )
                        return True

            from backend.services.hold_timeout_review_queue import (
                register_position_for_review,
                should_fallback_force_close,
                clear_position,
                get_pending_for_account,
            )
            from backend.services.position_hold_time import (
                is_position_hold_expired,
                format_hold_timeout_reason,
                is_short_no_ai_hold_nature,
            )

            account_id = int(getattr(pos, "account_id", 0) or 0)
            _nature = (getattr(pos, "trade_nature", "") or "").strip().lower()

            # 短线 scalp/intraday：不进 AI 复审；超时则规则强平（交给 TP/SL 之外的硬上限）
            if is_short_no_ai_hold_nature(_nature):
                expired, status = is_position_hold_expired(pos)
                if expired:
                    logger.warning(
                        f"[Paper] ⏰ 短线持仓硬超时强平: {pos.symbol} {pos.side} "
                        f"{format_hold_timeout_reason(status, pos.symbol)}"
                    )
                    self.close_position(
                        db,
                        pos.account_id,
                        pos.symbol,
                        pos.side,
                        reason="max_hold_timeout",
                        strategy_id=getattr(pos, "strategy_id", None),
                    )
                    self._tp_levels_cache.pop(pos.id, None)
                    self._peak_profit_cache.pop(pos.id, None)
                    return True
                return False

            # P0 修复：复审冷却期（被复审过的仓位 30 分钟内不再触发）
            _pos_key = f"{pos.id}"
            _now = time.time()
            _last_review_ts = getattr(self, "_review_cooldown", {}).get(_pos_key, 0)
            if _now - _last_review_ts < 1800:  # 30 分钟冷却
                return False
            if not hasattr(self, "_review_cooldown"):
                self._review_cooldown = {}
            self._review_cooldown[_pos_key] = _now

            flagged = register_position_for_review(pos, account_id=account_id)
            if flagged:
                _pending = get_pending_for_account(account_id)
                _rec = next((x for x in _pending if x.get("position_id") == pos.id), {})
                _rc = int(_rec.get("review_count", 0))
                expired, status = is_position_hold_expired(pos)
                if expired:
                    logger.info(
                        f"[Paper] ⏰ 持仓超时→排队AI复审: {pos.symbol} {pos.side} "
                        f"{format_hold_timeout_reason(status, pos.symbol)} "
                        f"(review#{_rc})"
                    )

            force, force_reason = should_fallback_force_close(
                pos, review_count=int(
                    next(
                        (x.get("review_count", 0) for x in get_pending_for_account(account_id)
                         if x.get("position_id") == pos.id),
                        0,
                    )
                ),
            )
            if force:
                sym = getattr(pos, "symbol", "")
                logger.warning(
                    f"[Paper] ⏰ 持仓超时兜底强平: {sym} {getattr(pos, 'side', '')} — {force_reason}"
                )
                self.close_position(
                    db,
                    pos.account_id,
                    pos.symbol,
                    pos.side,
                    reason="max_hold_timeout",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                clear_position(pos.id)
                self._tp_levels_cache.pop(pos.id, None)
                self._peak_profit_cache.pop(pos.id, None)
                return True
            return False
        except Exception as e:
            logger.debug(f"[Paper] max_hold 检查跳过: {e}")
            return False

    def _run_v1_protection(self, db, pos, entry, current_price, profit_pct, _nature):
        """v1 旧保护逻辑（保本推进 + 追踪止损 + SL + TP + 安全网）

        返回 True 表示持仓已平，调用方应 continue。

        [DEPRECATED Phase B+C — Phase E 已加运行期 warn] 默认配置
        (PROFIT_PROTECTION_VERSION=v2) 下本方法不会执行; 仅当 PROFIT_PROTECTION_VERSION
        被显式设为非 v2 或 _profit_manager 未初始化时才会作为兜底命中。Phase B+C 已将
        分段止盈/利润回撤/追踪/止盈安全网统一收敛进 _run_v2_protection._run_unified_staged_tp
        (ATR 自适应)。Phase E 在所有 dispatch 点加了 logger.warning 以监控意外触发;
        后续清理 PR 将删除本方法及其依赖的 _TP_SAFETY_NET_BY_NATURE /
        _BREAKEVEN_BY_NATURE / _TRAILING_BY_NATURE 三张表。请勿在新代码中调用本方法。
        """
        if self._enforce_max_hold_timeout(db, pos):
            return True

        _be_cfg = self._BREAKEVEN_BY_NATURE.get(_nature, self._BREAKEVEN_BY_NATURE["swing"])
        _be_activation = _be_cfg["activation"]
        _be_buffer = _be_cfg["buffer"]

        # ── 1. 保本止损自动推进 ──
        if entry > 0 and profit_pct >= _be_activation:
            if pos.side == "long":
                breakeven_sl = round(entry * (1 + _be_buffer), 6)
                current_sl = float(pos.sl_price) if pos.sl_price else 0
                if current_sl < breakeven_sl:
                    logger.info(
                        f"[Paper] 保本止损推进[{_nature}]: {pos.symbol} long "
                        f"SL {current_sl:.6f}→{breakeven_sl:.6f} "
                        f"(profit={profit_pct:.1%})")
                    pos.sl_price = breakeven_sl
            else:
                breakeven_sl = round(entry * (1 - _be_buffer), 6)
                current_sl = float(pos.sl_price) if pos.sl_price else float('inf')
                if current_sl > breakeven_sl:
                    logger.info(
                        f"[Paper] 保本止损推进[{_nature}]: {pos.symbol} short "
                        f"SL {current_sl:.6f}→{breakeven_sl:.6f} "
                        f"(profit={profit_pct:.1%})")
                    pos.sl_price = breakeven_sl

        # ── 2. 渐进式追踪止损 ──
        try:
            from backend.config.settings import RISK_USE_NATURE_EXIT_ORCHESTRATOR as _use_peo
        except Exception:
            _use_peo = False
        _trail_vol = self._classify_volatility(pos.symbol)
        _nature_trailing = self._TRAILING_BY_NATURE.get(_nature, self._TRAILING_BY_NATURE["swing"])
        _trail_cfg = _nature_trailing.get(_trail_vol, _nature_trailing.get("mid", {}))
        trail_activation = _trail_cfg["activation"]
        trail_distance = _trail_cfg["distance"]
        trail_tight_above = _trail_cfg["tight_above"]
        trail_tight_dist = _trail_cfg["tight_dist"]

        # ── TP 进度保护 ──
        # 2026-04-27: Pyramid 加仓后 entry 抬高 → profit_pct 下降，
        # 不应因此清除追踪止损（仓位已加大，追踪保护更需要保留）
        _tp_progress_ok = True
        _has_pyramid = (getattr(pos, 'add_count', None) or 0) > 0
        if pos.tp_price and pos.entry_price:
            _entry = float(pos.entry_price)
            _tp = float(pos.tp_price)
            _tp_dist = abs(_tp - _entry) / _entry if _entry > 0 else 0
            if _tp_dist > 0.005 and profit_pct < _tp_dist * 0.50 and not _has_pyramid:
                _tp_progress_ok = False
                if pos.trailing_stop_price:
                    logger.info(
                        f"[Paper] TP保护清除追踪价: {pos.symbol} {pos.side} "
                        f"profit={profit_pct:.2%} < TP进度50%({_tp_dist*0.5:.2%}), "
                        f"清除 trail={pos.trailing_stop_price}")
                    pos.trailing_stop_price = None

        if (not _use_peo) and profit_pct >= trail_activation and _tp_progress_ok:
            effective_dist = trail_tight_dist if profit_pct >= trail_tight_above else trail_distance
            if pos.side == "long":
                new_trail = round(current_price * (1 - effective_dist), 6)
                if not pos.trailing_stop_price or new_trail > pos.trailing_stop_price:
                    pos.trailing_stop_price = new_trail
            else:
                new_trail = round(current_price * (1 + effective_dist), 6)
                if not pos.trailing_stop_price or new_trail < pos.trailing_stop_price:
                    pos.trailing_stop_price = new_trail

        # ── 2.5 无保护持仓安全网：没有 SL 且已浮亏超 8% → 自动设置紧急止损 ──
        _NO_SL_EMERGENCY_PCT = {
            "scalp": -0.03, "intraday": -0.05, "swing": -0.08,
            "position": -0.10, "trend_follow": -0.12,
        }
        if (not pos.sl_price or float(pos.sl_price) <= 0) and profit_pct < 0:
            _emergency_threshold = _NO_SL_EMERGENCY_PCT.get(_nature, -0.08)
            if profit_pct <= _emergency_threshold:
                _sl_dist = abs(_emergency_threshold) * 1.2
                if pos.side == "long":
                    emergency_sl = round(entry * (1 - _sl_dist), 6)
                else:
                    emergency_sl = round(entry * (1 + _sl_dist), 6)
                pos.sl_price = emergency_sl
                logger.warning(
                    f"[Paper] 无SL紧急保护[{_nature}]: {pos.symbol} {pos.side} "
                    f"profit={profit_pct:.1%}≤{_emergency_threshold:.0%}，"
                    f"设置紧急SL=${emergency_sl:.4f}")

        # ── 2.9 硬性 SL 最小距离保护 ──
        self._enforce_min_sl(pos, entry, _nature)

        # ── 2.95 SL ↔ liq 安全边距保护（2026-04-22 爆仓事故修复） ──
        # 若 SL 紧贴 liq，价格跳到 liq 线时爆仓反而先于 SL 触发。
        # 把 SL 向 entry 方向拉 0.5% × entry，保证 SL 永远先于 liq。
        self._ensure_sl_inside_liq(pos)

        # ── 3. 检查止损 ──
        if pos.sl_price:
            hit_sl = (pos.side == "long" and current_price <= pos.sl_price) or \
                     (pos.side == "short" and current_price >= pos.sl_price)
            if hit_sl:
                reason = "breakeven_sl" if profit_pct >= 0 else "sl"
                logger.info(
                    f"[Paper] {'保本止损' if reason == 'breakeven_sl' else 'SL'} 触发: "
                    f"{pos.symbol} {pos.side} @{current_price} SL={pos.sl_price}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason=reason,
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.sl_price),
                )
                return True

        # ── 4. 检查 TP ──
        if pos.tp_price:
            hit_tp = (pos.side == "long" and current_price >= pos.tp_price) or \
                     (pos.side == "short" and current_price <= pos.tp_price)
            if hit_tp:
                logger.info(f"[Paper] TP 触发: {pos.symbol} {pos.side} @{current_price} TP={pos.tp_price}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="tp",
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.tp_price),
                )
                self._tp_levels_cache.pop(pos.id, None)
                return True

        # ── 5. 追踪止损触发 ──
        if (not _use_peo) and pos.trailing_stop_price and pos.trailing_stop_price > 0:
            hit_trail = (pos.side == "long" and current_price <= pos.trailing_stop_price) or \
                        (pos.side == "short" and current_price >= pos.trailing_stop_price)
            if hit_trail:
                _allow_trail_trigger = True
                if pos.tp_price and pos.entry_price:
                    _entry = float(pos.entry_price)
                    _tp = float(pos.tp_price)
                    _tp_dist = abs(_tp - _entry) / _entry if _entry > 0 else 0
                    if _tp_dist > 0.005 and profit_pct < _tp_dist * 0.30:
                        _allow_trail_trigger = False
                        logger.info(
                            f"[Paper] 追踪止损被TP保护阻止: {pos.symbol} {pos.side} "
                            f"profit={profit_pct:.2%} < TP进度30%({_tp_dist*0.3:.2%}), "
                            f"清除trail={pos.trailing_stop_price}")
                        pos.trailing_stop_price = None

                if _allow_trail_trigger:
                    logger.info(
                        f"[Paper] Trailing Stop 触发: {pos.symbol} {pos.side} "
                        f"@{current_price} trail={pos.trailing_stop_price} profit={profit_pct:.2%}")
                    self.close_position(
                        db, pos.account_id, pos.symbol, pos.side,
                        reason="trailing",
                        strategy_id=getattr(pos, "strategy_id", None),
                        fill_price_override=float(pos.trailing_stop_price),
                    )
                    return True

        # ── 6. 爆仓检查 ──
        if pos.liquidation_price and pos.liquidation_price > 0:
            hit_liq = (pos.side == "long" and current_price <= pos.liquidation_price) or \
                      (pos.side == "short" and current_price >= pos.liquidation_price)
            if hit_liq:
                logger.warning(
                    f"[Paper] 爆仓! {pos.symbol} {pos.side} "
                    f"@{current_price} liq={pos.liquidation_price}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="liquidation",
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.liquidation_price),
                )
                return True

        # ── 7. 止盈安全网 ──
        if entry > 0:
            vol_tier = self._classify_volatility(pos.symbol)
            _nature_tp_net = self._TP_SAFETY_NET_BY_NATURE.get(_nature, self._TP_SAFETY_NET_BY_NATURE["swing"])
            safety_pct = _nature_tp_net.get(vol_tier, 0.25)
            if profit_pct >= safety_pct:
                logger.info(
                    f"[Paper] 止盈安全网[{_nature}]触发: {pos.symbol} {pos.side} "
                    f"profit={profit_pct:.1%} >= {safety_pct:.0%}")
                self.close_position(db, pos.account_id, pos.symbol, pos.side, reason="safety_tp")
                self._tp_levels_cache.pop(pos.id, None)
                return True

        return False

    def _run_unified_staged_tp(
        self, db, pos, entry, current_price, profit_pct,
        *, atr_pct: Optional[float] = None, tp_cap: float = 0.80,
    ) -> bool:
        """Phase B+C 统一保护块: 分段止盈 + 利润回撤 + 追踪 + 止盈安全网。

        所有阈值均为 ATR×mult (价格口径)。返回 True 表示持仓已全平, 调用方应 continue;
        False 表示仅做了减仓 / SL 收紧 / 无动作, 继续走后续保护。

        状态持久化:
          - pos.tp_level_reached: 已触达的最高 TP 档位 (0/1/2/3)
          - pos.peak_pnl_pct: 峰值价格 PnL% (用于跨重启反推 peak price)
          - pos.sl_price: 每段 TP 后收紧 (单调向 entry 有利方向)
        """
        _entry = float(entry or 0)
        _price = float(current_price or 0)
        if _entry <= 0 or _price <= 0:
            return False

        _atr = float(atr_pct) if atr_pct and atr_pct > 0 else self._resolve_atr_pct(pos, _entry, _price)
        if _atr <= 0:
            _atr = 0.02
        # ATR 价格绝对距离 (entry × atr 小数); SL 价格偏移用此变量, ATR 倍数阈值用 _atr 小数
        _atr_price = _entry * _atr
        _regime = self._resolve_regime(pos)
        _params = self.REGIME_TP_PARAMS.get(_regime, self._UNIFIED_TP_DEFAULT_PARAMS)
        _side = str(getattr(pos, "side", "long")).lower()
        # side_direction: long → +1 (价涨盈利), short → -1 (价跌盈利)
        _side_dir = 1.0 if _side in ("long", "buy") else -1.0

        # 价格变动 (以 ATR 为单位, 盈利方向为正)
        _price_change_atr = ((_price - _entry) / _entry) * _side_dir / _atr

        # 峰值价格 (由持久化的 peak_pnl_pct 反推, 跨重启稳定)
        _peak_price = self._peak_price_from_pos(pos, _entry)
        # 当前价若创新高/新低(盈利方向), 更新 peak_pnl_pct 使 _peak_price 推进
        if _price_change_atr > 0:
            try:
                _new_peak_pct = max(
                    float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0),
                    self._position_pnl_pct(pos, _price),
                )
                if _new_peak_pct > float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0):
                    pos.peak_pnl_pct = _new_peak_pct
                    _peak_price = self._peak_price_from_pos(pos, _entry)
            except Exception:
                pass

        _level = int(getattr(pos, "tp_level_reached", 0) or 0)
        _tp1_done = _level >= 1
        _tp2_done = _level >= 2
        _tp3_done = _level >= 3

        # ── TP 安全网 (利润上限): 未杠杆 PnL% > cap → 全平 ──
        # 从 v1 _TP_SAFETY_NET_BY_NATURE 复活为统一硬上限, 防极端单边爆利回吐。
        try:
            _pnl_pct_raw = float(profit_pct) if profit_pct is not None else self._position_pnl_pct(pos, _price)
        except Exception:
            _pnl_pct_raw = self._position_pnl_pct(pos, _price)
        if tp_cap > 0 and _pnl_pct_raw > tp_cap:
            logger.warning(
                f"[Paper][v2-Unified] TP安全网(利润上限)全平: {pos.symbol} {_side} "
                f"pnl%={_pnl_pct_raw:.1%} > cap={tp_cap:.0%}")
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason="tp_safety_net_cap",
                strategy_id=getattr(pos, "strategy_id", None),
            )
            return True

        # ── 分段止盈 (按 TP3→TP2→TP1 顺序检查, 单 tick 只触发最高一档, 防双触发) ──
        if (not _tp3_done) and _price_change_atr >= _params["tp3_mult"]:
            # TP3: 平 30%, 启动追踪止损 (peak - ATR价格距×trail_mult)
            _closed = self._partial_close_by_pct(db, pos, 0.30, "staged_tp3")
            if _closed and _closed.get("closed_fully"):
                return True
            pos.tp_level_reached = max(_level, 3)
            _new_sl = _peak_price - _atr_price * _params["trail_mult"]
            self._tighten_sl_unified(pos, _new_sl, "staged_tp3")
            logger.info(
                f"[Paper][v2-Unified] TP3 触发: {pos.symbol} {_side} "
                f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp3_mult']}, 平30%, 启动追踪 SL→{pos.sl_price}")
        elif (not _tp2_done) and _price_change_atr >= _params["tp2_mult"]:
            # TP2: 平 25%, SL → TP1 价 + ATR价格距×0.5
            _closed = self._partial_close_by_pct(db, pos, 0.25, "staged_tp2")
            if _closed and _closed.get("closed_fully"):
                return True
            pos.tp_level_reached = max(_level, 2)
            _tp1_price = _entry + _atr_price * _params["tp1_mult"] * _side_dir
            self._tighten_sl_unified(pos, _tp1_price + _atr_price * 0.5 * _side_dir, "staged_tp2")
            logger.info(
                f"[Paper][v2-Unified] TP2 触发: {pos.symbol} {_side} "
                f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp2_mult']}, 平25%")
        elif (not _tp1_done) and _price_change_atr >= _params["tp1_mult"]:
            # TP1: 平 25%, SL → entry + ATR价格距×0.8 (保本+给呼吸空间)
            # [2026-07-30 crypto-native] 0.3×ATR≈0.15% 太紧，加密5m正常波动0.5-1%轻松击穿
            # → breakeven_tp 100% 微利出场。提升到 0.8×ATR 给足够缓冲。
            _closed = self._partial_close_by_pct(db, pos, 0.25, "staged_tp1")
            if _closed and _closed.get("closed_fully"):
                return True
            pos.tp_level_reached = max(_level, 1)
            self._tighten_sl_unified(pos, _entry + _atr_price * 0.8 * _side_dir, "staged_tp1")
            logger.info(
                f"[Paper][v2-Unified] TP1 触发: {pos.symbol} {_side} "
                f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp1_mult']}, 平25%, SL→保本")

        # ── 利润回撤保护 (peak 回撤, 以 ATR 为单位) ──
        # 仅当已有浮盈峰值时计算 (peak_price 在盈利方向超过 entry)
        _peak_atr = ((_peak_price - _entry) / _entry) * _side_dir / _atr if _entry > 0 else 0.0
        if _peak_atr > 0 and _peak_price > 0:
            # 回撤 ATR 数 (正数=从峰值回吐). 方向无关: long 价跌/short 价涨都为正。
            _dd_atr = ((_price - _peak_price) / _entry) * (-_side_dir) / _atr
            if _dd_atr > _params["dd_hard"]:
                # 硬回撤 (>4×ATR): 任何阶段都全平 (防还利)
                logger.warning(
                    f"[Paper][v2-Unified] 利润硬回撤全平: {pos.symbol} {_side} "
                    f"peak={_peak_price:.6f} → price={_price:.6f}, dd={_dd_atr:.2f}ATR > {_params['dd_hard']}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="profit_drawdown_hard",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                return True
            if _tp1_done and _dd_atr > 2.0:
                # 软回撤 (>2×ATR 且 TP1 已触发): 全平锁利
                logger.warning(
                    f"[Paper][v2-Unified] 利润回撤(TP1后)全平: {pos.symbol} {_side} "
                    f"peak={_peak_price:.6f} → price={_price:.6f}, dd={_dd_atr:.2f}ATR > 2.0")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="profit_drawdown_stage",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                return True

        # ── 追踪止损 (TP3 后, 单调收紧) ──
        if _tp3_done and _peak_price > 0:
            _new_trail = _peak_price - _atr_price * _params["trail_mult"]
            self._tighten_sl_unified(pos, _new_trail, "unified_trailing")

        return False

    def _tighten_sl_unified(self, pos, new_sl: float, reason: str) -> None:
        """把 SL 向 entry 有利方向收紧 (long 取较大, short 取较小), 只收紧不放宽。

        与 _enforce_min_sl 配合: 此处先收紧, _enforce_min_sl 保证不会被压到
        小于最小距离。对 short, sl_price 从 None/0 视作 +inf, 任何有限值都算收紧。
        """
        try:
            _side = str(getattr(pos, "side", "long")).lower()
            _new = float(new_sl)
            if _new <= 0:
                return
            _cur = float(getattr(pos, "sl_price", 0) or 0)
            if _side in ("long", "buy"):
                if _new > _cur:
                    pos.sl_price = round(_new, 8)
            else:  # short
                if _cur <= 0 or _new < _cur:
                    pos.sl_price = round(_new, 8)
        except Exception as _e:
            logger.debug(f"[Paper][v2-Unified] SL收紧失败({reason}): {_e}")

    def _run_v2_protection(self, db, pos, entry, current_price, profit_pct, _nature):
        """v2 利润保护系统 — 基于 TP 进度的分层保护

        返回 True 表示持仓已平，调用方应 continue。
        """
        if self._enforce_max_hold_timeout(db, pos):
            return True

        from backend.database.models import PaperBalance

        manager = self._profit_manager
        if not manager:
            # [Phase E] _profit_manager 未初始化时回退到 v1 兜底。
            # 正常路径 manager 必然存在; 命中此分支说明初始化顺序异常, warn 以便定位。
            logger.warning(
                "[DEPRECATED Phase E] _run_v2_protection 在 _profit_manager 未初始化时"
                "回退到 _run_v1_protection — 检查引擎初始化顺序。"
            )
            return self._run_v1_protection(db, pos, entry, current_price, profit_pct, _nature)

        # 获取账户权益
        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == pos.account_id
        ).first()
        account_equity = float(bal.total_equity) if bal else 10000

        # 更新峰值利润
        pos_id = pos.id
        current_upnl = float(pos.unrealized_pnl or 0) + float(pos.partial_realized_pnl or 0)
        peak = self._sync_peak_state(pos, current_upnl, current_price)
        position_value = float(entry) * float(pos.size or 0)

        # 获取当前保护等级
        level_reached = int(getattr(pos, "tp_level_reached", 0) or 0)

        # ── Tier-aware 最短持仓保护 ──
        # F5-fix: 优先从 trade_nature 映射到正确的 tier，
        # 确保 profit_manager 和 DSM 使用与子仓位规则一致的保护参数
        _pos_nature = getattr(pos, 'trade_nature', None)
        if _pos_nature:
            from backend.services.sub_position_manager import NATURE_TO_TIER
            _pos_tier = NATURE_TO_TIER.get(_pos_nature, getattr(pos, 'timeframe_tier', None) or 'mid')
        else:
            _pos_tier = getattr(pos, 'timeframe_tier', None) or 'mid'
        _min_hold_ok = True
        try:
            from backend.config.settings import TIER_PROTECTION_PARAMS as _TPP
            _tier_cfg = _TPP.get(_pos_tier, _TPP["mid"])
            _min_hold_sec = _tier_cfg["min_hold_sec"]
            if pos.opened_at and _min_hold_sec > 0:
                from datetime import timezone as _tz
                opened = pos.opened_at
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=_tz.utc)
                elapsed_sec = (datetime.now(_tz.utc) - opened).total_seconds()
                if elapsed_sec < _min_hold_sec:
                    _min_hold_ok = False
        except Exception as _crit_err:
            logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
            try: db.rollback()
            except Exception: pass

        if _min_hold_ok:
            result = manager.get_protection_action(
                entry=float(entry),
                current=float(current_price),
                tp=float(pos.tp_price) if pos.tp_price else None,
                sl=float(pos.sl_price) if pos.sl_price else None,
                side=pos.side,
                size=float(pos.size),
                peak_profit=peak,
                level_reached=level_reached,
                account_equity=account_equity,
                margin=float(pos.margin or 0),
                tier=_pos_tier,
            )
        else:
            # 最短持仓未达标 → 跳过保护动作，仅保留爆仓/SL/TP
            result = None

        # ══════════════════════════════════════════════════════════════
        # D6: 盈利回撤保护 — 在此检查仓位是否从峰值利润大幅回撤
        #
        # 放在所有 SL/TP/liq 检查之前，确保盈利蒸发时主动干预，
        # 而非被动等 SL 命中（等 SL 命中时可能已经亏掉大部分利润）。
        #
        # 三级响应：
        #   L1 (tighten_sl):  回撤达阈值，收紧 SL 锁定剩余利润
        #   L2 (partial_close): 严重回撤，减仓 50% + 锁利
        #   L3 (full_close):   翻转为亏损，全平止损
        # ══════════════════════════════════════════════════════════════
        try:
            from backend.services.profit_drawdown_guard import get_profit_drawdown_guard
            _dd_guard = get_profit_drawdown_guard()
            _dd_action = _dd_guard.evaluate(
                symbol=pos.symbol,
                side=pos.side,
                nature=_nature,
                entry_price=float(entry),
                current_price=float(current_price),
                peak_profit=peak,
                current_upnl=current_upnl,
                current_sl=float(pos.sl_price) if pos.sl_price else None,
                position_size=float(pos.size),
                tier=_pos_tier,
            )
            if _dd_action:
                _dd_type = _dd_action["type"]
                # 深挖第 3 轮 (2026-05-08)：盈利回撤保护动作统一落盘
                try:
                    from backend.services.unified_risk_gate import record_guard_block
                    record_guard_block(
                        db, account_id=pos.account_id,
                        guard_name="profit_drawdown_guard",
                        symbol=pos.symbol, side=pos.side,
                        reason=_dd_action.get("reason", _dd_type),
                        extra={
                            "type": _dd_type,
                            "drawdown_ratio": _dd_action.get("drawdown_ratio"),
                            "threshold_used": _dd_action.get("threshold_used"),
                            "peak_profit": peak,
                            "current_upnl": current_upnl,
                            "new_sl": _dd_action.get("new_sl"),
                            "close_ratio": _dd_action.get("close_ratio"),
                        },
                    )
                except Exception as _crit_err:
                    logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass
                if _dd_type == "full_close":
                    # L3: 翻转为亏损 → 立即全平
                    logger.warning(
                        f"[Paper][D6] 盈利回撤-全平: {pos.symbol} {pos.side} "
                        f"peak=${peak:.2f} → upnl=${current_upnl:.2f} "
                        f"(dd={_dd_action['drawdown_ratio']:.0%}, thresh={_dd_action['threshold_used']:.0%})")
                    self.close_position(db, pos.account_id, pos.symbol, pos.side,
                                       reason="profit_drawdown_full")
                    self._peak_profit_cache.pop(pos_id, None)
                    return True
                elif _dd_type == "partial_close":
                    # 冷却期检查：同一仓位 15 分钟内不允许再次 partial_close，防止连锁触发
                    _last_pc = self._last_partial_close_at.get(pos_id)
                    _now_pc = datetime.now(tz=timezone.utc)
                    if _last_pc and (_now_pc - _last_pc).total_seconds() < 900:
                        logger.debug(
                            f"[Paper][D6] 跳过partial_close(冷却中): {pos.symbol} {pos.side} "
                            f"距上次{(_now_pc-_last_pc).total_seconds():.0f}s")
                        return False
                    # L2: 严重回撤 → 减仓 50% + 锁利 SL
                    _close_sz = float(pos.size) * 0.50
                    logger.warning(
                        f"[Paper][D6] 盈利回撤-减仓: {pos.symbol} {pos.side} "
                        f"peak=${peak:.2f} → upnl=${current_upnl:.2f}, "
                        f"平仓50%={_close_sz:.4f}锁利, "
                        f"(dd={_dd_action['drawdown_ratio']:.0%}, thresh={_dd_action['threshold_used']:.0%})")
                    self.close_position(
                        db, pos.account_id, pos.symbol, pos.side,
                        reason="profit_drawdown_partial",
                        quantity=_close_sz,
                        strategy_id=getattr(pos, 'strategy_id', None),
                    )
                    # 收紧剩余仓位的 SL（重新查询仓位，因为 close_position 创建了新对象）
                    from backend.database.models import PaperPosition as _PPD6
                    _remaining = db.query(_PPD6).filter(
                        _PPD6.id == pos_id,
                        _PPD6.status == "open",
                    ).first()
                    if _remaining and _dd_action.get("new_sl"):
                        _remaining.sl_price = _dd_action["new_sl"]
                        db.commit()
                        logger.info(
                            f"[Paper][D6] 剩余仓位 SL 收紧: {pos.symbol} {pos.side} "
                            f"size={_remaining.size} SL→{_remaining.sl_price}")
                    # 重置 peak 为当前剩余仓位的盈亏，防止连锁触发
                    _remaining_upnl = self._calc_unrealized_pnl(
                        float(pos.entry_price), float(current_price),
                        float(_remaining.size) if _remaining else 0, pos.side
                    ) if _remaining else 0.0
                    self._peak_profit_cache[pos_id] = _remaining_upnl
                    if _remaining:
                        _remaining.peak_unrealized_pnl = _remaining_upnl
                        _remaining.peak_pnl_pct = self._position_pnl_pct(_remaining, current_price)
                        if _dd_action.get("new_sl"):
                            _remaining.exit_state_json = (
                                f'{{"last_guard":"profit_drawdown_partial","new_sl":{float(_dd_action["new_sl"]):.8f}}}'
                            )
                        db.commit()
                    self._last_partial_close_at[pos_id] = datetime.now(tz=timezone.utc)
                    logger.info(
                        f"[Paper][D6] 部分平仓后重置peak: {pos.symbol} {pos.side} "
                        f"peak→${_remaining_upnl:.2f}, 冷却15min防连锁")
                    return True
                elif _dd_type == "profit_stage_close":
                    # D7: 主动分段止盈，按 guard 建议比例减仓并锁利
                    _ratio = float(_dd_action.get("close_ratio") or 0.60)
                    _ratio = max(0.0, min(1.0, _ratio))
                    if _ratio <= 0:
                        return False
                    _close_sz = float(pos.size) * _ratio
                    logger.warning(
                        f"[Paper][D7] 分段止盈-减仓: {pos.symbol} {pos.side} "
                        f"ratio={_ratio:.0%} size={_close_sz:.4f} reason={_dd_action.get('reason')}")
                    self.close_position(
                        db, pos.account_id, pos.symbol, pos.side,
                        reason="profit_stage_close",
                        quantity=_close_sz,
                        strategy_id=getattr(pos, 'strategy_id', None),
                    )
                    from backend.database.models import PaperPosition as _PPD7
                    _remaining = db.query(_PPD7).filter(
                        _PPD7.id == pos_id,
                        _PPD7.status == "open",
                    ).first()
                    if _remaining and _dd_action.get("new_sl"):
                        _remaining.sl_price = _dd_action["new_sl"]
                        _remaining.peak_unrealized_pnl = self._calc_unrealized_pnl(
                            float(_remaining.entry_price), float(current_price),
                            float(_remaining.size or 0), _remaining.side
                        )
                        _remaining.peak_pnl_pct = self._position_pnl_pct(_remaining, current_price)
                        db.commit()
                    self._last_partial_close_at[pos_id] = datetime.now(tz=timezone.utc)
                    return True
                elif _dd_type == "breakeven_sl":
                    # D7: 浮盈达标后将 SL 推到成本附近，避免从盈利仓变亏损仓
                    _new_sl = _dd_action.get("new_sl")
                    if _new_sl:
                        pos.sl_price = float(_new_sl)
                        logger.info(
                            f"[Paper][D7] 保本SL推进: {pos.symbol} {pos.side} "
                            f"SL→{pos.sl_price:.6f} reason={_dd_action.get('reason')}")
                        db.commit()
                    return False
                elif _dd_type == "tighten_sl":
                    # L1: 达到阈值 → 收紧 SL 锁定剩余利润
                    # 仅在最短持仓已过或利润显著时执行（避免刚开仓就锁死）
                    if _min_hold_ok or peak > position_value * 0.03:
                        if _dd_action.get("new_sl"):
                            pos.sl_price = _dd_action["new_sl"]
                            logger.info(
                                f"[Paper][D6] 盈利回撤-锁利: {pos.symbol} {pos.side} "
                                f"peak=${peak:.2f} → upnl=${current_upnl:.2f}, "
                                f"SL→{pos.sl_price:.6f} "
                                f"(dd={_dd_action['drawdown_ratio']:.0%}, thresh={_dd_action['threshold_used']:.0%})")
                    else:
                        logger.debug(
                            f"[Paper][D6] 盈利回撤-锁利跳过(持仓未达标): "
                            f"{pos.symbol} dd={_dd_action['drawdown_ratio']:.0%}")
        except Exception as _dd_err:
            logger.debug(f"[Paper][D6] 盈利回撤保护检查跳过: {_dd_err}")

        # ══════════════════════════════════════════════════════════════
        # 2026-04-22 爆仓事故修复：SL 必须先于 liq 检查
        #
        # 原先 v2 顺序是 "先 liq 再 SL"，这在高杠杆仓（20x short，sl 距 entry ~4.5%，
        # liq 距 entry ~4.5% 两者几乎贴脸）里会导致价格一穿 liq 就直接爆仓，
        # AI 设的 SL 形同虚设。
        # 修正：1) 先调用 _ensure_sl_inside_liq 把 sl 拉到 liq 内侧 ≥0.5% × entry；
        #       2) SL 检查前置，爆仓只作为最后兜底。
        # ══════════════════════════════════════════════════════════════

        # ── SL↔liq 安全边距保护 ──
        self._ensure_sl_inside_liq(pos)

        # ── SL 优先检查（AI 设的止损价必须被尊重）──
        if pos.sl_price:
            hit_sl = (pos.side == "long" and current_price <= pos.sl_price) or \
                     (pos.side == "short" and current_price >= pos.sl_price)
            if hit_sl:
                reason = "breakeven_sl" if profit_pct >= 0 else "sl"
                logger.info(
                    f"[Paper][v2] {'保本止损' if reason == 'breakeven_sl' else 'SL'}触发: "
                    f"{pos.symbol} {pos.side} @{current_price} SL={pos.sl_price}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason=reason,
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.sl_price),
                )
                self._peak_profit_cache.pop(pos_id, None)
                return True

        # ── 爆仓检查（最后兜底，理论上 _ensure_sl_inside_liq 后不应触发）──
        if pos.liquidation_price and pos.liquidation_price > 0:
            hit_liq = (pos.side == "long" and current_price <= pos.liquidation_price) or \
                      (pos.side == "short" and current_price >= pos.liquidation_price)
            if hit_liq:
                logger.warning(
                    f"[Paper] 爆仓! {pos.symbol} {pos.side} "
                    f"@{current_price} liq={pos.liquidation_price} "
                    f"(SL={pos.sl_price} 未先触发，可能是开仓时 liq 内就没有可用 SL 空间)")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="liquidation",
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.liquidation_price),
                )
                self._peak_profit_cache.pop(pos_id, None)
                return True

        # ── TP 直接检查 ──
        if pos.tp_price:
            hit_tp = (pos.side == "long" and current_price >= pos.tp_price) or \
                     (pos.side == "short" and current_price <= pos.tp_price)
            if hit_tp:
                logger.info(f"[Paper][v2] TP 触发: {pos.symbol} {pos.side} @{current_price} TP={pos.tp_price}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="tp",
                    strategy_id=getattr(pos, "strategy_id", None),
                    fill_price_override=float(pos.tp_price),
                )
                self._peak_profit_cache.pop(pos_id, None)
                return True

        # ── DynamicStopManager 运行时追踪止损调整（按 tier 分化 ATR 倍数） ──
        try:
            try:
                from backend.config.settings import RISK_USE_NATURE_EXIT_ORCHESTRATOR as _use_peo
            except Exception:
                _use_peo = False
            if not _use_peo:
                from backend.services.adaptive_executor.dynamic_sl_tp import get_stop_manager
                dsm = get_stop_manager()
                pid_str = str(pos_id)
                _entry_f = float(entry)
                _size_f = float(pos.size)
                _side = pos.side
                _profit_pct = (current_price - _entry_f) / _entry_f if _side == "long" else (_entry_f - current_price) / _entry_f
                _high = float(getattr(pos, 'highest_price', current_price) or current_price)
                _low = float(getattr(pos, 'lowest_price', current_price) or current_price)
                _atr = float(getattr(pos, 'atr_at_entry', 0) or 0)
                if _atr <= 0:
                    _atr = abs(current_price - _entry_f) * 0.02 or current_price * 0.01

                # long tier 优先使用 4h ATR（更平滑，防止噪声震出）
                _atr_for_trail = _atr
                if _pos_tier == "long":
                    try:
                        from backend.services.unified_data_pool import UnifiedDataPool
                        snap = UnifiedDataPool().get_snapshot(max_age=60)
                        if snap and pos.symbol in snap.indicators:
                            atr_4h = snap.indicators[pos.symbol].get("atr_4h", 0)
                            if atr_4h > 0:
                                _atr_for_trail = atr_4h
                    except Exception as _crit_err:
                        logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass

                trail_price, trail_type = dsm.calculate_trailing_stop(
                    pid_str, _entry_f, current_price, _atr_for_trail, _side,
                    _profit_pct, max(_high, current_price), min(_low, current_price),
                    tier=_pos_tier,
                )
                if trail_price > 0:
                    old_sl = float(pos.sl_price or 0)
                    better = (
                        (_side == "long" and trail_price > old_sl) or
                        (_side == "short" and (old_sl <= 0 or trail_price < old_sl))
                    )
                    if better:
                        pos.sl_price = round(trail_price, 6)
                        logger.debug(
                            f"[Paper][v2+DSM] 追踪止损更新: {pos.symbol} {_side} "
                            f"tier={_pos_tier} SL→{trail_price:.6f} (ATR trailing)")
        except Exception as _dsm_err:
            logger.debug(f"[Paper][v2] DynamicStopManager 追踪异常(非致命): {_dsm_err}")

        # ── 硬性 SL 最小距离保护（防止 DSM 压死 SL）──
        self._enforce_min_sl(pos, entry, _nature)

        # ════════════════════════════════════════════════════════════════════
        # Phase B+C: 统一分段止盈 + 利润回撤 + 追踪 + 止盈安全网 (ATR 自适应)
        #
        # 把原 v1 死代码(_TP_SAFETY_NET/_TRAILING/_BREAKEVEN) + PEO staged TP +
        # profit_drawdown 的部分职责, 收敛为单一 ATR 自适应块。所有阈值均为
        # ATR×mult (价格口径)。状态持久化到 pos.tp_level_reached (0/1/2/3) +
        # pos.peak_pnl_pct (峰值价格%), 跨 tick / 跨重启稳定。
        #
        # 触发顺序每 tick: 安全网(80%) → 分段 TP(TP3→TP2→TP1 防双触发) →
        # 利润回撤(硬4× / 软2×) → TP3 后追踪止损收紧。
        # 任一全平动作返回 True; 分段减仓/SL 收紧后继续走 profit_manager 兜底。
        # ════════════════════════════════════════════════════════════════════
        try:
            from backend.config.settings import (
                RISK_V2_UNIFIED_STAGED_TP as _v2_unified_on,
                RISK_V2_TP_SAFETY_NET_CAP as _tp_cap,
            )
        except Exception:
            _v2_unified_on, _tp_cap = True, 0.80

        if _v2_unified_on:
            _closed_by_unified = self._run_unified_staged_tp(
                db, pos, entry, current_price, profit_pct,
                atr_pct=None, tp_cap=float(_tp_cap),
            )
            if _closed_by_unified:
                self._peak_profit_cache.pop(pos_id, None)
                return True
            try:
                db.commit()
            except Exception:
                try: db.rollback()
                except Exception: pass

        # v3 简化：不再用趋势分析覆盖 profit_manager 的平仓决策
        # profit_manager 的 TP 进度保护已经足够，不需要额外的回调判定层

        # 执行保护动作（仅当 _min_hold_ok 且 result 不 None 时）
        if result is None or result.action == "none":
            return False

        if result.action == "breakeven":
            if result.sl_price is not None:
                old_sl = float(pos.sl_price or 0)
                if pos.side == "long" and result.sl_price > old_sl:
                    pos.sl_price = result.sl_price
                    pos.trailing_stop_price = None
                    logger.info(
                        f"[Paper][v2] 保本推进: {pos.symbol} {pos.side} "
                        f"SL→{result.sl_price:.6f}")
                elif pos.side == "short" and (old_sl == 0 or result.sl_price < old_sl):
                    pos.sl_price = result.sl_price
                    pos.trailing_stop_price = None
                    logger.info(
                        f"[Paper][v2] 保本推进: {pos.symbol} {pos.side} "
                        f"SL→{result.sl_price:.6f}")
            return False

        if result.action == "partial_close":
            logger.info(
                f"[Paper][v2] 分批锁利: {pos.symbol} {pos.side} "
                f"平仓{result.close_pct:.0%} reason={result.reason}")
            closed = self._partial_close_by_pct(db, pos, result.close_pct, result.reason)
            if closed and closed.get("closed_fully"):
                self._peak_profit_cache.pop(pos_id, None)
                return True  # 意外全平
            # 更新等级记录
            if hasattr(pos, "tp_level_reached"):
                pos.tp_level_reached = level_reached + 1
            # 更新 SL
            if result.sl_price:
                pos.sl_price = result.sl_price
            return False

        if result.action == "close":
            logger.info(
                f"[Paper][v2] 保护平仓: {pos.symbol} {pos.side} "
                f"reason={result.reason} @{current_price}")
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason=result.reason,
                strategy_id=getattr(pos, "strategy_id", None),
            )
            self._peak_profit_cache.pop(pos_id, None)
            # 安全网：确保再开仓冷却已记录（防 close_position 内部失败）
            try:
                from backend.services.reentry_cooldown import record_full_close
                from backend.services.sub_position_manager import NATURE_TO_TIER
                _nature = (getattr(pos, "trade_nature", None) or "").strip().lower()
                _safe_tier = (
                    getattr(pos, "timeframe_tier", None)
                    or NATURE_TO_TIER.get(_nature, "mid")
                    or "mid"
                )
                record_full_close(
                    pos.account_id, pos.symbol, pos.side, tier=_safe_tier,
                    close_pnl=float(pos.unrealized_pnl or 0),
                    close_reason=result.reason or "",
                )
            except Exception as _crit_err:
                logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass
            return True

        return False

    def _write_retrospective(self, db, account_id: int, pos, exit_price: float,
                             total_pnl: float, exit_reason: str):
        """D7: 写决策复盘 — 记录判断对错 + 提炼教训（写入 analytics 库）。"""
        from backend.database.models import DecisionRetrospective
        from backend.database.connection import AnalyticsSessionLocal, sqlite_write_commit

        entry_price = float(pos.entry_price or 0)
        if entry_price <= 0 or not exit_price:
            logger.warning(
                f"[Retrospective] 跳过复盘: {pos.symbol} entry_price={entry_price} "
                f"exit_price={exit_price} 数据不完整"
            )
            return

        ana_db = AnalyticsSessionLocal()
        
        pnl_pct = (exit_price - entry_price) / entry_price if pos.side == "long" \
                  else (entry_price - exit_price) / entry_price
        pnl_pct = round(pnl_pct * 100, 4)
        
        # 判断正确性
        # 2026-06-18: AI 主驾改造后，归因需区分"AI 方向判断错" vs "止损/执行系统导致"。
        # 在 lesson 里标注 exit_reason 类别，供后续 feedback 归因更精准。
        if total_pnl > 0:
            was_correct = "yes"
            mistake = None
            lesson = f"{pos.symbol} {pos.side}: 盈利+{total_pnl:.2f}({pnl_pct:+.2f}%). 决策正确, 继续保持此策略逻辑."
        elif exit_reason in ("stop_loss", "liquidation", "force_close", "trailing_stop"):
            was_correct = "no"
            mistake = f"开仓后触发{exit_reason}, 亏损{total_pnl:.2f}. 可能原因: 入场时机过早/止损过紧/方向判断错误."
            # 归因标注：止损出场时区分是硬监控触发（SL/liquidation）还是 AI 主动决策
            _attr = "[归因:止损触发" + ("(爆仓,杠杆/仓位可能过高)" if exit_reason == "liquidation" else "(SL/TP硬监控)") + "]"
            lesson = (
                f"{pos.symbol} {pos.side}: 被{exit_reason}出场, 亏损{total_pnl:.2f}({pnl_pct:.2f}%). "
                f"{_attr} 下次类似情况考虑: 等待确认信号再入场, 评估方向判断与止损距离是否合理."
            )
        elif total_pnl < 0:
            was_correct = "no"
            mistake = f"手动/AI平仓亏损{total_pnl:.2f}({pnl_pct:.2f}%). 判断错误或市场逆转."
            lesson = f"{pos.symbol} {pos.side}: 亏损{total_pnl:.2f}({pnl_pct:.2f}%). [归因:AI主动平仓] 检查方向判断是否有误."
        else:
            was_correct = "partial"
            mistake = "接近保本平仓"
            lesson = f"{pos.symbol} {pos.side}: 保本平仓, 未盈利未亏损. 考虑是否值得交易."

        # 持仓时长
        holding_minutes = 0
        if pos.closed_at and pos.opened_at:
            o = pos.opened_at
            c = pos.closed_at
            if o.tzinfo is None:
                from datetime import timezone as _tz
                o = o.replace(tzinfo=_tz.utc)
            if c.tzinfo is None:
                c = c.replace(tzinfo=_tz.utc)
            holding_minutes = max(0, int((c - o).total_seconds() / 60))

        retro = DecisionRetrospective(
            account_id=account_id,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=entry_price,
            exit_price=round(exit_price, 6),
            realized_pnl=round(total_pnl, 6),
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            was_correct=was_correct,
            mistake_analysis=mistake,
            lesson_learned=lesson,
            holding_minutes=holding_minutes,
            strategy_id=getattr(pos, "strategy_id", None),
            decision_snapshot=(
                f"tier={getattr(pos, 'timeframe_tier', '')} "
                f"nature={getattr(pos, 'trade_nature', '')} "
                f"lev={getattr(pos, 'leverage', '')}"
            ),
        )
        try:
            ana_db.add(retro)
            ana_db.flush()
            sqlite_write_commit(ana_db, label="decision_retrospective")
            logger.info(
                f"[Retrospective] {pos.symbol} {pos.side} {was_correct}: "
                f"pnl={total_pnl:+.2f} reason={exit_reason} → analytics"
            )
            # 同步到 StrategyMemory.key_lessons（反馈闭环）
            try:
                from backend.services.decision_feedback_service import decision_feedback_service
                decision_feedback_service.sync_lesson_to_strategy_memory(
                    db,
                    strategy_id=getattr(pos, "strategy_id", None),
                    symbol=pos.symbol,
                    lesson=lesson or "",
                    was_correct=was_correct or "partial",
                    exit_reason=exit_reason,
                    tier=getattr(pos, "timeframe_tier", "") or "",
                    trade_nature=getattr(pos, "trade_nature", "") or "",
                )
            except Exception as mem_err:
                logger.debug("[Retrospective] key_lessons sync skip: %s", mem_err)

            # QAA 3.1: 同步写入语义记忆/RAG，供后续决策前按 symbol/regime 检索。
            try:
                from backend.services.qaa_trade_memory_bridge import ingest_trade_lesson

                ingest_trade_lesson(
                    lesson=lesson or "",
                    symbol=pos.symbol,
                    side=pos.side,
                    pnl=float(total_pnl),
                    pnl_pct=float(pnl_pct),
                    exit_reason=exit_reason,
                    strategy_id=getattr(pos, "strategy_id", None) or "",
                    tier=getattr(pos, "timeframe_tier", "") or "",
                    trade_nature=getattr(pos, "trade_nature", "") or "",
                    source=f"retrospective:{getattr(retro, 'id', None) or pos.symbol}",
                    metadata={
                        "account_id": account_id,
                        "holding_minutes": holding_minutes,
                        "was_correct": was_correct,
                    },
                )
            except Exception as qaa_mem_err:
                logger.debug("[Retrospective] QAA RAG lesson sync skip: %s", qaa_mem_err)
        except Exception as write_err:
            try:
                ana_db.rollback()
            except Exception:
                pass
            logger.warning(f"[Retrospective] analytics 写入失败: {write_err}")
        finally:
            try:
                ana_db.close()
            except Exception:
                pass

        # D7: 因子→AI闭环反馈 — 交易结果反馈给因子衰减监控
        try:
            from backend.services.factor_engine.factor_decay_monitor import decay_monitor
            _ic = 0.05 if total_pnl > 0 else -0.05
            decay_monitor.record_ic(f"strategy_{pos.symbol}", _ic)
        except Exception as _crit_err:
            logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
            try: db.rollback()
            except Exception: pass

    def _notify_learning_on_close(
        self, db, pos, fill_price, pnl, reason,
        *, is_partial: bool = False, learning_weight: float = 1.0,
    ):
        """持仓关闭（全平/部分平）时通知统一学习系统"""
        from backend.services.unified_learning_service import unified_learning, TradeOutcome
        from backend.services.market_fingerprint import compute_fingerprint_from_live
        from backend.database.connection import SessionLocal

        strategy_id = getattr(pos, "strategy_id", None) or ""
        entry_price = float(pos.entry_price or 0)
        if entry_price <= 0:
            return
        exchange = "unknown"
        try:
            from backend.services.exchange_config import get_active_exchange
            exchange = get_active_exchange() or "unknown"
        except Exception:
            pass

        # 从策略获取关联的 prompt template_id
        template_id = ""
        if strategy_id:
            try:
                from backend.database.models import AIStrategy
                strat = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == strategy_id
                ).first()
                if strat and strat.master_prompt_template_id:
                    template_id = str(strat.master_prompt_template_id)
            except Exception as _crit_err:
                logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass

        full_size = float(pos.original_size or pos.size or 1)
        pnl_pct = pnl / (entry_price * full_size) if entry_price > 0 else 0
        peak_pnl = float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0)
        peak_pnl_pct = float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0)
        retention_ratio = (float(pnl) / peak_pnl) if peak_pnl > 0 else None

        duration = 0
        if pos.closed_at and pos.opened_at:
            o = pos.opened_at
            c = pos.closed_at
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            if c.tzinfo is None:
                c = c.replace(tzinfo=timezone.utc)
            duration = max(0, int((c - o).total_seconds()))
        elif pos.opened_at and is_partial:
            o = pos.opened_at
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            duration = max(0, int((datetime.now(timezone.utc) - o).total_seconds()))

        # 尝试计算市场指纹 + TrendState 增强 regime 标签
        regime = "ranging"
        fp_dict = None
        adx_at_entry = 0.0
        trend_direction = "neutral"
        trend_strength = "none"
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.database.connection import market_engine
            from sqlalchemy import inspect as sa_inspect

            if sa_inspect(market_engine).has_table("crypto_klines"):
                coordinator = StrategyCoordinator(db)
                import time as _time
                now_ts = int(_time.time())
                start_ts = now_ts - 30 * 86400
                klines = coordinator._query_klines(pos.symbol, "1h", start_ts, now_ts, exchange)
                if klines and len(klines) >= 60:
                    fp_data = {
                        "closes": [k["close"] for k in klines],
                        "highs": [k["high"] for k in klines],
                        "lows": [k["low"] for k in klines],
                        "volumes": [k["volume"] for k in klines],
                    }
                    fp = compute_fingerprint_from_live(fp_data)
                    regime = fp.regime
                    fp_dict = fp.to_dict()
            else:
                logger.debug("[PaperEngine] market 库无 crypto_klines，跳过 regime fingerprint")
        except Exception as _crit_err:
            try:
                db.rollback()
            except Exception:
                pass
            msg = str(_crit_err)
            if "crypto_klines table missing" in msg:
                logger.debug("[PaperEngine] crypto_klines 不可用，跳过 fingerprint: %s", msg)
            else:
                logger.warning("[PaperEngine] fingerprint 计算跳过: %s", msg)

        # TrendState 增强：优先用 UnifiedDataPool 快照中的 ADX/趋势数据
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            from backend.services.trend_classifier import classify_from_indicators, classify_market_environment
            snap = UnifiedDataPool().get_snapshot(max_age=120)
            if snap and pos.symbol in snap.indicators:
                ind = snap.indicators[pos.symbol]
                kl = snap.klines
                # 优先使用 1d TrendState 做市场环境分级
                ts_1d = classify_from_indicators(ind, kl, "1d", pos.symbol)
                ts_4h = classify_from_indicators(ind, kl, "4h", pos.symbol)
                adx_at_entry = ind.get("adx_4h", ind.get("adx", 0))
                trend_direction = ts_4h.direction
                trend_strength = ts_4h.strength
                # 用 TrendState 覆盖 regime（更精确的学习标签）
                env = classify_market_environment(ts_1d)
                if env == "strong_trend":
                    regime = f"strong_trend_{ts_1d.direction}"  # e.g. "strong_trend_up"
                elif env == "weak_trend":
                    regime = f"weak_trend_{ts_4h.direction}"
                elif env == "volatile":
                    regime = "volatile"
                else:
                    regime = "ranging"
        except Exception as _crit_err:
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug("[PaperEngine] TrendState 增强跳过: %s", _crit_err)

        _raw_tier = getattr(pos, "timeframe_tier", None) or "swing"
        tier = self._TIER_TO_NATURE.get(_raw_tier, _raw_tier)
        _pos_nature = getattr(pos, "trade_nature", None) or tier
        _pos_meta = {}
        try:
            import json as _json
            _pos_meta = _json.loads(getattr(pos, "metadata_json", None) or "{}")
            if not isinstance(_pos_meta, dict):
                _pos_meta = {}
        except Exception:
            _pos_meta = {}
        outcome = TradeOutcome(
            source="paper",
            strategy_id=strategy_id,
            template_id=template_id,
            symbol=pos.symbol,
            side=pos.side,
            tier=tier,
            trade_nature=_pos_nature,
            entry_price=entry_price,
            exit_price=float(fill_price),
            pnl=float(pnl),
            pnl_pct=pnl_pct,
            duration_seconds=duration,
            regime_at_entry=regime,
            regime_at_exit=regime,
            fingerprint_at_entry=fp_dict,
            confidence=0.6,
            position_size=float(pos.original_size or pos.size or 0),
            opened_at=pos.opened_at,
            peak_pnl_pct=peak_pnl_pct,
            exit_pnl_pct=pnl_pct,
            retention_ratio=retention_ratio,
            health_at_exit=getattr(pos, "health_score", None),
            reversal_level_at_exit="",
            exit_channel=reason,
            metadata={
                "close_reason": reason,
                "tier": tier,
                "adx_at_entry": round(adx_at_entry, 1),
                "trend_direction": trend_direction,
                "trend_strength": trend_strength,
                "leverage": float(pos.leverage or 1.0),
                "paper_position_id": getattr(pos, "id", None),
                "peak_pnl": peak_pnl,
                "peak_pnl_pct": peak_pnl_pct,
                "exit_pnl_pct": pnl_pct,
                "retention_ratio": retention_ratio,
                "health_score": getattr(pos, "health_score", None),
                "health_regime": getattr(pos, "health_regime", None),
                "paper_order_id": None,
                "closed_at": pos.closed_at.isoformat() if getattr(pos, "closed_at", None) else None,
                "exchange": exchange,
                "market_type": "perp",
                "data_source": "paper_trading_engine",
                "partial_close": is_partial,
                "learning_weight": float(learning_weight),
                **({k: _pos_meta[k] for k in (
                    "agent_envelope", "agent_source", "alignment_score",
                    "cited_fact_ids", "cited_facts",
                    "thesis_id", "memory_event_ids", "hub_adjusted_at_entry",
                    "open_readiness", "session_id",
                ) if k in _pos_meta}),
                **({
                    "thesis_id": (_pos_meta.get("agent_envelope") or {}).get("thesis_id"),
                    "memory_event_ids": (_pos_meta.get("agent_envelope") or {}).get("memory_event_ids"),
                    "hub_adjusted_at_entry": (_pos_meta.get("agent_envelope") or {}).get("hub_adjusted"),
                    "session_id": _pos_meta.get("session_id"),
                } if isinstance(_pos_meta.get("agent_envelope"), dict) and _pos_meta.get("agent_envelope").get("thesis_id") else {}),
            },
        )
        learning_db = SessionLocal()
        try:
            unified_learning.process_outcome(learning_db, outcome)
        finally:
            learning_db.close()

        # L2 收敛: process_outcome 内部已自动调度全部学习后端。
        # partial 平仓不触发计数型后端（review/miner）的逻辑已下沉到
        # ThresholdBackend.should_trigger 的 _is_partial_outcome 判断。

        # ── P1.6: 即时教训 v2 — 亏损后异步 LLM 深度复盘 ──
        _abs_pnl = abs(float(pnl))
        if _abs_pnl >= 50 and not is_partial:
            try:
                # 1. 触发反事实推理沙盒
                from backend.services.counterfactual_sandbox import counterfactual_sandbox
                _trade_ctx = {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "pnl": float(pnl),
                    "pnl_pct": pnl_pct,
                    "entry_price": entry_price,
                    "exit_price": float(fill_price),
                    "close_reason": reason,
                    "duration_min": duration // 60,
                    "strategy_id": strategy_id,
                    "regime": regime,
                    "timeframe": getattr(pos, "timeframe_tier", "15m") or "15m",
                }
                counterfactual_sandbox.enqueue(db, _trade_ctx, loss_threshold=50.0)
                logger.info(
                    f"[Paper] P1.6 反事实沙盒已入队: {pos.symbol} "
                    f"PnL=${pnl:+.2f}"
                )
            except Exception as _cf_err:
                logger.debug(f"[Paper] 反事实沙盒入队跳过: {_cf_err}")

            try:
                # 2. 触发 OpenCode 深度复盘（异步）
                from backend.services.trade_memory_context import (
                    _trigger_opencode_deep_review,
                )
                _trade_dict_for_review = {
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "pnl": float(pnl),
                    "pnl_pct": pnl_pct,
                    "entry_price": entry_price,
                    "exit_price": float(fill_price),
                    "close_reason": reason,
                    "duration_seconds": duration,
                    "strategy_id": strategy_id,
                    "regime": regime,
                }
                _ae = (_pos_meta.get("agent_envelope") or {}) if isinstance(_pos_meta.get("agent_envelope"), dict) else {}
                if _ae.get("thesis_id"):
                    _trade_dict_for_review["thesis_id"] = _ae.get("thesis_id")
                    _trade_dict_for_review["evidence_chain_snapshot"] = _ae.get("evidence_chain_snapshot") or []
                    _trade_dict_for_review["open_readiness_at_entry"] = _ae.get("open_readiness_at_entry")
                _trigger_opencode_deep_review(db, _trade_dict_for_review)
                logger.info(
                    f"[Paper] P1.6 OpenCode深度复盘已触发: {pos.symbol} "
                    f"PnL=${pnl:+.2f}"
                )
            except Exception as _odr_err:
                logger.debug(f"[Paper] OpenCode深度复盘触发跳过: {_odr_err}")

        # 反馈信号权重：更新 SignalTradeFeedback 中的 trade_pnl
        try:
            from backend.services.signal_feedback_tracker import signal_feedback_tracker
            _pos_id = getattr(pos, "id", None)
            if _pos_id:
                signal_feedback_tracker.update_trade_pnl(db, _pos_id, float(pnl), pnl_pct)
                logger.debug(f"[Paper] SignalFeedback PnL updated: pos_id={_pos_id} pnl={pnl:.2f}")

        except Exception as _sf_err:
            try:
                db.rollback()
            except Exception:
                pass
            logger.debug(f"[Paper] SignalFeedback PnL update skipped: {_sf_err}")

        # 回写 DecisionSnapshot 的交易结果（自反思经验库闭环）
        # 重要：DecisionSnapshot 是 AnalyticsBase 模型，在 PG 三库部署下位于
        # alpha_analytics 库，必须用 AnalyticsSessionLocal 独立会话。
        # 此前误用主库 db 会话 + inspector.has_table 检查，主库无此表 →
        # 静默 return，导致快照盈亏长期不回填、每周经验提炼恒跳过。
        _ana_db = None
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import DecisionSnapshot
            from datetime import timedelta

            _ana_db = AnalyticsSessionLocal()
            _snap = None
            _cutoff = datetime.now(timezone.utc) - timedelta(hours=48)  # 扩大到48小时，覆盖long tier持仓

            # 策略 1: 精确匹配 strategy_id + symbol + 时间窗口
            _strategy_id_for_match = strategy_id if strategy_id else ""
            if _strategy_id_for_match:
                _snap = _ana_db.query(DecisionSnapshot).filter(
                    DecisionSnapshot.strategy_id == _strategy_id_for_match,
                    DecisionSnapshot.symbol == pos.symbol,
                    DecisionSnapshot.pnl.is_(None),
                    DecisionSnapshot.timestamp >= _cutoff,
                ).order_by(DecisionSnapshot.timestamp.desc()).first()

            # 策略 2: 模糊回退 — 按 symbol + action 方向一致 + 时间窗口
            if not _snap:
                _snap = _ana_db.query(DecisionSnapshot).filter(
                    DecisionSnapshot.symbol == pos.symbol,
                    DecisionSnapshot.action.isnot(None),
                    DecisionSnapshot.pnl.is_(None),
                    DecisionSnapshot.timestamp >= _cutoff,
                ).order_by(DecisionSnapshot.timestamp.desc()).first()

            if _snap:
                _snap.exit_price = float(fill_price)
                _snap.pnl = float(pnl)
                _snap.pnl_pct = pnl_pct
                _snap.duration_seconds = duration
                _snap.entry_price = entry_price
                if pnl_pct > 0.005:
                    _snap.quality_label = "good"
                elif pnl_pct > -0.003:
                    _snap.quality_label = "neutral"
                else:
                    _snap.quality_label = "bad"
                _snap.lesson_extracted = (
                    f"{'盈利' if pnl > 0 else '亏损'}{abs(pnl):.1f}$ "
                    f"({pnl_pct*100:+.2f}%) "
                    f"reason={reason} regime={regime} "
                    f"持仓{duration//60}分钟"
                )
                _ana_db.commit()
                logger.info(f"[Paper] DecisionSnapshot 回写: {pos.symbol} pnl={pnl:+.2f} quality={_snap.quality_label}")
        except Exception as _snap_err:
            if _ana_db is not None:
                try:
                    _ana_db.rollback()
                except Exception:
                    pass
            logger.warning(f"[Paper] DecisionSnapshot 回写跳过: {_snap_err}", exc_info=True)
        finally:
            if _ana_db is not None:
                try:
                    _ana_db.close()
                except Exception:
                    pass

    # ── 查询 ──────────────────────────────────────

    def get_balance(self, db: Session, account_id: int) -> Optional[Dict]:
        from backend.database.models import PaperBalance, PaperPosition
        # 禁止 autoflush：get_balance 内部修改 pos.mark_price 会触发 flush，
        # 与并发线程（_paper_tick）冲突导致 InFailedSqlTransaction。
        with db.no_autoflush:
            bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
            if not bal:
                return None

            open_positions = db.query(PaperPosition).filter(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "open",
            ).all()
            exchange = self._resolve_account_exchange(db, account_id)
            if open_positions:
                total_unrealized = 0.0
                for pos in open_positions:
                    try:
                        live = self._get_current_price(pos.symbol, exchange)
                        if live and live > 0:
                            pos.mark_price = live
                            pos.unrealized_pnl = self._calc_unrealized_pnl(
                                pos.entry_price, live, pos.size, pos.side
                            )
                            total_unrealized += self._calc_unrealized_pnl(
                                pos.entry_price, live, pos.size, pos.side
                            )
                        else:
                            total_unrealized += float(pos.unrealized_pnl or 0)
                    except Exception:
                        total_unrealized += float(pos.unrealized_pnl or 0)
                bal.unrealized_pnl = total_unrealized
                bal.total_equity = bal.available_balance + bal.frozen_margin + total_unrealized

            return self._balance_to_dict(bal)

    def get_positions(self, db: Session, account_id: int, status: str = "open") -> List[Dict]:
        from backend.database.models import PaperPosition
        with db.no_autoflush:
            positions = db.query(PaperPosition).filter(
                PaperPosition.account_id == account_id,
                PaperPosition.status == status,
            ).all()
            exchange = self._resolve_account_exchange(db, account_id)

            if status == "open":
                for pos in positions:
                    try:
                        live_price = self._get_current_price(pos.symbol, exchange)
                        if live_price and live_price > 0:
                            pos.mark_price = live_price
                            pos.unrealized_pnl = self._calc_unrealized_pnl(
                                pos.entry_price, live_price, pos.size, pos.side
                            )
                    except Exception:
                        pass

            result = [self._position_to_dict(p) for p in positions]

        # ── 整改#9 Phase 2/3：C7 对拍 + 可选投影读 ──
        try:
            from backend.services.event_sourcing.phase3 import resolve_position_list_for_read
            result = resolve_position_list_for_read(
                result, account_id=account_id, status=status,
            )
        except Exception as _es_read_err:
            logger.debug("[EventSourcing#9] 读路径/对拍跳过: %s", _es_read_err)

        # ── 净额视角增强: 为每个仓位注入该币种的净头寸信息 ──
        # 让 AI 决策看到对冲后的真实敞口（scalp 空 + trend 多 的净额）
        netting_on = False
        try:
            from backend.config.settings import PAPER_NETTING_MODE
            netting_on = bool(PAPER_NETTING_MODE)
        except Exception:
            netting_on = True

        if netting_on and status == "open" and result:
            try:
                from backend.services.paper_netting import compute_net_position
                from collections import defaultdict
                # 按 symbol 分组，每币种只算一次净头寸
                symbols = defaultdict(list)
                for p in positions:
                    symbols[p.symbol].append(p)
                net_cache = {}
                for sym, rows in symbols.items():
                    net_cache[sym] = compute_net_position(
                        db, account_id, sym, MAINTENANCE_MARGIN_RATE,
                    )
                for d in result:
                    np_ = net_cache.get(d.get("symbol"))
                    if np_:
                        d["net_group_side"] = np_.net_side
                        d["net_group_size"] = round(np_.net_size, 8)
                        d["net_group_signed_size"] = round(np_.net_signed_size, 8)
                        d["net_group_margin"] = round(np_.net_margin, 2)
                        d["net_group_leverage"] = np_.unified_leverage
                        d["net_group_liq_price"] = round(np_.net_liquidation_price, 2)
            except Exception as _net_err:
                logger.warning(f"[PaperEngine] 净额视角增强异常（放行）: {_net_err}")

        return result

    def get_orders(self, db: Session, account_id: int, status: Optional[str] = None, limit: int = 50) -> List[Dict]:
        from backend.database.models import PaperOrder, PaperPosition
        q = db.query(PaperOrder).filter(PaperOrder.account_id == account_id)
        if status:
            q = q.filter(PaperOrder.status == status)
        orders = q.order_by(PaperOrder.id.desc()).limit(limit).all()
        positions = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id
        ).all()
        entry_fallback = self._build_entry_price_fallback(orders)
        result = []
        for o in orders:
            d = self._order_to_dict(o)
            if not d.get("entry_price"):
                d["entry_price"] = entry_fallback.get(o.id) or self._resolve_entry_from_positions(o, positions)
            result.append(d)
        return result

    @staticmethod
    def _position_side_from_close_order(side: str) -> str:
        return "long" if str(side).lower() == "sell" else "short"

    @staticmethod
    def _position_side_from_open_order(side: str) -> str:
        return "long" if str(side).lower() == "buy" else "short"

    def _resolve_entry_from_positions(self, order, positions) -> Optional[float]:
        if not getattr(order, "close_reason", None):
            return float(order.filled_price) if order.filled_price else None
        pos_side = self._position_side_from_close_order(order.side)
        filled = order.filled_at
        best = None
        best_opened = None
        for p in positions:
            if p.symbol != order.symbol or p.side != pos_side:
                continue
            if order.strategy_id and p.strategy_id and p.strategy_id != order.strategy_id:
                continue
            if not p.entry_price or not p.opened_at:
                continue
            opened = p.opened_at
            if opened.tzinfo is None:
                from datetime import timezone as _tz
                opened = opened.replace(tzinfo=_tz.utc)
            closed = p.closed_at
            if closed and closed.tzinfo is None:
                from datetime import timezone as _tz
                closed = closed.replace(tzinfo=_tz.utc)
            if filled:
                ft = filled
                if ft.tzinfo is None:
                    from datetime import timezone as _tz
                    ft = ft.replace(tzinfo=_tz.utc)
                if opened > ft:
                    continue
                if closed and closed < ft:
                    continue
            if best_opened is None or opened > best_opened:
                best = p
                best_opened = opened
        return float(best.entry_price) if best else None

    def _build_entry_price_fallback(self, orders) -> Dict[int, float]:
        """按时间顺序回放订单，为缺少 entry_price 的历史记录推断开仓价。"""
        partial_reasons = {
            "manual_partial", "partial_tp", "profit_drawdown_partial",
            "profit_stage_close", "master_running_reduce", "master_defensive_reduce",
            "defensive_reduce",
        }
        active: Dict[tuple, float] = {}
        resolved: Dict[int, float] = {}
        for o in sorted(orders, key=lambda x: x.id):
            if getattr(o, "entry_price", None):
                ep = float(o.entry_price)
                resolved[o.id] = ep
                if not o.close_reason and o.status == "filled":
                    key = (
                        o.symbol,
                        o.strategy_id or "",
                        self._position_side_from_open_order(o.side),
                    )
                    active[key] = ep
                elif o.close_reason and o.status == "filled" and o.close_reason not in partial_reasons:
                    key = (
                        o.symbol,
                        o.strategy_id or "",
                        self._position_side_from_close_order(o.side),
                    )
                    active.pop(key, None)
                continue
            if o.status != "filled":
                continue
            if not o.close_reason:
                key = (
                    o.symbol,
                    o.strategy_id or "",
                    self._position_side_from_open_order(o.side),
                )
                ep = float(o.filled_price or 0)
                if ep > 0:
                    active[key] = ep
                    resolved[o.id] = ep
            else:
                key = (
                    o.symbol,
                    o.strategy_id or "",
                    self._position_side_from_close_order(o.side),
                )
                if key in active:
                    resolved[o.id] = active[key]
                if o.close_reason not in partial_reasons:
                    active.pop(key, None)
        return resolved

    def get_summary(self, db: Session, account_id: int) -> Dict:
        """交易统计摘要 —— 基于持仓维度统计，包含已关闭和仍在亏损的持仓

        若账户有过重置（last_reset_at），只统计重置后的交易。
        """
        from backend.database.models import PaperBalance, PaperPosition, PaperOrder
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            return {}

        # ── 重置时间截断 ──
        reset_at = bal.last_reset_at

        # ── 已关闭持仓：每个持仓 = 一笔完整交易 ──
        _closed_q = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.status == "closed",
        )
        if reset_at:
            _closed_q = _closed_q.filter(PaperPosition.closed_at >= reset_at)
        closed_positions = _closed_q.all()

        # 为每个已关闭持仓计算总 PnL（直接从仓位数据推算，避免跨仓位订单匹配问题）
        position_pnls: list[float] = []
        for pos in closed_positions:
            if not pos.close_price or not pos.entry_price:
                continue
            remaining_sz = float(pos.size or 0)
            if remaining_sz < 1e-8 and not float(pos.partial_realized_pnl or 0):
                continue
            remaining_pnl = self._calc_unrealized_pnl(
                pos.entry_price, pos.close_price, remaining_sz, pos.side
            )
            full_pnl = remaining_pnl + float(pos.partial_realized_pnl or 0)
            position_pnls.append(full_pnl)

        # ── 当前持仓中正在亏损的也算入"败"，给出真实胜率 ──
        _open_q = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.status == "open",
        )
        if reset_at:
            _open_q = _open_q.filter(PaperPosition.opened_at >= reset_at)
        open_positions = _open_q.all()

        open_losing_pnls: list[float] = []
        open_winning_pnls: list[float] = []
        for pos in open_positions:
            upnl = float(pos.unrealized_pnl or 0)
            partial = float(pos.partial_realized_pnl or 0)
            total_so_far = upnl + partial
            if total_so_far < -0.01:
                open_losing_pnls.append(total_so_far)
            elif total_so_far > 0.01:
                open_winning_pnls.append(total_so_far)

        closed_wins = [p for p in position_pnls if p > 0.01]
        closed_losses = [p for p in position_pnls if p < -0.01]

        total_wins = len(closed_wins) + len(open_winning_pnls)
        total_losses = len(closed_losses) + len(open_losing_pnls)
        total_trades = total_wins + total_losses + len([p for p in position_pnls if abs(p) <= 0.01])

        all_profit_vals = closed_wins + open_winning_pnls
        all_loss_vals = closed_losses + open_losing_pnls

        realized_pnl = sum(position_pnls)
        gross_profit = sum(all_profit_vals) if all_profit_vals else 0
        gross_loss = abs(sum(all_loss_vals)) if all_loss_vals else 0

        _filled_q = db.query(PaperOrder).filter(
            PaperOrder.account_id == account_id,
            PaperOrder.status == "filled",
        )
        if reset_at:
            _filled_q = _filled_q.filter(PaperOrder.filled_at >= reset_at)
        filled_count = _filled_q.count()

        return {
            "total_orders": filled_count,
            "total_closes": len(closed_positions),
            # 前端 PaperSummary.total_trades 依赖此字段；缺省会导致整行统计 KPI 不渲染
            "total_trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": total_wins / max(total_wins + total_losses, 1),
            "total_pnl": round(realized_pnl + sum(open_losing_pnls) + sum(open_winning_pnls), 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / max(gross_loss, 0.01), 2),
            "total_fees": round(float(bal.total_fee_paid or 0), 2),
            "realized_pnl": round(float(bal.realized_pnl or 0), 2),
            "return_pct": round((bal.total_equity - bal.initial_balance) / max(bal.initial_balance, 1) * 100, 2),
            "max_drawdown_pct": round(
                (bal.initial_balance - min(bal.total_equity, bal.initial_balance)) / max(bal.initial_balance, 1) * 100, 2
            ),
            "open_losing": len(open_losing_pnls),
            "open_winning": len(open_winning_pnls),
            "last_reset_at": reset_at.isoformat() if reset_at else None,
        }

    # ── AI 动态 TP/SL / 延长持仓 ────────────────

    def extend_position_hold_hours(
        self,
        db: Session,
        position_id: int,
        additional_hours: float,
        *,
        reason: str = "ai_extend_hold",
    ) -> Optional[Dict[str, Any]]:
        """AI 延长持仓上限：更新 expected_hold_hours，清除超时复审队列。

        短线 scalp/intraday 禁止延长（硬超时强平，不走 Master 续命）。
        """
        from backend.database.models import PaperPosition
        from backend.services.position_hold_time import (
            get_position_hold_status,
            resolve_tier_absolute_cap_seconds,
            is_short_no_ai_hold_nature,
        )

        if additional_hours <= 0:
            return None

        pos = db.query(PaperPosition).filter(
            PaperPosition.id == position_id,
            PaperPosition.status == "open",
        ).first()
        if not pos:
            return None

        if is_short_no_ai_hold_nature(getattr(pos, "trade_nature", None)):
            logger.info(
                f"[Paper] 延长持仓拒绝 {pos.symbol}: 短线({pos.trade_nature})禁止AI延长"
            )
            return None

        before = get_position_hold_status(pos)
        before_max_h = float(before.get("max_hold_hours") or 0)
        abs_cap_h = resolve_tier_absolute_cap_seconds(pos) / 3600.0
        new_h = min(before_max_h + float(additional_hours), abs_cap_h)
        if new_h <= before_max_h + 0.01:
            logger.info(
                f"[Paper] 延长持仓跳过 {pos.symbol}: 已达上限 {abs_cap_h:.1f}h"
            )
            return None

        pos.expected_hold_hours = round(new_h, 2)
        db.commit()

        after = get_position_hold_status(pos)
        try:
            from backend.services.hold_timeout_review_queue import clear_position
            clear_position(pos.id)
        except Exception:
            pass

        logger.info(
            f"[Paper] AI延长持仓 {pos.symbol} {pos.side}: "
            f"{before_max_h:.1f}h → {new_h:.1f}h (+{additional_hours:.1f}h) | {reason}"
        )
        return {
            "position_id": pos.id,
            "symbol": pos.symbol,
            "before_max_hours": before_max_h,
            "after_max_hours": new_h,
            "added_hours": round(new_h - before_max_h, 2),
            "reason": reason,
            "hold_status": after,
        }

    def update_position_tp_sl(
        self, db: Session, position_id: int,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
    ) -> bool:
        """AI 主动调整指定持仓的 TP/SL 价位，返回是否成功"""
        from backend.database.models import PaperPosition

        pos = db.query(PaperPosition).filter(
            PaperPosition.id == position_id,
            PaperPosition.status == "open",
        ).first()
        if not pos:
            logger.warning(f"[Paper] update_tp_sl: 持仓 {position_id} 不存在或已关闭")
            return False

        changed = False
        if tp_price is not None:
            old_tp = pos.tp_price
            pos.tp_price = tp_price
            changed = True
            logger.info(f"[Paper] AI调整TP: {pos.symbol} {pos.side} "
                        f"TP {old_tp}→{tp_price}")
        if sl_price is not None:
            old_sl = pos.sl_price
            pos.sl_price = sl_price
            changed = True
            logger.info(f"[Paper] AI调整SL: {pos.symbol} {pos.side} "
                        f"SL {old_sl}→{sl_price}")

        if changed:
            self._sync_attached_orders(db, pos)
            db.commit()
        return changed

    # ── 定时更新（供 scheduler 调用）────────────────

    def update_single_position(self, db: Session, pos) -> None:
        """单持仓短事务更新 — 减少锁持有时间，避免连接池耗尽。

        scheduler 将每个持仓拆为独立 Session 调用此方法，
        而非旧版 update_all_positions 的一个大事务。
        """
        from backend.database.models import PaperBalance

        try:
            exchange = self._resolve_account_exchange(db, pos.account_id)
            current_price = self._get_current_price(pos.symbol, exchange)
        except RuntimeError:
            return

        pos.mark_price = current_price
        pos.unrealized_pnl = self._calc_unrealized_pnl(
            pos.entry_price, current_price, pos.size, pos.side
        )

        if self._apply_exchange_attached_orders(db, pos, current_price):
            db.commit()
            return

        # ── Research 模式：资金费率结算 ──
        self._maybe_settle_funding(db, pos, current_price)

        # 微小持仓清理：名义价值 < $5 直接全平
        notional = float(pos.size) * current_price
        if notional < MIN_POSITION_NOTIONAL and float(pos.size) > 0:
            logger.info(f"[Paper] 微仓清理: {pos.symbol} {pos.side} "
                        f"notional=${notional:.2f}<${MIN_POSITION_NOTIONAL}")
            self.close_position(db, pos.account_id, pos.symbol, pos.side, reason="dust_cleanup")
            self._tp_levels_cache.pop(pos.id, None)
            db.commit()
            return

        # ── 计算基础盈亏百分比 ──
        entry = float(pos.entry_price) if pos.entry_price and float(pos.entry_price) > 0 else 0
        profit_pct = 0.0
        if entry > 0:
            if pos.side == "long":
                profit_pct = (current_price - entry) / entry
            else:
                profit_pct = (entry - current_price) / entry

        # ── 获取 trade_nature（优先用新字段，兼容旧 tier 值）──
        _explicit_nature = getattr(pos, "trade_nature", None)
        if _explicit_nature and _explicit_nature in self._BREAKEVEN_BY_NATURE:
            _nature = _explicit_nature
        else:
            _raw_tier = getattr(pos, "timeframe_tier", None) or "swing"
            _nature = self._TIER_TO_NATURE.get(_raw_tier, _raw_tier)
            if _nature not in self._BREAKEVEN_BY_NATURE:
                _nature = "swing"
        _be_cfg = self._BREAKEVEN_BY_NATURE.get(_nature, self._BREAKEVEN_BY_NATURE["swing"])

        # ── 利润保护（v1/v2 分支）──
        from backend.config.settings import PROFIT_PROTECTION_VERSION
        if PROFIT_PROTECTION_VERSION == "v2":
            should_continue = self._run_v2_protection(db, pos, entry, current_price, profit_pct, _nature)
        else:
            # [Phase E] v1 路径在默认配置下已死 (PROFIT_PROTECTION_VERSION=v2)。
            # 仅当显式回退时才会命中,保留兜底但在每次激活时打 warning,
            # 便于监控是否还有意外路径触发 v1。Phase E 之后将彻底删除。
            logger.warning(
                "[DEPRECATED Phase E] _run_v1_protection 已弃用 "
                "(B+C 统一 TP/trailing)。建议设置 PROFIT_PROTECTION_VERSION=v2。"
            )
            should_continue = self._run_v1_protection(db, pos, entry, current_price, profit_pct, _nature)

        # 修复（2026-06-23）：_run_v1/v2_protection 内部异常路径会调 db.rollback()
        # （见 line ~2000/~2069），导致本函数开头设置的 pos.mark_price /
        # unrealized_pnl 被回滚（dirty=False），最终 commit 成为空操作 →
        # mark_price 永不更新，前端显示价格"不刷新"。
        # 修复：在 protection 之后、commit 之前重新赋值，确保 mark_price 一定落盘。
        pos.mark_price = current_price
        pos.unrealized_pnl = self._calc_unrealized_pnl(
            pos.entry_price, current_price, pos.size, pos.side
        )

        if should_continue:
            db.commit()
            return
        self._sync_attached_orders(db, pos)

        # 更新余额
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == pos.account_id).first()
        if bal:
            self._recalc_balance(db, bal)

        db.commit()

    def reprice_position(self, db: Session, pos) -> None:
        """秒级快速定价：只更新 mark_price/unrealized + 硬性 TP/SL 触发，
        不做波动率分类/追踪止盈等重保护逻辑（由慢速 full tick 负责）。
        """
        from backend.database.models import PaperBalance
        try:
            exchange = self._resolve_account_exchange(db, pos.account_id)
            current_price = self._get_current_price(pos.symbol, exchange)
        except RuntimeError:
            return

        pos.mark_price = current_price
        pos.unrealized_pnl = self._calc_unrealized_pnl(
            pos.entry_price, current_price, pos.size, pos.side
        )

        hit = False
        if pos.sl_price and float(pos.sl_price) > 0:
            hit = (pos.side == "long" and current_price <= float(pos.sl_price)) or \
                  (pos.side == "short" and current_price >= float(pos.sl_price))
            reason = "sl"
        if not hit and pos.tp_price and float(pos.tp_price) > 0:
            hit = (pos.side == "long" and current_price >= float(pos.tp_price)) or \
                  (pos.side == "short" and current_price <= float(pos.tp_price))
            reason = "tp"
        if hit:
            logger.info(
                f"[Paper][Fast] {reason.upper()} 触发: {pos.symbol} {pos.side} "
                f"@{current_price} {reason.upper()}={getattr(pos, reason + '_price', 0)}"
            )
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason=reason,
                strategy_id=getattr(pos, "strategy_id", None),
                fill_price_override=float(getattr(pos, reason + "_price")),
            )
            self._tp_levels_cache.pop(pos.id, None)
            self._peak_profit_cache.pop(pos.id, None)

        bal = db.query(PaperBalance).filter(
            PaperBalance.account_id == pos.account_id
        ).first()
        if bal:
            self._recalc_balance(db, bal)
        db.commit()

    @staticmethod
    def _write_trade_fact(
        *,
        account_id: int,
        position_id: str,
        symbol: str,
        tier: str,
        side: str,
        entry_price: float,
        exit_price: float,
        fees: float,
        pnl: float,
        outcome: str,
        close_reason: str,
    ) -> None:
        """M10 样本仓库：独立会话写 trade_facts（隔离失败不影响主事务）。"""
        try:
            from sqlalchemy import text as _sa_text
            from backend.database.connection import SessionLocal as _ArenaLocal
            with _ArenaLocal() as _db:
                _db.execute(_sa_text(
                    "CREATE TABLE IF NOT EXISTS trade_facts ("
                    " id BIGSERIAL PRIMARY KEY,"
                    " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                    " source VARCHAR(8) NOT NULL DEFAULT 'paper',"
                    " account_id INT NOT NULL,"
                    " position_id VARCHAR(64) NOT NULL,"
                    " symbol VARCHAR(32) NOT NULL,"
                    " tier VARCHAR(8) NOT NULL,"
                    " side VARCHAR(8) NOT NULL,"
                    " entry_price DOUBLE PRECISION,"
                    " exit_price DOUBLE PRECISION,"
                    " fees DOUBLE PRECISION,"
                    " pnl DOUBLE PRECISION,"
                    " outcome VARCHAR(16),"
                    " close_reason VARCHAR(64),"
                    " factor_exposures JSONB,"
                    " resonance JSONB)"
                ))
                _db.execute(_sa_text(
                    "INSERT INTO trade_facts "
                    "(source, account_id, position_id, symbol, tier, side, entry_price, "
                    " exit_price, fees, pnl, outcome, close_reason) "
                    "VALUES ('paper', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r)"
                ), {
                    "a": int(account_id), "p": position_id, "s": str(symbol).upper(),
                    "t": str(tier or "short"), "d": str(side or ""),
                    "e": float(entry_price or 0), "x": float(exit_price or 0),
                    "f": float(fees or 0), "pnl": float(pnl or 0),
                    "o": str(outcome or ""), "r": str(close_reason or ""),
                })
                _db.commit()
        except Exception as _tf_err:
            logger.debug(f"[Paper] trade_fact 落库失败: {_tf_err}")

    def update_all_positions(self, db: Session) -> None:
        """批量更新所有 open 持仓的 mark_price, 检查 TP/SL/爆仓"""
        from backend.database.models import PaperPosition, PaperBalance

        open_positions = db.query(PaperPosition).filter(PaperPosition.status == "open").all()
        if not open_positions:
            return

        account_ids_touched = set()

        for pos in open_positions:
            try:
                exchange = self._resolve_account_exchange(db, pos.account_id)
                current_price = self._get_current_price(pos.symbol, exchange)
            except RuntimeError:
                continue

            pos.mark_price = current_price
            pos.unrealized_pnl = self._calc_unrealized_pnl(
                pos.entry_price, current_price, pos.size, pos.side
            )
            account_ids_touched.add(pos.account_id)

            if self._apply_exchange_attached_orders(db, pos, current_price):
                continue

            # ── Research 模式：资金费率结算 ──
            self._maybe_settle_funding(db, pos, current_price)

            # 微小持仓清理：名义价值 < $5 直接全平
            notional = float(pos.size) * current_price
            if notional < MIN_POSITION_NOTIONAL and float(pos.size) > 0:
                logger.info(f"[Paper] 微仓清理: {pos.symbol} {pos.side} "
                            f"notional=${notional:.2f}<${MIN_POSITION_NOTIONAL}")
                self.close_position(db, pos.account_id, pos.symbol, pos.side, reason="dust_cleanup")
                self._tp_levels_cache.pop(pos.id, None)
                continue

            # ── 计算基础盈亏百分比 ──
            entry = float(pos.entry_price) if pos.entry_price and float(pos.entry_price) > 0 else 0
            profit_pct = 0.0
            if entry > 0:
                if pos.side == "long":
                    profit_pct = (current_price - entry) / entry
                else:
                    profit_pct = (entry - current_price) / entry

            # ── 获取 trade_nature（优先用新字段，兼容旧 tier 值）──
            _explicit_nature = getattr(pos, "trade_nature", None)
            if _explicit_nature and _explicit_nature in self._BREAKEVEN_BY_NATURE:
                _nature = _explicit_nature
            else:
                _raw_tier = getattr(pos, "timeframe_tier", None) or "swing"
                _nature = self._TIER_TO_NATURE.get(_raw_tier, _raw_tier)
                if _nature not in self._BREAKEVEN_BY_NATURE:
                    _nature = "swing"
            _be_cfg = self._BREAKEVEN_BY_NATURE.get(_nature, self._BREAKEVEN_BY_NATURE["swing"])

            # ── 利润保护（v1/v2 分支）──
            from backend.config.settings import PROFIT_PROTECTION_VERSION
            if PROFIT_PROTECTION_VERSION == "v2":
                should_continue = self._run_v2_protection(db, pos, entry, current_price, profit_pct, _nature)
            else:
                # [Phase E] v1 路径在默认配置下已死 (PROFIT_PROTECTION_VERSION=v2)。
                # 同 update_position 内的 dispatch, 保留兜底但每次激活 warn。
                logger.warning(
                    "[DEPRECATED Phase E] _run_v1_protection 已弃用 "
                    "(B+C 统一 TP/trailing)。建议设置 PROFIT_PROTECTION_VERSION=v2。"
                )
                should_continue = self._run_v1_protection(db, pos, entry, current_price, profit_pct, _nature)
            if should_continue:
                continue
            self._sync_attached_orders(db, pos)

        # 更新涉及的余额
        for aid in account_ids_touched:
            bal = db.query(PaperBalance).filter(PaperBalance.account_id == aid).first()
            if bal:
                self._recalc_balance(db, bal)

        db.commit()

        # ── 孤立缓存清理：删除已不存在于 open 仓位中的条目 ──
        self._prune_stale_caches(open_positions)

    def _prune_stale_caches(self, open_positions) -> None:
        """清理 _peak_profit_cache / _tp_levels_cache 中已不在 open 仓位集合的孤立条目。

        正常平仓流程会 pop 对应 key，但异常中断或外部删除仓位时条目会残留，
        导致内存缓慢增长。
        """
        alive_ids = {pos.id for pos in open_positions}
        stale_peak = [k for k in self._peak_profit_cache if k not in alive_ids]
        stale_tp = [k for k in self._tp_levels_cache if k not in alive_ids]
        if stale_peak:
            for k in stale_peak:
                self._peak_profit_cache.pop(k, None)
        if stale_tp:
            for k in stale_tp:
                self._tp_levels_cache.pop(k, None)
        if stale_peak or stale_tp:
            logger.debug(
                "[PaperEngine] 孤立缓存清理: peak=%d, tp=%d",
                len(stale_peak), len(stale_tp),
            )

    def _maybe_settle_funding(self, db: Session, pos, current_price: float) -> None:
        """Research 模式下按周期结算资金费率。仅 demo/research 有意义时调用。

        简化规则：
        - 每 FUNDING_SETTLE_INTERVAL_SEC 秒结算一次
        - funding_rate > 0 时多头付费给空头，反之亦然
        - payment = notional * rate，直接计入 PaperBalance.realized_pnl
        """
        from backend.config.settings import PAPER_SIMULATION_TIER, FUNDING_SETTLE_INTERVAL_SEC
        if PAPER_SIMULATION_TIER != "research":
            return

        from backend.database.models import PaperFundingLedger

        now = datetime.now(timezone.utc)
        # 上次结算时间：从 ledger 取最近一条
        last_settle = db.query(PaperFundingLedger).filter(
            PaperFundingLedger.account_id == pos.account_id,
            PaperFundingLedger.symbol == pos.symbol,
        ).order_by(PaperFundingLedger.id.desc()).first()

        if last_settle and last_settle.settled_at:
            # DB TIMESTAMP 无时区（naive UTC），统一补 tzinfo 再相减，
            # 否则抛 "can't subtract offset-naive and offset-aware"，
            # 异常会中断本持仓后续的保本/追踪/分批止盈保护
            _last_at = last_settle.settled_at
            if _last_at.tzinfo is None:
                _last_at = _last_at.replace(tzinfo=timezone.utc)
            elapsed = (now - _last_at).total_seconds()
            if elapsed < FUNDING_SETTLE_INTERVAL_SEC:
                return

        # 获取 funding rate
        funding_rate = self._get_funding_rate(pos.symbol)
        if funding_rate == 0.0:
            return

        notional = float(pos.size) * current_price
        # 多头支付正费率，空头收取；反之亦然
        if pos.side == "long":
            payment = -notional * funding_rate   # 正费率时多头付费
        else:
            payment = notional * funding_rate    # 正费率时空头收入

        # 写入 ledger
        entry = PaperFundingLedger(
            account_id=pos.account_id,
            position_id=pos.id,
            symbol=pos.symbol,
            side=pos.side,
            notional=notional,
            funding_rate=funding_rate,
            payment=payment,
            settled_at=now,
        )
        db.add(entry)

        # 更新余额
        from backend.database.models import PaperBalance
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == pos.account_id).first()
        if bal:
            bal.realized_pnl = float(bal.realized_pnl or 0) + payment
            bal.available_balance = float(bal.available_balance or 0) + payment

        logger.info(
            f"[Paper] Funding结算: {pos.symbol} {pos.side} "
            f"rate={funding_rate:.6f} notional={notional:.2f} "
            f"payment={payment:+.4f}"
        )

    @staticmethod
    def _get_funding_rate(symbol: str) -> float:
        """从 PerpFunding 表读取最新资金费率，缺失返回 0"""
        try:
            from backend.database.models import PerpFunding
            from sqlalchemy import desc
            from backend.database.connection import get_session_for
            db = get_session_for(PerpFunding)()
            try:
                row = db.query(PerpFunding).filter(
                    PerpFunding.symbol == symbol,
                ).order_by(desc(PerpFunding.timestamp)).first()
                if row and row.funding_rate is not None:
                    return float(row.funding_rate)
            finally:
                db.close()
        except Exception as _crit_err:
            logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
            try: db.rollback()
            except Exception: pass
        return 0.0

    def check_pending_orders(self, db: Session) -> None:
        """检查挂单是否触发"""
        from backend.database.models import PaperOrder, PaperBalance
        from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
        from backend.services.exchange.paper_exchange_simulator import (
            PaperMarketState,
            simulate_exchange_order,
        )

        pending = db.query(PaperOrder).filter(PaperOrder.status == "pending").all()
        for order in pending:
            try:
                exchange = self._resolve_order_exchange(db, order)
                current_price = self._get_current_price(order.symbol, exchange)
            except RuntimeError:
                continue

            order_type = str(order.order_type or "").lower()
            if order_type == "limit":
                sim_result = simulate_exchange_order(
                    exchange=exchange,
                    order=ExchangeOrder(
                        order_id=f"paper_{order.id}",
                        symbol=order.symbol,
                        side=OrderSide.BUY if order.side == "buy" else OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        size=float(order.quantity or 0),
                        price=float(order.price or 0),
                        leverage=int(round(float(order.leverage or 1))),
                    ),
                    market=PaperMarketState(
                        symbol=order.symbol,
                        mark_price=float(current_price),
                        bid=float(current_price),
                        ask=float(current_price),
                    ),
                    resting_limit=True,
                )
                if sim_result.status.value != "filled":
                    continue
            elif order_type == "stop_market" and order.price:
                if order.side == "buy" and current_price < order.price:
                    continue
                if order.side == "sell" and current_price > order.price:
                    continue
            else:
                continue

            bal = db.query(PaperBalance).filter(PaperBalance.account_id == order.account_id).first()
            if bal:
                self._fill_market_order(db, order, bal)

    # ── 杠杆统一 ──────────────────────────────────

    def _unify_leverage_for_side(
        self, db: Session, account_id: int, symbol: str, side: str, target_leverage: float
    ) -> None:
        """统一所有同币种仓位的杠杆。

        Hyperliquid/Asterdex One-Way 模式下，同币种只有一个 net position，
        杠杆必须跨方向统一（PAPER_NETTING_MODE=true）。
        - true: 按 (account, symbol) 跨方向统一（long/short 共享一个杠杆）
        - false: 仅按 (account, symbol, side) 同方向统一（旧行为）

        杠杆来自明确订单指令，不从保证金/名义价值反推。
        """
        from backend.database.models import PaperPosition as _PP

        netting_on = False
        try:
            from backend.config.settings import PAPER_NETTING_MODE
            netting_on = bool(PAPER_NETTING_MODE)
        except Exception:
            netting_on = True

        q = db.query(_PP).filter(
            _PP.account_id == account_id,
            _PP.symbol == symbol,
            _PP.status == "open",
        )
        if not netting_on:
            # 旧行为: 仅同方向统一
            q = q.filter(_PP.side == side)

        all_positions = q.all()
        if len(all_positions) <= 1:
            return

        try:
            _target_lev = max(1.0, float(target_leverage or 1.0))
        except Exception:
            return

        if netting_on:
            # 交易所同币一仓一杠杆：所有本地子仓同步到本笔订单杠杆（已在 trade_gate adopt）。
            # 不得再按各 tier cap 留不同杠杆。
            for p in all_positions:
                _lev_old = float(p.leverage or 1.0)
                if abs(_lev_old - _target_lev) < 0.01:
                    continue
                _notional = float(p.size) * float(p.entry_price)
                p.leverage = _target_lev
                p.margin = _notional / _target_lev if _target_lev > 0 else p.margin
                p.liquidation_price = self._calc_liquidation_price(
                    float(p.entry_price), p.side, _target_lev
                )
                logger.info(
                    f"[Paper] 杠杆统一(同币): {symbol}[{getattr(p, 'trade_nature', '')}|"
                    f"{getattr(p, 'timeframe_tier', '')}] {_lev_old}x → {_target_lev}x"
                )
        else:
            # 旧行为(netting_off): 仅同方向统一到 target,仍按需钳制。
            _unified_lev = _clamp_leverage_by_tier(_target_lev, None)
            for p in all_positions:
                if abs(float(p.leverage or 0) - _unified_lev) < 0.01:
                    continue
                _old_lev = p.leverage
                _notional = float(p.size) * float(p.entry_price)
                p.leverage = _unified_lev
                p.margin = _notional / _unified_lev if _unified_lev > 0 else p.margin
                p.liquidation_price = self._calc_liquidation_price(
                    float(p.entry_price), p.side, _unified_lev
                )
                logger.info(
                    f"[Paper] 杠杆同步(同方向): {symbol}[{getattr(p, 'trade_nature', '')}|"
                    f"{getattr(p, 'timeframe_tier', '')}] {_old_lev}x → {_unified_lev}x"
                )

    # ── 计算工具 ──────────────────────────────────

    @staticmethod
    def _calc_liquidation_price(entry_price: float, side: str, leverage: float) -> float:
        """估算爆仓价（简化版逐仓）"""
        if leverage <= 1:
            return 0.0
        mm = MAINTENANCE_MARGIN_RATE
        if side == "long":
            return entry_price * (1 - (1 / leverage) + mm)
        else:
            return entry_price * (1 + (1 / leverage) - mm)

    @staticmethod
    def _calc_unrealized_pnl(entry_price: float, current_price: float, size: float, side: str) -> float:
        if side == "long":
            return (current_price - entry_price) * size
        else:
            return (entry_price - current_price) * size

    def _get_or_create_balance(self, db: Session, account_id: int):
        """获取或创建模拟余额记录。

        2026-05-08 深挖第 4 轮 修复：
        - 默认值从 $100 调整为 $10,000，更贴近真实模拟交易需求
        - 优先从 accounts.initial_capital 读取（如有），否则用 PAPER_DEFAULT_BALANCE 环境变量
        - 加大 warning，提示这是异常路径（正常应通过 init_account 显式初始化）
        """
        from backend.database.models import PaperBalance, Account
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            import os, traceback
            default_balance = float(os.getenv("PAPER_DEFAULT_BALANCE", "10000"))
            try:
                acct = db.query(Account).filter(Account.id == account_id).first()
                if acct and acct.initial_capital:
                    initial_cap = float(acct.initial_capital)
                    if initial_cap > 0:
                        default_balance = initial_cap
            except Exception as _crit_err:
                logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
                try: db.rollback()
                except Exception: pass

            logger.warning(
                f"[Paper] ⚠️ 自动创建 PaperBalance account_id={account_id} initial=${default_balance:.2f}（异常路径，"
                f"正常应通过 /api/paper/init 显式初始化）调用栈:\n{''.join(traceback.format_stack()[-5:])}"
            )
            bal = PaperBalance(
                account_id=account_id,
                initial_balance=default_balance,
                total_equity=default_balance,
                available_balance=default_balance,
                frozen_margin=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                total_fee_paid=0.0,
            )
            db.add(bal)
            db.flush()
        return bal

    def _recalc_balance(self, db: Session, bal) -> None:
        """从持仓 + 订单历史完整重算余额，防止数据漂移。

        不变量: available = initial + realized_pnl - total_fee_paid - frozen_margin

        PnL 来源:
        - 所有订单的 pnl（分批止盈订单、全平订单都有真实值）
        - 旧数据中分批止盈订单 pnl=None → 其利润已在全平订单中合并
        - 所以直接 SUM(order.pnl) 就是完整的已实现 PnL

        保证金（One-Way 净额模式, PAPER_NETTING_MODE=true）:
        - 按每币种净头寸 (signed size 求和) 计算净保证金，对冲对释放保证金
        - 匹配 Hyperliquid/Asterdex 真实 One-Way 行为
        - false 时回退到旧行级 margin 求和
        """
        from backend.database.models import PaperPosition, PaperOrder
        from sqlalchemy import func

        # SessionLocal uses autoflush=False. Without this, balance queries may
        # still see a just-closed position as open inside the same transaction.
        db.flush()

        open_positions = db.query(PaperPosition).filter(
            PaperPosition.account_id == bal.account_id,
            PaperPosition.status == "open",
        ).all()

        # ── 保证金计算: 净额模式 vs 旧行级求和 ──
        total_upnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)

        netting_on = False
        try:
            from backend.config.settings import PAPER_NETTING_MODE
            netting_on = bool(PAPER_NETTING_MODE)
        except Exception:
            netting_on = True  # 默认开启

        if netting_on and open_positions:
            # 按币种分组聚合净头寸
            from backend.services.paper_netting import aggregate_rows_to_net
            from collections import defaultdict
            by_symbol = defaultdict(list)
            for p in open_positions:
                by_symbol[p.symbol].append(p)

            total_margin = 0.0
            row_margin_sum_total = 0.0
            hedge_release_total = 0.0
            for sym, rows in by_symbol.items():
                np_ = aggregate_rows_to_net(sym, rows, MAINTENANCE_MARGIN_RATE)
                total_margin += np_.net_margin
                row_margin_sum_total += np_.row_margin_sum
                hedge_release_total += np_.hedge_release

            # 审计日志: 对冲释放量 > 0 时打印（仅显著释放时，避免噪音）
            if hedge_release_total > 1.0:
                logger.info(
                    f"[Paper] 净额对冲释放: account={bal.account_id} "
                    f"row_sum={row_margin_sum_total:.2f} net={total_margin:.2f} "
                    f"release={hedge_release_total:.2f} symbols={len(by_symbol)}"
                )
        else:
            # 旧行级求和（PAPER_NETTING_MODE=false 或无仓位时）
            total_margin = sum(float(p.margin or 0) for p in open_positions)

        # 所有订单的已实现 PnL（新数据: 每个订单独立记录；旧数据: 全平订单含累计）
        order_rpnl = float(db.query(func.coalesce(func.sum(PaperOrder.pnl), 0)).filter(
            PaperOrder.account_id == bal.account_id,
            PaperOrder.pnl.isnot(None),
        ).scalar() or 0)

        # 所有订单手续费（开仓+平仓均有）
        order_fees = float(db.query(func.coalesce(func.sum(PaperOrder.fee), 0)).filter(
            PaperOrder.account_id == bal.account_id,
            PaperOrder.fee.isnot(None),
        ).scalar() or 0)

        bal.realized_pnl = order_rpnl
        bal.total_fee_paid = order_fees
        bal.frozen_margin = total_margin
        bal.unrealized_pnl = total_upnl
        bal.available_balance = bal.initial_balance + order_rpnl - order_fees - total_margin
        bal.total_equity = bal.available_balance + total_margin + total_upnl

    # ── 序列化 ────────────────────────────────────

    @staticmethod
    def _balance_to_dict(bal) -> Dict:
        return {
            "account_id": bal.account_id,
            "initial_balance": bal.initial_balance,
            "total_equity": round(bal.total_equity, 2),
            "available_balance": round(bal.available_balance, 2),
            "frozen_margin": round(bal.frozen_margin, 2),
            "unrealized_pnl": round(bal.unrealized_pnl, 2),
            "realized_pnl": round(bal.realized_pnl, 2),
            "total_fee_paid": round(bal.total_fee_paid, 2),
            "return_pct": round(
                (bal.total_equity - bal.initial_balance) / max(bal.initial_balance, 1) * 100, 2
            ),
            "last_reset_at": PaperTradingEngine._utc_iso(bal.last_reset_at),
            "updated_at": PaperTradingEngine._utc_iso(bal.updated_at),
        }

    @staticmethod
    def _position_to_dict(p) -> Dict:
        import json as _json
        pnl_pct = 0
        if p.entry_price and p.entry_price > 0 and p.size > 0:
            raw_pnl_pct = p.unrealized_pnl / (p.entry_price * p.size) * 100
            pnl_pct = round(raw_pnl_pct * p.leverage, 2)
        _exit_state = None
        try:
            _exit_state = _json.loads(getattr(p, "exit_state_json", None) or "null")
        except Exception:
            _exit_state = None

        _base = {
            "id": p.id,
            "account_id": p.account_id,
            "symbol": p.symbol,
            "side": p.side,
            "size": p.size,
            "entry_price": round(p.entry_price, 6),
            "mark_price": round(p.mark_price, 6),
            "leverage": p.leverage,
            "margin": round(p.margin, 2),
            "unrealized_pnl": round(p.unrealized_pnl, 2),
            "pnl_pct": pnl_pct,
            "liquidation_price": round(p.liquidation_price, 2),
            "tp_price": p.tp_price,
            "sl_price": p.sl_price,
            "trailing_stop_price": p.trailing_stop_price,
            "status": p.status,
            "close_reason": p.close_reason,
            "opened_at": PaperTradingEngine._utc_iso(p.opened_at),
            "closed_at": PaperTradingEngine._utc_iso(p.closed_at),
            "strategy_id": getattr(p, "strategy_id", None),
            "timeframe_tier": getattr(p, "timeframe_tier", None),
            "add_count": getattr(p, "add_count", 0) or 0,
            "dca_count": getattr(p, "dca_count", 0) or 0,
            "original_margin": round(getattr(p, "original_margin", 0) or 0, 2),
            "dca_total_added": round(getattr(p, "dca_total_added", 0) or 0, 2),
            "last_add_at": PaperTradingEngine._utc_iso(getattr(p, "last_add_at", None)),
            "trade_nature": getattr(p, "trade_nature", None),
            "expected_hold_hours": getattr(p, "expected_hold_hours", None),
            "peak_unrealized_pnl": round(float(getattr(p, "peak_unrealized_pnl", 0.0) or 0.0), 2),
            "peak_pnl_pct": round(float(getattr(p, "peak_pnl_pct", 0.0) or 0.0) * 100, 2),
            "health_score": getattr(p, "health_score", None),
            "health_regime": getattr(p, "health_regime", None),
            "exit_state": _exit_state,
            "reduce_count": getattr(p, "reduce_count", 0) or 0,
            "last_reduce_at": PaperTradingEngine._utc_iso(getattr(p, "last_reduce_at", None)),
        }
        try:
            from backend.services.position_hold_time import get_position_hold_status
            _hold_st = get_position_hold_status(p)
            _base.update({
                "hold_age_hours": _hold_st.get("hold_age_hours"),
                "max_hold_hours": _hold_st.get("max_hold_hours"),
                "hold_remaining_hours": _hold_st.get("hold_remaining_hours"),
                "hold_progress_pct": _hold_st.get("hold_progress_pct"),
                "hold_expired": _hold_st.get("hold_expired"),
                "hold_near_timeout": _hold_st.get("hold_near_timeout"),
                "hold_ai_extended": _hold_st.get("hold_ai_extended"),
                "hold_ai_reviewable": _hold_st.get("hold_ai_reviewable"),
                "review_hold_hours": _hold_st.get("review_hold_hours"),
                "absolute_cap_hours": _hold_st.get("absolute_cap_hours"),
                "extendable_hours": _hold_st.get("extendable_hours"),
                "extend_step_hours_min": _hold_st.get("extend_step_hours_min"),
                "extend_step_hours_max": _hold_st.get("extend_step_hours_max"),
            })
        except Exception as _crit_err:
            logger.error(f"[PaperEngine] 关键操作异常: {_crit_err}", exc_info=True)
            try: db.rollback()
            except Exception: pass
        return _base

    @staticmethod
    def _order_to_dict(o) -> Dict:
        return {
            "id": o.id,
            "account_id": o.account_id,
            "strategy_id": o.strategy_id,
            "exchange": getattr(o, "exchange", None),
            "symbol": o.symbol,
            "side": o.side,
            "order_type": o.order_type,
            "price": o.price,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "filled_price": o.filled_price,
            "entry_price": round(o.entry_price, 6) if getattr(o, "entry_price", None) else None,
            "leverage": o.leverage,
            "tp_price": o.tp_price,
            "sl_price": o.sl_price,
            "fee": round(o.fee, 4) if o.fee else 0,
            "pnl": round(o.pnl, 2) if o.pnl is not None else None,
            "trade_nature": getattr(o, "trade_nature", None) or "",
            "close_reason": getattr(o, "close_reason", None),
            "status": o.status,
            "created_at": PaperTradingEngine._utc_iso(o.created_at),
            "filled_at": PaperTradingEngine._utc_iso(o.filled_at),
        }


# 单例
paper_engine = PaperTradingEngine()
