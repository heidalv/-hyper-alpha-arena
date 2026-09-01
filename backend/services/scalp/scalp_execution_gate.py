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
        # [2026-08-31 配置接线修复] settings.py 并未暴露全部 .env 键（如
        # SCALP_SHORT_PAPER_EXEMPT_MIN/FULL_MIN、SCALP_SHORT_EM_*），原实现
        # getattr(settings, name, default) 对这些键恒返回硬编码默认值——.env
        # 调整（空头加严 50/65 等）从不生效。改为 settings 缺失时回退 os.getenv。
        _v = getattr(_settings_mod, name, None)
        if _v is None:
            import os as _os_cfg
            _raw = _os_cfg.getenv(name)
            return _raw if _raw is not None else default
        return _v

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
        # [2026-08-26 数据驱动·用户指示] RSI 动量区禁反转：
        # 昨晚实证 RSI 55-70 强势动量区做空 = 胜率30.8%/均-83bp（全场最差桶）；
        # 对称禁 30-45 弱势动量区做多（-18.8bp）。极端区(>70/<30)保留给 MR 主逻辑。
        if bool(self._cfg("SCALP_MOMENTUM_ZONE_GATE", True)):
            _rsi = 0.0
            for _src in (getattr(signal, "rsi", None),
                         (market_data or {}).get("rsi"),
                         (market_data or {}).get("rsi_14")):
                try:
                    _rsi = float(_src or 0)
                    if _rsi > 0:
                        break
                except (TypeError, ValueError):
                    continue
            if 55.0 <= _rsi <= 70.0 and action == "sell":
                return GateDecision(False, lane_id, "block",
                                    f"momentum_zone_reversal: rsi={_rsi:.0f} 强势动量区禁做空",
                                    effective_score=score)
            if 30.0 <= _rsi <= 45.0 and action == "buy":
                return GateDecision(False, lane_id, "block",
                                    f"momentum_zone_reversal: rsi={_rsi:.0f} 弱势动量区禁做多",
                                    effective_score=score)
        entry = float(getattr(signal, "entry_price", 0) or market_data.get("price", 0) or 0)
        orch = (market_data or {}).get("orchestrator") or {}
        _is_mr_signal = bool((market_data or {}).get("ranging_mr")) or str(
            getattr(signal, "source", "") or ""
        ) == "ranging_mr"
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

        # [2026-08-23 短线赚钱改造 B] 空头条件化：全历史 short 1250 笔 WR 23.8%
        # 净亏 -104 vs long 1122 笔 +50——市场上行周期空头是结构性逆风（研究⑧同
        # 结论）。空头分两档开放：
        #   - 三条件齐备（trending + 4h偏空 + 资金费极端正）→ 正常仓位；
        #   - 两条件（4h偏空 + 资金费极端正）但 regime 非 trending → 半仓参与
        #     （不过度阻止、让样本积累）；其余场景拦截。
        # 多头不受任何影响。这是方向 alpha 的结构化使用。
        #
        # [2026-08-24 短线深挖 C] MR 豁免 + Paper 按分数分档：
        #   - MR 空头是"区间高位高抛"的均值回归，不参与趋势方向博弈——
        #     结构性逆风结论不适用，豁免（今日 28 笔 MR 空 +0.17 是全短线唯一正收益边）；
        #   - Paper 模式对 trend/lane 空头按分数分档放行（0.25/0.5/1.0x），
        #     让模拟盘在强信号空头上积累样本，而不是 7950 次/日整片拦截；
        #   - Live 保持原三条件/两条件硬规则不变。
        if side == "short" and bool(self._cfg("SCALP_SHORT_REQUIRES_TREND_DOWN", True)):
            _regime_name = (regime.regime or "").lower()
            _mid_bias = str((orch or {}).get("mid_bias") or "neutral").lower()
            try:
                _funding = float(market_data.get("funding_rate") or 0)
            except Exception:
                _funding = 0.0
            _funding_min = float(self._cfg("SCALP_SHORT_MIN_FUNDING", 0.0001) or 0.0001)
            _bias_ok = _mid_bias == "bearish"
            _bias_src = "thesis"
            if not _bias_ok and _mid_bias in ("neutral", "", "none"):
                # 阶段4：thesis 信封（mid_bias）因 MLTO thesis 下线常恒 neutral——
                # 确定性回退：近 24h 下跌动能(-2%) 且 1h 无反弹 → 规则版 trending-down，
                # 与资金费极端条件共同构成"下行 + 逼空挤压"空头场景（B 改造原意）。
                try:
                    _chg24 = float(market_data.get("price_change_24h_pct") or 0)
                    _chg1 = float(market_data.get("price_change_1h_pct") or 0)
                except Exception:
                    _chg24 = _chg1 = 0.0
                if _chg24 <= -0.02 and _chg1 <= 0.0:
                    _bias_ok = True
                    _bias_src = "rule_fallback"
            _fund_ok = _funding >= _funding_min
            _trend_ok = _regime_name == "trending"
            _conds_met = _bias_ok and _fund_ok
            # [2026-08-26 亏损复盘] 凌晨时段空头收紧：04:00-08:00 无趋势下行证据时，
            # MR 豁免仅限区间极高位(pos≥0.90)，Paper 分档再乘 0.5。
            # 实测 8/26 凌晨五连 SL（APT/UNI/XRP/SOL/ASTER，单笔 -1.4~-1.65）
            # 全部发生在该时段——反弹时段开空样本质量差。
            _em_active = False
            _em_extra = 1.0
            _em_min_pos = 1.01  # >1 永不满足 = 不限制
            try:
                if bool(self._cfg("SCALP_SHORT_EARLY_MORNING_GATE", True)):
                    import time as _time_em
                    _hh = int(_time_em.localtime().tm_hour)
                    _em_start = int(self._cfg("SCALP_SHORT_EM_START_HOUR", 4) or 4)
                    _em_end = int(self._cfg("SCALP_SHORT_EM_END_HOUR", 8) or 8)
                    if _em_start <= _hh < _em_end:
                        _em_active = True
                        _em_extra = float(self._cfg("SCALP_SHORT_EM_EXTRA_MULT", 0.5) or 0.5)
                        _em_min_pos = float(self._cfg("SCALP_SHORT_EM_MIN_RANGE_POS", 0.90) or 0.90)
            except Exception:
                pass
            if not _conds_met:
                if _is_mr_signal:
                    _mr_pos = None
                    try:
                        _mr_pos = float(getattr(advisory, "range_position_5m", None) or -1)
                    except Exception:
                        _mr_pos = None
                    _mr_em_ok = (not _em_active) or (_mr_pos is not None and _mr_pos >= _em_min_pos)
                    if _mr_em_ok:
                        # MR 空头豁免：区间高位高抛不属于趋势逆风（深挖 C）
                        logger.info(
                            "[ScalpGate] %s MR空头豁免空头条件化（区间高抛不参与趋势博弈）",
                            symbol,
                        )
                    elif is_paper:
                        size_mult *= _em_extra
                        logger.info(
                            "[ScalpGate] %s MR空头凌晨收紧 pos=%.2f<%.2f → 缩仓×%.2f",
                            symbol, _mr_pos or -1, _em_min_pos, _em_extra,
                        )
                    else:
                        return GateDecision(
                            False, lane_id, "hold",
                            f"凌晨时段({_em_start}:00-{_em_end}:00)空头无趋势证据且区间位不足 → 拦截",
                            effective_score=effective_score,
                            advisory=advisory,
                        )
                elif is_paper:
                    # Paper 分档放行：强信号空头继续积累样本（0.25/0.5/1.0x），
                    # 弱信号(<40)与旧规则一致拦截。Live 不受影响。
                    _short_esc = int(self._cfg("SCALP_SHORT_PAPER_EXEMPT_MIN", 40) or 40)
                    _short_full = int(self._cfg("SCALP_SHORT_PAPER_FULL_MIN", 55) or 55)
                    if effective_score >= _short_full:
                        if _em_active:
                            size_mult *= _em_extra
                            logger.info(
                                "[ScalpGate] %s Paper空头高分凌晨收紧 → 半仓样本",
                                symbol,
                            )
                        else:
                            logger.info(
                                "[ScalpGate] %s Paper空头高分放行 score=%d≥%d → 全仓",
                                symbol, effective_score, _short_full,
                            )
                    elif effective_score >= _short_esc:
                        size_mult *= 0.5
                        if _em_active:
                            size_mult *= _em_extra
                        logger.info(
                            "[ScalpGate] %s Paper空头中分放行 score=%d∈[%d,%d) → %s样本",
                            symbol, effective_score, _short_esc, _short_full,
                            f"0.25x(凌晨)" if _em_active else "半仓",
                        )
                    else:
                        size_mult *= 0.25
                        if _em_active:
                            size_mult *= _em_extra
                        logger.info(
                            "[ScalpGate] %s Paper空头低分放行 score=%d<%d → %s样本",
                            symbol, effective_score, _short_esc,
                            f"0.125x(凌晨)" if _em_active else "0.25x",
                        )
                else:
                    # [2026-08-29 全面修复·撤回放宽] 8fea911/2e7469a 的实盘空头
                    # 单条件(0.25x)/零条件(0.125x)试探仓被 30 天成交数据否决：
                    # 空头连续 5 周全亏(-824 毛)、盈亏比 1.10(多头 1.81)、
                    # mid 空头 -601——"解决零成交"不能靠放行负 EV 方向。恢复
                    # 全条件硬拦(4h偏空 AND funding≥门槛)。要回滚放宽设
                    # SCALP_SHORT_LIVE_STRICT=false（恢复 8/29 上午的分档试探行为）。
                    _live_strict = bool(self._cfg("SCALP_SHORT_LIVE_STRICT", True))
                    if _live_strict:
                        return GateDecision(
                            False, lane_id, "hold",
                            f"空头条件未齐(mid_bias={_mid_bias},bias_src={_bias_src},funding={_funding:.5f}≥"
                            f"{_funding_min:.5f})——30天空头全亏,实盘全条件门禁",
                            effective_score=effective_score,
                            advisory=advisory,
                        )
                    _live_half_ok = bool(_bias_ok or _fund_ok)
                    try:
                        _live_half_min = float(
                            self._cfg("SCALP_SHORT_LIVE_HALF_MIN_SCORE", 45) or 45
                        )
                        _live_probe_min = float(
                            self._cfg("SCALP_SHORT_LIVE_PROBE_MIN_SCORE", 50) or 50
                        )
                    except Exception:
                        _live_half_min = 45.0
                        _live_probe_min = 50.0
                    if _live_half_ok and effective_score >= _live_half_min:
                        size_mult *= 0.25
                        logger.info(
                            "[ScalpGate] %s 实盘空头单条件降级放行 score=%d≥%.0f "
                            "(bias=%s funding_ok=%s) → 0.25x试探",
                            symbol, effective_score, _live_half_min,
                            _bias_ok, _fund_ok,
                        )
                    elif effective_score >= _live_probe_min:
                        size_mult *= 0.125
                        logger.info(
                            "[ScalpGate] %s 实盘空头零条件高分试探 score=%d≥%.0f → 0.125x",
                            symbol, effective_score, _live_probe_min,
                        )
                    else:
                        return GateDecision(
                            False, lane_id, "hold",
                            f"空头条件未齐(mid_bias={_mid_bias},bias_src={_bias_src},funding={_funding:.5f}≥"
                            f"{_funding_min:.5f})——上行周期空头结构性逆风",
                            effective_score=effective_score,
                            advisory=advisory,
                        )
            if _trend_ok is False and _conds_met and not _is_mr_signal:
                size_mult *= 0.5
                logger.info(
                    "[ScalpGate] %s 空头两条件齐但 regime=%s（非trending）→ 半仓参与",
                    symbol, _regime_name,
                )

        # [2026-09-01 F24 多头镜像条件化] 空头侧自 2026-08-23 起有趋势一致性门
        # （4h偏空+资金费才放行），多头侧一直"不受任何影响"——该不对称结论
        # 来自上涨周期样本（short 1250 笔 WR 23.8% vs long +50）。今日下行日
        # 实测反转为：多头 0/3 全亏(-0.436/笔)，空头 6/15 胜(+0.014/笔)。
        # 结构性逆风与方向无关：镜像门让多头也要求 4h 偏多（或 24h 上涨动能
        # 回退），MR 低位低吸豁免；Paper 按分数分档缩仓（保持采样不停止），
        # Live 全条件硬拦（SCALP_LONG_LIVE_STRICT=false 可回滚分档试探）。
        if side == "long" and bool(self._cfg("SCALP_LONG_REQUIRES_TREND_UP", True)):
            _mid_bias_l = str((orch or {}).get("mid_bias") or "neutral").lower()
            _bias_ok_l = _mid_bias_l == "bullish"
            _bias_src_l = "thesis"
            if not _bias_ok_l and _mid_bias_l in ("neutral", "", "none"):
                try:
                    _chg24_l = float(market_data.get("price_change_24h_pct") or 0)
                    _chg1_l = float(market_data.get("price_change_1h_pct") or 0)
                except Exception:
                    _chg24_l = _chg1_l = 0.0
                if _chg24_l >= 0.02 and _chg1_l >= 0.0:
                    _bias_ok_l = True
                    _bias_src_l = "rule_fallback"
            if not _bias_ok_l:
                if _is_mr_signal:
                    logger.info(
                        "[ScalpGate] %s MR多头豁免多头条件化（区间低位低吸不参与趋势博弈）",
                        symbol,
                    )
                elif is_paper:
                    _long_full = int(self._cfg("SCALP_LONG_PAPER_FULL_MIN", 55) or 55)
                    _long_esc = int(self._cfg("SCALP_LONG_PAPER_EXEMPT_MIN", 40) or 40)
                    if effective_score >= _long_full:
                        logger.info(
                            "[ScalpGate] %s Paper多头趋势未齐高分放行 score=%d≥%d → 全仓样本",
                            symbol, effective_score, _long_full,
                        )
                    elif effective_score >= _long_esc:
                        size_mult *= 0.5
                        logger.info(
                            "[ScalpGate] %s Paper多头趋势未齐中分 score=%d∈[%d,%d) → 半仓样本",
                            symbol, effective_score, _long_esc, _long_full,
                        )
                    else:
                        size_mult *= 0.25
                        logger.info(
                            "[ScalpGate] %s Paper多头趋势未齐低分 score=%d<%d → 0.25x样本",
                            symbol, effective_score, _long_esc,
                        )
                else:
                    _long_strict = bool(self._cfg("SCALP_LONG_LIVE_STRICT", True))
                    if _long_strict:
                        return GateDecision(
                            False, lane_id, "hold",
                            f"多头条件未齐(mid_bias={_mid_bias_l},bias_src={_bias_src_l})"
                            "——下行周期多头结构性逆风",
                            effective_score=effective_score,
                            advisory=advisory,
                        )
                    size_mult *= 0.25
                    logger.info(
                        "[ScalpGate] %s 实盘多头趋势未齐(bias=%s src=%s) → 0.25x试探",
                        symbol, _mid_bias_l, _bias_src_l,
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
        # [2026-08-24 短线深挖 D] MR 豁免：高插针密度正是震荡均值回归的主场
        # （长影线=边界被反复试探=高抛低吸机会），且 MR 宽 SL(≥1.2%) 天然免疫
        # 短插针。趋势打法保留原硬拦截。
        wick_block = self._check_wick_manipulation(market_data, is_mr=_is_mr_signal)
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
                symbol=symbol,
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

        _plan = (market_data or {}).get("_tpsl_plan") if isinstance(market_data, dict) else None
        if isinstance(_plan, dict) and _plan.get("reason"):
            logger.info(
                "[ScalpGate] %s tpsl play=%s regime=%s sl=%.3f%% tp=%.3f%% | %s",
                symbol,
                _plan.get("playbook"),
                _plan.get("regime"),
                float(_plan.get("sl_pct") or sl_pct) * 100.0,
                float(_plan.get("tp_pct") or tp_pct) * 100.0,
                str(_plan.get("reason") or "")[:120],
            )
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
                "tpsl_plan": _plan if isinstance(_plan, dict) else None,
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

    def _check_wick_manipulation(self, market_data: Dict[str, Any], is_mr: bool = False) -> str:
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
        if is_mr:
            return ""  # [2026-08-24 深挖 D] MR 豁免：插针密度环境正是 MR 主场
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
