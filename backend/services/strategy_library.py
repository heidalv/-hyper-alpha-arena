"""
Strategy Library — 策略库服务（混合路线核心）

职责:
1. 从 strategy_templates 表加载活跃模板
2. 按市场状态 (regime) + tier 匹配最佳模板
3. 从模板 signal_params 计算技术信号
4. 从模板创建 AIStrategy 记录（首次使用时）
5. 跟踪模板表现并更新评级

LLM 角色变更: 从"裸决策者"变为"信号审核者"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── regime 映射 ─────────────────────────────────────────
# MarketRegime → template.market_regime
REGIME_TO_TEMPLATE_REGIME: Dict[str, str] = {
    "trending_up": "bull",
    "trending_down": "bull",       # 趋势策略可做多做空，方向由信号决定
    "ranging": "sideways",
    "low_volatility": "sideways",
    "high_volatility": "all",      # 高波动匹配 "all" 通配模板
    "crash": "",                   # 崩盘不匹配任何模板
    "bull": "bull",
    "bear": "bull",
    "sideways": "sideways",
    "all": "all",
}


# ── 信号计算所需的 K 线数量 ─────────────────────────────
MIN_KLINE_COUNT = 120  # 最长的 lookback (ema_slow=100 + 安全边距)


def build_promoted_strategy_config(strategy, memory=None) -> Dict[str, Any]:
    """从 AIStrategy + StrategyMemory 构建含完整 genome 快照的模板 strategy_config。"""
    genome = strategy.genome or {}
    pv = strategy.prompt_variables or {}
    signal_params = genome.get("signal_params") or {}
    if not signal_params and isinstance(genome.get("indicators"), dict):
        signal_params = genome.get("indicators") or {}

    risk_params = {
        "max_position_size": strategy.max_position_size,
        "stop_loss_pct": strategy.stop_loss_pct,
        "take_profit_pct": strategy.take_profit_pct,
        "max_daily_loss": strategy.max_daily_loss,
        "max_leverage": strategy.max_leverage,
        "default_leverage": strategy.default_leverage,
        "leverage_mode": strategy.leverage_mode,
        "snowball_enabled": strategy.snowball_enabled,
        "signal_params": signal_params,
    }

    now_iso = datetime.now(timezone.utc).isoformat()
    mem_summary = {}
    if memory:
        mem_summary = {
            "total_trades": memory.total_trades,
            "win_rate": memory.win_rate,
            "sharpe_ratio": memory.sharpe_ratio,
            "max_drawdown": memory.max_drawdown,
            "successful_patterns": (memory.successful_patterns or [])[:5],
            "key_lessons": (memory.key_lessons or [])[:5],
        }

    return {
        "strategy_logic": pv.get("strategy_logic", ""),
        "entry_conditions": pv.get("entry_conditions", []),
        "exit_conditions": pv.get("exit_conditions", []),
        "risk_params": risk_params,
        "signal_definitions": genome.get("signal_definitions", []),
        "applicable_symbols": strategy.target_symbols or ["BTC"],
        "genome": {
            "signal_params": signal_params,
            "trade_nature": genome.get("trade_nature") or "swing",
            "source_strategy_id": strategy.strategy_id,
            "promoted_at": now_iso,
            "direction": genome.get("direction"),
            "timeframe_tier": getattr(strategy, "timeframe_tier", None) or "mid",
        },
        "verified_at": now_iso,
        "verification_source": "live",
        "memory_summary": mem_summary,
        "_live_stats": {"total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0},
    }


@dataclass
class TemplateMatch:
    """模板匹配结果"""
    template_id: str
    template_name: str
    category: str
    tier: str
    timeframe: str
    rating: float
    confidence: float                # 匹配置信度 0~1
    match_reason: str                # "exact_regime_match" / "wildcard_all" / "category_fallback"
    risk_params: Dict[str, Any] = field(default_factory=dict)
    signal_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalResult:
    """信号计算结果"""
    direction: str                   # "buy" / "sell" / "hold"
    confidence: float                # 0~100
    reason: str                      # 人类可读的信号原因
    score: float                     # 原始信号得分 -1.0~+1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class StrategyLibrary:
    """策略库 — 加载/匹配/信号计算/评级"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._templates: List[Dict[str, Any]] = []
        self._loaded = False
        logger.info("[StrategyLibrary] 策略库单例已创建")

    # ══════════════════════════════════════════════════
    #  模板加载
    # ══════════════════════════════════════════════════

    def load_templates(self, db: Session) -> int:
        """从 strategy_templates 表加载所有活跃模板到内存缓存"""
        from backend.database.models import StrategyTemplate

        rows = db.query(StrategyTemplate).filter(
            StrategyTemplate.is_active == True,
        ).all()

        self._templates = []
        for tpl in rows:
            cfg = tpl.strategy_config or {}
            risk = cfg.get("risk_params", {}) if isinstance(cfg, dict) else {}
            signal = risk.get("signal_params", {}) if isinstance(risk, dict) else {}
            if not signal and isinstance(cfg, dict):
                _genome_snap = cfg.get("genome") or {}
                if isinstance(_genome_snap, dict):
                    signal = _genome_snap.get("signal_params") or {}
            if not isinstance(signal, dict):
                signal = {}
            if not isinstance(risk, dict):
                risk = {}
            _live_stats = cfg.get("_live_stats", {}) if isinstance(cfg, dict) else {}
            if not isinstance(_live_stats, dict):
                _live_stats = {}

            self._templates.append({
                "template_id": tpl.template_id,
                "name": tpl.name,
                "category": tpl.category or cfg.get("category", "trend") if isinstance(cfg, dict) else "trend",
                "market_regime": tpl.market_regime or "all",
                "tier": tpl.tier or "mid",
                "timeframe": tpl.timeframe or "1h",
                "risk_level": tpl.risk_level or "moderate",
                "rating": float(tpl.rating or 3.0),
                "source": tpl.source or "builtin",
                "tags": list(tpl.tags or []),
                "signal_params": signal,
                "risk_params": {k: v for k, v in risk.items() if k != "signal_params"},
                "strategy_logic": cfg.get("strategy_logic", "") if isinstance(cfg, dict) else "",
                "verified_at": cfg.get("verified_at") if isinstance(cfg, dict) else None,
                "verification_source": cfg.get("verification_source") if isinstance(cfg, dict) else None,
                "_live_stats": _live_stats,
                "_orm": tpl,
            })

        self._loaded = True
        logger.info(f"[StrategyLibrary] 加载 {len(self._templates)} 个活跃模板")
        return len(self._templates)

    def reload_if_needed(self, db: Session) -> int:
        """如果未加载则加载，否则返回缓存数"""
        if not self._loaded:
            return self.load_templates(db)
        return len(self._templates)

    @property
    def template_count(self) -> int:
        return len(self._templates)

    # ══════════════════════════════════════════════════
    #  模板匹配
    # ══════════════════════════════════════════════════

    def match(
        self,
        db: Session,
        market_regime: str,
        symbol: str = "",
        tier: Optional[str] = None,
        trend_direction: str = "neutral",
    ) -> List[TemplateMatch]:
        """按市场状态匹配最佳模板。

        Args:
            db: 数据库会话（用于懒加载模板）
            market_regime: 当前市场状态 (trending_up/trending_down/ranging/crash/...)
            symbol: 交易对（预留，未来可按 symbol 筛选模板）
            tier: 时间层级筛选 (short/mid/long)，None 返回所有 tier
            trend_direction: 趋势方向 (up/down/neutral)

        Returns:
            匹配的模板列表，按综合评分降序排列
        """
        self.reload_if_needed(db)

        if market_regime == "crash":
            return []  # 崩盘不匹配任何模板

        target_regime = REGIME_TO_TEMPLATE_REGIME.get(market_regime, "all")
        results = []

        # P5-fix(2026-05-08): regime 兼容矩阵 — 单一 regime 不应过度过滤掉所有反向类别模板
        # 例如 ranging 下也允许 trend/momentum 作为 0.45 权重的备选（趋势若真起来 AI 还能拿到信号）
        # 这是为了解决"市场1h判定为ranging，但4h/15m已起趋势 → AI 无 BUY 信号被锁死 hold"的问题
        REGIME_FALLBACK_WEIGHTS = {
            "sideways": {"bull": 0.50},   # 震荡市仍允许趋势模板低权重备选
            "bull":     {"sideways": 0.45},  # 趋势市也保留区间模板（用于回调识别）
            "all":      {"bull": 0.85, "sideways": 0.85},
        }

        for tpl in self._templates:
            tpl_regime = tpl.get("market_regime", "all")
            tpl_tier = tpl.get("tier", "mid")

            # tier 筛选
            if tier and tpl_tier != tier:
                continue

            # regime 匹配评分
            match_reason = None
            if tpl_regime == target_regime:
                match_conf = 1.0
                match_reason = "exact_regime_match"
            elif tpl_regime == "all":
                match_conf = 0.55
                match_reason = "wildcard_all"
            else:
                # P5-fix: 兼容矩阵 fallback — 不再 continue 直接放弃
                _fallback_w = REGIME_FALLBACK_WEIGHTS.get(target_regime, {}).get(tpl_regime, 0)
                if _fallback_w > 0:
                    match_conf = _fallback_w
                    match_reason = f"cross_regime_fallback({target_regime}→{tpl_regime})"
                else:
                    continue  # 真不匹配（如 sideways 下不会要 crash 模板）

            # rating 加权
            rating = float(tpl.get("rating", 3.0))
            rating_factor = min(1.3, max(0.5, rating / 3.5))
            final_conf = match_conf * rating_factor

            # verified / champion 加权（软优先召回）
            tags_lower = [str(t).lower() for t in (tpl.get("tags") or [])]
            verified_boost = 1.0
            if "champion" in tags_lower:
                verified_boost += 0.30
            if "backtest_validated" in tags_lower or "回测验证" in (tpl.get("tags") or []):
                verified_boost += 0.20
            if tpl.get("source") == "promoted" or tpl.get("verified_at"):
                verified_boost += 0.15
            final_conf *= verified_boost

            # P5-fix: 阈值从 0.35 降到 0.30，让 cross_regime fallback (0.45*0.5=0.225 起) 也能进
            # 但仅当确实 fallback 时降阈值，exact/all 仍按 0.35 卡
            _min_conf = 0.30 if match_reason and "fallback" in match_reason else 0.35
            if final_conf < _min_conf:
                continue

            results.append(TemplateMatch(
                template_id=tpl["template_id"],
                template_name=tpl["name"],
                category=tpl["category"],
                tier=tpl_tier,
                timeframe=tpl["timeframe"],
                rating=rating,
                confidence=round(final_conf, 3),
                match_reason=match_reason,
                risk_params=tpl.get("risk_params", {}),
                signal_params=tpl.get("signal_params", {}),
            ))

        results.sort(key=lambda m: m.confidence, reverse=True)
        return results

    def get_template_by_id(self, db: Session, template_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取模板数据"""
        self.reload_if_needed(db)
        for tpl in self._templates:
            if tpl["template_id"] == template_id:
                return tpl
        return None

    # ══════════════════════════════════════════════════
    #  信号计算
    # ══════════════════════════════════════════════════

    def compute_signals(
        self,
        match: TemplateMatch,
        klines_df: pd.DataFrame,
        current_price: Optional[float] = None,
    ) -> SignalResult:
        """从匹配的模板和 K 线数据计算交易信号。

        Args:
            match: 模板匹配结果
            klines_df: K 线 DataFrame (columns: open, high, low, close, volume)
            current_price: 当前价格（可选，默认取最后一根 K 线的收盘价）

        Returns:
            SignalResult: 信号方向 + 置信度 + 原因
        """
        if klines_df is None or len(klines_df) < 20:
            return SignalResult(
                direction="hold", confidence=0, reason="K线数据不足(需要≥20根)",
                score=0, metadata={}
            )

        category = match.category
        sp = match.signal_params

        try:
            if category in ("trend", "momentum", "swing"):
                return self._compute_trend_signal(klines_df, sp, category, match)
            elif category in ("range", "mean_reversion"):
                return self._compute_range_signal(klines_df, sp, match)
            elif category == "breakout":
                return self._compute_breakout_signal(klines_df, sp, match)
            else:
                return self._compute_trend_signal(klines_df, sp, "trend", match)
        except Exception as e:
            logger.debug(f"[StrategyLibrary] 信号计算异常 {match.template_id}: {e}")
            return SignalResult(
                direction="hold", confidence=0, reason=f"信号计算异常: {e}",
                score=0, metadata={}
            )

    def _compute_trend_signal(
        self,
        df: pd.DataFrame,
        sp: Dict[str, Any],
        category: str,
        match: TemplateMatch,
    ) -> SignalResult:
        """趋势/动量/摆动类信号：EMA 排列 + MACD + RSI"""
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0]*len(df))

        # EMA
        ema_fast = int(sp.get("ema_fast", 9))
        ema_mid = int(sp.get("ema_mid", 21))
        ema_slow = int(sp.get("ema_slow", 55))

        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        ema_m = close.ewm(span=ema_mid, adjust=False).mean()
        ema_s = close.ewm(span=ema_slow, adjust=False).mean()

        # 当前值
        cur_close = close.iloc[-1]
        cur_ema_f = ema_f.iloc[-1]
        cur_ema_m = ema_m.iloc[-1]
        cur_ema_s = ema_s.iloc[-1]
        prev_ema_f = ema_f.iloc[-2] if len(ema_f) >= 2 else cur_ema_f
        prev_ema_m = ema_m.iloc[-2] if len(ema_m) >= 2 else cur_ema_m

        score = 0.0
        reasons = []

        # 1. EMA 排列
        if cur_ema_f > cur_ema_m > cur_ema_s:
            score += 0.35
            reasons.append(f"EMA多头排列({ema_fast}>{ema_mid}>{ema_slow})")
        elif cur_ema_f < cur_ema_m < cur_ema_s:
            score -= 0.35
            reasons.append(f"EMA空头排列({ema_fast}<{ema_mid}<{ema_slow})")

        # 2. EMA 金叉/死叉（快线穿越中线）
        if prev_ema_f <= prev_ema_m and cur_ema_f > cur_ema_m:
            score += 0.25
            reasons.append(f"EMA金叉({ema_fast}↑{ema_mid})")
        elif prev_ema_f >= prev_ema_m and cur_ema_f < cur_ema_m:
            score -= 0.25
            reasons.append(f"EMA死叉({ema_fast}↓{ema_mid})")

        # 3. MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line

        if macd_hist.iloc[-1] > 0 and macd_hist.iloc[-2] <= 0 if len(macd_hist) >= 2 else False:
            score += 0.20
            reasons.append("MACD金叉")
        elif macd_hist.iloc[-1] < 0 and macd_hist.iloc[-2] >= 0 if len(macd_hist) >= 2 else False:
            score -= 0.20
            reasons.append("MACD死叉")
        elif macd_hist.iloc[-1] > 0:
            score += 0.10
        elif macd_hist.iloc[-1] < 0:
            score -= 0.10

        # 4. RSI
        rsi_period = int(sp.get("rsi_period", 14))
        rsi = self._compute_rsi(close, rsi_period)
        rsi_val = rsi.iloc[-1] if len(rsi) > 0 else 50

        if category == "momentum":
            # 动量策略：超买继续涨，超卖继续跌
            rsi_long_hi = float(sp.get("rsi_long_hi", 85))
            rsi_short_lo = float(sp.get("rsi_short_lo", 15))
            if rsi_val > rsi_long_hi:
                score += 0.15
                reasons.append(f"RSI动量超买({rsi_val:.0f}>{rsi_long_hi:.0f})")
            elif rsi_val < rsi_short_lo:
                score -= 0.15
                reasons.append(f"RSI动量超卖({rsi_val:.0f}<{rsi_short_lo:.0f})")
        else:
            # 趋势策略：超卖做多，超买做空
            rsi_lo = float(sp.get("rsi_long_lo", 30))
            rsi_hi = float(sp.get("rsi_short_hi", 70))
            if rsi_val < rsi_lo:
                score += 0.15
                reasons.append(f"RSI超卖反弹({rsi_val:.0f}<{rsi_lo:.0f})")
            elif rsi_val > rsi_hi:
                score -= 0.15
                reasons.append(f"RSI超买回落({rsi_val:.0f}>{rsi_hi:.0f})")

        # 5. 成交量确认（动量策略）
        if category == "momentum":
            vol_mult = float(sp.get("momentum_vol_mult", 1.1))
            vol_ma = volume.rolling(20).mean()
            if len(vol_ma) > 0 and volume.iloc[-1] > vol_ma.iloc[-1] * vol_mult:
                score = score * 1.15  # 放量确认，增强信号
                reasons.append(f"放量确认(vol>{vol_mult}x均量)")

        # 6. Swing 回调确认
        if category == "swing":
            pullback_lo = float(sp.get("swing_pullback_lo", -0.06))
            pullback_hi = float(sp.get("swing_pullback_hi", 0.01))
            # 计算价格相对 MA 的偏离
            ma_mid = close.rolling(ema_mid).mean()
            deviation = (cur_close - ma_mid.iloc[-1]) / ma_mid.iloc[-1] if ma_mid.iloc[-1] > 0 else 0
            if pullback_lo <= deviation <= pullback_hi and score > 0:
                score += 0.15
                reasons.append(f"回调买入窗口(dev={deviation:.2%})")
            elif deviation < pullback_lo:
                score *= 0.5  # 偏离过大，弱化多头信号
                reasons.append(f"偏离过大(dev={deviation:.2%}<{pullback_lo:.0%})")

        # 7. min_bars_between 检查（避免频繁交易）
        # 此检查留到调用方执行（FullAutoTrading 的 reentry_cooldown 已覆盖）

        # 换算方向
        direction = "buy" if score > 0.10 else ("sell" if score < -0.10 else "hold")
        confidence = min(95, abs(score) * 120)  # score 0.5 → 60%, 0.8 → 96%

        return SignalResult(
            direction=direction,
            confidence=round(confidence, 1),
            reason="; ".join(reasons) if reasons else "无明显信号",
            score=round(score, 3),
            metadata={
                "ema_fast": round(cur_ema_f, 4),
                "ema_slow": round(cur_ema_s, 4),
                "rsi": round(rsi_val, 1),
                "macd_hist": round(float(macd_hist.iloc[-1]), 6),
                "template_id": match.template_id,
                "category": category,
            },
        )

    def _compute_range_signal(
        self,
        df: pd.DataFrame,
        sp: Dict[str, Any],
        match: TemplateMatch,
    ) -> SignalResult:
        """震荡/均值回归类信号：BB 带边缘 + RSI 极值"""
        close = df["close"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0]*len(df))

        bb_period = int(sp.get("bb_period", 20))
        bb_std = float(sp.get("bb_std", 2.0))
        rsi_ob = float(sp.get("rsi_ob", 72))
        rsi_os = float(sp.get("rsi_os", 28))
        bb_edge_pct = float(sp.get("bb_edge_pct", 0.25))

        # BB
        ma = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        upper = ma + bb_std * std
        lower = ma - bb_std * std

        cur_close = close.iloc[-1]
        cur_upper = upper.iloc[-1]
        cur_lower = lower.iloc[-1]
        cur_ma = ma.iloc[-1]
        bb_width = (cur_upper - cur_lower) / cur_ma if cur_ma > 0 else 0

        # BB 位置 (0=下轨, 0.5=中轨, 1=上轨)
        bb_position = (cur_close - cur_lower) / (cur_upper - cur_lower) if (cur_upper - cur_lower) > 0 else 0.5

        # RSI
        rsi = self._compute_rsi(close, 14)
        rsi_val = rsi.iloc[-1] if len(rsi) > 0 else 50

        score = 0.0
        reasons = []

        # BB 边缘检测
        if bb_position <= bb_edge_pct and rsi_val < rsi_os:
            score += 0.40
            reasons.append(f"BB下轨({bb_position:.0%}) + RSI超卖({rsi_val:.0f})")
        elif bb_position >= (1 - bb_edge_pct) and rsi_val > rsi_ob:
            score -= 0.40
            reasons.append(f"BB上轨({bb_position:.0%}) + RSI超买({rsi_val:.0f})")
        elif bb_position <= 0.15:
            score += 0.20
            reasons.append(f"接近BB下轨({bb_position:.0%})")
        elif bb_position >= 0.85:
            score -= 0.20
            reasons.append(f"接近BB上轨({bb_position:.0%})")

        # 低波动确认（均值回归在低波动时更有效）
        vol_quiet_mult = float(sp.get("vol_quiet_mult", 0.8))
        vol_ma = volume.rolling(20).mean()
        if len(vol_ma) > 0 and volume.iloc[-1] < vol_ma.iloc[-1] * vol_quiet_mult:
            score = score * 1.1
            reasons.append("低波动确认")

        direction = "buy" if score > 0.15 else ("sell" if score < -0.15 else "hold")
        confidence = min(90, abs(score) * 110)

        return SignalResult(
            direction=direction,
            confidence=round(confidence, 1),
            reason="; ".join(reasons) if reasons else "无震荡信号",
            score=round(score, 3),
            metadata={
                "bb_position": round(bb_position, 3),
                "bb_width": round(bb_width, 4),
                "rsi": round(rsi_val, 1),
                "template_id": match.template_id,
                "category": match.category,
            },
        )

    def _compute_breakout_signal(
        self,
        df: pd.DataFrame,
        sp: Dict[str, Any],
        match: TemplateMatch,
    ) -> SignalResult:
        """突破类信号：N 周期高/低突破 + 放量确认 + EMA 过滤"""
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0]*len(df))

        lookback = int(sp.get("breakout_lookback", 30))
        vol_surge_mult = float(sp.get("vol_surge_mult", 1.3))
        ema_fast = int(sp.get("ema_fast", 10))
        ema_mid = int(sp.get("ema_mid", 25))

        cur_close = close.iloc[-1]
        cur_high = high.iloc[-1]
        cur_low = low.iloc[-1]

        # N 周期最高/最低（不含当前 K 线）
        n_high = high.iloc[-(lookback+1):-1].max()
        n_low = low.iloc[-(lookback+1):-1].min()

        # EMA 趋势过滤
        ema_f = close.ewm(span=ema_fast, adjust=False).mean()
        ema_m = close.ewm(span=ema_mid, adjust=False).mean()

        score = 0.0
        reasons = []

        # 突破检测
        if cur_close > n_high * 1.001:  # 0.1% 容差
            score += 0.45
            reasons.append(f"突破{lookback}周期高点({n_high:.2f})")
        elif cur_close < n_low * 0.999:
            score -= 0.45
            reasons.append(f"跌破{lookback}周期低点({n_low:.2f})")

        # 成交量确认
        vol_ma = volume.rolling(20).mean()
        if len(vol_ma) > 0 and volume.iloc[-1] > vol_ma.iloc[-1] * vol_surge_mult:
            score = score * 1.25
            reasons.append(f"放量突破(vol>{vol_surge_mult}x均量)")
        elif abs(score) > 0:
            score = score * 0.6  # 无量突破不可信
            reasons.append("无量突破(信号减弱)")

        # EMA 方向过滤（突破方向必须与 EMA 趋势一致）
        cur_ema_f = ema_f.iloc[-1]
        cur_ema_m = ema_m.iloc[-1]
        if score > 0 and cur_ema_f < cur_ema_m:
            score = score * 0.5
            reasons.append("EMA方向不一致(空头排列中的向上突破)")
        elif score < 0 and cur_ema_f > cur_ema_m:
            score = score * 0.5
            reasons.append("EMA方向不一致(多头排列中的向下突破)")

        direction = "buy" if score > 0.20 else ("sell" if score < -0.20 else "hold")
        confidence = min(90, abs(score) * 100)

        return SignalResult(
            direction=direction,
            confidence=round(confidence, 1),
            reason="; ".join(reasons) if reasons else "无突破信号",
            score=round(score, 3),
            metadata={
                "n_high": round(float(n_high), 4),
                "n_low": round(float(n_low), 4),
                "template_id": match.template_id,
                "category": match.category,
            },
        )

    # ══════════════════════════════════════════════════
    #  RSI 计算
    # ══════════════════════════════════════════════════

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI 指标"""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    # ══════════════════════════════════════════════════
    #  AIStrategy 创建
    # ══════════════════════════════════════════════════

    def create_strategy_from_template(
        self,
        db: Session,
        template_id: str,
        account_id: int,
        symbol: str,
    ) -> Optional[str]:
        """从策略模板创建 AIStrategy 记录。

        Returns:
            AIStrategy.id (str) 或 None
        """
        tpl_data = None
        for t in self._templates:
            if t["template_id"] == template_id:
                tpl_data = t
                break

        if not tpl_data:
            logger.warning(f"[StrategyLibrary] 模板 {template_id} 不存在于缓存")
            return None

        import uuid
        from backend.database.models import AIStrategy

        strategy_id = f"tpl_{template_id.replace('tpl_','')}_{uuid.uuid4().hex[:6]}"

        # 构建 genome
        genome = {
            "source_template_id": template_id,
            "trade_nature": self._category_to_nature(tpl_data["category"]),
            "signal_params": tpl_data["signal_params"],
            "generation": 1,
            "created_from_template_at": datetime.now(timezone.utc).isoformat(),
        }
        # 合并风险参数
        for k, v in tpl_data["risk_params"].items():
            genome[k] = v

        strategy = AIStrategy(
            strategy_id=strategy_id,
            name=f"[{tpl_data['category']}] {symbol} {tpl_data['tier']}",
            description=tpl_data.get("strategy_logic", f"从模板 {template_id} 创建"),
            account_id=account_id,
            primary_symbol=symbol,
            target_symbols=[symbol],
            timeframe_tier=tpl_data["tier"],
            timeframe=tpl_data["timeframe"],
            status="active",
            auto_execute=True,
            require_confirmation=False,
            max_position_size=float(tpl_data["risk_params"].get("max_position_size", 0.15)),
            stop_loss_pct=float(tpl_data["risk_params"].get("stop_loss_pct", 0.03)),
            take_profit_pct=float(tpl_data["risk_params"].get("take_profit_pct", 0.08)),
            max_daily_loss=float(tpl_data["risk_params"].get("max_daily_loss", 0.10)),
            default_leverage=int(tpl_data["risk_params"].get("default_leverage", 10)),
            max_leverage=int(tpl_data["risk_params"].get("max_leverage", 20)),
            genome=genome,
            learning_enabled=True,
            optimization_target="sharpe",
            trigger_mode="hybrid",
            auto_mode="template_driven",
            created_at=datetime.now(timezone.utc),
            activated_at=datetime.now(timezone.utc),
        )

        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        # 更新模板使用计数
        orm = tpl_data.get("_orm")
        if orm:
            orm.live_usage_count = (orm.live_usage_count or 0) + 1
            db.commit()

        logger.info(
            f"[StrategyLibrary] 从模板 {template_id} 创建 AIStrategy: "
            f"{strategy_id} symbol={symbol} tier={tpl_data['tier']}"
        )
        return strategy_id

    @staticmethod
    def _category_to_nature(category: str) -> str:
        """模板类别 → trade_nature 映射"""
        mapping = {
            "trend": "trend_follow",
            "momentum": "intraday",
            "range": "intraday",
            "swing": "swing",
            "mean_reversion": "swing",
            "breakout": "trend_follow",
            "scalping": "intraday",
        }
        return mapping.get(category, "swing")

    # ══════════════════════════════════════════════════
    #  表现跟踪 + 评级更新
    # ══════════════════════════════════════════════════

    def record_trade_result(
        self,
        db: Session,
        template_id: str,
        pnl: float,
        symbol: str,
    ) -> None:
        """记录模板的单笔交易结果（内存 + strategy_config._live_stats 持久化）"""
        from backend.database.models import StrategyTemplate

        for tpl in self._templates:
            if tpl["template_id"] != template_id:
                continue
            stats = tpl.setdefault("_live_stats", {
                "total_trades": 0,
                "total_pnl": 0.0,
                "wins": 0,
                "losses": 0,
            })
            stats["total_trades"] += 1
            stats["total_pnl"] += pnl
            if pnl > 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

            orm = tpl.get("_orm")
            if orm:
                cfg = dict(orm.strategy_config or {})
                cfg["_live_stats"] = dict(stats)
                orm.strategy_config = cfg
                orm.updated_at = datetime.now(timezone.utc)
                try:
                    db.commit()
                except Exception as exc:
                    logger.debug(f"[StrategyLibrary] live_stats 持久化失败: {exc}")
                    db.rollback()
            return

    def update_ratings(self, db: Session) -> Dict[str, Any]:
        """根据模板的在线表现更新评级，并写回 strategy_templates 表。

        每周调用一次（在 evolution_scheduler.weekly_experience_distill 中）。

        Returns:
            更新摘要: {"updated": int, "deactivated": list, "champions": list}
        """
        from backend.database.models import StrategyTemplate

        updated = 0
        deactivated = []
        champions = []

        for tpl_data in self._templates:
            stats = tpl_data.get("_live_stats", {})
            total = stats.get("total_trades", 0)
            if total < 5:
                continue  # 数据不足，不调整评级

            wins = stats.get("wins", 0)
            wr = wins / total if total > 0 else 0
            total_pnl = stats.get("total_pnl", 0.0)
            avg_pnl = total_pnl / total if total > 0 else 0

            orm = tpl_data.get("_orm")
            old_rating = float(tpl_data.get("rating", 3.0))

            if avg_pnl > 0 and wr >= 0.45:
                new_rating = min(5.0, old_rating + 0.15)
            elif avg_pnl > 0:
                new_rating = min(5.0, old_rating + 0.05)
            elif wr < 0.30 and total >= 10:
                new_rating = max(0, old_rating - 0.25)
            else:
                new_rating = max(0, old_rating - 0.10)

            # 写回内存和数据库
            tpl_data["rating"] = new_rating

            if orm:
                orm.rating = new_rating
                orm.live_avg_return = avg_pnl
                orm.updated_at = datetime.now(timezone.utc)

            # Champion 检测
            if new_rating >= 4.5 and old_rating < 4.5:
                champions.append(tpl_data["template_id"])
                if orm:
                    tags = list(orm.tags or [])
                    if "champion" not in tags:
                        tags.append("champion")
                        orm.tags = tags
                    tpl_data["tags"] = tags

            # 失活检测
            if new_rating < 2.0 and tpl_data.get("source") != "builtin":
                tpl_data["is_active"] = False
                deactivated.append(tpl_data["template_id"])
                if orm:
                    orm.is_active = False

            updated += 1

        if updated:
            db.commit()
            logger.info(
                f"[StrategyLibrary] 评级更新: {updated} 个模板, "
                f"champions={champions}, deactivated={deactivated}"
            )

        # 周度评级后重置计数器（累计 stats 已持久化到 strategy_config._live_stats）
        for tpl_data in self._templates:
            stats = tpl_data.get("_live_stats") or {}
            if stats:
                tpl_data["_live_stats"] = {
                    "total_trades": 0,
                    "total_pnl": 0.0,
                    "wins": 0,
                    "losses": 0,
                }
                orm = tpl_data.get("_orm")
                if orm:
                    cfg = dict(orm.strategy_config or {})
                    cfg["_live_stats"] = dict(tpl_data["_live_stats"])
                    orm.strategy_config = cfg

        return {"updated": updated, "deactivated": deactivated, "champions": champions}

    # ══════════════════════════════════════════════════
    #  状态查询
    # ══════════════════════════════════════════════════

    def get_status(self) -> Dict[str, Any]:
        """返回策略库状态摘要"""
        by_tier = {"short": 0, "mid": 0, "long": 0}
        by_category: Dict[str, int] = {}
        for tpl in self._templates:
            t = tpl.get("tier", "mid")
            by_tier[t] = by_tier.get(t, 0) + 1
            c = tpl.get("category", "unknown")
            by_category[c] = by_category.get(c, 0) + 1

        return {
            "total_templates": len(self._templates),
            "loaded": self._loaded,
            "by_tier": by_tier,
            "by_category": by_category,
            "templates": [
                {
                    "template_id": t["template_id"],
                    "name": t["name"],
                    "rating": t["rating"],
                    "category": t["category"],
                    "tier": t["tier"],
                    "stats": t.get("_live_stats", {}),
                }
                for t in self._templates
            ],
        }


# ── 模块级单例 ────────────────────────────────────────
strategy_library = StrategyLibrary()
