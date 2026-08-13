"""ScalpFactorRouter — 短线因子路由器（2026-06-18 三层架构）。

替代 DirectionAgent 对 scalp/intraday 的处理。
从现有因子引擎取信号，纯规则决策，延迟 <100ms（不调 LLM）。

设计原则：
- 因子是主，AI 是辅：因子分数 >=70 直通执行，50-70 可选 LLM 确认，<50 不交易
- 复用现有 factor_engine / market_flow / fee_guard，不重写因子计算
- 遵守 QAA 架构：作为 master_controller handler 的子路由，不绕过 QAA
- 遵守现有仓位管理：输出汇入同一套 paper_engine / sub_position_manager
- 2026-06-27：独立 APScheduler job（fullauto_scalp_*），与 AI 主循环锁解耦
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 配置（从 settings 读，有默认值）
# 门槛校准：因子引擎 direction∈[-1,+1] 是保守方向系数，0.3-0.4 即有效信号。
# 历史默认 50/70 基于 score=|dir|×str×100（二次惩罚），几乎不可达，导致全天无交易。
# 2026-06-20 修正：score 改为 |dir|×100（一次项），门槛同步下调。
# 2026-06-27：探索 30 / 直通 40（独立调度后需与当前市场 score 分布对齐）
from backend.config.settings import (
    SCALP_FACTOR_CONFIRM_THRESHOLD as _SCALP_CONFIRM_THRESHOLD,
    SCALP_FACTOR_EXECUTE_THRESHOLD as _SCALP_EXECUTE_THRESHOLD,
)
_SCALP_USE_LLM_CONFIRM = os.getenv("SCALP_USE_LLM_CONFIRM", "false").lower() == "true"

# [2026-08-13 P2-12] 山寨币交易过滤：打分币宇宙（FACTOR_SCORER_SYMBOLS）外的币种
# 需「显式迁移白名单」或「该币独立实盘结算记录达标」才允许交易。
# 实证：FARTCOIN -0.457%/LIT -1.18%/PUMP -0.479%，山寨币亏损最重且因子可迁移性未验证。
# 回滚：SCALP_SCORE_UNIVERSE_ONLY=0|false|off。
_SCALP_UNIVERSE_ONLY = (os.getenv("SCALP_SCORE_UNIVERSE_ONLY", "1") or "1").strip().lower() in (
    "1", "true", "yes", "on",
)
_UNIVERSE_CACHE_SEC = 60.0   # 每币过滤结果缓存秒数（避免每 tick 查库）
_universe_cache: Dict[str, tuple] = {}


def _symbol_universe_allowed(symbol: str) -> tuple:
    """返回 (allowed, note)：山寨币宇宙过滤判定（TTL 缓存）。"""
    sym = (symbol or "").strip().upper()
    if not sym:
        return False, "empty_symbol"
    _now = time.time()
    _cached = _universe_cache.get(sym)
    if _cached and _now - _cached[0] < _UNIVERSE_CACHE_SEC:
        return _cached[1]
    if not _SCALP_UNIVERSE_ONLY:
        return True, "universe_filter_off"
    try:
        from backend.config.settings import FACTOR_SCORER_SYMBOLS
        _universe = {s.strip().upper() for s in str(FACTOR_SCORER_SYMBOLS).split(",") if s.strip()}
    except Exception:
        _universe = {"BTC", "ETH", "SOL"}
    if sym in _universe:
        result = (True, "score_universe")
        _universe_cache[sym] = (_now, result)
        return result
    # 显式迁移白名单（仅 BTC/ETH/SOL 训练因子的迁移白名单）
    _allowlist = {
        s.strip().upper()
        for s in (os.getenv("SCALP_ALTCOIN_MIGRATION_ALLOWLIST", "") or "").split(",")
        if s.strip()
    }
    if sym in _allowlist:
        result = (True, "migration_allowlist")
        _universe_cache[sym] = (_now, result)
        return result
    # 该币独立实盘结算记录达标（样本外证据代理）：
    # 已结算 ≥ SCALP_ALTCOIN_MIN_SETTLED 且胜率 ≥ SCALP_ALTCOIN_MIN_WINRATE。
    try:
        _min_n = max(50, int(os.getenv("SCALP_ALTCOIN_MIN_SETTLED", "200") or 200))
        _min_wr = float(os.getenv("SCALP_ALTCOIN_MIN_WINRATE", "0.42") or 0.42)
    except (TypeError, ValueError):
        _min_n, _min_wr = 200, 0.42
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ScalpSignalLog
        from sqlalchemy import func as _sa_func
        _db = SessionLocal()
        try:
            _n = _db.query(_sa_func.count(ScalpSignalLog.id)).filter(
                ScalpSignalLog.symbol == sym,
                ScalpSignalLog.settled == True,  # noqa: E712
                ScalpSignalLog.win.isnot(None),
            ).scalar() or 0
            if _n < _min_n:
                result = (False, f"altcoin_no_oos_evidence:{_n}<{_min_n}")
            else:
                _wins = _db.query(_sa_func.count(ScalpSignalLog.id)).filter(
                    ScalpSignalLog.symbol == sym,
                    ScalpSignalLog.settled == True,  # noqa: E712
                    ScalpSignalLog.win == True,  # noqa: E712
                ).scalar() or 0
                _wr = _wins / _n if _n else 0.0
                if _wr >= _min_wr:
                    result = (True, f"altcoin_oos_wr:{_wr:.2f}")
                else:
                    result = (False, f"altcoin_wr_too_low:{_wr:.2f}<{_min_wr}")
        finally:
            _db.close()
    except Exception as exc:
        logger.debug("[ScalpRouter] 山寨币过滤检查失败(默认拦截): %s", exc)
        result = (False, "altcoin_filter_error")
    _universe_cache[sym] = (_now, result)
    return result


@dataclass
class ScalpSignal:
    """短线因子路由器的输出信号。"""
    action: str = "hold"          # buy / sell / hold
    confidence: int = 0           # 0-100
    factor_score: int = 0         # 因子总分
    direction: str = "neutral"    # long / short / neutral
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    factor_breakdown: Dict[str, float] = field(default_factory=dict)
    source: str = "factor_router"
    reasoning: str = ""


class ScalpFactorRouter:
    """短线因子路由器 — 单例。"""

    _instance: Optional["ScalpFactorRouter"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_scalp_nature(self, nature: str) -> bool:
        """判断是否是短线 nature。"""
        return (nature or "").lower() in ("scalp", "intraday")

    def evaluate(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        mode: str = "paper",
    ) -> ScalpSignal:
        """从因子引擎取信号，按阈值决策。

        Args:
            symbol: 交易对（如 BTC）
            market_data: 该 symbol 的市场数据（含因子信号、K线、订单流）
            mode: paper/live（Paper 样本期放宽自适应门槛与微结构硬拦）

        Returns:
            ScalpSignal: 决策结果
        """
        if not market_data or not symbol:
            return ScalpSignal(reasoning="无数据")
        # [2026-08-13 P2-12] 山寨币宇宙过滤：打分币外的币种需白名单/实盘证据，
        # 否则直接 hold（诊断：山寨币亏损最重且因子可迁移性未验证）。
        _uni_ok, _uni_note = _symbol_universe_allowed(symbol)
        if not _uni_ok:
            return ScalpSignal(action="hold", reasoning=f"山寨币过滤({_uni_note})，不交易")
        _mode_l = (mode or "paper").strip().lower()
        if not _mode_l:
            _mode_l = str((market_data or {}).get("trading_mode") or "paper").strip().lower()
        _is_paper = _mode_l == "paper"

        # 1. 从 market_data 提取因子信号
        factor_score, direction, breakdown = self._extract_factor_signal(symbol, market_data)

        # 1.45 清算磁吸独立开仓信号（2026-07-07）：
        # [升级E 2026-07-12] 禁用磁吸独立开仓——实测18笔胜率17%、亏$2.69。
        # 磁吸信号只用于保护性平仓（scalp_loop._check_liq_magnet_reversal_exit），
        # 不再作为开仓方向来源。设置 SCALP_LIQ_MAGNET_OPEN_DISABLED=false 可恢复。
        import os as _os_e
        if _os_e.getenv("SCALP_LIQ_MAGNET_OPEN_DISABLED", "true").lower() not in ("1", "true", "yes"):
            if direction == "neutral":
                try:
                    from backend.services.crypto_alpha_signals import crypto_alpha
                    _lm_seed = crypto_alpha.liquidation_magnet(symbol)
                    if _lm_seed.available and _lm_seed.severity == "high" and _lm_seed.direction != "neutral":
                        direction = _lm_seed.direction
                        _seed_score = min(100, _SCALP_CONFIRM_THRESHOLD + 5)
                        factor_score = max(factor_score, _seed_score)
                        breakdown["liq_magnet_seed"] = _seed_score
                        breakdown["liq_magnet_seed_note"] = _lm_seed.note
                except Exception as e:
                    logger.debug(f"[ScalpRouter] {symbol} 磁吸独立开仓信号计算失败(安全降级): {e}")

        # 1.5 币圈原生 alpha 加成（清算磁吸/订单簿失衡共振 → 进攻加分）
        # 这是币圈永续合约独有的 alpha，传统 RSI/MACD 没有。详见 crypto_alpha_signals.py
        crypto_bonus, crypto_reason = self._apply_crypto_alpha_offense(symbol, direction)
        if crypto_bonus > 0:
            factor_score = min(100, factor_score + crypto_bonus)
            breakdown["crypto_alpha"] = crypto_bonus
            if crypto_reason:
                breakdown["crypto_reason"] = crypto_reason

        # 1.6 趋势软加分（2026-06-26：顺势短线加分，不强制周期一致性）
        # 如果长线趋势和短线方向同向 → +5 分（鼓励顺势短线）
        # 反向 → 不变（不拦截，短线有自由判断能力）
        trend_bonus = self._apply_trend_boost(symbol, direction, market_data)
        if trend_bonus > 0:
            factor_score = min(100, factor_score + trend_bonus)
            breakdown["trend_boost"] = trend_bonus

        # 1.65 AI概率引擎融合（2026-07-06：接入 cycle_direction_probability short tier）
        # 把训练好但短线从未用过的周期方向概率引擎（统计模型，非LLM，<1ms查表）按
        # 校准质量加权融合进因子分数，给短线补一层"AI第二意见"。默认开启，
        # SCALP_FUSION_ENABLED=false 可秒回滚到改动前行为。详见 scalp_fusion_scorer.py。
        try:
            from backend.services.scalp.scalp_fusion_scorer import scalp_fusion_scorer
            _fusion = scalp_fusion_scorer.compute_fusion_adjustment(
                direction, market_data.get("klines_15m"),
            )
            if _fusion.delta:
                factor_score = max(0, min(100, factor_score + _fusion.delta))
            if _fusion.breakdown:
                breakdown.update(_fusion.breakdown)
        except Exception as e:
            logger.debug(f"[ScalpRouter] AI概率融合失败(安全降级): {e}")

        # [2026-08-13 P0-2] score≥70 高分段历史胜率条件（校准不达标则封顶 69，
        # 不再凭高分拿高仓位待遇——实证高分桶胜率 36.5% 低于保本线）。
        try:
            from backend.services.scalp.scalp_score_calibration import high_score_cap
            _capped, _cap_note = high_score_cap(factor_score)
            if _cap_note:
                factor_score = _capped
                breakdown["high_score_gate"] = _cap_note
        except Exception:
            pass

        # 1.7 动态胜率门槛（2026-06-26：根据该币种历史表现自适应门槛）
        adaptive_threshold = self._get_adaptive_threshold(symbol, is_paper=_is_paper)

        if factor_score < adaptive_threshold:
            return ScalpSignal(
                action="hold", factor_score=factor_score,
                direction=direction, factor_breakdown=breakdown,
                reasoning=f"因子分数{factor_score}<{adaptive_threshold}，不交易",
            )

        # [restored 2026-07-08] 重新启用微观结构过滤（阶段一 1.3），flag 门控。
        # 只对"强反向证据"一票否决（清算簇 high 反向 / CVD 强对立 / 挂单强对立），
        # 由 SCALP_MICRO_GUARD_STRICT 控制严格度；EV 闸门做主，这里只拦最明显的逆势接刀。
        # 之所以恢复：2026-07-01 拆掉后短线在瀑布中逆势接刀的亏损明显增多。
        try:
            from backend.config.settings import (
                SCALP_MICROSTRUCTURE_GUARD_ENABLED as _MICRO_ON,
            )
        except Exception:
            _MICRO_ON = True
        if _MICRO_ON:
            _micro_ok, _micro_reason = self._check_microstructure(symbol, market_data, direction)
            if not _micro_ok:
                _paper_soft = False
                _soft_pen = 8
                if _is_paper:
                    try:
                        from backend.config.settings import (
                            PAPER_SCALP_MICRO_SOFT,
                            PAPER_SCALP_MICRO_SOFT_PENALTY,
                        )
                        _paper_soft = bool(PAPER_SCALP_MICRO_SOFT)
                        _soft_pen = int(PAPER_SCALP_MICRO_SOFT_PENALTY or 8)
                    except Exception:
                        _paper_soft = True
                if _paper_soft:
                    # Paper：对立证据降为扣分，仍允许开仓（仓位由下游 Gate 缩）
                    factor_score = max(0, int(factor_score) - max(0, _soft_pen))
                    breakdown["micro_soft"] = _micro_reason
                    breakdown["micro_soft_penalty"] = _soft_pen
                    logger.info(
                        "[ScalpRouter] %s Paper微结构软放行 -%d: %s",
                        symbol, _soft_pen, _micro_reason,
                    )
                else:
                    return ScalpSignal(
                        action="hold", factor_score=factor_score,
                        direction=direction, factor_breakdown=breakdown,
                        reasoning=f"微观结构拦截: {_micro_reason}",
                    )

        # 2. 计算入场价/SL/TP
        price = float(market_data.get("price", 0) or market_data.get("mark_price", 0) or 0)
        if price <= 0:
            return ScalpSignal(reasoning="无有效价格")

        sl_pct, tp_pct = self._compute_sl_tp(market_data, direction=direction, price=price)

        # [restored 2026-07-08] 重新启用手续费守卫（阶段一 1.3），flag 门控。
        # 便宜的早筛：TP 连来回手续费的 3x 都覆盖不了的信号直接 hold，不必再往下算。
        # 与下游 EV 闸门（完整期望值）互补：这里只看费用覆盖，EV 看含胜率的净期望。
        if _MICRO_ON:
            try:
                _ex = str(market_data.get("exchange") or "") if isinstance(market_data, dict) else ""
                if not self._fee_guard_passes(tp_pct, sl_pct, price=price, symbol=symbol, exchange=_ex):
                    return ScalpSignal(
                        action="hold", factor_score=factor_score,
                        direction=direction, factor_breakdown=breakdown,
                        reasoning=f"手续费守卫: TP {tp_pct:.2%} 无法覆盖往返成本",
                    )
            except Exception as _fg_err:
                logger.debug(f"[ScalpRouter] {symbol} 手续费守卫跳过: {_fg_err}")

        # 3. 按阈值决策
        action = "buy" if direction == "long" else "sell" if direction == "short" else "hold"
        if action == "hold":
            return ScalpSignal(action="hold", factor_score=factor_score, reasoning="方向中性")

        # score >= EXECUTE(45) → 直通；CONFIRM(25)-EXECUTE → 探索（模拟盘鼓励）
        if factor_score >= _SCALP_EXECUTE_THRESHOLD:
            _src = "factor_direct"
            _reason = f"因子直通(score={factor_score}≥{_SCALP_EXECUTE_THRESHOLD})"
        elif _SCALP_USE_LLM_CONFIRM:
            # 可选 LLM 确认（这里预留接口，实际 LLM 调用在 ScalpAgent）
            _src = "factor_llm_confirmed"
            _reason = f"因子+LLM确认(score={factor_score})"
        else:
            # 不用 LLM 确认时，50-70 区间也放行（模拟盘鼓励探索）
            _src = "factor_explore"
            _reason = f"因子探索(score={factor_score}≥{_SCALP_CONFIRM_THRESHOLD})"

        try:
            from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
            side = "long" if action == "buy" else "short"
            sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
                market_data, side=side, entry=price,
            )
        except Exception:
            sl_price = price * (1 - sl_pct) if action == "buy" else price * (1 + sl_pct)
            tp_price = price * (1 + tp_pct) if action == "buy" else price * (1 - tp_pct)

        logger.info(
            f"[ScalpRouter] {symbol} {action} score={factor_score} dir={direction} "
            f"entry={price:.2f} sl={sl_pct:.2%} tp={tp_pct:.2%} [{_src}]"
        )

        return ScalpSignal(
            action=action, confidence=factor_score,
            factor_score=factor_score, direction=direction,
            entry_price=price, sl_price=sl_price, tp_price=tp_price,
            sl_pct=sl_pct, tp_pct=tp_pct,
            factor_breakdown=breakdown,
            source=_src, reasoning=_reason,
        )

    @staticmethod
    def _calibrate_score(raw_dir: float) -> int:
        """因子方向值 → 校准后的 score（0-100）。

        原问题：direction z-score 归一化后集中在 0.8-1.0 → score 82-100 无区分度。
        修复：sigmoid 类非线性映射，让 0.8-1.0 映射到 55-80（需要多因子共振才能到 80+）。
        """
        import math
        abs_d = abs(raw_dir)
        # sigmoid 映射：abs_d=0→0, 0.3→32, 0.5→50, 0.8→68, 1.0→76
        score = int(100 / (1 + math.exp(-8 * (abs_d - 0.4))))
        return max(0, min(80, score))  # 硬上限 80（留 81-100 给共振加成）

    def _extract_factor_signal(self, symbol: str, market_data: Dict) -> tuple:
        """从 market_data 提取因子信号。

        优先用 factor_engine handler 算好的复合信号；
        回退到 market_data 里的 technical indicators 做简单合成。
        """
        breakdown = {}
        score = 0
        direction = "neutral"

        # 优先：QAA factor_engine handler 的输出（修复 BUG B：键名 factor_v3）
        factor_signal = (
            market_data.get("factor_signal")
            or market_data.get("composite_signal")
            or market_data.get("factor_v3")  # V3 流水线的实际键名
        )
        if factor_signal and isinstance(factor_signal, dict):
            _dir = float(factor_signal.get("direction", 0) or 0)
            # 校准后的 score（非线性映射，消除 82-100 集中问题）
            score = self._calibrate_score(_dir)
            direction = "long" if _dir > 0.15 else "short" if _dir < -0.15 else "neutral"
            breakdown["composite"] = score
            breakdown["raw_dir"] = round(_dir, 3)
            return score, direction, breakdown

        # 回退1：多周期因子引擎共振（2026-06-26 升级：5m+15m 双周期）
        # 原：只用 5m 单周期调因子引擎，信号噪声大
        # 新：5m+15m 双周期，共振时 score×1.3，矛盾时 score×0.5
        klines_5m = market_data.get("klines")
        if klines_5m is not None and hasattr(klines_5m, '__len__') and len(klines_5m) > 20:
            try:
                from backend.services.factor_engine.base_factors import factor_engine
                from backend.services.factor_engine.factor_evaluation_pipeline import factor_pipeline
                import pandas as pd

                klines_5m_df = klines_5m if isinstance(klines_5m, pd.DataFrame) else pd.DataFrame(klines_5m)
                # [fix] 传 timeframe 给因子引擎，z-score 归一化按 symbol+timeframe 隔离
                _md_5m = dict(market_data) if isinstance(market_data, dict) else {"symbol": symbol}
                _md_5m.setdefault("timeframe", "5m")
                # 与 scalp_loop 缓存路径共用排除集与精选白名单，避免缓存命中/回退重算口径分裂
                from backend.services.scalp.scalp_factor_exclude import (
                    get_scalp_factor_exclude_categories,
                    get_scalp_factor_allowlist,
                )
                _excl = get_scalp_factor_exclude_categories()
                _allow = get_scalp_factor_allowlist()
                factor_values_5m = factor_engine.compute_all_factors(
                    klines_5m_df, _md_5m, exclude_categories=_excl, allowlist=_allow
                )

                if factor_values_5m:
                    composite_5m = factor_pipeline.compute_weighted_signals(factor_values_5m, _md_5m)
                    if composite_5m is not None:
                        score_5m = self._calibrate_score(composite_5m.direction)
                        dir_5m = "long" if composite_5m.direction > 0.15 else (
                            "short" if composite_5m.direction < -0.15 else "neutral")

                        # 尝试加载 15m K线做共振确认
                        dir_15m = None
                        score_15m = 0
                        try:
                            klines_15m = market_data.get("klines_15m")
                            if klines_15m and hasattr(klines_15m, '__len__') and len(klines_15m) > 20:
                                klines_15m_df = klines_15m if isinstance(klines_15m, pd.DataFrame) else pd.DataFrame(klines_15m)
                                _md_15m = dict(market_data) if isinstance(market_data, dict) else {"symbol": symbol}
                                _md_15m["timeframe"] = "15m"  # [fix] z-score 按 15m 隔离
                                factor_values_15m = factor_engine.compute_all_factors(
                                    klines_15m_df, _md_15m, exclude_categories=_excl, allowlist=_allow
                                )
                                if factor_values_15m:
                                    composite_15m = factor_pipeline.compute_weighted_signals(factor_values_15m, _md_15m)
                                    if composite_15m is not None:
                                        score_15m = self._calibrate_score(composite_15m.direction)
                                        dir_15m = "long" if composite_15m.direction > 0.15 else (
                                            "short" if composite_15m.direction < -0.15 else "neutral")
                        except Exception:
                            pass

                        # 多周期共振融合
                        if dir_15m is not None and dir_15m != "neutral":
                            if dir_15m == dir_5m:
                                # 共振：同向 → 加成
                                score = int(score_5m * 1.3)
                                direction = dir_5m
                                breakdown = {
                                    "composite_5m": score_5m,
                                    "composite_15m": score_15m,
                                    "resonance": "+30%",
                                }
                            else:
                                # 矛盾：反向 → 削弱
                                score = int(score_5m * 0.5)
                                direction = dir_5m  # 以 5m 为主
                                breakdown = {
                                    "composite_5m": score_5m,
                                    "composite_15m": score_15m,
                                    "resonance": "-50%(矛盾)",
                                }
                        else:
                            # 无 15m 数据 → 只用 5m
                            score = score_5m
                            direction = dir_5m
                            breakdown = {"composite_5m": score_5m, "resonance": "无15m确认"}
                        return score, direction, breakdown
            except Exception as e:
                logger.debug(f"[ScalpRouter] 多周期因子引擎失败: {e}")

        # 回退2：无 K线数据时的最简合成（保持向后兼容）
        indicators = market_data.get("indicators", {}) if isinstance(market_data, dict) else {}
        rsi = float(indicators.get("rsi", 50) or 50)
        macd = float(indicators.get("macd", 0) or 0)
        ema_trend = float(indicators.get("ema_trend", 0) or 0)

        # RSI 反向（超卖看多、超买看空）
        rsi_score = max(-50, min(50, (50 - rsi) * 2))
        # MACD 顺势
        macd_score = max(-30, min(30, macd * 100))
        # EMA 顺势
        ema_score = max(-20, min(20, ema_trend * 20))

        total = rsi_score + macd_score + ema_score
        score = min(100, abs(total))
        direction = "long" if total > 15 else "short" if total < -15 else "neutral"
        breakdown = {"rsi": rsi_score, "macd": macd_score, "ema": ema_score}

        return score, direction, breakdown

    def _apply_trend_boost(self, symbol: str, direction: str, market_data: Dict) -> int:
        """趋势软加分（不强制周期一致性）。

        长线趋势和短线方向同向时 +5 分（鼓励顺势短线）。
        反向时不加不减（短线有自由判断能力，不被惩罚）。
        趋势来源：market_data 里的 orchestrator 评估或 4h EMA 趋势。

        Returns:
            加分（0 或 5）
        """
        if direction == "neutral":
            return 0
        try:
            # 优先从 market_data 的 orchestrator 评估获取长线方向
            _orch = market_data.get("orchestrator") or {}
            long_bias = _orch.get("long_bias") or _orch.get("long_view_bias") or ""
            if not long_bias:
                # 回退：从 4h indicators 获取 EMA 趋势
                _ind_4h = market_data.get("indicators_4h") or {}
                long_bias = _ind_4h.get("ema_trend", "")

            if long_bias and long_bias != "mixed" and long_bias != "neutral":
                _trend_dir = "long" if "bullish" in long_bias else "short" if "bearish" in long_bias else ""
                if _trend_dir == direction:
                    logger.debug(f"[ScalpRouter] {symbol} 趋势软加分: 短线{direction} 长线{_trend_dir} 同向 +5")
                    return 5
        except Exception:
            pass
        return 0

    def _get_adaptive_threshold(self, symbol: str, is_paper: bool = True) -> int:
        """动态胜率门槛：按币种近期胜率自适应 + 分数-胜率校准门槛上提（P0-2）。"""
        base = self._adaptive_threshold_inner(symbol, is_paper)
        # [2026-08-13 P0-2] 校准门槛只升不降：历史分桶胜率决定的保本门槛优先
        try:
            from backend.services.scalp.scalp_score_calibration import effective_threshold
            return int(effective_threshold(base))
        except Exception:
            return base

    def _adaptive_threshold_inner(self, symbol: str, is_paper: bool = True) -> int:
        """动态胜率门槛（原有逻辑，见 _get_adaptive_threshold docstring）。

        胜率 < 30%（连亏币）→ 门槛提高（Live 50；Paper 有上限，默认 38）
        胜率 > 60%（表现好）→ 门槛降到 CONFIRM-5（最低 25）
        中间 → 用默认阈值 CONFIRM

        Returns:
            该 symbol 的开仓门槛
        """
        try:
            from backend.database.connection import SessionLocal
            from sqlalchemy import text
            db = SessionLocal()
            try:
                row = db.execute(text("""
                    SELECT count(*),
                           count(*) FILTER (WHERE unrealized_pnl > 0)
                    FROM paper_positions
                    WHERE status='closed' AND trade_nature='scalp'
                    AND symbol = :sym
                    AND closed_at >= NOW() - INTERVAL '3 days'
                """), {"sym": symbol.upper()}).fetchone()
                n = int(row[0] or 0)
                if n < 5:
                    # 样本不足 → 用默认阈值
                    return _SCALP_CONFIRM_THRESHOLD
                wins = int(row[1] or 0)
                wr = wins / n
                if wr < 0.30:
                    _loss_th = 50
                    if is_paper:
                        try:
                            from backend.config.settings import PAPER_SCALP_ADAPTIVE_LOSS_CEILING
                            # Paper 样本期：不抬到 50，避免 36–49 分整批饿死
                            _loss_th = max(
                                int(_SCALP_CONFIRM_THRESHOLD),
                                min(50, int(PAPER_SCALP_ADAPTIVE_LOSS_CEILING or 38)),
                            )
                        except Exception:
                            _loss_th = 38
                    logger.info(
                        f"[ScalpRouter] {symbol} 近{n}笔胜率{wr:.0%}<30% → 门槛{_loss_th}"
                        f"{'(paper)' if is_paper else '(live)'}"
                    )
                    return _loss_th
                elif wr > 0.60:
                    _lo = max(25, _SCALP_CONFIRM_THRESHOLD - 5)
                    logger.debug(f"[ScalpRouter] {symbol} 近{n}笔胜率{wr:.0%}>60% → 门槛降低到{_lo}")
                    return _lo
                return _SCALP_CONFIRM_THRESHOLD
            finally:
                db.close()
        except Exception:
            return _SCALP_CONFIRM_THRESHOLD

    def _apply_crypto_alpha_offense(self, symbol: str, direction: str) -> tuple:
        """币圈原生 alpha 进攻加分。

        清算磁吸/订单簿失衡与因子方向共振时给因子分加成。这是币圈永续合约独有
        的 alpha，传统 RSI/MACD 等技术指标完全没有。详见 crypto_alpha_signals.py。

        Returns:
            (bonus, reason): bonus=加分(0-20), reason=说明(可空)
        """
        if direction == "neutral":
            return 0, ""
        try:
            from backend.services.crypto_alpha_signals import crypto_alpha
            bundle = crypto_alpha.get_bundle(symbol)
            bonus = 0
            reasons = []

            # 清算磁吸共振（最强信号）：方向一致且 severity=high/medium
            lm = bundle.liquidation_magnet
            if lm.available and lm.direction == direction and lm.strength > 0:
                lm_bonus = 12 if lm.severity == "high" else 6
                bonus += lm_bonus
                reasons.append(f"清算磁吸共振({lm.severity})+{lm_bonus}")

            # 订单簿失衡共振
            obi = bundle.orderbook_imbalance
            if obi.available and obi.direction == direction and obi.strength > 0.3:
                obi_bonus = 8
                bonus += obi_bonus
                reasons.append(f"挂单失衡共振+{obi_bonus}")

            # CVD 共振
            cvd = bundle.cvd_pressure
            if cvd.available and cvd.direction == direction and cvd.strength > 0.4:
                cvd_bonus = 5
                bonus += cvd_bonus
                reasons.append(f"CVD共振+{cvd_bonus}")

            return min(20, bonus), ";".join(reasons)
        except Exception as e:
            logger.debug(f"[ScalpRouter] crypto_alpha_offense 失败: {e}")
            return 0, ""

    def _check_microstructure(self, symbol: str, market_data: Dict, direction: str = "neutral") -> tuple:
        """微观结构过滤（防守）。

        多重防线，任何一条不通过即拦截，杜绝在瀑布中接刀：
        1. CVD 与因子方向强对立（主动买卖盘与方向完全相反）→ 拦截
        2. 清算簇磁吸方向与开仓方向反向且 severity=high → 拦截（防在级联清算中逆势）
        3. 订单簿失衡与因子方向强对立（如因子看多但卖盘挂单厚 2x+）→ 拦截

        历史问题：此函数原是空壳死代码（无论 CVD 如何都返回 True），是短线
        tier 累计亏损的主因之一。现重写为真实多重过滤。

        Args:
            symbol: 交易对
            market_data: 市场数据（兼容旧 CVD 字段）
            direction: 因子方向 long/short/neutral（用于判断是否对立）
        """
        # ── 0. 币圈原生 alpha 防守（主要防线）──
        if direction in ("long", "short"):
            try:
                from backend.services.crypto_alpha_signals import crypto_alpha
                bundle = crypto_alpha.get_bundle(symbol)

                # 清算簇磁吸与方向反向且 high → 拦截
                lm = bundle.liquidation_magnet
                if lm.available and lm.severity == "high" and lm.direction != "neutral":
                    opp = "short" if direction == "long" else "long"
                    if lm.direction == opp:
                        return False, f"清算簇{lm.severity}磁吸反向({lm.note})→拦截逆势接刀"

                # CVD 与方向强对立 → 拦截
                cvd = bundle.cvd_pressure
                if cvd.available and cvd.strength > 0.6 and cvd.direction != "neutral":
                    opp = "short" if direction == "long" else "long"
                    if cvd.direction == opp:
                        return False, f"CVD强对立({cvd.note})→拦截"

                # 订单簿失衡与方向强对立 → 拦截
                obi = bundle.orderbook_imbalance
                if obi.available and obi.strength > 0.7 and obi.direction != "neutral":
                    opp = "short" if direction == "long" else "long"
                    if obi.direction == opp:
                        return False, f"挂单失衡强对立({obi.note})→拦截"
            except Exception as e:
                logger.debug(f"[ScalpRouter] 币圈alpha防守检查失败，降级放行: {e}")

        # ── 1. 旧 CVD 字段兼容（market_data 自带的 cvd/taker_imbalance）──
        cvd = market_data.get("cvd") or market_data.get("taker_imbalance")
        if cvd is not None:
            cvd = float(cvd)
            direction_hint = market_data.get("factor_direction", direction)
            if abs(cvd) >= 50 and direction_hint in ("long", "short"):
                # CVD 负且看多，或 CVD 正且看空 → 对立
                is_opposed = (cvd < -50 and direction_hint == "long") or (
                    cvd > 50 and direction_hint == "short"
                )
                if is_opposed:
                    return False, f"CVD对立(cvd={cvd:.0f} vs dir={direction_hint})"

        return True, "微观结构检查通过"

    def _compute_sl_tp(self, market_data: Dict, direction: str = "long", price: float = 0.0) -> tuple:
        """计算短线 SL/TP（委托 StructureStopCalculator，结构 swing 优先）。"""
        try:
            from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
            side = "long" if direction == "long" else "short"
            sl_pct, tp_pct, _, _ = structure_stop_calculator.compute_sl_tp(
                market_data, side=side, entry=price,
            )
            return sl_pct, tp_pct
        except Exception:
            atr_pct = float(market_data.get("volatility_value", 0) or
                            market_data.get("atr_pct", 0.015) or 0.015)
            sl_pct = max(0.012, min(0.035, atr_pct * 1.5))
            tp_pct = max(0.015, min(0.06, atr_pct * 2.5))
            return sl_pct, tp_pct

    def _fee_guard_passes(self, tp_pct: float, sl_pct: float, price: float = 0.0,
                          symbol: str = "", exchange: str = "") -> bool:
        """手续费守卫：TP 必须能覆盖来回成本。

        修复（2026-06-24）：原代码调用了不存在的 fee_guard.passes() 方法 → 每次抛
        AttributeError → 永远走 tp_pct>=0.005 超宽松兜底 → scalp 从不因手续费拦截。
        现改为调真实 check_open(notional, tp_pct, exchange)，按实际交易所费率评估。
        """
        try:
            from backend.services.fee_guard import fee_guard
            # scalp 用名义价值 ~1000 USD 估算（check_open 按 tp_pct 比例判断，
            # notional 大小只影响滑点档位，对比例阈值影响很小）
            notional = price * 10.0 if price > 0 else 1000.0
            ok, _reason = fee_guard.check_open(
                notional_usd=notional,
                tp_pct=tp_pct,
                is_maker=False,
                trade_nature="scalp",
                exchange=exchange or None,
            )
            return ok
        except Exception:
            # 兜底：按实际交易所费率动态计算往返成本（不再硬编码 0.5%）
            try:
                from backend.services.fee_schedule_service import get_fee_rate
                _ex = exchange or None
                rt_fee = get_fee_rate(_ex, is_maker=False) * 2  # 往返 taker
            except Exception:
                rt_fee = 0.00035 * 2  # 降级 hyperliquid taker 往返
            # TP 必须 >= 3x 往返费率（与 FeeGuard.MIN_PROFIT_FEE_RATIO 一致）
            return tp_pct >= rt_fee * 3.0


# 全局单例
scalp_factor_router = ScalpFactorRouter()
