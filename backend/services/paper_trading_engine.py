"""
Paper Trading Engine — 内置模拟交易引擎

使用真实市场价格 + 虚拟资金，完全本地执行，不依赖任何外部交易所 API。
支持：市价单/限价单、杠杆、保证金管理、止盈止损、爆仓检测、手续费模拟。
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

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


def _paper_default_exchange() -> str:
    """[2026-08-31] 引擎默认交易所 = 币安（.env DEFAULT_EXCHANGE=binance）。

    历史硬编码 asterdex 已退役；账户配置/active_exchange 都取不到时的最后一
    层回退统一走这里。PAPER_EXCHANGE_RULES_FALLBACK 可覆盖（与 paper 仿真
    规则表同一开关）。
    """
    import os as _os_px
    _v = str(_os_px.getenv("PAPER_EXCHANGE_RULES_FALLBACK", "binance") or "binance").strip().lower()
    return _v or "binance"

# tier→nature 唯一权威(阶段 C §2:消除本类与 position_memory_manager 双映射分歧)
# 模块级别名供 from-import 测试与跨模块一致性校验引用。
from backend.services.tp_sl_authority import TIER_TO_NATURE as _TIER_TO_NATURE  # noqa: E402

# 杠杆 tier cap —— 唯一权威已迁至 leverage_authority(阶段 C),此处仅委托。
from backend.services.leverage_authority import resolve_leverage as _resolve_lev_authority  # noqa: E402


def _clamp_leverage_by_tier(leverage: float, tier, symbol=None) -> float:
    """按 tier cap 钳制杠杆(委托单一权威 leverage_authority,阶段 C)。

    新单不被旧仓位 max 污染(根因 2 修复)。tier=None 时权威按最高 cap
    处理并 floor 到 1.0,与历史行为等价。

    [2026-09-07 杠杆根治 P2] 新增 symbol 参数：传入后由币种档一锤定音
    （requested 被忽略），与交易所 set_leverage(symbol, x) 语义对齐。

    Args:
        leverage: 目标杠杆
        tier: 仓位档位 ("short"/"mid"/"long") 或 None
        symbol: 币种（可选，传入则启用一币一档）

    Returns:
        钳制后的杠杆: 不低于 1,不高于该 tier 的 cap。
    """
    return _resolve_lev_authority(tier=tier, requested=leverage, symbol=symbol)


def _as_float_or_none(v) -> Optional[float]:
    """宽容取数：非数值（None / MagicMock / 字符串垃圾 / NaN）一律返回 None。

    用于**保护侧不变式**的市价取值：判定必须建立在真实可用的价格上，
    取不到时应放行（沿用既有语义），绝不能因为取数失败而阻断止损管理。
    放在模块级是为了让判定函数本身不依赖具体对象的属性形状。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN（float('nan') != 自身）
        return None
    return f


# 现价与开仓价的最大允许偏离：超过即认为取到的价不可信（含 Mock 被强转成 1.0 这类假数值）
_SANITY_MAX_DEV = 0.5


class PaperTradingEngine:
    """模拟交易引擎 — 单例，线程安全操作通过 DB session 隔离"""

    # ══════════════════════════════════════════════════════════════
    #  按 trade_nature 差异化的退出管理参数
    #  scalp → intraday → swing → position → trend_follow
    # ══════════════════════════════════════════════════════════════

    # tier → nature 唯一权威(阶段 C §2:消除与 position_memory_manager 的双映射分歧)
    # 绑定到模块级 _TIER_TO_NATURE(同对象),保留类属性访问 self._TIER_TO_NATURE。
    _TIER_TO_NATURE = _TIER_TO_NATURE

    # [Phase E 2026-08-30] v1 三张参数表已删除：
    #   _TP_SAFETY_NET_BY_NATURE / _BREAKEVEN_BY_NATURE / _TRAILING_BY_NATURE
    # 及兼容别名 _TP_SAFETY_NET/_BREAKEVEN_ACTIVATION/_BREAKEVEN_BUFFER/_TRAILING_BY_VOL。
    # 现行保护统一走 _run_v2_protection（profit_manager）+ 统一分段止盈块。
    _VALID_NATURES = frozenset({
        "scalp", "intraday", "swing", "position", "trend_follow",
    })

    # [2026-09-03 v3 F2c] trade_facts 写入失败累计计数（进程内），ops 巡检 / 边际账本读取。
    _TRADE_FACT_WRITE_FAILURES = 0

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
        # [PostFill P1-2] MAE 谷值缓存（与 peak 对称）：价格% 与美元口径
        self._trough_pct_cache: Dict[int, float] = {}
        self._trough_usd_cache: Dict[int, float] = {}
        self._last_partial_close_at: Dict[int, datetime] = {}  # pos_id -> last partial close time
        try:
            from backend.services.profit_protection_manager import profit_manager
            self._profit_manager = profit_manager
        except Exception:
            self._profit_manager = None

    @staticmethod
    def normalize_close_reason(reason: str, pnl: float) -> str:
        """按实际盈亏修正平仓标签，避免移动止损/保本止损盈利出场仍显示「止损」。

        ## ⚠️ 这张表是"止损"与"止盈"混在一起的历史原因（轮97 复核）

        `reason='sl'` 且 `pnl>0` ⇒ 改写成 `breakeven_tp`。也就是说
        **账本里的 `breakeven_tp` 其实是"锁利型止损成交"，不是"走到 TP 目标的止盈"**。
        实测 account 14 近 30 天：`breakeven_tp` 147 笔，147 笔的止损位都在成本**盈利侧**，
        PnL 合计 +533.74；同时段**没有任何一笔**通过 TP 目标止盈。

        本函数保留原语义（`edge_ledger` / `reentry_cooldown` / channel breaker 都按
        字符串匹配，改字符串=改行为）。要区分"锁利离场"与"保护离场"请读
        `stop_kind()` / `stop_vs_entry_pct()` 写入的 `stop_kind` 附加字段，
        或看日志里的 `[锁利型止损 …]` / `[保护型止损 …]` 标记。
        """
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
        # DB close_reason 扩至 VARCHAR(100)；仍截断防历史库未迁移
        return r[:100]

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
        """把峰值利润写入内存和 DB 字段，避免服务重启后保护状态丢失。

        [PostFill P1-2] 同时对称维护 MAE 谷值（trough_pnl_pct / trough_unrealized_pnl）：
        平仓后供遥测与因子进化闭环区分"止损太紧"(MAE 浅但被 SL 扫出) vs
        "方向错"(MAE 深)。谷值只下探不上抬，与 peak 只上推对称。
        """
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
        # ── MAE 谷值同步（失败不影响交易）──
        try:
            _cur_pct = self._position_pnl_pct(pos, current_price)
            trough_pct = min(
                float(self._trough_pct_cache.get(pos_id, 0.0) or 0.0),
                float(getattr(pos, "trough_pnl_pct", 0.0) or 0.0),
                _cur_pct,
            )
            trough_usd = min(
                float(self._trough_usd_cache.get(pos_id, 0.0) or 0.0),
                float(getattr(pos, "trough_unrealized_pnl", 0.0) or 0.0),
                float(current_upnl or 0.0),
            )
            if pos_id:
                self._trough_pct_cache[pos_id] = trough_pct
                self._trough_usd_cache[pos_id] = trough_usd
            pos.trough_pnl_pct = trough_pct
            pos.trough_unrealized_pnl = trough_usd
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

    @staticmethod
    def stop_kind(pos, fill_price: Optional[float] = None) -> str:
        """判断这次成交用的止损是**锁利型**还是**保护型**（轮97）。

        ## 为什么必须区分（用户 2026-09-18 提问："盈利单，你按照止损出了是怎么回事"）

        系统里只有**一条**止损线 `sl_price`，它同时承担两种角色：

        | 角色 | 位置 | 成交时 |
        |---|---|---|
        | 保护型止损 | 在成本**亏损侧** | 真亏 → `reason='sl'` |
        | 锁利型止损 | 在成本**盈利侧** | 回吐到该线 → `pnl>0` → 被 `normalize_close_reason` 改写成 `breakeven_tp` |

        于是账本上"止盈"与"止损"混为一谈。实测 account 14 近 30 天：
          · `breakeven_tp` **147 笔，147 笔全是锁利型**（PnL 合计 +533.74）；
          · `sl` 218 笔里还有 **82 笔**止损位在盈利侧（成交在亏损侧 = 滑点/跳空穿过锁利位）。
        也就是说**没有任何一笔是通过 TP 目标止盈的**，用户看到的"盈利单按止损出"是真实写照。

        `close_reason` 字符串**保持不变**（`edge_ledger` / `reentry_cooldown` /
        channel breaker 都按字符串匹配，改字符串会连带改行为）；
        本函数只产出**附加标记** `profit_lock` / `protective`，写进退出事件与日志，
        供人读与归因使用。

        返回 `""` 表示信息不足（无入场价/无止损）。
        """
        try:
            entry = float(getattr(pos, "entry_price", 0) or 0)
            sl = float(getattr(pos, "sl_price", 0) or 0)
            if entry <= 0 or sl <= 0:
                return ""
            side = str(getattr(pos, "side", "") or "").lower()
            if side in ("long", "buy"):
                return "profit_lock" if sl > entry else "protective"
            return "profit_lock" if sl < entry else "protective"
        except Exception:
            return ""

    @staticmethod
    def stop_vs_entry_pct(pos) -> Optional[float]:
        """止损位相对入场价的百分比（多头为正=盈利侧）；信息不足返回 None。"""
        try:
            entry = float(getattr(pos, "entry_price", 0) or 0)
            sl = float(getattr(pos, "sl_price", 0) or 0)
            if entry <= 0 or sl <= 0:
                return None
            side = str(getattr(pos, "side", "") or "").lower()
            sign = 1.0 if side in ("long", "buy") else -1.0
            return round((sl / entry - 1) * 100.0 * sign, 4)
        except Exception:
            return None

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

            # [2026-09-10 第二十八轮] 幂等护栏：同一仓位的 `final_trade_outcome` 只写一次。
            # 实测 #4638（9/9 23:21）在一次平仓中被两条并发通道各写一次、hard_line 再补登一次
            # → 3 个事件、且落库 reason 不是首个触发通道（sl 先触发、记录却成了
            # thesis_invalidation）。金额本身只扣一次（reduce_count=0、总 USD 只算一次），
            # 但归因/审计会读到错误通道，故在事件层加幂等。
            _etype_guard = str(event_type or "")[:40]
            if _etype_guard == "final_trade_outcome":
                _pid_guard = int(getattr(pos, "id", 0) or 0)
                if _pid_guard:
                    _dup = (
                        db.query(PositionExitEvent.id)
                        .filter(
                            PositionExitEvent.position_id == _pid_guard,
                            PositionExitEvent.event_type == "final_trade_outcome",
                        )
                        .first()
                    )
                    if _dup is not None:
                        logger.debug(
                            "[Paper] final_trade_outcome 幂等跳过 pos=%s reason=%s",
                            _pid_guard, exit_channel,
                        )
                        return

            peak_pnl = float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0)
            current_pnl = float(getattr(pos, "unrealized_pnl", 0.0) or 0.0)
            realized = float(pnl) if pnl is not None else current_pnl
            retention = None
            if peak_pnl > 0 and realized is not None:
                retention = max(-1.0, min(2.0, float(realized) / peak_pnl))

            # VARCHAR(40) 列：长 reason（如 mlto_invalidation 长文）会触发
            # StringDataRightTruncation，整笔平仓事务回滚 → BTC mid 管仓反复失败。
            _rev = (metadata or {}).get("reversal_level")
            _rev_s = (str(_rev)[:40] if _rev is not None else None)
            _ch_s = (str(exit_channel)[:80] if exit_channel is not None else None)
            _etype = str(event_type or "")[:40]
            # ── [轮97 修] 给"止损成交"打上锁利/保护标记 ──
            # 用户提问"盈利单，你按照止损出了是怎么回事"：系统只有一条 SL 线，
            # 它在成本之上时是**锁利型**（回吐到该线成交，pnl>0 → 被改写成
            # `breakeven_tp`），在成本之下时才是**保护型**。两者此前在事件流里
            # 完全无法区分（实测 30 天 147 笔 breakeven_tp 全是锁利型）。
            # 这里只加**附加字段**，不动 `exit_channel`/`close_reason` 语义，
            # 以免影响按字符串匹配的 edge_ledger / reentry_cooldown / breaker。
            _meta = dict(metadata or {})
            if _etype in ("final_trade_outcome", "partial_exit_event", "hard_line_close"):
                _sk = self.stop_kind(pos)
                if _sk:
                    _meta.setdefault("stop_kind", _sk)
                    _meta.setdefault("stop_vs_entry_pct", self.stop_vs_entry_pct(pos))
            event = PositionExitEvent(
                position_id=int(getattr(pos, "id", 0) or 0),
                account_id=int(getattr(pos, "account_id", 0) or 0),
                strategy_id=getattr(pos, "strategy_id", None),
                symbol=getattr(pos, "symbol", ""),
                side=getattr(pos, "side", ""),
                trade_nature=getattr(pos, "trade_nature", None),
                event_type=_etype,
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
                reversal_level=_rev_s,
                exit_channel=_ch_s,
                metadata_json=json.dumps(_meta, ensure_ascii=False),
            )
            db.add(event)
        except Exception as event_err:
            logger.debug(f"[Paper] 退出事件记录失败(非致命): {event_err}")

    def _record_hard_line_exit_source(self, db, pos, reason: str) -> None:
        """[PostFill §3.5 2026-08-31] 硬线直调全平补登 ExitSource 事件。

        硬线（SL/TP/爆仓/超时）绕过状态机直调 close_position，此前不留
        ExitSource 痕迹（枚举 STOP_LOSS/TAKE_PROFIT/LIQUIDATION/TIME_DECAY
        生产路径 0 构造）→ 事件流账实分离。失败不影响交易。
        """
        try:
            from backend.services.exit.exit_types import ExitSource
            _map = {
                "sl": ExitSource.STOP_LOSS.value,
                "tp": ExitSource.TAKE_PROFIT.value,
                "liquidation": ExitSource.LIQUIDATION.value,
                "max_hold_timeout": ExitSource.TIME_DECAY.value,
            }
            _src = _map.get(str(reason or "").lower())
            if not _src:
                return
            self._record_exit_event(
                db, pos,
                event_type="hard_line_close",
                exit_channel=reason,
                metadata={"exit_source": _src, "channel": "hard_line_direct"},
            )
        except Exception:
            pass

    def _record_postfill_telemetry(
        self, db, pos, kind: str, metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """[PostFill §3.7 2026-08-31] observe_only / 假设 PnL 影子遥测。

        无 A/B 直接全开后的补位机制：把"旧实现会怎么做"（counterfactual）与
        "被硬门拒绝的提案"落进 position_exit_events 事件流
        （event_type=postfill_telemetry_<kind>），供事后对比单档 vs 三档、
        延长 vs 拒绝的假设 PnL。只写事件，绝不影响执行路径；db 缺失时静默跳过。
        """
        try:
            if db is None or pos is None:
                return
            self._record_exit_event(
                db, pos,
                event_type=f"postfill_telemetry_{kind}"[:40],
                exit_channel="postfill_telemetry",
                metadata={"kind": kind, **(metadata or {})},
            )
        except Exception:
            pass

    @staticmethod
    def _enforce_min_sl(pos, entry: float, nature: str,
                        tier: Optional[str] = None) -> None:
        """确保 SL 距离不低于硬性最小值（防止被保本/追踪压得太紧）。

        2026-04-27 修复：保本止损已将 SL 推到盈利侧（long: SL>entry, short: SL<entry）
        时不应被本函数拉回亏损侧，否则保本保护形同虚设。

        [调研轮15b 2026-09-16] **与层上限冲突时上限赢**。此前 mid/swing 下限 4.5%、
        long/trend_follow 6.5% 是"无脑硬下限"，于是本函数在每个保护 tick 把上游三层
        封顶（提案层 clamp_stop_distance / 价格层 tp_sl_prices / PosMgr._calc_tp_sl）
        已经压到 2%~3% 的 SL **重新拉回 4.5%~6.5%** —— 实测线上 6 笔未平仓 SL 距离
        4.67~6.50% 全部等于该下限，是"止损远到不会响"（赢家 MAE 仅 1.2%，
        §87 审计：SL 中位 6.52% ⇒ sl 通道 0 笔）的**唯一存活原因**。
        上限（`MIDLONG_SL_MAX_PCT_<TIER>`，显式配置才生效）比下限更紧时以下限=上限
        收口；上限未配置（0）时行为与历史完全一致（回滚位）。
        """
        min_dist = PaperTradingEngine._MIN_SL_DISTANCE_BY_NATURE.get(nature, 0.025)
        # 震荡均值回归单（scalp_mr_ 前缀）用专用小止损下限，避免被 2.5% 硬拉宽而破坏
        # "小止损小止盈"的正期望结构。
        _is_mr = str(getattr(pos, "strategy_id", "") or "").startswith("scalp_mr_")
        if _is_mr:
            min_dist = PaperTradingEngine._MR_MIN_SL_DISTANCE
        # 层上限优先：只有当它比 nature 下限更紧时才覆盖（等价于"两者取更紧者"）。
        _tier = tier or getattr(pos, "timeframe_tier", None) or ""
        try:
            _cap = PaperTradingEngine.sl_max_pct_for_tier(_tier)
        except Exception:
            _cap = 0.0
        if _cap > 0 and min_dist > _cap:
            logger.info(
                "[Paper] SL 下限让位于层上限: tier=%s nature=%s floor %.2f%%→%.2f%%",
                _tier or "?", nature, min_dist * 100, _cap * 100,
            )
            min_dist = _cap
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
    # [§88 执行 2026-09-11 / 决策 P29-A] **SL 距离上限**（按层可配）
    #
    # 背景（§87 审计）：long 层 SL 距离中位 **6.52%**，而持仓期最大不利偏移（MAE）
    # 均值只有 **−1.41%**（约 SL 的 1/5）⇒ 30 天 34 笔里 `sl` 通道 **0 笔**，
    # 止损"远到不会响"，亏损只能由叙事通道在 −1.4%~−2% 处终止（单笔 −$19.22 = mid 的 6.3×）。
    # 因 ATR 下限（`apply_structure_atr_floor`：SL ≥ 1dATR×1.5）会把 SL 撑到 6%+，
    # 只在某一条路径改乘数覆盖不全 ⇒ 这里在**下单收口点**统一加"不得超过该层上限"的夹子。
    #
    # 键：`MIDLONG_SL_MAX_PCT_<TIER>` → `MIDLONG_SL_MAX_PCT` → 0（**默认关闭**）。
    # 只夹"过远"的一侧；过近由既有的 `_MIN_SL_DISTANCE_BY_NATURE` 负责。
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def sl_max_pct_for_tier(tier: str) -> float:
        import os as _os

        t = str(tier or "").strip().lower()
        for key in (f"MIDLONG_SL_MAX_PCT_{t.upper()}" if t else "", "MIDLONG_SL_MAX_PCT"):
            if not key:
                continue
            raw = _os.environ.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def safe_sl_price(sl_price, *, side: str, market: float, entry: float = 0.0) -> float:
        """保护侧不变式：多头 SL 必须低于现价、空头 SL 必须高于现价。

        ## 为什么必须有这道闸（2026-09-18 实盘事故）

        BNB 长线仓 #4715（entry=742.52）在**加仓后 0.55 秒**被平在 764.33，
        账面 +12.11。实际当时 BNB 只在 750–756 成交，764.33 是**从未存在的价格**。

        成因链：
          1. 峰值以 `peak_pnl_pct`（相对 entry 的百分比）持久化；
          2. 加仓把 entry 从 737.57 加权平均到 742.52，但 `peak_pnl_pct` 未换算，
             仍保留按旧 entry 算出的比例；
          3. 下一 tick 用 `entry × (1 + peak_pnl_pct)` 反推峰值价 → 得到**幽灵峰值**
             775.97（真实最高仅 759.98）；
          4. 保本推进处的「峰值追踪融合」取 `peak_px × (1-1.5%) = 764.33` 写入 SL；
          5. 该 SL 在现价 751.0 **之上**，`current_price <= sl_price` 恒真 → 立即触发；
          6. `close_position(fill_price_override=sl_price)` 用 SL 价而非市价成交 →
             **幽灵成交**，虚增 PnL。

        本函数在第 4 步拦截：任何会被写到现价错误一侧的 SL 一律拒绝（返回 0 表示不更新）。
        合法方向（多头 SL 上移 / 空头 SL 下移）完全不受影响。

        返回 0 的语义 = 「不要更新 SL」。**市价取不到或明显不可信时按原值放行**，
        以免因一次取价失败/坏值而放弃止损管理：

          - 非数值（None / 垃圾字符串 / NaN）→ 放行；
          - 与 entry 偏离超过 `_SANITY_MAX_DEV`（50%）→ 视为坏值，放行。
            这同时挡掉"MagicMock 之类对象被 float() 强转成 1.0"这种假数值。
        """
        s = _as_float_or_none(sl_price)
        if s is None or s <= 0:
            return 0.0
        m = _as_float_or_none(market)
        if m is None or m <= 0:
            return s          # 市价不可用 → 放行，不阻断既有逻辑
        e = _as_float_or_none(entry)
        if e is not None and e > 0 and abs(m - e) / e > _SANITY_MAX_DEV:
            # 现价与开仓价偏离过大（含被强转的假数值）→ 认为不可信，不做方向判定
            return s
        if str(side or "").lower() in ("long", "buy"):
            return s if s < m else 0.0
        return s if s > m else 0.0

    @staticmethod
    def safe_tp_price(tp_price, *, side: str, market: float, entry: float = 0.0) -> float:
        """**止盈侧不变式**：多头 TP 必须高于 entry（现价），空头 TP 必须低于 entry。

        ## 为什么必须有这道闸（2026-09-19 轮104 事故，`safe_sl_price` 的镜像）

        BTC 长线仓 #4712：滚仓 `buy 0.00165536 @80968.54` 成交后写入
        `tp=65689.43 / sl=80788.19`（**方向全反**：TP 在市价下方 19%、SL 几乎贴市价）。
        4 秒后秒级硬 TP 判定 `现价 80808 ≥ tp 65689` 立即成立 →
        `close_position(fill_price_override=min(tp, mkt)=65689.43)` →
        **按一个从未存在的价格"止盈平仓"**，并凭空记 −41.45 亏损。
        账户权益 4626.73 → 4574.66。

        根因在 `position_memory_manager._calc_tp_sl`（只认 `side=="buy"`，
        而加仓/补仓传的是 `"long"` → 走空头分支；已在本轮修正）。
        本函数是**收口层的第二道防线**：即使上游再算错方向，也写不进、触发不了。

        返回 0 的语义 = 「不要更新 TP」（与 `safe_sl_price` 的 0 语义一致）。
        市价/入场价不可信时按原值放行，避免因取价失败而放弃止盈管理。

        注：判据用 **entry**（多空的分界），不用现价 ——
        现价在触发那一刻本来就应当越过 TP，用现价会把**正常触发**误判成非法。
        """
        t = _as_float_or_none(tp_price)
        if t is None or t <= 0:
            return 0.0
        e = _as_float_or_none(entry)
        if e is None or e <= 0:
            # 没有入场价就没有多空分界，退回现价判定（多头 TP 在市价上方才合法）
            m = _as_float_or_none(market)
            if m is None or m <= 0:
                return t
            return t if (t > m if str(side or "").lower() in ("long", "buy") else t < m) else 0.0
        m = _as_float_or_none(market)
        if m is not None and m > 0 and abs(m - e) / e > _SANITY_MAX_DEV:
            return t          # 取价不可信 → 不做方向判定（与 safe_sl_price 同口径）
        if str(side or "").lower() in ("long", "buy"):
            return t if t > e else 0.0
        return t if t < e else 0.0

    def add_order_tp_decision(self, existing, order_tp, market: float = 0.0):
        """加仓/补仓合并时，订单的 TP 该不该写进持仓？→ `(tp值, action)`。

        action ∈ {"trend_lane_clear", "inverted_reject", "write", "keep"}。

        ## 为什么单独抽出来（2026-09-19 轮104）

        加仓合并路径（`place_order` 的 `existing.tp_price = order.tp_price`）
        直接赋值、绕过了 `update_position_tp_sl` 的两道闸，是 BTC #4712 的写入现场。
        抽成纯决策方法的目的与 `_should_run_unified_staged_tp` 相同：
          ① 判定与调用点分离，单测可直接断言（不必构造整个下单环境）；
          ② 让"长线车道没有固定 TP"这条契约只有**一处**定义。

        ## 两类处置

        1. **长线车道 → 清空**（`trend_lane_clear`）。
           修好方向之后 `_calc_tp_sl` 会给出 `new_avg × 1.1625`
           （trend_follow 的 tp_base = 6.5% × 2.5）—— 一个**可达**的固定止盈，
           会被 `_enforce_max_hold_timeout` 的 Layer-0 硬 TP 判定在 +16.25% 整仓平掉。
           这与车道自身的定义冲突：
             · `trend_e1_engine._adopt_position` 明写「趋势仓不设固定 TP
               （让利润奔跑；唯一出场 = 规则失效 / Chandelier）」并置 None；
             · 车道 `ExitPolicy.for_lane("long").tp_pct` 就是 **null**；
             · 长线车道的盈利引擎是**滚仓**，不是定点止盈。
        2. **方向非法 → 拒绝**（`inverted_reject`）：多头 TP 在开仓价下方
           （或空头在上方）一律不写，保留原值 —— 事故值 65689.43 vs 开仓 78492.80。
        """
        if existing is None:
            return None, "keep"
        try:
            _is_trend = bool(self._is_trend_lane_member(
                existing,
                getattr(existing, "timeframe_tier", None),
                getattr(existing, "trade_nature", None),
            ))
        except Exception:
            _is_trend = False
        if _is_trend:
            return None, "trend_lane_clear"
        _t = _as_float_or_none(order_tp)
        if _t is None or _t <= 0:
            return None, "keep"
        _safe = self.safe_tp_price(
            _t,
            side=str(getattr(existing, "side", "") or "").strip().lower(),
            market=market,
            entry=_as_float_or_none(getattr(existing, "entry_price", None)) or 0.0,
        )
        if _safe <= 0 and (_as_float_or_none(market) or 0.0) > 0:
            return None, "inverted_reject"
        return (_safe or _t), "write"

    def tp_direction_illegal(self, pos, tp_price, market: float = 0.0) -> bool:
        """[2026-09-19 轮104] **触发侧**第二道闸：TP 落在开仓价错误一侧 → 拒绝成交。

        写入侧（`update_position_tp_sl` / `evaluate_pyramid` 链路）已由 `safe_tp_price`
        把门，但存量仓位的历史脏 TP（例如 BTC #4712 那样在修复前写进去的）
        仍会躺在库里面；只要它还在，任何一次 tick 都可能拿它当成交价平仓。
        因此在**三个 TP 触发点**统一再判一次：方向非法 → 拒绝触发 + ERROR 留痕。

        信息不足（缺 entry / 缺 TP / 取价不可信）一律**不拦截** ——
        本闸只拦"确定反向"的 TP，不做任何推测性阻断。
        """
        _t = _as_float_or_none(tp_price)
        _e = _as_float_or_none(getattr(pos, "entry_price", None))
        if _t is None or _t <= 0 or _e is None or _e <= 0:
            return False
        _ok = self.safe_tp_price(
            _t, side=str(getattr(pos, "side", "") or ""), market=market, entry=_e)
        if _ok > 0:
            return False
        logger.error(
            "[Paper] TP 触发被止盈侧不变式拦截（方向非法，拒绝按该价成交）: "
            "%s %s TP=%.6f 开仓=%s 市价=%s —— 请检查该仓 TP 写入来源",
            getattr(pos, "symbol", "?"), getattr(pos, "side", "?"), _t, _e, market,
        )
        return True

    @staticmethod
    def rebase_peak_pct_for_entry(pos, old_entry: float, new_entry: float) -> None:
        """加仓导致均价变化时，把 `peak_pnl_pct` 按**同一峰值价格**换算到新均价。

        `peak_pnl_pct` 是 `峰值价/entry − 1` 的比例，只在 entry 不变时有意义。
        加仓抬高均价后若不同步换算，用新 entry 反推峰值会系统性**高估**峰值价
        （实测 775.97 vs 真实 759.98），进而把保本/追踪止损推到现价之上 ——
        即 #4715 事故的直接触发条件。

        保持「峰值价格」不变，只改比例口径：

            peak_new_pct = peak_old_pct + (old_entry − new_entry) / new_entry      # long
            peak_new_pct = peak_old_pct + (new_entry − old_entry) / new_entry      # short

        无历史峰值（pct ≤ 0）或参数非法时不动。
        """
        try:
            oe = float(old_entry or 0)
            ne = float(new_entry or 0)
            pct = float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0)
            if oe <= 0 or ne <= 0 or pct <= 0:
                return
            if abs(oe - ne) < 1e-12:
                return
            if str(getattr(pos, "side", "")).lower() in ("long", "buy"):
                peak_price = oe * (1.0 + pct)
                new_pct = peak_price / ne - 1.0
            else:
                peak_price = oe * (1.0 - pct)
                new_pct = 1.0 - peak_price / ne
            pos.peak_pnl_pct = float(max(0.0, new_pct))
            # 美元口径峰值同样按新仓量重算（旧峰值是在更小仓量下产生的）
            _peak_usd = float(getattr(pos, "peak_unrealized_pnl", 0.0) or 0.0)
            _size = float(getattr(pos, "size", 0.0) or 0.0)
            if _peak_usd > 0 and _size > 0:
                _px_delta = (peak_price - ne) if str(getattr(pos, "side", "")).lower() in ("long", "buy") \
                    else (ne - peak_price)
                pos.peak_unrealized_pnl = float(max(0.0, _px_delta * _size))
        except Exception as exc:
            logger.debug("[Paper] peak 换算失败(非致命): %s", exc)

    @staticmethod
    def clamp_sl_price(sl_price, *, side: str, entry: float, tier: str):
        """把**过远**的 SL 拉近到该层上限；过近不动、上限为 0 时完全不动。

        返回 `(sl_price, clamped: bool, detail: str)`，便于日志与测试断言。
        """
        try:
            cap = PaperTradingEngine.sl_max_pct_for_tier(tier)
            e = float(entry or 0)
            s = float(sl_price) if sl_price is not None else 0.0
            if cap <= 0 or s <= 0 or e <= 0:
                return sl_price, False, "off"
            if str(side or "").lower() in ("long", "buy"):
                limit = e * (1.0 - cap)
                if s < limit:
                    return round(limit, 8), True, f"long_sl_cap({cap:.2%})"
            else:
                limit = e * (1.0 + cap)
                if s > limit:
                    return round(limit, 8), True, f"short_sl_cap({cap:.2%})"
            return sl_price, False, "within_cap"
        except Exception:
            return sl_price, False, "error"

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
        # [2026-08-31] 历史硬编码 asterdex → 币安（当前主力交易所）
        fallback = get_active_exchange() or _paper_default_exchange()
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
            # [2026-08-31] 历史硬编码 asterdex → 币安（当前主力交易所）
            return _paper_default_exchange()

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
                exchange = get_active_exchange() or _paper_default_exchange()
            except Exception:
                exchange = _paper_default_exchange()
        exchange = exchange.strip().lower() or _paper_default_exchange()
        # 0) 数据中心唯一数据源（DC_ONLY）：秒级 ticker，1.5s TTL，最新鲜。
        #    必须优先于下方的 60s price_cache 兜底，否则持仓盯市价会滞后数十秒。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.data_center import data_center
                price = data_center.get_price(symbol, exchange)
                if price and price > 0:
                    return float(price)
        except Exception:
            pass
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
        # [2026-08-15 P0-4 修复] 原实现守卫 raise 被 except:pass 吞掉后仍会落入
        # 下方 ccxt 直连；现重构为 DC_ONLY 下失败直接抛错，绝不回退直连。
        from backend.services.market_data import _dc_only_enabled
        if _dc_only_enabled():
            from backend.services.data_center import data_center
            price = data_center.get_price(symbol, exchange)
            if price and price > 0:
                return float(price)
            raise RuntimeError(
                f"DC_ONLY 下数据中心无 {symbol} 价格（禁止 ccxt 直连兜底）"
            )
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

    @staticmethod
    def _mark_binance_enabled() -> bool:
        """盯市参考价开关：true 时 mark_price/unrealized 用 Binance 实时价（成交仍 Asterdex）。"""
        import os
        return os.getenv("MARK_PRICE_BINANCE_REFERENCE", "true").strip().lower() not in (
            "0", "false", "no", "off",
        )

    @staticmethod
    def _get_mark_price(symbol: str, exchange: Optional[str] = None) -> float:
        """盯市参考价（实时跳动）：Binance 实时价优先，缺失回退 Asterdex。

        与 _get_current_price 分离：后者是「成交价」（Asterdex），本方法是「盯市价」
        （Binance，用于 mark_price/unrealized/TP-SL 触发）。Binance 无该交易对时自动
        回退 Asterdex，不改变成交口径。
        """
        if PaperTradingEngine._mark_binance_enabled():
            try:
                from backend.services.data_center import data_center
                price = data_center.get_reference_price(symbol, exchange, "trade")
                if price and price > 0:
                    return float(price)
            except Exception:
                pass
        return PaperTradingEngine._get_current_price(symbol, exchange)

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

    def _set_tenant_from_account(self, db: Session, account_id: int) -> None:
        """[2026-08-22 M1-1] 把租户上下文设为该账户所有者的 user_id。

        背景：后台交易循环以 is_admin 穿透 RLS 写仓，tenant_id_var 为 None →
        自动填钩子落 DEFAULT 1 → 属主（RLS 按 user_id 过滤）看不到仓位。
        account.user_id 是行所有权的权威（0004 迁移：tenant_id=accounts.user_id），
        在交易引擎写点统一设置，保证新行落正确租户。
        """
        try:
            from backend.core.tenant import tenant_id_var
            from backend.database.models import Account as _A
            _acct = db.query(_A).filter(_A.id == account_id).first()
            if _acct is not None and _acct.user_id:
                tenant_id_var.set(int(_acct.user_id))
        except Exception as _tid_err:
            logger.debug(f"[PaperEngine] 设置租户上下文失败: {_tid_err}")

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
            # [2026-08-22 M1-1] 行所有权=账户 user_id：下单前设置租户上下文
            self._set_tenant_from_account(db, account_id)
            exchange = self._resolve_account_exchange(db, account_id)

            # ── [2026-09-10 第 12 轮] 组合闸**收口点**：mid/long 一律经组合风控（净敞口 + 并发上限）──
            # 背景：该闸此前只挂在 `midlong_helpers.try_execute_independent_agent_open` 一处，
            # 实测覆盖率 3/7 个入口——`trend_e1_engine`、`master_execution` 直下、
            # **加仓（midlong_position_manager）**、`paper_execution` 全部绕过（§48）。
            # 挂到本收口点后一次覆盖全部入口；与上游那次检查**幂等**（只读）。
            # 只对 mid/long 生效：scalp/short 返回 not_midlong，行为完全不变。
            try:
                from backend.services.mlto.midlong_portfolio_risk import (
                    choke_point_open_allowed as _choke_gate,
                )
                _ck_ok, _ck_why = _choke_gate(
                    db, account_id, symbol=symbol, action=side,
                    tier=timeframe_tier, trade_nature=trade_nature,
                    new_notional=float(quantity or 0) * float(price or 0),
                    # 探针识别：上游以 thesis 的 dir_src 判定，收口点只能按标识串启发式识别；
                    # 万一漏判，探针会面对**更严**的常规上限（保守方向，§48.5 已注明）。
                    is_probe=("probe" in f"{strategy_id or ''} {position_metadata or ''}".lower()),
                )
                if not _ck_ok:
                    logger.warning(
                        "[MidLongChokeGate] 拒单 %s %s: %s (acct=%s tier=%s nature=%s)",
                        symbol, side, _ck_why, account_id, timeframe_tier, trade_nature,
                    )
                    return None
            except Exception as _ck_err:
                logger.warning("[MidLongChokeGate] 检查异常(fail-open): %s", _ck_err)

            # ── [2026-09-05] 短线新开硬闸：纸盘+实盘都禁；已有仓只许平/减 ──
            if add_type not in ("reduce", "close"):
                try:
                    from backend.services.full_auto.scalp_open_gate import scalp_new_open_blocked
                    _sc_block, _sc_reason = scalp_new_open_blocked(
                        add_type, trade_nature, timeframe_tier,
                    )
                    if _sc_block:
                        logger.info(
                            "[Paper] 拒开短线 %s %s nature=%s tier=%s: %s",
                            symbol, side, trade_nature, timeframe_tier, _sc_reason,
                        )
                        return {
                            "success": False, "blocked": True, "blocked_layer": "scalp_open_gate",
                            "blocked_by": "scalp_open_disabled", "reason": _sc_reason,
                            "reason_code": "scalp_open_disabled",
                        }
                except Exception as _sc_err:
                    logger.warning("[Paper] 短线新开闸检查异常（拒开）: %s", _sc_err)
                    try:
                        from backend.config.settings import SCALP_OPEN_DISABLED as _sod
                    except Exception:
                        _sod = True
                    if _sod:
                        return {
                            "success": False, "blocked": True, "blocked_layer": "scalp_open_gate",
                            "blocked_by": "scalp_open_disabled", "reason": "scalp_open_gate_error",
                            "reason_code": "scalp_open_disabled",
                        }

            # ── [2026-09-03 v3 方向1] E1 独占长车道：非 E1 来源的 tier=long 新开仓只记为提议（TREND_E1_LONG_LANE_EXCLUSIVE）──
            if add_type not in ("reduce", "close"):
                try:
                    from backend.services.trend_e1_engine import long_lane_open_allowed as _e1_gate
                    _e1_ok, _e1_reason = _e1_gate(
                        timeframe_tier, trade_nature, position_metadata, add_type,
                        # [调研轮10] 传 symbol/session：AI 长线选币标的走窄口径例外
                        symbol=symbol,
                        session_id=str(getattr(session, "session_id", "") or "") or None,
                    )
                    if not _e1_ok:
                        logger.info(f"[Paper][TrendE1] 拒开 {symbol} {side} tier={timeframe_tier}: {_e1_reason}")
                        return {
                            "success": False, "blocked": True, "blocked_layer": "trend_e1_long_lane",
                            "blocked_by": "trend_e1", "reason": _e1_reason, "reason_code": "trend_e1_long_lane_exclusive",
                        }
                except Exception as _e1_err:
                    logger.debug(f"[Paper][TrendE1] 长车道独占检查异常（放行）: {_e1_err}")

            # ── [2026-09-03 v3 方向1] PositionConstruction 单一权威：所有车道的新开/加仓名义与杠杆在此夹紧 ──
            # 上游 8 条定仓轨（AI 百分比表 / ATR / 动态杠杆 / Kelly / SizingAgent / 短线名义 / 中长线 ATR 乘子 / 编排器）
            # 从此只是"提议"；硬帽：单币 ≤35% 权益、单笔风险 ≤ risk_per_trade、同簇 ≤50%、车道 gross、杠杆 ≤3x。
            # 平仓/减仓不经此处。异常放行（只记日志）。
            if add_type not in ("reduce", "close"):
                try:
                    from backend.services import position_construction as _pc
                    if _pc.enforce_enabled():
                        _pc_px = price if (price and price > 0) else self._get_current_price(symbol, exchange)
                        if _pc_px and _pc_px > 0 and quantity and quantity > 0:
                            _lane = _pc.normalize_lane(timeframe_tier, trade_nature)
                            _open = _pc.open_notionals(db, account_id, symbol, lane=_lane)
                            # [轮72 修 fail-open] 读不到在手敞口时**不得**当作「零敞口」放行：
                            # 那会让 clamp 把整额帽额叠加在真实仓位之上，等于绕过单一权威。
                            # （旧实现只在 open_notionals 内部记 DEBUG，调用方无从分辨。）
                            if not _open.get("ok", False):
                                logger.warning(
                                    f"[Paper][PositionConstruction] 拒单 {symbol} {side} lane={_lane}: "
                                    f"在手敞口读取失败，无法夹紧名义/杠杆（fail-closed）")
                                return {
                                    "success": False, "blocked": True,
                                    "blocked_layer": "position_construction",
                                    "blocked_by": "position_construction",
                                    "reason": "在手敞口读取失败，按 fail-closed 拒绝（避免未夹紧下单）",
                                    "reason_code": "position_construction_exposure_unreadable",
                                    "position_construction": {"lane": _lane, "open_notionals_ok": False},
                                }
                            _sd = None
                            if sl_price and sl_price > 0:
                                _sd = abs(float(_pc_px) - float(sl_price)) / float(_pc_px)
                            _cl = _pc.clamp(
                                lane=_lane, symbol=symbol, equity=float(bal.total_equity or 0), price=float(_pc_px),
                                notional=float(quantity) * float(_pc_px), leverage=float(leverage or 1.0),
                                stop_distance_pct=_sd, symbol_open_notional=_open["symbol"],
                                cluster_open_notional=_open["cluster"], lane_open_notional=_open["lane"],
                                is_add=bool(add_type),
                            )
                            if _cl.blocked:
                                logger.warning(
                                    f"[Paper][PositionConstruction] 拒单 {symbol} {side} lane={_lane}: {_cl.reason}")
                                return {
                                    "success": False, "blocked": True,
                                    "blocked_layer": "position_construction", "blocked_by": "position_construction",
                                    "reason": _cl.reason, "reason_code": "position_construction_room_zero",
                                    "position_construction": _cl.to_dict(),
                                }
                            if _cl.changed:
                                logger.info(
                                    f"[Paper][PositionConstruction] {symbol} {side} lane={_lane} "
                                    f"notional {float(quantity) * float(_pc_px):.2f}→{_cl.notional:.2f} "
                                    f"lev {leverage}→{_cl.leverage} caps={_cl.caps_applied}")
                                quantity = float(_cl.quantity)
                                leverage = float(_cl.leverage)
                                if position_metadata is None:
                                    position_metadata = {}
                                try:
                                    position_metadata["position_construction"] = {
                                        "caps": _cl.caps_applied, "lane": _lane, "notional": _cl.notional,
                                        "leverage": _cl.leverage,
                                    }
                                except Exception:
                                    pass
                except Exception as _pc_err:
                    logger.warning(f"[Paper][PositionConstruction] 检查异常（放行）: {_pc_err}")

            # ── [2026-09-03 v3 方向7] RiskEngine 单入口：急停 / TradingState / 连通性 / 避险窗口 / 日配额 ──
            # 开仓与加仓必经；平仓/减仓永远放行（引擎内部按 is_open 判定）。异常时按“已落盘状态”判定，
            # 引擎自身异常不阻断（fail-open 仅限引擎崩溃，状态判定本身不会因数据缺失而误放行）。
            try:
                from backend.services.risk.risk_engine import pre_trade as _v3_pre_trade, PreTradeRequest as _V3Req
                _v3_price = price if (price and price > 0) else self._get_current_price(symbol, exchange)
                _v3_verdict = _v3_pre_trade(
                    db,
                    _V3Req(
                        account_id=int(account_id), symbol=symbol, side=side,
                        is_open=(add_type not in ("reduce", "close")),
                        venue="paper", tier=timeframe_tier, trade_nature=trade_nature,
                        notional=float(quantity or 0) * float(_v3_price or 0),
                        equity=float(bal.total_equity or 0), leverage=float(leverage or 1.0),
                        source=str(strategy_id or ""),
                    ),
                )
                if not _v3_verdict.allowed:
                    logger.warning(
                        f"[Paper][RiskEngine v3] 拦截 {symbol} {side} qty={quantity} "
                        f"tier={timeframe_tier}/{trade_nature}: {_v3_verdict.reason}"
                    )
                    return _v3_verdict.as_block_result()
                # 黑天鹅剧本可临时把新开仓杠杆压到 1x（TradeGate 权威值之上再钳一层）
                if _v3_verdict.max_leverage and add_type not in ("reduce", "close"):
                    leverage = min(float(leverage or 1.0), float(_v3_verdict.max_leverage))
            except Exception as _v3_err:
                logger.warning(f"[Paper][RiskEngine v3] 检查异常（放行）: {_v3_err}")

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

            # ── [2026-09-03 v3 F2] 账户级日手续费预算门（不受锁强度配置影响）──
            # 只拦新开/加仓；平仓/减仓永远放行。口径与阈值见 ledger/fee_budget.py。
            if add_type not in ("reduce", "close"):
                try:
                    from backend.services.ledger.fee_budget import check_fee_budget as _fee_budget_check
                    _fb_px = price if (price and price > 0) else self._get_current_price(symbol, exchange)
                    _fb = _fee_budget_check(
                        db, account_id,
                        est_notional=float(quantity or 0) * float(_fb_px or 0),
                        equity=float(bal.total_equity or 0),
                        source="paper",
                    )
                    if not _fb.allowed:
                        logger.warning(
                            f"[Paper][FeeBudget] 拦截 {symbol} {side} qty={quantity}: {_fb.reason}"
                        )
                        return {
                            "success": False, "blocked": True,
                            "blocked_layer": "fee_budget", "blocked_by": "fee_budget",
                            "reason": _fb.reason, "reason_code": "fee_budget_exceeded",
                            "fee_budget": _fb.to_dict(),
                        }
                except Exception as _fb_err:
                    logger.debug(f"[Paper][FeeBudget] 检查异常（放行）: {_fb_err}")

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
            # [§88 执行 2026-09-11 / 决策 P29-A] 下单收口点统一夹住"过远的 SL"
            # （按层上限 `MIDLONG_SL_MAX_PCT_<TIER>`；默认 0 = 关闭 ⇒ 与既有行为一致）。
            _sl2, _sl_clamped, _sl_why = self.clamp_sl_price(
                sl_price, side=side, entry=price, tier=timeframe_tier or trade_nature)
            if _sl_clamped:
                logger.info(
                    "[Paper] SL 距离超上限被拉近 %s %s: %s → %s（%s）",
                    symbol, side, sl_price, _sl2, _sl_why,
                )
                sl_price = _sl2
                order.sl_price = _sl2
            if hasattr(order, "trade_nature"):
                order.trade_nature = trade_nature or "swing"
            # [2026-09-19 轮104] 下单收口点补齐**止盈侧**校验。
            # 上面只夹了 SL（clamp_sl_price），TP 侧此前完全没有不变式，
            # 反向 TP 可以一路写进订单 → 持仓 → 秒级硬 TP 判定按它成交（#4712）。
            # 方向非法的 TP 一律丢弃（**没有 TP 也好过反向 TP**：反向 TP 不是止盈，
            # 而是一条"按任意价立即成交"的指令）。
            if tp_price:
                _tp_ok = self.safe_tp_price(tp_price, side=side, market=price, entry=price)
                if _tp_ok <= 0:
                    logger.error(
                        "[Paper] 下单 TP 被止盈侧不变式拦截（丢弃该 TP）: %s %s "
                        "TP=%s 开仓价=%s —— 多头 TP 必须在开仓价上方、空头在下方",
                        symbol, side, tp_price, price,
                    )
                    tp_price = None
                    order.tp_price = None
                else:
                    order.tp_price = _tp_ok
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

        # [三周期持仓时间收敛 2026-08-13] research 车道落库保险丝：
        # 上游（pair_binding_lane / kline_research_lane）已传 timeframe_tier="research"，
        # 但历史生产版本曾以 mid 落库（112 笔研究仓污染中线统计/冷却/门控）。
        # 此处按 trade_nature 强制校正，任何版本/入口都不可能再把研究仓写入 mid。
        if (trade_nature or "").strip().lower() in ("research", "pair_research"):
            timeframe_tier = "research"

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

        # ── [2026-09-19 轮104 续] 止盈侧不变式（**取到真实市价之后**再判一次）──
        # 订单创建时那次校验用的是 `price`，而**市价单的 price 就是 None**
        # （DB 实证：滚仓单 23846 `price=None`，只有 filled_price）⇒ 那次校验对市价单是空转。
        # 这里在真实市价到手后复验，把"开仓即被反向 TP 秒平"这条历史出血口堵死：
        # 全库扫查发现 94 笔 `close_reason=tp` 却成交在**亏损侧**（合计 −895.88，
        # 中位持仓 26.5 秒，80/94 无加仓），全部来自各类入场路径直接带入的反向 TP。
        # 判据参考价：限价单用自身 `price`（耐心挂单的入场基准），市价单用 `current_price`。
        _ref_entry = float(order.price) if (order.price and float(order.price) > 0) else float(current_price or 0)
        if order.tp_price:
            # [轮117 2026-09-19 修 **致命 NameError**] 本函数签名是
            # `(self, db, order, bal, timeframe_tier, add_type, trade_nature, …)` ——
            # **没有 `side` / `symbol` 这两个参数**。轮104 续加的这段复验写成了
            # `side=str(side or "")` 与 `symbol` ⇒ 只要 `order.tp_price` 非空就抛
            # `NameError: name 'side' is not defined` ⇒ `place_order` 返回 False ⇒
            # `[FullAuto] 模拟交易执行异常` ⇒ 审计里统一落成 `paper_trade_false`。
            # 实测（16:49:46）：**中线每一单都带 TP ⇒ 全军覆没**；长线 trend_e1 的
            # 订单 TP 为空 ⇒ 恰好绕过（所以"只有中线被冻结，长线正常"）。
            # 时间线也吻合：轮104 续 02:44 提交，中线最后一笔成交停在 02:28。
            _side = str(getattr(order, "side", "") or "")
            _sym = str(getattr(order, "symbol", "") or "")
            _tp_choke = self.safe_tp_price(
                order.tp_price, side=_side, market=float(current_price or 0),
                entry=_ref_entry)
            if _tp_choke <= 0 and float(current_price or 0) > 0:
                logger.error(
                    "[Paper] 下单 TP 被止盈侧不变式拦截（丢弃该 TP）: %s %s TP=%s "
                    "参考入场=%s 市价=%s —— 多头 TP 必须在开仓价上方、空头在下方",
                    _sym, _side, order.tp_price, _ref_entry, current_price,
                )
                order.tp_price = None
                tp_price = None      # 入参副本同步（位置构造随后改用 order.tp_price）
            else:
                order.tp_price = _tp_choke or order.tp_price

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
        # [P2-4] 新限价单不再强制 maker（模拟器按盘口穿越判定 taker/maker）；
        # resting_limit 仅用于挂单后触发路径（check_pending_orders 传 True）。
        force_maker = False

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
            _old_entry = float(existing.entry_price or 0)
            existing.entry_price = (
                (existing.entry_price * existing.size + fill_price * order.quantity) / total_size
            )
            existing.size = total_size
            # 杠杆是交易指令，不从保证金反推。加仓/补仓后直接沿用本次订单杠杆，
            # 保证金按新的总名义价值 ÷ 订单杠杆重算。
            # [根因 2 修复] add/DCA 不用 max 提杠杆,按目标仓位 tier cap 钳制,
            # 避免新订单把既有低杠杆仓位强制提到高杠杆。
            # [2026-09-07 杠杆根治 P2] 传 symbol：一币一档，与交易所同币单杠杆对齐。
            existing.leverage = _clamp_leverage_by_tier(
                float(order.leverage or existing.leverage or 1.0),
                getattr(existing, "timeframe_tier", None),
                symbol=getattr(existing, "symbol", None),
            )
            existing.margin = (existing.size * existing.entry_price) / existing.leverage
            existing.mark_price = current_price
            existing.liquidation_price = self._calc_liquidation_price(
                existing.entry_price, existing.side, existing.leverage
            )
            # ── [2026-09-19 轮104] 加仓/补仓合并时的 TP/SL 双向不变式 ──
            # 这是 BTC #4712 事故的**写入现场**：`position_memory_manager._calc_tp_sl`
            # 因为只认 `side=="buy"` 而加仓传的是 `"long"`，走空头分支算出
            # tp=65689（在市价下方 19%）、sl=80788（贴着市价）。
            # 这里直接赋值、绕过了 `update_position_tp_sl` 的两道闸，
            # 于是脏 TP 落库，4 秒后被秒级硬 TP 判定按它成交并虚记 −41.45。
            # 现在在**入口**收口：方向非法的值一律不写，保留原值并留 ERROR。
            _side_add = str(getattr(existing, "side", "") or "").strip().lower()
            _entry_add = float(getattr(existing, "entry_price", 0) or 0)
            _mkt_add = _as_float_or_none(current_price) or 0.0
            _tp_add, _tp_action = self.add_order_tp_decision(existing, order.tp_price, _mkt_add)
            if _tp_action == "trend_lane_clear":
                if existing.tp_price:
                    logger.info(
                        "[Paper] 长线车道加仓：清除固定 TP %s（%s %s，"
                        "车道声明 tp_pct=null，出场=规则失效/Chandelier）",
                        existing.tp_price, existing.symbol, existing.side,
                    )
                existing.tp_price = None
            elif _tp_action == "inverted_reject":
                logger.error(
                    "[Paper] 加仓TP被止盈侧不变式拦截（拒绝写入）: %s %s "
                    "目标TP=%s 开仓=%s 市价=%s 保持原TP=%s",
                    existing.symbol, existing.side, order.tp_price,
                    _entry_add, _mkt_add, existing.tp_price,
                )
            elif _tp_action == "write":
                existing.tp_price = _tp_add
            if order.sl_price:
                _safe_sl_add = self.safe_sl_price(
                    order.sl_price, side=_side_add, market=_mkt_add, entry=_entry_add)
                if _safe_sl_add <= 0 and _mkt_add > 0:
                    logger.error(
                        "[Paper] 加仓SL被保护侧不变式拦截（拒绝写入）: %s %s "
                        "目标SL=%s 开仓=%s 市价=%s 保持原SL=%s",
                        existing.symbol, existing.side, order.sl_price,
                        _entry_add, _mkt_add, existing.sl_price,
                    )
                else:
                    existing.sl_price = _safe_sl_add or order.sl_price
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
            # [2026-09-18 幽灵峰值修复] peak_pnl_pct / peak_unrealized_pnl 同样基于旧均价，
            # 必须按**同一峰值价格**换算到新均价。不改会造成「峰值价被高估 → 保本止损被推到
            # 现价之上 → 下一 tick 以该止损价幽灵成交」（BNB #4715，加仓后 0.55s 被平在 764.33，
            # 而当时真实价 751）。详见 safe_sl_price 的注释。
            self.rebase_peak_pct_for_entry(existing, _old_entry, float(existing.entry_price or 0))
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
            # [2026-09-19 轮104] 建仓瞬间再验一次方向：成交价可能因滑点偏离下单参考价
            # （多头 price=100/TP=101 合法，但 fill=101.5 会让 TP 落回开仓价下方 → 反向 TP）。
            # 这里以**权威入场价 fill_price** 复验，不合法就丢弃该 TP。
            _tp_open = order.tp_price
            if _tp_open:
                _tp_open_ok = self.safe_tp_price(
                    _tp_open, side=pos_side, market=current_price, entry=fill_price)
                if _tp_open_ok <= 0:
                    logger.error(
                        "[Paper] 建仓TP被止盈侧不变式拦截（丢弃）: %s %s TP=%s "
                        "成交价=%s（滑点致方向非法）",
                        order.symbol, pos_side, _tp_open, fill_price,
                    )
                    _tp_open = None
                else:
                    _tp_open = _tp_open_ok
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
                tp_price=_tp_open,
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
            # [2026-09-03 修复] PaperPosition 没有 metadata_json 列：原 `_pos_kwargs["metadata_json"] = ...` 在任何
            # 带 position_metadata 的开仓上都会抛 TypeError（'metadata_json' is an invalid keyword argument）。
            # 开仓元数据改为并入 exit_state_json.open_metadata（同一 JSON 文本列，merge 语义，各出场模块可读）。
            # [2026-09-03 v3 方向1] 开仓即快照本车道 ExitPolicy 声明（三重屏障 / trailing / 递减 ROI / 结构失效），
            # 持仓终身按声明执行，事后可审计"这笔仓当时的出场规则是什么"。
            try:
                import json as _json_xp
                from backend.services.exit.exit_policy import ExitPolicy as _XP
                from backend.services.position_construction import normalize_lane as _nl_xp
                _xp_lane = _nl_xp(timeframe_tier, _pos_kwargs.get("trade_nature"))
                _es_init = {"exit_policy": _XP.for_lane(_xp_lane).to_dict()}
                # [2026-09-07] 声明 SL 与实际附着 SL 对齐：ATR floor / 结构止损
                # 常把硬 SL 拉宽到 ~4.5%，但 ExitPolicy 仍快照车道默认 3% →
                # soft sl_pct 会比硬单更早触发，口径打架。按实际附着距离重写声明。
                try:
                    _ep_entry = float(_pos_kwargs.get("entry_price") or 0)
                    _ep_sl = float(_pos_kwargs.get("sl_price") or 0)
                    _ep_side = str(_pos_kwargs.get("side") or "long").lower()
                    if _ep_entry > 0 and _ep_sl > 0:
                        if _ep_side in ("long", "buy"):
                            _att = abs(_ep_entry - _ep_sl) / _ep_entry * 100.0
                        else:
                            _att = abs(_ep_sl - _ep_entry) / _ep_entry * 100.0
                        if _att > 0.05:
                            _es_init["exit_policy"]["sl_pct"] = round(_att, 4)
                except Exception:
                    pass
                if position_metadata and isinstance(position_metadata, dict):
                    try:
                        _es_init["open_metadata"] = _json_xp.loads(_json_xp.dumps(position_metadata, ensure_ascii=False, default=str))
                    except Exception:
                        pass
                    _ssp = position_metadata.get("structural_stop_price") or position_metadata.get("invalidation_price")
                    if _ssp:
                        _es_init["structural_stop_price"] = float(_ssp)
                    if position_metadata.get("entry_source"):
                        _es_init["entry_source"] = str(position_metadata["entry_source"])[:40]
                _pos_kwargs["exit_state_json"] = _json_xp.dumps(_es_init, ensure_ascii=False)
            except Exception as _xp_err:
                logger.debug(f"[Paper][ExitPolicy] 开仓快照失败（忽略）: {_xp_err}")
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
        position_id: Optional[int] = None,
        trade_nature: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """平掉指定持仓，支持部分平仓 (quantity=None 表示全部平仓)

        按 strategy_id 精确匹配子仓位；不传则匹配任意同币种同方向仓位。
        子仓系统靠 trade_nature 隔离，平仓时需要 strategy_id 精确定位。

        [2026-08-22 M0-8] 新增 position_id / trade_nature 定位键：
        多周期同 symbol 同 side 并存（scalp+swing+trend_follow）时，调用方必须
        传 position_id（或 strategy_id+trade_nature），否则 .first() 会平到
        错误的那条腿（"平错腿"）。position_id 优先于其它过滤条件。
        """
        from backend.database.models import PaperPosition, PaperBalance, PaperOrder

        # [2026-08-22 M1-1] 行所有权=账户 user_id：写仓位前设置租户上下文
        self._set_tenant_from_account(db, account_id)

        pos_query = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == symbol,
            PaperPosition.side == side,
            PaperPosition.status == "open",
        )
        if position_id is not None:
            pos_query = pos_query.filter(PaperPosition.id == position_id)
        elif trade_nature:
            pos_query = pos_query.filter(PaperPosition.trade_nature == trade_nature)
        if strategy_id:
            pos_query = pos_query.filter(PaperPosition.strategy_id == strategy_id)
        # [2026-09-16 调研轮7 缺陷 HAA-ACC-01] 平仓幂等/防重：
        #  (a) `populate_existing()` —— SQLAlchemy 身份映射默认**不会覆盖已加载属性**，
        #      同一 Session 早先读过该仓（出场栈评估阶段），后续 query 会拿到**陈旧对象**
        #      （status 仍是 open）⇒ 两条出场通道各生成一笔全量平仓单、各记一次 PnL。
        #      实测：VIRTUAL 4638 的两笔全量平仓单（23479/23480，相隔 15s，分别来自
        #      `sl` 与 `thesis_invalidation`）把真实 -41.88 记成 -82.43，多计 -40.55 USD；
        #      12 条同类重复合计 -72.37 USD（1.574% 净值，经 `_recalc_balance` 进入权益）。
        #  (b) `with_for_update()` —— 行锁把并发平仓串行化（SQLite 等方言自动忽略）。
        #  (c) 拿到行后**复核 status/size**：已平或已无量 → 直接返回 None，绝不建第二笔单。
        # 回滚：PAPER_CLOSE_IDEMPOTENT=false（退化为旧的 first() 语义）。
        _idem = str(os.getenv("PAPER_CLOSE_IDEMPOTENT", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        )
        if _idem:
            try:
                pos = pos_query.populate_existing().with_for_update().first()
            except Exception:  # 方言不支持 FOR UPDATE → 至少强制刷新
                pos = pos_query.populate_existing().first()
        else:
            pos = pos_query.first()
        if not pos:
            logger.warning(f"[Paper] 无持仓可平: {symbol} {side}"
                           f"{' strategy=' + strategy_id if strategy_id else ''}")
            return None
        if _idem and (
            str(getattr(pos, "status", "") or "").lower() != "open"
            or float(getattr(pos, "size", 0) or 0) <= 0
        ):
            logger.warning(
                f"[Paper] 平仓幂等拦截(已平/无量): {symbol} {side} "
                f"pos_id={getattr(pos, 'id', None)} status={getattr(pos, 'status', None)} "
                f"size={getattr(pos, 'size', None)} reason={reason}"
            )
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

        # ── [PostFill P1-1 2026-08-30] minNotional 双边可行性门 ──
        # 决策层不得发出交易所会拒的部分平仓单：平仓份额与平后剩余份额都
        # 必须达到交易所最小名义。份额不达标 → 放弃本次部分平仓（返回 None，
        # 档位不消费）；剩余不达标 → 升级为全平（防尘仓死等 SL）。
        if is_partial:
            try:
                from backend.services.exit.feasibility_gate import check_partial_close_notional
                _verdict, _vdetail = check_partial_close_notional(
                    exchange=exchange, chunk_qty=close_qty, pos_qty=remaining_size,
                    price=float(current_price or 0),
                )
                if _verdict == "reject":
                    # [2026-08-31 卡平仓死循环修复] 仓位名义本身 < 2×minNotional
                    # 时（如 BNB 长线 $9.38 名义要求"减半"→ chunk $4.69 < $5），
                    # 拒绝只会让调用方每 30s 重试同一动作形成死循环。此类微仓
                    # 升级为全平（设计意图：防尘仓死等）。
                    try:
                        _pos_notional = float(remaining_size) * float(current_price or 0)
                        _min_notional = None
                        from backend.services.exit.feasibility_gate import resolve_min_notional_usd
                        _min_notional = resolve_min_notional_usd(exchange)
                        if 0 < _pos_notional < 2.0 * float(_min_notional or 5.0):
                            logger.info(
                                f"[Paper] 部分平仓拒绝但微仓(名义${_pos_notional:.2f}<"
                                f"2×min${_min_notional:.2f})→升级全平: {symbol} {side} reason={reason}"
                            )
                            is_partial = False
                            close_qty = remaining_size
                            _verdict = "escalate_full_dust"
                    except Exception:
                        pass
                    if _verdict == "reject":
                        logger.info(
                            f"[Paper] 部分平仓拒绝(minNotional): {symbol} {side} "
                            f"reason={reason} {_vdetail}"
                        )
                        self._record_exit_event(
                            db, pos, event_type="partial_exit_rejected",
                            exit_channel=reason,
                            metadata={"gate": "min_notional", "detail": _vdetail, "reason": reason},
                        )
                        db.commit()
                        return None
                if _verdict == "escalate_full" or _verdict == "escalate_full_dust":
                    logger.info(
                        f"[Paper] 部分平仓→全平(剩余低于minNotional): {symbol} {side} "
                        f"reason={reason} {_vdetail}"
                    )
                    is_partial = False
                    close_qty = remaining_size
            except Exception as _mn_err:
                logger.debug(f"[Paper] minNotional门跳过({symbol}): {_mn_err}")

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
        # [P0-6 权威口径] 将已实现盈亏写入 unrealized_pnl 字段（closed 状态下复用为
        # realized_pnl 存档），且【已含分批 partial_realized_pnl】（total_pnl = final + partial）。
        # 消费方（learning_loop._paper_position_pnl / reentry_cooldown._durable_reopen_blocked）
        # 读取 closed 仓位 unrealized_pnl 时禁止再叠加 partial_realized_pnl，否则双计。
        # _recalc_balance 只查 open 仓位，不影响余额计算
        pos.unrealized_pnl = total_pnl

        # ── R2 已实现亏损风控事件（阶段3）：单笔亏损 >1.5% 权益 → RiskEvent + 币种 24h 禁开 ──
        try:
            _eq = float(getattr(bal, "total_equity", 0) or 0)
            if _eq > 0 and float(total_pnl or 0) < 0 and abs(float(total_pnl or 0)) > _eq * 0.015:
                from backend.services.symbol_penalty import flag_symbol_risk_event
                flag_symbol_risk_event(
                    str(getattr(pos, "symbol", "") or ""),
                    {"pnl": round(float(total_pnl or 0), 2), "equity": round(_eq, 2),
                     "pct": round(float(total_pnl or 0) / _eq * 100, 2),
                     "close_reason": actual_reason},
                )
                logger.warning(
                    "[RiskEvent] %s 单笔已实现亏损 %.2f (%.2f%% 权益) → 24h 禁开该币",
                    pos.symbol, float(total_pnl or 0), float(total_pnl or 0) / _eq * 100,
                )
        except Exception as _re_err:
            logger.debug("[RiskEvent] 记录失败: %s", _re_err)

        # ── 融合归因（阶段2）：来源标签 + 出场通道熔断数据（close 钩子，仅内存字典 + 节流落盘）──
        try:
            from backend.services.source_attribution import attribution as _attr
            _attr.record_close(
                int(getattr(pos, "id", 0) or 0),
                pnl=float(final_pnl or 0),   # 只记 final 腿（partial 腿已在 _partial_close 单独入账）
                fee=float(final_fee or 0),
                close_reason=actual_reason,
                tier=str(getattr(pos, "timeframe_tier", None) or ""),
                symbol=str(getattr(pos, "symbol", "") or ""),
                nature=str(getattr(pos, "trade_nature", "") or ""),
            )
        except Exception as _attr_err:
            # [§80 修复 2026-09-11] 原为 debug ⇒ INFO 级生产日志里**完全不可见**：
            # 这条链喂的是"出场通道熔断"的滚动窗（§77–§79），一旦静默失败，
            # 熔断器会停在旧窗口上、且没人知道（§41.2/§51.7 同类纪律：fail-open 必须可见）。
            logger.warning("[FusionAttr] 归因记录失败(熔断窗将不含本次平仓): %s", _attr_err)

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
            metadata={
                "reason": actual_reason, "final_pnl": final_pnl, "partial_pnl": partial_pnl_sum,
                # [PostFill P3] 平仓遥测：MFE/MAE/funding 一并入事件流，供因子
                # 进化闭环区分"止损太紧 vs 方向错"与资金费偏置归因。
                "mfe_pnl_pct": float(getattr(pos, "peak_pnl_pct", 0.0) or 0.0),
                "mae_pnl_pct": float(getattr(pos, "trough_pnl_pct", 0.0) or 0.0),
                "mae_usd": float(getattr(pos, "trough_unrealized_pnl", 0.0) or 0.0),
                "funding_accrued": self._funding_accrued_total(db, pos),
            },
        )

        # ── [2026-09-07] 情景记忆结局回填（海马体记忆巩固的一环）──
        # 平仓结局回填到对应论题情景，供主脑下次检索「相似行情的历史结局」。
        try:
            from backend.database.models import FullAutoSession as _FAS
            from backend.services.mlto.episodic_memory import backfill_outcome
            _sess = (
                db.query(_FAS)
                .filter(_FAS.paper_account_id == int(account_id))
                .order_by(_FAS.id.desc())
                .first()
            )
            _sess_id = str(getattr(_sess, "session_id", "") or "") if _sess else ""
            if _sess_id:
                _entry = float(getattr(pos, "entry_price", 0) or 0)
                _dirn = 1 if str(getattr(pos, "side", "")).lower() in ("long", "buy") else -1
                _pct = ((float(fill_price or 0) - _entry) / _entry * 100 * _dirn) if _entry > 0 else 0.0
                _hold_h = 0.0
                _opened = getattr(pos, "opened_at", None)
                if _opened is not None:
                    from datetime import datetime as _dt, timezone as _tz
                    _o = _opened if _opened.tzinfo else _opened.replace(tzinfo=_tz.utc)
                    _hold_h = max(0.0, (_dt.now(_tz.utc) - _o).total_seconds() / 3600.0)
                backfill_outcome(
                    session_id=_sess_id,
                    symbol=str(getattr(pos, "symbol", "") or ""),
                    tier=str(getattr(pos, "timeframe_tier", "") or "mid"),
                    pnl=float(total_pnl or 0),
                    pct=_pct,
                    hold_hours=_hold_h,
                    close_reason=str(actual_reason or ""),
                )
        except Exception as _ep_err:
            logger.debug("[Episodic] 结局回填钩子失败: %s", _ep_err)

        self._recalc_balance(db, bal)
        self._cancel_attached_orders(
            db, pos,
            exclude_order_id=int(close_order.id) if getattr(close_order, "id", None) else None,
        )
        db.commit()

        self._tp_levels_cache.pop(pos.id, None)
        self._peak_profit_cache.pop(pos.id, None)
        self._trough_pct_cache.pop(pos.id, None)
        self._trough_usd_cache.pop(pos.id, None)
        # [2026-08-31] 平仓后清理统一离场状态机的 per-position 追踪状态：
        # 此前 reset_position 无人调用，状态随历史仓位无限累积（内存）且
        # position_id 复用时会继承旧仓的 reduce_count/breakeven 标记。
        try:
            from backend.services.exit.unified_exit_state_machine import exit_state_machine
            exit_state_machine.reset_position(int(pos.id))
        except Exception:
            pass

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
        # [2026-08-25] 补落 factor_exposures(开仓时点最近同向信号的因子快照)
        # 与 strategy_id,使复盘/学习层恢复因子归因与策略归因。
        _fx = None
        try:
            from sqlalchemy import text as _sa_text
            from backend.database.connection import SessionLocal as _ArenaLocal2
            _opened = getattr(pos, "opened_at", None)
            if _opened is not None:
                try:
                    _opened_naive = _opened.replace(tzinfo=None) if _opened.tzinfo is not None else _opened
                except Exception:
                    _opened_naive = _opened
                with _ArenaLocal2() as _db2:
                    _fx_row = _db2.execute(_sa_text(
                        "SELECT features_json FROM scalp_signal_log "
                        "WHERE symbol=:s AND direction=:d AND created_at <= :t "
                        "ORDER BY created_at DESC LIMIT 1"
                    ), {"s": str(symbol).upper(), "d": str(side), "t": _opened_naive}).first()
                    if _fx_row and _fx_row[0]:
                        import json as _json
                        _fx = _json.loads(_fx_row[0]) if isinstance(_fx_row[0], str) else _fx_row[0]
        except Exception as _fx_err:
            logger.debug(f"[Paper] factor_exposures 查询跳过: {_fx_err}")

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
                # [2026-08-29 全面修复 P1.6] outcome 按净盈亏(毛-费)判定：
                # 旧毛口径下费拖型小赢被标 win（30 天费 -58.6 全被学习层
                # 当盈利样本）。pnl 列保持毛口径，净额 = pnl - fees 可推导。
                outcome=("win" if ((total_pnl or 0) - (total_fee or 0)) > 0
                         else ("loss" if ((total_pnl or 0) - (total_fee or 0)) < 0 else "scratch")),
                close_reason=actual_reason,
                factor_exposures=_fx,
                strategy_id=str(getattr(pos, "strategy_id", "") or ""),
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
        # [2026-08-29 全面修复 P1.6] 透传平仓费，学习标签按净盈亏（毛-费）判定
        # ——旧口径费拖型小赢被标 win，学习信号系统性偏乐观（30 天费 -58.6）。
        try:
            self._notify_learning_on_close(
                db, pos, fill_price, total_pnl, reason, close_fee=float(total_fee or 0),
            )
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
        # [2026-09-10 第 8 轮审计] **减仓计数口径修复**：
        # `reduce_count` 是出场节流的判据（`unified_exit_state_machine.max_reduce_count`、
        # `master_execution._DEF_REDUCE_MAX/REDUCE_MAX_COUNT`、`defensive_cycle` 均按它限流），
        # 但此前**主流减仓路径从不更新该列**（只有 midlong_position_manager / master_execution
        # 的子仓分支写）⇒ 判据恒为 0、单仓减仓上限形同不存在。
        # 实证：全账户 524 笔有减仓事件而仅 26 笔 `reduce_count>0`；近 75 天 46 笔减仓 ≥4 次、
        # 最多 10 次；§30.4 的「BNB 7 分钟减半 4 次至 $9 尘埃仓」即其后果。
        # 这里补上权威自增，使 DB 列成为**单一事实源**。
        pos.reduce_count = int(getattr(pos, "reduce_count", 0) or 0) + 1
        pos.last_reduce_at = datetime.now(timezone.utc)
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

        # ── 融合归因（阶段4）：部分平仓腿同样入账（每腿记一次；全平时只记 final 腿，不重叠）──
        try:
            from backend.services.source_attribution import attribution as _attr_p
            _attr_p.record_close(
                int(getattr(pos, "id", 0) or 0),
                pnl=float(partial_pnl or 0),
                fee=float(partial_fee or 0),
                close_reason=actual_reason,
                tier=str(getattr(pos, "timeframe_tier", None) or ""),
                symbol=str(getattr(pos, "symbol", "") or ""),
                nature=str(getattr(pos, "trade_nature", "") or ""),
            )
        except Exception as _attr_p_err:
            # [§80 同款修复] 部分平仓腿同样喂熔断窗，失败必须可见
            logger.warning("[FusionAttr] 部分平仓归因失败(熔断窗将不含本腿): %s", _attr_p_err)

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
        # ── [PostFill P1-1 2026-08-30] 手续费预算门 ──
        # 微操腿费用占比（已付分批费用+本腿预估）/当前名义 超预算 → 放弃
        # 本次减仓（返回 None，TP 档位不消费），防止小仓被费用吃成负期望。
        try:
            from backend.services.exit.feasibility_gate import fee_budget_exceeded
            _px = float(getattr(pos, "mark_price", 0) or 0) or float(pos.entry_price or 0)
            # [2026-08-31 PostFill] 预估腿费按账户交易所实际 taker 费率。
            # 原硬编码 0.0005 与 asterdex 0.005% 差 10 倍、hyperliquid 0.035%
            # 差 1.4 倍 → 预算门系统性误判（asterdex 永远拦不住 / HL 过度拦截）。
            try:
                from backend.services.fee_schedule_service import get_fee_rate
                _est_rate = float(get_fee_rate(
                    self._resolve_account_exchange(db, pos.account_id),
                    is_maker=False,
                ))
            except Exception:
                _est_rate = TAKER_FEE_RATE
            _exceeded, _fdetail = fee_budget_exceeded(
                fees_paid=float(getattr(pos, "partial_fee_paid", 0) or 0),
                est_leg_fee=close_qty * _px * _est_rate,
                notional_now=float(pos.size or 0) * _px,
            )
            if _exceeded:
                logger.info(
                    f"[Paper] 分批平仓被费用预算门拦截: {pos.symbol} {pos.side} "
                    f"reason={reason} {_fdetail}"
                )
                return None
        except Exception:
            pass
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
            # [2026-08-22 M0-7] SL/追踪/爆仓按止损价成交是乐观假设（快市滑点被绕过）。
            # 对止损类平仓按 _calc_slip(is_sl=True) 施加反向滑点，亏损口径才真实。
            mark = float(fill_price_override)
            _stop_ov = reason in (
                "stop_loss", "sl", "liquidation", "force_close",
                "trailing_stop", "trailing", "emergency_drawdown",
            )
            if _stop_ov:
                _ov_notional = qty * mark
                _ov_nature = getattr(pos, "trade_nature", None) or "swing"
                _ov_slip = _calc_slip(_ov_notional, _ov_nature, is_sl=True)
                if close_side == "sell":       # 平多：按 bid 成交（比止损价更差）
                    bid = mark * (1 - _ov_slip)
                    ask = mark
                else:                          # 平空：按 ask 成交
                    ask = mark * (1 + _ov_slip)
                    bid = mark
            else:
                bid = ask = mark
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
            # [轮104] 触发侧不变式：方向非法的 TP 绝不成交（BTC #4712 幽灵止盈）
            if self.tp_direction_illegal(pos, pos.tp_price, float(current_price or 0)):
                return False
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
                    if hit_tp and self.tp_direction_illegal(pos, tp_price, current_price):
                        # [轮104] 脏 TP：记录后跳过，不按该价成交
                        hit_tp = False
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
                        # [PostFill §3.5] 爆仓硬线补登 ExitSource.LIQUIDATION
                        self._record_hard_line_exit_source(db, pos, "liquidation")
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
                resolve_tier_from_position,
            )

            account_id = int(getattr(pos, "account_id", 0) or 0)
            _nature = (getattr(pos, "trade_nature", "") or "").strip().lower()

            # [2026-09-03 v3 方向1] E1 趋势仓：时间不是屏障（回测持仓中位数以月计），唯一出场 = 规则失效 / Chandelier
            # （trend_e1_engine 日任务 + 上方硬 SL 层）。跳过 AI 复审队列与兜底强平。
            try:
                from backend.services.trend_e1_engine import is_e1_position as _is_e1
                if _is_e1(pos):
                    return False
            except Exception:
                pass

            # [三周期持仓时间收敛 2026-08-13] 短线判定双保险：trade_nature 短线
            # 或 tier=short 一律不进 AI 复审队列，超时直接 max_hold_timeout 强平。
            # 根因: 08-08 六笔 short/scalp 仓在引擎停摆后落回复审队列兜底链条，
            # 延迟约 28h 才被强平；tier 判定补齐 nature 缺失时的漏网路径。
            _is_short_tier = is_short_no_ai_hold_nature(_nature) or (
                resolve_tier_from_position(pos) == "short"
            )
            if _is_short_tier:
                expired, status = is_position_hold_expired(pos)
                if expired:
                    # [2026-08-23 短线赚钱改造 A] 浮盈续命：45min 超时时浮盈仓推
                    # 保本 SL 再给 15min（数据实证：持仓 >2h 的仓 50-67% 胜率，
                    # 0-1h 才 33.5%——赢家要让它跑，输家立即平）。
                    _ext_until = 0.0
                    try:
                        import json as _json_ext
                        _raw_ext = getattr(pos, "exit_state_json", None) or {}
                        if isinstance(_raw_ext, str):
                            _ext = _json_ext.loads(_raw_ext or "{}") or {}
                        else:
                            _ext = dict(_raw_ext or {})
                        _ext_until = float(_ext.get("max_hold_ext_until") or 0)
                    except Exception:
                        _ext_until = 0.0
                    if _ext_until and time.time() < _ext_until:
                        return False
                    _mark = float(getattr(pos, "mark_price", 0) or 0)
                    _entry = float(getattr(pos, "entry_price", 0) or 0)
                    _side_ab = (getattr(pos, "side", "") or "").lower()
                    if _mark > 0 and _entry > 0:
                        _profit_pct = (_mark - _entry) / _entry if _side_ab == "long" else (_entry - _mark) / _entry
                    else:
                        _profit_pct = 0.0
                    if not _ext_until and _profit_pct > 0:
                        try:
                            import json as _json_ext2
                            _new_sl = (_entry * (1 + 0.0015) if _side_ab == "long"
                                       else _entry * (1 - 0.0015))
                            pos.sl_price = round(_new_sl, 8)
                            _raw_ext2 = getattr(pos, "exit_state_json", None) or {}
                            if isinstance(_raw_ext2, str):
                                _ext2 = _json_ext2.loads(_raw_ext2 or "{}") or {}
                            else:
                                _ext2 = dict(_raw_ext2 or {})
                            _ext2["max_hold_ext_until"] = time.time() + 900.0
                            pos.exit_state_json = _json_ext2.dumps(_ext2, ensure_ascii=False)
                            db.flush()
                            try:
                                db.commit()
                            except Exception:
                                db.rollback()
                            logger.info(
                                "[Paper] ⏰ 短线超时浮盈续命: %s %s 浮盈%.3f%% → SL推保本%+.4f%% 再给15min",
                                pos.symbol, _side_ab, _profit_pct * 100,
                                (1 + 0.0015) * 100 - 100 if _side_ab == "long" else -(1 - 0.0015) * 100 + 100,
                            )
                            return False
                        except Exception as _ext_err:
                            logger.debug("[Paper] 浮盈续命失败，走强平: %s", _ext_err)
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
                    # [PostFill §3.5] 超时强平补登 ExitSource.TIME_DECAY
                    self._record_hard_line_exit_source(db, pos, "max_hold_timeout")
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
                # [PostFill §3.5] 超时兜底强平补登 ExitSource.TIME_DECAY
                self._record_hard_line_exit_source(db, pos, "max_hold_timeout")
                self._tp_levels_cache.pop(pos.id, None)
                self._peak_profit_cache.pop(pos.id, None)
                return True
            return False
        except Exception as e:
            logger.debug(f"[Paper] max_hold 检查跳过: {e}")
            return False

    # [Phase E 2026-08-30] _run_v1_protection 及其三张参数表
    # (_TP_SAFETY_NET_BY_NATURE/_BREAKEVEN_BY_NATURE/_TRAILING_BY_NATURE) 已整体删除。
    # 默认 PROFIT_PROTECTION_VERSION=v2 下该路径自 Phase B+C 起就从未执行；
    # 历史兜底调用点已全部改为 warn+跳过（不提供 v1 回退）。

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

        # ── [PostFill P3 2026-08-30] 分批档数按当前名义自动降档 ──
        # 期望值研究（Quantified Strategies / Bulkowski）：分批止盈系统性截断
        # 右尾，小仓拆档后每档贴着 minNotional 下限、费用占比放大，数学劣势
        # 被进一步放大。故名义额不足时自动降档：
        #   名义 < TP_LADDER_SINGLE_MAX_NOTIONAL_USD(默认$30) → 单档：TP1 全平
        #   名义 < TP_LADDER_TWO_MAX_NOTIONAL_USD(默认$100)   → 两档：TP1平40% + TP2清仓
        #   否则 → 原三档 TP1/TP2/TP3（25/25/30+跳档补齐）
        try:
            from backend.config.settings import (
                TP_LADDER_SINGLE_MAX_NOTIONAL_USD as _SINGLE_MAX,
                TP_LADDER_TWO_MAX_NOTIONAL_USD as _TWO_MAX,
            )
        except Exception:
            _SINGLE_MAX, _TWO_MAX = 30.0, 100.0
        _notional_now = _price * float(getattr(pos, "size", 0) or 0)

        if 0 < _notional_now < _SINGLE_MAX:
            # ── 单档模式：TP1 触发即全平（不拆档）──
            if (not _tp1_done) and _price_change_atr >= _params["tp1_mult"]:
                logger.info(
                    f"[Paper][v2-Unified] 单档TP全平(名义${_notional_now:.1f}<${_SINGLE_MAX:.0f}): "
                    f"{pos.symbol} {_side} Δ={_price_change_atr:.2f}ATR ≥ {_params['tp1_mult']}"
                )
                # [PostFill §3.7] 假设 PnL 影子：旧三档实现此刻会平 25% 继续持有，
                # 降档实现实际全平。记下 counterfactual 供事后对比右尾截断效应。
                # kind 需短：event_type 列 VARCHAR(40)。
                self._record_postfill_telemetry(
                    db, pos, "staged_tp_cf",
                    {
                        "mode": "single",
                        "legacy_close_pct": 0.25,
                        "actual": "full_close",
                        "notional": round(_notional_now, 2),
                        "atr_change": round(_price_change_atr, 4),
                    },
                )
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="staged_tp1_single",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                return True
        elif _notional_now < _TWO_MAX:
            # ── 两档模式：TP1 平 40% + 保本，TP2 清仓 ──
            if (not _tp2_done) and _price_change_atr >= _params["tp2_mult"]:
                logger.info(
                    f"[Paper][v2-Unified] 两档模式TP2清仓(名义${_notional_now:.1f}): "
                    f"{pos.symbol} {_side} Δ={_price_change_atr:.2f}ATR ≥ {_params['tp2_mult']}"
                )
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="staged_tp2_clear",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                return True
            if (not _tp1_done) and _price_change_atr >= _params["tp1_mult"]:
                # [PostFill §3.7] 假设 PnL 影子：旧三档实现此刻平 25%，两档实现平 40%。
                self._record_postfill_telemetry(
                    db, pos, "staged_tp_cf",
                    {
                        "mode": "two_tier",
                        "legacy_close_pct": 0.25,
                        "actual_close_pct": 0.40,
                        "notional": round(_notional_now, 2),
                        "atr_change": round(_price_change_atr, 4),
                    },
                )
                _closed = self._partial_close_by_pct(db, pos, 0.40, "staged_tp1")
                if _closed and _closed.get("closed_fully"):
                    return True
                if _closed is None:
                    # 可行性门拦截（minNotional/费用预算）：档位不消费，下 tick 重试
                    return False
                pos.tp_level_reached = 1
                self._tighten_sl_unified(pos, _entry + _atr_price * 0.8 * _side_dir, "staged_tp1", market=current_price)
                logger.info(
                    f"[Paper][v2-Unified] 两档模式TP1: {pos.symbol} {_side} "
                    f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp1_mult']}, 平40%, SL→保本"
                )
        else:
            # ── 三档模式（原路径，名义充足时）──
            # [P0-7e 跳档修复] 价格一跳穿多档时，按未触发的档位累计减仓比例一次执行，
            # 避免只减最高档而 TP1/TP2 的 25%+25% 永久丢失（此前 70% 仓位暴露给反转）。
            if (not _tp3_done) and _price_change_atr >= _params["tp3_mult"]:
                _missing_ratio = 0.30  # TP3 档
                if not _tp2_done:
                    _missing_ratio += 0.25  # 补齐 TP2
                if not _tp1_done:
                    _missing_ratio += 0.25  # 补齐 TP1
                _closed = self._partial_close_by_pct(db, pos, _missing_ratio, "staged_tp3")
                if _closed and _closed.get("closed_fully"):
                    return True
                if _closed is None:
                    return False  # 可行性门拦截：档位不消费，下 tick 重试
                pos.tp_level_reached = 3
                # [P0-7 方向修复] 空单必须乘 _side_dir：short 的 peak 在入场价下方，
                # 追踪止损应位于 peak 上方（朝 entry 方向），漏乘会把 SL 放到现价下方 →
                # 下一次判定立即触发并以低于市价成交，虚增空单浮盈。
                _new_sl = _peak_price - _atr_price * _params["trail_mult"] * _side_dir
                self._tighten_sl_unified(pos, _new_sl, "staged_tp3", market=current_price)
                logger.info(
                    f"[Paper][v2-Unified] TP3 触发: {pos.symbol} {_side} "
                    f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp3_mult']}, "
                    f"跳档补齐减仓{_missing_ratio:.0%}, 启动追踪 SL→{pos.sl_price}")
            elif (not _tp2_done) and _price_change_atr >= _params["tp2_mult"]:
                _missing_ratio = 0.25  # TP2 档
                if not _tp1_done:
                    _missing_ratio += 0.25  # 补齐 TP1
                _closed = self._partial_close_by_pct(db, pos, _missing_ratio, "staged_tp2")
                if _closed and _closed.get("closed_fully"):
                    return True
                if _closed is None:
                    return False  # 可行性门拦截：档位不消费，下 tick 重试
                pos.tp_level_reached = 2
                _tp1_price = _entry + _atr_price * _params["tp1_mult"] * _side_dir
                self._tighten_sl_unified(pos, _tp1_price + _atr_price * 0.5 * _side_dir, "staged_tp2", market=current_price)
                logger.info(
                    f"[Paper][v2-Unified] TP2 触发: {pos.symbol} {_side} "
                    f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp2_mult']}, "
                    f"跳档补齐减仓{_missing_ratio:.0%}")
            elif (not _tp1_done) and _price_change_atr >= _params["tp1_mult"]:
                # TP1: 平 25%, SL → entry + ATR价格距×0.8 (保本+给呼吸空间)
                # [2026-07-30 crypto-native] 0.3×ATR≈0.15% 太紧，加密5m正常波动0.5-1%轻松击穿
                # → breakeven_tp 100% 微利出场。提升到 0.8×ATR 给足够缓冲。
                _closed = self._partial_close_by_pct(db, pos, 0.25, "staged_tp1")
                if _closed and _closed.get("closed_fully"):
                    return True
                if _closed is None:
                    return False  # 可行性门拦截：档位不消费，下 tick 重试
                pos.tp_level_reached = 1
                self._tighten_sl_unified(pos, _entry + _atr_price * 0.8 * _side_dir, "staged_tp1", market=current_price)
                logger.info(
                    f"[Paper][v2-Unified] TP1 触发: {pos.symbol} {_side} "
                    f"Δ={_price_change_atr:.2f}ATR ≥ {_params['tp1_mult']}, 平25%, SL→保本")

        # ── 利润回撤保护 (peak 回撤, 以 ATR 为单位) ──
        # 仅当已有浮盈峰值时计算 (peak_price 在盈利方向超过 entry)
        _peak_atr = ((_peak_price - _entry) / _entry) * _side_dir / _atr if _entry > 0 else 0.0
        if _peak_atr > 0 and _peak_price > 0:
            # 回撤 ATR 数 (正数=从峰值回吐). 方向无关: long 价跌/short 价涨都为正。
            _dd_atr = ((_price - _peak_price) / _entry) * (-_side_dir) / _atr
            if _dd_atr > _params["dd_hard"] and _price_change_atr > 0:
                # 硬回撤 (>4×ATR): 任何阶段都全平 (防还利)
                # [2026-08-24 退出语义修复] 仅在"仍处盈利侧"(_price_change_atr>0)触发：
                # 价格已穿越入场价（亏损侧）时没有可保护的利润，该区域由 SL/论题失效
                # 退出负责。此前 UNI trend/swing 两仓在 -5.8%/-6.0% 亏损区被
                # profit_drawdown_hard 提前砍掉，破坏了 SL/结构退出语义。
                logger.warning(
                    f"[Paper][v2-Unified] 利润硬回撤全平: {pos.symbol} {_side} "
                    f"peak={_peak_price:.6f} → price={_price:.6f}, dd={_dd_atr:.2f}ATR > {_params['dd_hard']}")
                self.close_position(
                    db, pos.account_id, pos.symbol, pos.side,
                    reason="profit_drawdown_hard",
                    strategy_id=getattr(pos, "strategy_id", None),
                )
                return True
            if _dd_atr > _params["dd_hard"]:
                logger.info(
                    f"[Paper][v2-Unified] 利润硬回撤跳过(已入亏损侧): {pos.symbol} {_side} "
                    f"peak={_peak_price:.6f} → price={_price:.6f}, dd={_dd_atr:.2f}ATR, "
                    f"交给 SL/thesis 退出")
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
            # [P0-7 方向修复] 同 TP3：空单追踪止损须乘 _side_dir，避免 SL 落在现价下方。
            _new_trail = _peak_price - _atr_price * _params["trail_mult"] * _side_dir
            self._tighten_sl_unified(pos, _new_trail, "unified_trailing", market=current_price)

        return False

    def _tighten_sl_unified(self, pos, new_sl: float, reason: str,
                            market: Optional[float] = None) -> None:
        """把 SL 向 entry 有利方向收紧 (long 取较大, short 取较小), 只收紧不放宽。

        与 _enforce_min_sl 配合: 此处先收紧, _enforce_min_sl 保证不会被压到
        小于最小距离。对 short, sl_price 从 None/0 视作 +inf, 任何有限值都算收紧。

        [2026-09-18] 另加**保护侧不变式**：SL 不得落到现价错误一侧。
        `market` 优先用调用方传入的**本 tick 现价**（权威，与触发判定同源）；
        未传时退回 `pos.mark_price`；两者都拿不到则不拦截（不因取价失败放弃止损管理）。
        """
        try:
            _side = str(getattr(pos, "side", "long")).lower()
            _new = float(new_sl)
            if _new <= 0:
                return
            _market = float(market or 0) or float(getattr(pos, "mark_price", 0) or 0)
            _safe = self.safe_sl_price(_new, side=_side, market=_market,
                                       entry=_as_float_or_none(getattr(pos, "entry_price", None)) or 0.0)
            if _safe <= 0 and _market > 0:
                logger.warning(
                    "[Paper][v2-Unified] SL 收紧被保护侧不变式拦截(%s): %s %s 目标=%s 现价=%s",
                    reason, getattr(pos, "symbol", "?"), _side, _new, _market,
                )
                return
            _new = _safe if _safe > 0 else _new
            _cur = float(getattr(pos, "sl_price", 0) or 0)
            if _side in ("long", "buy"):
                if _new > _cur:
                    pos.sl_price = round(_new, 8)
            else:  # short
                if _cur <= 0 or _new < _cur:
                    pos.sl_price = round(_new, 8)
        except Exception as _e:
            logger.debug(f"[Paper][v2-Unified] SL收紧失败({reason}): {_e}")

    def _run_exit_policy_layer(self, db, pos, entry: float, current_price: float, tier, nature) -> bool:
        """[2026-09-03 v3 方向1] 按持仓开仓时声明的 ExitPolicy 评估一根 tick。

        返回 True = 已平仓（调用方 return）。tighten_sl 只朝有利方向改 pos.sl_price（并同步交易所 TP/SL）。
        峰值 ROI（价格口径）记在 exit_state_json.exit_policy_peak_roi_pct，与 peak_pnl_pct（保证金口径）解耦。
        """
        import json as _json_ep
        from datetime import timezone as _tz_ep
        from backend.services.exit import exit_policy as _xp
        from backend.services.position_construction import normalize_lane as _norm_lane

        if not _xp.enforce_enabled():
            return False
        if not entry or entry <= 0 or not current_price or current_price <= 0:
            return False
        lane = _norm_lane(tier, nature)
        try:
            es = _json_ep.loads(getattr(pos, "exit_state_json", None) or "{}") or {}
        except Exception:
            es = {}
        policy = _xp.policy_from_exit_state(es, lane)
        if not policy.enabled:
            return False
        # [2026-09-16 调研轮7] 存量仓策略刷新：车道重标定（trail/min_roi/TP/time_limit）
        # 对开仓快照永不生效 → 同账户新旧策略并存、旧仓按已证伪档位出场（实测 XRP 4683
        # +4.42% ROI 仍用 trail 3.0/1.5）。刷新只动利润保护字段，止损字段保持快照值。
        # 回滚：EXIT_POLICY_REFRESH_OPEN=false。
        if _xp.refresh_enabled():
            try:
                _new_policy, _chg = _xp.refreshed_policy(policy, lane)
                if _chg:
                    # [调研轮7 修正] 把合并后的策略**写回快照**（es["exit_policy"]），
                    # 而不是只替换内存变量：① 幂等——下一 tick 不再重复检测/追加历史；
                    # ② 鲁棒——本 tick 后续任何异常都不会让刷新"看起来生效、实际没生效"
                    #   （首版只改内存变量，实测出现 chg 非空但 verdict=hold/ok，
                    #    即刷新被静默吞掉、SOL 该平没平）。
                    _hist = es.get("exit_policy_history")
                    if not isinstance(_hist, list):
                        _hist = []
                    _hist.append({
                        "ts": time.time(), "reason": "lane_recalibration",
                        "changes": _chg,
                    })
                    es["exit_policy_history"] = _hist[-5:]
                    es["exit_policy_refreshed_at"] = time.time()
                    es["exit_policy"] = _new_policy.to_dict()
                    pos.exit_state_json = _json_ep.dumps(es, ensure_ascii=False)
                    policy = _new_policy
                    # 注意：`side` 在本函数后段才赋值（~3280 行），此处只能用 pos.side，
                    # 否则 UnboundLocalError 会被外层 except 吞掉（调研轮7 实测踩过）。
                    logger.info(
                        f"[Paper][ExitPolicy] 刷新存量仓策略 {pos.symbol} "
                        f"{getattr(pos, 'side', '?')} lane={lane} changes={list(_chg)}"
                    )
            except Exception as _rf_err:
                # 必须可见：静默 fail-open 会让"刷新没生效"伪装成"策略本来就该 hold"
                logger.warning(
                    f"[Paper][ExitPolicy] 存量仓刷新失败(fail-open，本 tick 用旧快照) "
                    f"{getattr(pos, 'symbol', '?')}: {type(_rf_err).__name__}: {_rf_err}"
                )

        # 持仓时长（opened_at 为库内 naive 北京钟面 → UTC）
        elapsed = 0.0
        try:
            from backend.utils.db_datetime import parse_db_naive_to_utc as _norm_ts_ep
            opened = _norm_ts_ep(pos.opened_at) if pos.opened_at else None
            if opened is not None:
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=_tz_ep.utc)
                elapsed = max(0.0, (datetime.now(_tz_ep.utc) - opened).total_seconds())
        except Exception:
            elapsed = 0.0

        side = "long" if str(pos.side).lower() == "long" else "short"
        roi = (current_price - entry) / entry * 100.0
        roi = roi if side == "long" else -roi
        prev_peak = float(es.get("exit_policy_peak_roi_pct") or 0.0)
        peak = max(prev_peak, roi)
        if peak > prev_peak + 1e-9:
            es["exit_policy_peak_roi_pct"] = round(peak, 6)
            try:
                pos.exit_state_json = _json_ep.dumps(es, ensure_ascii=False)
            except Exception:
                pass

        snap = _xp.ExitSnapshot(
            side=side, entry=float(entry), current=float(current_price), elapsed_sec=elapsed, peak_roi_pct=peak,
            sl_price=float(pos.sl_price) if pos.sl_price else None,
            tp_price=float(pos.tp_price) if pos.tp_price else None,
            structural_stop_price=(float(es["structural_stop_price"]) if es.get("structural_stop_price") else None),
            roi_pct=roi,
        )
        verdict = _xp.evaluate(policy, snap)
        if verdict.action == "close":
            logger.info(
                f"[Paper][ExitPolicy] close {pos.symbol} {side} lane={lane} reason={verdict.reason} "
                f"roi={roi:.3f}% peak={peak:.3f}% elapsed={elapsed / 3600:.2f}h detail={verdict.detail}")
            fill = None
            if verdict.reason in ("sl", "structural_invalidation") and verdict.detail.get("level"):
                fill = float(verdict.detail["level"])
            elif verdict.reason == "tp" and verdict.detail.get("level"):
                fill = float(verdict.detail["level"])
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason=f"exit_policy:{verdict.reason}",
                strategy_id=getattr(pos, "strategy_id", None),
                fill_price_override=fill,
            )
            return True
        if verdict.action == "tighten_sl" and verdict.new_sl:
            old_sl = pos.sl_price
            # [2026-09-18] 保护侧不变式：ExitPolicy 给出的新 SL 若落在现价错误一侧，
            # 下一 tick 立即触发并以该价幽灵成交（#4715）。拒绝并记 warning。
            _safe_new_sl = self.safe_sl_price(
                verdict.new_sl, side=side, market=float(current_price or 0),
                entry=float(getattr(pos, "entry_price", 0) or 0))
            if _safe_new_sl <= 0 and float(current_price or 0) > 0:
                logger.warning(
                    "[Paper][ExitPolicy] tighten_sl 被保护侧不变式拦截：%s %s 目标SL=%s 现价=%s",
                    pos.symbol, side, verdict.new_sl, current_price,
                )
                return False
            pos.sl_price = float(_safe_new_sl or verdict.new_sl)
            try:
                self._ensure_sl_inside_liq(pos)
            except Exception:
                pass
            first_activation = not bool(es.get("exit_policy_trailing_active"))
            es["exit_policy_trailing_active"] = True
            try:
                pos.exit_state_json = _json_ep.dumps(es, ensure_ascii=False)
            except Exception:
                pass
            logger.info(
                f"[Paper][ExitPolicy] tighten_sl {pos.symbol} {side} lane={lane} {old_sl}→{pos.sl_price} "
                f"(peak={peak:.3f}% lock={verdict.detail.get('lock_roi_pct')})")
            try:
                from backend.services.exchange.live_tpsl_sync import (
                    maybe_sync_live_tpsl as _sync_ep, maybe_place_native_trailing as _native_ep,
                )
                # live 账户：首次激活优先挂交易所原生 TRAILING_STOP_MARKET（LIVE_NATIVE_TRAILING_STOP），软件 SL 同步兜底
                if first_activation and policy.trailing_callback_pct:
                    _native_ep(db, pos, callback_pct=float(policy.trailing_callback_pct))
                _sync_ep(db, pos, force=True)
            except Exception:
                pass
        return False

    # ════════════════════════════════════════════════════════════════════════
    # [2026-09-18 轮99] 趋势车道成员判定 + 跳过日内保护的可见性
    # ════════════════════════════════════════════════════════════════════════
    TREND_LANE_NATURES = ("trend_follow", "position")
    _TREND_SKIP_LOGGED: set = set()          # 已打印过"跳过日内保护"的仓位 id（有界）

    @classmethod
    def _is_trend_lane_member(cls, pos, tier=None, nature=None) -> bool:
        """该仓位是否属**趋势车道**（long / trend_follow / position / E1）。

        ## 为什么必须与开关解耦（轮99）

        旧判定把"车道成员"绑在 `long_v2_enabled()`（= 入场闸 `LONG_TREND_V2` 且主脑未接管）
        上，而 `.env` 里 `LONG_TREND_V2=0` + 主脑已接管 ⇒ 恒 False
        ⇒ "长线仓跳过中短线口径止盈/保本/追踪/回撤"这条规则**从未生效**，
        长线仓被 ATR(1h) 阶梯在 +3.8%/+5.8% 分批止盈、并把止损拖到成本上方 1%~2%。

        **入场闸是入场闸，车道是车道**：一个仓位属不属于趋势车道，
        只由它自身的 (tier, nature, entry_source) 决定，与任何开关无关。

        回滚开关 `EXIT_TREND_LANE_SKIP_INTRADAY`（默认 true）；关掉即恢复旧行为
        （所有车道共享 ATR 阶梯）。
        """
        try:
            from backend.config import settings as _st
            if not bool(getattr(_st, "EXIT_TREND_LANE_SKIP_INTRADAY", True)):
                return False
        except Exception:
            pass
        # [轮100] 车道归属不再用散落的 (tier, nature) 元组判断，统一走车道策略真源
        # （`config/lane_policy.py`）——"中线/长线是两个概念"这件事只能有一处定义。
        try:
            from backend.config.lane_policy import is_long_lane as _is_long_lane
            _t = tier if tier is not None else getattr(pos, "timeframe_tier", None)
            _n = nature if nature is not None else getattr(pos, "trade_nature", None)
            if _is_long_lane(tier=_t, nature=_n):
                return True
        except Exception:
            # 真源不可用时退回等价的内联判断（失败方向：仍按趋势车道处理，
            # 宁可少用日内阶梯，也不要让长线被日内阶梯管）
            _tier = str(tier if tier is not None else getattr(pos, "timeframe_tier", "") or "").strip().lower()
            _nature = str(nature if nature is not None else getattr(pos, "trade_nature", "") or "").strip().lower()
            if _tier == "long" or _nature in cls.TREND_LANE_NATURES:
                return True
        # E1 趋势仓：契约明写"唯一出场 = 规则失效 / Chandelier"，无论上面两个字段如何都必须算成员
        try:
            from backend.services.trend_e1_engine import is_e1_position as _is_e1
            return bool(_is_e1(pos))
        except Exception:
            return False

    def _should_run_unified_staged_tp(self, pos) -> bool:
        """本仓位是否该跑"中短线口径"的统一分段止盈（ATR 阶梯）。

        趋势车道（long / trend_follow / position / E1）→ False。
        抽成独立方法有两个目的：
          ① 判定与调用点分离，便于单测直接断言（不必构造整个 tick 环境）；
          ② 让"长线不跑日内阶梯"这条契约只有**一处**定义，不会出现
             "注释说跳过、条件写成别的变量"的再次脱节（轮99 的根因）。
        """
        try:
            return not self._is_trend_lane_member(
                pos,
                getattr(pos, "timeframe_tier", None),
                getattr(pos, "trade_nature", None),
            )
        except Exception:
            # 判定异常时按"跑"处理（= 旧行为），宁可多一层保护也不要静默失去保护
            return True

    @classmethod
    def _log_trend_lane_skip_once(cls, pos, is_member: bool) -> None:
        """趋势车道跳过日内保护时，每个仓位打一条 INFO 说明"为什么没做分档止盈"。

        为什么要日志：这条跳过会让人误以为"系统没在管这个仓"，
        而实际是**按车道契约交给 Chandelier / 规则失效**。没有这条日志，
        排查时只能看到"仓位不动"，与"保护失效"无法区分。
        """
        if not is_member:
            return
        try:
            _pid = int(getattr(pos, "id", 0) or 0)
            if not _pid or _pid in cls._TREND_SKIP_LOGGED:
                return
            if len(cls._TREND_SKIP_LOGGED) >= 2048:      # 有界，绝不无界增长
                cls._TREND_SKIP_LOGGED.clear()
            cls._TREND_SKIP_LOGGED.add(_pid)
            logger.info(
                "[Paper][轮99] %s %s 属趋势车道（tier=%s nature=%s）→ 跳过统一分段止盈/"
                "保本/硬软回撤/追踪（中短线口径）与 ExitPolicy 层；"
                "出场只由 Chandelier 结构止损 / 规则失效 / 硬层 failsafe 决定",
                getattr(pos, "symbol", "?"), getattr(pos, "side", ""),
                getattr(pos, "timeframe_tier", None), getattr(pos, "trade_nature", None),
            )
        except Exception:
            pass

    def _run_v2_protection(self, db, pos, entry, current_price, profit_pct, _nature):
        """v2 利润保护系统 — 基于 TP 进度的分层保护

        返回 True 表示持仓已平，调用方应 continue。
        """
        # [2026-09-02 P1.3] 峰谷遥测提到最前：原先 _sync_peak_state 在下面的
        # max_hold_timeout 与 manager 检查之后，这两处任一提前 return 就整 tick
        # 跳过遥测更新 —— 实测 MAE 覆盖率只有 10.1%（peak 有 73.4%），且止损单
        # 的 MAE 均值仅 -0.029% 而止损距离为 1.11%，说明触损那一刻从未被记下。
        # 遥测是纯观测量（min/max 幂等），不应受保护逻辑的控制流影响；2.2 用
        # MFE/MAE 校准 TP/SL 依赖它的完整性。
        try:
            _upnl_tel = (float(pos.unrealized_pnl or 0)
                         + float(pos.partial_realized_pnl or 0))
            self._sync_peak_state(pos, _upnl_tel, current_price)
        except Exception as _tel_err:
            logger.debug(f"[Paper] 峰谷遥测同步跳过({getattr(pos,'symbol','?')}): {_tel_err}")

        if self._enforce_max_hold_timeout(db, pos):
            return True

        from backend.database.models import PaperBalance

        manager = self._profit_manager
        if not manager:
            # [Phase E 2026-08-30] v1 兜底已删除：manager 未初始化属异常初始化
            # 顺序，本 tick 跳过利润保护（硬 SL/TP/超时仍由 reprice/max_hold 兜底），
            # warn 以便定位。
            logger.warning(
                "[Paper] _run_v2_protection 在 _profit_manager 未初始化时跳过保护 "
                "— 检查引擎初始化顺序。"
            )
            return False

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
                # [2026-08-22 M1-3] opened_at 是北京钟面 naive（PG 会话 tz=Asia/Shanghai），
                # 原来按 UTC 解读会让保护期判定偏差 8h（保护实际延迟 8 小时）。
                from backend.utils.db_datetime import parse_db_naive_to_utc as _norm_ts
                opened = _norm_ts(pos.opened_at)
                if opened is None:
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

        # [2026-08-24 long_trend_v2 全接管] V2 长线仓唯一退出 = manage_long_position
        # 的 decide_long（结构破坏/周线 Chandelier/极端回撤60-80%/30d no_progress/
        # 结构目标减半）。本函数内所有短中线口径的止盈/保本/追踪/回撤保护一律跳过，
        # 只保留 _enforce_max_hold_timeout 的 SL/TP(failsafe)/liq 硬层。
        #
        # ══════════════════════════════════════════════════════════════════════
        # [2026-09-18 轮99 修] 上面这条"长线跳过"**从未生效过**，根因是判定绑错了开关
        # ══════════════════════════════════════════════════════════════════════
        # 旧实现：
        #     _v2_long_managed = long_v2_enabled() and (nature 是长线 or tier == long)
        # 而 `long_v2_enabled()` 读的是**入场闸** `LONG_TREND_V2`（`.env` 现为 **0**），
        # 且主脑模式（`MIDLONG_BRAIN_MODE`）开启时还会再静默否决一次
        # （见 `long_trend_v2.py:long_v2_enabled` 的 §58 注释）⇒ 两个条件都指向 False
        # ⇒ `_v2_long_managed` 对**任何**长线仓恒为 False
        # ⇒ 长线仓照样跑"中短线口径"的统一分段止盈/保本/硬软回撤/追踪。
        #
        # 实测后果（account 14 近 30 天，长线 48 笔）：
        #   · 中位持仓 **13.56h**（车道设计 24–168h）、中位实现 **+0.53%**；
        #   · 出场事件里长线仓照样出现 `staged_tp1`（**+3.83%**）与 `staged_tp2`（**+5.80%**）
        #     —— 那是 ATR(1h) 阶梯（`tp1_mult=2.0`/`tp2_mult=3.0`）；
        #   · 每次分档后把止损拖到 `entry + 0.8×ATR`（≈成本上方 1%~2%）
        #     ⇒ 一次 2% 级正常回撤就把整笔趋势仓按"微利"平掉
        #     （4 笔 long 以 +1.14%/+2.05%/+2.11%/+4.33% 收场，reason=breakeven_tp）；
        #   · 而车道自己声明的档位是 `exit_policy.tp_stages=[8,15,25]%` —— 声明与执行完全脱节
        #     （`exit_policy.py:22` 明确写着"本模块**只声明**档位"）。
        #
        # 修法：把"是否属趋势车道"与**入场闸开关彻底解耦** —— 车道成员身份只由
        # 仓位自身的 (tier, nature, E1 标记) 决定：
        #     tier == "long"  或  nature ∈ (trend_follow, position)  或  E1 仓位(entry_source=trend_e1)
        # 成员 ⇒ 本函数内所有中短线口径的止盈/保本/追踪/回撤 + ExitPolicy 层**一律跳过**，
        # 只保留硬层（failsafe SL/TP/liq/超时）与车道自己的 Chandelier/规则失效退出。
        # 回滚：`EXIT_TREND_LANE_SKIP_INTRADAY=false`（settings.py 已声明 + 已登记 env_registry）。
        _v2_long_managed = self._is_trend_lane_member(pos, _pos_tier, _pos_nature)
        self._log_trend_lane_skip_once(pos, _v2_long_managed)

        # ── [2026-09-03 v3 方向1] ExitPolicy 车道声明层（三重屏障收敛）──
        # 结构失效价 / 硬 SL·TP 口径 / time_limit / 时间递减 ROI / trailing 激活-回撤，在硬层之后、利润管理器之前
        # 评估一次。V2 长线仓（唯一出场 = long_trend_v2 的 L1/Chandelier）跳过。命中 close → 平仓并 return。
        if not _v2_long_managed:
            try:
                if self._run_exit_policy_layer(db, pos, float(entry), float(current_price), _pos_tier, _pos_nature):
                    self._peak_profit_cache.pop(pos_id, None)
                    return True
            except Exception as _ep_err:
                logger.warning(f"[Paper][ExitPolicy] 评估异常（跳过本 tick）{getattr(pos, 'symbol', '?')}: {_ep_err}")

        if _min_hold_ok and not _v2_long_managed:
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
            # 最短持仓未达标 或 V2 长线仓 → 跳过保护动作，仅保留爆仓/SL/TP
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
            _skip_d6 = bool(_v2_long_managed)
            # [2026-09-07] mid/swing 交给 ExitPolicy 单一出口，跳过 D6。
            # 根因：profit_drawdown_full 在 mid 上频繁锁利/全平，均持仓 ~8h，
            # 与中线兑现窗口冲突（ExitPolicy trail/min_roi/no_progress 已覆盖）。
            if not _skip_d6 and str(_pos_tier or "").lower() == "mid":
                try:
                    from backend.services.exit.exit_policy import enforce_enabled as _ep_on
                    if _ep_on():
                        _skip_d6 = True
                except Exception:
                    _skip_d6 = True  # fail-closed：宁跳过 D6 也不短线化 mid
            if not _skip_d6 and str(_nature or "").lower() == "swing":
                try:
                    from backend.services.exit.exit_policy import enforce_enabled as _ep_on2
                    if _ep_on2():
                        _skip_d6 = True
                except Exception:
                    _skip_d6 = True
            if _skip_d6:
                # [2026-08-24 long_trend_v2] V2 长线仓跳过 D6；mid 见上。
                _dd_action = None
            else:
                from backend.services.profit_drawdown_guard import (
                    basis_from_original_enabled,
                    get_profit_drawdown_guard,
                )
                _dd_guard = get_profit_drawdown_guard()
                # [2026-09-10 根因修复] 门槛基准用**原始名义**：peak_profit 是残仓之前
                # （更大仓位）赚到的历史美元峰值，而 pos.size 在分批减仓后只剩残仓
                # （实测 ASTER 1/8、UNI 1/16、BTC 1/35）→ 旧口径「3%×当前名义」崩塌，
                # 守卫在尘埃残仓上误触发全平（profit_drawdown_full 出场后 24h 价格回归
                # +1.39%、72h +3.61%，即出场过早）。original_size 缺失时回退当前 size。
                # 回滚：PDG_BASIS_ORIGINAL_NOTIONAL=false。
                _dd_basis = None
                if basis_from_original_enabled():
                    _dd_basis = float(entry) * float(
                        getattr(pos, "original_size", None) or pos.size or 0
                    ) or None
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
                    position_value_basis=_dd_basis,
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
                        # [P0-7 单调] 走 _tighten_sl_unified（long 只升/short 只降），
                        # 直接赋值会把回撤后的更低 SL 反向放宽已锁利润位。
                        self._tighten_sl_unified(_remaining, _dd_action["new_sl"], "profit_drawdown_partial", market=current_price)
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
                            # [2026-08-31 PostFill] 合并更新而非整覆盖：exit_state_json
                            # 是 SM/PEO/trend_agent 的共享状态库（nature_staged_tp 等键），
                            # 整覆盖会把其他模块写入的状态 clobber 掉。
                            try:
                                import json as _json_dd
                                _st_dd = _json_dd.loads(_remaining.exit_state_json or "{}")
                            except Exception:
                                _st_dd = {}
                            if not isinstance(_st_dd, dict):
                                _st_dd = {}
                            _st_dd["last_guard"] = "profit_drawdown_partial"
                            _st_dd["new_sl"] = float(_dd_action["new_sl"])
                            _remaining.exit_state_json = _json_dd.dumps(
                                _st_dd, ensure_ascii=False
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
                        # [P0-7 单调] 同 partial_close：单调收紧，不直接赋值。
                        self._tighten_sl_unified(_remaining, _dd_action["new_sl"], "profit_stage_close", market=current_price)
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
                        # [P0-7 单调] 保本 SL 同样只朝有利方向推进。
                        self._tighten_sl_unified(pos, float(_new_sl), "breakeven_sl", market=current_price)
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
                            # [P0-7 单调] 收紧 SL 走单调守卫，防止回撤时把锁利位往下调。
                            self._tighten_sl_unified(pos, _dd_action["new_sl"], "profit_drawdown_tighten", market=current_price)
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
        # [2026-08-24 long_trend_v2] V2 长线仓跳过本层固定 TP 关闭（让利润奔跑）：
        # 合成 TP 仅作为 Layer-0 failsafe 由 _enforce_max_hold_timeout 兜底，
        # 正常止盈 = decide_long 的结构目标减半 + Chandelier 追踪。
        if pos.tp_price and not _v2_long_managed:
            hit_tp = (pos.side == "long" and current_price >= pos.tp_price) or \
                     (pos.side == "short" and current_price <= pos.tp_price)
            if hit_tp and self.tp_direction_illegal(pos, pos.tp_price, current_price):
                # [轮104] 脏 TP：拒绝按反向价成交
                hit_tp = False
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
        # [2026-08-24 long_trend_v2] V2 长线仓跳过 DSM（4h/短周期 ATR 追踪会震出周线趋势），
        # 追踪只认 decide_long 的周线 Chandelier 上移。
        try:
            try:
                from backend.config.settings import RISK_USE_NATURE_EXIT_ORCHESTRATOR as _use_peo
            except Exception:
                _use_peo = False
            if not _use_peo and not _v2_long_managed:
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
        self._enforce_min_sl(pos, entry, _nature, _pos_tier)

        # ── [调研轮15b 2026-09-16] 层上限随 tick 收窄（只夹"过远"一侧）──
        # `_enforce_min_sl` 只负责"别太紧"；本块负责"别太远"。二者同 tick 相邻执行，
        # 且下限已让位于上限（见 `_enforce_min_sl`），因此最终 SL 距离 = 上限。
        # 未配置 `MIDLONG_SL_MAX_PCT_<TIER>` 时 cap=0 ⇒ 本块完全不动（回滚位）；
        # 保本/盈利侧止损（long: SL>entry, short: SL<entry）永远不受影响。
        try:
            _sl2, _sl_clamped, _sl_why = self.clamp_sl_price(
                getattr(pos, "sl_price", None),
                side=getattr(pos, "side", ""),
                entry=entry,
                tier=_pos_tier,
            )
            if _sl_clamped:
                logger.info(
                    "[Paper] SL 距离超上限，随 tick 收窄 %s %s tier=%s: %s → %s（%s）",
                    getattr(pos, "symbol", "?"), getattr(pos, "side", ""),
                    _pos_tier, getattr(pos, "sl_price", None), _sl2, _sl_why,
                )
                pos.sl_price = _sl2
        except Exception as _sl_cap_err:
            logger.debug("[Paper] SL 上限收窄异常(非致命): %s", _sl_cap_err)

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

        if _v2_unified_on and self._should_run_unified_staged_tp(pos):
            # [2026-08-24 long_trend_v2] V2 长线仓跳过统一分段止盈/保本/硬软回撤/追踪
            # （中短线口径；设计 V2 §4.3.3-4.3.5 废除长线的 50% 进度推保本与分档 TP）。
            # [轮99] 这条"长线跳过"此前**从未生效** —— 见 `_is_trend_lane_member` 的说明。
            # [2026-08-22 M0-10] stale-decision 防护：profit_manager/DSM 的 result
            # 在 tick 开头基于旧尺寸/状态计算，而统一分段止盈可能在本 tick 已减仓/平仓；
            # 尺寸变化后继续应用旧 result 会造成同 tick 双重减仓。此处先记尺寸，
            # 统一块之后校验：仓位已平 → 直接返回；尺寸已变 → 丢弃 stale result。
            _size_before = float(getattr(pos, "size", 0) or 0)
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
            if str(getattr(pos, "status", "") or "").lower() != "open":
                self._peak_profit_cache.pop(pos_id, None)
                return True
            _size_after = float(getattr(pos, "size", 0) or 0)
            if _size_after != _size_before:
                logger.info(
                    f"[Paper][v2] {pos.symbol} 尺寸已由统一分段止盈变更 "
                    f"{_size_before:.6f}->{_size_after:.6f}，丢弃 stale profit_manager 结果 (M0-10)"
                )
                return False

        # v3 简化：不再用趋势分析覆盖 profit_manager 的平仓决策
        # profit_manager 的 TP 进度保护已经足够，不需要额外的回调判定层

        # 执行保护动作（仅当 _min_hold_ok 且 result 不 None 时）
        if result is None or result.action == "none":
            return False

        if result.action == "breakeven":
            if result.sl_price is not None:
                old_sl = float(pos.sl_price or 0)
                # [2026-08-22 PROFIT-1 底层出场改良] 保本推进升级为「峰值追踪优先」：
                # 有历史峰值时，SL = 保本线 与 峰值价±1.5% 的较优点（long 取更高 / short 取更低），
                # 让盈利单随峰值抬升而跑远，避免在微利处被固定保本线扫掉
                # （历史峰值保留率：scalp -701% / trend_follow 6.2% —— 利润几乎全回吐）。
                _sl = float(result.sl_price)
                _peak_price = self._peak_price_from_pos(pos, entry)
                if _peak_price > 0:
                    try:
                        _trail_pct = float(os.getenv("PAPER_PEAK_TRAIL_PCT", "0.015") or 0.015)
                    except Exception:
                        _trail_pct = 0.015
                    if str(getattr(pos, "side", "")).lower() == "long":
                        _cand = max(_sl, _peak_price * (1 - _trail_pct))
                    else:
                        _cand = min(_sl, _peak_price * (1 + _trail_pct))
                    if abs(_cand - _sl) > 1e-9:
                        logger.info(
                            f"[Paper][v2] 峰值追踪融合: {pos.symbol} {pos.side} "
                            f"保本→{_sl:.6f} 峰值追踪→{_cand:.6f} (PROFIT-1)"
                        )
                        _sl = _cand
                # [2026-09-18 幽灵峰值修复] 保护侧不变式：SL 若落在现价的错误一侧，
                # 下一 tick 必然触发，且会以 SL 价（而非市价）成交 → 幽灵成交。
                # 这里是 BNB #4715 事故的写入点，必须拦截。
                _sl = self.safe_sl_price(_sl, side=getattr(pos, "side", ""),
                                         market=float(current_price or 0),
                                         entry=float(getattr(pos, "entry_price", 0) or 0))
                if _sl <= 0:
                    logger.warning(
                        "[Paper][v2] 保本推进被保护侧不变式拦截：%s %s 目标SL=%s 现价=%s "
                        "（峰值可能已失真，跳过本次 SL 更新）",
                        getattr(pos, "symbol", "?"), getattr(pos, "side", ""),
                        getattr(result, "sl_price", None), current_price,
                    )
                    return False
                _side_p = str(getattr(pos, "side", "")).lower()
                if _side_p == "long" and _sl > old_sl:
                    pos.sl_price = _sl
                    pos.trailing_stop_price = None
                    logger.info(
                        f"[Paper][v2] 保本推进: {pos.symbol} {pos.side} "
                        f"SL→{_sl:.6f}")
                elif _side_p == "short" and (old_sl == 0 or _sl < old_sl):
                    pos.sl_price = _sl
                    pos.trailing_stop_price = None
                    logger.info(
                        f"[Paper][v2] 保本推进: {pos.symbol} {pos.side} "
                        f"SL→{_sl:.6f}")
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
            # [2026-08-22 M1-6] 单调守卫：锁利 SL 只许收紧（long 升 / short 降），
            # 回撤时不得反向放宽已锁利润位（原直接赋值 → 可能回摆）。
            if result.sl_price:
                # [2026-09-18] 保护侧不变式优先于单调守卫：落在现价错误一侧的 SL 直接拒绝，
                # 否则会在下一 tick 立刻触发并以该 SL 价幽灵成交（BNB #4715 事故）。
                _new_sl = self.safe_sl_price(result.sl_price, side=getattr(pos, "side", ""),
                                             market=float(current_price or 0),
                                             entry=float(getattr(pos, "entry_price", 0) or 0))
                _old_sl = float(getattr(pos, "sl_price", 0) or 0)
                _is_long = str(getattr(pos, "side", "long") or "long").lower() == "long"
                if _new_sl <= 0:
                    logger.warning(
                        "[Paper][v2] 分批锁利 SL 被保护侧不变式拦截：%s %s 目标SL=%s 现价=%s",
                        pos.symbol, pos.side, result.sl_price, current_price,
                    )
                elif _old_sl <= 0:
                    pos.sl_price = _new_sl
                elif _is_long and _new_sl > _old_sl:
                    pos.sl_price = _new_sl
                elif (not _is_long) and _new_sl < _old_sl:
                    pos.sl_price = _new_sl
                else:
                    logger.info(
                        f"[Paper][v2] {pos.symbol} partial_close 锁利 SL 回摆被单调守卫拦截 "
                        f"({_old_sl:.6f}→{_new_sl:.6f})"
                    )
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

        # exit_reason 列 VARCHAR(50)：长 reason（如 midlong:no_progress ...）曾触发
        # StringDataRightTruncation 导致复盘静默丢失；与主退出事件同一原则截断兜底。
        exit_reason = str(exit_reason or "")[:50]
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
                f"lev={getattr(pos, 'leverage', '')} "
                # [PostFill P3 2026-08-30] MFE/MAE/资金费入回顾负载：因子进化
                # 闭环据此区分"止损太紧"(MAE 浅仍被扫出) vs "方向错"(MAE 深)
                # 与资金费偏置归因。
                f"mfe={float(getattr(pos, 'peak_pnl_pct', 0) or 0):+.4f} "
                f"mae={float(getattr(pos, 'trough_pnl_pct', 0) or 0):+.4f} "
                f"funding_acc={self._funding_accrued_total(db, pos):+.4f}"
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

        # [2026-09-02] 移除原 D7 "因子衰减反馈"调用。它做的是
        #   decay_monitor.record_ic(f"strategy_{pos.symbol}", 0.05 if pnl>0 else -0.05)
        # 两处都错：① key 是策略/品种名而非因子 ID，衰减状态文件里因此只有
        # strategy_BTC 这类键，get_factor_weight_penalty(因子ID) 永远 miss、恒返回
        # 1.0，衰减惩罚从未对任何真实因子生效；② IC 是 ±0.05 占位常数，盈亏各半
        # 时 recent≈0 低于 retire_ic(0.01)，把这些假键全判成 dead/retire。
        # 单笔平仓算不出 IC（单点相关无定义）；真实 IC 现由
        # factor_ic_evaluator.run_factor_ic_evaluation 按因子聚合样本算出 Rank IC
        # 后直接喂给 decay_monitor.record_ic。

    def _notify_learning_on_close(
        self, db, pos, fill_price, pnl, reason,
        *, is_partial: bool = False, learning_weight: float = 1.0,
        close_fee: float = 0.0,
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
            # PaperPosition 无 metadata_json 列；开仓元数据在 exit_state_json.open_metadata
            _es_raw = getattr(pos, "exit_state_json", None) or "{}"
            _es_obj = _json.loads(_es_raw) if isinstance(_es_raw, str) else (_es_raw or {})
            if not isinstance(_es_obj, dict):
                _es_obj = {}
            _om = _es_obj.get("open_metadata")
            if isinstance(_om, dict):
                _pos_meta = dict(_om)
            # 顶层兜底字段（历史/补写可能挂在 exit_state 根上）
            for _k in ("thesis_id", "session_id", "entry_source", "timeframe_tier"):
                if _k not in _pos_meta and _es_obj.get(_k) is not None:
                    _pos_meta[_k] = _es_obj.get(_k)
            if "entry_source" not in _pos_meta and _es_obj.get("entry_source"):
                _pos_meta["entry_source"] = _es_obj.get("entry_source")
            # 兼容：若未来补回 metadata_json 列，仍可读
            _legacy = getattr(pos, "metadata_json", None)
            if _legacy and not _pos_meta.get("thesis_id"):
                try:
                    _leg = _json.loads(_legacy) if isinstance(_legacy, str) else (_legacy or {})
                    if isinstance(_leg, dict):
                        for _k, _v in _leg.items():
                            _pos_meta.setdefault(_k, _v)
                except Exception:
                    pass
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
                # [2026-08-28 实盘零成交修复] 熔断/学习按账户隔离所需
                "account_id": getattr(pos, "account_id", None),
                # [2026-08-29 全面修复 P1.6] 净盈亏口径：pnl(毛) - close_fee。
                # 下游（mid 熔断/复盘/胜率统计）按 net 判定，费拖型小赢不再算赢。
                "close_fee": float(close_fee or 0),
                "net_pnl": float(pnl or 0) - float(close_fee or 0),
                "adx_at_entry": round(adx_at_entry, 1),
                "trend_direction": trend_direction,
                "trend_strength": trend_strength,
                "leverage": float(pos.leverage or 1.0),
                "paper_position_id": getattr(pos, "id", None),
                # [P0-4] 快照关联：unified_learning 已有 snapshot_id 拷贝键（decision_context），
                # 平仓回写匹配到 DecisionSnapshot 后把唯一键带进学习数据，三方关联闭环。
                "snapshot_id": getattr(pos, "_matched_snapshot_id", None),
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
                "timeframe_tier": getattr(pos, "timeframe_tier", None) or tier,
                **({k: _pos_meta[k] for k in (
                    "agent_envelope", "agent_source", "alignment_score",
                    "cited_fact_ids", "cited_facts",
                    "thesis_id", "memory_event_ids", "hub_adjusted_at_entry",
                    "open_readiness", "session_id", "timeframe_tier",
                    "analysis_run_id", "entry_source",
                ) if k in _pos_meta}),
                **({
                    "thesis_id": (
                        _pos_meta.get("thesis_id")
                        or (_pos_meta.get("agent_envelope") or {}).get("thesis_id")
                    ),
                    "memory_event_ids": (
                        _pos_meta.get("memory_event_ids")
                        or (_pos_meta.get("agent_envelope") or {}).get("memory_event_ids")
                    ),
                    "hub_adjusted_at_entry": (
                        _pos_meta.get("hub_adjusted_at_entry")
                        or (_pos_meta.get("agent_envelope") or {}).get("hub_adjusted")
                    ),
                    "session_id": _pos_meta.get("session_id"),
                } if (
                    _pos_meta.get("thesis_id")
                    or (
                        isinstance(_pos_meta.get("agent_envelope"), dict)
                        and _pos_meta.get("agent_envelope").get("thesis_id")
                    )
                ) else {}),
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

            # [P0-4] 方向一致性 + 唯一性：同币种 48h 内多策略/重入场时，
            # 模糊匹配会把盈亏挂到错误决策。候选不唯一 → 记 ambiguous 并跳过（宁缺勿错）。
            #
            # [2026-09-06] VIRTUAL 平仓根因：开仓前 evaluate 会连写多条
            # executed=False 快照（同 strategy+symbol+buy），平仓匹配只看
            # pnl IS NULL → 把「未成交试单」和真成交搅在一起 → ambiguous 跳过。
            # 修复：优先只认 executed=True；多条真成交时用 opened_at 最近邻。
            _pos_side = str(getattr(pos, "side", "") or "").lower()
            _want_dir = (
                "buy" if _pos_side in ("long", "buy")
                else ("sell" if _pos_side in ("short", "sell") else None)
            )
            _opened_at = getattr(pos, "opened_at", None)

            def _pick_unique_snap(_raw_cands):
                if _want_dir:
                    _raw_cands = [
                        c for c in _raw_cands
                        if (getattr(c, "direction", "") or "") == _want_dir
                        or (getattr(c, "action", "") or "") == _want_dir
                    ]
                if not _raw_cands:
                    return None, 0
                _exec = [c for c in _raw_cands if bool(getattr(c, "executed", False))]
                _pool = _exec if _exec else []
                # 没有真成交快照 → 不拿试单顶替（宁缺勿错）
                if not _pool:
                    return None, len(_raw_cands)
                if len(_pool) == 1:
                    return _pool[0], 1
                if _opened_at is not None:
                    try:
                        def _dist(c):
                            ts = getattr(c, "timestamp", None)
                            if ts is None:
                                return 10**18
                            oa = _opened_at
                            # 统一为 naive/aware 可减
                            if getattr(ts, "tzinfo", None) and oa.tzinfo is None:
                                from datetime import timezone as _tz
                                oa = oa.replace(tzinfo=_tz.utc)
                            elif oa.tzinfo and getattr(ts, "tzinfo", None) is None:
                                ts = ts.replace(tzinfo=oa.tzinfo)
                            return abs((ts - oa).total_seconds())
                        _pool_sorted = sorted(_pool, key=_dist)
                        # 最近邻明显近于第二近（<15min 且领先 ≥60s）才采纳，否则仍 ambiguous
                        if _dist(_pool_sorted[0]) <= 15 * 60 and (
                            len(_pool_sorted) < 2
                            or _dist(_pool_sorted[1]) - _dist(_pool_sorted[0]) >= 60
                        ):
                            return _pool_sorted[0], len(_pool)
                    except Exception:
                        pass
                return None, len(_pool)

            # 策略 1: 精确匹配 strategy_id + symbol + 方向 + 时间窗口
            _strategy_id_for_match = strategy_id if strategy_id else ""
            if _strategy_id_for_match:
                _cands = _ana_db.query(DecisionSnapshot).filter(
                    DecisionSnapshot.strategy_id == _strategy_id_for_match,
                    DecisionSnapshot.symbol == pos.symbol,
                    DecisionSnapshot.pnl.is_(None),
                    DecisionSnapshot.timestamp >= _cutoff,
                ).order_by(DecisionSnapshot.timestamp.desc()).limit(8).all()
                _snap, _n = _pick_unique_snap(_cands)
                if not _snap and _n > 1:
                    logger.warning(
                        "[Paper] DecisionSnapshot 回写 ambiguous（%d 候选），跳过以防归因错配: %s %s",
                        _n, pos.symbol, _pos_side,
                    )

            # 策略 2: 模糊回退 — 同样优先 executed=True
            if not _snap:
                _cands = _ana_db.query(DecisionSnapshot).filter(
                    DecisionSnapshot.symbol == pos.symbol,
                    DecisionSnapshot.action.isnot(None),
                    DecisionSnapshot.pnl.is_(None),
                    DecisionSnapshot.timestamp >= _cutoff,
                ).order_by(DecisionSnapshot.timestamp.desc()).limit(8).all()
                _snap, _n = _pick_unique_snap(_cands)
                if not _snap and _n > 1:
                    logger.warning(
                        "[Paper] DecisionSnapshot 回退回写 ambiguous（%d 候选），跳过: %s %s",
                        _n, pos.symbol, _pos_side,
                    )

            if _snap:
                # [P0-4] 学习侧关联：记录匹配到的快照唯一键，供 TradeOutcome metadata 传递
                pos._matched_snapshot_id = (
                    getattr(_snap, "trace_id", None)
                    or getattr(_snap, "proposal_id", None)
                    or getattr(_snap, "id", None)
                )
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
                        live = self._get_mark_price(pos.symbol, exchange)
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
            # [2026-08-17] 固定排序：原查询无 ORDER BY，PostgreSQL 返回堆序，
            # 持仓行每次刷新(3s 轮询)顺序都会乱跳。按 id DESC(新开仓在前)固定行序。
            positions = db.query(PaperPosition).filter(
                PaperPosition.account_id == account_id,
                PaperPosition.status == status,
            ).order_by(PaperPosition.id.desc()).all()
            exchange = self._resolve_account_exchange(db, account_id)

            if status == "open":
                for pos in positions:
                    try:
                        live_price = self._get_mark_price(pos.symbol, exchange)
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
        """订单历史（含开仓价回退）。

        [2026-09-18 性能] 原实现对**该账户全部** `paper_positions` 做 ORM 实体物化，
        只为极少数缺 `entry_price` 的订单查一次开仓价。实测（账号 14）：
        3,124 行 → 一次调用 **84.5ms**（其中 SQL 25ms、SQLAlchemy 造 3,124 个对象约 60ms），
        而该端点被前端每 5s 轮询一次。改为：
          1. 先算出真正需要回退的订单（缺 entry_price 且回放推断不出）；
          2. 只为这些订单涉及的 `(symbol, side)` 组合取 **6 列投影**（不建实体）；
          3. 时间窗/策略匹配等判定仍由 `_resolve_entry_from_positions` 原样执行。
        口径不变：候选集是原候选集的**超集**判定等价（解析器只按 symbol+side 过滤）。
        """
        from backend.database.models import PaperOrder

        q = db.query(PaperOrder).filter(PaperOrder.account_id == account_id)
        if status:
            q = q.filter(PaperOrder.status == status)
        orders = q.order_by(PaperOrder.id.desc()).limit(limit).all()
        entry_fallback = self._build_entry_price_fallback(orders)

        # 先物化订单字典（与原实现同序、同次数），并挑出需要持仓候选的订单
        materialized: List[Tuple[Any, Dict]] = []
        need_candidates: List[Any] = []
        for o in orders:
            d = self._order_to_dict(o)
            materialized.append((o, d))
            if not d.get("entry_price") and not entry_fallback.get(o.id):
                need_candidates.append(o)

        candidates = (
            self._load_entry_price_candidates(db, account_id, need_candidates)
            if need_candidates else []
        )

        result = []
        for o, d in materialized:
            if not d.get("entry_price"):
                d["entry_price"] = (
                    entry_fallback.get(o.id)
                    or self._resolve_entry_from_positions(o, candidates)
                )
            result.append(d)
        return result

    def _load_entry_price_candidates(
        self, db: Session, account_id: int, orders: List[Any]
    ) -> List[Any]:
        """按 `(symbol, side)` 取开仓价回退所需的最小列集（不构造 ORM 实体）。

        - 只有**平仓单**（`close_reason` 非空）才可能用到持仓候选；
          其余订单 `_resolve_entry_from_positions` 直接返回 `filled_price`，无需候选。
        - 侧向映射沿用 `_position_side_from_close_order`，与解析器口径一致。
        - 不下推任何时间语义：`opened_at/closed_at` 的判断仍在解析器里原样执行。
        """
        from backend.database.models import PaperPosition

        pairs: set = set()
        for o in orders:
            if not getattr(o, "close_reason", None) or not getattr(o, "symbol", None):
                continue
            pairs.add((o.symbol, self._position_side_from_close_order(o.side)))
        if not pairs:
            return []

        symbols = sorted({s for s, _ in pairs})
        sides = sorted({sd for _, sd in pairs})
        rows = (
            db.query(
                PaperPosition.symbol,
                PaperPosition.side,
                PaperPosition.strategy_id,
                PaperPosition.entry_price,
                PaperPosition.opened_at,
                PaperPosition.closed_at,
            )
            .filter(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol.in_(symbols),
                PaperPosition.side.in_(sides),
            )
            .all()
        )
        # symbol+side 需精确配对，避免 (A, long) 与 (B, short) 交叉命中
        return [r for r in rows if (r.symbol, r.side) in pairs]


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

        # ── [PostFill §3.4.c 2026-08-31] 延长持仓硬条件补齐 ──
        # 设计原承诺：peak≥1R / 未破追踪线 / regime 未翻转 / 预计剩余持仓期
        # funding 成本 < 剩余浮盈 20%。此前只落地了短线禁令 + 3× 上限。
        # POSTFILL_EXTEND_HARD_GATE=false 可整体回滚（默认 true）。
        # 注：本模块无顶层 import os，须局部导入（否则 NameError 被吞 → 门恒开）。
        try:
            import os as _os_pf
            _hard_gate = str(_os_pf.getenv("POSTFILL_EXTEND_HARD_GATE", "true")).strip().lower() in (
                "1", "true", "yes", "on",
            )
        except Exception:
            _hard_gate = True
        if _hard_gate:
            _reject = self._postfill_extend_precondition(db, pos, additional_hours)
            if _reject:
                logger.info(f"[Paper] 延长持仓拒绝 {pos.symbol}: {_reject}")
                # [PostFill §3.7] 影子遥测：被硬条件拒绝的延长提案落事件流，
                # 事后对比"若放行"的假设 PnL（无 A/B 直接全开的补位）。
                self._record_postfill_telemetry(
                    db, pos, "extend_rejected",
                    {"reason": _reject, "additional_hours": float(additional_hours)},
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

        # [PostFill §3.7] 影子遥测：放行的延长一并落事件流，与 extend_rejected
        # 配对统计通过率与假设 PnL。
        self._record_postfill_telemetry(
            db, pos, "extend_granted",
            {
                "added_hours": round(new_h - before_max_h, 2),
                "after_max_hours": round(new_h, 2),
                "reason": reason,
            },
        )

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

    def _postfill_extend_precondition(
        self, db: Session, pos, additional_hours: float,
    ) -> Optional[str]:
        """[PostFill §3.4.c 2026-08-31] 延长持仓的 4 条硬条件，返回拒绝原因或 None。

        1. peak ≥ 1R：峰值浮盈 ≥ 单仓风险（|entry−SL|×size；无 SL 用 ATR 兜底）
        2. 未破追踪线：trailing_stop_price（无则 sl_price）未被现价击穿
        3. regime 未翻转：exit_state_json.regime（开仓时）≠ 当前快照 regime 即拒
        4. funding：预计延长段成本 |rate|×notional×h/8 < 剩余浮盈 20%

        缺数据（价格/费率/regime）时 fail-open（放行 + 跳过该条），有数据硬执行；
        任何异常不影响延长主链（返回 None 放行）。
        """
        try:
            try:
                _exchange = self._resolve_account_exchange(db, pos.account_id)
                _price = float(self._get_current_price(pos.symbol, _exchange) or 0)
            except Exception:
                _price = float(getattr(pos, "mark_price", 0) or 0)
            _side = str(getattr(pos, "side", "long") or "long").lower()
            _entry = float(getattr(pos, "entry_price", 0) or 0)
            _size = float(getattr(pos, "size", 0) or 0)
            _peak = float(getattr(pos, "peak_unrealized_pnl", 0) or 0)
            _upnl = float(getattr(pos, "unrealized_pnl", 0) or 0)

            # ── 1) peak ≥ 1R ──
            _sl = float(getattr(pos, "sl_price", 0) or 0)
            if _sl > 0 and _entry > 0:
                _risk = abs(_entry - _sl) * _size
            elif _entry > 0 and _size > 0:
                _atr = max(float(self._resolve_atr_pct(pos, _entry, _price) or 0), 0.005)
                _risk = _entry * _atr * _size
            else:
                _risk = 0.0
            if _risk > 0 and _peak < _risk:
                return f"peak ${_peak:.2f} < 1R ${_risk:.2f}"

            # ── 2) 未破追踪线 ──
            if _price > 0:
                _trail = float(getattr(pos, "trailing_stop_price", 0) or 0) or _sl
                if _trail > 0:
                    _broken = (
                        (_side in ("long", "buy") and _price <= _trail)
                        or (_side in ("short", "sell") and _price >= _trail)
                    )
                    if _broken:
                        return f"追踪线已破 (price={_price} trail={_trail})"

            # ── 3) regime 未翻转 ──
            try:
                import json as _json_pf
                _entry_regime = None
                _st = _json_pf.loads(getattr(pos, "exit_state_json", None) or "{}")
                if isinstance(_st, dict):
                    _entry_regime = _st.get("regime")
                _cur_regime = None
                try:
                    from backend.services.unified_data_pool import UnifiedDataPool
                    _snap = UnifiedDataPool().get_snapshot(max_age=300)
                    if _snap and pos.symbol in _snap.indicators:
                        _cur_regime = _snap.indicators[pos.symbol].get("regime")
                except Exception:
                    pass
                if _entry_regime and _cur_regime and str(_entry_regime) != str(_cur_regime):
                    return f"regime 翻转 {_entry_regime}→{_cur_regime}"
            except Exception:
                pass

            # ── 4) 预计剩余持仓期 funding 成本 < 剩余浮盈 20% ──
            try:
                _rate = 0.0
                try:
                    from backend.services.unified_data_pool import UnifiedDataPool
                    _snap = UnifiedDataPool().get_snapshot(max_age=300)
                    if _snap and pos.symbol in _snap.indicators:
                        _rate = float(_snap.indicators[pos.symbol].get("funding_rate", 0) or 0)
                except Exception:
                    pass
                if _rate != 0 and _price > 0 and _size > 0:
                    _est_cost = abs(_rate) * _price * _size * float(additional_hours) / 8.0
                    if _upnl <= 0:
                        return f"无剩余浮盈覆盖预计funding成本 ${_est_cost:.4f}"
                    if _est_cost >= 0.2 * _upnl:
                        return (
                            f"预计funding成本 ${_est_cost:.4f} ≥ 剩余浮盈20% "
                            f"(${0.2 * _upnl:.4f})"
                        )
            except Exception:
                pass
        except Exception as _pf_err:
            logger.debug(f"[Paper] 延长持仓前置条件检查异常(放行): {_pf_err}")
            return None
        return None

    def update_position_tp_sl(
        self, db: Session, position_id: int,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        sl_source: str = "",
    ) -> bool:
        """AI 主动调整指定持仓的 TP/SL 价位，返回是否成功

        [2026-09-18 轮96 修 Fix C] 新增 `sl_source`：标记这次止损是**谁**写进来的。
          · `"trailing"` = 追踪派生（tighten_trailing / trailing_lock / 保本推进）
            —— 这类止损会随行情一路贴近市价，本质是"锁利工具"，不是"保护工具"；
          · `"structural"` = 结构位（Chandelier / 初始 SL / 人工处置）；
          · `""`（默认）= 未标注，按结构位对待（保持既有行为）。

        为什么必须区分：追踪派生止损一旦被拉到成本之上，就成了**绕过 min_hold 的平仓通道** ——
        硬线成交不看最短持仓（`unified_exit_state_machine:10` 明示"硬事实直通不可拦截"），
        于是"72h 内不许主动平"的纪律被"把止损挪到 1% 之外"轻易绕开。
        实测 2026-09-18：4 笔 trend_follow 在 9.4–13.6h 内被此类止损全平。
        `reprice_position` 会据此在 min_hold 内**拒付**追踪派生止损（改回结构位）。
        """
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
            # [2026-09-19 轮104] 止盈侧不变式（`safe_sl_price` 的镜像）。
            # BTC #4712 滚仓后写入 tp=65689 而市价 80808（多头 TP 在市价下方 19%），
            # 4 秒后被秒级硬 TP 判定按这个**从未存在的价格**"止盈"平仓并虚记亏损。
            # 这里拒绝一切落在开仓价错误一侧的 TP；拒绝时**不 return**，
            # 以免把同一次调用里的 SL 调整一起吞掉。
            _side_tp = str(getattr(pos, "side", "long") or "long").lower()
            _mkt_tp = _as_float_or_none(getattr(pos, "mark_price", None)) or 0.0
            _safe_tp = self.safe_tp_price(
                tp_price, side=_side_tp, market=_mkt_tp,
                entry=_as_float_or_none(getattr(pos, "entry_price", None)) or 0.0)
            if _safe_tp <= 0 and _mkt_tp > 0:
                logger.error(
                    "[Paper] AI调整TP 被止盈侧不变式拦截（方向非法，拒绝写入）: "
                    "%s %s 目标TP=%.6f 市价=%s 开仓=%s",
                    pos.symbol, pos.side, float(tp_price or 0), _mkt_tp,
                    getattr(pos, "entry_price", None),
                )
            else:
                pos.tp_price = _safe_tp or tp_price
                changed = True
                logger.info(f"[Paper] AI调整TP: {pos.symbol} {pos.side} "
                            f"TP {old_tp}→{pos.tp_price}")
        if sl_price is not None:
            old_sl = pos.sl_price
            # [2026-08-27 止损失效修复] SL 只收紧不放宽：8/26-27 实测 UNI swing 的 SL
            # 被复查路径从 -4.5% 放宽到 -9%（4.1285→3.9359），行情急跌时越走越远，
            # 用户感知"止损失效"。默认拒绝放宽（MIDLONG_ALLOW_SL_WIDEN=true 回滚）。
            try:
                # [2026-08-31] 本模块无顶层 import os：原 os.environ 在此作用域
                # NameError 被 except 吞掉 → 回滚开关 MIDLONG_ALLOW_SL_WIDEN 形同虚设。
                # 局部导入恢复开关可用性（默认行为不变：拒绝放宽）。
                import os as _os_widen
                _allow_widen = str(_os_widen.environ.get("MIDLONG_ALLOW_SL_WIDEN", "false")).strip().lower() in ("1", "true", "yes", "on")
            except Exception:
                _allow_widen = False
            _cur_sl = float(old_sl or 0)
            _new_sl = float(sl_price or 0)
            _side_sl = str(getattr(pos, "side", "long") or "long").lower()
            _would_widen = (
                _cur_sl > 0 and _new_sl > 0 and (
                    (_side_sl in ("long", "buy") and _new_sl < _cur_sl)
                    or (_side_sl in ("short", "sell") and _new_sl > _cur_sl)
                )
            )
            if _would_widen and not _allow_widen:
                logger.warning(
                    "[Paper] 拒绝放宽SL: %s %s SL %.6f→%.6f（只收紧不放宽；"
                    "MIDLONG_ALLOW_SL_WIDEN=true 回滚）",
                    pos.symbol, pos.side, _cur_sl, _new_sl,
                )
            else:
                # [2026-09-18] 保护侧不变式：除 liq 内侧外，还必须在**现价**的止损侧。
                # 取价一律走 _as_float_or_none：市价不可用（None/Mock/垃圾值）时放行，
                # 不因为取数失败而阻断 AI 的止损调整。
                _mkt_u = _as_float_or_none(getattr(pos, "mark_price", None)) or 0.0
                _safe_sl = self.safe_sl_price(
                    sl_price, side=_side_sl, market=_mkt_u,
                    entry=_as_float_or_none(getattr(pos, "entry_price", None)) or 0.0)
                if _safe_sl <= 0 and _mkt_u > 0:
                    logger.warning(
                        "[Paper] AI调整SL 被保护侧不变式拦截: %s %s 目标=%.6f 市价=%s",
                        pos.symbol, pos.side, float(sl_price or 0), _mkt_u,
                    )
                    return False
                pos.sl_price = _safe_sl or sl_price
                # [P0-7 结构性加固] 所有 AI/紧急 SL 调整必须位于爆仓价内侧（含紧急 SL 路径），
                # 否则高杠杆下 SL 永不先触发。_ensure_sl_inside_liq 会自动把越界 SL 钳回 liq 内侧。
                try:
                    self._ensure_sl_inside_liq(pos)
                except Exception as _liq_err:
                    logger.debug(f"[Paper] update_tp_sl liq 守卫跳过: {_liq_err}")
                # [轮96 Fix C] 留痕：这次止损是谁写的（供 reprice_position 的 min_hold 拒付判定）
                self._stamp_sl_source(pos, sl_source, old_value=old_sl)
                changed = True
                logger.info(f"[Paper] AI调整SL: {pos.symbol} {pos.side} "
                            f"SL {old_sl}→{pos.sl_price}"
                            f"{f' source={sl_source}' if sl_source else ''}")

        if changed:
            self._sync_attached_orders(db, pos)
            db.commit()
            try:
                from backend.services.exchange.live_tpsl_sync import maybe_sync_live_tpsl
                maybe_sync_live_tpsl(db, pos, force=True)
            except Exception as _sync_err:
                logger.debug("[Paper] live tpsl sync skip: %s", _sync_err)
        return changed

    def _record_sl_defer_event(self, db, pos, info: dict) -> None:
        """[轮96 Fix C] 把「SL 在保护期内被拒付」登记成 `position_exit_events` 一行。

        为什么要落库而不是只打日志：这是一次**本该发生的平仓被系统主动拦下**，
        属于必须可审计的动作；日志会被轮转，事件表不会。
        """
        from backend.database.models import PositionExitEvent
        _ev = PositionExitEvent(
            position_id=int(getattr(pos, "id", 0) or 0),
            account_id=int(getattr(pos, "account_id", 0) or 0),
            strategy_id=getattr(pos, "strategy_id", None),
            symbol=getattr(pos, "symbol", ""),
            side=getattr(pos, "side", ""),
            trade_nature=getattr(pos, "trade_nature", None),
            event_type="sl_deferred_min_hold",
            price=float(getattr(pos, "mark_price", 0) or 0),
            pnl=float(getattr(pos, "unrealized_pnl", 0) or 0),
            close_ratio=0.0,
            peak_pnl_at_event=float(getattr(pos, "peak_unrealized_pnl", 0) or 0),
            peak_pnl_pct_at_event=float(getattr(pos, "peak_pnl_pct", 0) or 0),
            pnl_at_event=float(getattr(pos, "unrealized_pnl", 0) or 0),
            pnl_pct_at_event=self._position_pnl_pct(pos),
            exit_channel="sl_deferred",
            metadata_json=json.dumps(info or {}, ensure_ascii=False, default=str)[:4000],
        )
        db.add(_ev)
        db.commit()

    # ── 定时更新（供 scheduler 调用）────────────────

    # ════════════════════════════════════════════════════════════════════════
    # [2026-09-18 轮96 修 Fix C] 追踪派生止损的**来源留痕** + min_hold 内拒付
    # ════════════════════════════════════════════════════════════════════════
    SL_META_KEY = "sl_meta"

    @classmethod
    def _stamp_sl_source(cls, pos, source: str, *, old_value=None) -> None:
        """把「这次止损是谁写的」写进 `exit_state_json.sl_meta`（失败不影响交易）。"""
        if not source:
            return
        try:
            import json as _json

            from backend.utils.db_datetime import utc_now_for_db
            _raw = getattr(pos, "exit_state_json", None)
            _es = _json.loads(_raw) if isinstance(_raw, str) and _raw.strip() else (
                _raw if isinstance(_raw, dict) else {}
            )
            _es = dict(_es or {})
            _es[cls.SL_META_KEY] = {
                "source": str(source)[:32],
                "value": float(getattr(pos, "sl_price", 0) or 0),
                "prev": float(old_value or 0),
                "set_at": utc_now_for_db().isoformat(),
            }
            pos.exit_state_json = _json.dumps(_es, ensure_ascii=False)
        except Exception as _se:
            logger.debug("[Paper] sl_meta 留痕失败(不影响交易): %s", _se)

    @staticmethod
    def _min_lock_profit_pct(tier: str) -> float:
        """车道「最小锁定利润」：拒付追踪派生止损时，回退位不得让已锁利润低于它。

        [轮96 Fix C] 见 `settings.MIDLONG_MIN_LOCK_PROFIT_PCT_*` 的说明。
        读不到配置时按车道默认（long 2.5% / mid 0.5% / short 0）。
        """
        try:
            from backend.config import settings as _st
            _v = getattr(_st, f"MIDLONG_MIN_LOCK_PROFIT_PCT_{str(tier or '').upper()}", None)
            if _v is not None:
                return max(0.0, float(_v))
        except Exception:
            pass
        return {"long": 0.025, "mid": 0.005}.get(str(tier or "").lower(), 0.0)

    @staticmethod
    def sl_meta_of(pos) -> dict:
        """读回 `sl_meta`；读不到返回空 dict（= 按结构位对待）。"""
        try:
            import json as _json
            _raw = getattr(pos, "exit_state_json", None)
            _es = _json.loads(_raw) if isinstance(_raw, str) and _raw.strip() else (
                _raw if isinstance(_raw, dict) else {}
            )
            _m = (_es or {}).get("sl_meta")
            return _m if isinstance(_m, dict) else {}
        except Exception:
            return {}

    def _sl_min_hold_verdict(self, pos, current_price: float) -> tuple:
        """判断「这笔止损该不该在最短持仓期内被拒付」。

        返回 `(defer: bool, info: dict)`；`defer=True` 表示**不要成交**。

        规则（全部满足才拒付，任何一条不满足都按正常止损成交）：
          1. 该止损的来源标记为 `trailing`（追踪派生）；
          2. 持仓时长 < 本车道 `TIER_PROTECTION_PARAMS[tier].min_hold_sec`；
          3. 该止损位于**盈利区**（多头在成本之上）—— 亏损失效的止损永远直接成交；
          4. 未触及保护期内的紧急亏损阈值 `min_hold_emergency_loss_pct`；
          5. 存在可回退的**结构位**（`exit_state_json.structural_stop_price`）——
             否则回退后下一 tick 会再次触发、形成死循环，故此时照常成交。

        失败方向：任何异常/信息缺失一律 `defer=False`（照常止损）。
        真实止损绝不因为"状态读不出来"而被挡住 —— 这是保护侧该有的方向。
        """
        info = {"defer": False, "why": ""}
        try:
            meta = self.sl_meta_of(pos)
            if str(meta.get("source") or "") != "trailing":
                info["why"] = "非追踪派生止损"
                return False, info

            tier = str(getattr(pos, "timeframe_tier", None)
                       or ("long" if str(getattr(pos, "trade_nature", "") or "").lower()
                           in ("trend_follow", "position") else "mid")).strip().lower()
            from backend.config.settings import TIER_PROTECTION_PARAMS
            _tp = TIER_PROTECTION_PARAMS.get(tier) or {}
            min_hold = float(_tp.get("min_hold_sec") or 0)
            if min_hold <= 0:
                info["why"] = f"tier={tier} 无保护期"
                return False, info

            from backend.utils.db_datetime import db_dt_for_age
            from datetime import datetime as _dt, timezone as _tz
            opened = db_dt_for_age(getattr(pos, "opened_at", None))
            if opened is None:
                info["why"] = "无开仓时间"
                return False, info
            held_sec = (_dt.now(_tz.utc) - opened).total_seconds()
            if held_sec >= min_hold:
                info["why"] = f"已过保护期 ({held_sec/3600:.1f}h ≥ {min_hold/3600:.1f}h)"
                return False, info

            entry = float(getattr(pos, "entry_price", 0) or 0)
            side = str(getattr(pos, "side", "") or "").lower()
            is_long = side in ("long", "buy")
            if entry <= 0 or current_price <= 0:
                info["why"] = "无入场价/市价"
                return False, info
            # 条件 3：止损在盈利区（多头：市价仍在成本之上；空头对称）
            in_profit = (current_price > entry) if is_long else (current_price < entry)
            if not in_profit:
                info["why"] = "止损位于亏损区（按真实止损成交）"
                return False, info

            # 条件 4：紧急亏损阈值（保证金口径），与 unified_exit_state_machine 的保护层同口径
            _emg = float(_tp.get("min_hold_emergency_loss_pct") or 0)
            margin = float(getattr(pos, "margin", 0) or 0)
            upnl = float(getattr(pos, "unrealized_pnl", 0) or 0)
            if _emg > 0 and margin > 0 and upnl < 0:
                loss_pct = abs(upnl) / margin * 100.0
                if loss_pct >= _emg:
                    info["why"] = f"触及紧急亏损 {loss_pct:.2f}% ≥ {_emg}%"
                    return False, info

            # 条件 5：必须能回退到结构位
            try:
                import json as _json
                _raw = getattr(pos, "exit_state_json", None)
                _es = _json.loads(_raw) if isinstance(_raw, str) and _raw.strip() else (
                    _raw if isinstance(_raw, dict) else {}
                )
                _structural = float((_es or {}).get("structural_stop_price") or 0)
            except Exception:
                _structural = 0.0
            if _structural <= 0:
                info["why"] = "无结构位可回退（防死循环，按止损成交）"
                return False, info
            # 回退位 = max(结构位, 入场×(1+最小锁定利润))（多头；空头对称）。
            # 只用结构位是不够的：E1 的 Chandelier 会长期在入场价之下，
            # 直接回退等于把已锁定的利润全部还回去（与"放回结构位但保留锁利"的要求冲突）。
            _min_lock = self._min_lock_profit_pct(tier)
            if _min_lock > 0 and entry > 0:
                _lock_sl = entry * (1 + _min_lock) if is_long else entry * (1 - _min_lock)
                _restore = max(_structural, _lock_sl) if is_long else min(_structural, _lock_sl)
                # 锁利地板必须仍在市价的止损侧，否则会被下一次 tick 立刻触发
                if is_long and _restore >= current_price:
                    _restore = _structural
                if (not is_long) and _restore <= current_price:
                    _restore = _structural
            else:
                _restore = _structural
            _structural = _restore
            # 结构位必须在市价的止损侧，否则回退无意义（可能立即再次触发）
            if is_long and _structural >= current_price:
                info["why"] = "结构位在市价错误一侧"
                return False, info
            if (not is_long) and _structural <= current_price:
                info["why"] = "结构位在市价错误一侧"
                return False, info

            info.update({
                "defer": True, "why": "min_hold 内追踪派生止损拒付",
                "tier": tier, "held_hours": round(held_sec / 3600.0, 2),
                "min_hold_hours": round(min_hold / 3600.0, 1),
                "structural_stop": _structural, "sl": float(getattr(pos, "sl_price", 0) or 0),
                "peak_pnl_pct": float(getattr(pos, "peak_pnl_pct", 0) or 0),
            })
            return True, info
        except Exception as _ve:
            logger.debug("[Paper] min_hold 拒付判定异常(按正常止损成交): %s", _ve)
            return False, {"defer": False, "why": f"判定异常: {_ve}"}

    def update_single_position(self, db: Session, pos) -> None:
        """单持仓短事务更新 — 减少锁持有时间，避免连接池耗尽。

        scheduler 将每个持仓拆为独立 Session 调用此方法，
        而非旧版 update_all_positions 的一个大事务。
        """
        from backend.database.models import PaperBalance

        _sl0 = float(getattr(pos, "sl_price", 0) or 0)
        _tp0 = float(getattr(pos, "tp_price", 0) or 0)
        try:
            exchange = self._resolve_account_exchange(db, pos.account_id)
            current_price = self._get_mark_price(pos.symbol, exchange)
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
        # [Phase E 2026-08-30] v1 三张参数表已删除，nature 合法性改用显式集合。
        _explicit_nature = getattr(pos, "trade_nature", None)
        if _explicit_nature and _explicit_nature in self._VALID_NATURES:
            _nature = _explicit_nature
        else:
            _raw_tier = getattr(pos, "timeframe_tier", None) or "swing"
            _nature = self._TIER_TO_NATURE.get(_raw_tier, _raw_tier)
            if _nature not in self._VALID_NATURES:
                _nature = "swing"

        # ── 利润保护（v2 唯一路径；v1 已于 Phase E 删除，显式回退只告警跳过）──
        from backend.config.settings import PROFIT_PROTECTION_VERSION
        if PROFIT_PROTECTION_VERSION == "v2":
            should_continue = self._run_v2_protection(db, pos, entry, current_price, profit_pct, _nature)
        else:
            logger.warning(
                "[Paper] PROFIT_PROTECTION_VERSION!=v2：v1 已删除(Phase E)，本 tick 跳过利润保护"
            )
            should_continue = False

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
        _changed = (
            abs(float(getattr(pos, "sl_price", 0) or 0) - _sl0) > 1e-12
            or abs(float(getattr(pos, "tp_price", 0) or 0) - _tp0) > 1e-12
        )

        # 更新余额
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == pos.account_id).first()
        if bal:
            self._recalc_balance(db, bal)

        db.commit()
        try:
            from backend.services.exchange.live_tpsl_sync import maybe_sync_live_tpsl
            maybe_sync_live_tpsl(db, pos, force=_changed)
        except Exception as _sync_err:
            logger.debug("[Paper] live tpsl sync skip: %s", _sync_err)

    def reprice_position(self, db: Session, pos) -> None:
        """秒级快速定价：只更新 mark_price/unrealized + 硬性 TP/SL 触发，
        不做波动率分类/追踪止盈等重保护逻辑（由慢速 full tick 负责）。
        """
        from backend.database.models import PaperBalance
        try:
            exchange = self._resolve_account_exchange(db, pos.account_id)
            current_price = self._get_mark_price(pos.symbol, exchange)
        except RuntimeError:
            return

        pos.mark_price = current_price
        pos.unrealized_pnl = self._calc_unrealized_pnl(
            pos.entry_price, current_price, pos.size, pos.side
        )

        hit = False
        reason = "sl"
        if pos.sl_price and float(pos.sl_price) > 0:
            hit = (pos.side == "long" and current_price <= float(pos.sl_price)) or \
                  (pos.side == "short" and current_price >= float(pos.sl_price))
            reason = self.sl_reason_for_position(pos, current_price) if hit else "sl"
        if not hit and pos.tp_price and float(pos.tp_price) > 0:
            # ── [2026-09-19 轮104] 秒级快路径：趋势车道不做定点止盈 ──
            # 这条路径正是 BTC #4712 的**行刑者**（滚仓后 4.4 秒按 65689.43 成交）。
            # 慢速 tick 的 `_run_v2_protection` 早已按车道跳过本层固定 TP
            # （见 L4266 注释「V2 长线仓跳过本层固定 TP 关闭」），
            # 但秒级快路径**从未**继承这条契约 —— 与轮99 同型的"契约没落到真正触发的路径上"。
            # 车道契约（`trend_e1_engine._adopt_position` / `ExitPolicy.for_lane("long").tp_pct=None`）：
            # 长线仓不设固定 TP，唯一出场 = 规则失效 / Chandelier；Layer-0 failsafe 仍在
            # `_enforce_max_hold_timeout`（慢速 tick）里兜底。
            if self._is_trend_lane_member(
                    pos, getattr(pos, "timeframe_tier", None),
                    getattr(pos, "trade_nature", None)):
                self._log_trend_lane_skip_once(pos, True)
            else:
                hit = (pos.side == "long" and current_price >= float(pos.tp_price)) or \
                      (pos.side == "short" and current_price <= float(pos.tp_price))
                # [轮104] 触发侧不变式：方向非法的 TP 绝不成交，光靠下面的 min/max
                # 成交价修正救不了 —— 多头取 min(tp, mkt) 恰好选中那个反向的 tp 本身
                # （65689 < 80808），"修正"反而确认了幽灵价。唯一正确处置是拒绝成交。
                if hit and self.tp_direction_illegal(pos, pos.tp_price, current_price):
                    hit = False
                reason = "tp"
        if hit:
            _px_attr = "sl_price" if reason in ("sl", "breakeven_sl") else "tp_price"
            # ── [2026-09-18 轮96 修 Fix C] min_hold 内拒付「追踪派生」止损 ──
            # 硬线成交不看最短持仓（`unified_exit_state_machine:10`：硬事实直通不可拦截），
            # 于是"把止损收紧到成本上方 1%"就成了绕过 72h 纪律的平仓通道。
            # 2026-09-18 实测：4 笔 trend_follow 在 9.4–13.6h 内以此方式全平。
            # 处理：若是追踪派生止损、且仍在保护期内、且止损位于盈利区 ⇒ 不成交，
            # 把止损回退到**结构位**（Chandelier 等），让仓位继续按设计持有。
            # 真实止损（亏损区/结构性/紧急）一律照常成交 —— 见 _sl_min_hold_verdict。
            if _px_attr == "sl_price":
                _defer, _dinfo = self._sl_min_hold_verdict(pos, current_price)
                if _defer:
                    logger.warning(
                        "[Paper][Fast] SL 拒付（min_hold 内追踪派生止损）: %s %s "
                        "held=%.2fh < %.1fh, SL=%.6f → 回退结构位 %.6f (%s)",
                        pos.symbol, pos.side, _dinfo.get("held_hours", 0.0),
                        _dinfo.get("min_hold_hours", 0.0), _dinfo.get("sl", 0.0),
                        _dinfo.get("structural_stop", 0.0), _dinfo.get("why", ""),
                    )
                    try:
                        self._stamp_sl_source(pos, "structural",
                                              old_value=_dinfo.get("sl"))
                        pos.sl_price = float(_dinfo.get("structural_stop") or 0)
                        db.commit()
                    except Exception as _rb:
                        logger.warning("[Paper][Fast] SL 回退结构位失败: %s", _rb)
                    try:
                        self._record_sl_defer_event(db, pos, _dinfo)
                    except Exception as _dve:
                        logger.debug("[Paper] SL 拒付事件登记失败: %s", _dve)
                    # 仓位仍在册（浮动盈亏刚被更新过），本 tick 的余额重算不能省
                    try:
                        _bal_d = db.query(PaperBalance).filter(
                            PaperBalance.account_id == pos.account_id).first()
                        if _bal_d:
                            self._recalc_balance(db, _bal_d)
                            db.commit()
                    except Exception as _bde:
                        logger.debug("[Paper][Fast] SL 拒付后余额重算跳过: %s", _bde)
                    return
            # [轮97] 止损成交必须让人一眼分清"锁利"还是"保护"：
            # 同一条 SL 线在成本之上时是锁利型（回吐成交、pnl>0 → reason 被改写
            # 成 breakeven_tp），在成本之下时才是保护型。此前日志只写 "SL 触发"，
            # 运维无法分辨"盈利单为什么按止损出"。
            _sk_log = self.stop_kind(pos) if _px_attr == "sl_price" else ""
            _sk_pct = self.stop_vs_entry_pct(pos) if _sk_log else None
            _sk_txt = ""
            if _sk_log == "profit_lock":
                _sk_txt = f" [锁利型止损 止损位在成本{_sk_pct:+.2f}%处]"
            elif _sk_log == "protective":
                _sk_txt = f" [保护型止损 止损位在成本{_sk_pct:+.2f}%处]"
            logger.info(
                f"[Paper][Fast] {reason.upper()} 触发: {pos.symbol} {pos.side} "
                f"@{current_price} {_px_attr}={getattr(pos, _px_attr, 0)}{_sk_txt}"
            )
            # [2026-09-18 幽灵成交修复] 成交价不得优于市价：正常情况 SL 在市价的止损侧
            # （多头 SL ≤ 现价），用 SL 价成交是「成交在该线」的正常语义；但若 SL 落在
            # 现价错误一侧（#4715 事故），用 SL 价成交就凭空造出一个不存在的价格。
            # 这里强制用「市价 / 触发线」中对持仓不利的那个，保证成交价永远是可成交的。
            _trigger_px = float(getattr(pos, _px_attr) or 0)
            _mkt = float(current_price or 0)
            if _trigger_px > 0 and _mkt > 0:
                _is_long_pos = str(getattr(pos, "side", "")).lower() in ("long", "buy")
                _fill = min(_trigger_px, _mkt) if _is_long_pos else max(_trigger_px, _mkt)
                if abs(_fill - _trigger_px) > 1e-9:
                    logger.warning(
                        "[Paper][Fast] %s %s 触发线 %s 落在市价 %s 的错误一侧，"
                        "成交价按市价修正（防幽灵成交）",
                        pos.symbol, pos.side, _trigger_px, _mkt,
                    )
            else:
                _fill = _trigger_px or _mkt
            self.close_position(
                db, pos.account_id, pos.symbol, pos.side,
                reason=reason,
                strategy_id=getattr(pos, "strategy_id", None),
                fill_price_override=_fill,
            )
            # ── [PostFill §3.5 2026-08-31] 硬线全平补登 ExitSource 事件 ──
            self._record_hard_line_exit_source(db, pos, reason)
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
        factor_exposures: Any = None,
        strategy_id: str = "",
    ) -> None:
        """M10 样本仓库：独立会话写 trade_facts（隔离失败不影响主事务）。"""
        # [2026-08-31 测试污染根治] 本函数用自己的生产 SessionLocal，单元测试
        # （in-memory SQLite）跑 close_position 时会把假成交（entry=100 等夹具
        # 数据）写进生产 trade_facts——实测污染 26 行，毒化学习/校准/IC 管线。
        # pytest 环境直接跳过（测试断言不依赖本仓库）。
        import os as _os_tf
        if _os_tf.environ.get("PYTEST_CURRENT_TEST"):
            return
        try:
            from sqlalchemy import text as _sa_text
            from backend.database.connection import SessionLocal as _ArenaLocal
            with _ArenaLocal() as _db:
                # [2026-09-03 v3 F2c] 建表/放宽列宽/补列统一交给 ledger.trade_facts_reconcile
                # （幂等、进程内只跑一次）。根因：close_reason VARCHAR(64) 被中长线
                # 120 字符出场原因撑爆 → INSERT 静默失败 → 近 7 天 10 笔 mid 仓无样本。
                from backend.services.ledger.trade_facts_reconcile import ensure_trade_facts_schema
                ensure_trade_facts_schema(_db)
                import json as _json
                _fx_json = None
                if factor_exposures:
                    try:
                        _fx_json = _json.dumps(factor_exposures, ensure_ascii=False)
                    except Exception:
                        _fx_json = None
                _db.execute(_sa_text(
                    "INSERT INTO trade_facts "
                    "(source, account_id, position_id, symbol, tier, side, entry_price, "
                    " exit_price, fees, pnl, outcome, close_reason, factor_exposures, strategy_id) "
                    "VALUES ('paper', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r, "
                    " CAST(:fx AS JSONB), :sid)"
                ), {
                    "a": int(account_id), "p": position_id, "s": str(symbol).upper()[:32],
                    "t": str(tier or "short")[:8], "d": str(side or "")[:8],
                    "e": float(entry_price or 0), "x": float(exit_price or 0),
                    "f": float(fees or 0), "pnl": float(pnl or 0),
                    "o": str(outcome or "")[:16], "r": str(close_reason or "")[:200],
                    "fx": _fx_json if _fx_json else "null",
                    "sid": str(strategy_id or "")[:64],
                })
                _db.commit()
        except Exception as _tf_err:
            # [2026-09-03 v3 F2c] 学习样本丢失必须可见：warning + 计数器（ops 巡检读取），
            # 日对账任务（trade_facts_reconcile）会把漏掉的样本按同口径补回。
            PaperTradingEngine._TRADE_FACT_WRITE_FAILURES += 1
            logger.warning(
                "[Paper] trade_fact 落库失败(累计 %d) pos=%s %s: %s",
                PaperTradingEngine._TRADE_FACT_WRITE_FAILURES, position_id, symbol, _tf_err,
            )

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
                current_price = self._get_mark_price(pos.symbol, exchange)
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
            # [Phase E 2026-08-30] v1 三张参数表已删除，nature 合法性改用显式集合。
            _explicit_nature = getattr(pos, "trade_nature", None)
            if _explicit_nature and _explicit_nature in self._VALID_NATURES:
                _nature = _explicit_nature
            else:
                _raw_tier = getattr(pos, "timeframe_tier", None) or "swing"
                _nature = self._TIER_TO_NATURE.get(_raw_tier, _raw_tier)
                if _nature not in self._VALID_NATURES:
                    _nature = "swing"

            # ── 利润保护（v2 唯一路径；v1 已于 Phase E 删除，显式回退只告警跳过）──
            from backend.config.settings import PROFIT_PROTECTION_VERSION
            if PROFIT_PROTECTION_VERSION == "v2":
                should_continue = self._run_v2_protection(db, pos, entry, current_price, profit_pct, _nature)
            else:
                logger.warning(
                    "[Paper] PROFIT_PROTECTION_VERSION!=v2：v1 已删除(Phase E)，本 tick 跳过利润保护"
                )
                should_continue = False
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

    @staticmethod
    def _funding_accrued_total(db: Session, pos) -> float:
        """[PostFill P1-2] 该持仓累计资金费（正=收入, 负=支出）。

        从 PaperFundingLedger 按 position_id 聚合；无结算记录返回 0。
        持仓监视/延长持仓决策用它扣除资金费成本。
        """
        try:
            from backend.database.models import PaperFundingLedger
            pid = int(getattr(pos, "id", 0) or 0)
            if not pid:
                return 0.0
            rows = db.query(PaperFundingLedger.payment).filter(
                PaperFundingLedger.position_id == pid,
            ).all()
            return float(sum(float(r[0] or 0) for r in rows))
        except Exception:
            return 0.0

    def _maybe_settle_funding(self, db: Session, pos, current_price: float) -> None:
        """Research 模式下按周期结算资金费率。仅 demo/research 有意义时调用。

        简化规则：
        - 每 FUNDING_SETTLE_INTERVAL_SEC 秒结算一次
        - funding_rate > 0 时多头付费给空头，反之亦然
        - payment = notional * rate，直接计入 PaperBalance.realized_pnl
        """
        # [P0-8] 默认档也结算资金费（不再绑定 research 档）：
        # 中期持仓跨 8h 结算点，此前 demo 档 PnL 从不含资金费，与
        # funding_net_rr_ok 入场闸门的成本假设脱节，学习闭环在无资金费偏置的 PnL 上自训。
        from backend.config.settings import FUNDING_SETTLE_ENABLED, FUNDING_SETTLE_INTERVAL_SEC
        if not FUNDING_SETTLE_ENABLED:
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

        # 写入 ledger（[P0-8] 带 timeframe_tier 供口径审计）
        entry = PaperFundingLedger(
            account_id=pos.account_id,
            position_id=pos.id,
            symbol=pos.symbol,
            side=pos.side,
            notional=notional,
            funding_rate=funding_rate,
            payment=payment,
            tier=getattr(pos, "timeframe_tier", None) or None,
            settled_at=now,
        )
        db.add(entry)

        # 更新余额（[P0-8] dry-run 观察期：FUNDING_SETTLE_APPLY_PNL=false 时仅记账不动净值）
        from backend.config.settings import FUNDING_SETTLE_APPLY_PNL
        from backend.database.models import PaperBalance
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == pos.account_id).first()
        if bal and FUNDING_SETTLE_APPLY_PNL:
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
        """检查挂单是否触发。

        [2026-09-07] 先快照 pending id，结束只读事务，再按单取价/成交。
        旧实现：SELECT pending 后事务开着，_get_current_price 走 DC/REST
        可达数十秒 → LeakGuard 点名 check_pending_orders idle-in-transaction。
        """
        from backend.database.models import PaperOrder, PaperBalance
        from backend.database.connection import release_idle_txn
        from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
        from backend.services.exchange.paper_exchange_simulator import (
            PaperMarketState,
            simulate_exchange_order,
        )

        pending = db.query(PaperOrder).filter(PaperOrder.status == "pending").all()
        snaps = [
            {
                "id": int(o.id),
                "symbol": str(o.symbol or ""),
                "side": str(o.side or ""),
                "order_type": str(o.order_type or "").lower(),
                "price": float(o.price) if o.price is not None else None,
                "quantity": float(o.quantity or 0),
                "leverage": float(o.leverage or 1),
                "account_id": int(o.account_id),
                "exchange": getattr(o, "exchange", None),
            }
            for o in pending
        ]
        release_idle_txn(db, where="check_pending_orders.pre_price")
        for snap in snaps:
            try:
                # 轻量 stub：_resolve_order_exchange 需要 order 对象
                order_stub = type("O", (), snap)()
                exchange = self._resolve_order_exchange(db, order_stub)
                current_price = self._get_current_price(snap["symbol"], exchange)
            except RuntimeError:
                continue
            except Exception as _px_err:
                logger.debug("[Paper] pending 取价跳过 id=%s: %s", snap["id"], _px_err)
                continue

            order_type = snap["order_type"]
            trigger = False
            if order_type == "limit":
                sim_result = simulate_exchange_order(
                    exchange=exchange,
                    order=ExchangeOrder(
                        order_id=f"paper_{snap['id']}",
                        symbol=snap["symbol"],
                        side=OrderSide.BUY if snap["side"] == "buy" else OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        size=float(snap["quantity"] or 0),
                        price=float(snap["price"] or 0),
                        leverage=int(round(float(snap["leverage"] or 1))),
                    ),
                    market=PaperMarketState(
                        symbol=snap["symbol"],
                        mark_price=float(current_price),
                        bid=float(current_price),
                        ask=float(current_price),
                    ),
                    resting_limit=True,
                )
                trigger = sim_result.status.value == "filled"
            elif order_type == "stop_market" and snap["price"]:
                if snap["side"] == "buy" and current_price >= snap["price"]:
                    trigger = True
                elif snap["side"] == "sell" and current_price <= snap["price"]:
                    trigger = True
            if not trigger:
                continue

            order = db.query(PaperOrder).filter(
                PaperOrder.id == snap["id"], PaperOrder.status == "pending",
            ).first()
            if not order:
                continue
            bal = db.query(PaperBalance).filter(
                PaperBalance.account_id == order.account_id,
            ).first()
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
    def _calc_liquidation_price(entry_price: float, side: str, leverage: float,
                                exchange: Optional[str] = None) -> float:
        """估算爆仓价（简化版逐仓）。

        [P2-3] 维持保证金率按交易所取值（原全局 MAINTENANCE_MARGIN_RATE 0.005，
        与 fee_schedule_service 的 per-exchange 真相源矛盾，各所爆仓价偏移 ~0.1%）。
        """
        if leverage <= 1:
            return 0.0
        mm = MAINTENANCE_MARGIN_RATE
        if exchange:
            try:
                from backend.services.fee_schedule_service import get_maint_margin_rate
                _ex_mm = get_maint_margin_rate(exchange)
                if _ex_mm and _ex_mm > 0:
                    mm = float(_ex_mm)
            except Exception:
                pass
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

        [2026-08-22 M0-2] 账户存在性校验：accounts 无此 id 时拒绝自动建余额
        （历史上该机制允许"幽灵账户"凭空获得资金池并开仓）。
        """
        from backend.database.models import PaperBalance, Account
        # [2026-08-22 M1-1] 行所有权=账户 user_id：创建余额前设置租户上下文
        self._set_tenant_from_account(db, account_id)
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == account_id).first()
        if not bal:
            acct = db.query(Account).filter(Account.id == account_id).first()
            if not acct:
                logger.error(
                    f"[PaperEngine] 拒绝为不存在的账户 {account_id} 自动创建余额 "
                    f"(资金池必须有对应的 accounts.id)"
                )
                return None
            import os, traceback
            default_balance = float(os.getenv("PAPER_DEFAULT_BALANCE", "10000"))
            try:
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

        # [2026-09-03 v3 F2e] 软重置语义：last_reset_at 之后的订单/资金费才计入权益。
        # 此前 recalc 无条件汇总全历史订单 → reset_balance_only 会在下一次 recalc 被
        # 还原（账户 14 初始 500 → 权益 203 与全历史一致，重置形同虚设）。
        # 重置前开的仓位：保证金照常占用；其平仓 pnl 落在重置后的订单里，自然计入。
        _reset_at = getattr(bal, "last_reset_at", None)
        _since_filters = []
        if _reset_at is not None:
            _since_filters.append(PaperOrder.created_at >= _reset_at)

        # 订单的已实现 PnL（新数据: 每个订单独立记录；旧数据: 全平订单含累计）
        order_rpnl = float(db.query(func.coalesce(func.sum(PaperOrder.pnl), 0)).filter(
            PaperOrder.account_id == bal.account_id,
            PaperOrder.pnl.isnot(None),
            *_since_filters,
        ).scalar() or 0)

        # 订单手续费（开仓+平仓均有）
        order_fees = float(db.query(func.coalesce(func.sum(PaperOrder.fee), 0)).filter(
            PaperOrder.account_id == bal.account_id,
            PaperOrder.fee.isnot(None),
            *_since_filters,
        ).scalar() or 0)

        # [2026-08-22 M1-2] funding 覆盖竞争修复：_maybe_settle_funding 会把资金费
        # 计入 realized_pnl，但此处 _recalc_balance 无条件把它重置回 SUM(order.pnl)
        # → 资金费盈亏在"orders-only 与 orders+funding"间震荡。
        # 修复：funding 收支独立从 paper_funding_ledger 读取，再并入 realized/equity。
        _funding_pnl = 0.0
        try:
            from backend.config.settings import FUNDING_SETTLE_APPLY_PNL as _apply_pnl
            if _apply_pnl:
                from backend.database.models import PaperFundingLedger
                _fq = db.query(func.coalesce(func.sum(PaperFundingLedger.payment), 0)).filter(
                    PaperFundingLedger.account_id == bal.account_id
                )
                if _reset_at is not None:
                    _fq = _fq.filter(PaperFundingLedger.settled_at >= _reset_at)
                _funding_pnl = float(_fq.scalar() or 0)
        except Exception as _fund_err:
            logger.debug(f"[Paper] funding 并入 recalc 跳过: {_fund_err}")

        bal.realized_pnl = order_rpnl + _funding_pnl
        bal.total_fee_paid = order_fees
        bal.frozen_margin = total_margin
        bal.unrealized_pnl = total_upnl
        bal.available_balance = bal.initial_balance + order_rpnl + _funding_pnl - order_fees - total_margin
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
