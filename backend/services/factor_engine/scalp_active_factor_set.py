"""ScalpActiveFactorSet — 短线活跃因子集 + 动态权重 + 衰减退役（阶段二 2.3 / 2.4）。

定位
====
把"通过回测打分闸门（A/B 级）的发现因子"收敛成一个可查询、可衰减复检的**短线
活跃因子集**，并对接已有的交易反馈 IC 动态权重（`factor_ic_evaluator` 产出的
`data/factor_runtime_weights.json`）。

- 活跃集来源：`custom_factor_store` 中 `status='active'` 的公式因子。它们已经被
  `FactorEngine._load_active_custom_factors()` 挂进 `FACTORS`，因此天然进入
  `compute_all_factors` → 短线因子合成，并随 IC 权重回写自动获得动态权重。
- 衰减退役（2.4）：定期对活跃因子重跑单因子样本外回测，IC 衰减到阈值以下的
  自动降级（active → candidate/rejected）并从实时 FACTORS 摘除，形成闭环。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# IC 退役阈值：重检后 |IC| 低于此值 → 退役；低于降权阈值 → 降级为候选。
_RETIRE_ABS_IC = float(os.getenv("SCALP_ACTIVE_RETIRE_ABS_IC", "0.015"))


def _is_scalp(rec: Dict[str, Any]) -> bool:
    """非 midlong 标签的（含未标记）都归短线，避免与中长线因子集混淆。"""
    return str((rec.get("extra") or {}).get("horizon") or "scalp").lower() != "midlong"


class ScalpActiveFactorSet:
    """短线活跃因子集管理（单例）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 查询 ──
    def get_active_factors(self) -> List[Dict[str, Any]]:
        """返回活跃因子 + 其运行时 IC 动态权重。"""
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
        except Exception:
            return []
        active = [r for r in custom_factor_store.list_active() if _is_scalp(r)]
        weights = self._runtime_weights()
        for rec in active:
            rec["runtime_weight"] = weights.get(rec["factor_id"], 1.0)
        return active

    @staticmethod
    def _runtime_weights() -> Dict[str, float]:
        try:
            from backend.services.factor_ic_evaluator import load_runtime_factor_weights
            return load_runtime_factor_weights() or {}
        except Exception:
            # 直接读文件兜底
            try:
                import json
                path = os.path.join("data", "factor_runtime_weights.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        return {str(k): float(v) for k, v in json.load(f).items()}
            except Exception:
                pass
            return {}

    # ── 衰减复检（2.4）──
    def recheck_and_prune(self) -> Dict[str, Any]:
        """对活跃因子重跑单因子回测，IC 衰减到阈值以下的自动退役/降级。"""
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
            from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
        except Exception as e:
            return {"checked": 0, "retired": 0, "error": str(e)}

        active = [r for r in custom_factor_store.list_active() if _is_scalp(r)]
        checked = 0
        retired = 0
        reduced = 0
        for rec in active:
            fid = rec.get("factor_id")
            formula = rec.get("formula")
            if not fid or not formula:
                continue
            try:
                sr = factor_backtest_scorer.score_formula(fid, formula)
                checked += 1
                # 记录 IC 进衰减监控（供趋势判断）
                try:
                    from backend.services.factor_engine.factor_decay_monitor import decay_monitor
                    decay_monitor.record_ic(fid, sr.ic_mean)
                except Exception:
                    pass

                abs_ic = abs(sr.ic_mean)
                if abs_ic < _RETIRE_ABS_IC or sr.grade in ("D", "F"):
                    # 退役：从实时 FACTORS 摘除 + 目录标记 rejected
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="rejected",
                    )
                    self._detach_from_engine(fid)
                    retired += 1
                    logger.info(f"[ActiveFactorSet] 退役衰减因子 {fid} (|IC|={abs_ic:.3f} grade={sr.grade})")
                elif sr.grade == "C":
                    # 降级为候选（暂不参与实时，等下次闸门复议）
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="candidate",
                    )
                    self._detach_from_engine(fid)
                    reduced += 1
                    logger.info(f"[ActiveFactorSet] 降级因子 {fid} (grade=C)")
                else:
                    # 仍达标：更新分数保持 active
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="active",
                    )
            except Exception as e:
                logger.debug(f"[ActiveFactorSet] 复检 {fid} 跳过: {e}")

        return {"checked": checked, "retired": retired, "reduced": reduced}

    @staticmethod
    def _scores_dict(sr) -> Dict[str, Any]:
        return {
            "ic_mean": sr.ic_mean, "icir": sr.icir,
            "ic_decay_halflife": sr.ic_decay_halflife,
            "oos_net_return": sr.oos_net_return, "oos_sharpe": sr.oos_sharpe,
            "oos_win_rate": sr.oos_win_rate, "oos_trades": sr.oos_trades,
        }

    @staticmethod
    def _detach_from_engine(factor_id: str) -> None:
        """把退役/降级的公式因子从运行中的 FACTORS 摘除。"""
        try:
            from backend.services.factor_engine.base_factors import factor_engine
            factor_engine.FACTORS.pop(factor_id, None)
        except Exception:
            pass

    # ── 可观测性快照 ──
    def get_health_snapshot(self) -> Dict[str, Any]:
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
        except Exception:
            return {"active": 0, "candidate": 0, "rejected": 0}
        active = [r for r in custom_factor_store.list_active() if _is_scalp(r)]
        weights = self._runtime_weights()
        ics = [r.get("scores", {}).get("ic_mean") for r in active if r.get("scores")]
        ics = [x for x in ics if isinstance(x, (int, float))]
        return {
            "active": len(active),
            "candidate": len([r for r in custom_factor_store.list_candidates() if _is_scalp(r)]),
            "rejected": len([r for r in custom_factor_store.list(status="rejected") if _is_scalp(r)]),
            "avg_active_ic": round(sum(ics) / len(ics), 4) if ics else None,
            "top_active": sorted(
                [
                    {
                        "factor_id": r["factor_id"],
                        "grade": r.get("grade"),
                        "ic_mean": r.get("scores", {}).get("ic_mean"),
                        "runtime_weight": weights.get(r["factor_id"], 1.0),
                    }
                    for r in active
                ],
                key=lambda x: abs(x.get("ic_mean") or 0),
                reverse=True,
            )[:10],
        }


# 全局单例
scalp_active_factor_set = ScalpActiveFactorSet()
