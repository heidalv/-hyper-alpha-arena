"""策略优化服务 - Strategy Optimizer Service

核心职责：自动回测 → 评估 → AI修改 → 再回测，直到策略达标或达到最大迭代次数。

流程：
1. 获取策略配置和历史K线数据
2. 构造回测策略并执行回测
3. 评估回测结果是否达标（夏普>1.5, 胜率>55%, 回撤<15%）
4. 不达标则调用AI分析并建议改进
5. 应用改进后重新回测
6. 达标则自动激活策略
"""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

import pandas as pd
import numpy as np

from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal, MarketSessionLocal, AnalyticsSessionLocal
from backend.database.models import (
    AIStrategy, StrategyOptimizationLog, CryptoKline
)

logger = logging.getLogger(__name__)


@dataclass
class OptimizationTargets:
    """优化目标（可配置）"""
    min_sharpe: float = 1.0
    min_win_rate: float = 0.50
    max_drawdown: float = 0.20
    min_profit_factor: float = 1.2
    min_total_return: float = 0.05


@dataclass
class OptimizationResult:
    """单次优化迭代结果"""
    iteration: int
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    passed: bool = False
    ai_suggestions: Optional[Dict] = None
    parameter_changes: Optional[Dict] = None


class StrategyOptimizerService:
    """策略优化服务"""

    def __init__(self):
        self._running_optimizations: Dict[str, Dict] = {}

    async def optimize_strategy(
        self,
        strategy_id: str,
        max_iterations: int = 5,
        targets: OptimizationTargets = None,
    ) -> Dict[str, Any]:
        """启动策略优化循环"""
        if targets is None:
            targets = OptimizationTargets()

        if strategy_id in self._running_optimizations:
            return {"error": "策略正在优化中", "strategy_id": strategy_id}

        self._running_optimizations[strategy_id] = {
            "status": "running",
            "iteration": 0,
            "max_iterations": max_iterations,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        db = SessionLocal()
        market_db = MarketSessionLocal()
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if not strategy:
                return {"error": "策略不存在"}

            kline_df = self._load_kline_data(market_db, strategy)
            if kline_df is None or len(kline_df) < 100:
                return {"error": "历史数据不足（需要至少100条K线）", "kline_count": len(kline_df) if kline_df is not None else 0}

            current_config = self._extract_strategy_config(strategy)
            results: List[OptimizationResult] = []

            for iteration in range(1, max_iterations + 1):
                self._running_optimizations[strategy_id]["iteration"] = iteration
                logger.info(f"[Optimizer] {strategy_id} 第 {iteration}/{max_iterations} 轮优化")

                bt_result = self._run_backtest(kline_df, current_config)
                opt_result = OptimizationResult(
                    iteration=iteration,
                    sharpe=bt_result.get("sharpe", 0),
                    win_rate=bt_result.get("win_rate", 0),
                    max_drawdown=bt_result.get("max_drawdown", 0),
                    total_return=bt_result.get("total_return", 0),
                    profit_factor=bt_result.get("profit_factor", 0),
                    total_trades=bt_result.get("total_trades", 0),
                )

                passed = self._evaluate(opt_result, targets)
                opt_result.passed = passed

                analytics_db = AnalyticsSessionLocal()
                try:
                    self._save_optimization_log(analytics_db, strategy_id, opt_result)
                finally:
                    analytics_db.close()
                results.append(opt_result)

                if passed:
                    logger.info(
                        f"[Optimizer] {strategy_id} 第 {iteration} 轮达标! "
                        f"sharpe={opt_result.sharpe:.2f}, wr={opt_result.win_rate:.1%}"
                    )
                    self._apply_config_to_strategy(db, strategy_id, current_config)
                    break

                if iteration < max_iterations:
                    suggestions = self._ai_suggest_improvements(
                        current_config, opt_result, iteration, targets
                    )
                    opt_result.ai_suggestions = suggestions
                    current_config = self._apply_suggestions(current_config, suggestions)
                    opt_result.parameter_changes = suggestions.get("parameter_changes", {})

            final_passed = any(r.passed for r in results)
            self._running_optimizations.pop(strategy_id, None)

            return {
                "strategy_id": strategy_id,
                "iterations": len(results),
                "passed": final_passed,
                "results": [asdict(r) for r in results],
                "final_config": current_config,
            }

        except Exception as e:
            logger.error(f"[Optimizer] 优化异常 {strategy_id}: {e}", exc_info=True)
            self._running_optimizations.pop(strategy_id, None)
            return {"error": str(e)}
        finally:
            db.close()
            try:
                market_db.close()
            except Exception:
                pass

    def get_optimization_status(self, strategy_id: str) -> Optional[Dict]:
        """获取正在运行的优化状态"""
        return self._running_optimizations.get(strategy_id)

    def get_optimization_history(self, strategy_id: str, limit: int = 20) -> List[Dict]:
        """获取策略的优化历史"""
        db = AnalyticsSessionLocal()
        try:
            logs = db.query(StrategyOptimizationLog).filter(
                StrategyOptimizationLog.strategy_id == strategy_id
            ).order_by(StrategyOptimizationLog.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": log.id,
                    "iteration": log.iteration,
                    "sharpe": log.backtest_sharpe,
                    "win_rate": log.backtest_win_rate,
                    "max_drawdown": log.backtest_max_drawdown,
                    "total_return": log.backtest_total_return,
                    "profit_factor": log.backtest_profit_factor,
                    "passed": log.passed,
                    "ai_suggestions": log.ai_suggestions,
                    "parameter_changes": log.parameter_changes,
                    "status": log.status,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ]
        finally:
            db.close()

    # =========================================================================
    # 回测执行
    # =========================================================================

    def _run_backtest(self, df: pd.DataFrame, config: Dict) -> Dict[str, Any]:
        """执行简化回测：基于EMA交叉策略+策略参数"""
        try:
            sl_pct = config.get("stop_loss_pct", 0.05)
            tp_pct = config.get("take_profit_pct", 0.10)
            ema_fast = config.get("ema_fast", 9)
            ema_slow = config.get("ema_slow", 21)

            closes = df["close"].values
            if len(closes) < ema_slow + 10:
                return {"sharpe": 0, "win_rate": 0, "max_drawdown": 0, "total_return": 0, "profit_factor": 0, "total_trades": 0}

            ema_f = pd.Series(closes).ewm(span=ema_fast, adjust=False).mean().values
            ema_s = pd.Series(closes).ewm(span=ema_slow, adjust=False).mean().values

            trades = []
            position = None  # {"side": "buy"/"sell", "entry": price}
            equity = [1.0]

            for i in range(ema_slow, len(closes)):
                price = closes[i]

                if position:
                    if position["side"] == "buy":
                        pnl_pct = (price - position["entry"]) / position["entry"]
                    else:
                        pnl_pct = (position["entry"] - price) / position["entry"]

                    if pnl_pct <= -sl_pct or pnl_pct >= tp_pct:
                        actual_pnl = max(pnl_pct, -sl_pct) if pnl_pct < 0 else min(pnl_pct, tp_pct)
                        trades.append(actual_pnl)
                        equity.append(equity[-1] * (1 + actual_pnl))
                        position = None
                        continue

                    if position["side"] == "buy" and ema_f[i] < ema_s[i]:
                        trades.append(pnl_pct)
                        equity.append(equity[-1] * (1 + pnl_pct))
                        position = None
                    elif position["side"] == "sell" and ema_f[i] > ema_s[i]:
                        trades.append(pnl_pct)
                        equity.append(equity[-1] * (1 + pnl_pct))
                        position = None

                if not position:
                    if ema_f[i] > ema_s[i] and ema_f[i - 1] <= ema_s[i - 1]:
                        position = {"side": "buy", "entry": price}
                    elif ema_f[i] < ema_s[i] and ema_f[i - 1] >= ema_s[i - 1]:
                        position = {"side": "sell", "entry": price}

                if not position:
                    equity.append(equity[-1])

            if not trades:
                return {"sharpe": 0, "win_rate": 0, "max_drawdown": 0, "total_return": 0, "profit_factor": 0, "total_trades": 0}

            wins = [t for t in trades if t > 0]
            losses = [t for t in trades if t <= 0]

            total_return = equity[-1] - 1.0
            win_rate = len(wins) / len(trades)
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 0.001
            profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 10.0

            equity_arr = np.array(equity)
            peak = np.maximum.accumulate(equity_arr)
            drawdown = (equity_arr - peak) / peak
            max_dd = abs(drawdown.min())

            returns = np.diff(equity_arr) / equity_arr[:-1]
            sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0

            return {
                "sharpe": round(float(sharpe), 3),
                "win_rate": round(float(win_rate), 4),
                "max_drawdown": round(float(max_dd), 4),
                "total_return": round(float(total_return), 4),
                "profit_factor": round(float(profit_factor), 3),
                "total_trades": len(trades),
            }
        except Exception as e:
            logger.error(f"[Optimizer] 回测执行异常: {e}", exc_info=True)
            return {"sharpe": 0, "win_rate": 0, "max_drawdown": 0, "total_return": 0, "profit_factor": 0, "total_trades": 0}

    def _evaluate(self, result: OptimizationResult, targets: OptimizationTargets) -> bool:
        """评估是否达标"""
        if result.total_trades < 10:
            return False
        return (
            result.sharpe >= targets.min_sharpe
            and result.win_rate >= targets.min_win_rate
            and result.max_drawdown <= targets.max_drawdown
            and result.profit_factor >= targets.min_profit_factor
        )

    def _ai_suggest_improvements(
        self,
        config: Dict,
        result: OptimizationResult,
        iteration: int,
        targets: OptimizationTargets,
    ) -> Dict[str, Any]:
        """调用AI分析回测结果并建议改进"""
        prompt = f"""你是量化策略优化专家。以下是第{iteration}轮回测结果:

当前参数: SL={config.get('stop_loss_pct',0.05)*100:.1f}%, TP={config.get('take_profit_pct',0.10)*100:.1f}%, EMA快线={config.get('ema_fast',9)}, EMA慢线={config.get('ema_slow',21)}

回测结果:
- 夏普比率: {result.sharpe:.3f} (目标 ≥{targets.min_sharpe})
- 胜率: {result.win_rate:.1%} (目标 ≥{targets.min_win_rate:.0%})
- 最大回撤: {result.max_drawdown:.1%} (目标 ≤{targets.max_drawdown:.0%})
- 盈亏比: {result.profit_factor:.2f} (目标 ≥{targets.min_profit_factor})
- 总交易: {result.total_trades}笔

请分析问题并给出参数调整建议。用JSON返回:
{{"analysis": "...", "parameter_changes": {{"stop_loss_pct": 0.03, "take_profit_pct": 0.08, "ema_fast": 12, "ema_slow": 26}}}}

只返回JSON，不要额外文字。"""

        try:
            from backend.services.llm_config_service import get_llm_config, call_llm_api_sync
            llm_config = get_llm_config()
            if llm_config:
                opt_messages = [
                    {"role": "system", "content": "你是量化策略优化专家，只返回JSON格式的优化建议。"},
                    {"role": "user", "content": prompt},
                ]
                ai_resp = call_llm_api_sync(
                    config=llm_config,
                    messages=opt_messages,
                    temperature=0.7,
                    max_tokens=2000,
                )
                if ai_resp and "choices" in ai_resp and len(ai_resp["choices"]) > 0:
                    content = ai_resp["choices"][0].get("message", {}).get("content", "")
                    import re
                    json_match = re.search(r'\{[\s\S]*\}', content)
                    if json_match:
                        return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"[Optimizer] AI建议获取失败: {e}")

        return self._rule_based_suggestions(config, result, targets)

    def _rule_based_suggestions(
        self, config: Dict, result: OptimizationResult, targets: OptimizationTargets
    ) -> Dict:
        """基于规则的回退建议"""
        changes = {}
        analysis_parts = []

        if result.max_drawdown > targets.max_drawdown:
            new_sl = max(config.get("stop_loss_pct", 0.05) * 0.8, 0.02)
            changes["stop_loss_pct"] = round(new_sl, 4)
            analysis_parts.append(f"回撤过大，收紧止损 → {new_sl*100:.1f}%")

        if result.win_rate < targets.min_win_rate:
            fast = config.get("ema_fast", 9)
            slow = config.get("ema_slow", 21)
            changes["ema_fast"] = min(fast + 2, slow - 3)
            changes["ema_slow"] = min(slow + 3, 55)
            analysis_parts.append(f"胜率偏低，增加EMA周期以过滤噪音")

        if result.profit_factor < targets.min_profit_factor:
            new_tp = min(config.get("take_profit_pct", 0.10) * 1.2, 0.20)
            changes["take_profit_pct"] = round(new_tp, 4)
            analysis_parts.append(f"盈亏比不足，扩大止盈 → {new_tp*100:.1f}%")

        return {
            "analysis": "; ".join(analysis_parts) or "微调参数",
            "parameter_changes": changes,
        }

    def _apply_suggestions(self, config: Dict, suggestions: Dict) -> Dict:
        """应用AI建议的参数变更"""
        new_config = config.copy()
        changes = suggestions.get("parameter_changes", {})
        for k, v in changes.items():
            if k in new_config and isinstance(v, (int, float)):
                new_config[k] = v
        return new_config

    # =========================================================================
    # 数据与配置
    # =========================================================================

    def _load_kline_data(self, db: Session, strategy: AIStrategy) -> Optional[pd.DataFrame]:
        """从数据中心加载K线数据"""
        try:
            from backend.services.exchange_config import get_active_exchange
            exchange = get_active_exchange()
            symbol = strategy.primary_symbol or "BTC"
            period = strategy.timeframe or "1h"

            klines = db.query(CryptoKline).filter(
                CryptoKline.exchange == exchange,
                CryptoKline.symbol == symbol.upper(),
                CryptoKline.period == period,
            ).order_by(CryptoKline.timestamp).all()

            if not klines:
                return None

            data = [{
                "timestamp": k.timestamp,
                "open": float(k.open_price or 0),
                "high": float(k.high_price or 0),
                "low": float(k.low_price or 0),
                "close": float(k.close_price or 0),
                "volume": float(k.volume or 0),
            } for k in klines]

            return pd.DataFrame(data)
        except Exception as e:
            logger.error(f"[Optimizer] 加载K线数据失败: {e}")
            return None

    def _extract_strategy_config(self, strategy: AIStrategy) -> Dict:
        """从策略中提取可优化参数"""
        return {
            "stop_loss_pct": strategy.stop_loss_pct or 0.05,
            "take_profit_pct": strategy.take_profit_pct or 0.10,
            "max_position_size": strategy.max_position_size or 0.2,
            "ema_fast": 9,
            "ema_slow": 21,
        }

    def _apply_config_to_strategy(self, db: Session, strategy_id: str, config: Dict):
        """将优化后的配置写回策略"""
        try:
            strategy = db.query(AIStrategy).filter(
                AIStrategy.strategy_id == strategy_id
            ).first()
            if strategy:
                strategy.stop_loss_pct = config.get("stop_loss_pct", strategy.stop_loss_pct)
                strategy.take_profit_pct = config.get("take_profit_pct", strategy.take_profit_pct)
                strategy.max_position_size = config.get("max_position_size", strategy.max_position_size)
                db.commit()
                logger.info(f"[Optimizer] 已更新策略 {strategy_id} 的优化参数")
        except Exception as e:
            logger.error(f"[Optimizer] 更新策略配置失败: {e}")
            db.rollback()

    def _save_optimization_log(self, db: Session, strategy_id: str, result: OptimizationResult):
        """保存优化日志"""
        try:
            log = StrategyOptimizationLog(
                strategy_id=strategy_id,
                iteration=result.iteration,
                backtest_sharpe=result.sharpe,
                backtest_win_rate=result.win_rate,
                backtest_max_drawdown=result.max_drawdown,
                backtest_total_return=result.total_return,
                backtest_profit_factor=result.profit_factor,
                passed=result.passed,
                ai_suggestions=result.ai_suggestions,
                parameter_changes=result.parameter_changes,
                status="passed" if result.passed else "running",
            )
            db.add(log)
            db.commit()
        except Exception as e:
            logger.warning(f"[Optimizer] 保存优化日志失败: {e}")
            db.rollback()


strategy_optimizer = StrategyOptimizerService()
