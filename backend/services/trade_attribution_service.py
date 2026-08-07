"""TradeAttributionService — 因果归因分析（2026-07-21 P2）

背景：
    系统从未把"开仓时的因子状态/orchestrator方向/regime"和"最终亏损"做关联分析。
    decision_arbiter.jsonl 只记"谁想平"，decision_feedback_service 只做 close_reason
    维度的 PnL 分桶统计，ai_attribution_service 已废弃为 stub。

本服务提供：
    1. capture_factor_snapshot() — 从 market_envs 提取因子快照，注入 market_snapshot_json
    2. build_attribution_report() — 按开仓上下文维度做"条件→结果"关联分析

使用方式：
    开仓时：attribution_service.capture_factor_snapshot(market_envs) → dict → 存入 market_snapshot_json
    平仓后：attribution_service.build_attribution_report(db, days=30) → 归因报告
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TradeAttributionService:
    """因果归因分析服务。"""

    # ── 开仓时：捕获因子快照 ──

    @staticmethod
    def capture_factor_snapshot(market_envs: Dict[str, Any]) -> Dict[str, Any]:
        """从 market_envs 提取开仓时的因子状态快照。

        将此快照合并到 DecisionSnapshot.market_snapshot_json 中，
        平仓后可用于关联分析（哪些因子状态导致了盈利/亏损）。

        Returns:
            dict — 包含因子方向/强度/置信度/regime 的快照
        """
        if not market_envs:
            return {}

        snapshot: Dict[str, Any] = {"_factor_snapshot_captured_at": datetime.now(timezone.utc).isoformat()}

        for sym, info in market_envs.items():
            if not isinstance(info, dict):
                continue
            sym_data: Dict[str, Any] = {
                "factor_direction": float(info.get("factor_direction", 0) or 0),
                "factor_strength": float(info.get("factor_strength", 0) or 0),
                "factor_confidence": float(info.get("factor_confidence", 0) or 0),
                "factor_regime": info.get("factor_regime", "unknown"),
                "factor_engine_ok": info.get("factor_engine_ok", True),
                "market_cycle": info.get("market_cycle", "unknown"),
                "trend_direction": info.get("trend_direction", "neutral"),
                "volatility_regime": info.get("volatility_regime", "normal"),
                "volatility_value": float(info.get("volatility_value", 0) or 0),
                "atr_value": float(info.get("atr_value", 0) or 0),
                "data_reliable": info.get("data_reliable", True),
                "orchestrator_action": "",
                "orchestrator_long_conf": 0,
                "orchestrator_mid_conf": 0,
                "orchestrator_short_conf": 0,
            }
            orch = info.get("orchestrator")
            if isinstance(orch, dict):
                sym_data["orchestrator_action"] = orch.get("action", "")
                sym_data["orchestrator_long_conf"] = float(orch.get("long_conf", 0) or 0)
                sym_data["orchestrator_mid_conf"] = float(orch.get("mid_conf", 0) or 0)
                sym_data["orchestrator_short_conf"] = float(orch.get("short_conf", 0) or 0)

            # Top 因子明细（如果存在）
            fv3 = info.get("factor_v3")
            if isinstance(fv3, dict) and fv3.get("factor_details"):
                fd = fv3["factor_details"]
                if isinstance(fd, dict):
                    sorted_factors = sorted(
                        fd.items(),
                        key=lambda x: abs(float(x[1].get("direction", 0) or 0)),
                        reverse=True,
                    )[:5]
                    sym_data["top_factors_at_open"] = [
                        {
                            "name": fname,
                            "direction": float(finfo.get("direction", 0) or 0),
                            "strength": float(finfo.get("strength", 0) or 0),
                            "category": finfo.get("category", "?"),
                        }
                        for fname, finfo in sorted_factors
                    ]

            snapshot[sym] = sym_data

        return snapshot

    # ── 平仓后：关联分析 ──

    @classmethod
    def build_attribution_report(
        cls,
        db,
        days: int = 30,
        min_trades: int = 3,
    ) -> Dict[str, Any]:
        """按开仓上下文维度做"条件→结果"关联分析。

        Args:
            db: Analytics 数据库 session
            days: 回溯天数
            min_trades: 每个维度分桶最少交易数（低于此数无统计意义）

        Returns:
            归因报告 dict
        """
        from backend.database.models import DecisionSnapshot

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            snaps = db.query(DecisionSnapshot).filter(
                DecisionSnapshot.timestamp >= cutoff,
                DecisionSnapshot.executed == True,
                DecisionSnapshot.pnl.isnot(None),
                DecisionSnapshot.action.in_(["buy", "sell"]),
            ).all()
        except Exception as e:
            logger.warning(f"[Attribution] 查询 DecisionSnapshot 失败: {e}")
            return {"error": str(e)}

        if len(snaps) < min_trades:
            return {
                "error": f"样本不足: {len(snaps)} < {min_trades}",
                "total_trades": len(snaps),
            }

        report: Dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_trades": len(snaps),
            "total_pnl": sum(s.pnl or 0 for s in snaps),
            "win_rate": len([s for s in snaps if (s.pnl or 0) > 0]) / len(snaps) if snaps else 0,
            "by_regime": {},
            "by_factor_direction": {},
            "by_factor_confidence": {},
            "by_orchestrator_alignment": {},
            "by_data_reliability": {},
            "insights": [],
        }

        # 按维度分桶
        _regime_buckets: Dict[str, list] = defaultdict(list)
        _factor_dir_buckets: Dict[str, list] = defaultdict(list)
        _factor_conf_buckets: Dict[str, list] = defaultdict(list)
        _orch_align_buckets: Dict[str, list] = defaultdict(list)
        _reliable_buckets: Dict[str, list] = defaultdict(list)

        for s in snaps:
            pnl = float(s.pnl or 0)
            mkt = s.market_snapshot_json if isinstance(s.market_snapshot_json, dict) else {}
            factor_snap = mkt.get("_factor_snapshot", {})
            sym_snap = factor_snap.get(s.symbol, {}) if isinstance(factor_snap, dict) else {}

            # 维度1: 开仓时 regime
            regime = s.regime_at_decision or sym_snap.get("market_cycle", "unknown")
            _regime_buckets[str(regime)].append(pnl)

            # 维度2: 因子方向
            f_dir = float(sym_snap.get("factor_direction", 0) or 0)
            if f_dir > 0.1:
                _factor_dir_buckets["bullish"].append(pnl)
            elif f_dir < -0.1:
                _factor_dir_buckets["bearish"].append(pnl)
            else:
                _factor_dir_buckets["neutral"].append(pnl)

            # 维度3: 因子置信度
            f_conf = float(sym_snap.get("factor_confidence", 0) or 0)
            if f_conf >= 0.6:
                _factor_conf_buckets["high(>=0.6)"].append(pnl)
            elif f_conf >= 0.3:
                _factor_conf_buckets["medium(0.3-0.6)"].append(pnl)
            else:
                _factor_conf_buckets["low(<0.3)"].append(pnl)

            # 维度4: 编排器对齐（方向与编排器一致 vs 逆向）
            orch_action = str(sym_snap.get("orchestrator_action", "")).lower()
            s_direction = "buy" if s.direction == "buy" else "sell"
            if orch_action in ("buy", "long") and s_direction == "buy":
                _orch_align_buckets["aligned"].append(pnl)
            elif orch_action in ("sell", "short") and s_direction == "sell":
                _orch_align_buckets["aligned"].append(pnl)
            elif orch_action in ("buy", "long", "sell", "short"):
                _orch_align_buckets["counter_trend"].append(pnl)
            else:
                _orch_align_buckets["orch_neutral"].append(pnl)

            # 维度5: 数据可靠性
            is_reliable = sym_snap.get("data_reliable", True)
            _reliable_buckets["reliable" if is_reliable else "unreliable"].append(pnl)

        # 填充报告
        def _bucket_stats(buckets: Dict[str, list], target: dict) -> None:
            for key, pnls in buckets.items():
                if len(pnls) < min_trades:
                    continue
                target[key] = {
                    "count": len(pnls),
                    "total_pnl": round(sum(pnls), 2),
                    "avg_pnl": round(sum(pnls) / len(pnls), 2),
                    "win_rate": round(len([p for p in pnls if p > 0]) / len(pnls), 3),
                }

        _bucket_stats(_regime_buckets, report["by_regime"])
        _bucket_stats(_factor_dir_buckets, report["by_factor_direction"])
        _bucket_stats(_factor_conf_buckets, report["by_factor_confidence"])
        _bucket_stats(_orch_align_buckets, report["by_orchestrator_alignment"])
        _bucket_stats(_reliable_buckets, report["by_data_reliability"])

        # 生成洞察
        cls._generate_insights(report)

        logger.info(
            f"[Attribution] 归因报告生成: {len(snaps)} trades, "
            f"total_pnl={report['total_pnl']:.2f}, "
            f"win_rate={report['win_rate']:.1%}"
        )
        return report

    @staticmethod
    def _generate_insights(report: dict) -> None:
        """从分桶统计中提取可读洞察。"""
        insights: list[str] = []

        # Regime 洞察
        for regime, stats in report.get("by_regime", {}).items():
            if stats.get("avg_pnl", 0) < -5 and stats.get("count", 0) >= 3:
                insights.append(
                    f"⚠️ {regime} 环境下平均亏损 ${stats['avg_pnl']:.2f} "
                    f"(共{stats['count']}笔)，建议在此 regime 下收紧开仓门槛"
                )

        # 因子方向洞察
        for fdir, stats in report.get("by_factor_direction", {}).items():
            wr = stats.get("win_rate", 0)
            if wr > 0.6:
                insights.append(
                    f"✅ 因子方向={fdir} 时胜率 {wr:.0%} "
                    f"(共{stats['count']}笔)，因子信号有效"
                )
            elif wr < 0.3 and stats.get("count", 0) >= 3:
                insights.append(
                    f"❌ 因子方向={fdir} 时胜率仅 {wr:.0%} "
                    f"(共{stats['count']}笔)，因子可能失效或被反向利用"
                )

        # 编排器对齐洞察
        aligned = report.get("by_orchestrator_alignment", {}).get("aligned", {})
        counter = report.get("by_orchestrator_alignment", {}).get("counter_trend", {})
        if aligned.get("avg_pnl", 0) > 0 and counter.get("avg_pnl", 0) < 0:
            insights.append(
                f"📊 编排器对齐交易 avg_pnl=${aligned['avg_pnl']:.2f}，"
                f"逆向 avg_pnl=${counter.get('avg_pnl', 0):.2f} — "
                f"建议禁止逆编排器开仓"
            )

        # 数据可靠性洞察
        unreliable = report.get("by_data_reliability", {}).get("unreliable", {})
        if unreliable.get("count", 0) >= 3 and unreliable.get("avg_pnl", 0) < 0:
            insights.append(
                f"🔴 data_reliable=False 时开仓平均亏损 ${unreliable['avg_pnl']:.2f} "
                f"(共{unreliable['count']}笔)，证实了阻断不可靠数据开仓的必要性"
            )

        report["insights"] = insights


# 全局单例
attribution_service = TradeAttributionService()
