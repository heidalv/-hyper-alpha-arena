"""多频率对齐与约束验证服务 - Multi-Frequency Alignment Service

P2 核心组件，负责:
1. 多周期信号一致性校验 (validate_alignment)
2. 对齐评分 (compute_alignment_score) 
3. 交易许可判定 (get_trading_permission)
4. 主导周期推荐 (suggest_dominant_freq)
5. 跨周期约束验证 (validate_cross_freq_constraints)

用法:
    from services.multi_freq_alignment import multi_freq_alignment
    
    result = multi_freq_alignment.validate_alignment(
        m15_dir="bullish", m15_str=0.7,
        m1h_dir="bullish", m1h_str=0.5,
        m4h_dir="bearish", m4h_str=0.3,
    )
    # result: {"aligned": False, "score": 0.45, "permission": "restricted", ...}
"""

import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass 
class AlignmentResult:
    """多频率对齐分析结果"""
    aligned: bool = False
    alignment_status: str = "unknown"    # aligned / divergent / conflicting / insufficient_data
    
    # 各周期分析
    freq_analysis: Dict[str, dict] = field(default_factory=dict)
    
    # 评分 (0~1)
    alignment_score: float = 0.0         # 对齐度评分
    trend_consistency: float = 0.0       # 趋势一致性
    volatility_harmony: float = 0.0      # 波动率协调度
    
    # 交易许可
    permission: str = "unknown"          # full / restricted / critical_only / denied
    permission_reason: str = ""
    
    # 推荐
    recommended_freq: str = "unknown"    # 推荐关注周期
    recommended_leverage_scale: float = 1.0
    recommended_position_scale: float = 1.0
    
    # 风险提示
    risk_flags: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class MultiFreqAlignment:
    """多频率对齐验证与约束服务"""
    
    # 权重配置
    DIRECTION_WEIGHT = 0.50       # 方向一致性权重
    STRENGTH_WEIGHT = 0.20        # 强度一致性权重
    VOLATILITY_WEIGHT = 0.15      # 波动率协调权重
    RSI_WEIGHT = 0.15             # RSI协同权重
    
    # 阈值配置
    STRONG_STRENGTH = 0.6         # 强趋势阈值
    CONFLICT_THRESHOLD = -0.3     # 冲突方向得分阈值
    HARMONY_BONUS = 0.15          # 一致性加分
    
    def validate_alignment(
        self,
        m15_dir: str = "neutral",
        m15_str: float = 0.0,
        m15_vol: float = 0.0,
        m15_rsi: float = 50.0,
        m1h_dir: str = "neutral",
        m1h_str: float = 0.0,
        m1h_vol: float = 0.0,
        m1h_rsi: float = 50.0,
        m4h_dir: str = "neutral",
        m4h_str: float = 0.0,
        m4h_vol: float = 0.0,
        m4h_rsi: float = 50.0,
        current_price: float = 0.0,
    ) -> AlignmentResult:
        """
        校验多周期信号一致性并返回对齐分析结果。
        
        直接传入 MarketEnvironment 的各频率字段即可。
        """
        result = AlignmentResult()
        warnings = []
        
        # ── 方向转数值 ──
        def _dir_to_val(d: str) -> float:
            if d in ("bullish", "neutral_bullish"):
                return 1.0
            elif d in ("bearish", "neutral_bearish"):
                return -1.0
            return 0.0
        
        d15 = _dir_to_val(m15_dir)
        d1h = _dir_to_val(m1h_dir)
        d4h = _dir_to_val(m4h_dir)
        
        # 各周期分析存入
        result.freq_analysis = {
            "15m": {"dir": m15_dir, "dir_val": d15, "strength": m15_str, "vol": m15_vol, "rsi": m15_rsi},
            "1h":  {"dir": m1h_dir, "dir_val": d1h,  "strength": m1h_str,  "vol": m1h_vol,  "rsi": m1h_rsi},
            "4h":  {"dir": m4h_dir, "dir_val": d4h,  "strength": m4h_str,  "vol": m4h_vol,  "rsi": m4h_rsi},
        }
        
        # ── 1. 方向一致性评分 ──
        dirs = [d15, d1h, d4h]
        non_zero = [d for d in dirs if d != 0]
        
        if len(non_zero) >= 2:
            # 方向一致性：所有非零方向符号相同 → +1, 有冲突 → -1, 混合 → 0
            signs = [1 if d > 0 else -1 for d in non_zero]
            if len(set(signs)) == 1:
                direction_score = 1.0
            elif any(s * signs[0] < 0 for s in signs[1:]):
                direction_score = -1.0  # 有冲突
            else:
                direction_score = 0.0
        else:
            direction_score = 0.0  # 方向不明确
        
        result.trend_consistency = direction_score
        
        # ── 2. 强度一致性评分 ──
        strengths = [m15_str, m1h_str, m4h_str]
        active_str = [s for s in strengths if s > 0.2]
        if len(active_str) >= 2:
            import numpy as np
            str_std = float(np.std(active_str))
            str_mean = float(np.mean(active_str))
            # 标准差越小 → 强度越一致 → 得分越高
            if str_mean > 0:
                cv = str_std / str_mean
                strength_score = max(0.0, 1.0 - cv * 2)
            else:
                strength_score = 0.0
        else:
            strength_score = 0.5
        
        # ── 3. 波动率协调度 ──
        vols = [m15_vol, m1h_vol, m4h_vol]
        active_vol = [v for v in vols if v > 0]
        if len(active_vol) >= 2:
            import numpy as np
            # 正常: 15m vol > 1h vol > 4h vol (短周期波动天然高于长周期)
            if m15_vol >= m1h_vol >= m4h_vol and all(v > 0 for v in [m15_vol, m1h_vol, m4h_vol]):
                volatility_score = 1.0
            else:
                # 检查是否有异常倒挂（如4h波动大于15m）
                if m4h_vol > 0 and m15_vol > 0 and m4h_vol > m15_vol * 1.5:
                    volatility_score = 0.0
                    warnings.append("波动率倒挂: 4h波动 > 1.5×15m波动，可能处于极端行情")
                else:
                    volatility_score = 0.5
        else:
            volatility_score = 0.5
        
        result.volatility_harmony = volatility_score
        
        # ── 4. RSI 协同度 ──
        rsis = [m15_rsi, m1h_rsi, m4h_rsi]
        active_rsi = [r for r in rsis if r > 0]
        if len(active_rsi) >= 2:
            # 正常: 如果趋势看多, RSI 应该 > 50
            if d4h > 0 and all(r >= 45 for r in active_rsi):
                rsi_score = 1.0
            elif d4h < 0 and all(r <= 55 for r in active_rsi):
                rsi_score = 1.0
            else:
                rsi_score = 0.5
        else:
            rsi_score = 0.5
        
        # ── 综合评分 ──
        raw_score = (
            direction_score * self.DIRECTION_WEIGHT
            + strength_score * self.STRENGTH_WEIGHT
            + volatility_score * self.VOLATILITY_WEIGHT
            + rsi_score * self.RSI_WEIGHT
        )
        # 归一化到 [0, 1]
        result.alignment_score = round(max(0.0, min(1.0, (raw_score + 1.0) / 2.0)), 4)
        
        # ── 对齐状态 ──
        if direction_score >= 1.0 and result.alignment_score >= 0.65:
            result.alignment_status = "aligned"
            result.aligned = True
        elif direction_score <= -1.0:
            result.alignment_status = "conflicting"
            result.aligned = False
        elif result.alignment_score >= 0.45:
            result.alignment_status = "divergent"
            result.aligned = False
        else:
            result.alignment_status = "insufficient_data"
            result.aligned = False
        
        # ── 推荐主导周期 ──
        result.recommended_freq = self._suggest_dominant_freq(
            d15, m15_str, d1h, m1h_str, d4h, m4h_str
        )
        
        # ── 交易许可 ──
        result.permission, result.permission_reason = self._determine_permission(
            result.alignment_status, direction_score, result.alignment_score,
            result.recommended_freq, m4h_str
        )
        
        # ── 杠杆/仓位缩放 ──
        if result.alignment_status == "aligned":
            result.recommended_leverage_scale = 1.10
            result.recommended_position_scale = 1.15
        elif result.alignment_status == "conflicting":
            result.recommended_leverage_scale = 0.60
            result.recommended_position_scale = 0.50
        elif result.alignment_status == "divergent":
            result.recommended_leverage_scale = 0.85
            result.recommended_position_scale = 0.80
        else:
            result.recommended_leverage_scale = 0.70
            result.recommended_position_scale = 0.60
        
        # ── 风险提示 ──
        result.risk_flags = self._generate_risk_flags(
            result.alignment_status, direction_score, warnings,
            m15_rsi, m1h_rsi, m4h_rsi
        )
        result.warnings = warnings
        
        logger.debug(
            f"[MultiFreqAlign] {result.alignment_status}: "
            f"score={result.alignment_score:.3f}, "
            f"dir={direction_score}, perm={result.permission}, "
            f"rec_freq={result.recommended_freq}"
        )
        
        return result

    def _suggest_dominant_freq(
        self,
        d15: float, s15: float,
        d1h: float, s1h: float,
        d4h: float, s4h: float,
    ) -> str:
        """推荐主导周期"""
        # 4h 优先：如果4h有明确方向且强度≥0.5，以4h为主
        if d4h != 0 and s4h >= 0.5:
            return "4h"
        # 1h 次之
        if d1h != 0 and s1h >= 0.4:
            return "1h"
        # 15m 作为补充
        if d15 != 0 and s15 >= 0.3:
            return "15m"
        # 默认选择强度最高的
        scores = {"4h": abs(d4h) * s4h, "1h": abs(d1h) * s1h, "15m": abs(d15) * s15}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "unknown"

    def _determine_permission(
        self,
        status: str,
        dir_score: float,
        align_score: float,
        dominant_freq: str,
        m4h_str: float,
    ) -> Tuple[str, str]:
        """根据对齐状态决定交易许可级别"""
        if status == "aligned":
            return ("full", f"三周期共振({dominant_freq}主导)，可全仓操作")
        elif status == "divergent":
            return ("restricted", f"周期偏离(主导:{dominant_freq})，建议减仓至80%，以主导周期为准")
        elif status == "conflicting":
            return ("critical_only", "多周期方向冲突，仅允许极小仓位试探(≤30%)，或建议观望")
        else:
            return ("denied", "数据不足或周期混乱，禁止交易")
    
    def _generate_risk_flags(
        self,
        status: str,
        dir_score: float,
        warnings: list,
        m15_rsi: float,
        m1h_rsi: float,
        m4h_rsi: float,
    ) -> list:
        """生成风险标记"""
        flags = []
        
        if status == "conflicting":
            flags.append("HIGH: 多周期方向冲突，禁止重仓")
        
        if dir_score <= -0.5:
            flags.append("MEDIUM: 部分周期方向不一致")
        
        # RSI 极端检查
        if m4h_rsi > 75:
            flags.append("WARNING: 4h RSI超买(>75)，注意回调")
        elif m4h_rsi < 25:
            flags.append("WARNING: 4h RSI超卖(<25)，注意反弹")
        
        if m15_rsi > 80 and m1h_rsi > 70:
            flags.append("WARNING: 短周期RSI双双超买")
        elif m15_rsi < 20 and m1h_rsi < 30:
            flags.append("WARNING: 短周期RSI双双超卖")
        
        flags.extend(warnings)
        return flags
    
    def compute_alignment_score_from_env(self, env) -> AlignmentResult:
        """从 MarketEnvironment 对象快速计算对齐结果"""
        return self.validate_alignment(
            m15_dir=env.m15_trend_dir,
            m15_str=env.m15_trend_strength,
            m15_vol=env.m15_volatility_pct,
            m15_rsi=env.m15_rsi,
            m1h_dir=env.m1h_trend_dir,
            m1h_str=env.m1h_trend_strength,
            m1h_vol=env.m1h_volatility_pct,
            m1h_rsi=env.m1h_rsi,
            m4h_dir=env.m4h_trend_dir,
            m4h_str=env.m4h_trend_strength,
            m4h_vol=env.m4h_volatility_pct,
            m4h_rsi=env.m4h_rsi,
            current_price=env.current_price,
        )
    
    def validate_cross_freq_constraints(
        self,
        higher_tf_dir: int,   # 大周期方向: -1/0/1
        lower_tf_dir: int,    # 小周期方向: -1/0/1
    ) -> Tuple[bool, str]:
        """
        跨周期硬约束验证: 小周期方向不得与大周期方向冲突。
        
        Returns:
            (passes, reason)
        """
        if higher_tf_dir == 0:
            return (True, "大周期无方向，不约束")
        
        if lower_tf_dir == 0:
            return (True, "小周期无方向，不约束")
        
        if higher_tf_dir * lower_tf_dir < 0:
            return (False, f"方向冲突: 大周期({higher_tf_dir}) vs 小周期({lower_tf_dir})")
        
        return (True, "方向一致")

    def get_entry_timing_score(
        self,
        env,  # MarketEnvironment
    ) -> float:
        """
        入场时机评分 (0~1):
        - 多周期对齐 + 强趋势 → 高分
        - 周期冲突或弱趋势 → 低分
        """
        result = self.compute_alignment_score_from_env(env)
        
        base = result.alignment_score
        
        # 主导周期强度修正
        dom_freq = result.recommended_freq
        dom_str = {
            "15m": env.m15_trend_strength,
            "1h": env.m1h_trend_strength,
            "4h": env.m4h_trend_strength,
        }.get(dom_freq, 0.0)
        
        score = base * 0.6 + dom_str * 0.4
        
        # RSI 极端减分
        if env.m4h_rsi > 80 or env.m4h_rsi < 20:
            score *= 0.7
        if env.m15_rsi > 85 or env.m15_rsi < 15:
            score *= 0.8
        
        return round(max(0.0, min(1.0, score)), 4)


# ── 全局单例 ──
multi_freq_alignment = MultiFreqAlignment()
