"""ScalpExecutionGate — 统一规则门（毫秒级，不调 LLM）。"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config import settings as _settings_mod
from backend.services.scalp.scalp_advisory_cache import ScalpAdvisory, scalp_advisory_cache
from backend.services.scalp.scalp_structure_scanner import scalp_structure_scanner
from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
from backend.services.decision_core.regime_agent import classify_regime

logger = logging.getLogger(__name__)


@dataclass
class GateDecision:
    allowed: bool
    lane_decision_id: str = ""
    tier: str = "hold"  # direct / veto / hold / block
    reason: str = ""
    sl_price: float = 0.0
    tp_price: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    effective_score: int = 0
    advisory: Optional[ScalpAdvisory] = None
    needs_veto: bool = False
    size_multiplier: float = 1.0
    audit: Dict[str, Any] = field(default_factory=dict)


class ScalpExecutionGate:
    """规则快开门控 + 结构 SL + advisory 软约束。"""

    @staticmethod
    def _cfg(name: str, default=None):
        return getattr(_settings_mod, name, default)

    def evaluate(
        self,
        symbol: str,
        signal,  # ScalpSignal
        market_data: Dict[str, Any],
        account_id: int = 0,
        mode: str = "paper",
    ) -> GateDecision:
        lane_id = f"scalp_{uuid.uuid4().hex[:12]}"
        lane_enabled = bool(self._cfg("SCALP_EXECUTION_LANE_ENABLED", True))
        veto_band_low = int(self._cfg("SCALP_VETO_BAND_LOW", 35) or 35)
        direct_threshold = int(self._cfg("SCALP_DIRECT_THRESHOLD", 30) or 30)
        # [fix 2026-07-01] range_position 阈值从 0.72/0.28 放宽到 0.97/0.03。
        # 旧值太严：趋势行情中价格必然在区间高位(>0.72)，导致58%的做多信号被拦。
        # 新值只在真正的区间极端边界(>97%/<3%)才拦，让趋势跟随能正常运作。
        range_max_long = float(self._cfg("SCALP_RANGE_MAX_LONG", 0.97) or 0.97)
        range_min_short = float(self._cfg("SCALP_RANGE_MIN_SHORT", 0.03) or 0.03)
        orch_conflict_min = int(self._cfg("SCALP_ORCH_CONFLICT_MIN_SCORE", 50) or 50)
        is_paper = (mode or "paper").strip().lower() == "paper"
        size_mult = 1.0
        universe_soft_note = ""

        if not lane_enabled:
            return GateDecision(False, lane_id, "block", "SCALP_EXECUTION_LANE_ENABLED=false")

        score = int(getattr(signal, "factor_score", 0) or 0)
        action = (getattr(signal, "action", "hold") or "hold").lower()
        direction = (getattr(signal, "direction", "neutral") or "neutral").lower()

        if action not in ("buy", "sell"):
            return GateDecision(False, lane_id, "hold", "无开仓信号")

        if score < veto_band_low:
            return GateDecision(
                False, lane_id, "hold",
                f"score={score}<{veto_band_low}",
                effective_score=score,
            )

        side = "long" if action == "buy" else "short"
        entry = float(getattr(signal, "entry_price", 0) or market_data.get("price", 0) or 0)
        orch = (market_data or {}).get("orchestrator") or {}
        advisory = scalp_advisory_cache.get(symbol)
        if advisory is None or (time.time() - advisory.updated_at) > 900:
            advisory = scalp_structure_scanner.scan(symbol, market_data, orch)

        _adv_pen = int(advisory.penalty or 0)
        if is_paper and _adv_pen > 0:
            try:
                _pm = float(self._cfg("PAPER_SCALP_ADVISORY_PENALTY_MULT", 0.5) or 0.5)
                _adv_pen = int(round(_adv_pen * max(0.0, min(1.0, _pm))))
            except Exception:
                _adv_pen = max(0, _adv_pen // 2)
        effective_score = score - _adv_pen

        # Regime：默认 defer 到 V5 终裁（避免与 unified_gate 双重硬拦）；
        # 仍计算 size_multiplier。关闭 SCALP_GATE_DEFER_REGIME_TO_V5 时恢复本地硬拦。
        regime = classify_regime(market_data or {})
        _defer_regime = bool(self._cfg("SCALP_GATE_DEFER_REGIME_TO_V5", True))
        if not regime.allow_open and not _defer_regime:
            return GateDecision(
                False, lane_id, "block",
                f"regime={regime.regime}: {regime.detail}",
                effective_score=effective_score,
                advisory=advisory,
            )
        if not regime.allow_open and _defer_regime:
            logger.info(
                "[ScalpGate] %s regime=%s 交由 V5 终裁（本层不硬拦）: %s",
                symbol, regime.regime, regime.detail,
            )

        # Universe动态降级：Live 硬拦新开；Paper 样本期默认缩仓软放行
        # （2026-08-02：PUMP/ZEC/KAITO 降级硬拦是开仓断崖主因之一）。
        try:
            from backend.services.alpha.universe_manager import universe_manager as _universe_mgr
            if _universe_mgr.is_degraded(symbol):
                _soft = bool(self._cfg("PAPER_SCALP_UNIVERSE_DEGRADED_SOFT", True))
                if is_paper and _soft:
                    _um = float(self._cfg("PAPER_SCALP_UNIVERSE_DEGRADED_SIZE_MULT", 0.35) or 0.35)
                    size_mult *= max(0.15, min(1.0, _um))
                    universe_soft_note = f"universe_degraded_soft×{size_mult:.2f}"
                    logger.info(
                        "[ScalpGate] %s Paper宇宙降级软放行 缩仓×%.2f（Live仍硬拦）",
                        symbol, size_mult,
                    )
                else:
                    return GateDecision(
                        False, lane_id, "block",
                        f"universe_degraded: {symbol} 流动性跌破门槛,暂停新开仓(已有持仓不受影响)",
                        effective_score=effective_score,
                        advisory=advisory,
                    )
        except Exception as e:
            logger.debug(f"[ScalpGate] Universe降级检查跳过: {e}")

        # 插针/操纵防护（规划文档§3.4，2026-07-18 新增）：与下方的
        # _adjust_sl_for_stop_hunt(SL避让猎杀区) 是同一个"操纵防护"主题的两个
        # 环节——那个是"已经开仓后怎么放SL避免被刺",这个是"这根K线本身就不可信,
        # 直接不开"。放在因子层只是一个权重项会被其他1000+因子稀释到不起作用，
        # 必须在执行门做专用硬拦截。
        wick_block = self._check_wick_manipulation(market_data)
        if wick_block:
            return GateDecision(
                False, lane_id, "block", wick_block,
                effective_score=effective_score,
                advisory=advisory,
            )

        # 区间过滤
        # [fix 2026-06-30] 高分豁免追高/追空拦截：强趋势信号(score≥阈值)本身就是趋势确认，
        # 价格在区间高位是趋势行情的常态，不该被一刀切禁止。只在弱信号时保留追高风险保护。
        range_pos = advisory.range_position_5m
        _high_score_exempt = int(self._cfg("SCALP_RANGE_HIGH_SCORE_EXEMPT", "50") or 50)
        if side == "long" and range_pos > range_max_long and effective_score < _high_score_exempt:
            return GateDecision(
                False, lane_id, "block",
                f"range_position={range_pos:.2f}>{range_max_long} 禁追多(score={effective_score}<{_high_score_exempt})",
                effective_score=effective_score,
                advisory=advisory,
            )
        if side == "short" and range_pos < range_min_short and effective_score < _high_score_exempt:
            return GateDecision(
                False, lane_id, "block",
                f"range_position={range_pos:.2f}<{range_min_short} 禁追空(score={effective_score}<{_high_score_exempt})",
                effective_score=effective_score,
                advisory=advisory,
            )

        # [restored 2026-07-08 · 软否决] 阶段一 1.3：恢复对强反向 advisory 的约束，
        # 但改为"缩仓"而非"一票否决"——既不架空 AI/因子的方向判断（旧硬拦否掉 58%
        # buy 信号的问题），又不再对明显逆多周期的信号满仓开。flag 门控。
        # 判定"强反向"：advisory 明确给出反向裁决（做多时 allow_short / 做空时
        # allow_long），或 verdict=avoid。命中则把仓位乘数打折并小幅扣分。
        soft_veto_mult = 1.0
        try:
            _restore_on = bool(self._cfg("SCALP_MICROSTRUCTURE_GUARD_ENABLED", True))
        except Exception:
            _restore_on = True
        if _restore_on and advisory is not None:
            _verdict = (advisory.advisory_verdict or "neutral").lower()
            _opposed = (
                (side == "long" and _verdict == "allow_short")
                or (side == "short" and _verdict == "allow_long")
            )
            if _verdict == "avoid" or _opposed:
                soft_veto_mult = float(self._cfg("SCALP_REVERSE_SOFT_VETO_MULT", 0.5) or 0.5)
                # 2026-07-09 短线逆势解禁：保留缩仓（仓位反映风险），但开关开启时
                # 去掉 -5 扣分——这个扣分正是把逆势信号从 ~35 推到 veto 带以下、
                # 触发大量 "score<30 hold" 的元凶。评分应反映信号质量，不再兼做逆势惩罚。
                _allow_counter = bool(self._cfg("SCALP_ALLOW_COUNTER_TREND", True))
                if not _allow_counter:
                    effective_score -= 5
                logger.info(
                    "[ScalpGate] %s 反向软否决: advisory=%s side=%s → 缩仓×%.2f%s",
                    symbol, _verdict, side, soft_veto_mult,
                    "" if _allow_counter else " (score-5)",
                )

        # 震荡均值回归模式（2026-07-09）：MR 单已在 scalp_ranging_mr 里贴着区间边缘
        # 算好了小止盈小止损，这里【不能】再套 structure_stop_calculator 的 2%/1% 硬垫高，
        # 否则薄利目标被抬到够不到、MR 完全失效。故 ranging_mr 单直接沿用信号自带 sl/tp。
        _is_mr = bool((market_data or {}).get("ranging_mr"))
        if _is_mr and float(getattr(signal, "tp_pct", 0) or 0) > 0 and float(getattr(signal, "sl_pct", 0) or 0) > 0:
            tp_pct = float(signal.tp_pct)
            sl_pct = float(signal.sl_pct)
            sl_price = float(getattr(signal, "sl_price", 0) or 0) or (
                entry * (1 - sl_pct) if side == "long" else entry * (1 + sl_pct)
            )
            tp_price = float(getattr(signal, "tp_price", 0) or 0) or (
                entry * (1 + tp_pct) if side == "long" else entry * (1 - tp_pct)
            )
        else:
            sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
                market_data,
                side=side,
                entry=entry,
                swing_low=advisory.swing_low_5m,
                swing_high=advisory.swing_high_5m,
            )

        # 猎杀区：SL 距 stop cluster < 0.3% → 调整 SL 或 penalty
        # 震荡均值回归（2026-07-09）：MR 单的止损是【刻意贴区间边缘的小止损】，
        # 猎杀区避让会把它往外推（实测把 0.62% 撑到 1.2%），直接把盈亏比压到 <1.0
        # 触发 V5 冤杀。MR 打法本身就在赌"边缘反弹"，跳过此避让、保留自带小止损。
        if not _is_mr:
            sl_price, sl_pct, hunt_note = self._adjust_sl_for_stop_hunt(
                sl_price, entry, side, advisory.stop_clusters,
            )
            if hunt_note:
                effective_score -= 10
                logger.info("[ScalpGate] %s hunt_adjust: %s", symbol, hunt_note)

        # 猎杀区/结构位加宽 SL 后必须重算 TP，否则 RR 结构性倒挂进 V5 必拦
        tp_pct, tp_price = self._ensure_min_rr(
            entry, side, sl_pct, tp_pct, tp_price, is_mr=_is_mr, is_paper=is_paper,
        )

        if effective_score < veto_band_low:
            return GateDecision(
                False, lane_id, "hold",
                f"penalty后 score={effective_score}<{veto_band_low}",
                sl_price=sl_price, tp_price=tp_price,
                sl_pct=sl_pct, tp_pct=tp_pct,
                effective_score=effective_score,
                advisory=advisory,
            )

        needs_veto = veto_band_low <= effective_score < direct_threshold
        tier = "veto" if needs_veto else "direct"

        logger.info(
            "[ScalpGate] %s %s score=%d eff=%d tier=%s advisory=%s id=%s",
            symbol, action, score, effective_score, tier,
            advisory.advisory_verdict, lane_id,
        )

        # 震荡市缩仓 + 反向软否决 + Paper 宇宙降级软放行，一并写入 size_multiplier
        size_mult *= float(getattr(regime, "size_multiplier", 1.0) or 1.0) * soft_veto_mult
        _reason = getattr(signal, "reasoning", "") or ""
        if universe_soft_note:
            _reason = f"{universe_soft_note}; {_reason}".strip("; ")
        return GateDecision(
            allowed=True,
            lane_decision_id=lane_id,
            tier=tier,
            reason=_reason,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            effective_score=effective_score,
            advisory=advisory,
            needs_veto=needs_veto,
            size_multiplier=max(0.1, min(1.0, size_mult)),
            audit={
                "factor_score": score,
                "effective_score": effective_score,
                "advisory_verdict": advisory.advisory_verdict,
                "regime": regime.regime,
                "range_position": range_pos,
                "universe_soft": universe_soft_note or None,
            },
        )

    def _ensure_min_rr(
        self,
        entry: float,
        side: str,
        sl_pct: float,
        tp_pct: float,
        tp_price: float,
        *,
        is_mr: bool,
        is_paper: bool,
    ) -> Tuple[float, float]:
        """SL 变宽后抬 TP，保证 tp/sl ≥ 最低盈亏比。"""
        if entry <= 0 or sl_pct <= 0:
            return tp_pct, tp_price
        try:
            if is_mr:
                min_rr = float(self._cfg("SCALP_MR_MIN_RR", 1.0) or 1.0)
            elif is_paper:
                min_rr = float(self._cfg("V5_SCALP_MIN_RR_PAPER", 1.3) or 1.3)
            else:
                min_rr = float(self._cfg("V5_SCALP_MIN_RR", 1.4) or 1.4)
        except Exception:
            min_rr = 1.0 if is_mr else 1.3
        if min_rr <= 0:
            return tp_pct, tp_price
        rr = tp_pct / sl_pct
        if rr + 1e-9 >= min_rr:
            return tp_pct, tp_price
        new_tp = min(0.05, max(tp_pct, sl_pct * min_rr))
        if side == "long":
            new_tp_price = entry * (1.0 + new_tp)
        else:
            new_tp_price = entry * (1.0 - new_tp)
        logger.info(
            "[ScalpGate] RR修复 tp %.3f%%→%.3f%% (sl=%.3f%% min_rr=%.2f mr=%s)",
            tp_pct * 100, new_tp * 100, sl_pct * 100, min_rr, is_mr,
        )
        return new_tp, new_tp_price

    def _check_wick_manipulation(self, market_data: Dict[str, Any]) -> str:
        """插针/操纵防护硬拦截（规划文档§2.3.5 + §3.4 公式原文）。

        wick_ratio(单根K线) = max(upper_wick, lower_wick) / (body + eps)
        high_wick_density   = 最近20根K线中 wick_ratio>3.0 的占比
        signal = block  when high_wick_density > threshold(默认0.3)

        注意 high_wick_density 是"最近20根里插针形态出现的频率"，不是单根K线
        的影线占比——单根大影线是正常波动，但最近20根里超过3成都是长影线插针，
        说明当前是"止损猎杀/操纵频发"的行情环境，此时任何一次开仓都可能被
        同样的手法打掉，故直接 block 而非降权（放在因子层会被其他1000+因子稀释）。

        无K线数据、或不足20根历史时安全放行（不误杀正常开仓）。
        """
        enabled = bool(self._cfg("SCALP_WICK_MANIPULATION_GUARD_ENABLED", True))
        if not enabled:
            return ""
        threshold = float(self._cfg("SCALP_WICK_DENSITY_BLOCK_THRESHOLD", 0.30) or 0.30)
        try:
            klines = (market_data or {}).get("klines")
            if klines is None:
                return ""
            import pandas as _pd
            df = klines if isinstance(klines, _pd.DataFrame) else _pd.DataFrame(klines)
            if df is None or len(df) < 20 or not {"open", "high", "low", "close"}.issubset(df.columns):
                return ""
            window = df.tail(20)
            o = window["open"].astype(float)
            h = window["high"].astype(float)
            l = window["low"].astype(float)
            c = window["close"].astype(float)

            upper_wick = (h - _pd.concat([o, c], axis=1).max(axis=1)).clip(lower=0)
            lower_wick = (_pd.concat([o, c], axis=1).min(axis=1) - l).clip(lower=0)
            body = (c - o).abs()
            wick_ratio = _pd.concat([upper_wick, lower_wick], axis=1).max(axis=1) / (body + 1e-10)

            high_wick_density = float((wick_ratio > 3.0).sum()) / len(window)
            if high_wick_density > threshold:
                return (
                    f"高插针密度环境 high_wick_density={high_wick_density:.2f}>{threshold} "
                    f"(近20根K线中止损猎杀/操纵形态频发,暂停开仓)"
                )
        except Exception as e:
            logger.debug(f"[ScalpGate] 插针检测跳过: {e}")
        return ""

    def _adjust_sl_for_stop_hunt(
        self,
        sl_price: float,
        entry: float,
        side: str,
        clusters: List[str],
    ) -> Tuple[float, float, str]:
        if entry <= 0 or not clusters:
            return sl_price, abs(entry - sl_price) / entry if entry else 0.0, ""

        min_dist_pct = 999.0
        nearest = None
        for c in clusters:
            p = scalp_structure_scanner.parse_cluster_price(c)
            if p is None or p <= 0:
                continue
            dist = abs(sl_price - p) / entry
            if dist < min_dist_pct:
                min_dist_pct = dist
                nearest = p

        if min_dist_pct >= 0.003 or nearest is None:
            return sl_price, abs(entry - sl_price) / entry if entry else 0.0, ""

        buffer = 0.004
        if side == "long":
            new_sl = min(sl_price, nearest * (1 - buffer))
        else:
            new_sl = max(sl_price, nearest * (1 + buffer))
        sl_pct = abs(entry - new_sl) / entry
        return new_sl, sl_pct, f"SL远离猎杀区@{nearest:.2f} dist={min_dist_pct:.3%}"


scalp_execution_gate = ScalpExecutionGate()
