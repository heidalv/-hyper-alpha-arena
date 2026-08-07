"""回测驱动优化闭环 BacktestLoop（方案需求 6）

把"回测结果"作为统一账本事件，驱动三件事：
  1. 记为 EVOLVE 阶段血缘（无论来源是模板 GA 还是信号回测，统一进 evolution_lineage）；
  2. flag 门控下调用 AutoOptimizer，用回测指标自动优化参数；
  3. 把回测/交易结果转成 RL 转移样本写入 ReplayBuffer，喂给 RL agent 训练。

这样"信号回测 + 策略进化回测"在血缘账本层归一（BacktestRun 仍是关系库主账本，
本闭环负责跨来源统一 + 驱动下游优化，避免侵入既有回测 DB 结构）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .envelope import EvolutionEnvelope, STAGE_EVOLVE, STAGE_LEARN, STATUS_PASSED, STATUS_REJECTED
from .ledger import ledger
from . import flags
from .rl_core.replay_buffer import replay_buffer

logger = logging.getLogger(__name__)


class BacktestLoop:
    """回测闭环编排（单例 backtest_loop）。"""

    def ingest_result(
        self,
        *,
        source: str,
        symbol: Optional[str],
        metrics: Dict[str, Any],
        template_id: Optional[str] = None,
        run_id: Optional[str] = None,
        lineage_id: Optional[str] = None,
        trades: Optional[List[Dict[str, Any]]] = None,
        drive_optimization: bool = True,
    ) -> Dict[str, Any]:
        """吸收一次回测结果，返回处理摘要。

        Args:
            source: 回测来源（strategy_evolver | signal_backtest | live_pipeline ...）
            metrics: {sharpe, win_rate, max_dd, profit_factor, total_trades, total_return ...}
            trades: 可选逐笔（用于生成 RL 转移样本）
        """
        summary: Dict[str, Any] = {"recorded": False, "optimized": False, "replay_added": 0}

        # 1) 统一记为 evolve 血缘节点
        try:
            passed = self._is_good(metrics)
            env = EvolutionEnvelope.root(
                stage=STAGE_EVOLVE,
                source=source,
                symbol=symbol,
                payload={"template_id": template_id, "run_id": run_id},
                metrics=metrics,
                status=STATUS_PASSED if passed else STATUS_REJECTED,
                lineage_id=lineage_id,
            )
            ledger.record(env)
            summary["recorded"] = True
            summary["lineage_id"] = env.lineage_id
        except Exception as exc:
            logger.debug("[BacktestLoop] 记录血缘失败: %s", exc)
            env = None

        # 2) flag 门控：回测结果驱动参数优化
        if drive_optimization and template_id:
            summary["optimized"] = self._drive_optimization(template_id, metrics)

        # 3) 回测逐笔 → RL 转移样本
        if trades:
            summary["replay_added"] = self._seed_replay(
                symbol=symbol, source=source, trades=trades,
                lineage_id=env.lineage_id if env else lineage_id,
            )

        # 4) learn 阶段：把优化/回放结果回灌血缘
        if env is not None:
            try:
                learn = env.child(
                    stage=STAGE_LEARN,
                    source="backtest_loop",
                    payload={
                        "optimized": summary["optimized"],
                        "replay_added": summary["replay_added"],
                    },
                    status=STATUS_PASSED,
                )
                ledger.record(learn)
            except Exception:
                pass

        return summary

    @staticmethod
    def _is_good(metrics: Dict[str, Any]) -> bool:
        try:
            sharpe = float(metrics.get("sharpe") or metrics.get("sharpe_ratio") or 0)
            wr = float(metrics.get("win_rate") or 0)
            return sharpe >= 0.8 and wr >= 0.45
        except Exception:
            return False

    def _drive_optimization(self, template_id: str, metrics: Dict[str, Any]) -> bool:
        """回测指标驱动 AutoOptimizer 参数优化（默认由 HYPOTHESIS_AUTO_EVOLVE 同类护栏门控）。"""
        if not flags.get_flag("LEARNING_CORE_ENABLED"):
            return False
        try:
            from backend.services.auto_optimizer import AutoOptimizer
            optimizer = AutoOptimizer()
            fn = getattr(optimizer, "queue_optimization", None)
            if callable(fn):
                fn(template_id=template_id, reason="backtest_loop", context={"metrics": metrics})
                return True
        except Exception as exc:
            logger.debug("[BacktestLoop] 优化驱动跳过: %s", exc)
        return False

    def _seed_replay(
        self,
        *,
        symbol: Optional[str],
        source: str,
        trades: List[Dict[str, Any]],
        lineage_id: Optional[str],
    ) -> int:
        """把逐笔交易转成 RL 转移样本（粗粒度：每笔平仓一个 transition）。

        action 语义：1 open_long / 2 open_short / 3 close；reward = 该笔已实现收益率。
        state 采用交易入场时的特征快照（若无则空），供离线 RL 冷启动。
        """
        transitions: List[Dict[str, Any]] = []
        for t in trades:
            try:
                side = str(t.get("side", "")).lower()
                action = 1 if side in ("buy", "long") else (2 if side in ("sell", "short") else 3)
                reward = float(
                    t.get("pnl_pct")
                    or t.get("return")
                    or t.get("pnl")
                    or 0.0
                )
                transitions.append({
                    "symbol": symbol or t.get("symbol"),
                    "source": source if source in ("live", "paper") else "backtest",
                    "state": t.get("features") or t.get("state") or {},
                    "action": action,
                    "reward": reward,
                    "next_state": None,
                    "done": True,
                    "lineage_id": lineage_id,
                })
            except Exception:
                continue
        if not transitions:
            return 0
        return replay_buffer.add_batch(transitions)


# 单例
backtest_loop = BacktestLoop()
