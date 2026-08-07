"""
交易智慧编译器 — 从回测进化结果中提取四类智慧，编译为提示词片段

四类智慧：
  1. 风控智慧 (risk)    — 最优止损/仓位/杠杆参数
  2. 市况智慧 (regime)  — 不同市况下的最佳策略
  3. 信号智慧 (signal)  — 技术信号的可靠度排行
  4. 教训智慧 (lesson)  — 失败模式和规避建议
"""

import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import numpy as np
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class BacktestInsightCompiler:
    """从回测进化历史中提取交易智慧并编译为提示词片段"""

    def extract_wisdom(self, db: Session, template_id: str) -> Dict[str, Any]:
        """
        从指定模板的回测历史中提取四类智慧。

        Returns:
            {
                "risk": {...},
                "regime": {...},
                "signal": {...},
                "lesson": {...},
                "meta": {"template_id", "sample_count", "extracted_at"},
            }
        """
        from backend.database.models import BacktestRun, BacktestTrade

        runs = db.query(BacktestRun).filter(
            BacktestRun.template_id == template_id,
            BacktestRun.status == "completed",
            BacktestRun.total_trades != None,
            BacktestRun.total_trades > 0,
        ).order_by(BacktestRun.created_at.desc()).limit(50).all()

        if not runs:
            logger.info(f"[WisdomCompiler] 模板 {template_id} 无回测数据")
            return self._empty_wisdom(template_id)

        champions = [r for r in runs if r.is_champion]
        all_trades = []
        for r in runs[:20]:
            trades = db.query(BacktestTrade).filter(
                BacktestTrade.run_id == r.run_id
            ).all()
            all_trades.extend(trades)

        risk_wisdom = self._extract_risk_wisdom(runs, champions)
        regime_wisdom = self._extract_regime_wisdom(runs, champions)
        signal_wisdom = self._extract_signal_wisdom(all_trades, runs)
        lesson_wisdom = self._extract_lesson_wisdom(all_trades, runs)

        return {
            "risk": risk_wisdom,
            "regime": regime_wisdom,
            "signal": signal_wisdom,
            "lesson": lesson_wisdom,
            "meta": {
                "template_id": template_id,
                "runs_analyzed": len(runs),
                "trades_analyzed": len(all_trades),
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def compile_to_prompt_fragment(
        self,
        wisdom: Dict[str, Any],
        current_regime: str = "ranging",
    ) -> str:
        """将结构化智慧编译为自然语言提示词片段"""
        parts = []

        # 风控智慧
        risk = wisdom.get("risk", {})
        if risk:
            parts.append(self._compile_risk(risk))

        # 市况智慧（突出当前市况）
        regime = wisdom.get("regime", {})
        if regime:
            parts.append(self._compile_regime(regime, current_regime))

        # 信号智慧
        signal = wisdom.get("signal", {})
        if signal:
            parts.append(self._compile_signal(signal))

        # 教训智慧
        lesson = wisdom.get("lesson", {})
        if lesson:
            parts.append(self._compile_lesson(lesson))

        if not parts:
            return ""

        header = "\n\n--- 回测进化经验参考 (自动生成，仅供参考) ---\n"
        return header + "\n".join(parts) + "\n--- 经验参考结束 ---"

    def save_wisdom_to_db(
        self, db: Session, template_id: str, wisdom: Dict[str, Any], tier: str = "mid"
    ) -> List[int]:
        """将提取的智慧保存到数据库"""
        from backend.database.models import TradingWisdom

        saved_ids = []
        for wtype in ("risk", "regime", "signal", "lesson"):
            content = wisdom.get(wtype, {})
            if not content:
                continue

            fragment = ""
            if wtype == "risk":
                fragment = self._compile_risk(content)
            elif wtype == "regime":
                fragment = self._compile_regime(content, "ranging")
            elif wtype == "signal":
                fragment = self._compile_signal(content)
            elif wtype == "lesson":
                fragment = self._compile_lesson(content)

            sample_count = wisdom.get("meta", {}).get("trades_analyzed", 0)
            confidence = min(1.0, sample_count / 500) if sample_count > 0 else 0.1

            existing = db.query(TradingWisdom).filter(
                TradingWisdom.template_id == template_id,
                TradingWisdom.wisdom_type == wtype,
            ).first()

            if existing:
                existing.content = content
                existing.prompt_fragment = fragment
                existing.confidence = confidence
                existing.sample_count = sample_count
                existing.tier = tier
                existing.is_active = True
            else:
                w = TradingWisdom(
                    template_id=template_id,
                    tier=tier,
                    wisdom_type=wtype,
                    content=content,
                    prompt_fragment=fragment,
                    confidence=confidence,
                    sample_count=sample_count,
                    is_active=True,
                )
                db.add(w)
                db.flush()
                saved_ids.append(w.id)

        db.commit()
        logger.info(f"[WisdomCompiler] 模板 {template_id} 智慧已保存/更新")
        return saved_ids

    def get_active_wisdom(
        self, db: Session, template_id: str, current_regime: str = "ranging"
    ) -> str:
        """获取当前可用的智慧提示词片段（供 AI 决策注入）"""
        from backend.database.models import TradingWisdom

        wisdoms = db.query(TradingWisdom).filter(
            TradingWisdom.template_id == template_id,
            TradingWisdom.is_active == True,
        ).all()

        if not wisdoms:
            return ""

        parts = []
        wisdom_ids = []
        for w in wisdoms:
            if w.prompt_fragment:
                effectiveness = w.effectiveness_score or 0.5
                if effectiveness < 0.2:
                    continue
                parts.append(w.prompt_fragment)
                wisdom_ids.append(w.id)

        if not parts:
            return ""

        header = "\n\n--- 回测进化经验参考 (自动生成) ---\n"
        result = header + "\n".join(parts) + "\n--- 经验参考结束 ---"

        # 附加元信息供追踪
        result += f"\n<!-- wisdom_ids:{json.dumps(wisdom_ids)} -->"
        return result

    # ══════════════════════════════════════════════════
    #  内部提取方法
    # ══════════════════════════════════════════════════

    def get_wisdom_for_strategy(
        self, db: Session, ai_strategy_id: str, current_regime: str = "ranging"
    ) -> str:
        """根据 AI 策略 ID 获取关联模板的精准智慧"""
        try:
            from backend.database.models import AIStrategy
            strat = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == ai_strategy_id
            ).first()
            if not strat:
                return ""

            genome = strat.genome or {}
            source_tpl_id = genome.get("source_template_id") if isinstance(genome, dict) else None

            if source_tpl_id:
                return self.get_active_wisdom(db, source_tpl_id, current_regime)

            return ""
        except Exception as e:
            logger.debug(f"[WisdomCompiler] 策略智慧查找失败: {e}")
            return ""

    def _extract_risk_wisdom(self, runs, champions) -> Dict:
        """从回测运行中提取最优风控参数（精确数值）"""
        good_runs = [r for r in runs if (r.sharpe_ratio or 0) > 0.5 and (r.win_rate or 0) > 0.35]
        if not good_runs:
            good_runs = runs[:10]

        sl_vals, tp_vals, pos_vals, lev_vals = [], [], [], []
        for r in good_runs:
            cfg = r.strategy_config or r.risk_params or {}
            risk = cfg.get("risk_params", cfg) if isinstance(cfg, dict) else {}
            if "stop_loss_pct" in risk:
                sl_vals.append(risk["stop_loss_pct"])
            if "take_profit_pct" in risk:
                tp_vals.append(risk["take_profit_pct"])
            if "max_position_size" in risk:
                pos_vals.append(risk["max_position_size"])
            if "default_leverage" in risk:
                lev_vals.append(risk["default_leverage"])

        result = {
            "best_sharpe": round(max((r.sharpe_ratio or 0) for r in good_runs), 2),
            "best_win_rate": round(max((r.win_rate or 0) for r in good_runs) * 100, 1),
            "sample_runs": len(good_runs),
        }

        if sl_vals:
            median_sl = float(np.median(sl_vals))
            result["optimal_stop_loss"] = f"{median_sl*100:.1f}%"
            result["optimal_stop_loss_raw"] = round(median_sl, 4)
        else:
            result["optimal_stop_loss"] = "3-5%"

        if tp_vals:
            median_tp = float(np.median(tp_vals))
            result["optimal_take_profit"] = f"{median_tp*100:.1f}%"
            result["optimal_take_profit_raw"] = round(median_tp, 4)
        else:
            result["optimal_take_profit"] = "8-12%"

        if pos_vals:
            median_pos = float(np.median(pos_vals))
            result["optimal_position_size"] = f"{median_pos*100:.0f}%"
            result["optimal_position_size_raw"] = round(median_pos, 4)
        else:
            result["optimal_position_size"] = "15-20%"

        if lev_vals:
            median_lev = float(np.median(lev_vals))
            result["optimal_leverage"] = f"{median_lev:.0f}x"
            result["optimal_leverage_raw"] = round(median_lev, 1)
        else:
            result["optimal_leverage"] = "1-2x"

        return result

    def _extract_regime_wisdom(self, runs, champions) -> Dict:
        """提取不同市况下的表现"""
        regime_stats: Dict[str, list] = {"trending": [], "ranging": [], "volatile": []}

        for r in runs:
            cfg = r.strategy_config or {}
            regime_perf = cfg.get("regime_performance", {})
            if isinstance(regime_perf, dict):
                for regime, perf in regime_perf.items():
                    if regime in regime_stats and isinstance(perf, dict):
                        regime_stats[regime].append(perf)

        result = {}
        for regime, perfs in regime_stats.items():
            if perfs:
                avg_wr = np.mean([p.get("win_rate", 0) for p in perfs])
                avg_pnl = np.mean([p.get("avg_pnl_pct", 0) for p in perfs])
                total_trades = sum(p.get("trades", 0) for p in perfs)
                result[regime] = {
                    "avg_win_rate": round(avg_wr * 100, 1),
                    "avg_pnl_pct": round(avg_pnl * 100, 2),
                    "total_trades": total_trades,
                    "recommendation": self._regime_recommendation(regime, avg_wr, avg_pnl),
                }
        return result

    def _extract_signal_wisdom(self, trades, runs) -> Dict:
        """从交易记录中提取信号可靠度"""
        if not trades:
            return {}

        side_stats = {"long": {"wins": 0, "losses": 0}, "short": {"wins": 0, "losses": 0}}
        exit_stats: Dict[str, Dict] = {}

        for t in trades:
            side = t.side or "long"
            if side in side_stats:
                if (t.pnl or 0) > 0:
                    side_stats[side]["wins"] += 1
                else:
                    side_stats[side]["losses"] += 1

            reason = t.exit_reason or "unknown"
            if reason not in exit_stats:
                exit_stats[reason] = {"count": 0, "total_pnl_pct": 0.0}
            exit_stats[reason]["count"] += 1
            exit_stats[reason]["total_pnl_pct"] += (t.pnl_pct or 0)

        long_total = side_stats["long"]["wins"] + side_stats["long"]["losses"]
        short_total = side_stats["short"]["wins"] + side_stats["short"]["losses"]

        return {
            "long_win_rate": round(side_stats["long"]["wins"] / max(long_total, 1) * 100, 1),
            "short_win_rate": round(side_stats["short"]["wins"] / max(short_total, 1) * 100, 1),
            "long_trades": long_total,
            "short_trades": short_total,
            "exit_distribution": {
                k: {"count": v["count"], "avg_pnl_pct": round(v["total_pnl_pct"] / max(v["count"], 1) * 100, 2)}
                for k, v in exit_stats.items()
            },
        }

    def _extract_lesson_wisdom(self, trades, runs) -> Dict:
        """提取失败模式和教训"""
        lessons = []

        if trades:
            loss_trades = [t for t in trades if (t.pnl or 0) < 0]
            if loss_trades:
                sl_losses = [t for t in loss_trades if t.exit_reason == "sl"]
                if len(sl_losses) > len(loss_trades) * 0.5:
                    lessons.append("止损触发占亏损交易50%以上，考虑放宽止损或优化入场时机")

                consecutive = self._max_consecutive_losses(trades)
                if consecutive >= 5:
                    lessons.append(f"出现过{consecutive}次连续亏损，建议连亏3次后暂停或减仓")

        for r in runs:
            if (r.max_drawdown or 0) > 0.20:
                lessons.append(f"最大回撤达{(r.max_drawdown or 0)*100:.0f}%，建议回撤超15%时主动减仓")
                break

        for r in runs:
            if (r.avg_holding_bars or 0) > 100:
                lessons.append("平均持仓时间过长，可能错过更好的入场机会")
                break

        if not lessons:
            lessons.append("暂无明显失败模式，继续保持当前策略纪律")

        return {"lessons": lessons[:5]}

    # ══════════════════════════════════════════════════
    #  编译方法（结构 → 自然语言）
    # ══════════════════════════════════════════════════

    def _compile_risk(self, risk: Dict) -> str:
        lines = ["[回测优化风控建议]"]
        if risk.get("optimal_stop_loss"):
            lines.append(f"  - 建议止损: {risk['optimal_stop_loss']}")
        if risk.get("optimal_take_profit"):
            lines.append(f"  - 建议止盈: {risk['optimal_take_profit']}")
        if risk.get("optimal_position_size"):
            lines.append(f"  - 建议仓位: {risk['optimal_position_size']}")
        if risk.get("optimal_leverage"):
            lines.append(f"  - 建议杠杆: {risk['optimal_leverage']}")
        if risk.get("best_win_rate"):
            lines.append(f"  - 回测最佳胜率: {risk['best_win_rate']}% (基于{risk.get('sample_runs', 0)}次回测)")
        if risk.get("best_sharpe"):
            lines.append(f"  - 回测最优夏普比率: {risk['best_sharpe']}")
        return "\n".join(lines)

    def _compile_regime(self, regime: Dict, current: str) -> str:
        lines = ["[市况经验]"]
        for r_name, r_data in regime.items():
            if not isinstance(r_data, dict):
                continue
            marker = " ← 当前" if r_name == current else ""
            rec = r_data.get("recommendation", "")
            wr = r_data.get("avg_win_rate", 0)
            lines.append(f"  - {r_name}{marker}: 胜率{wr}%, {rec}")
        if not regime:
            lines.append("  - 暂无分市况数据")
        return "\n".join(lines)

    def _compile_signal(self, signal: Dict) -> str:
        lines = ["[信号经验]"]
        if signal.get("long_win_rate") is not None:
            lines.append(f"  - 做多胜率: {signal['long_win_rate']}% ({signal.get('long_trades', 0)}笔)")
        if signal.get("short_win_rate") is not None:
            lines.append(f"  - 做空胜率: {signal['short_win_rate']}% ({signal.get('short_trades', 0)}笔)")
        exits = signal.get("exit_distribution", {})
        if exits:
            top_exits = sorted(exits.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
            for reason, data in top_exits:
                lines.append(f"  - 出场原因 {reason}: {data['count']}次, 平均盈亏{data['avg_pnl_pct']}%")
        return "\n".join(lines)

    def _compile_lesson(self, lesson: Dict) -> str:
        lines = ["[历史教训]"]
        for l in lesson.get("lessons", []):
            lines.append(f"  - {l}")
        return "\n".join(lines)

    # ══════════════════════════════════════════════════
    #  辅助方法
    # ══════════════════════════════════════════════════

    @staticmethod
    def _regime_recommendation(regime: str, win_rate: float, avg_pnl: float) -> str:
        if regime == "trending":
            if win_rate > 0.5:
                return "趋势市表现好，可适当加仓"
            return "趋势市表现一般，注意顺势操作"
        elif regime == "ranging":
            if win_rate > 0.5:
                return "震荡市表现好，适合区间操作"
            return "震荡市表现差，建议减少交易或观望"
        elif regime == "volatile":
            if win_rate > 0.45:
                return "高波动市有优势，但需严格止损"
            return "高波动市容易亏损，建议收紧止损或观望"
        return ""

    @staticmethod
    def _max_consecutive_losses(trades) -> int:
        max_cl, cl = 0, 0
        for t in trades:
            if (t.pnl or 0) <= 0:
                cl += 1
                max_cl = max(max_cl, cl)
            else:
                cl = 0
        return max_cl

    @staticmethod
    def _empty_wisdom(template_id: str) -> Dict:
        return {
            "risk": {},
            "regime": {},
            "signal": {},
            "lesson": {"lessons": ["暂无回测数据，请先运行回测进化"]},
            "meta": {
                "template_id": template_id,
                "runs_analyzed": 0,
                "trades_analyzed": 0,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            },
        }


# 单例
insight_compiler = BacktestInsightCompiler()
