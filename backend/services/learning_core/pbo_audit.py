"""
PBO-aware 血缘账本审计（整改#21）—— 对标 Bailey&López de Prado 2014 / López de Prado CSCV。

问题：演化系统持续跑 trial，若 DSR 的 N 不跨代累计则虚高乐观；champion recovery
重引入的个体算额外 trial。本模块让血缘账本"PBO-aware"：
  - 累计 trial N 跨代统计（按 lineage）。
  - 复用整改#1 的 overfitting_metrics 在线诚实重算累积 DSR / CSCV-PBO。
  - PBO>阈值 → 建议拒绝该代晋升（gated by PBO_AUDIT_ENABLED）。

零风险：
  - 纯读账本 + 纯计算，不写 DB、不改进化流程。
  - PBO_AUDIT_ENABLED 默认 false → should_reject_promotion 恒返回 False（不拦截）。
  - overfitting_metrics 缺失或数据不足时安全降级（返回 None，不抛出）。

trial/收益信息约定存放在 envelope.metrics 中（无需 DB schema 迁移）：
  metrics = {
      "sharpe": float,                 # 该配置的（OOS）Sharpe
      "returns": [float, ...],         # 可选，收益序列（DSR 精算用）
      "is_oos": bool,                  # 样本内/外
      "selection_rank": int,           # 当代选择排名
  }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return os.environ.get("PBO_AUDIT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def reject_threshold() -> float:
    try:
        return float(os.environ.get("PBO_REJECT_THRESHOLD", "0.5"))
    except ValueError:
        return 0.5


def annotate_envelope(env, *, trial_count: Optional[int] = None,
                      is_oos: Optional[bool] = None,
                      selection_rank: Optional[int] = None,
                      sharpe: Optional[float] = None,
                      returns: Optional[List[float]] = None):
    """把 PBO 相关信息写入 envelope（字段 + metrics 双写，保证持久化）。

    account 层在 ledger.record 前调用即可，纯附加、向后兼容。
    """
    try:
        if trial_count is not None:
            env.cumulative_trial_count = int(trial_count)
            env.metrics["cumulative_trial_count"] = int(trial_count)
        if is_oos is not None:
            env.is_oos = bool(is_oos)
            env.metrics["is_oos"] = bool(is_oos)
        if selection_rank is not None:
            env.selection_rank = int(selection_rank)
            env.metrics["selection_rank"] = int(selection_rank)
        if sharpe is not None:
            env.metrics["sharpe"] = float(sharpe)
        if returns is not None:
            env.metrics["returns"] = [float(x) for x in returns]
    except Exception as e:  # noqa: BLE001
        logger.debug("[PBO#21] annotate_envelope 失败: %s", e)
    return env


class PBOAuditor:
    """基于血缘账本记录的累计 trial / DSR / PBO 审计器。"""

    def __init__(self, ledger=None):
        self._ledger = ledger

    def _get_ledger(self):
        if self._ledger is not None:
            return self._ledger
        try:
            from backend.services.learning_core.ledger import ledger as _l
            self._ledger = _l
        except Exception:  # noqa: BLE001
            try:
                from .ledger import ledger as _l
                self._ledger = _l
            except Exception as e:  # noqa: BLE001
                logger.debug("[PBO#21] 无法加载 ledger: %s", e)
                self._ledger = None
        return self._ledger

    def count_trials(self, lineage_id: str) -> int:
        """该 lineage 累计 trial 数（含 recovery 重引入）。"""
        led = self._get_ledger()
        if led is None:
            return 0
        try:
            return len(led.get_lineage(lineage_id))
        except Exception:  # noqa: BLE001
            return 0

    def _collect_performance(self, lineage_ids: Optional[List[str]] = None,
                             limit: int = 500) -> List[Dict[str, Any]]:
        """从账本收集带 sharpe/returns 的记录。"""
        led = self._get_ledger()
        if led is None:
            return []
        rows: List[Dict[str, Any]] = []
        try:
            if lineage_ids:
                for lid in lineage_ids:
                    rows.extend(led.get_lineage(lid))
            else:
                rows = led.recent(limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.debug("[PBO#21] 收集绩效失败: %s", e)
            return []
        out = []
        for r in rows:
            m = r.get("metrics") or {}
            if "sharpe" in m or "returns" in m:
                out.append(m)
        return out

    def compute_cumulative_dsr(self, observed_sharpe: float,
                               lineage_ids: Optional[List[str]] = None) -> Optional[dict]:
        """累积 Deflated Sharpe Ratio：N = 账本累计 trial 总数（跨代）。"""
        try:
            from backend.services.backtest_engine.overfitting_metrics import deflated_sharpe_ratio
        except Exception as e:  # noqa: BLE001
            logger.debug("[PBO#21] overfitting_metrics 不可用: %s", e)
            return None
        perf = self._collect_performance(lineage_ids)
        n_trials = max(len(perf), 1)
        # 取最优配置的 returns 作 DSR 精算样本；无则用 sharpe 近似
        returns: List[float] = []
        for m in perf:
            rr = m.get("returns")
            if rr and len(rr) > len(returns):
                returns = list(rr)
        import numpy as np
        ret_arr = np.asarray(returns, dtype=float) if returns else np.asarray([observed_sharpe, 0.0])
        try:
            dsr, p = deflated_sharpe_ratio(observed_sharpe, n_trials, ret_arr)
        except Exception as e:  # noqa: BLE001
            logger.debug("[PBO#21] DSR 计算失败: %s", e)
            return None
        return {"dsr": dsr, "p_value": p, "n_trials": n_trials,
                "observed_sharpe": observed_sharpe}

    def compute_pbo_cscv(self, n_blocks: int = 16,
                         lineage_ids: Optional[List[str]] = None) -> Optional[dict]:
        """用账本内所有配置的 IS/OOS 收益矩阵算 CSCV-PBO。"""
        try:
            from backend.services.backtest_engine.overfitting_metrics import compute_pbo_cscv
        except Exception as e:  # noqa: BLE001
            logger.debug("[PBO#21] overfitting_metrics 不可用: %s", e)
            return None
        perf = self._collect_performance(lineage_ids)
        series = [m["returns"] for m in perf if m.get("returns") and len(m["returns"]) >= 4]
        if len(series) < 2:
            return None
        import numpy as np
        min_len = min(len(s) for s in series)
        mat = np.asarray([s[:min_len] for s in series], dtype=float)
        half = mat.shape[1] // 2
        if half < 2:
            return None
        is_r, oos_r = mat[:, :half], mat[:, half:]
        try:
            res = compute_pbo_cscv(is_r, oos_r, n_blocks=n_blocks)
        except Exception as e:  # noqa: BLE001
            logger.debug("[PBO#21] PBO 计算失败: %s", e)
            return None
        return {"pbo": res.pbo, "logit_pbo": res.logit_pbo,
                "n_combinations": res.n_combinations,
                "verdict": getattr(res, "verdict", None),
                "n_strategies": len(series)}

    def champion_overfit_audit(self, champion_lineage_id: str) -> dict:
        """审计 champion 是否 in-sample 过拟合（含 recovery 重引入的额外 trial）。"""
        trials = self.count_trials(champion_lineage_id)
        pbo = self.compute_pbo_cscv(lineage_ids=[champion_lineage_id])
        dsr = None
        perf = self._collect_performance([champion_lineage_id])
        sharpes = [m["sharpe"] for m in perf if "sharpe" in m]
        if sharpes:
            dsr = self.compute_cumulative_dsr(max(sharpes), lineage_ids=[champion_lineage_id])
        return {"lineage_id": champion_lineage_id, "cumulative_trials": trials,
                "pbo": pbo, "cumulative_dsr": dsr}

    def should_reject_promotion(self, pbo_result: Optional[dict] = None,
                                lineage_ids: Optional[List[str]] = None) -> bool:
        """PBO>阈值 → 拒绝该代晋升。开关关闭时恒 False（不拦截，零风险）。"""
        if not is_enabled():
            return False
        res = pbo_result if pbo_result is not None else self.compute_pbo_cscv(lineage_ids=lineage_ids)
        if not res or res.get("pbo") is None:
            return False
        reject = res["pbo"] > reject_threshold()
        if reject:
            logger.warning("[PBO#21] PBO=%.3f > %.2f → 拒绝该代晋升",
                           res["pbo"], reject_threshold())
        return reject


_auditor_singleton: Optional[PBOAuditor] = None


def get_auditor() -> PBOAuditor:
    global _auditor_singleton
    if _auditor_singleton is None:
        _auditor_singleton = PBOAuditor()
    return _auditor_singleton
