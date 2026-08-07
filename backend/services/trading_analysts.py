"""多路分析师体系 — 每路独立分析，总控综合决策

架构（六路分析师 + K线 + 总控）：
  PositionAnalyst  → 每个持仓的健康状况、方向对齐、风险评估
  MarketAnalyst    → 行情趋势、周期、波动率、支撑阻力
  IntelAnalyst     → 新闻情绪、鲸鱼动向、衍生品信号、恐贪指数
  RiskAnalyst      → 账户风险敞口、保证金使用、回撤、相关性
  StrategyAnalyst  → 策略有效性、胜率、连亏、适配度
  KlineAnalyst     → K线形态分析（可选 LLM，受 KLINE_ANALYST_MODE 控制）

  MasterController → 综合所有分析师报告，调用 LLM 做最终决策

LLM 调用策略：
  - K线分析师: rotate=分批深度分析+缓存复用 / all=每轮全量
  - 总控: 每轮一次 LLM 调用，K线至少保留 1 个 LLM 配额
  - 预算: LLM_MAX_CALLS_PER_CYCLE 控制每轮最大调用次数

设计原则：
  - 分析师全部是规则化计算（快速、确定性、无 LLM 开销）
  - 只有 KlineAnalyst + MasterController 调用 LLM
  - 每路分析独立，可并行执行
  - 输出结构化 dict，方便前端展示
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  Pydantic 输出模型 — 用于 LLM 结构化输出校验
# ══════════════════════════════════════════════════════

class SymbolDecision(BaseModel):
    """单个币种的交易决策（统一模型，不再区分固定周期）"""
    symbol: str
    tier: Optional[str] = None          # 保留字段兼容，不再用于决策分流
    action: Literal["hold", "buy", "sell", "close", "reduce", "pyramid", "dca"]
    confidence: int = Field(ge=0, le=100)
    reasoning: str
    adjust_tp: Optional[float] = None
    adjust_sl: Optional[float] = None
    partial_close_pct: Optional[int] = Field(default=None, ge=0, le=100)
    # ── 新增: AI 动态交易特征（取代固定 tier）──
    trade_nature: Optional[str] = None   # scalp/intraday/swing/position/trend_follow
    expected_hold_hours: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    # AI 策略输出：杠杆与开仓仓位（占可用余额比例）
    # 注：约束放宽并在 before-validator 中归一化，避免 LLM 返回 0/越界值时整轮校验失败
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    position_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v):
        """LLM 偶发返回大写/同义词（BUY/open_long/HOLD/空仓等），统一映射到枚举值。"""
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        mapping = {
            "open_long": "buy", "long": "buy", "buy_long": "buy",
            "open_short": "sell", "short": "sell", "buy_short": "sell",
            "close_long": "close", "close_short": "close", "close_all": "close",
            "flatten": "close", "exit": "close",
            "reduce_position": "reduce", "decrease": "reduce",
            "add": "pyramid", "increase": "pyramid", "加仓": "pyramid",
            "dca_in": "dca", "averaging_down": "dca",
            "do_nothing": "hold", "wait": "hold", "观望": "hold", "no_action": "hold",
        }
        if s in mapping:
            return mapping[s]
        return s  # 让 Literal 约束处理合法值；非法值由下游宽松接受兜底

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, v):
        """LLM 可能返回小数或越界值，钳制到 0–100 整数。"""
        if v is None:
            return 50
        try:
            return max(0, min(100, int(round(float(v)))))
        except (TypeError, ValueError):
            return 50

    @field_validator("leverage", mode="before")
    @classmethod
    def _normalize_leverage(cls, v):
        """LLM 在 hold 时常返回 0 或缺失，统一钳制到 5–20 区间，None 时返回 None 让下游补全。"""
        if v is None or v == "":
            return None
        try:
            lev = float(v)
        except (TypeError, ValueError):
            return None
        if lev <= 0:
            # 0 或负数视为"未提供"，交给 _normalize_decision_sizing 按置信度补全
            return None
        return int(max(5, min(20, round(lev))))

    @field_validator("partial_close_pct", mode="before")
    @classmethod
    def _normalize_partial_close_pct(cls, v):
        """LLM 可能返回小数（25.5）、字符串或越界值，截断为合法整数百分比。"""
        if v is None or v == "":
            return None
        try:
            val = float(v)
        except (TypeError, ValueError):
            return None
        if val <= 0:
            return None
        return max(0, min(100, int(round(val))))

    @field_validator("position_pct", mode="before")
    @classmethod
    def _normalize_position_pct(cls, v):
        if v is None:
            return v
        try:
            pct = float(v)
        except (TypeError, ValueError):
            return v
        if pct > 1.0:
            pct = pct / 100.0
        return round(max(0.04, min(0.35, pct)), 4)


class MasterDecisionOutput(BaseModel):
    """MasterController LLM 输出的完整格式"""
    overall_assessment: str
    risk_level: Literal["low", "medium", "high", "critical"]
    decisions: List[SymbolDecision]

    @field_validator("risk_level", mode="before")
    @classmethod
    def _normalize_risk_level(cls, v):
        """LLM 偶发返回大写/中文/同义词，统一映射到枚举值。"""
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        mapping = {
            "low": "low", "低": "low", "low_risk": "low", "minimal": "low",
            "medium": "medium", "med": "medium", "中": "medium", "moderate": "medium", "normal": "medium",
            "high": "high", "高": "high", "high_risk": "high",
            "critical": "critical", "极高": "critical", "extreme": "critical", "very_high": "critical", "severe": "critical",
        }
        return mapping.get(s, s)


# ══════════════════════════════════════════════════════
#  通用报告结构
# ══════════════════════════════════════════════════════

@dataclass
class AnalystSignal:
    """单条分析信号"""
    symbol: str = ""
    signal: str = ""          # bullish / bearish / neutral / danger / warning
    score: float = 50         # 0=极度看空/危险  50=中性  100=极度看多/安全
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalystReport:
    """分析师报告"""
    analyst: str = ""
    timestamp: str = ""
    risk_score: float = 50    # 0=极度安全  100=极度危险
    summary: str = ""
    signals: List[Dict] = field(default_factory=list)
    recommendation: str = ""  # 一句话建议

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════
#  1. 仓位分析师 — 每个持仓的健康评估
# ══════════════════════════════════════════════════════

class PositionAnalyst:
    """分析每个持仓：PnL、方向对齐、距离止损/止盈、持仓时长"""

    ANALYST_NAME = "仓位分析师"

    def analyze(self, positions: List[Dict], market_data: Dict) -> AnalystReport:
        signals = []
        total_risk = 0
        n_positions = len(positions) if positions else 0

        for pos in (positions or []):
            sym = pos.get("symbol", "")
            side = pos.get("side", "")
            upnl = pos.get("unrealized_pnl", 0)
            margin = pos.get("margin", 0)
            entry = pos.get("entry_price", 0)
            mark = pos.get("mark_price", 0)
            lev = pos.get("leverage", 1)
            sl = pos.get("sl_price", 0)
            tp = pos.get("tp_price", 0)

            pnl_pct = (upnl / margin * 100) if margin > 0 else 0

            # 方向与市场趋势是否对齐
            mkt = market_data.get(sym, {}) if isinstance(market_data, dict) else {}
            trend = ""
            if hasattr(mkt, "trend_direction"):
                trend = mkt.trend_direction
            elif isinstance(mkt, dict):
                trend = mkt.get("trend_direction", "")
                if not trend:
                    orch = mkt.get("orchestrator", {})
                    if isinstance(orch, dict):
                        trend = orch.get("mid_bias", "neutral")

            aligned = self._check_alignment(side, trend)

            # 距离止损距离
            sl_distance_pct = 0
            if sl and entry and mark:
                if side in ("buy", "long"):
                    sl_distance_pct = (mark - sl) / mark * 100 if mark > 0 else 0
                else:
                    sl_distance_pct = (sl - mark) / mark * 100 if mark > 0 else 0

            # 仓位风险评分：0=安全 100=危险
            pos_risk = 50
            if pnl_pct < -20:
                pos_risk = 95
            elif pnl_pct < -10:
                pos_risk = 80
            elif pnl_pct < -5:
                pos_risk = 65
            elif pnl_pct > 10:
                pos_risk = 20
            elif pnl_pct > 5:
                pos_risk = 30

            if not aligned:
                pos_risk = min(100, pos_risk + 15)
            if 0 < sl_distance_pct < 2:
                pos_risk = min(100, pos_risk + 10)

            total_risk += pos_risk

            signal_type = "danger" if pos_risk > 75 else "warning" if pos_risk > 60 else "neutral" if pos_risk > 40 else "bullish"

            signals.append({
                "symbol": sym,
                "signal": signal_type,
                "score": round(100 - pos_risk, 1),
                "detail": (
                    f"{sym} {side} PnL={pnl_pct:+.1f}% "
                    f"{'⚠️方向逆趋势' if not aligned else '✅方向顺趋势'} "
                    f"{'🔴接近止损' if 0 < sl_distance_pct < 2 else ''}"
                ),
                "data": {
                    "pnl_pct": round(pnl_pct, 2),
                    "upnl": round(upnl, 2),
                    "margin": round(margin, 2),
                    "leverage": lev,
                    "trend_aligned": aligned,
                    "sl_distance_pct": round(sl_distance_pct, 2),
                    "trend": trend,
                },
            })

        avg_risk = total_risk / n_positions if n_positions > 0 else 0
        danger_count = sum(1 for s in signals if s["signal"] == "danger")
        warning_count = sum(1 for s in signals if s["signal"] == "warning")

        if n_positions == 0:
            summary = "当前无持仓"
            rec = "无持仓风险"
        elif danger_count > 0:
            summary = f"{n_positions}个持仓中{danger_count}个高危"
            rec = f"建议处理{danger_count}个高危仓位（亏损大或方向逆趋势）"
        elif warning_count > 0:
            summary = f"{n_positions}个持仓中{warning_count}个需关注"
            rec = "部分仓位有风险，建议减仓或设好止损"
        else:
            summary = f"{n_positions}个持仓健康状况良好"
            rec = "持仓状况正常，继续持有"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(avg_risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )

    @staticmethod
    def _check_alignment(side: str, trend: str) -> bool:
        if not trend or trend == "neutral":
            return True
        long_side = side in ("buy", "long")
        trend_bull = trend in ("bullish", "strongly_bullish")
        trend_bear = trend in ("bearish", "strongly_bearish")
        if long_side and trend_bull:
            return True
        if not long_side and trend_bear:
            return True
        if long_side and trend_bear:
            return False
        if not long_side and trend_bull:
            return False
        return True


# ══════════════════════════════════════════════════════
#  2. 行情分析师 — 市场趋势、周期、波动率
# ══════════════════════════════════════════════════════

class MarketAnalyst:
    """分析市场环境：趋势方向、强度、波动率、周期阶段"""

    ANALYST_NAME = "行情分析师"

    def analyze(self, market_envs: Dict[str, Any]) -> AnalystReport:
        """market_envs: {symbol: MarketEnvironment or dict}"""
        signals = []
        risk_scores = []

        for sym, env in (market_envs or {}).items():
            if isinstance(env, dict) and "error" in env:
                continue

            trend = self._get(env, "trend_direction", "unknown")
            strength = self._get(env, "trend_strength", 0)
            vol = self._get(env, "volatility_regime", "normal")
            cycle = self._get(env, "market_cycle", "unknown")
            price = self._get(env, "current_price", 0)
            sentiment = self._get(env, "sentiment_index", 50)

            # 市场风险评分
            risk = 50
            if vol in ("extreme", "very_high"):
                risk = 85
            elif vol == "high":
                risk = 70
            elif vol == "low":
                risk = 30

            if cycle == "bear":
                risk = min(100, risk + 10)
            elif cycle == "bull":
                risk = max(0, risk - 10)

            risk_scores.append(risk)

            signal_type = "danger" if risk > 75 else "warning" if risk > 60 else "neutral" if risk > 35 else "bullish"

            signals.append({
                "symbol": sym,
                "signal": signal_type,
                "score": round(100 - risk, 1),
                "detail": (
                    f"{sym}: 趋势={trend}({strength*100:.0f}%) "
                    f"波动={vol} 周期={cycle} 情绪={sentiment:.0f}"
                ),
                "data": {
                    "trend": trend,
                    "trend_strength": round(strength, 3),
                    "volatility": vol,
                    "cycle": cycle,
                    "price": price,
                    "sentiment": sentiment,
                },
            })

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 50
        high_vol = sum(1 for s in signals if s["data"].get("volatility") in ("high", "extreme"))

        if high_vol > 0:
            summary = f"{len(signals)}个市场中{high_vol}个高波动"
            rec = "市场波动较大，建议谨慎操作，降低仓位"
        elif avg_risk < 35:
            summary = "市场整体环境良好"
            rec = "市场平稳，适合正常交易"
        else:
            summary = f"市场中性，{len(signals)}个交易对在监控中"
            rec = "市场无极端信号，按计划执行"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(avg_risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )

    @staticmethod
    def _get(env, key, default=None):
        if hasattr(env, key):
            return getattr(env, key)
        if isinstance(env, dict):
            return env.get(key, default)
        return default


# ══════════════════════════════════════════════════════
#  3. 情报分析师 — 新闻、鲸鱼、衍生品、情绪
# ══════════════════════════════════════════════════════

class IntelAnalyst:
    """分析情报数据：新闻影响、鲸鱼流向、衍生品信号、恐贪指数"""

    ANALYST_NAME = "情报分析师"

    def analyze(self, intel_data: Dict[str, Any]) -> AnalystReport:
        """intel_data: {symbol: {sentiment_index, whale_direction, derivatives_signal, news_top_event, ...}}"""
        signals = []
        risk_scores = []

        for sym, info in (intel_data or {}).items():
            if not isinstance(info, dict):
                continue

            sentiment = info.get("sentiment_index", 50)
            whale = info.get("whale_direction", 0)
            deriv_signal = info.get("derivatives_signal", "neutral")
            funding = info.get("funding_rate", 0)
            news_event = info.get("news_top_event", "")
            news_impact = info.get("news_impact", 0)

            # 情报风险评分（多维度综合）
            risk = 50
            if sentiment < 20:
                risk = 85
            elif sentiment < 35:
                risk = 70
            elif sentiment > 75:
                risk = 30
            elif sentiment > 60:
                risk = 40

            if abs(whale) > 0.6:
                if whale < 0:
                    risk = min(100, risk + 15)
                else:
                    risk = max(0, risk - 10)
            elif abs(whale) > 0.3:
                if whale < 0:
                    risk = min(100, risk + 8)
                else:
                    risk = max(0, risk - 5)

            if deriv_signal == "bearish":
                risk = min(100, risk + 10)
            elif deriv_signal == "bullish":
                risk = max(0, risk - 10)

            oi_chg = float(info.get("oi_change_1h", 0) or 0)
            if abs(oi_chg) > 3.0:
                risk = min(100, risk + 6)
            liq_long = float(info.get("liquidation_1h_long", 0) or 0)
            liq_short = float(info.get("liquidation_1h_short", 0) or 0)
            if max(liq_long, liq_short) > 5_000_000:
                risk = min(100, risk + 8)

            # 资金费率参与评分：极端费率意味着单边行情可能反转
            if abs(funding) > 0.001:
                risk = min(100, risk + 8)
            elif abs(funding) > 0.0005:
                risk = min(100, risk + 4)

            # 重大新闻影响评分
            if abs(news_impact) > 0.5:
                risk = min(100, risk + int(abs(news_impact) * 10))

            risk_scores.append(risk)

            signal_type = "danger" if risk > 75 else "warning" if risk > 60 else "neutral" if risk > 35 else "bullish"

            whale_text = "🐋流入" if whale > 0.3 else "🐋流出" if whale < -0.3 else "🐋平静"
            news_text = f" | 📰{news_event[:80]}" if news_event else ""

            signals.append({
                "symbol": sym,
                "signal": signal_type,
                "score": round(100 - risk, 1),
                "detail": (
                    f"{sym}: 情绪={sentiment:.0f} {whale_text}({whale:+.2f}) "
                    f"衍生品={deriv_signal} 资金费率={funding:.6f}{news_text}"
                ),
                "data": {
                    "sentiment": sentiment,
                    "whale_direction": whale,
                    "derivatives_signal": deriv_signal,
                    "funding_rate": funding,
                    "news_top_event": (news_event or "")[:200],
                    "news_impact": news_impact,
                },
            })

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 50

        fear_count = sum(1 for s in signals if s["data"].get("sentiment", 50) < 30)
        whale_exit = sum(1 for s in signals if s["data"].get("whale_direction", 0) < -0.3)

        if fear_count > 0:
            summary = f"市场情绪恐惧，{fear_count}个币种情绪低迷"
            rec = "市场恐惧情绪浓厚，建议防守为主"
        elif whale_exit > 0:
            summary = f"{whale_exit}个币种有鲸鱼资金流出"
            rec = "鲸鱼在撤退，注意风险"
        else:
            summary = "情报面整体中性"
            rec = "无重大情报异常，正常执行"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(avg_risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )


# ══════════════════════════════════════════════════════
#  4. 风险分析师 — 账户风险敞口
# ══════════════════════════════════════════════════════

class RiskAnalyst:
    """分析账户风险：总敞口、保证金使用率、回撤、仓位集中度"""

    ANALYST_NAME = "风险分析师"

    def analyze(self, balance: Dict, positions: List[Dict],
                session_stats: Dict) -> AnalystReport:
        signals = []
        risk = 50

        total_equity = balance.get("total_equity", 0) if balance else 0
        available = (balance.get("available_balance", 0) or balance.get("available", 0)) if balance else 0
        total_margin = sum(p.get("margin", 0) for p in (positions or []))
        current_dd = session_stats.get("current_drawdown", 0)
        max_dd = session_stats.get("max_drawdown", 0)
        dd_limit = session_stats.get("max_total_drawdown_pct", 0.30)
        total_pnl = session_stats.get("total_pnl", 0)

        # 保证金使用率
        margin_usage = total_margin / total_equity if total_equity > 0 else 0

        # 回撤离上限的距离
        dd_headroom = dd_limit - current_dd if dd_limit > 0 else 1

        # 持仓集中度（最大单仓占比）
        max_single = 0
        if positions and total_margin > 0:
            max_single = max(p.get("margin", 0) for p in positions) / total_margin

        # ── 同向敞口检查 ──
        long_margin = sum(float(p.get("margin", 0)) for p in (positions or []) if p.get("side") == "long")
        short_margin = sum(float(p.get("margin", 0)) for p in (positions or []) if p.get("side") == "short")
        long_count = sum(1 for p in (positions or []) if p.get("side") == "long")
        short_count = sum(1 for p in (positions or []) if p.get("side") == "short")
        max_side_margin = max(long_margin, short_margin)
        max_side_pct = max_side_margin / total_equity if total_equity > 0 else 0

        # 综合风险评分
        if current_dd > dd_limit:
            risk = 95
        elif dd_headroom < 0.02:
            risk = 85
        elif dd_headroom < 0.05:
            risk = 70
        elif current_dd > 0.10:
            risk = 60
        else:
            risk = 30

        if margin_usage > 0.8:
            risk = min(100, risk + 15)
        elif margin_usage > 0.5:
            risk = min(100, risk + 5)

        if max_single > 0.6:
            risk = min(100, risk + 10)

        # 同向敞口 > 50% 总权益 → 高风险
        if max_side_pct > 0.50:
            risk = min(100, risk + 12)
        elif max_side_pct > 0.35:
            risk = min(100, risk + 5)

        signals.append({
            "symbol": "_ACCOUNT",
            "signal": "danger" if risk > 75 else "warning" if risk > 60 else "neutral" if risk > 35 else "bullish",
            "score": round(100 - risk, 1),
            "detail": (
                f"权益=${total_equity:,.0f} 可用=${available:,.0f} "
                f"保证金使用={margin_usage*100:.1f}% "
                f"回撤={current_dd*100:.1f}%/{dd_limit*100:.0f}% "
                f"总PnL=${total_pnl:+,.0f}"
            ),
            "data": {
                "total_equity": round(total_equity, 2),
                "available": round(available, 2),
                "margin_usage_pct": round(margin_usage * 100, 1),
                "current_drawdown_pct": round(current_dd * 100, 1),
                "max_drawdown_pct": round(max_dd * 100, 1),
                "dd_limit_pct": round(dd_limit * 100, 0),
                "dd_headroom_pct": round(dd_headroom * 100, 1),
                "total_pnl": round(total_pnl, 2),
                "position_concentration": round(max_single * 100, 1),
                "n_positions": len(positions or []),
                "long_margin": round(long_margin, 2),
                "short_margin": round(short_margin, 2),
                "max_side_exposure_pct": round(max_side_pct * 100, 1),
            },
        })

        # ── 同向敞口过高警告 ──
        if max_side_pct > 0.50:
            dom_side = "多(long)" if long_margin > short_margin else "空(short)"
            signals.append({
                "symbol": "_EXPOSURE",
                "signal": "danger",
                "score": 0,
                "detail": f"⚠️ {dom_side}头总敞口${max_side_margin:,.0f} 占权益{max_side_pct*100:.0f}%>50%，单边风险极高",
            })

        # ── 方向一致性警告（3个以上品种同向持仓）──
        if long_count >= 3 or short_count >= 3:
            dom_side = "多" if long_count >= 3 else "空"
            dom_cnt = long_count if long_count >= 3 else short_count
            signals.append({
                "symbol": "_CORRELATION",
                "signal": "warning",
                "score": 30,
                "detail": f"⚠️ {dom_cnt}个品种同时{dom_side}头持仓，高相关风险（crypto高相关性）",
            })

        if risk > 75:
            summary = "账户风险极高，回撤接近上限"
            rec = "建议立即减仓降低风险敞口"
        elif risk > 60:
            summary = "账户风险偏高"
            rec = "建议减少新建仓位，优先管理现有仓位"
        elif risk > 35:
            summary = "账户风险中等"
            rec = "风险可控，但注意仓位管理"
        else:
            summary = "账户风险较低"
            rec = "账户健康，可正常交易"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )


# ══════════════════════════════════════════════════════
#  5. 策略分析师 — 策略有效性评估
# ══════════════════════════════════════════════════════

class StrategyAnalyst:
    """分析策略表现：胜率、连亏、风险收益比、适配度"""

    ANALYST_NAME = "策略分析师"

    def analyze(self, strategies: List[Dict]) -> AnalystReport:
        signals = []
        risk_scores = []

        for strat in (strategies or []):
            sid = strat.get("strategy_id", "")
            name = strat.get("name", sid[:12])
            symbol = strat.get("primary_symbol", "")
            status = strat.get("status", "")
            tier = strat.get("tier", "mid")
            total_trades = strat.get("total_trades", 0)
            win_rate = strat.get("win_rate", 0)  # 已是百分比 (0-100)
            total_pnl = strat.get("total_pnl", 0)
            avg_profit = strat.get("avg_profit", 0)
            avg_loss = strat.get("avg_loss", 0)
            max_drawdown = strat.get("max_drawdown", 0)
            sharpe = strat.get("sharpe_ratio", 0)
            perf_by_regime = strat.get("performance_by_regime", {})

            # 盈亏比 (avg_loss 通常为负值)
            profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else 0

            risk = 50
            if total_trades >= 5:
                # 胜率维度
                if win_rate < 30:
                    risk = 80
                elif win_rate < 40:
                    risk = 65
                elif win_rate > 60:
                    risk = 25
                elif win_rate > 50:
                    risk = 35

                # 盈亏比维度：好的盈亏比降低风险
                if profit_loss_ratio > 2.0:
                    risk = max(0, risk - 15)
                elif profit_loss_ratio > 1.5:
                    risk = max(0, risk - 8)
                elif profit_loss_ratio < 0.8 and profit_loss_ratio > 0:
                    risk = min(100, risk + 10)

                # 回撤维度
                if max_drawdown > 0.20:
                    risk = min(100, risk + 20)
                elif max_drawdown > 0.10:
                    risk = min(100, risk + 10)

                # Sharpe 维度
                if sharpe > 1.5:
                    risk = max(0, risk - 10)
                elif sharpe < 0:
                    risk = min(100, risk + 10)

            if total_pnl < -500:
                risk = min(100, risk + 20)
            elif total_pnl < -200:
                risk = min(100, risk + 10)
            elif total_pnl > 200:
                risk = max(0, risk - 10)

            risk_scores.append(risk)
            signal_type = "danger" if risk > 75 else "warning" if risk > 60 else "neutral" if risk > 35 else "bullish"

            # 构建详细分析文本
            detail_parts = [
                f"{name}[{tier}]: {total_trades}笔 胜率={win_rate:.1f}%",
                f"PnL=${total_pnl:+.2f}",
            ]
            if profit_loss_ratio > 0:
                detail_parts.append(f"盈亏比={profit_loss_ratio:.2f}")
            if max_drawdown > 0:
                detail_parts.append(f"最大回撤={max_drawdown*100:.1f}%")
            if sharpe != 0:
                detail_parts.append(f"Sharpe={sharpe:.2f}")

            # 市况表现摘要
            regime_summary = ""
            if perf_by_regime and isinstance(perf_by_regime, dict):
                regime_parts = []
                for regime_key, regime_data in perf_by_regime.items():
                    if isinstance(regime_data, dict):
                        r_trades = regime_data.get("trades", 0)
                        r_wins = regime_data.get("wins", 0)
                        if r_trades > 0:
                            r_wr = r_wins / r_trades * 100
                            regime_parts.append(f"{regime_key}:{r_wr:.0f}%({r_trades}笔)")
                if regime_parts:
                    regime_summary = " | 市况: " + " ".join(regime_parts[:4])

            detail_text = " ".join(detail_parts) + regime_summary

            signals.append({
                "symbol": symbol,
                "signal": signal_type,
                "score": round(100 - risk, 1),
                "detail": detail_text,
                "data": {
                    "strategy_id": sid,
                    "name": name,
                    "tier": tier,
                    "total_trades": total_trades,
                    "win_rate": round(win_rate, 1),
                    "total_pnl": round(total_pnl, 2),
                    "avg_profit": round(avg_profit, 4),
                    "avg_loss": round(avg_loss, 4),
                    "profit_loss_ratio": round(profit_loss_ratio, 2),
                    "max_drawdown": round(max_drawdown, 4),
                    "sharpe_ratio": round(sharpe, 2),
                    "status": status,
                },
            })

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 50
        poor = sum(1 for s in signals if s["signal"] in ("danger", "warning"))

        if poor > 0:
            summary = f"{len(strategies or [])}个策略中{poor}个表现不佳"
            rec = "部分策略效果差，考虑淘汰或调整"
        elif signals:
            summary = f"{len(strategies or [])}个策略运行正常"
            rec = "策略表现在预期范围内"
        else:
            summary = "当前无活跃策略"
            rec = "需要创建策略才能交易"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(avg_risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )


# ══════════════════════════════════════════════════════
#  5.1 K线深度分析师 — LLM 驱动的多周期技术分析
# ══════════════════════════════════════════════════════

class KlineAnalyst:
    """使用 LLM 深度分析多周期K线数据（5m/15m/1h/4h/1d）。

    职责：
      - K线形态识别（吞没、十字星、锤子线等）
      - 多周期趋势共振分析
      - 关键支撑阻力位计算
      - 成交量异动检测
      - 技术指标综合解读（MA/RSI/MACD/布林带/ATR）

    与其他规则化分析师不同，KlineAnalyst 使用 LLM 做深度分析，
    输出结构化报告供 MasterController 综合。
    """

    ANALYST_NAME = "K线分析师"

    # 五个分析周期
    TIMEFRAMES = {
        "5m":  {"count": 96,  "label": "5分钟",  "role": "超短期入场精准"},
        "15m": {"count": 96,  "label": "15分钟", "role": "短期趋势与形态"},
        "1h":  {"count": 72,  "label": "1小时",  "role": "中期趋势确认"},
        "4h":  {"count": 60,  "label": "4小时",  "role": "波段方向判定"},
        "1d":  {"count": 30,  "label": "日线",   "role": "大周期结构方向"},
    }

    # K线形态关键词（用于规则化快速筛选）
    _BULLISH_PATTERNS = {"bullish_engulf", "hammer", "morning_star", "piercing", "three_white"}
    _BEARISH_PATTERNS = {"bearish_engulf", "shooting_star", "evening_star", "dark_cloud", "three_black"}

    # ── Tier 1 优化: K-line LLM 结果缓存 ──
    # key: f"{symbol}:{input_hash}" → (timestamp, result_dict)
    _llm_cache: Dict[str, tuple] = {}
    # key: symbol → {"price": last_close, "vol": last_volume, "tick": last_tick, "result": cached_result}
    _last_analysis_state: Dict[str, dict] = {}
    # 当前 tick 计数（由 MasterController.reset 时传入或自行跟踪）
    _current_tick: int = 0
    # rotate 模式轮询游标：key=rotate:{account_id}
    _rotate_cursor: Dict[str, int] = {}
    # 持仓币种优先分析（由 run_full_analysis 注入）
    _priority_symbols: List[str] = []

    def _select_rotate_batch(self, symbols: List[str], batch_size: int) -> tuple:
        """轮换选取本轮做 LLM 的币种；返回 (llm_batch, cache_only)。"""
        syms = sorted({str(s).upper() for s in (symbols or []) if s})
        if not syms:
            return [], []

        priority = [
            str(s).upper()
            for s in (getattr(self, "_priority_symbols", None) or [])
            if s
        ]
        ordered: List[str] = []
        seen: set = set()
        for s in priority:
            if s in syms and s not in seen:
                ordered.append(s)
                seen.add(s)
        for s in syms:
            if s not in seen:
                ordered.append(s)
                seen.add(s)

        n = len(ordered)
        bs = max(1, min(int(batch_size or 1), n))
        acct = getattr(self, "_account_id", None) or "default"
        key = f"rotate:{acct}"
        cursor = int(self._rotate_cursor.get(key, 0)) % n
        llm_batch = [ordered[(cursor + i) % n] for i in range(bs)]
        self._rotate_cursor[key] = (cursor + bs) % n
        cache_only = [s for s in ordered if s not in llm_batch]
        return llm_batch, cache_only

    def _restore_cached_signal(self, symbol: str) -> Optional[Dict]:
        """rotate 模式下复用上一轮 LLM 深度结论。"""
        sym = (symbol or "").upper()
        if not sym:
            return None
        _priority_set = {
            str(s).upper()
            for s in (getattr(self, "_priority_symbols", None) or [])
            if s
        }
        if sym in _priority_set:
            return None
        st = self._last_analysis_state.get(sym) or self._last_analysis_state.get(symbol)
        if isinstance(st, dict) and st.get("result"):
            logger.debug(f"[KlineAnalyst] {sym} 复用上轮分析缓存")
            return st["result"]

        from backend.config.settings import KLINE_LLM_CACHE_TTL
        import time as _time
        now = _time.time()
        best: Optional[tuple] = None
        for key, (ts, result) in self._llm_cache.items():
            if not key.startswith(f"{sym}:"):
                continue
            if now - ts > KLINE_LLM_CACHE_TTL:
                continue
            if best is None or ts > best[0]:
                best = (ts, result)
        if best:
            logger.debug(f"[KlineAnalyst] {sym} 复用 LLM 哈希缓存")
            return best[1]
        return None

    @classmethod
    def invalidate_symbol_cache(cls, symbol: str) -> None:
        """编排器 bias 翻转时清除 symbol 级 K 线分析缓存。"""
        sym = (symbol or "").upper()
        if not sym:
            return
        cls._last_analysis_state.pop(sym, None)
        for key in list(cls._llm_cache.keys()):
            if key.startswith(f"{sym}:"):
                cls._llm_cache.pop(key, None)

    def analyze(self, symbols: List[str]) -> AnalystReport:
        """对每个 symbol 执行多周期K线深度分析（支持 rotate/all/off 模式）"""
        from backend.config.settings import (
            KLINE_ANALYST_MODE, KLINE_ROTATE_BATCH_SIZE, KLINE_LLM_MAX_PER_CYCLE,
        )

        if KLINE_ANALYST_MODE == "off":
            return AnalystReport(
                analyst=self.ANALYST_NAME,
                timestamp=datetime.now(timezone.utc).isoformat(),
                risk_score=50,
                summary="K线LLM分析已禁用（KLINE_ANALYST_MODE=off）",
                recommendation="使用规则化回退",
                signals=[],
            )

        symbols_to_analyze = [s for s in (symbols or []) if s]
        llm_symbols: List[str] = []
        cache_symbols: List[str] = []

        if KLINE_ANALYST_MODE == "rotate" and len(symbols_to_analyze) > KLINE_ROTATE_BATCH_SIZE:
            llm_symbols, cache_symbols = self._select_rotate_batch(
                symbols_to_analyze, KLINE_ROTATE_BATCH_SIZE,
            )
            if KLINE_LLM_MAX_PER_CYCLE > 0:
                llm_symbols = llm_symbols[:KLINE_LLM_MAX_PER_CYCLE]
            logger.info(
                f"[KlineAnalyst] rotate 拆分: 本轮LLM={llm_symbols} "
                f"缓存复用={len(cache_symbols)} 总币种={len(symbols_to_analyze)}"
            )
        else:
            llm_symbols = list(symbols_to_analyze)
            if KLINE_LLM_MAX_PER_CYCLE > 0:
                llm_symbols = llm_symbols[:KLINE_LLM_MAX_PER_CYCLE]
            logger.info(
                f"[KlineAnalyst] 全量分析({len(symbols_to_analyze)}): "
                f"LLM={llm_symbols}"
            )

        signals = []
        risk_scores = []
        all_details = []

        from concurrent.futures import ThreadPoolExecutor, as_completed
        _analysis_results: Dict[str, Any] = {}

        def _safe_analyze(sym: str):
            try:
                return sym, self._analyze_symbol(sym)
            except Exception as e:
                logger.warning(f"[KlineAnalyst] {sym} 分析异常: {e}")
                return sym, None

        from backend.config.settings import KLINE_ANALYST_MAX_PARALLEL
        from backend.services.llm_config_service import (
            get_llm_config_for_analysis,
            should_use_llm_streaming,
        )
        _llm_cfg = get_llm_config_for_analysis(getattr(KlineAnalyst, "_account_id", None), tier="quick")
        _stream_mode = should_use_llm_streaming(_llm_cfg)
        _workers = min(max(len(llm_symbols), 1), KLINE_ANALYST_MAX_PARALLEL)
        _as_completed_kw: dict = {}
        if llm_symbols:
            if not _stream_mode:
                from backend.services.llm_config_service import resolve_llm_call_timeout
                _per_call_timeout = resolve_llm_call_timeout(_llm_cfg)
                _batch_count = max(1, (len(llm_symbols) + _workers - 1) // _workers)
                _as_completed_kw["timeout"] = min(_batch_count * _per_call_timeout + 30, 900)
            else:
                _cap = float(os.getenv("LLM_STREAM_SAFETY_CAP_SECONDS", "0") or "0")
                if _cap > 0:
                    _as_completed_kw["timeout"] = _cap
            with ThreadPoolExecutor(max_workers=_workers) as pool:
                futures = {pool.submit(_safe_analyze, s): s for s in llm_symbols}
                for fut in as_completed(futures, **_as_completed_kw):
                    try:
                        sym, result = fut.result()
                        _analysis_results[str(sym).upper()] = result
                    except Exception as e:
                        sym = futures[fut]
                        logger.warning(f"[KlineAnalyst] {sym} 并行分析超时: {e}")
                        _analysis_results[str(sym).upper()] = None

        for symbol in symbols_to_analyze:
            sym_key = str(symbol).upper()
            if sym_key not in _analysis_results:
                cached = self._restore_cached_signal(sym_key)
                if cached:
                    _analysis_results[sym_key] = cached
                else:
                    # rotate 模式：本轮未跑 LLM 且内存缓存为空（常见于进程重启后）
                    # 用规则化技术面回退，避免误报「K线分析失败/缺失」（DB 里 K 线通常仍在）
                    try:
                        kline_data = self._fetch_klines(symbol)
                        if kline_data:
                            snap = self._compute_technical_snapshot(symbol, kline_data)
                            _analysis_results[sym_key] = self._rule_based_analysis(symbol, snap)
                            logger.info(
                                f"[KlineAnalyst] {sym_key} rotate 缓存未命中，使用规则化K线回退"
                            )
                        else:
                            _analysis_results[sym_key] = None
                    except Exception as _rb_err:
                        logger.debug(f"[KlineAnalyst] {sym_key} 规则回退失败: {_rb_err}")
                        _analysis_results[sym_key] = None

        for symbol in symbols_to_analyze:
            sym_key = str(symbol).upper()
            result = _analysis_results.get(sym_key)
            if result:
                signals.append(result["signal"])
                risk_scores.append(result["risk_score"])
                all_details.append(result["detail"])
            else:
                signals.append({
                    "symbol": symbol,
                    "signal": "neutral",
                    "score": 50,
                    "detail": f"K线分析失败或超时",
                    "data": {},
                })

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 50

        if not signals:
            return AnalystReport(
                analyst=self.ANALYST_NAME,
                timestamp=datetime.now(timezone.utc).isoformat(),
                risk_score=50,
                summary="无K线数据可用",
                recommendation="数据不足，建议观望",
            )

        bullish_count = sum(1 for s in signals if s.get("signal") == "bullish")
        bearish_count = sum(1 for s in signals if s.get("signal") == "bearish")

        if bearish_count > bullish_count:
            summary = f"{len(symbols)}个币种中{bearish_count}个偏空，整体承压"
            rec = "多周期技术面偏空，谨慎开多"
        elif bullish_count > bearish_count:
            summary = f"{len(symbols)}个币种中{bullish_count}个偏多，整体偏好"
            rec = "多周期技术面偏多，可寻找顺势机会"
        else:
            summary = f"多空均衡，{len(symbols)}个币种方向分化"
            rec = "技术面方向不明，等待信号共振"

        return AnalystReport(
            analyst=self.ANALYST_NAME,
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_score=round(avg_risk, 1),
            summary=summary,
            signals=signals,
            recommendation=rec,
        )

    def _analyze_symbol(self, symbol: str) -> Optional[Dict]:
        """对单个 symbol 进行多周期K线分析（含 Tier 1 缓存优化）"""
        import hashlib, json as _json_module, time as _time_module

        from backend.config.settings import (
            KLINE_LLM_CACHE_TTL, KLINE_LLM_CACHE_MAX_SIZE,
            KLINE_CHANGE_THRESHOLD_PCT, KLINE_FORCE_REFRESH_EVERY_N_TICKS,
        )

        # 1. 收集多周期K线数据
        kline_data = self._fetch_klines(symbol)
        if not kline_data:
            return None

        # 2. 规则化快速扫描 — 计算基础技术指标
        technical_snapshot = self._compute_technical_snapshot(symbol, kline_data)

        # ── Tier 1: K线 LLM 缓存 + Symbol 级变更检测 ──
        # 计算轻量哈希（仅用每周期最新 close 价 + 趋势方向，避免微小的 OHLCV 变化失效缓存）
        _hash_data = {}
        for tf in sorted(kline_data.keys()):
            candles = kline_data[tf]
            if candles and candles[-1].get("close"):
                _hash_data[f"{tf}_c"] = round(float(candles[-1].get("close", 0)), 2)
            tech = technical_snapshot.get(tf, {})
            if tech:
                _hash_data[f"{tf}_t"] = tech.get("trend", "?")
        _cache_input = _json_module.dumps(_hash_data, sort_keys=True, default=str).encode()
        cache_hash_val = hashlib.md5(_cache_input).hexdigest()
        cache_key = f"{symbol}:{cache_hash_val}"

        now = _time_module.time()

        _priority_set = {
            str(s).upper()
            for s in (getattr(self, "_priority_symbols", None) or [])
            if s
        }
        _force_refresh = (symbol or "").upper() in _priority_set

        # 1A: 检查 LLM 结果缓存（输入哈希未变 + TTL 内）
        cache_miss_reasons = []
        if not _force_refresh and cache_key in self._llm_cache:
            cached_ts, cached_result = self._llm_cache[cache_key]
            age = now - cached_ts
            if age < KLINE_LLM_CACHE_TTL:
                logger.info(
                    f"[KlineAnalyst] {symbol} ✓ 命中LLM缓存 "
                    f"(age={age:.0f}s, TTL={KLINE_LLM_CACHE_TTL}s)"
                )
                return cached_result
            else:
                cache_miss_reasons.append(f"hash命中但TTL过期(age={age:.0f}s)")

        # 1B: Symbol 级变更检测（基于 1h 收盘价）
        ref_price = (
            technical_snapshot.get("1h", {}).get("current")
            or technical_snapshot.get("15m", {}).get("current")
            or technical_snapshot.get("5m", {}).get("current")
            or 0
        )

        prev_state = self._last_analysis_state.get(symbol)
        if not _force_refresh and prev_state and prev_state.get("result") and ref_price > 0:
            prev_price = prev_state.get("price", 0)
            if prev_price > 0:
                price_change = abs(ref_price - prev_price) / prev_price
                ticks_since = self._current_tick - prev_state.get("tick", 0)
                if price_change < KLINE_CHANGE_THRESHOLD_PCT and ticks_since < KLINE_FORCE_REFRESH_EVERY_N_TICKS:
                    logger.info(
                        f"[KlineAnalyst] {symbol} ✓ 价格变化微小 "
                        f"({price_change:.4%} < {KLINE_CHANGE_THRESHOLD_PCT:.2%}), "
                        f"tick差={ticks_since}, 复用上轮结果"
                    )
                    return prev_state["result"]
                elif prev_state.get("tick") is not None:
                    cache_miss_reasons.append(
                        f"价格变化={price_change:.4%}(>={KLINE_CHANGE_THRESHOLD_PCT:.2%}) "
                        f"tick差={ticks_since}(<{KLINE_FORCE_REFRESH_EVERY_N_TICKS}"
                        f"{' ✓' if ticks_since < KLINE_FORCE_REFRESH_EVERY_N_TICKS else ' ✗'})"
                    )

        if cache_miss_reasons:
            logger.info(
                f"[KlineAnalyst] {symbol} ✗ 缓存未命中: {'; '.join(cache_miss_reasons)}"
            )
        elif _force_refresh:
            logger.info(f"[KlineAnalyst] {symbol} 持仓币种强制 LLM 重分析")

        # 3. LLM 深度分析
        llm_analysis = self._llm_deep_analysis(symbol, kline_data, technical_snapshot)

        if llm_analysis:
            # ── 存入缓存 ──
            # 1A: LLM 结果缓存
            if len(self._llm_cache) >= KLINE_LLM_CACHE_MAX_SIZE:
                oldest_key = min(self._llm_cache, key=lambda k: self._llm_cache[k][0])
                del self._llm_cache[oldest_key]
            self._llm_cache[cache_key] = (now, llm_analysis)

            # 1B: 状态追踪
            self._last_analysis_state[symbol] = {
                "price": ref_price,
                "tick": self._current_tick,
                "result": llm_analysis,
            }

            return llm_analysis

        # 4. LLM 失败时用规则化结果
        return self._rule_based_analysis(symbol, technical_snapshot)

    def _fetch_klines(self, symbol: str) -> Dict[str, List[Dict]]:
        """从数据库获取多周期K线；不足时回退交易所 API（与 UnifiedDataPool 一致）。"""
        try:
            from backend.services.kline_data_service import kline_service
        except ImportError:
            return {}

        result = {}
        for tf, cfg in self.TIMEFRAMES.items():
            candles = []
            try:
                candles = kline_service.get_klines_from_db(
                    symbol, tf, count=cfg["count"]
                ) or []
            except Exception as e:
                logger.debug(f"[KlineAnalyst] {symbol} {tf} DB K线失败: {e}")

            if len(candles) < 5:
                try:
                    from backend.services.market_data import get_kline_data
                    api_rows = get_kline_data(symbol, period=tf, count=cfg["count"]) or []
                    if len(api_rows) >= 5:
                        candles = api_rows
                        logger.info(
                            f"[KlineAnalyst] {symbol} {tf} 使用 API 回退 ({len(candles)} 根)"
                        )
                except Exception as api_err:
                    logger.debug(f"[KlineAnalyst] {symbol} {tf} API K线失败: {api_err}")

            if candles and len(candles) >= 5:
                result[tf] = candles

        return result

    def _compute_technical_snapshot(self, symbol: str, kline_data: Dict) -> Dict:
        """规则化计算技术指标快照（不依赖LLM）"""
        snapshot = {}

        for tf, candles in kline_data.items():
            if not candles or len(candles) < 10:
                continue

            closes = [float(c.get("close", 0)) for c in candles if c.get("close")]
            highs = [float(c.get("high", 0)) for c in candles if c.get("high")]
            lows = [float(c.get("low", 0)) for c in candles if c.get("low")]
            volumes = [float(c.get("volume", 0)) for c in candles if c.get("volume")]

            if not closes or len(closes) < 10:
                continue

            current = closes[-1]

            # 移动平均线
            ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else current
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else current
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current

            # RSI (14)
            rsi = self._calc_rsi(closes, 14)

            # 价格相对位置
            period_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            period_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            price_position = (current - period_low) / (period_high - period_low) * 100 if period_high > period_low else 50

            # 成交量变化
            avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 1
            recent_vol = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else avg_vol
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

            # MACD 简化
            ema12 = self._calc_ema(closes, 12)
            ema26 = self._calc_ema(closes, 26)
            macd_val = ema12 - ema26 if ema12 and ema26 else 0

            # 趋势判断
            if current > ma5 > ma10 > ma20:
                trend = "strong_bullish"
            elif current > ma10 > ma20:
                trend = "bullish"
            elif current < ma5 < ma10 < ma20:
                trend = "strong_bearish"
            elif current < ma10 < ma20:
                trend = "bearish"
            else:
                trend = "neutral"

            snapshot[tf] = {
                "current": round(current, 2),
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2),
                "ma20": round(ma20, 2),
                "rsi": round(rsi, 1),
                "macd": round(macd_val, 4),
                "price_position": round(price_position, 1),
                "vol_ratio": round(vol_ratio, 2),
                "trend": trend,
                "period_high": round(period_high, 2),
                "period_low": round(period_low, 2),
                "candle_count": len(candles),
            }

        return snapshot

    @staticmethod
    def _calc_rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _calc_ema(data: List[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _format_kline_summary(self, kline_data: Dict[str, List[Dict]], snapshot: Dict) -> str:
        """将K线数据格式化为 LLM 可读的分析摘要"""
        lines = []

        for tf, cfg in self.TIMEFRAMES.items():
            candles = kline_data.get(tf, [])
            tech = snapshot.get(tf, {})
            if not candles or not tech:
                continue

            # 最近5根K线的OHLCV摘要
            recent = candles[-5:]
            candle_strs = []
            for c in recent:
                o, h, l, cl = float(c.get("open", 0)), float(c.get("high", 0)), \
                              float(c.get("low", 0)), float(c.get("close", 0))
                v = float(c.get("volume", 0))
                change_pct = ((cl - o) / o * 100) if o > 0 else 0
                candle_strs.append(
                    f"[O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{cl:.2f} V:{v:.0f} {change_pct:+.2f}%]"
                )

            trend = tech.get("trend", "?")
            rsi = tech.get("rsi", 50)
            macd = tech.get("macd", 0)
            vol_ratio = tech.get("vol_ratio", 1)
            price_pos = tech.get("price_position", 50)
            ma5, ma10, ma20 = tech.get("ma5", 0), tech.get("ma10", 0), tech.get("ma20", 0)
            ph, pl = tech.get("period_high", 0), tech.get("period_low", 0)

            lines.append(
                f"**{cfg['label']}({tf})** — 角色: {cfg['role']} | "
                f"趋势={trend} RSI={rsi:.1f} MACD={macd:.4f} "
                f"量比={vol_ratio:.2f} 价格位置={price_pos:.0f}%\n"
                f"  MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} | "
                f"区间高={ph:.2f} 低={pl:.2f}\n"
                f"  近5根: {' → '.join(candle_strs)}"
            )

        return "\n\n".join(lines)

    def _llm_deep_analysis(self, symbol: str, kline_data: Dict, snapshot: Dict) -> Optional[Dict]:
        """调用 LLM 进行K线深度分析（受 LLM 预算控制）"""
        import json as _json

        from backend.services.llm_config_service import (
            build_stream_progress_observer,
            get_llm_config_for_analysis,
            call_llm_api_sync,
            should_use_llm_streaming,
        )

        # ── LLM 预算检查 ──
        try:
            from backend.config.settings import KLINE_LLM_MAX_PER_CYCLE
            if hasattr(KlineAnalyst, '_llm_call_count'):
                KlineAnalyst._llm_call_count += 1
                if KLINE_LLM_MAX_PER_CYCLE > 0 and KlineAnalyst._llm_call_count > KLINE_LLM_MAX_PER_CYCLE:
                    logger.warning(
                        f"[KlineAnalyst] K线LLM预算耗尽({KlineAnalyst._llm_call_count}/"
                        f"{KLINE_LLM_MAX_PER_CYCLE}), {symbol} 本轮跳过（请增大 "
                        f"KLINE_LLM_MAX_PER_CYCLE 或减少交易币种数）")
                    return None
            else:
                KlineAnalyst._llm_call_count = 1
        except Exception:
            pass

        llm_config = get_llm_config_for_analysis(getattr(KlineAnalyst, "_account_id", None), tier="quick")
        if not llm_config:
            return None
        if should_use_llm_streaming(llm_config):
            logger.info(
                f"[KlineAnalyst] {symbol} 流式深度分析 {llm_config.model} "
                f"(等 [DONE]，防挂死={os.getenv('LLM_STREAM_SAFETY_CAP_SECONDS', '240')}s)"
            )

        kline_summary = self._format_kline_summary(kline_data, snapshot)

        flow_block = ""
        try:
            from backend.database.connection import SessionLocal
            from backend.services.kline_enrichment_service import (
                capture_flow_indicators_for_symbol,
                normalize_flow_symbol,
            )
            with SessionLocal() as _db:
                flow = capture_flow_indicators_for_symbol(_db, symbol)
            if flow.get("flow_data_ok"):
                flow_block = (
                    f"\n## 订单流（{normalize_flow_symbol(symbol)}，来自 Hyperliquid 成交聚合）\n"
                    f"- 1h CVD累计: {flow.get('cvd_cumulative_1h', 0):,.0f} | "
                    f"当期Δ: {flow.get('cvd_delta_1h', 0):,.0f}\n"
                    f"- 1h Taker买/卖比: {flow.get('taker_ratio_1h', 1):.3f} "
                    f"(买${flow.get('taker_buy_1h', 0):,.0f} / 卖${flow.get('taker_sell_1h', 0):,.0f})\n"
                    f"- 15m Taker比: {flow.get('taker_ratio_15m', 1):.3f}\n"
                )
            else:
                flow_block = (
                    "\n## 订单流\n- 暂无 CVD/Taker 序列（请确认 MarketFlowCollector "
                    "已订阅该币种并运行数分钟）\n"
                )
        except Exception:
            pass

        prompt = f"""你是一个专业的技术分析师，专注于加密货币K线多周期分析。
请基于以下 {symbol} 的多周期K线数据和技术指标，进行深度分析。

## {symbol} 多周期K线数据

{kline_summary}
{flow_block}

## 分析要求（请严格按JSON格式返回）

请分析以下维度：
1. **多周期趋势共振**：5个周期的趋势是否一致？哪些周期矛盾？
2. **关键支撑阻力位**：基于近期高低点、MA、整数关口判断
3. **K线形态识别**：最近K线是否有吞没、十字星、锤子线等经典形态
4. **成交量异动**：量比>1.5代表放量，<0.5代表缩量
5. **RSI超买超卖**：RSI>70超买，<30超卖
6. **综合判断**：多周期加权结论

请返回以下JSON（不要包含其他文本）：
{{
  "direction": "bullish" 或 "bearish" 或 "neutral",
  "confidence": 0到100的整数,
  "summary": "一句话总结",
  "key_patterns": ["识别到的形态1", "形态2"],
  "support_levels": [支撑位1, 支撑位2],
  "resistance_levels": [阻力位1, 阻力位2],
  "trend_resonance": "5周期中有几个方向一致",
  "volume_signal": "放量/缩量/正常",
  "risk_warning": "需要注意的风险点",
  "recommendation": "做多/做空/观望 的具体建议"
}}"""

        try:
            messages = [
                {"role": "system", "content": "你是专业加密货币技术分析师。只返回JSON，不要其他文本。"},
                {"role": "user", "content": prompt},
            ]
            response = call_llm_api_sync(
                llm_config, messages, temperature=0.2, max_tokens=1500,
                response_format={"type": "json_object"},
                account_id=getattr(KlineAnalyst, "_account_id", None),
                caller="KlineAnalyst:deep_analysis",
                progress_observer=build_stream_progress_observer(
                    f"KlineAnalyst:{symbol}",
                ),
            )

            if not response:
                return None

            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return None

            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                return None

            parsed = _json.loads(json_match.group())

            direction = parsed.get("direction", "neutral")
            confidence = min(100, max(0, int(parsed.get("confidence", 50))))
            summary = parsed.get("summary", "")
            patterns = parsed.get("key_patterns", [])
            supports = parsed.get("support_levels", [])
            resistances = parsed.get("resistance_levels", [])
            resonance = parsed.get("trend_resonance", "")
            vol_signal = parsed.get("volume_signal", "")
            risk_warning = parsed.get("risk_warning", "")
            recommendation = parsed.get("recommendation", "")

            # 映射方向到信号
            signal_map = {"bullish": "bullish", "bearish": "bearish"}
            signal_type = signal_map.get(direction, "neutral")

            # 风险评分：bearish=高风险，bullish=低风险
            risk_score = 80 if direction == "bearish" else 25 if direction == "bullish" else 50

            detail = (
                f"{symbol} {direction}({confidence}%) | "
                f"共振={resonance} 量={vol_signal}\n"
                f"支撑={supports} 阻力={resistances}\n"
                f"形态={patterns}\n"
                f"风险={risk_warning} | 建议={recommendation}"
            )

            return {
                "signal": {
                    "symbol": symbol,
                    "signal": signal_type,
                    "score": confidence,
                    "detail": detail,
                    "data": {
                        "source": "llm_deep",
                        "direction": direction,
                        "confidence": confidence,
                        "patterns": patterns,
                        "supports": supports,
                        "resistances": resistances,
                        "resonance": resonance,
                        "vol_signal": vol_signal,
                        "risk_warning": risk_warning,
                        "recommendation": recommendation,
                        "snapshot": snapshot,
                    },
                },
                "risk_score": risk_score,
                "detail": summary,
            }

        except Exception as e:
            logger.warning(f"[KlineAnalyst] LLM分析失败 {symbol}: {e}")
            return None

    def _rule_based_analysis(self, symbol: str, snapshot: Dict) -> Dict:
        """LLM不可用时的规则化回退分析 — 严格模式下无真实K线则只输出 neutral"""
        try:
            from backend.config.settings import STRICT_DATA_GATE
            if STRICT_DATA_GATE:
                has_real = False
                for tf, tech in (snapshot or {}).items():
                    if not isinstance(tech, dict):
                        continue
                    close = float(tech.get("close", 0) or 0)
                    trend = tech.get("trend", "neutral")
                    if close > 0 and trend not in ("neutral", "", None):
                        has_real = True
                        break
                if not has_real:
                    return {
                        "signal": {
                            "symbol": symbol,
                            "signal": "neutral",
                            "score": 0,
                            "detail": f"{symbol} DATA_MISSING:无真实K线趋势，禁止规则假方向",
                            "data": {"direction": "neutral", "blocked": True},
                        },
                        "risk_score": 50,
                        "detail": "严格门控: K线分析师无数据",
                    }
        except Exception:
            pass

        bullish_count = 0
        bearish_count = 0
        details = []

        for tf, tech in snapshot.items():
            trend = tech.get("trend", "neutral")
            rsi = tech.get("rsi", 50)
            vol_ratio = tech.get("vol_ratio", 1)

            if "bullish" in trend:
                bullish_count += 1
            elif "bearish" in trend:
                bearish_count += 1

            details.append(f"{tf}:{trend}/RSI={rsi:.0f}/量比={vol_ratio:.1f}")

        if bullish_count > bearish_count + 1:
            direction = "bullish"
            risk_score = 25
        elif bearish_count > bullish_count + 1:
            direction = "bearish"
            risk_score = 80
        else:
            direction = "neutral"
            risk_score = 50

        return {
            "signal": {
                "symbol": symbol,
                "signal": direction,
                "score": 50,
                "detail": f"{symbol} 规则化分析: {'/'.join(details)} | 方向={direction}",
                "data": {"source": "rule_fallback", "direction": direction, "snapshot": snapshot},
            },
            "risk_score": risk_score,
            "detail": f"规则化回退: {direction}",
        }


def kline_deep_signal_ready(analyst_reports: Optional[Dict], symbol: str) -> tuple:
    """趋势仓门控：是否已有该币种的 K 线 LLM 深度结论（非规则回退/失败）。"""
    sym_u = (symbol or "").upper()
    if not sym_u:
        return False, "symbol 为空"
    kline_rep = (analyst_reports or {}).get("kline")
    if not kline_rep:
        return False, "无 K线分析师报告"
    rep = kline_rep if isinstance(kline_rep, dict) else (
        kline_rep.to_dict() if hasattr(kline_rep, "to_dict") else {}
    )
    for sig in rep.get("signals", []) or []:
        if (sig.get("symbol") or "").upper() != sym_u:
            continue
        detail = str(sig.get("detail") or "")
        if "K线分析失败" in detail or "DATA_MISSING" in detail:
            return False, detail[:120] or "K线分析失败"
        data = sig.get("data") if isinstance(sig.get("data"), dict) else {}
        if data.get("source") == "llm_deep":
            return True, data.get("recommendation") or detail[:80]
        if data.get("resonance") or data.get("recommendation"):
            return True, str(data.get("recommendation") or detail[:80])
    return False, "本轮无该币种 K线 LLM 深度结论"


# ══════════════════════════════════════════════════════
#  5.5 多空辩论层 — 从分析师报告中提取对立论点
# ══════════════════════════════════════════════════════

class DebateLayer:
    """从5路分析师报告中提取多空对立论点（纯规则，零延迟）。

    论文依据: TradingAgents (UCLA/MIT, AAAI 2025) — 辩证式多空辩论
    能减少过度自信的单向决策，提高风险调整后收益。
    """

    _BULL_SIGNALS = {"bullish"}
    _BEAR_SIGNALS = {"bearish", "danger", "warning"}

    def generate_debate(
        self, reports: Dict[str, AnalystReport], symbols: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """为每个 symbol 生成 bull/bear 论点摘要（带信号强度加权）。

        不再简单计数，而是按信号置信度/强度加权：
        - strong_bullish / strong_bearish → 权重 2.0
        - bullish / bearish → 权重 1.0
        - danger / warning → 权重 0.5（间接信号）

        Returns:
            {symbol: {"bull_case": str, "bear_case": str,
                       "bull_count": int, "bear_count": int,
                       "bull_weight": float, "bear_weight": float}}
        """
        sym_set = {s.upper() for s in symbols}
        result: Dict[str, Dict[str, Any]] = {}

        for sym in symbols:
            bull_pts: List[str] = []
            bear_pts: List[str] = []
            bull_weight = 0.0
            bear_weight = 0.0

            for name, report in reports.items():
                if not report:
                    continue
                r = report.to_dict() if isinstance(report, AnalystReport) else (
                    report if isinstance(report, dict) else {})
                analyst_label = r.get("analyst", name)

                for sig in r.get("signals", []):
                    sig_sym = (sig.get("symbol") or "").upper()
                    if sig_sym and sig_sym != sym.upper():
                        continue

                    sig_type = sig.get("signal", "")
                    detail = sig.get("detail", "")
                    if not detail:
                        continue

                    # 信号强度加权
                    _strength_map = {
                        "strong_bullish": 2.0, "strong_bearish": 2.0,
                        "bullish": 1.0, "bearish": 1.0,
                        "danger": 0.5, "warning": 0.5,
                    }
                    _weight = _strength_map.get(sig_type, 0.5)
                    # 附加置信度加权（如果分析师提供了 confidence 字段）
                    _conf = float(sig.get("confidence", 0.5) or 0.5)
                    _weight *= max(0.3, min(1.5, _conf))

                    if sig_type in self._BULL_SIGNALS:
                        bull_pts.append(f"[{analyst_label}](×{_weight:.1f}) {detail}")
                        bull_weight += _weight
                    elif sig_type in self._BEAR_SIGNALS:
                        bear_pts.append(f"[{analyst_label}](×{_weight:.1f}) {detail}")
                        bear_weight += _weight

            result[sym] = {
                "bull_case": " | ".join(bull_pts[:6]) or "暂无明确看多信号",
                "bear_case": " | ".join(bear_pts[:6]) or "暂无明确看空信号",
                "bull_count": len(bull_pts),
                "bear_count": len(bear_pts),
                "bull_weight": round(bull_weight, 1),
                "bear_weight": round(bear_weight, 1),
            }

        return result

    def format_debate_text(
        self, debate: Dict[str, Dict[str, Any]], entry_gate_pct: int = 50,
    ) -> str:
        """将辩论结果格式化为 prompt 文本段落"""
        if not debate:
            return ""
        _gate = int(entry_gate_pct or 50)
        lines = ["## 多空辩论摘要（你必须同时权衡两方论点）\n"]
        for sym, d in debate.items():
            lines.append(f"### {sym}")
            lines.append(f"- 看多({d['bull_count']}条): {d['bull_case']}")
            lines.append(f"- 看空({d['bear_count']}条): {d['bear_case']}")
            lines.append("")
        lines.append(
            "⚠️ 重要：多空辩论结果直接影响决策。\n"
            f"- 支持方论据数 ≥ 反对方 + 1 → 允许按支持方方向开仓（还需满足置信度≥{_gate}%）\n"
            "- 辩论 0:0 表示分析师均未给出明确方向信号（非互锁），此时应根据K线分析、预筛选结果、情报信号等其他维度判断\n"
            "- 辩论平局（如 1:1）视为互锁 → hold，但预筛选通过的标的可结合技术指标单独评估\n"
            "- 支持方仅多 1 条论据时（如 1:0、2:1），属于轻微占优，可配合其他条件开仓\n"
            "- 在 reasoning 中必须说明你如何权衡了正反论点。\n")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════
#  6. 总控 — 综合所有分析师报告做最终决策
# ══════════════════════════════════════════════════════

class MasterController:
    """总控决策器：综合五路分析师报告，调用 LLM 做最终决策"""

    # Phase 3: 实时教训缓存（类变量，跨实例共享）
    _recent_lessons_cache: Dict[str, List[Dict]] = {}
    _recent_lessons_ts: float = 0.0

    def __init__(self):
        self._debate_layer = DebateLayer()
        self._db_session = None

    def _build_factor_signals_prompt_block(self, market_envs: Optional[Dict]) -> str:
        """从 DB 缓存与 market_summary.factor_v3 组装因子 prompt（QAA v3 无 db 时仍可用）。"""
        if not market_envs:
            return ""

        from datetime import datetime as _dt_f

        _factor_lines: List[str] = []
        _now_f = _dt_f.utcnow()
        _seen: set = set()

        def _append_factor_line(sym: str, payload: dict, source: str) -> None:
            sym_u = str(sym).upper()
            if sym_u in _seen or not isinstance(payload, dict):
                return
            _seen.add(sym_u)
            _factor_lines.append(
                f"- {sym_u}: 方向={payload.get('direction_label', 'neutral')} "
                f"强度={float(payload.get('signal_score') or 0):.2f} "
                f"置信={float(payload.get('confidence') or 0):.2f} "
                f"regime={payload.get('regime', '?')} "
                f"({int(payload.get('factor_count') or 0)} 因子, {source})"
            )

        if self._db_session:
            try:
                from backend.database.models import ATASFactorCache

                for _fsym in list(market_envs.keys())[:14]:
                    _row = (
                        self._db_session.query(ATASFactorCache)
                        .filter(ATASFactorCache.cache_key == f"{_fsym}_15m_composite")
                        .first()
                    )
                    if _row is None or not isinstance(_row.value, dict):
                        continue
                    if _row.expires_at and _row.expires_at < _now_f:
                        continue
                    _append_factor_line(_fsym, _row.value, "db")
            except Exception as _db_err:
                logger.debug("[MasterController] 因子 DB 注入跳过: %s", _db_err)

        for _fsym, _info in list(market_envs.items())[:14]:
            if not isinstance(_info, dict):
                continue
            _fv3 = _info.get("factor_v3")
            if isinstance(_fv3, dict):
                _append_factor_line(_fsym, _fv3, "runtime")

        if not _factor_lines:
            return ""

        _factor_lines.insert(
            0,
            "### 📐 量化因子复合信号（21因子引擎/15m — 与你的判断互相验证）",
        )
        _factor_lines.append(
            "使用规则：因子方向与你一致 → 增强开仓信心；因子方向明确相反"
            "且置信≥0.55 → 执行层会硬性否决你的开仓（factor veto），不要强行开反向单。"
        )
        logger.info(
            "[MasterController] 因子信号已注入 prompt: %d symbols (db=%s)",
            len(_seen), bool(self._db_session),
        )
        return "\n".join(_factor_lines)

    def synthesize(
        self,
        reports: Dict[str, AnalystReport],
        symbols: List[str],
        mode: str = "running",
        portfolio: Optional[Dict] = None,
        market_envs: Optional[Dict] = None,
        strategies: Optional[List[Dict]] = None,
        db: Session = None,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """综合所有分析师报告，调用 LLM 做最终决策

        Args:
            reports: {"position": report, "market": report, ...}
            symbols: 交易对列表
            mode: "running" 正常模式 / "defensive" 防守模式
            portfolio: 投资组合信息
            market_envs: 每个 symbol 的完整市场环境数据（含 orchestrator）
            strategies: 策略列表（含 StrategyMemory 真实统计和历史记忆）

        Returns:
            {"decisions": [...], "overall_assessment": str, "risk_level": str}
        """
        import json as _json

        _synth_t0 = time.time()
        logger.info(
            f"[MasterController] synthesize start: symbols={len(symbols or [])}, "
            f"positions={len((portfolio or {}).get('positions', []) if isinstance(portfolio, dict) else [])}, "
            f"mode={mode}"
        )
        self._db_session = db
        self._account_id = account_id
        _build_t = time.time()
        report_text = self._build_report_text(
            reports, mode, portfolio, market_envs=market_envs, strategies=strategies,
            symbols=symbols
        )
        logger.info(
            f"[MasterController] report_text built: len={len(report_text)}, "
            f"elapsed={time.time() - _build_t:.2f}s"
        )

        # Phase 2: 追加长上下文扩展（根据 AI_EVOLUTION_LEVEL 控制）
        try:
            _level = int(os.getenv("AI_EVOLUTION_LEVEL", "0"))
            if _level >= 2:
                _extended = self._build_extended_context(
                    symbols=symbols,
                    market_envs=market_envs,
                    strategies=strategies,
                )
                if _extended:
                    report_text = report_text + "\n\n" + _extended
                    logger.info(
                        f"[MasterController] 长上下文扩展追加: +{len(_extended)} chars, "
                        f"total={len(report_text)}"
                    )
        except Exception as _ext_err:
            logger.debug(f"[MasterController] 长上下文扩展跳过: {_ext_err}")

        symbols_text = ", ".join(symbols)

        # 多空辩论层：从分析师报告提取对立论点
        debate = self._debate_layer.generate_debate(reports, symbols)

        # [tier-aware v1] 从 portfolio.balance 提取 tier 上下文，
        # 让 LLM 知道当前是哪个 tier 的独立分析、该 tier 是否已有持仓
        _tier_context_text = ""
        _entry_gate = 50
        try:
            from backend.config.settings import V5_SCALP_MIN_CONFIDENCE as _V5_SCALP_RAW
        except Exception:
            _V5_SCALP_RAW = 70
        # 2026-06-18: paper 模式 scalp gate 放宽到 50（与执行层 unified_gate paper 放宽一致）
        _is_paper_mode = str(mode).lower() != "live"
        _V5_SCALP = 50 if _is_paper_mode else _V5_SCALP_RAW
        _scalp_gate = _V5_SCALP
        try:
            _bal = (portfolio or {}).get("balance", {}) if isinstance(portfolio, dict) else {}
            _cur_tier = str(_bal.get("_tier", "")).strip().lower() if isinstance(_bal, dict) else ""
            _cur_tier_label = _bal.get("_tier_label", "") if isinstance(_bal, dict) else ""
            _cur_tier_budget = float(_bal.get("_tier_budget", 0) or 0) if isinstance(_bal, dict) else 0
            _cur_tier_used = float(_bal.get("_tier_margin_used", 0) or 0) if isinstance(_bal, dict) else 0
            _cur_tier_avail = float(_bal.get("_tier_margin_available", 0) or 0) if isinstance(_bal, dict) else 0
            _tier_positions = portfolio.get("positions", []) if isinstance(portfolio, dict) else []
            _symbols_upper = {s.upper() for s in (symbols or [])}
            _same_tier_syms = set()
            _orphan_tier_syms = set()
            if isinstance(_tier_positions, list):
                for _p in _tier_positions:
                    if isinstance(_p, dict):
                        _s = (_p.get("symbol") or "").upper()
                        _same_tier_syms.add(_s)
                        if _s and _s not in _symbols_upper:
                            _orphan_tier_syms.add(_s)
            _missing_syms = [s for s in (symbols or []) if s.upper() not in _same_tier_syms]
            # P5-fix(2026-05-08): 提取本 tier 每个持仓的关键状态（让 LLM 看到详情而非只看 symbol 列表）
            _tier_pos_lines: list = []
            for _p in (_tier_positions or []):
                if not isinstance(_p, dict):
                    continue
                _sym_p = (_p.get("symbol") or "").upper()
                _side_p = (_p.get("side") or "").lower()
                _entry_p = float(_p.get("entry_price") or 0)
                _mark_p = float(_p.get("mark_price") or 0)
                _margin_p = float(_p.get("margin") or 0)
                _upnl_p = float(_p.get("unrealized_pnl") or 0)
                _lev_p = float(_p.get("leverage") or 0)
                _pnl_pct = (_upnl_p / _margin_p * 100.0) if _margin_p > 0 else 0.0
                _tp_p = _p.get("tp_price") or _p.get("take_profit_price")
                _sl_p = _p.get("sl_price") or _p.get("stop_loss_price")
                _add_cnt = _p.get("add_count", 0) or 0
                _dca_cnt = _p.get("dca_count", 0) or 0
                _reduce_cnt = _p.get("reduce_count", 0) or 0
                _health = _p.get("trend_health") or {}
                _reversal = _p.get("reversal_signal") or {}
                _health_str = ""
                if isinstance(_health, dict) and _health:
                    _health_str += (
                        f" 健康分={float(_health.get('score', 0) or 0):.0f}"
                        f"/阈值{float(_health.get('nature_adjusted_threshold', 0) or 0):.0f}"
                        f"({str(_health.get('regime', 'unknown'))})"
                    )
                if isinstance(_reversal, dict) and _reversal:
                    _health_str += (
                        f" 反转={str(_reversal.get('level', 'none'))}"
                        f"/紧迫{int(float(_reversal.get('urgency', 0) or 0))}"
                    )
                _tp_str = f" TP=${float(_tp_p):.4f}" if _tp_p else " TP=未设"
                _sl_str = f" SL=${float(_sl_p):.4f}" if _sl_p else " SL=未设"
                _ops_str = ""
                if _add_cnt: _ops_str += f" 加仓{_add_cnt}次"
                if _dca_cnt: _ops_str += f" 补仓{_dca_cnt}次"
                if _reduce_cnt: _ops_str += f" 减仓{_reduce_cnt}次"
                # 孤立持仓标注
                _orphan_mark = " [⚠ 已移出交易对，建议评估平仓]" if _sym_p in _orphan_tier_syms else ""
                _tier_pos_lines.append(
                    f"  • {_sym_p} {_side_p} size={_p.get('size', 0)} entry=${_entry_p:.4f} "
                    f"mark=${_mark_p:.4f} | 保证金=${_margin_p:.0f}({_lev_p:.0f}x) "
                    f"PnL=${_upnl_p:+.2f}({_pnl_pct:+.2f}%){_tp_str}{_sl_str}"
                    f"{_health_str}{_ops_str}{_orphan_mark}"
                )
            _tier_pos_detail_text = "\n".join(_tier_pos_lines) if _tier_pos_lines else "  (本 tier 暂无持仓)"

            if _cur_tier in ("short", "mid", "long"):
                from backend.services.entry_confidence_gate import resolve_entry_gate_pct
                _gate_list = []
                for _gsym in (symbols or []):
                    _ginfo = (market_envs or {}).get(_gsym, {}) if isinstance(market_envs, dict) else {}
                    _gorch = _ginfo.get("orchestrator", {}) if isinstance(_ginfo, dict) else {}
                    _gate_list.append(
                        resolve_entry_gate_pct(_cur_tier, _ginfo.get("regime", ""), _gorch)
                    )
                _entry_gate = max(_gate_list) if _gate_list else resolve_entry_gate_pct(_cur_tier, "", {})
                # V5: scalp 门槛不再随 entry_gate 走低，硬下限 70
                _scalp_gate = max(_V5_SCALP, _entry_gate + 8)

                # ── 成熟度/探索期感知（2026-06-13）──
                #    全局可能已 mature，但「无历史成交样本的新币」在执行层 resolve_relief
                #    里按 symbol+side 仍判为 warmup（任一维度 warmup→放宽，floor 40 放行）。
                #    因此这里不按全局阶段、而按「paper 模式 + 新币探索期」放宽提示词门槛，
                #    与执行层 per-symbol 行为对齐，让 LLM 敢对同向弱信号的新币开小仓累积样本。
                #    live 模式（真金）一律严格，不放宽。
                _is_paper = str(mode).lower() != "live"
                # 2026-06-18: prompt 门槛与执行层 paper 放宽同步。
                # 原来这里硬编码 70/72/1.8（和执行层不一致），AI 看到"scalp≥70%"自己拒绝开仓。
                # 现 paper 模式用放宽值（与 unified_gate 的 paper 放宽层一致），live 保持严格。
                _exec_swing_floor = 50
                _exec_ranging_extra = 10
                _exec_scalp_floor = 50 if _is_paper else 70   # paper 50, live 70
                _exec_trend_floor = 55 if _is_paper else 72   # paper 55, live 72
                _exec_min_rr = 1.3 if _is_paper else 1.8      # paper 1.3, live 1.8
                _exec_ranging_floor = _exec_swing_floor + _exec_ranging_extra
                # 新币（无历史样本）探索期放宽后的门槛（大胆放宽口径）
                _warm_swing_floor = 45
                _warm_scalp_floor = 50
                _warm_trend_floor = 55
                # 在候选(尚无持仓)币种里挑出「无任何历史成交样本」的新币——
                # 这类币在执行层 resolve_relief 按 symbol+side 判 warmup、自动放行(floor40)，
                # 显式列出让 LLM 知道对哪些币用放宽门槛。
                _warm_new_syms: list = []
                if _is_paper and _missing_syms:
                    try:
                        from backend.services.maturity_controller import get_maturity_state
                        _mstate = get_maturity_state() or {}
                        _bss = _mstate.get("by_symbol_side") or {}
                        for _ms in _missing_syms:
                            _mu = str(_ms).upper()
                            _has_hist = any(
                                str(_k).split("|", 1)[0].upper() == _mu for _k in _bss.keys()
                            )
                            if not _has_hist:
                                _warm_new_syms.append(_ms)
                    except Exception:
                        # 状态读不到时，保守把全部候选当新币放宽（探索期鼓励出手）
                        _warm_new_syms = list(_missing_syms)
                _warmup_clause = ""
                if _is_paper:
                    _new_syms_txt = ", ".join(_warm_new_syms) if _warm_new_syms else "（本轮无新币）"
                    _warmup_clause = (
                        "\n8. **【模拟盘探索期放宽 — 对「无历史成交样本的新币」生效】**：目标是攒样本而非苛求完美机会。\n"
                        f"   - **本轮享受放宽门槛的新币**：{_new_syms_txt}\n"
                        f"   - 对上述新币：开仓门槛放宽至 "
                        f"**swing≥{_warm_swing_floor}% / scalp≥{_warm_scalp_floor}% / trend_follow≥{_warm_trend_floor}%**"
                        f"（执行层对这类新币会自动放行，floor 40）；不在该列表、已有充足历史的币仍按上面严格门槛。\n"
                        "   - 对新币：只要 **编排器 final_side 与开仓方向一致** 且达到上述放宽门槛即可开仓；**不要因为长线与中线方向矛盾就对新币一律 hold**。\n"
                        "   - 长/中线方向矛盾时：**允许按中/短线方向开 swing/scalp 小仓**（仓位会自动偏小），仅**禁止**开 trend_follow/position 趋势仓。\n"
                        "   - 弱但同向（如中线 bullish 40-50%）的新币信号值得开小仓试错——盈亏都会进入学习库，这正是探索期的价值；过度保守会让系统学不到东西。"
                    )

                _default_nature = {"short": "intraday", "mid": "swing", "long": "trend_follow"}.get(_cur_tier, "swing")
                _tier_conf_key = {"short": "短线置信度", "mid": "中线置信度", "long": "长线置信度"}.get(_cur_tier, "中线置信度")
                _tier_context_text = f"""
## 🎯 当前分析上下文 — 周期专属独立决策（必须严格遵守）
**本轮你只为 `{_cur_tier_label}({_cur_tier})` tier 做决策。** 其他 tier(short/mid/long) 由独立线程分析，不要替它们决定。

**该 tier 持仓状况**
- 当前 `{_cur_tier}` tier 已持仓的币种: {sorted(_same_tier_syms) if _same_tier_syms else '无（全部空仓）'}{f" (其中 {_sorted_orphan} 已移出交易对，建议评估平仓)" if (_sorted_orphan := sorted(_orphan_tier_syms)) else ""}
- **本 tier 尚无持仓、可评估进场的币种**: {_missing_syms if _missing_syms else '无'}
- 本 tier 预算: ${_cur_tier_budget:.0f} / 已用 ${_cur_tier_used:.0f} / 可用 ${_cur_tier_avail:.0f}
- 本 tier 默认 trade_nature: `{_default_nature}`（short: scalp/intraday；mid: swing；long: trend_follow/position）
- 本 tier 对应置信度维度: **{_tier_conf_key}**（只参考这个维度，忽略其他 tier 的置信度）

### 📌 本 tier 持仓详情（你必须为下面每一个持仓给出明确管理决策）
{_tier_pos_detail_text}

### 🔥 铁律（V5 高确信优先 — 宁缺毋滥）
1. **空 tier 出手条件**：对「本 tier 尚无持仓」的币种：
   - {_tier_conf_key} ≥ **{_entry_gate}%**（与编排器分层置信度+行情 regime 联动）
   - ⚠️ **执行层 V5 闸门的真实门槛（已有历史样本的币，不满足直接拦截不执行）**：基础 {_exec_swing_floor}% + 震荡市(ranging) 额外 +{_exec_ranging_extra} → **震荡市需要 ≥{_exec_ranging_floor}%**；scalp 硬下限 **{_exec_scalp_floor}%**；**trend_follow/position 趋势仓硬下限 {_exec_trend_floor}%**；TP:SL 必须 ≥ {_exec_min_rr}:1。confidence 未达对应门槛时直接给 hold。**（注：无历史样本的新币门槛已在下方铁律放宽，见探索期条款。）**
   - 多空辩论中支持方论据数 ≥ 反对方 + **1**（0:0 无信号时此条不适用，改为看K线/预筛选；1:1 互锁视为 hold）
   - 编排器 final_side 与开仓方向一致（swing/scalp/intraday 逆势仅当 confidence≥75% 且编排器置信<50%）
   **以上三条全部满足**才 buy/sell；同时必须能给出 TP:SL ≥ {_exec_min_rr}:1 的合理出场结构，否则 hold。
2. **预筛选通过 ≠ 必须开仓**：技术指标预筛选只是入围条件，开仓还需方向证据与盈亏结构同时成立。
3. **hold 是合法且常见的专业决策**：每个标的**必须给出明确的偏多/偏空/中性判断**。
   - ✅ 合法理由：「{_tier_conf_key} 仅 48% < {_entry_gate}% 门槛 → hold」
   - ✅ 合法理由：「多空辩论 1:1 互锁 → hold」（注：0:0 是无信号非互锁，需看其他维度决策）
   - ✅ 合法理由：「盈亏比无法达到 1.8:1 → hold」
4. **跨 tier 独立**：其他 tier 是否已持有同 symbol 仓位，**不是本 tier 不开仓的理由**。它们是独立子仓，各自管理盈亏。
5. **置信度对应**：本 tier 的 action 和 confidence 必须与 **{_tier_conf_key}** 对齐；不要用长线置信度否决短线决策，也不要用短线噪声否决中线决策。**confidence 反映你对方向判断的确信程度，而非"我是否决定开仓"**——hold 也可以有 30-45% 的 confidence（说明有倾向但未达门槛）。
6. **nature 约束**：本 tier 如果开仓，trade_nature 必须属于该 tier 范围（short→scalp/intraday；mid→swing；long→trend_follow/position），默认用 `{_default_nature}`。
7. **持仓必须管理（P5 新增）**：上面"持仓详情"里列的每个持仓，**本轮必须有对应的明确决策**——hold/pyramid/dca/reduce/close 任选其一，并在 reasoning 里给出对当前 PnL/趋势/SL 距离的具体判断；**不允许跳过**任何持仓不评估。
   - hold 也必须显式给出（reasoning 写"为什么继续持有"+"下次评估的触发条件"）
   - pnl_pct ≥ +1.5% 且趋势继续 → 优先评估 pyramid
   - pnl_pct ∈ [-8%, -2%] 且原方向仍成立 → 评估 dca
   - 趋势反转 / pnl_pct < -8% / SL 距离 < ATR×0.5 → 评估 reduce 或 close
   - SL/TP 缺失 → 必须给出 adjust_sl 或 adjust_tp 决策
{_warmup_clause}

### 预期决策分布
- 多周期并行架构的目标是捕捉**净扣费后有数学期望**的交易机会，而非提高出手次数。
- 出手次数不是 KPI；证据不足时 hold 优于勉强开仓。
- 震荡市噪声多、手续费侵蚀重，开仓门槛自动收紧；趋势市顺势机会才是主要利润来源。
"""
                if _cur_tier == "long":
                    _tier_context_text += """
### 📈 长线趋势仓专规（合约主攻方向）
- **默认 trade_nature=trend_follow**：合约收益应主要来自顺势趋势仓，不靠频繁短线刷单。
- **开趋势仓前必须在 reasoning 写清「局势判断」**（缺一不可）：
  1. **宏观/战略面**：战略分析师报告中的周期阶段、macro_bias、风险预算是否与方向一致
  2. **多周期共振**：编排器 long_view 与 mid_view 同向（short 不得强反向）
  3. **趋势质量**：ADX/均线结构支持延续，不是震荡假突破
- **禁止开趋势仓**：编排器 wait/frozen；long 与 mid 方向矛盾；ranging 且长线置信度<65%；战略面与方向冲突
- **K线深度分析必看**：上方「K线分析师」报告须有多周期共振/支撑阻力结论；无深度 K 线结论不得开 trend_follow
- **持仓管理**：趋势仓让利润奔跑；减仓需浮盈>5%且间隔>24h；除非多周期全反转否则不轻易平仓
"""
                # ── P2 D15: 注入 tier 专属战略提示（杠杆/持仓/SL 等硬约束） ──
                try:
                    from backend.services.risk_band_resolver import get_tier_prompt_hint
                    _tier_hint = get_tier_prompt_hint(_cur_tier)
                    if _tier_hint:
                        _tier_context_text += f"""
### ⚡️ 本 tier 战略铁律（P2 D15 — 2026-04 迭代新增）
> {_tier_hint}

（违反会被系统拦截：例如 long tier 如果你给出 15x 杠杆，将被强制降到 {"{"}long cap{"}"}，白白浪费 AI 的一次出手。）
"""
                except Exception as _hint_err:
                    logger.debug(f"[MasterController] tier hint 注入失败(非致命): {_hint_err}")
        except Exception as _ctx_err:
            logger.debug(f"[MasterController] tier context 注入失败(非致命): {_ctx_err}")
            _tier_context_text = ""

        _hold_timeout_text = ""
        try:
            _bal_ht = (portfolio or {}).get("balance", {}) if isinstance(portfolio, dict) else {}
            _ht_alerts = _bal_ht.get("_hold_timeout_alerts") or []
            if _ht_alerts:
                _lines = [
                    "## ⏰ 持仓时限复审（必须由你决策 — 系统不会到点自动强平）",
                    "以下仓位已到达或接近 **tier 复审点**（仅 **中线/长线**）。",
                    "**短线 scalp/intraday 不在此列表**：禁止 extend_hold_hours，超时由系统硬平，交给 TP/SL。",
                    "你需要结合大趋势判断：**平仓 / 减仓 / 续持 / 延长上限**。",
                    "",
                ]
                _has_expired = any(bool(a.get("expired")) for a in _ht_alerts)
                for a in _ht_alerts:
                    _st = "已超过上限" if a.get("expired") else "到达复审点"
                    _lines.append(
                        f"- **{a.get('symbol')}** {a.get('side')} "
                        f"[pos_id={a.get('position_id')}] [{a.get('tier')}/{a.get('trade_nature')}] "
                        f"{_st}: 已持{a.get('hold_age_hours')}h / 当前上限{a.get('max_hold_hours')}h "
                        f"(复审点{a.get('review_hold_hours', a.get('max_hold_hours'))}h, "
                        f"进度{a.get('hold_progress_pct')}%, 已复审{a.get('review_count',0)}轮)"
                    )
                    if a.get("summary"):
                        _lines.append(f"  - {a['summary']}")
                _lines.extend([
                    "",
                    "### 决策原则（时限复审专用）",
                    "1. **大趋势仍与持仓同向**（编排器 long/mid/short 至少两档同向、置信度≥55%）："
                    "  优先 **hold + extend_hold_hours**（延长 4~16 小时，仅 mid/long），并 tighten adjust_sl；不要仅因到点就 close。",
                    "2. **趋势反转或浮亏扩大**：close 或 reduce，并说明关键证据。",
                    "3. **临近复审点但未超时**：可提前 reduce 锁利，或 extend_hold_hours 给趋势延续空间。",
                    "4. 选择续持/延长时，必须在 reasoning 写明：为何大趋势仍支持、下一复审条件。",
                    "5. **extend_hold_hours** 仅对 mid/long 已有持仓填写；短线必须填 0 或省略。",
                    "6. 禁止以「观望」「等待」为由跳过这些持仓 — 每个都要出现在 decisions 里。",
                ])
                if _has_expired:
                    _lines.extend([
                        "",
                        "### ⚠️ 已超过上限 — 强制决策（不可只写 hold）",
                        "对标记为「已超过上限」的仓位，**禁止**仅回复 hold 而不给出 extend_hold_hours 或 close/reduce。",
                        "必须二选一：① **close/reduce**（趋势反转/深亏/逻辑失效） "
                        "② **extend_hold_hours=4~16**（趋势仍同向，写明延长理由）。",
                        "若已复审≥2轮仍无动作，优先 close 或 reduce，不要无限续命。",
                    ])
                _hold_timeout_text = "\n".join(_lines) + "\n"
        except Exception as _ht_prompt_err:
            logger.debug(f"[MasterController] hold_timeout 注入失败: {_ht_prompt_err}")

        debate_text = self._debate_layer.format_debate_text(debate, entry_gate_pct=_entry_gate)

        # Phase 3: 因子对齐检查（根据 AI_EVOLUTION_LEVEL >= 3）
        _factor_alignment = ""
        try:
            _level = int(os.getenv("AI_EVOLUTION_LEVEL", "0"))
            if _level >= 3:
                _factor_alignment = self._build_factor_alignment_check(market_envs)
                if _factor_alignment:
                    _tier_context_text = _factor_alignment + "\n" + _tier_context_text
                    logger.info("[MasterController] 因子对齐检查已注入")
        except Exception as _fa_err:
            logger.debug(f"[MasterController] 因子对齐检查跳过: {_fa_err}")

        # ── P2-3: 注入最近该币种盈亏教训 (strategy_memories key_lessons) ──
        _recent_lessons_text = ""
        try:
            from backend.database.models import StrategyMemory
            _rel_lessons: list = []
            _seen_lessons: set = set()
            if db is not None:
                _strategy_ids = set()
                for _s in (strategies or []):
                    _sid = _s.get("strategy_id") if isinstance(_s, dict) else getattr(_s, "strategy_id", None)
                    if _sid:
                        _strategy_ids.add(_sid)
                if _strategy_ids:
                    _mems = db.query(StrategyMemory).filter(
                        StrategyMemory.strategy_id.in_(_strategy_ids)
                    ).all()
                    from backend.services.lesson_utils import lesson_dedupe_key
                    for _mem in _mems:
                        _kl = _mem.key_lessons or []
                        for _lesson in (_kl or [])[-10:]:
                            if not isinstance(_lesson, dict):
                                continue
                            _lkey = lesson_dedupe_key(_lesson)
                            if _lkey in _seen_lessons:
                                continue
                            _seen_lessons.add(_lkey)
                            _rel_lessons.append(_lesson)
                _global = db.query(StrategyMemory).filter(StrategyMemory.strategy_id == "_global_").first()
                if _global and _global.key_lessons:
                    from backend.services.lesson_utils import lesson_dedupe_key
                    for _lesson in (_global.key_lessons or [])[-5:]:
                        if isinstance(_lesson, dict) and _lesson.get("source") == "opencode":
                            _lkey = lesson_dedupe_key(_lesson)
                            if _lkey not in _seen_lessons:
                                _seen_lessons.add(_lkey)
                                _rel_lessons.append(_lesson)
            if _rel_lessons:
                _rel_lessons = _rel_lessons[-5:]
                _lines = []
                for _l in _rel_lessons:
                    _sev = _l.get("severity", "info")
                    _sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "info": "🔵"}.get(_sev, "⚪")
                    _lines.append(f"{_sev_icon} [{_l.get('type','')}] {_l.get('symbol','?')}[{_l.get('tier','?')}] @{_l.get('regime','?')} — {_l.get('lesson','')}")
                _recent_lessons_text = f"""
## 📊 P2-3 反馈闭环：最近交易教训（从 strategy_memories.key_lessons 实时注入）
> 以下是从相同策略最近交易中自动提炼的教训，可参考以优化仓位/止损，但不作为开仓禁令。

{chr(10).join(_lines)}

**💡 决策时可将上述教训作为仓位管理与止损参考。以当前行情信号为主要依据，历史教训不禁止同方向开仓。**
"""
        except Exception as _lesson_err:
            logger.debug(f"[MasterController] lessons 注入失败(非致命): {_lesson_err}")
            _recent_lessons_text = ""

        if mode == "defensive":
            action_constraint = """## 重要约束：当前为防守模式
你只能做以下操作（禁止开新仓/加仓/补仓）：
- hold: 继续持有（趋势仍有利或亏损有限） ← 优先选择
- adjust_sl: 调整止损（收紧SL降低风险） ← 优先选择
- reduce: 减仓（降低风险敢口）
- close: 全部平仓（方向错误/亏损过大/趋势逆转）
⚠️ 防守模式下也可以调整 TP/SL（adjust_tp/adjust_sl），并可通过 partial_close_pct 主动止盈

## 防守模式操作权限约束（分层管理）
- 轻微亏损(0~-2%): 只能hold（可建议调整SL）
- 中度亏损(-2%~-5%): 允许reduce最多25%
- 严重亏损(<-5%): 允许close或设紧急SL
- 已减仓≥2次的仓位: 强制hold
请优先选择"hold"和"adjust_sl"，减少不必要的减仓操作。"""
        else:
            action_constraint = """## 可执行操作（7种）
- buy: 新开多仓（该 symbol+tier 无同向持仓时）
- sell: 新开空仓（该 symbol+tier 无同向持仓时）
- pyramid: 顺势加仓（已有同向持仓且盈利时追加，金字塔式）
- dca: 逆势补仓（已有同向持仓且亏损但趋势未变时补仓降均价）
- reduce: 减仓（趋势反转/亏损过大）
- close: 全部平仓
- hold: 不操作

## pyramid（顺势加仓）使用规则
- 仅在已有仓位盈利 >= 1.5% 时考虑
- 趋势必须仍然明确（不是震荡区间）
- 每个仓位最多加仓2次（第1次加仓50%、第2次25%的原始仓位）
- 加仓不是贪婪，是在趋势确认后适度追加

## dca（逆势补仓）使用规则 — 极其谨慎！
- 仅在亏损 2%~8% 之间，且你仍然坚信原方向正确时使用
- 必须满足：中线+长线仍支持原方向
- 亏损 > 8% 绝对禁止补仓（应该止损，不是补仓）
- 补仓量 = 原始仓位的30%，最多补仓1次
- 如果风控分数高 / 多项指标恶化，选 reduce 而非 dca
- 不确定时选 hold，绝不选 dca"""

        # 反馈闭环：复盘约束注入（DecisionRetrospective + 绩效归因）
        _feedback_constraints_text = ""
        try:
            from backend.services.decision_feedback_service import decision_feedback_service
            _strategy_ids = [
                s.get("strategy_id") for s in (strategies or [])
                if isinstance(s, dict) and s.get("strategy_id")
            ]
            _feedback_constraints_text = decision_feedback_service.get_prompt_injection(
                db=db,
                account_id=account_id,
                strategy_ids=_strategy_ids or None,
            )
        except Exception as _fb_err:
            logger.debug(f"[MasterController] 反馈闭环注入跳过: {_fb_err}")

        # V5 决策核心：费用感知 + 盈亏结构纪律注入（与 Direction/Risk 同一来源）
        _v5_context_text = ""
        try:
            from backend.services.decision_core import build_v5_prompt_block
            _v5_context_text = build_v5_prompt_block(db=db, account_id=account_id)
        except Exception as _v5_err:
            logger.debug(f"[MasterController] V5 上下文注入跳过: {_v5_err}")

        from backend.services.ai_prompt_layers import (
            EvidenceScoreConfig,
            LayeredPromptContext,
            build_layered_master_prompt,
        )
        _evidence_cfg = EvidenceScoreConfig(entry_threshold=_entry_gate)
        # short tier 亏损归因：额外惩罚已在 EvidenceScoreConfig.short_tier_penalty
        _layered_ctx = LayeredPromptContext(
            report_text=report_text,
            debate_text=debate_text,
            symbols_text=symbols_text,
            tier_context_text=_tier_context_text,
            hold_timeout_text=_hold_timeout_text,
            recent_lessons_text=_recent_lessons_text,
            feedback_constraints_text=_feedback_constraints_text,
            action_constraint=action_constraint,
            mode=mode,
            entry_gate=_entry_gate,
            scalp_gate=_scalp_gate,
            evidence_config=_evidence_cfg,
            v5_context_text=_v5_context_text,
        )
        prompt = build_layered_master_prompt(_layered_ctx)

        try:
            logger.info(
                f"[MasterController] calling LLM with prompt_len={len(prompt)}, "
                f"elapsed_before_llm={time.time() - _synth_t0:.2f}s"
            )
            result = self._call_llm(prompt)
            if result:
                logger.info(
                    f"[MasterController] synthesize done: "
                    f"elapsed={time.time() - _synth_t0:.2f}s"
                )
                return result
        except Exception as e:
            logger.warning(f"[MasterController] LLM 调用失败，使用规则回退: {e}")

        logger.warning(
            f"[MasterController] synthesize fallback: elapsed={time.time() - _synth_t0:.2f}s"
        )
        return self._rule_based_fallback(reports, symbols, mode,
                                                 market_envs=market_envs,
                                                 portfolio=portfolio)

    def _build_report_text(self, reports: Dict[str, AnalystReport], mode: str,
                           portfolio: Optional[Dict] = None,
                           market_envs: Optional[Dict] = None,
                           strategies: Optional[List[Dict]] = None,
                           symbols: Optional[List[str]] = None) -> str:
        parts = []
        for name, report in reports.items():
            if not report:
                continue
            r = report if isinstance(report, dict) else report.to_dict()
            header = f"### {r.get('analyst', name)} (风险评分: {r.get('risk_score', 50)}/100)"
            summary = f"**结论**: {r.get('summary', '')} | **建议**: {r.get('recommendation', '')}"
            details = []
            for sig in r.get("signals", [])[:8]:
                icon = {"danger": "🔴", "warning": "🟡", "neutral": "⚪", "bullish": "🟢"}.get(sig.get("signal", ""), "⚪")
                details.append(f"  {icon} {sig.get('detail', '')}")
            parts.append(f"{header}\n{summary}\n" + "\n".join(details))

        # 注入每个 symbol 的核心市场硬数据 + 数据质量标记
        if market_envs:
            env_lines = ["### 📊 实时市场数据（你的决策必须基于这些数据，不要编造数据）"]
            for sym, info in market_envs.items():
                if not isinstance(info, dict) or "error" in info:
                    continue
                price = info.get("current_price", 0)
                cycle = info.get("market_cycle", "?")
                cycle_conf = info.get("cycle_confidence", 0)
                trend = info.get("trend_direction", "?")
                trend_str = info.get("trend_strength", 0)
                vol = info.get("volatility_regime", "?")
                vol_val = info.get("volatility_value", 0)
                atr = info.get("atr_value", 0)
                sentiment = info.get("sentiment_index", 50)
                sent_zone = info.get("sentiment_zone", "?")
                whale = info.get("whale_direction", 0)
                funding = info.get("funding_rate", 0)
                deriv = info.get("derivatives_signal", "?")
                # 修复5：暴露衍生品数据质量——degraded时显示警告
                if info.get("derivatives_degraded"):
                    deriv = "⚠️衍生品数据不可用"
                liquidity = info.get("liquidity_score", 1.0)
                news = info.get("news_top_event", "")
                news_impact = info.get("news_impact", 0)

                # 数据质量标记
                data_reliable = info.get("data_reliable", True)
                price_stale = info.get("price_stale_warning", False)
                kline_count = info.get("kline_count", 0)
                quality_tag = ""
                if not data_reliable:
                    quality_tag = " ⚠️数据不足"
                if price_stale:
                    quality_tag += " ⚠️价格可能过期"

                # AI 精选币标记（来自 AutoCoinSelector，数据证实精选币单笔盈利
                # 效率显著高于默认币种，提示 LLM 差异化对待）
                _ac_meta = info.get("auto_coin_meta")
                _ac_tag = ""
                if isinstance(_ac_meta, dict):
                    _ac_tag = (
                        f" 🌟AI精选(评分{_ac_meta.get('score', 0):.2f}"
                        f"/置信{_ac_meta.get('ai_confidence', 0):.2f})"
                    )

                env_lines.append(
                    f"- **{sym}** ${price:,.2f}{quality_tag}{_ac_tag} | "
                    f"周期={cycle}(置信{cycle_conf*100:.0f}%) "
                    f"趋势={trend}(强度{trend_str:.2f}) "
                    f"波动={vol}({vol_val:.3f}) ATR={atr:.4f}"
                )
                if isinstance(_ac_meta, dict) and _ac_meta.get("ai_reason"):
                    env_lines.append(f"  🌟 选币理由: {str(_ac_meta['ai_reason'])[:120]}")
                env_lines.append(
                    f"  情绪={sentiment:.0f}({sent_zone}) "
                    f"鲸鱼={whale:+.1f} 资金费率={funding:.4f} "
                    f"衍生品={deriv} 流动性={liquidity:.1f}"
                )
                if news:
                    impact_tag = f"(影响力{abs(news_impact):.1f})" if news_impact else ""
                    env_lines.append(f"  📰 {news[:200]}{impact_tag}")

                # 情报引擎完整结构（与统一快照同源；避免重复计算且不一致）
                # MasterController 是真实 LLM 决策主线，不能在这里临时触发
                # 衍生品/鲸鱼/新闻等外部数据抓取；否则 LLM 调用前就可能被慢 API 拖死。
                _intel_txt = (info.get("intelligence_prompt") or "").strip()
                if _intel_txt:
                    env_lines.append(f"  ```\n{_intel_txt}\n  ```")
                if info.get("oi_change_1h") is not None:
                    env_lines.append(
                        f"  OI1h={info.get('oi_change_1h', 0):+.2f}% "
                        f"清算L/S=${info.get('liquidation_1h_long', 0):,.0f}/"
                        f"${info.get('liquidation_1h_short', 0):,.0f} "
                        f"多空比={info.get('long_short_ratio', 1):.2f}"
                    )

                orch = info.get("orchestrator")
                if isinstance(orch, dict):
                    orch_action = orch.get("action", "")
                    orch_dir = orch.get("direction", "")
                    orch_reason = orch.get("reasoning", "")
                    # 分层置信度：分别展示长/中/短线置信度，让 AI 按周期判断
                    _lc_raw = float(orch.get("long_conf", 0) or 0)
                    _mc_raw = float(orch.get("mid_conf", 0) or 0)
                    _sc_raw = float(orch.get("short_conf", 0) or 0)
                    _lc_pct = _lc_raw * 100 if _lc_raw <= 1.0 else _lc_raw
                    _mc_pct = _mc_raw * 100 if _mc_raw <= 1.0 else _mc_raw
                    _sc_pct = _sc_raw * 100 if _sc_raw <= 1.0 else _sc_raw
                    _rec_nature = orch.get("recommended_nature", "")
                    if orch_action:
                        env_lines.append(
                            f"  🎯 编排器建议: {orch_action} {orch_dir} "
                            f"推荐={_rec_nature} — {orch_reason[:200]}"
                        )
                        env_lines.append(
                            f"  📊 分层置信度: 长线={_lc_pct:.0f}% "
                            f"中线={_mc_pct:.0f}% 短线={_sc_pct:.0f}%"
                        )

                # ── 2026-07-21 P1：Top-10 因子明细注入 ──
                # 原来只注入 direction/strength/confidence/regime 4个标量，
                # AI 无法引用具体因子（如 RSI=72 但 funding 极端反向）。
                # 现在提取 top-10 因子明细，让 AI 看到每个因子的名称/方向/强度。
                _fv3 = info.get("factor_v3")
                _factor_details = None
                if isinstance(_fv3, dict):
                    _factor_details = _fv3.get("factor_details")
                if not _factor_details:
                    # 从 DB 缓存提取 factor_details
                    try:
                        from backend.database.models import ATASFactorCache
                        from datetime import datetime as _dt_fd
                        if self._db_session:
                            _fd_row = (
                                self._db_session.query(ATASFactorCache)
                                .filter(ATASFactorCache.cache_key == f"{sym}_15m_composite")
                                .first()
                            )
                            if _fd_row and isinstance(_fd_row.value, dict):
                                _factor_details = _fd_row.value.get("factor_details")
                    except Exception:
                        pass
                if isinstance(_factor_details, dict) and _factor_details:
                    # 按强度排序取 top-10
                    _sorted_factors = sorted(
                        _factor_details.items(),
                        key=lambda x: abs(float(x[1].get("direction", 0) or 0)),
                        reverse=True,
                    )[:10]
                    if _sorted_factors:
                        _factor_detail_lines = []
                        for _fname, _finfo in _sorted_factors:
                            _fdir = float(_finfo.get("direction", 0) or 0)
                            _fstr = float(_finfo.get("strength", 0) or 0)
                            _fcat = _finfo.get("category", "?")
                            _dir_label = "多" if _fdir > 0.1 else ("空" if _fdir < -0.1 else "中性")
                            _factor_detail_lines.append(
                                f"    {_fname}({_fcat}): {_dir_label} 方向={_fdir:+.2f} 强度={_fstr:.2f}"
                            )
                        if _factor_detail_lines:
                            env_lines.append(
                                f"  📐 Top-10 因子明细:"
                            )
                            env_lines.extend(_factor_detail_lines)
            _has_auto_coin = any(
                isinstance(i, dict) and isinstance(i.get("auto_coin_meta"), dict)
                for i in market_envs.values()
            )
            if _has_auto_coin:
                env_lines.append(
                    "\n### 🌟 AI 自动选币交易守则（仅适用于非核心小币；BTC/ETH/SOL/BNB/ASTER 按重点训练正常仓位）\n"
                    "- 自动选币小币**不得**比核心训练币加大仓位\n"
                    "- 仅当置信度≥72%且盈亏比≥2.2时才考虑开仓；中等信号一律观望\n"
                    "- 首仓必须从试探仓开始，连续盈利验证后才可小幅加仓\n"
                    "- 止损必须**严于**普通币（不宽于该周期默认止损），严禁宽止损扛单"
                )
            if len(env_lines) > 1:
                parts.append("\n".join(env_lines))

            # ── 策略库模板信号（混合路线：verified/champion 置顶）──
            _all_tpl_signals = []
            for sym, info in market_envs.items():
                if not isinstance(info, dict):
                    continue
                for ts in info.get("template_signals", []) or []:
                    if isinstance(ts, dict):
                        _all_tpl_signals.append((sym, ts))

            def _verified_rank(item):
                _sym, ts = item
                score = 0
                if ts.get("verified"):
                    score += 100
                tags = [str(t).lower() for t in (ts.get("tags") or [])]
                if "champion" in tags:
                    score += 50
                score += float(ts.get("signal_confidence") or 0)
                score += float(ts.get("match_confidence") or 0) * 10
                return score

            _all_tpl_signals.sort(key=_verified_rank, reverse=True)

            _verified_banner = []
            for sym, ts in _all_tpl_signals[:5]:
                if not ts.get("verified"):
                    continue
                _wr_hint = ts.get("live_win_rate") or ts.get("backtest_win_rate")
                _wr_txt = f" 历史WR≈{_wr_hint}%" if _wr_hint else ""
                _verified_banner.append(
                    f"- ⭐ **{ts.get('template_name', '?')}** [{sym}/{ts.get('tier', '?')}] "
                    f"→ {str(ts.get('direction', 'hold')).upper()} "
                    f"(信号{ts.get('signal_confidence', 0):.0f}%){_wr_txt}"
                )
            if _verified_banner:
                parts.insert(
                    0,
                    "### 🏆 已验证策略模板（软优先 — 同 regime 优先采纳，仍可 hold 但需说明理由）\n"
                    + "\n".join(_verified_banner[:3]),
                )

            _tpl_parts = []
            for sym, info in market_envs.items():
                if not isinstance(info, dict):
                    continue
                tpl_signals = list(info.get("template_signals", []) or [])
                if not tpl_signals:
                    continue
                tpl_signals.sort(
                    key=lambda ts: (
                        1 if ts.get("verified") else 0,
                        1 if "champion" in [str(t).lower() for t in (ts.get("tags") or [])] else 0,
                        float(ts.get("signal_confidence") or 0),
                    ),
                    reverse=True,
                )
                _tpl_parts.append(f"### 🧬 {sym} 策略库信号（你只需审核，不可凭空编造方向）")
                for ts in tpl_signals:
                    _dir_icon = {"buy": "🟢", "sell": "🔴", "hold": "⚪"}.get(ts.get("direction", "hold"), "⚪")
                    _verified_tag = " [✅已验证]" if ts.get("verified") else ""
                    _champion_tag = (
                        " [🏆Champion]"
                        if "champion" in [str(t).lower() for t in (ts.get("tags") or [])]
                        else ""
                    )
                    _tpl_parts.append(
                        f"- {_dir_icon} **{ts.get('template_name', '?')}** [{ts.get('tier', '?')}]"
                        f"{_verified_tag}{_champion_tag} "
                        f"→ {ts.get('direction', 'hold').upper()} "
                        f"(信号置信度 {ts.get('signal_confidence', 0):.0f}%, "
                        f"匹配={ts.get('match_confidence', 0):.2f})"
                    )
                    _tpl_parts.append(f"  理由: {ts.get('reason', '无')}")
                    _tpl_parts.append(
                        f"  类别={ts.get('category', '?')} "
                        f"来源模板={ts.get('template_id', '?')}"
                    )
            if _tpl_parts:
                _tpl_parts.insert(0, (
                    "### 🧬 策略库模板信号（混合路线 — 你必须审核这些信号）\n"
                    "**你的任务**:\n"
                    "1. 审核上述策略库信号是否适合当前市场环境\n"
                    "2. 如同意，在 reasoning 中说明「采纳模板 {template_id} 信号」并执行\n"
                    "3. 如有异议，说明具体原因（如：波动率过高不适合趋势策略）并建议调整为 hold\n"
                    "4. **关于「无匹配模板」的处理**（P5-fix 2026-05-08 重要更新 — 软约束）：\n"
                    "   - 默认情况：策略库无信号时优先 hold\n"
                    "   - **越权放行条件（同时满足，可越过策略库给出 buy/sell）**：\n"
                    "     a) 编排器明确推荐进场（recommended_slots 包含本 tier）\n"
                    "     b) 多空辩论中支持方论据数 ≥ 反对方 + 2（明显占优）\n"
                    "     c) 本 tier 对应置信度 ≥ 55%（高于普通 45% 门槛）\n"
                    "     d) K线分析师方向与编排器一致，且非 strong_反向\n"
                    "   - 越权时必须在 reasoning 里写：『越过策略库无信号铁律 — 因为 [a/b/c/d 具体证据]』\n"
                    "   - 触发情景示例：1h 判定 ranging 但 15m/5m 起明显动量 → 此时策略库只匹配区间模板会漏掉 scalp 机会\n"
                ))
                parts.append("\n".join(_tpl_parts))

            # Regime 感知指导（MARS 论文启发）
            REGIME_GUIDANCE = {
                "trending": "📈 **当前为趋势行情** — 适合顺势操作，仓位上限可到80%，信心门槛50%即可。止损可适度放宽让趋势运行。",
                "ranging": "📊 **当前为震荡行情** — 适度提高确信度(≥55%)，仓位控制在50%以内，止损从紧。预筛选通过的标的可正常开仓。",
                "volatile": "⚡ **当前为高波动行情** — 仓位不超过30%，信心门槛65%，止损极紧，尽量少开新仓。",
                "crisis": "🚨 **当前为危机行情** — 仓位不超过10%，信心门槛75%以上才考虑开仓，优先保护本金。",
            }
            regime_hints = []
            for sym, info in market_envs.items():
                if isinstance(info, dict):
                    sym_regime = info.get("market_cycle", "ranging") or "ranging"
                    guidance = REGIME_GUIDANCE.get(sym_regime)
                    if guidance:
                        regime_hints.append(f"- {sym}: {guidance}")
            if regime_hints:
                parts.append("### 🎯 Regime 操作指南\n" + "\n".join(regime_hints[:5]))

        # M5: 量化因子复合信号（DB 缓存 + market_summary.factor_v3 双通道）
        _factor_block = self._build_factor_signals_prompt_block(market_envs)
        if _factor_block:
            parts.append(_factor_block)

        # 注入账户概况（让 LLM 了解资金与持仓全貌）
        if portfolio:
            bal = portfolio.get("balance") or {}
            positions = portfolio.get("positions") or []
            stats = portfolio.get("session_stats") or {}
            total_equity = bal.get("total_equity", 0)
            available = bal.get("available_balance", 0) or bal.get("available", 0)
            total_margin = sum(float(p.get("margin", 0)) for p in positions)
            margin_pct = (total_margin / total_equity * 100) if total_equity > 0 else 0
            current_dd = stats.get("current_drawdown", 0)
            win_rate = stats.get("win_rate", 0)
            total_pnl = stats.get("total_pnl", 0)

            acct_lines = [
                f"### 📋 账户概况",
                f"- 总权益: ${total_equity:,.0f} | 可用: ${available:,.0f} | "
                f"已用保证金: ${total_margin:,.0f} ({margin_pct:.1f}%)",
                f"- 当前回撤: {current_dd*100:.1f}% | 胜率: {win_rate*100:.1f}% | "
                f"累计PnL: ${total_pnl:+,.0f}",
            ]
            NATURE_LABELS = {
                "scalp": "快速投机", "intraday": "日内交易",
                "swing": "波段操作", "position": "中期持仓",
                "trend_follow": "趋势跟踪",
            }
            if positions:
                # 构建 session symbols 集合，用于识别孤立持仓
                _session_sym_set = {s.upper() for s in (symbols or [])}
                acct_lines.append("- 当前持仓（你需要主动管理每个持仓的 TP/SL）:")
                for p in positions[:10]:
                    sym = p.get("symbol", "?")
                    side = p.get("side", "?")
                    margin = float(p.get("margin", 0))
                    upnl = float(p.get("unrealized_pnl", 0))
                    leverage = p.get("leverage", "?")
                    entry = float(p.get("entry_price", 0))
                    mark = float(p.get("mark_price", 0))
                    trade_nature = p.get("trade_nature") or p.get("timeframe_tier") or "swing"
                    nature_label = NATURE_LABELS.get(trade_nature, trade_nature)

                    pnl_pct = (upnl / margin * 100) if margin > 0 else 0.0

                    tp = p.get("tp_price")
                    sl = p.get("sl_price")
                    tp_sl_str = ""
                    if tp and float(tp) > 0:
                        tp_dist = abs(float(tp) - mark) / mark * 100 if mark > 0 else 0
                        tp_sl_str += f" TP=${float(tp):.4f}(距{tp_dist:.1f}%)"
                    if sl and float(sl) > 0:
                        sl_dist = abs(float(sl) - mark) / mark * 100 if mark > 0 else 0
                        tp_sl_str += f" SL=${float(sl):.4f}(距{sl_dist:.1f}%)"

                    age_str = ""
                    opened_at_str = p.get("opened_at") or ""
                    if opened_at_str:
                        try:
                            from datetime import datetime as _dt, timezone as _tz
                            ot = _dt.fromisoformat(str(opened_at_str).replace("Z", "+00:00"))
                            if ot.tzinfo is None:
                                ot = ot.replace(tzinfo=_tz.utc)
                            age_min = (_dt.now(_tz.utc) - ot).total_seconds() / 60.0
                            if age_min < 60:
                                age_str = f" | 持仓{age_min:.0f}分钟"
                            else:
                                age_str = f" | 持仓{age_min/60:.1f}小时"
                        except Exception:
                            pass

                    add_cnt = p.get("add_count", 0) or 0
                    dca_cnt = p.get("dca_count", 0) or 0
                    orig_margin = p.get("original_margin", 0) or 0
                    add_info = ""
                    if add_cnt > 0:
                        add_info += f" | 已加仓{add_cnt}次"
                    if dca_cnt > 0:
                        add_info += f" | 已补仓{dca_cnt}次"
                    if orig_margin > 0 and abs(margin - orig_margin) > 1:
                        add_info += f" | 原始保证金${orig_margin:.0f}"

                    _reduce_cnt = p.get("reduce_count", 0) or 0
                    _reduce_info = f" | 已减仓{_reduce_cnt}次" if _reduce_cnt > 0 else ""

                    # 孤立持仓标注：不在 session.symbols 中的持仓
                    _orphan_tag = ""
                    if sym.upper() not in _session_sym_set:
                        _orphan_tag = " [⚠ 已移出交易对，建议评估平仓]"

                    acct_lines.append(
                        f"  - {sym} {side} entry=${entry:.6g} mark=${mark:.6g} "
                        f"保证金${margin:.0f} {leverage}x | "
                        f"浮盈亏${upnl:+.0f}({pnl_pct:+.1f}%){age_str} | "
                        f"【{nature_label}】{tp_sl_str}{add_info}{_reduce_info}{_orphan_tag}")
                acct_lines.append(
                    "\n=== 子仓管理规则 ===\n"
                    "- trend_follow(趋势跟踪): 除非多周期全反转否则不动, 减仓需利润>5%且间隔>24h\n"
                    "- swing(波段): 短线回调可减仓, 但利润需>2%且间隔>6h\n"
                    "- intraday(日内): 灵活操作, 但预期利润必须覆盖3倍手续费\n"
                    "- 同标的最多3个子仓, 必须同方向\n"
                    "- 你的action需通过trade_nature指明针对哪个子仓\n"
                    "- 预期利润必须覆盖3倍手续费,否则不开仓"
                )
            else:
                acct_lines.append("- 当前无持仓")
            parts.append("\n".join(acct_lines))

        # 策略历史表现与记忆（来自 StrategyMemory）
        if strategies:
            mem_lines = ["### 📝 策略历史表现（来自交易记忆，辅助你做出更好决策）"]
            TIER_LABELS = {"short": "短线", "mid": "中线", "long": "长线"}
            has_data = False
            for s in strategies:
                t_trades = s.get("total_trades", 0)
                if t_trades == 0:
                    continue
                has_data = True
                s_name = s.get("name", "")[:30]
                s_tier = TIER_LABELS.get(s.get("tier", "mid"), "中线")
                s_wr = s.get("win_rate", 0)
                s_ap = s.get("avg_profit", 0)
                s_al = s.get("avg_loss", 0)
                s_pnl = s.get("total_pnl", 0)
                s_dd = s.get("max_drawdown", 0)
                s_sharpe = s.get("sharpe_ratio", 0)
                plr = abs(s_ap / s_al) if s_al != 0 else 0

                line = (
                    f"- {s_name}[{s_tier}]: {t_trades}笔 "
                    f"胜率{s_wr:.1f}% 盈亏比{plr:.2f} "
                    f"PnL${s_pnl:+.2f} 最大回撤{s_dd*100:.1f}% "
                    f"Sharpe={s_sharpe:.2f}"
                )
                mem_lines.append(line)

                # 市况维度表现
                perf = s.get("performance_by_regime", {})
                if perf and isinstance(perf, dict):
                    regime_bits = []
                    for rk, rd in perf.items():
                        if isinstance(rd, dict) and rd.get("trades", 0) > 0:
                            r_trades = rd["trades"]
                            r_wins = rd.get("wins", 0)
                            r_wr = r_wins / r_trades * 100 if r_trades > 0 else 0
                            regime_bits.append(f"{rk}行情胜率{r_wr:.0f}%({r_trades}笔)")
                    if regime_bits:
                        mem_lines.append(f"  市况分解: {' | '.join(regime_bits[:5])}")

                # 关键教训
                lessons = s.get("key_lessons", [])
                if lessons and isinstance(lessons, list):
                    for lesson in lessons[:3]:
                        if isinstance(lesson, str) and lesson.strip():
                            mem_lines.append(f"  💡 教训: {lesson[:120]}")
                        elif isinstance(lesson, dict):
                            text = lesson.get("message") or lesson.get("lesson") or str(lesson)
                            mem_lines.append(f"  💡 教训: {str(text)[:120]}")

                # 成功/失败模式 Top3
                for pat in (s.get("successful_patterns") or [])[:3]:
                    _pt = pat if isinstance(pat, str) else (
                        pat.get("pattern") or pat.get("description") or str(pat)
                    )
                    if str(_pt).strip():
                        mem_lines.append(f"  ✅ 成功模式: {str(_pt)[:120]}")
                for pat in (s.get("failed_patterns") or [])[:3]:
                    _pt = pat if isinstance(pat, str) else (
                        pat.get("pattern") or pat.get("description") or str(pat)
                    )
                    if str(_pt).strip():
                        mem_lines.append(f"  ❌ 失败模式: {str(_pt)[:120]}")

            if has_data:
                mem_lines.append("")
                mem_lines.append("⚠️ 请参考以上历史数据，在同类市况中优先使用表现好的策略方向，避免重蹈覆辙。")
                parts.append("\n".join(mem_lines))

        # M4: 上一轮被 V5 闸门拦截的决策回灌（让 LLM 知道为什么没执行）
        try:
            from backend.services.decision_core.unified_gate import (
                build_block_feedback_section,
            )

            _blocks_sec = build_block_feedback_section(max_age_seconds=900)
            if _blocks_sec:
                parts.append(_blocks_sec)
        except Exception as _bf_err:
            logger.debug(f"[MasterController] 闸门回灌注入跳过: {_bf_err}")

        # M3: 交易员心理状态（连亏/频率摩擦/仓位调节 — 此前只在 legacy 路径注入，
        # V5 重构后主路径丢失了这段「亏钱的痛」上下文，现在接回）
        if self._db_session and getattr(self, "_account_id", None):
            try:
                from backend.services.position_memory_manager import position_manager

                _mental_ctx = position_manager.get_ai_context(
                    self._db_session, self._account_id
                )
                if _mental_ctx:
                    parts.append(_mental_ctx)
            except Exception as _ms_err:
                logger.debug(f"[MasterController] 心理状态注入跳过: {_ms_err}")

        # M1: 逐笔战绩 + 连亏状态（让 LLM 看到自己最近每笔的真实结果）
        # M2: Reflexion 亏损教训（分层记忆检索）
        if self._db_session:
            try:
                from backend.services.trade_memory_context import (
                    build_recent_trades_section,
                    build_loss_lessons_section,
                )

                _recent_sec = build_recent_trades_section(self._db_session, limit=15)
                if _recent_sec:
                    parts.append(_recent_sec)

                _regime_hint_mm = ""
                if market_envs:
                    for _env in market_envs.values():
                        if isinstance(_env, dict) and _env.get("market_cycle"):
                            _regime_hint_mm = _env["market_cycle"]
                            break
                _lessons_sec = build_loss_lessons_section(
                    self._db_session,
                    symbols=list(market_envs.keys()) if market_envs else None,
                    regime=_regime_hint_mm,
                )
                if _lessons_sec:
                    parts.append(_lessons_sec)
            except Exception as _mm_err:
                logger.warning(f"[MasterController] 交易记忆注入失败(跳过): {_mm_err}")

        # 历史类比（来自 decision_snapshots 的交易经验）
        if self._db_session:
            try:
                from backend.services.experience_retriever import experience_retriever
                symbols = list({s.get("primary_symbol", "") for s in (strategies or []) if s.get("primary_symbol")})
                regime_hint = None
                if market_envs:
                    for env_data in market_envs.values():
                        if isinstance(env_data, dict) and env_data.get("market_cycle"):
                            regime_hint = env_data["market_cycle"]
                            break
                analogy_text = experience_retriever.format_for_prompt(
                    self._db_session, symbols, regime=regime_hint,
                )
                if analogy_text:
                    is_rag = "RAG 语义检索" in analogy_text
                    logger.info(f"[MasterController] 历史类比注入成功: {len(analogy_text)} 字符, RAG={is_rag}, symbols={symbols[:5]}, regime={regime_hint}")
                    parts.append(analogy_text)
                else:
                    logger.info(f"[MasterController] 历史类比返回空, symbols={symbols[:5]}, regime={regime_hint}")
            except Exception as _exp_err:
                logger.warning(f"[MasterController] 历史类比注入失败: {_exp_err}")

        # ── 合约趋势仓主攻策略（统一分析 / 分 tier 分析均注入）──
        parts.append(
            "### 📈 合约盈利主攻：趋势仓（trend_follow / long tier）\n"
            "- 合约应以**顺势趋势仓**打收益，短线(scalp/intraday)只做辅助。\n"
            "- 新开 trend_follow/position 时，reasoning 必须包含**局势判断**："
            "战略面+多周期共振+趋势强度，三者不清楚则 hold。\n"
            "- 编排器 long_view 与 mid_view 矛盾、或战略 macro_bias 与方向冲突 → 禁止开趋势仓。\n"
            "- **K线分析师（LLM 深度）** 的结论为趋势仓硬性参考：无多周期共振/支撑阻力解读 → 禁止开 trend_follow。"
        )

        # ── 战略分析师上下文注入（宏观 x 新币 x 经验记忆）──
        try:
            from backend.services.strategic_analyst.engine import get_strategic_engine
            _strat_engine = get_strategic_engine()
            _strat_section = _strat_engine.get_strategic_prompt_section()
            if _strat_section:
                parts.append(_strat_section)
                logger.debug("[MasterController] 战略分析师上下文注入成功")
        except Exception as _strat_err:
            logger.debug(f"[MasterController] 战略分析师注入跳过: {_strat_err}")

        # ── 混合模式：注入预筛选结果（技术指标通过的标的）──
        try:
            from backend.services.full_auto_trading_service import FullAutoTradingService
            # 从实例变量获取预筛选结果
            _svc = None
            # 尝试从全局单例获取
            try:
                from backend.services.full_auto_trading_service import full_auto_service
                _svc = full_auto_service if not isinstance(full_auto_service, type) else None
            except Exception:
                pass
            if _svc is None:
                try:
                    from backend.services.full_auto_trading_service import get_full_auto_service
                    _svc = get_full_auto_service()
                except Exception:
                    pass
            if _svc is not None:
                _ps_results = getattr(_svc, '_pre_screen_results', None)
                if _ps_results and hasattr(_ps_results, 'passed_symbols') and _ps_results.passed_symbols:
                    from backend.services.signal_pre_screener import get_signal_pre_screener
                    _screener = get_signal_pre_screener()
                    _ps_section = _screener.format_prescreen_prompt_section(_ps_results, "short")
                    if _ps_section:
                        parts.append(_ps_section)
                        logger.debug("[MasterController] 预筛选结果注入成功")
        except Exception as _ps_err:
            logger.debug(f"[MasterController] 预筛选注入跳过: {_ps_err}")

        # Phase 3/5: 实时教训注入（根据 AI_EVOLUTION_LEVEL >= 4）
        try:
            _level = int(os.getenv("AI_EVOLUTION_LEVEL", "0"))
            if _level >= 4:
                # Task 5: Reflexion 轻量教训
                _recent_sec = MasterController.build_recent_lessons_section()
                if _recent_sec:
                    parts.append(_recent_sec)
                    logger.debug("[MasterController] 实时教训已注入到prompt")
                # Task 7.1: OpenCode 深度复盘结构化教训（root_cause/mistake_category）
                _opencode_sec = self._build_recent_opencode_lessons_section()
                if _opencode_sec:
                    parts.append(_opencode_sec)
                    logger.debug("[MasterController] OpenCode深度教训已注入到prompt")
        except Exception as _rl_err:
            logger.debug(f"[MasterController] 实时教训注入跳过: {_rl_err}")

        return "\n\n".join(parts)

    # ══════════════════════════════════════════════════════
    #  Phase 2: 长上下文扩展方法（_build_extended_context + 3个Layer）
    # ══════════════════════════════════════════════════════

    def _build_extended_context(
        self,
        symbols: Optional[List[str]] = None,
        market_envs: Optional[Dict] = None,
        strategies: Optional[List[Dict]] = None,
        max_tokens: int = 8000,
    ) -> str:
        """
        逐层追加扩展上下文，超token预算时自动停止。
        优先级: K线数据 > 交易经验 > 关键价位
        """
        budget = max(max_tokens, 3000)
        parts = []

        # Layer 1: 长序列K线摘要（最高优先级）
        kline_section = self._build_kline_extended(symbols, market_envs, budget // 3 * 4)
        if kline_section:
            parts.append(kline_section)
            budget -= len(kline_section) // 4  # 粗略token估算

        # Layer 2: 最近交易经验（次高优先级）
        if budget > 1000:
            trade_section = self._build_trade_experience(symbols, strategies, budget * 4)
            if trade_section:
                parts.append(trade_section)
                budget -= len(trade_section) // 4

        # Layer 3: 关键价位历史（如果有余量）
        if budget > 800:
            level_section = self._build_key_levels(symbols, market_envs, budget * 4)
            if level_section:
                parts.append(level_section)

        return "\n---\n".join(p for p in parts if p)

    def _build_kline_extended(
        self,
        symbols: Optional[List[str]] = None,
        market_envs: Optional[Dict] = None,
        max_chars: int = 12000,
    ) -> str:
        """
        Layer 1: 从 unified_data_pool 读取已缓存的1h/4h K线，
        压缩格式注入到prompt中。每根K线一行，自动标记放量/缩量。
        """
        if not symbols or not self._db_session:
            return ""
        try:
            from backend.services.unified_data_pool import get_data_pool
            pool = get_data_pool()
        except Exception:
            return ""

        lines = ["### 📈 扩展K线数据（长序列量价分析）"]
        remaining = max_chars - len(lines[0])

        for sym in (symbols or [])[:3]:  # 最多3个品种
            if remaining < 500:
                break
            try:
                # 取最近72根1h K线
                klines = pool.get_klines(sym, "1h", 72) if hasattr(pool, 'get_klines') else None
                if not klines:
                    continue

                # 计算关键统计
                prices = [k.get("close", 0) for k in klines if isinstance(k, dict)]
                volumes = [k.get("volume", 0) for k in klines if isinstance(k, dict)]
                if not prices:
                    continue

                # 趋势斜率: 首尾价格变化率
                trend_slope = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
                # 20周期均量
                avg_vol = sum(volumes[-20:]) / max(len(volumes[-20:]), 1) if volumes else 0

                header = (
                    f"\n**{sym} 1hK线 (最近{len(prices)}根):** "
                    f"趋势斜率={trend_slope:+.1f}% | "
                    f"现价=${prices[-1]:,.2f}"
                )
                lines.append(header)
                remaining -= len(header)

                # 压缩每根K线
                for i, k in enumerate(klines):
                    if remaining < 60:
                        break
                    if not isinstance(k, dict):
                        continue
                    o, h, l, c = k.get("open", 0), k.get("high", 0), k.get("low", 0), k.get("close", 0)
                    v = k.get("volume", 0)
                    vol_ratio = v / avg_vol if avg_vol > 0 else 1.0
                    vol_tag = f"[{vol_ratio:.1f}x]" if vol_ratio > 1.3 else ("[0.{:.0f}x]".format(vol_ratio * 10) if vol_ratio < 0.7 else "")
                    ts = str(k.get("timestamp", ""))[:16]
                    line = f"  {ts} O:{o:.0f} H:{h:.0f} L:{l:.0f} C:{c:.0f} V:{v:.1f}{vol_tag}\n"
                    lines.append(line)
                    remaining -= len(line)

            except Exception as _kline_err:
                logger.debug(f"[MasterController] K线扩展 {sym} 失败: {_kline_err}")
                continue

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def _build_trade_experience(
        self,
        symbols: Optional[List[str]] = None,
        strategies: Optional[List[Dict]] = None,
        max_chars: int = 8000,
    ) -> str:
        """
        Layer 2: 最近交易经验叙事 — 编译为成功/失败案例格式
        """
        if not self._db_session:
            return ""
        try:
            from backend.database.models import StrategyTrade
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz

            sym_filter = {s.upper() for s in (symbols or [])}
            # [fix] 时间窗口：只取最近 48h 平仓，避免几天前的旧连亏束缚当前决策
            _cutoff = _dt.now(_tz.utc) - _td(hours=48)
            recent = (
                self._db_session.query(StrategyTrade)
                .filter(
                    StrategyTrade.status == "closed",
                    ~StrategyTrade.strategy_id.like("rebate_%"),
                    StrategyTrade.closed_at >= _cutoff,
                )
                .order_by(StrategyTrade.closed_at.desc())
                .limit(30)
                .all()
            )
            # 窗口内无记录 → 回退取最近30笔（不卡时间），保证有经验可参考
            if not recent:
                recent = (
                    self._db_session.query(StrategyTrade)
                    .filter(
                        StrategyTrade.status == "closed",
                        ~StrategyTrade.strategy_id.like("rebate_%"),
                    )
                    .order_by(StrategyTrade.closed_at.desc())
                    .limit(30)
                    .all()
                )
            if not recent:
                return ""

            wins = [t for t in recent if float(t.pnl or 0) > 0]
            losses = [t for t in recent if float(t.pnl or 0) < 0]

            lines = ["### 💼 最近交易经验（来自你过去的真实决策）"]
            remaining = max_chars

            if wins:
                lines.append("\n**✅ 成功案例（前3）:**")
                for t in wins[:3]:
                    pnl = float(t.pnl or 0)
                    reason = (t.ai_reasoning or "")[:60]
                    line = f"  - {t.symbol} {t.side}: PnL=${pnl:+.2f} | 入场理由: {reason}"
                    if remaining - len(line) > 0:
                        lines.append(line)
                        remaining -= len(line)

            if losses:
                lines.append("\n**❌ 失败案例（前3）:**")
                for t in losses[:3]:
                    pnl = float(t.pnl or 0)
                    reason = (t.ai_reasoning or "")[:60]
                    close_reason = ""
                    if isinstance(t.decision_context, dict):
                        close_reason = str(t.decision_context.get("close_reason", ""))[:30]
                    line = f"  - {t.symbol} {t.side}: PnL=${pnl:+.2f} | 平仓={close_reason}"
                    if remaining - len(line) > 0:
                        lines.append(line)
                        remaining -= len(line)

            # 连亏检测
            streak_loss = 0
            for t in recent:
                if float(t.pnl or 0) < 0:
                    streak_loss += 1
                else:
                    break
            if streak_loss >= 3:
                lines.append(f"\n⚠️ **警告: 最近连续亏损 {streak_loss} 笔！下笔开仓前必须找到与之前失败交易不同的关键证据。**")

            if len(lines) <= 1:
                return ""
            return "\n".join(lines)

        except Exception as _trade_err:
            logger.debug(f"[MasterController] 交易经验扩展失败: {_trade_err}")
            return ""

    def _build_key_levels(
        self,
        symbols: Optional[List[str]] = None,
        market_envs: Optional[Dict] = None,
        max_chars: int = 6000,
    ) -> str:
        """
        Layer 3: 关键价位 — 从K线数据自动提取支撑/阻力/成交量密集区
        """
        if not symbols or not self._db_session:
            return ""
        try:
            from backend.services.unified_data_pool import get_data_pool
            pool = get_data_pool()
        except Exception:
            return ""

        lines = ["### 🎯 关键价位参考"]
        remaining = max_chars

        for sym in (symbols or [])[:3]:
            if remaining < 300:
                break
            try:
                klines = pool.get_klines(sym, "1h", 720) if hasattr(pool, 'get_klines') else None
                if not klines or len(klines) < 10:
                    continue

                highs = [k.get("high", 0) for k in klines if isinstance(k, dict)]
                lows = [k.get("low", 0) for k in klines if isinstance(k, dict)]
                closes = [k.get("close", 0) for k in klines if isinstance(k, dict)]
                if not highs or not lows:
                    continue

                cur_price = closes[-1] if closes else 0
                high_30d = max(highs)
                low_30d = min(lows)

                # 简单阻力/支撑 = 30日最高/最低
                section = (
                    f"\n**{sym}** (现价=${cur_price:,.2f}): "
                    f"30日高=${high_30d:,.2f} | 30日低=${low_30d:,.2f}"
                )
                if remaining - len(section) > 0:
                    lines.append(section)
                    remaining -= len(section)

                # 成交量分布简版：价格区间计数
                if len(closes) >= 30:
                    vol_buckets = {}
                    for c in closes[-30:]:
                        bucket = int(c / 100) * 100  # 每100为一个桶
                        vol_buckets[bucket] = vol_buckets.get(bucket, 0) + 1
                    top_buckets = sorted(vol_buckets.items(), key=lambda x: x[1], reverse=True)[:3]
                    for bucket_val, count in top_buckets:
                        tag = "支撑" if bucket_val < cur_price else "阻力"
                        sub_line = f"  - ${bucket_val}-{bucket_val + 100}: {count}/30天（{tag}区）"
                        if remaining - len(sub_line) > 0:
                            lines.append(sub_line)
                            remaining -= len(sub_line)

            except Exception as _lv_err:
                logger.debug(f"[MasterController] 关键价位 {sym} 失败: {_lv_err}")
                continue

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════
    #  Phase 3: 因子对齐检查 + 实时教训缓存 + 注入方法
    # ══════════════════════════════════════════════════════

    def _build_factor_alignment_check(self, market_envs: Optional[Dict]) -> str:
        """构建因子-AI对齐检查指令，注入到tier_context中"""
        if not market_envs:
            return ""

        factor_lines = []
        for sym, info in market_envs.items():
            if not isinstance(info, dict):
                continue
            fv3 = info.get("factor_v3", {})
            if not isinstance(fv3, dict):
                continue
            f_dir = fv3.get("direction_label", "neutral")
            f_conf = float(fv3.get("confidence", 0) or 0)
            f_strength = float(fv3.get("signal_score", 0) or 0)
            if f_dir == "neutral" or f_conf < 0.3:
                continue
            factor_lines.append(
                f"- {sym}: 因子方向={f_dir} 强度={f_strength:.2f} 置信={f_conf:.2f}"
            )

        if not factor_lines:
            return ""

        return dedent(f"""
        ### 🔗 因子-AI对齐检查（你必须在reasoning中完成这一步）
        以下是21因子引擎对各个品种的量化判断。请逐一检查：
        {chr(10).join(factor_lines)}

        **对齐规则**：
        1. 如果你的方向判断与因子方向一致 → 在reasoning中写"因子-AI对齐(+)"并可以增加5-10%的confidence
        2. 如果你与因子方向矛盾 → 必须写清楚"为何否决因子信号"，原因只能是以下之一：
           - 量价结构不支持（给出具体的量/价证据）
           - 市场心理不支持（极端情绪反向指标）
           - 多周期共振不支持（编排器矛盾）
        3. 如果你与因子都neutral → 没关系，写"因子-AI均中性"

        注意：因子的置信度≥0.55时会触发执行层的硬否决——如果你打算开反向仓，系统会直接拦截。
        所以**建议在因子明确方向时不要强行反向**，除非你有压倒性的证据。
        """).strip()

    @classmethod
    def inject_recent_lesson(cls, symbol: str, lesson: str, mistake_category: str = ""):
        """每笔交易结束后由全自动服务调用，注入即时教训"""
        import threading
        if not hasattr(cls, '_recent_lessons_lock'):
            cls._recent_lessons_lock = threading.Lock()
        with cls._recent_lessons_lock:
            if symbol not in cls._recent_lessons_cache:
                cls._recent_lessons_cache[symbol] = []
            cls._recent_lessons_cache[symbol].append({
                "lesson": lesson,
                "mistake_category": mistake_category,
                "ts": time.time(),
            })
            # 保留最近10条
            cls._recent_lessons_cache[symbol] = cls._recent_lessons_cache[symbol][-10:]
            cls._recent_lessons_ts = time.time()
            logger.info(f"[MasterController] 实时教训已注入: {symbol} {lesson[:50]}...")

    @classmethod
    def build_recent_lessons_section(cls) -> str:
        """构建即时OpenCode/Reflexion教训段落（注入到prompt末尾）"""
        import threading
        if not hasattr(cls, '_recent_lessons_lock'):
            cls._recent_lessons_lock = threading.Lock()
        with cls._recent_lessons_lock:
            # 清理过期（30分钟）
            now = time.time()
            for sym in list(cls._recent_lessons_cache.keys()):
                cls._recent_lessons_cache[sym] = [
                    l for l in cls._recent_lessons_cache[sym]
                    if now - l.get("ts", 0) < 1800
                ]
                if not cls._recent_lessons_cache[sym]:
                    del cls._recent_lessons_cache[sym]

            # 收集所有最近教训
            all_lessons = []
            for sym, lessons in cls._recent_lessons_cache.items():
                for l in lessons:
                    all_lessons.append({"symbol": sym, **l})
            all_lessons.sort(key=lambda x: x.get("ts", 0), reverse=True)

            if not all_lessons:
                return ""

            lines = ["### 🧠 实时交易教训（最近30分钟内深度复盘结果）"]
            for l in all_lessons[:5]:
                cat = l.get("mistake_category", "")
                cat_tag = f" [{cat}]" if cat else ""
                lines.append(f"  ⚡ {l['symbol']}{cat_tag}: {l['lesson']}")
            lines.append("📌 以上是最近交易的深度复盘结果——你的下一步决策必须考虑这些教训。")
            return "\n".join(lines)

    # ══════════════════════════════════════════════════════
    #  Phase 5: OpenCode 结构化教训缓存（Per-Trade Deep Review 回流）
    # ══════════════════════════════════════════════════════

    _recent_opencode_lessons: List[Dict] = []
    _recent_opencode_lessons_ts: float = 0.0
    _recent_opencode_lessons_lock = None  # 延迟初始化

    @classmethod
    def inject_opencode_lesson(cls, lesson: Dict):
        """由 OpenCode 深度复盘完成后调用，注入即时结构化教训"""
        import threading
        if cls._recent_opencode_lessons_lock is None:
            cls._recent_opencode_lessons_lock = threading.Lock()
        with cls._recent_opencode_lessons_lock:
            cls._recent_opencode_lessons.append({
                **lesson,
                "ts": time.time(),
            })
            # 保留最近20条，30分钟过期
            cls._recent_opencode_lessons = [
                l for l in cls._recent_opencode_lessons[-20:]
                if time.time() - l.get("ts", 0) < 1800
            ]
            cls._recent_opencode_lessons_ts = time.time()
            logger.info(
                f"[MasterController] OpenCode教训已注入: "
                f"{lesson.get('symbol', '?')} {lesson.get('lesson', '')[:50]}..."
            )

    def _build_recent_opencode_lessons_section(self) -> str:
        """构建即时OpenCode教训段落（注入到prompt末尾）"""
        import threading
        if self._recent_opencode_lessons_lock is None:
            self._recent_opencode_lessons_lock = threading.Lock()
        with self._recent_opencode_lessons_lock:
            # 清理过期（30分钟）
            self._recent_opencode_lessons = [
                l for l in self._recent_opencode_lessons
                if time.time() - l.get("ts", 0) < 1800
            ]
            if not self._recent_opencode_lessons:
                return ""

            lines = ["### 🧠 OpenCode 即时复盘教训（最近30分钟内）"]
            for l in self._recent_opencode_lessons[-5:]:  # 最多5条
                root = l.get("root_cause", "?")
                lesson = l.get("lesson", "")
                cat = l.get("mistake_category", "")
                sym = l.get("symbol", "?")
                cat_tag = f" (归类: {cat})" if cat else ""
                lines.append(f"  ⚡ [{root}] {sym}: {lesson}{cat_tag}")
            lines.append("📌 以上是最近交易的深度复盘结果——你的下一步决策必须考虑这些教训。")
            return "\n".join(lines)

    @staticmethod
    def _normalize_decision_sizing(decision: Dict[str, Any]) -> Dict[str, Any]:
        """规范化 AI 输出的杠杆与仓位比例，缺失时按置信度补全。"""
        action = str(decision.get("action", "hold")).lower()
        if action not in ("buy", "sell", "pyramid", "dca"):
            return decision

        conf = int(decision.get("confidence", 50) or 50)
        conf = max(0, min(100, conf))

        lev = decision.get("leverage")
        try:
            lev = int(lev) if lev is not None else None
        except (TypeError, ValueError):
            lev = None
        if lev is None or lev <= 0:
            lev = max(5, min(20, 5 + conf // 7))
        decision["leverage"] = max(5, min(20, lev))

        pct = decision.get("position_pct")
        if pct is None:
            pct = decision.get("target_portion_of_balance")
        try:
            pct = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        if pct is None or pct <= 0:
            pct = max(0.05, min(0.25, 0.04 + conf / 1000.0))
        elif pct > 1.0:
            pct = pct / 100.0
        decision["position_pct"] = round(max(0.04, min(0.35, pct)), 4)
        return decision

    def _enrich_master_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """为 MasterController 输出补全/规范化杠杆与仓位字段。"""
        if not isinstance(result, dict):
            return result
        decisions = result.get("decisions")
        if not isinstance(decisions, list):
            return result
        result["decisions"] = [
            self._normalize_decision_sizing(d) if isinstance(d, dict) else d
            for d in decisions
        ]
        return result

    _MAX_LLM_RETRIES = int(os.getenv("MASTER_LLM_MAX_RETRIES", "0"))

    # 追踪 LLM 调用健康状态（类级别）
    _llm_call_stats = {
        "total_calls": 0,
        "total_success": 0,
        "total_failures": 0,
        "last_error": "",
        "last_error_time": "",
    }

    def _call_llm(self, prompt: str) -> Optional[Dict]:
        """同步调用 LLM，带结构化输出 + Pydantic 校验 + 自动重试 + 详细错误追踪"""
        import json as _json
        import re
        import time as _time

        from backend.services.llm_config_service import (
            build_stream_progress_observer,
            get_llm_config_for_analysis,
            call_llm_api_sync,
            should_use_llm_streaming,
        )

        self._llm_call_stats["total_calls"] += 1

        llm_config = get_llm_config_for_analysis(getattr(self, "_account_id", None))
        if not llm_config:
            err = "无可用LLM配置(API Key/Base URL 未设置)"
            logger.warning(f"[MasterController] {err}")
            self._llm_call_stats["total_failures"] += 1
            self._llm_call_stats["last_error"] = err
            self._llm_call_stats["last_error_time"] = (
                datetime.now(timezone.utc).isoformat())
            return None

        _stream = should_use_llm_streaming(llm_config)
        logger.info(
            f"[MasterController] 调用LLM(深度分析): {llm_config.model} @ {llm_config.base_url} "
            f"mode={'流式[DONE]' if _stream else '非流式固定超时'}"
        )

        messages = [
            {"role": "system", "content": dedent("""
                你是一名管理$5亿加密对冲基金的CIO，运行多周期并行(short/mid/long)交易架构。
                你的决策质量直接决定投资人的回报，因此你必须进行严谨、有深度的推理。

                ## 分析框架（六步法，每一步都要在reasoning中体现）

                ### 第一步：宏观叙事构建
                - 当前市场的主导叙事是什么？（降息预期/监管恐慌/技术突破/资金轮动/避险情绪）
                - 编排器 long_view 的方向和置信度是否与叙事一致？
                - 如果长线置信度<40%，说明大方向不明确——此时你应比平时更谨慎

                ### 第二步：量价结构解读
                - 查看K线数据中的成交量和价格关系：
                  - 上涨放量+回调缩量=健康的吸筹结构（看涨）
                  - 下跌放量+反弹缩量=派发结构（看跌）
                  - 价格创新高但量能递减=背离（警告）
                - 关键位置（整数关口、前高/前低、MA交叉点）的成交量行为：
                  - 突破关键位时放量=有效突破，缩量突破=假突破概率高

                ### 第三步：市场心理评估
                - 恐惧贪婪指数在哪个区域？极端恐惧(≤25)往往是买入窗口，极端贪婪(≥75)要提高警惕
                - 资金费率极端正值→市场过度做多（回调风险）；极端负值→过度做空（反弹风险）
                - 新闻情绪和鲸鱼动向是否暗示市场正在从众？
                - **从众是交易者的敌人：当所有信号都指向同一方向时，问自己"谁在对面接盘？"**

                ### 第四步：多周期共振验证
                - 编排器分层置信度（长/中/短线）是否一致？
                  - 三线一致+高置信度=最佳交易机会
                  - 两线一致+一线中性=可以操作但降低仓位
                  - 长中矛盾=趋势不确定，仅允许轻仓短线
                - 本tier的K线形态是否与编排器方向一致？

                ### 第五步：反向假设检验（必须回答）
                - **如果我的判断错了，最可能的三个原因是什么？**
                - 当前的止损位是否考虑了反向假设中的最坏情况？
                - 如果市场向反方向运动2%，我的持仓会怎样？

                ### 第六步：决策合成
                - 综合以上五步，给出最终的action/confidence/reasoning
                - confidence不是"我多想开仓"，而是"我对方向判断的确信程度"
                - hold也是一种专业决策——当证据不充分时，不交易本身就是最好的交易

                ## 交易哲学（内化到你的决策中）
                1. 趋势是你的朋友，但极端情绪是你的反向指标
                2. 量在价先——异常成交量是重大行情的唯一可靠先兆
                3. 市场从不确定→确定→新的不确定，你必须在不确定时谨慎，在确定时果断
                4. 亏损的交易不一定是错的，盈利的交易不一定是对的——关注决策过程而非结果
                5. 合约的复利敌人是手续费和滑点，每笔约0.09%——没有足够盈亏比就hold

                ## 硬性约束
                - 只返回JSON，不要额外文字
                - 对指定的tier做决策，不要越界
                - 开仓必须给出stop_loss_pct/take_profit_pct（TP:SL>=1.8）
                - 合约主攻trend_follow趋势仓，短线只做辅助
                """).strip()
            },
            {"role": "user", "content": prompt},
        ]

        last_error_detail = ""
        for attempt in range(1 + self._MAX_LLM_RETRIES):
            call_start = _time.time()
            try:
                _max_tokens = int(os.getenv("MASTER_LLM_MAX_TOKENS", "8192"))
                ai_response = call_llm_api_sync(
                    llm_config, messages, temperature=0.3, max_tokens=_max_tokens,
                    response_format={"type": "json_object"},
                    account_id=getattr(self, "_account_id", None),
                    caller="MasterController:synthesize",
                    progress_observer=build_stream_progress_observer(
                        "MasterController:synthesize",
                    ),
                )
            except Exception as call_exc:
                elapsed = _time.time() - call_start
                is_timeout = elapsed > 120 or "timeout" in str(call_exc).lower()
                last_error_detail = (
                    f"{'超时' if is_timeout else '异常'}({elapsed:.1f}s): "
                    f"{str(call_exc)[:120]}")
                logger.error(
                    f"[MasterController] LLM 调用异常 (attempt {attempt+1}): "
                    f"{last_error_detail}")
                if attempt < self._MAX_LLM_RETRIES:
                    _time.sleep(min(2 ** attempt, 4))
                continue

            elapsed = _time.time() - call_start

            if not ai_response:
                last_error_detail = f"LLM 返回空响应({elapsed:.1f}s)"
                logger.warning(
                    f"[MasterController] {last_error_detail} (attempt {attempt+1})")
                if attempt < self._MAX_LLM_RETRIES:
                    _time.sleep(min(2 ** attempt, 4))
                continue

            choice = (ai_response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                ).strip()
            if not content and message.get("reasoning_content"):
                # 部分推理模型或代理会把正文错误放入 reasoning_content。
                # 这里先尝试解析，避免 HTTP 200 被误判为空响应。
                content = str(message.get("reasoning_content") or "").strip()
            if not content:
                last_error_detail = f"LLM 返回空内容({elapsed:.1f}s)"
                usage_info = ai_response.get("usage", {}) if isinstance(ai_response, dict) else {}
                logger.warning(
                    f"[MasterController] {last_error_detail} (attempt {attempt+1}); "
                    f"finish_reason={choice.get('finish_reason')}; "
                    f"message_keys={list(message.keys())}; usage={usage_info}")
                continue

            logger.info(f"[MasterController] LLM 响应耗时 {elapsed:.1f}s, "
                        f"内容长度 {len(content)} 字符")

            # 解析 JSON — 多策略提取（适配深度推理模型长文本输出）
            parsed = None
            # 策略1: 直接解析
            try:
                parsed = _json.loads(content)
            except _json.JSONDecodeError:
                pass
            # 策略2: 从 ```json 代码块提取
            if not parsed:
                fence_match = re.search(r'```json\s*\n?(.*?)\n?```', content, re.DOTALL)
                if fence_match:
                    try:
                        parsed = _json.loads(fence_match.group(1).strip())
                    except _json.JSONDecodeError:
                        pass
            # 策略3: 从最后一个完整JSON对象提取（推理模型JSON在末尾）
            if not parsed:
                all_braces = list(re.finditer(r'\{', content))
                if all_braces:
                    # 从最后一个 { 开始找匹配的 }
                    for m in reversed(all_braces):
                        depth = 0
                        end = -1
                        for i, ch in enumerate(content[m.start():], m.start()):
                            if ch == '{':
                                depth += 1
                            elif ch == '}':
                                depth -= 1
                                if depth == 0:
                                    end = i + 1
                                    break
                        if end > m.start():
                            try:
                                parsed = _json.loads(content[m.start():end])
                                break
                            except _json.JSONDecodeError:
                                continue
            # 策略4: 正则兜底
            if not parsed:
                json_match = re.search(r'\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]+\}', content, re.DOTALL)
                if json_match:
                    try:
                        parsed = _json.loads(json_match.group())
                    except _json.JSONDecodeError:
                        pass

            if not parsed:
                last_error_detail = f"JSON解析失败: {content[:120]}"
                logger.warning(
                    f"[MasterController] LLM JSON解析失败 (attempt {attempt+1}): "
                    f"{content[:200]}")
                if attempt < self._MAX_LLM_RETRIES:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content":
                        "你的回复不是合法JSON。请严格按要求只返回JSON对象，"
                        "包含 overall_assessment, risk_level, decisions 字段。"})
                continue

            # Pydantic 结构校验
            try:
                validated = MasterDecisionOutput.model_validate(parsed)
                self._llm_call_stats["total_success"] += 1
                logger.info(
                    f"[MasterController] LLM 调用成功 "
                    f"(attempt {attempt+1}, {elapsed:.1f}s)")
                # 修正 llm_usage_logs 的 success 标记（HTTP 200 但内容空不算成功）
                self._mark_llm_usage_success()
                return self._enrich_master_result(validated.model_dump())
            except Exception as val_err:
                last_error_detail = f"Pydantic校验失败: {str(val_err)[:150]}"
                logger.warning(
                    f"[MasterController] {last_error_detail} (attempt {attempt+1})")
                if attempt < self._MAX_LLM_RETRIES:
                    messages.append({"role": "assistant", "content": content})
                    # [fix] P1-2: 增强重试提示，列举具体缺失字段而非泛泛错误
                    err_str = str(val_err)[:300]
                    missing_hint = ""
                    if "Field required" in err_str or "missing" in err_str:
                        missing_hint = "（注意：所有字段名必须为英文 camelCase，例如 overall_assessment, risk_level, decisions）"
                    messages.append({"role": "user", "content":
                        f"JSON格式校验失败: {err_str}。{missing_hint}"
                        f"请修正后重新返回完整JSON。"
                        f"必须包含: overall_assessment(str), risk_level(low/medium/high/critical), "
                        f"decisions(数组，每项含symbol/action/confidence/reasoning)。"})
                else:
                    if "decisions" in parsed and isinstance(parsed["decisions"], list):
                        logger.info("[MasterController] Pydantic校验失败但结构可用，宽松接受")
                        self._llm_call_stats["total_success"] += 1
                        return self._enrich_master_result(parsed)

        self._llm_call_stats["total_failures"] += 1
        self._llm_call_stats["last_error"] = last_error_detail
        self._llm_call_stats["last_error_time"] = (
            datetime.now(timezone.utc).isoformat())
        logger.warning(
            f"[MasterController] LLM 全部{1 + self._MAX_LLM_RETRIES}次尝试失败，"
            f"最后错误: {last_error_detail}")
        return None

    def _mark_llm_usage_success(self):
        """修正最近一条 llm_usage_logs 记录：如果之前被标记为 success=true 但实际内容空，
        这里不再重复标记。此方法用于在 Pydantic 校验成功后触发额外的日志修正。"""
        try:
            from backend.database.connection import get_session_context
            with get_session_context() as db:
                db.execute(
                    "UPDATE llm_usage_logs SET error_message='ok_content_valid' "
                    "WHERE id = (SELECT MAX(id) FROM llm_usage_logs "
                    "WHERE call_type LIKE '%_call_llm%') "
                    "AND (error_message IS NULL OR error_message = '')"
                )
                db.commit()
        except Exception:
            pass  # 非关键路径，不影响主流程

    def _rule_based_fallback(self, reports: Dict[str, AnalystReport],
                              symbols: List[str], mode: str,
                              market_envs: Optional[Dict] = None,
                              portfolio: Optional[Dict] = None) -> Dict:
        """LLM 不可用时的规则回退 — 基于分析师信号综合评分 + 编排器置信度增强"""
        decisions = []
        pos_report = reports.get("position")
        risk_report = reports.get("risk")
        market_report = reports.get("market")
        intel_report = reports.get("intel")

        risk_score = 50
        if risk_report:
            r = risk_report if isinstance(risk_report, dict) else risk_report.to_dict()
            risk_score = r.get("risk_score", 50)

        # 收集每个 symbol 的信号统计
        sym_signals: Dict[str, Dict[str, int]] = {s: {"bullish": 0, "bearish": 0, "warning": 0, "neutral": 0} for s in symbols}
        for _name, _report in reports.items():
            if not _report:
                continue
            _r = _report if isinstance(_report, dict) else _report.to_dict()
            for sig in _r.get("signals", []):
                _sym = sig.get("symbol", "")
                _signal = sig.get("signal", "neutral")
                if _sym in sym_signals:
                    if _signal in ("bullish",):
                        sym_signals[_sym]["bullish"] += 1
                    elif _signal in ("bearish", "danger"):
                        sym_signals[_sym]["bearish"] += 1
                    elif _signal in ("warning",):
                        sym_signals[_sym]["warning"] += 1
                    else:
                        sym_signals[_sym]["neutral"] += 1

        pos_signals = {}
        if pos_report:
            r = pos_report if isinstance(pos_report, dict) else pos_report.to_dict()
            for sig in r.get("signals", []):
                pos_signals[sig.get("symbol", "")] = sig

        for sym in symbols:
            ps = pos_signals.get(sym)
            ss = sym_signals.get(sym, {"bullish": 0, "bearish": 0, "warning": 0, "neutral": 0})
            total_signals = ss["bullish"] + ss["bearish"] + ss["warning"] + ss["neutral"]

            # ── 增强：读取编排器置信度和波动率 ──
            _orch_conf = 0.0
            _orch_dir = ""
            _vol_regime = "normal"
            _trend = ""
            if market_envs and isinstance(market_envs, dict):
                _env = market_envs.get(sym, {})
                if isinstance(_env, dict):
                    _vol_regime = _env.get("volatility_regime", "normal")
                    _trend = _env.get("trend_direction", "")
                    _orch = _env.get("orchestrator", {})
                    if isinstance(_orch, dict):
                        _orch_dir = _orch.get("direction", "")
                        # 取最相关的置信度
                        for _ck in ("short_conf", "mid_conf", "long_conf"):
                            _cv = float(_orch.get(_ck, 0) or 0)
                            _cv = _cv * 100 if _cv <= 1.0 else _cv
                            _orch_conf = max(_orch_conf, _cv)

            # 检查是否已有持仓（规则回退也需管理）
            _has_pos = False
            _pos_side = ""
            if portfolio and isinstance(portfolio, dict):
                for _pp in (portfolio.get("positions") or []):
                    if isinstance(_pp, dict) and (_pp.get("symbol") or "").upper() == sym.upper():
                        _has_pos = True
                        _pos_side = (_pp.get("side") or "").lower()
                        break

            if ps and ps.get("signal") == "danger":
                if mode == "defensive":
                    decisions.append({
                        "symbol": sym, "tier": "mid", "action": "reduce",
                        "confidence": 70,
                        "reasoning": "高危仓位+规则回退，建议减仓",
                    })
                else:
                    decisions.append({
                        "symbol": sym, "tier": "mid", "action": "close",
                        "confidence": 60,
                        "reasoning": "高危仓位+规则回退，建议平仓",
                    })
            elif total_signals > 0:
                bull_ratio = ss["bullish"] / total_signals
                bear_ratio = (ss["bearish"] + ss["warning"]) / total_signals
                net_score = bull_ratio - bear_ratio

                # ── 编排器置信度校准 ──
                # 编排器置信度≥45%时，给同方向信号额外加分
                _orch_boost = 0.0
                if _orch_conf >= 45:
                    if _orch_dir in ("long", "bullish") and net_score > 0:
                        _orch_boost = 0.1
                    elif _orch_dir in ("short", "bearish") and net_score < 0:
                        _orch_boost = 0.1
                    elif _orch_dir in ("long", "bullish") and net_score < 0:
                        _orch_boost = -0.1  # 编排器与信号冲突，降低净分数
                    elif _orch_dir in ("short", "bearish") and net_score > 0:
                        _orch_boost = -0.1

                _adj_net = net_score + _orch_boost

                # ── 极端波动率惩罚 ──
                if _vol_regime == "extreme":
                    _adj_net *= 0.5  # 极端波动下降低开仓信心

                # ── 已有持仓时优先管理 ──
                if _has_pos and mode != "defensive":
                    # 有持仓时默认 hold，除非信号强反转
                    if _pos_side == "long" and _adj_net < -0.5 and risk_score > 50:
                        decisions.append({
                            "symbol": sym, "tier": "mid", "action": "reduce",
                            "confidence": min(70, int(50 + abs(_adj_net) * 30)),
                            "reasoning": f"规则回退: 多仓方向+信号强反转(净={_adj_net:+.2f})+编排器{_orch_dir}(置信{_orch_conf:.0f}%)",
                        })
                    elif _pos_side == "short" and _adj_net > 0.5 and risk_score > 50:
                        decisions.append({
                            "symbol": sym, "tier": "mid", "action": "reduce",
                            "confidence": min(70, int(50 + abs(_adj_net) * 30)),
                            "reasoning": f"规则回退: 空仓方向+信号强反转(净={_adj_net:+.2f})+编排器{_orch_dir}(置信{_orch_conf:.0f}%)",
                        })
                    else:
                        decisions.append({
                            "symbol": sym, "tier": "mid", "action": "hold",
                            "confidence": 50,
                            "reasoning": f"规则回退: 持仓管理+信号未强反转(净={_adj_net:+.2f})+编排器{_orch_dir}(置信{_orch_conf:.0f}%)",
                        })
                elif _adj_net > 0.3 and risk_score < 60 and mode != "defensive":
                    decisions.append({
                        "symbol": sym, "tier": "mid", "action": "hold",
                        "confidence": 10,
                        "reasoning": (
                            f"规则回退禁止开仓(原偏多 net={_adj_net:+.2f})；"
                            f"无LLM/数据不全时仅观望，持仓靠止盈止损"
                        ),
                    })
                elif _adj_net < -0.3 and risk_score < 60 and mode != "defensive":
                    decisions.append({
                        "symbol": sym, "tier": "mid", "action": "hold",
                        "confidence": 10,
                        "reasoning": (
                            f"规则回退禁止开仓(原偏空 net={_adj_net:+.2f})；"
                            f"无LLM/数据不全时仅观望，持仓靠止盈止损"
                        ),
                    })
                else:
                    confidence = max(20, min(45, int(50 - abs(_adj_net) * 30)))
                    decisions.append({
                        "symbol": sym, "tier": "mid", "action": "hold",
                        "confidence": confidence,
                        "reasoning": f"规则回退: 多空信号平衡(多{ss['bullish']}/空{ss['bearish']+ss['warning']})，观望"
                                     + (f" 编排器={_orch_dir}(置信{_orch_conf:.0f}%)" if _orch_dir else ""),
                    })
            else:
                decisions.append({
                    "symbol": sym, "tier": "mid", "action": "hold",
                    "confidence": 30,
                    "reasoning": "LLM不可用且无分析师信号，规则建议观望",
                })

        risk_level = "critical" if risk_score > 75 else "high" if risk_score > 60 else "medium" if risk_score > 35 else "low"

        from backend.services.data_readiness_gate import strip_rule_fallback_opens
        result = {
            "overall_assessment": (
                f"LLM不可用，规则回退仅允许 hold/减仓/平仓，禁止假数据开仓 "
                f"(信号条数={sum(sum(v.values()) for v in sym_signals.values())})"
            ),
            "risk_level": risk_level,
            "decisions": decisions,
        }
        return strip_rule_fallback_opens(result)


# ══════════════════════════════════════════════════════
#  集成接口 — 一键运行所有分析师
# ══════════════════════════════════════════════════════

class TradingAnalystSystem:
    """多路分析师系统的统一入口"""

    def __init__(self):
        self.position_analyst = PositionAnalyst()
        self.market_analyst = MarketAnalyst()
        self.intel_analyst = IntelAnalyst()
        self.risk_analyst = RiskAnalyst()
        self.strategy_analyst = StrategyAnalyst()
        self.kline_analyst = KlineAnalyst()
        self.master = MasterController()

    def _attach_l1_position_sensing(
        self,
        positions: List[Dict],
        market_envs: Dict[str, Any],
        db: Session = None,
    ) -> None:
        """为持仓注入 TrendHealthScore / ReversalSignalPack。

        只做观察和 prompt 增强；健康分低不能单独触发交易动作。
        """
        if not positions:
            return
        try:
            from backend.services.trend_health_score import get_trend_health_scorer
            from backend.services.reversal_signal_pack import get_reversal_signal_builder

            scorer = get_trend_health_scorer()
            reversal_builder = get_reversal_signal_builder()
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                symbol = str(pos.get("symbol") or "").upper()
                if not symbol:
                    continue
                side = pos.get("side") or pos.get("direction") or "long"
                nature = pos.get("trade_nature") or pos.get("nature") or "swing"
                env = (market_envs or {}).get(symbol) or (market_envs or {}).get(symbol.upper()) or {}

                health = scorer.evaluate(
                    symbol=symbol,
                    side=side,
                    trade_nature=nature,
                    market_env=env,
                )
                reversal = reversal_builder.evaluate(
                    symbol=symbol,
                    side=side,
                    trade_nature=nature,
                    market_env=env,
                    health=health,
                )
                pos["trend_health"] = health.to_dict()
                pos["health_score"] = health.score
                pos["health_regime"] = health.regime
                pos["reversal_signal"] = reversal.to_dict()

                if db is not None and pos.get("id"):
                    try:
                        from backend.database.models import PaperPosition
                        db_pos = db.query(PaperPosition).filter(PaperPosition.id == int(pos["id"])).first()
                        if db_pos:
                            db_pos.health_score = health.score
                            db_pos.health_regime = health.regime
                    except Exception:
                        pass
        except Exception as sensing_err:
            logger.debug(f"[Analysts] L1持仓感知跳过: {sensing_err}")

    def run_full_analysis(
        self,
        positions: List[Dict],
        market_envs: Dict[str, Any],
        intel_data: Dict[str, Any],
        balance: Dict,
        session_stats: Dict,
        strategies: List[Dict],
        symbols: List[str],
        mode: str = "running",
        db: Session = None,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """运行所有分析师 → 总控综合决策

        Returns:
            {
                "reports": {analyst_name: AnalystReport.to_dict()},
                "master_decision": {overall_assessment, risk_level, decisions},
                "timestamp": str,
            }
        """
        # 并行运行五路分析师（规则化，毫秒级）
        # 重置 LLM 调用计数器（每个分析周期）
        KlineAnalyst._llm_call_count = 0
        KlineAnalyst._current_tick += 1  # Tier 1: tick 计数（用于缓存过期判断）
        KlineAnalyst._account_id = account_id
        KlineAnalyst._priority_symbols = [
            p.get("symbol") for p in (positions or []) if p.get("symbol")
        ]
        reports = {}
        analysis_started = time.time()

        def _log_stage(stage: str, started: float) -> None:
            logger.info(
                f"[Analysts] stage={stage} elapsed={time.time() - started:.2f}s "
                f"total={time.time() - analysis_started:.2f}s"
            )

        from backend.config.settings import ANALYST_RULES_PARALLEL, ANALYST_RULES_MAX_PARALLEL

        _rule_jobs = {
            "position": (self.position_analyst.analyze, (positions, market_envs), "仓位分析师"),
            "market": (self.market_analyst.analyze, (market_envs,), "行情分析师"),
            "intel": (self.intel_analyst.analyze, (intel_data,), "情报分析师"),
            "risk": (self.risk_analyst.analyze, (balance, positions, session_stats), "风险分析师"),
            "strategy": (self.strategy_analyst.analyze, (strategies,), "策略分析师"),
        }

        def _run_rule(key: str):
            fn, args, label = _rule_jobs[key]
            try:
                return key, fn(*args), None
            except Exception as err:
                return key, None, err

        if ANALYST_RULES_PARALLEL and len(_rule_jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            _rules_t = time.time()
            _workers = min(len(_rule_jobs), max(1, ANALYST_RULES_MAX_PARALLEL))
            with ThreadPoolExecutor(max_workers=_workers) as pool:
                futures = {pool.submit(_run_rule, k): k for k in _rule_jobs}
                for fut in as_completed(futures):
                    key, result, err = fut.result()
                    label = _rule_jobs[key][2]
                    if err is not None:
                        logger.warning(f"[Analysts] {label}异常: {err}")
                        reports[key] = AnalystReport(analyst=label, summary=f"分析失败: {err}")
                    else:
                        reports[key] = result
            _log_stage(f"rules_parallel×{_workers}", _rules_t)
        else:
            for key, (fn, args, label) in _rule_jobs.items():
                try:
                    _stage_t = time.time()
                    reports[key] = fn(*args)
                    _log_stage(key, _stage_t)
                except Exception as e:
                    logger.warning(f"[Analysts] {label}异常: {e}")
                    reports[key] = AnalystReport(analyst=label, summary=f"分析失败: {e}")

        try:
            _stage_t = time.time()
            reports["kline"] = self.kline_analyst.analyze(symbols)
            _log_stage("kline", _stage_t)
        except Exception as e:
            logger.warning(f"[Analysts] K线分析师异常: {e}")
            reports["kline"] = AnalystReport(analyst="K线分析师", summary=f"分析失败: {e}")

        try:
            _stage_t = time.time()
            self._attach_l1_position_sensing(positions, market_envs, db=db)
            _log_stage("l1_position_sensing", _stage_t)
        except Exception as e:
            logger.debug(f"[Analysts] L1持仓感知异常: {e}")

        # 总控综合决策（调用 LLM）
        reports_dict = {}
        for k, v in reports.items():
            reports_dict[k] = v.to_dict() if isinstance(v, AnalystReport) else v

        _stage_t = time.time()
        try:
            from backend.services.dual_agent_coordinator import dual_agent_coordinator
            master_decision = dual_agent_coordinator.coordinate(
                master_controller=self.master,
                reports=reports,
                symbols=symbols,
                mode=mode,
                portfolio={
                    "balance": balance,
                    "positions": positions,
                    "session_stats": session_stats,
                },
                market_envs=market_envs,
                strategies=strategies,
                db=db,
                account_id=account_id,
            )
            master_decision = self.master._enrich_master_result(master_decision)
            _log_stage("master_or_dual_agent", _stage_t)
        except Exception as _dual_err:
            logger.warning(f"[Analysts] DualAgentCoordinator异常，回退Master: {_dual_err}", exc_info=True)
            master_decision = self.master.synthesize(
                reports=reports,
                symbols=symbols,
                mode=mode,
                portfolio={
                    "balance": balance,
                    "positions": positions,
                    "session_stats": session_stats,
                },
                market_envs=market_envs,
                strategies=strategies,
                db=db,
                account_id=account_id,
            )
            _log_stage("master_llm_fallback", _stage_t)

        return {
            "reports": reports_dict,
            "master_decision": master_decision,
            "symbol_tier_slices": _build_symbol_tier_slices(reports_dict, symbols),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _build_symbol_tier_slices(reports_dict: Dict[str, Any], symbols: List[str]) -> Dict[str, Dict[str, list]]:
    """按 symbol × tier 预索引分析师信号，供 MLTO evidence_ingest 直接消费。"""
    sym_set = {(s or "").upper() for s in (symbols or []) if s}
    slices: Dict[str, Dict[str, list]] = {s: {"mid": [], "long": []} for s in sym_set}
    mid_analysts = {"kline", "market", "intel", "strategy", "position", "risk"}
    long_analysts = {"kline", "market", "intel", "strategy"}

    for analyst_key, rep in (reports_dict or {}).items():
        if analyst_key.startswith("_") or not isinstance(rep, dict):
            continue
        analyst_label = rep.get("analyst") or analyst_key
        for sig in rep.get("signals") or []:
            if not isinstance(sig, dict):
                continue
            sym = (sig.get("symbol") or "").upper()
            if sym not in slices:
                continue
            item = {**sig, "analyst": analyst_key, "source_analyst": analyst_label}
            tier = str(sig.get("tier") or "").lower()
            if tier == "mid":
                slices[sym]["mid"].append(item)
            elif tier == "long":
                slices[sym]["long"].append(item)
            else:
                if analyst_key in mid_analysts:
                    slices[sym]["mid"].append(item)
                if analyst_key in long_analysts:
                    slices[sym]["long"].append(item)
    return slices


def merge_reports_with_tier_slices(result: Dict[str, Any]) -> Dict[str, Any]:
    """将 symbol_tier_slices 合并进 reports，供 session.analyst_reports 持久化。"""
    reports = dict((result or {}).get("reports") or {})
    slices = (result or {}).get("symbol_tier_slices")
    if slices:
        reports["_symbol_tier_slices"] = slices
    return reports


# 全局单例
analyst_system = TradingAnalystSystem()
