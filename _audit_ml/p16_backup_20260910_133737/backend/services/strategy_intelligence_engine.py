"""
策略智能选择引擎 — 基于环境-绩效矩阵的策略选择与切换

核心职责：
1. select_best_strategy: 根据当前市场指纹，从绩效矩阵中选择得分最高的策略模板
2. should_switch_strategy: 判断当前策略是否应切换到更适合当前环境的策略
3. get_regime_ranking: 查询所有模板在指定环境下的排名
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SOURCE_WEIGHTS = {"live": 1.0, "paper": 0.6, "backtest": 0.3}
SWITCH_THRESHOLD = 1.5  # 新策略得分需高于当前的1.5倍才建议切换
MIN_SAMPLES = 5         # 绩效矩阵至少需要5个样本才可信


class StrategyIntelligenceEngine:
    """策略智能选择引擎（单例）"""

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
        logger.info("[Intelligence] 策略智能选择引擎初始化完成")

    def select_best_strategy(
        self,
        db: Session,
        symbol: str,
        market_data: Optional[Dict] = None,
        regime: Optional[str] = None,
        top_n: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        基于三源融合的绩效矩阵选择最优策略。

        Args:
            db: 数据库会话
            symbol: 交易对
            market_data: K线数据（用于计算指纹，可选）
            regime: 直接指定市场状态（优先于 market_data）
            top_n: 返回前N个候选

        Returns:
            排序后的候选模板列表 [{"template_id": ..., "score": ..., "regime": ...}]
        """
        from backend.database.models import StrategyRegimeScore, StrategyTemplate

        # 确定当前市场状态
        if regime:
            current_regime = regime
        elif market_data:
            from backend.services.market_fingerprint import compute_fingerprint_from_live
            fp = compute_fingerprint_from_live(market_data)
            current_regime = fp.regime
        else:
            current_regime = self._detect_regime_for_symbol(db, symbol)

        # 查询绩效矩阵
        scores = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.regime == current_regime,
            StrategyRegimeScore.sample_count >= MIN_SAMPLES,
        ).all()

        if not scores:
            logger.info(f"[Intelligence] {symbol} 无{current_regime}环境下的绩效数据，回退到全模板")
            return self._fallback_select(db, top_n)

        # 三源加权聚合
        template_agg: Dict[str, float] = {}
        for s in scores:
            w = SOURCE_WEIGHTS.get(s.source, 0.3)
            decay = max(0.01, s.decay_factor or 1.0)
            composite = s.composite_score or 0
            template_agg[s.template_id] = (
                template_agg.get(s.template_id, 0) + composite * w * decay
            )

        ranked = sorted(template_agg.items(), key=lambda x: -x[1])[:top_n]

        result = []
        for tid, score in ranked:
            tmpl = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == tid,
                StrategyTemplate.is_active == True,
            ).first()
            if tmpl is None:
                continue
            result.append({
                "template_id": tid,
                "score": round(score, 4),
                "regime": current_regime,
                "name": tmpl.name,
                "category": tmpl.category,
            })

        logger.info(
            f"[Intelligence] {symbol} regime={current_regime} 候选策略: "
            + ", ".join(f"{r['template_id']}({r['score']:.3f})" for r in result)
        )
        return result

    def should_switch_strategy(
        self,
        db: Session,
        current_strategy_template_id: str,
        current_regime: str,
    ) -> Optional[Dict[str, Any]]:
        """
        判断是否需要切换策略。

        Returns:
            None 表示不需要切换，否则返回建议切换的目标模板信息。
        """
        from backend.database.models import StrategyRegimeScore

        current_score = self._get_weighted_score(db, current_strategy_template_id, current_regime)
        if current_score <= 0:
            return None

        # 找到该环境下最优模板
        best = self.select_best_strategy(db, symbol="", regime=current_regime, top_n=1)
        if not best:
            return None

        best_tid = best[0]["template_id"]
        best_score = best[0]["score"]

        if best_tid == current_strategy_template_id:
            return None

        if best_score > current_score * SWITCH_THRESHOLD:
            logger.info(
                f"[Intelligence] 建议切换策略: "
                f"{current_strategy_template_id}({current_score:.3f}) → "
                f"{best_tid}({best_score:.3f}) regime={current_regime}"
            )
            return {
                "target_template_id": best_tid,
                "target_score": best_score,
                "current_score": current_score,
                "regime": current_regime,
                "improvement_pct": round((best_score / max(current_score, 0.001) - 1) * 100, 1),
            }

        return None

    def get_regime_ranking(
        self, db: Session, regime: str
    ) -> List[Dict[str, Any]]:
        """获取指定环境下所有模板的排名"""
        from backend.database.models import StrategyRegimeScore

        scores = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.regime == regime,
        ).all()

        template_agg: Dict[str, Dict] = {}
        for s in scores:
            if s.template_id not in template_agg:
                template_agg[s.template_id] = {
                    "weighted_score": 0.0,
                    "total_samples": 0,
                    "sources": {},
                }
            w = SOURCE_WEIGHTS.get(s.source, 0.3)
            decay = max(0.01, s.decay_factor or 1.0)
            template_agg[s.template_id]["weighted_score"] += (s.composite_score or 0) * w * decay
            template_agg[s.template_id]["total_samples"] += s.sample_count or 0
            template_agg[s.template_id]["sources"][s.source] = {
                "score": round(s.composite_score or 0, 4),
                "samples": s.sample_count or 0,
                "win_rate": round(s.win_rate or 0, 3),
            }

        ranked = sorted(template_agg.items(), key=lambda x: -x[1]["weighted_score"])
        return [
            {"template_id": tid, "regime": regime, **info}
            for tid, info in ranked
        ]

    def get_strategy_score(
        self, db: Session, template_id: str, regime: str
    ) -> float:
        """获取策略在指定环境下的加权得分"""
        return self._get_weighted_score(db, template_id, regime)

    # ══════════════════════════════════════════════════
    #  私有方法
    # ══════════════════════════════════════════════════

    def _get_weighted_score(self, db: Session, template_id: str, regime: str) -> float:
        """计算策略的三源加权得分"""
        from backend.database.models import StrategyRegimeScore

        scores = db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id == template_id,
            StrategyRegimeScore.regime == regime,
        ).all()

        total = 0.0
        for s in scores:
            w = SOURCE_WEIGHTS.get(s.source, 0.3)
            decay = max(0.01, s.decay_factor or 1.0)
            total += (s.composite_score or 0) * w * decay
        return total

    def _detect_regime_for_symbol(self, db: Session, symbol: str) -> str:
        """检测指定交易对的当前市场状态 — 优先用因子引擎"""
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            from backend.services.exchange_config import get_active_exchange
            from backend.services.market_fingerprint import compute_fingerprint_from_live
            coordinator = StrategyCoordinator(db)
            exchange = get_active_exchange()
            now_ts = int(datetime.now(timezone.utc).timestamp())
            start_ts = now_ts - 30 * 86400
            klines = coordinator._query_klines(symbol, "1h", start_ts, now_ts, exchange)
            if not klines or len(klines) < 60:
                return "ranging"

            # D7: 优先用因子引擎判断市场状态
            try:
                import pandas as pd
                from services.factor_engine.base_factors import FactorEngine
                from services.factor_engine.factor_weighting import DynamicFactorWeighting
                _df = pd.DataFrame(klines)
                if not _df.empty and all(c in _df.columns for c in ('open','high','low','close','volume')):
                    _fe = FactorEngine()
                    _fv = _fe.compute_all_factors(_df)
                    if _fv:
                        _fw = DynamicFactorWeighting(factor_engine=_fe)
                        _adp = _fw.calculate_adaptive_weights(_fv, None)
                        if _adp.confidence > 0.4:
                            return _adp.regime.value
            except Exception:
                pass

            fp_data = {"closes":[k["close"] for k in klines],"highs":[k["high"] for k in klines],"lows":[k["low"] for k in klines],"volumes":[k["volume"] for k in klines]}
            fp = compute_fingerprint_from_live(fp_data)
            return fp.regime
        except Exception as e:
            logger.warning(f"[Intelligence] 检测 {symbol} 状态失败: {e}")
            return "ranging"

    def _fallback_select(self, db: Session, top_n: int) -> List[Dict]:
        """无绩效数据时，回退到按评分/使用量选择"""
        from backend.database.models import StrategyTemplate

        templates = db.query(StrategyTemplate).filter(
            StrategyTemplate.is_active == True,
        ).order_by(
            StrategyTemplate.rating.desc(),
            StrategyTemplate.live_usage_count.desc(),
        ).limit(top_n).all()

        return [
            {
                "template_id": t.template_id,
                "score": 0.0,
                "regime": "unknown",
                "name": t.name,
                "category": t.category or "unknown",
            }
            for t in templates
        ]


# 全局单例
strategy_intelligence = StrategyIntelligenceEngine()
