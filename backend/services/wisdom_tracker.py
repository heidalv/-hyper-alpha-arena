"""
交易智慧效果追踪器

核心职责：
1. 记录每次 AI 决策使用了哪些智慧片段
2. 交易结束后，评估智慧对决策质量的贡献
3. 更新智慧的有效性评分（effectiveness_score）
4. 自动停用持续无效的智慧
5. 生成智慧效果报告

闭环：回测产出智慧 → 注入AI决策 → 实盘验证 → 更新评分 → 淘汰/强化
"""

import json
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

EMA_ALPHA = 0.15
MIN_SAMPLES_FOR_DEACTIVATION = 10
DEACTIVATION_THRESHOLD = 0.25

# ── 阶段2(S2-10) wisdom 闭环增强：净扣费 + 质量闸门 + 验证强度 ──
# 质量闸门：|pnl_pct| 或 |pnl| 任一达到门槛才计入有效评估样本（防噪声污染）。
QUALITY_PNL_PCT_GATE = 0.003     # 0.3% 涨跌
QUALITY_PNL_USD_GATE = 1.0       # 1 USD
# 净扣费：按 |pnl| 金额的 tanh 加权信号（小赚小亏低权重，大亏重罚）
AMOUNT_SCALE_USD = 50.0          # tanh(|pnl|/50) 饱和点
# 验证强度排序：quality_hit_count 达到该值视为充分验证（权重 1.0）
MIN_QUALITY_SAMPLES = 5


def _settings_cfg() -> Dict[str, Any]:
    """读 S2-10 配置（带缺省，settings 缺失不炸）。"""
    try:
        from backend.config.settings import (
            WISDOM_QUALITY_PNL_PCT_GATE,
            WISDOM_QUALITY_PNL_USD_GATE,
            WISDOM_AMOUNT_SCALE_USD,
            WISDOM_MIN_QUALITY_SAMPLES,
        )
        return {
            "pnl_pct_gate": float(WISDOM_QUALITY_PNL_PCT_GATE),
            "pnl_usd_gate": float(WISDOM_QUALITY_PNL_USD_GATE),
            "amount_scale": float(WISDOM_AMOUNT_SCALE_USD),
            "min_quality": int(WISDOM_MIN_QUALITY_SAMPLES),
        }
    except Exception:
        return {
            "pnl_pct_gate": QUALITY_PNL_PCT_GATE,
            "pnl_usd_gate": QUALITY_PNL_USD_GATE,
            "amount_scale": AMOUNT_SCALE_USD,
            "min_quality": MIN_QUALITY_SAMPLES,
        }


class WisdomTracker:
    """追踪交易智慧在实盘中的效果"""

    # ── 4.2 噪音过滤：系统中途软退出/反手砍仓样本不参与评估 ──
    # 这些退出不代表 thesis/策略逻辑失效，而是风控或系统行为（历史胜率极低），
    # 计入样本会污染智慧评分（把"被砍仓"误判为"智慧无效"）。
    NOISE_CLOSE_KEYWORDS = (
        "master_running_reduce",
        "master_defensive_reduce",
        "ai_reverse",
        "ai_cut_loss",
        "soft_exit",
    )

    @staticmethod
    def _is_noise_close(close_reason: str) -> bool:
        """退出原因命中噪音清单 → 样本不计入验证。"""
        if not close_reason:
            return False
        rl = str(close_reason).lower()
        return any(kw in rl for kw in WisdomTracker.NOISE_CLOSE_KEYWORDS)

    def record_wisdom_ids(
        self,
        db: Session,
        wisdom_ids: List[int],
    ) -> int:
        """记录一次注入（不依赖 AIDecisionLog，供 thesis 链使用）。

        返回实际计数条数。
        """
        from backend.database.models import TradingWisdom

        counted = 0
        for wid in wisdom_ids:
            w = db.query(TradingWisdom).filter(TradingWisdom.id == wid).first()
            if w and w.is_active:
                w.applied_count = (w.applied_count or 0) + 1
                counted += 1
        db.flush()
        return counted

    def record_wisdom_usage(
        self,
        db: Session,
        decision_log_id: int,
        wisdom_ids: List[int],
    ):
        """记录本次 AI 决策使用了哪些智慧"""
        from backend.database.models import AIDecisionLog, TradingWisdom

        decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.id == decision_log_id
        ).first()
        if not decision:
            return

        decision.wisdom_applied = {"wisdom_ids": wisdom_ids, "applied_at": datetime.now(timezone.utc).isoformat()}

        for wid in wisdom_ids:
            w = db.query(TradingWisdom).filter(TradingWisdom.id == wid).first()
            if w:
                w.applied_count = (w.applied_count or 0) + 1

        db.flush()

    def evaluate_wisdom_result(
        self,
        db: Session,
        wisdom_ids: List[int],
        pnl: float,
        pnl_pct: float,
        close_reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """按 PnL 结果评估智慧效果（不依赖 AIDecisionLog，供 thesis 链使用）。

        净扣费：tanh(|pnl|/scale) 金额加权信号 ∈ [-1,1]。
        质量闸门：|pnl_pct| 与 |pnl| 任一达到门槛才计入有效样本。
        噪音过滤：master_running_reduce 等软退出样本跳过（不计分不计数）。

        返回 {"signal", "skipped", "deactivated"} 或 None。
        """
        from backend.database.models import TradingWisdom

        if not wisdom_ids:
            return None

        # 噪音过滤（4.2）：软退出/反手砍仓 → 不评估
        if self._is_noise_close(close_reason):
            logger.debug(
                f"[WisdomTracker] 噪音退出 {close_reason!r}，跳过评估 wisdom_ids={wisdom_ids}"
            )
            return {"signal": 0.0, "skipped": "noise_close", "deactivated": []}

        cfg = _settings_cfg()
        pnl = float(pnl or 0)
        pnl_pct = float(pnl_pct or 0)

        # 质量闸门：噪声样本不计入（也不更新评分）
        if abs(pnl_pct) < cfg["pnl_pct_gate"] and abs(pnl) < cfg["pnl_usd_gate"]:
            logger.debug(
                f"[WisdomTracker] 未过质量闸门，跳过评估 (pnl={pnl:.4f} pct={pnl_pct:.4f})"
            )
            return {"signal": 0.0, "skipped": "quality_gate", "deactivated": []}

        # 净扣费：tanh(|pnl|/scale) 金额加权信号 ∈ [-1, 1]
        amount_w = math.tanh(abs(pnl) / max(cfg["amount_scale"], 1e-9))
        signal = amount_w if pnl > 0 else -amount_w
        is_positive = pnl > 0
        deactivated = []

        for wid in wisdom_ids:
            w = db.query(TradingWisdom).filter(TradingWisdom.id == wid).first()
            if not w:
                continue

            old_score = w.effectiveness_score if w.effectiveness_score is not None else 0.5
            new_score = old_score * (1 - EMA_ALPHA) + signal * EMA_ALPHA
            w.effectiveness_score = round(new_score, 4)
            w.last_updated = datetime.now(timezone.utc)

            # 质量样本计数（验证强度排序依据）
            w.evaluation_count = (w.evaluation_count or 0) + 1
            if is_positive:
                w.quality_hit_count = (w.quality_hit_count or 0) + 1

            if (w.applied_count or 0) >= MIN_SAMPLES_FOR_DEACTIVATION and new_score < DEACTIVATION_THRESHOLD:
                w.is_active = False
                deactivated.append(wid)
                logger.info(
                    f"[WisdomTracker] 停用低效智慧 id={wid} type={w.wisdom_type} "
                    f"score={new_score:.3f} after {w.applied_count} uses "
                    f"(eval={w.evaluation_count})"
                )

        db.flush()
        logger.debug(
            f"[WisdomTracker] 评估 wisdom_ids={wisdom_ids}: "
            f"pnl={pnl:.4f} pct={pnl_pct:.4f} signal={signal:+.4f}"
        )
        return {"signal": round(signal, 4), "skipped": None, "deactivated": deactivated}

    def evaluate_trade_result(
        self,
        db: Session,
        decision_log_id: int,
        pnl: float,
        pnl_pct: float,
    ):
        """交易结束后评估智慧效果（阶段2(S2-10) 增强）。

        净扣费：信号不再只是 0/1，而是按 |pnl| 金额 tanh 加权 ——
        亏损金额越大扣分越狠，小赚小亏几乎不影响评分。

        质量闸门：|pnl_pct| 与 |pnl| 任一达到门槛才计入有效样本，
        否则跳过（不污染 evaluation_count / quality_hit_count）。

        4.2 起委托 evaluate_wisdom_result（含噪音过滤）。
        """
        from backend.database.models import AIDecisionLog

        decision = db.query(AIDecisionLog).filter(
            AIDecisionLog.id == decision_log_id
        ).first()
        if not decision:
            return

        applied = decision.wisdom_applied
        if not applied or not isinstance(applied, dict):
            return

        wisdom_ids = applied.get("wisdom_ids", [])
        if not wisdom_ids:
            return

        close_reason = applied.get("close_reason", "") or ""
        self.evaluate_wisdom_result(db, wisdom_ids, pnl, pnl_pct, close_reason=close_reason)

    def parse_wisdom_ids_from_response(self, ai_response: str) -> List[int]:
        """从 AI 决策响应中解析使用的 wisdom_ids"""
        import re
        match = re.search(r'<!-- wisdom_ids:(\[[\d,\s]*\]) -->', ai_response)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        return []

    def get_ranked_wisdom(self, db: Session, limit: int = 20, min_quality: Optional[int] = None) -> List[Dict[str, Any]]:
        """验证强度排序（阶段2(S2-10)）：避免样本少但碰巧高分的智慧霸榜。

        强度 = effectiveness_score × min(1, quality_hit_count/min_quality)
              × log(1+applied_count)
        仅返回活跃智慧，按强度降序。
        """
        from backend.database.models import TradingWisdom

        cfg = _settings_cfg()
        min_q = min_quality if min_quality is not None else cfg["min_quality"]

        rows = db.query(TradingWisdom).filter(TradingWisdom.is_active == True).all()  # noqa: E712
        ranked = []
        for w in rows:
            eff = w.effectiveness_score if w.effectiveness_score is not None else 0.5
            qhits = w.quality_hit_count or 0
            strength = (
                max(0.0, eff)
                * min(1.0, qhits / max(min_q, 1))
                * math.log1p(w.applied_count or 0)
            )
            ranked.append({
                "id": w.id,
                "type": w.wisdom_type,
                "tier": w.tier,
                "template_id": w.template_id,
                "effectiveness": round(eff, 4),
                "evaluation_count": w.evaluation_count or 0,
                "quality_hit_count": qhits,
                "applied_count": w.applied_count or 0,
                "strength": round(strength, 4),
            })
        ranked.sort(key=lambda x: x["strength"], reverse=True)
        return ranked[:limit]

    def get_wisdom_effectiveness_report(self, db: Session) -> Dict[str, Any]:
        """生成智慧效果报告"""
        from backend.database.models import TradingWisdom

        all_wisdom = db.query(TradingWisdom).filter(
            TradingWisdom.applied_count > 0
        ).all()

        if not all_wisdom:
            return {"total": 0, "active": 0, "deactivated": 0, "by_type": {}}

        active = [w for w in all_wisdom if w.is_active]
        deactivated = [w for w in all_wisdom if not w.is_active]

        by_type: Dict[str, Dict] = {}
        for w in all_wisdom:
            wt = w.wisdom_type or "unknown"
            if wt not in by_type:
                by_type[wt] = {"count": 0, "avg_effectiveness": 0.0, "total_applied": 0, "active": 0}
            by_type[wt]["count"] += 1
            by_type[wt]["total_applied"] += (w.applied_count or 0)
            if w.is_active:
                by_type[wt]["active"] += 1
            if w.effectiveness_score is not None:
                by_type[wt]["avg_effectiveness"] += w.effectiveness_score

        for wt, data in by_type.items():
            if data["count"] > 0:
                data["avg_effectiveness"] = round(data["avg_effectiveness"] / data["count"], 3)

        top_wisdom = sorted(
            [w for w in active if w.effectiveness_score is not None],
            key=lambda w: w.effectiveness_score,
            reverse=True,
        )[:5]

        return {
            "total": len(all_wisdom),
            "active": len(active),
            "deactivated": len(deactivated),
            "by_type": by_type,
            "top_wisdom": [
                {
                    "id": w.id,
                    "type": w.wisdom_type,
                    "tier": w.tier,
                    "effectiveness": w.effectiveness_score,
                    "applied_count": w.applied_count,
                    "template_id": w.template_id,
                }
                for w in top_wisdom
            ],
        }

    def auto_refresh_wisdom(self, db: Session):
        """自动刷新：重新提取并保存所有活跃模板的智慧"""
        from backend.database.models import StrategyTemplate
        from backend.services.backtest_insight_compiler import insight_compiler

        templates = db.query(StrategyTemplate).filter(
            StrategyTemplate.is_active == True
        ).all()

        refreshed = 0
        for tpl in templates:
            try:
                wisdom = insight_compiler.extract_wisdom(db, tpl.template_id)
                if wisdom.get("meta", {}).get("runs_analyzed", 0) > 0:
                    tier = getattr(tpl, "tier", "mid") or "mid"
                    insight_compiler.save_wisdom_to_db(db, tpl.template_id, wisdom, tier)
                    refreshed += 1
            except Exception as e:
                logger.warning(f"[WisdomTracker] 刷新模板 {tpl.template_id} 智慧失败: {e}")

        logger.info(f"[WisdomTracker] 智慧刷新完成: {refreshed}/{len(templates)} 个模板")
        return refreshed


wisdom_tracker = WisdomTracker()
