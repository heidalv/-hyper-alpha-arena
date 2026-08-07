"""
Strategic Analyst - 宏观关联分析引擎

核心功能：
1. 宏观体制分类（risk_on/risk_off/neutral/transition）
2. 跨市场相关性计算（BTC↔SPX/BTC↔DXY/BTC↔CSI300）
3. 影响评估（对加密市场的影响方向和强度）
4. 体制转换检测
"""

import logging
from typing import List, Optional
from datetime import datetime

import numpy as np

from .models import MacroSnapshot, MacroAssessment, CrossMarketCorrelation

logger = logging.getLogger(__name__)


class MacroAnalysisEngine:
    """
    宏观关联分析引擎

    输入: MacroSnapshot (采集的宏观指标)
    输出: MacroAssessment (宏观评估结果)
    """

    # 体制分类阈值
    RISK_ON_THRESHOLD = 0.3       # risk_on_score > 0.3 → risk_on
    RISK_OFF_THRESHOLD = -0.3     # risk_on_score < -0.3 → risk_off
    TRANSITION_ZONE = 0.15        # |score| < 0.15 → transition

    # 相关性体制阈值
    DECOUPLED_THRESHOLD = 0.2     # |r| < 0.2 → decoupled
    STRONG_CORR_THRESHOLD = 0.5   # |r| > 0.5 → strong_corr

    def analyze(self, snapshot: MacroSnapshot) -> MacroAssessment:
        """
        执行宏观分析流水线

        Args:
            snapshot: 宏观快照数据

        Returns:
            MacroAssessment: 宏观评估结果
        """
        assessment = MacroAssessment()

        # 1. 计算 risk_on_score
        risk_on_score = self._calculate_risk_on_score(snapshot)
        assessment.risk_on_score = risk_on_score

        # 2. 宏观体制分类
        assessment.regime = self._classify_regime(risk_on_score, snapshot)
        assessment.confidence = self._calculate_confidence(snapshot, assessment.regime)

        # 3. 影响评估
        impact_dir, impact_mag = self._assess_impact(snapshot, assessment.regime)
        assessment.impact_direction = impact_dir
        assessment.impact_magnitude = impact_mag

        # 4. 分项评估
        assessment.dxy_impact = self._assess_dxy_impact(snapshot)
        assessment.spx_impact = self._assess_spx_impact(snapshot)
        assessment.china_market_impact = self._assess_china_impact(snapshot)
        assessment.liquidity_condition = self._assess_liquidity(snapshot)

        # 5. 关键风险
        assessment.key_risks = self._identify_key_risks(snapshot)

        # 6. 跨市场相关性
        assessment.cross_market_correlations = self._build_correlation_map(snapshot)

        # 7. 体制转换信号
        assessment.regime_transition_signal = self._detect_transition(snapshot, assessment)

        logger.info(
            f"[MacroAnalysis] Regime={assessment.regime}, "
            f"RiskOnScore={assessment.risk_on_score:.3f}, "
            f"Impact={assessment.impact_direction}/{assessment.impact_magnitude:.2f}, "
            f"Confidence={assessment.confidence:.2f}"
        )

        return assessment

    # -----------------------------------------------------------------------
    # 核心计算方法
    # -----------------------------------------------------------------------

    def _calculate_risk_on_score(self, s: MacroSnapshot) -> float:
        """
        计算风险偏好评分 (-1 ~ +1)
        正值 = 风险偏好(risk_on)，利于加密市场
        负值 = 风险规避(risk_off)，不利于加密市场
        """
        score = 0.0
        weights_total = 0.0

        # DXY: 美元上涨 → 风险规避，对加密利空 (权重 25%)
        if s.dxy_change_pct is not None:
            w = 0.25
            # DXY 日涨幅 > 0.3% 是较强的风险规避信号
            dxy_signal = -np.clip(s.dxy_change_pct / 0.5, -1, 1)
            score += dxy_signal * w
            weights_total += w

        # SPX: 美股上涨 → 风险偏好，对加密利好 (权重 25%)
        if s.spx_change_pct is not None:
            w = 0.25
            spx_signal = np.clip(s.spx_change_pct / 1.0, -1, 1)
            score += spx_signal * w
            weights_total += w

        # 恐贪指数 (权重 25%)
        if s.fear_greed_index is not None:
            w = 0.25
            # 标准化: 50=中性, 0=极度恐惧(-1), 100=极度贪婪(+1)
            fg_signal = (s.fear_greed_index - 50) / 50
            score += fg_signal * w
            weights_total += w

        # BTC Dominance: 上升 → 避险（利空山寨）但说明资金在加密市场内 (权重 15%)
        if s.btc_dominance is not None:
            w = 0.15
            # 高 BTC dominance (>60%) = 避险，低 (<50%) = 风险偏好
            if s.btc_dominance > 60:
                dom_signal = -0.3
            elif s.btc_dominance < 50:
                dom_signal = 0.3
            else:
                dom_signal = 0.0
            score += dom_signal * w
            weights_total += w

        # CSI300: 中国股市上涨 → 对加密中性偏利好 (权重 10%)
        if s.csi300_change_pct is not None:
            w = 0.10
            csi_signal = np.clip(s.csi300_change_pct / 1.5, -1, 1)
            score += csi_signal * w
            weights_total += w

        if weights_total > 0:
            score = score / weights_total

        return float(np.clip(score, -1.0, 1.0))

    def _classify_regime(self, risk_on_score: float, s: MacroSnapshot) -> str:
        """宏观体制分类"""
        if risk_on_score > self.RISK_ON_THRESHOLD:
            return "risk_on"
        elif risk_on_score < self.RISK_OFF_THRESHOLD:
            return "risk_off"
        elif abs(risk_on_score) < self.TRANSITION_ZONE:
            return "transition"
        else:
            return "neutral"

    def _calculate_confidence(self, s: MacroSnapshot, regime: str) -> float:
        """
        计算体制判断的置信度 (0~1)
        基于数据完整性和信号一致性
        """
        # 数据完整性
        fields = [
            s.dxy_change_pct, s.spx_change_pct, s.fear_greed_index,
            s.btc_dominance, s.csi300_change_pct
        ]
        available = sum(1 for f in fields if f is not None)
        completeness = available / len(fields)

        # 信号一致性：各指标方向是否一致
        signals = []
        if s.dxy_change_pct is not None:
            signals.append(-1 if s.dxy_change_pct > 0 else 1)  # DXY 下跌利好
        if s.spx_change_pct is not None:
            signals.append(1 if s.spx_change_pct > 0 else -1)  # SPX 上涨利好
        if s.fear_greed_index is not None:
            signals.append(1 if s.fear_greed_index > 50 else -1)
        if s.csi300_change_pct is not None:
            signals.append(1 if s.csi300_change_pct > 0 else -1)

        if len(signals) >= 2:
            avg_signal = np.mean(signals)
            consistency = abs(avg_signal)  # 信号越一致，一致性越高
        else:
            consistency = 0.3  # 数据不足时给中等置信度

        confidence = completeness * 0.5 + consistency * 0.5
        return float(np.clip(confidence, 0.1, 1.0))

    def _assess_impact(self, s: MacroSnapshot, regime: str) -> tuple:
        """评估对加密市场的影响方向和强度"""
        direction_map = {
            "risk_on": ("bullish", 0.6),
            "risk_off": ("bearish", 0.6),
            "neutral": ("neutral", 0.2),
            "transition": ("neutral", 0.4),
        }
        direction, magnitude = direction_map.get(regime, ("neutral", 0.0))

        # 根据具体数据调整强度
        adjustments = 0.0
        if s.fear_greed_index is not None:
            if s.fear_greed_index > 75 or s.fear_greed_index < 25:
                adjustments += 0.2  # 极端情绪加大影响

        if s.dxy_change_pct is not None and abs(s.dxy_change_pct) > 0.5:
            adjustments += 0.15  # DXY 大幅波动加大影响

        magnitude = float(np.clip(magnitude + adjustments, 0.0, 1.0))
        return direction, magnitude

    def _assess_dxy_impact(self, s: MacroSnapshot) -> str:
        """DXY 对加密市场的影响"""
        if s.dxy_change_pct is None:
            return "neutral"
        if s.dxy_change_pct > 0.5:
            return "bearish"   # 美元走强 → 加密利空
        elif s.dxy_change_pct < -0.5:
            return "bullish"   # 美元走弱 → 加密利好
        return "neutral"

    def _assess_spx_impact(self, s: MacroSnapshot) -> str:
        """SPX 对加密市场的影响"""
        if s.spx_change_pct is None:
            return "neutral"
        if s.spx_change_pct > 1.0:
            return "bullish"   # 美股大涨 → 风险偏好
        elif s.spx_change_pct < -1.0:
            return "bearish"   # 美股大跌 → 风险规避
        return "neutral"

    def _assess_china_impact(self, s: MacroSnapshot) -> str:
        """中国股市对加密市场的影响"""
        if s.csi300_change_pct is None:
            return "neutral"
        if s.csi300_change_pct > 2.0:
            return "bullish"
        elif s.csi300_change_pct < -2.0:
            return "bearish"
        return "neutral"

    def _assess_liquidity(self, s: MacroSnapshot) -> str:
        """流动性评估"""
        if s.fed_funds_rate is None:
            return "normal"
        if s.fed_funds_rate > 5.0:
            return "tight"      # 高利率 → 流动性紧缩
        elif s.fed_funds_rate < 2.0:
            return "loose"      # 低利率 → 流动性宽松
        return "normal"

    def _identify_key_risks(self, s: MacroSnapshot) -> List[str]:
        """识别关键风险因素"""
        risks = []

        if s.dxy_change_pct is not None and s.dxy_change_pct > 0.8:
            risks.append(f"美元指数大幅走强(DXY日涨{s.dxy_change_pct:.2f}%)，加密市场承压")

        if s.spx_change_pct is not None and s.spx_change_pct < -1.5:
            risks.append(f"美股大幅下跌(SPX日跌{abs(s.spx_change_pct):.2f}%)，风险偏好下降")

        if s.fear_greed_index is not None:
            if s.fear_greed_index < 20:
                risks.append(f"市场极度恐惧(恐贪指数={s.fear_greed_index:.0f})，可能存在恐慌性抛售")
            elif s.fear_greed_index > 85:
                risks.append(f"市场极度贪婪(恐贪指数={s.fear_greed_index:.0f})，存在过热回调风险")

        if s.fed_funds_rate is not None and s.fed_funds_rate > 5.0:
            risks.append(f"高利率环境(联邦基金利率={s.fed_funds_rate:.2f}%)，流动性紧缩")

        if s.csi300_change_pct is not None and s.csi300_change_pct < -3.0:
            risks.append(f"A股大幅下跌(沪深300日跌{abs(s.csi300_change_pct):.2f}%)，亚洲风险偏好下降")

        return risks

    def _build_correlation_map(self, s: MacroSnapshot) -> dict:
        """构建跨市场相关性映射"""
        corrs = {}
        if s.btc_sp500_corr_30d is not None:
            corrs["btc_spx_30d"] = s.btc_sp500_corr_30d
        if s.btc_dxy_corr_30d is not None:
            corrs["btc_dxy_30d"] = s.btc_dxy_corr_30d
        if s.btc_csi300_corr_30d is not None:
            corrs["btc_csi300_30d"] = s.btc_csi300_corr_30d
        return corrs

    def _detect_transition(self, s: MacroSnapshot, assessment: MacroAssessment) -> bool:
        """检测宏观体制转换信号"""
        # 指标矛盾信号
        bullish_signals = 0
        bearish_signals = 0

        if s.dxy_change_pct is not None:
            if s.dxy_change_pct < 0:
                bullish_signals += 1
            else:
                bearish_signals += 1

        if s.spx_change_pct is not None:
            if s.spx_change_pct > 0:
                bullish_signals += 1
            else:
                bearish_signals += 1

        if s.fear_greed_index is not None:
            if s.fear_greed_index > 50:
                bullish_signals += 1
            else:
                bearish_signals += 1

        # 多空信号接近（差异 <= 1）表示过渡状态
        if abs(bullish_signals - bearish_signals) <= 1 and (bullish_signals + bearish_signals) >= 3:
            return True

        # 极端指标反转
        if s.fear_greed_index is not None:
            if 35 < s.fear_greed_index < 65 and s.spx_change_pct is not None:
                if abs(s.spx_change_pct) > 1.5:
                    return True

        return False

    # -----------------------------------------------------------------------
    # 跨市场相关性计算（需要 BTC 历史数据 + 外部市场数据）
    # -----------------------------------------------------------------------

    def calculate_cross_market_correlations(
        self,
        btc_returns: List[float],
        spx_returns: Optional[List[float]] = None,
        dxy_returns: Optional[List[float]] = None,
        csi300_returns: Optional[List[float]] = None,
    ) -> List[CrossMarketCorrelation]:
        """
        计算跨市场相关性

        Args:
            btc_returns: BTC 日收益率序列
            spx_returns: SPX 日收益率序列
            dxy_returns: DXY 日收益率序列
            csi300_returns: CSI300 日收益率序列

        Returns:
            各相关性对的 CrossMarketCorrelation 列表
        """
        results = []
        btc_arr = np.array(btc_returns) if btc_returns else np.array([])

        if len(btc_arr) < 7:
            return results

        pairs = [
            ("btc_spx", spx_returns),
            ("btc_dxy", dxy_returns),
            ("btc_csi300", csi300_returns),
        ]

        for pair_name, ext_returns in pairs:
            if ext_returns is None or len(ext_returns) < 7:
                continue

            ext_arr = np.array(ext_returns)
            min_len = min(len(btc_arr), len(ext_arr))
            btc_trimmed = btc_arr[-min_len:]
            ext_trimmed = ext_arr[-min_len:]

            corr = CrossMarketCorrelation(pair_name=pair_name)

            # 7日相关性
            if min_len >= 7:
                corr.correlation_7d = float(np.corrcoef(btc_trimmed[-7:], ext_trimmed[-7:])[0, 1])

            # 30日相关性
            if min_len >= 30:
                corr.correlation_30d = float(np.corrcoef(btc_trimmed[-30:], ext_trimmed[-30:])[0, 1])

            # 90日相关性
            if min_len >= 90:
                corr.correlation_90d = float(np.corrcoef(btc_trimmed[-90:], ext_trimmed[-90:])[0, 1])

            # 相关性体制
            r = corr.correlation_30d if corr.correlation_30d != 0.0 else corr.correlation_7d
            if abs(r) < self.DECOUPLED_THRESHOLD:
                corr.regime = "decoupled"
            elif abs(r) > self.STRONG_CORR_THRESHOLD:
                corr.regime = "strong_corr"
            else:
                corr.regime = "weak_corr"

            # 滚动 Beta
            if min_len >= 30:
                ext_var = np.var(ext_trimmed[-30:])
                if ext_var > 0:
                    corr.rolling_beta = float(
                        np.cov(btc_trimmed[-30:], ext_trimmed[-30:])[0, 1] / ext_var
                    )

            results.append(corr)

        return results
