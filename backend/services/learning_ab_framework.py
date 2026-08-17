"""
A/B 学习对照框架 — P3.3
对策略/因子/参数变体进行 A/B 对照实验，量化每次学习改进的效果。

核心机制：
1. **影子账户**：在 paper trading 层面创建影子子账户，real vs shadow 对照
2. **对照维度**：策略参数、因子集、AI prompt、风控参数
3. **统计检验**：配对 t 检验判定是否有显著改进
4. **自动归档**：通过检验的 variant → 自动晋升为新的 default

加密适配：
- 随机对照期延长（加密波动大需更长时间验证）
- 费率差异控制（确保两组在相同的 funding rate 环境下对比）
"""

from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

EXPERIMENT_STATE_FILE = "data/ab_experiments_state.json"


class LearningABFramework:
    """A/B 学习对照框架"""

    _instance: Optional["LearningABFramework"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._active_experiments: Dict[str, Dict[str, Any]] = {}
        self._completed_experiments: List[Dict[str, Any]] = []
        self._load_state()
        logger.info("[ABFramework] A/B 学习对照框架初始化完成")

    @classmethod
    def get_instance(cls) -> "LearningABFramework":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_AB_FRAMEWORK_ENABLED
            return bool(AI_AB_FRAMEWORK_ENABLED)
        except Exception:
            return False

    def _load_state(self):
        try:
            import os
            if os.path.isfile(EXPERIMENT_STATE_FILE):
                with open(EXPERIMENT_STATE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    self._active_experiments = data.get("active", {})
                    self._completed_experiments = data.get("completed", [])
        except Exception:
            self._active_experiments = {}
            self._completed_experiments = []

    def _save_state(self):
        try:
            import os
            os.makedirs("data", exist_ok=True)
            with open(EXPERIMENT_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "active": self._active_experiments,
                        "completed": self._completed_experiments[-50:],
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as exc:
            logger.debug(f"[ABFramework] 状态保存失败: {exc}")

    def create_experiment(
        self,
        *,
        name: str,
        control_config: Dict[str, Any],      # 对照组（当前配置）
        treatment_config: Dict[str, Any],    # 实验组（新配置）
        symbol: str = "BTC",
        min_trades: int = 20,                # 最少交易笔数
        max_duration_hours: int = 48,        # 最大运行时间
        significance_level: float = 0.05,    # 显著性水平
    ) -> Dict[str, Any]:
        """
        创建一个 A/B 对照实验。

        Returns:
            {
                "experiment_id": str,
                "status": "created",
                "control": {...},
                "treatment": {...},
            }
        """
        if not self._is_enabled():
            return {"skipped": "AI_AB_FRAMEWORK_ENABLED=false"}

        import uuid
        exp_id = f"ab_{name}_{uuid.uuid4().hex[:8]}"

        experiment = {
            "experiment_id": exp_id,
            "name": name,
            "symbol": symbol,
            "control_config": control_config,
            "treatment_config": treatment_config,
            "min_trades": min_trades,
            "max_duration_hours": max_duration_hours,
            "significance_level": significance_level,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            # 对照数据收集
            "control_trades": [],
            "treatment_trades": [],
            "control_pnl_series": [],
            "treatment_pnl_series": [],
        }

        self._active_experiments[exp_id] = experiment
        self._save_state()

        logger.info(f"[ABFramework] 创建实验: {exp_id} ({name}) min_trades={min_trades}")
        return experiment

    def record_trade(
        self,
        experiment_id: str,
        group: str,  # "control" or "treatment"
        pnl: float,
        *,
        entry_price: float = 0,
        exit_price: float = 0,
        direction: str = "",
        duration_minutes: float = 0,
    ) -> Dict[str, Any]:
        """
        记录一笔交易到实验中。

        当任一组达到 min_trades 时自动触发统计分析。
        """
        exp = self._active_experiments.get(experiment_id)
        if not exp:
            return {"error": f"实验不存在: {experiment_id}"}

        trade = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "direction": direction,
            "duration_minutes": duration_minutes,
        }

        if group == "control":
            exp["control_trades"].append(trade)
            exp["control_pnl_series"].append(pnl)
        else:
            exp["treatment_trades"].append(trade)
            exp["treatment_pnl_series"].append(pnl)

        # 检查是否需要统计检验
        c_count = len(exp["control_trades"])
        t_count = len(exp["treatment_trades"])

        if c_count >= exp["min_trades"] and t_count >= exp["min_trades"]:
            return self._analyze_experiment(experiment_id)

        return {
            "experiment_id": experiment_id,
            "status": "collecting",
            "control_trades": c_count,
            "treatment_trades": t_count,
        }

    def _analyze_experiment(self, experiment_id: str) -> Dict[str, Any]:
        """
        对实验进行统计分析。

        检验内容：
        1. 独立样本 Welch t 检验（PNL均值差异）
        2. Sharpe 差异
        3. Win rate 差异
        4. Max drawdown 差异
        5. 综合评分

        通过条件：t检验 p < significance_level 且 treatment 均值 > control 均值
        """
        exp = self._active_experiments.get(experiment_id)
        if not exp:
            return {"error": f"实验不存在: {experiment_id}"}

        c_pnl = np.array(exp["control_pnl_series"], dtype=float)
        t_pnl = np.array(exp["treatment_pnl_series"], dtype=float)

        # 对齐长度
        min_len = min(len(c_pnl), len(t_pnl))
        c_pnl = c_pnl[:min_len]
        t_pnl = t_pnl[:min_len]

        if min_len < 5:
            return {"status": "insufficient_data"}

        from scipy import stats

        result = {
            "experiment_id": experiment_id,
            "name": exp["name"],
            "symbol": exp["symbol"],
            "samples": min_len,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. [P2-6] 独立样本 Welch t 检验。
        # 原 ttest_rel 按索引强行配对——两臂是独立时序样本（非同一时点配对交易），
        # 配对检验假设不成立会把随机噪声判为显著差异。Welch 不假设方差齐性，更稳健。
        t_stat, p_value = stats.ttest_ind(c_pnl, t_pnl, equal_var=False)
        result["ttest_statistic"] = round(float(t_stat), 4)
        result["ttest_pvalue"] = round(float(p_value), 4)

        # 2. 均值差异
        c_mean = float(np.mean(c_pnl))
        t_mean = float(np.mean(t_pnl))
        result["control_mean_pnl"] = round(c_mean, 4)
        result["treatment_mean_pnl"] = round(t_mean, 4)
        result["pnl_improvement"] = round(t_mean - c_mean, 4)
        result["pnl_improvement_pct"] = round(
            (t_mean - c_mean) / max(abs(c_mean), 0.01) * 100, 1
        )

        # 3. Sharpe
        def _calc_sharpe(r):
            if len(r) < 2 or np.std(r) < 1e-10:
                return 0.0
            return float(np.mean(r) / np.std(r))

        c_sharpe = _calc_sharpe(c_pnl)
        t_sharpe = _calc_sharpe(t_pnl)
        result["control_sharpe"] = round(c_sharpe, 4)
        result["treatment_sharpe"] = round(t_sharpe, 4)

        # 4. Win rate
        c_win = sum(1 for p in c_pnl if p > 0) / max(min_len, 1)
        t_win = sum(1 for p in t_pnl if p > 0) / max(min_len, 1)
        result["control_win_rate"] = round(c_win, 4)
        result["treatment_win_rate"] = round(t_win, 4)

        # 5. Max drawdown
        def _calc_mdd(r):
            cum = np.cumsum(r)
            peak = np.maximum.accumulate(cum)
            dd = cum - peak
            return float(abs(min(dd))) if len(dd) > 0 else 0

        result["control_max_dd"] = round(_calc_mdd(c_pnl), 4)
        result["treatment_max_dd"] = round(_calc_mdd(t_pnl), 4)

        # 6. 综合判定
        passed = (
            p_value < exp["significance_level"]
            and t_mean > c_mean
            and t_sharpe > c_sharpe * 0.8
        )

        result["passed"] = passed
        result["verdict"] = self._make_verdict(passed, result)

        # 通过 → 自动归档 + 晋升
        if passed:
            exp["status"] = "completed_passed"
            result["action"] = "treatment_promoted"
            self._promote_treatment(exp)
        else:
            exp["status"] = "completed_rejected"
            result["action"] = "treatment_rejected"

        # 移动到完成队列
        self._completed_experiments.append({**exp, "result": result})
        self._active_experiments.pop(experiment_id, None)
        self._save_state()

        logger.info(
            f"[ABFramework] 实验完成 {experiment_id}: "
            f"passed={passed} p={p_value:.4f} "
            f"ΔPNL={result['pnl_improvement']:.4f}"
        )
        return result

    def _promote_treatment(self, exp: Dict[str, Any]):
        """将 treatment 配置晋升为新的默认配置"""
        try:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                from backend.database.models import StrategyMemory
                memory = (
                    db.query(StrategyMemory)
                    .filter(StrategyMemory.strategy_id == exp["name"])
                    .first()
                )
                if memory:
                    lessons = list(memory.key_lessons or [])
                    lessons.append({
                        "type": "ab_experiment_promotion",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "experiment_id": exp["experiment_id"],
                        "new_config": exp["treatment_config"],
                        "old_config": exp["control_config"],
                    })
                    memory.key_lessons = lessons[-30:]
                    db.commit()
                    logger.info(f"[ABFramework] treatment晋升已写入策略记忆: {exp['name']}")
            finally:
                db.close()
        except Exception as exc:
            logger.debug(f"[ABFramework] 晋升写入失败: {exc}")

    @staticmethod
    def _make_verdict(passed: bool, result: Dict[str, Any]) -> str:
        if passed:
            delta = result.get("pnl_improvement", 0)
            return (
                f"✅ A/B通过: treatment显著优于control "
                f"(ΔPNL={delta:.4f}, p={result['ttest_pvalue']:.4f})"
            )
        delta = result.get("pnl_improvement", 0)
        if delta > 0 and result.get("ttest_pvalue", 1) > 0.05:
            return f"⚠️ 趋势正向但未达显著性 (ΔPNL={delta:.4f}, p={result['ttest_pvalue']:.4f})"
        return f"❌ 未通过: treatment不及control或差异不显著"

    def check_timeout_experiments(self):
        """检查过期实验，自动关闭超时实验"""
        now = datetime.now(timezone.utc)
        timeout_ids = []
        for exp_id, exp in list(self._active_experiments.items()):
            created = exp.get("created_at", "")
            if created:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    hours = (now - created_dt).total_seconds() / 3600
                    if hours > exp["max_duration_hours"]:
                        timeout_ids.append(exp_id)
                except Exception:
                    pass

        for exp_id in timeout_ids:
            exp = self._active_experiments.pop(exp_id)
            exp["status"] = "timeout"
            exp["closed_at"] = now.isoformat()
            self._completed_experiments.append(exp)
            self._save_state()
            logger.info(f"[ABFramework] 实验超时自动关闭: {exp_id}")

        return {"timeout_closed": len(timeout_ids)}

    def get_active_experiments(self) -> List[Dict[str, Any]]:
        """获取当前活跃实验列表"""
        result = []
        for exp_id, exp in self._active_experiments.items():
            result.append({
                "experiment_id": exp_id,
                "name": exp["name"],
                "symbol": exp["symbol"],
                "status": exp["status"],
                "control_trades": len(exp.get("control_trades", [])),
                "treatment_trades": len(exp.get("treatment_trades", [])),
                "created_at": exp.get("created_at", ""),
            })
        return result

    def get_status(self) -> Dict[str, Any]:
        return {
            "active_count": len(self._active_experiments),
            "completed_count": len(self._completed_experiments),
            "active_experiments": self.get_active_experiments(),
            "recent_results": [
                {
                    "experiment_id": e.get("experiment_id", ""),
                    "name": e.get("name", ""),
                    "passed": e.get("result", {}).get("passed", False),
                    "pnl_improvement": e.get("result", {}).get("pnl_improvement", 0),
                }
                for e in self._completed_experiments[-5:]
            ],
        }


# 全局单例
learning_ab_framework = LearningABFramework.get_instance()
