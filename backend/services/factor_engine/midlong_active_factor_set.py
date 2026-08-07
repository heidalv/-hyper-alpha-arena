"""MidLongActiveFactorSet — 中长线活跃因子集 + 时间框架样本外复检退役（S4 基座）。

定位（与 ScalpActiveFactorSet 对称，泛化到 4h/1d）
================================================
把"通过 4h/1d 样本外回测打分闸门（A/B 级）的发现因子"收敛成一个**中长线活跃因子集**，
与短线因子集在 `custom_factor_store` 中通过 `extra.horizon` 标签隔离：

- 中长线因子登记时打标 `extra={"horizon": "midlong", "timeframe": "4h"|"1d"}`。
- 本集合只管理 `horizon=="midlong"` 的 active 因子；短线集合只管非 midlong 的。
- 复检退役：定期在各自时间框架(4h/1d)重跑单因子样本外回测，IC 衰减到阈值以下的
  自动降级/退役，并从实时 FACTORS 摘除，形成闭环。

注入决策
========
`build_snapshot(symbol)` 用 `FactorService.compute` 在 4h/1d 上算出活跃因子当前值，
供中长线独立循环把因子读数注入 `market_data`（MLTO / SwingAgent / TrendAgent 参考）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 中长线 IC 退役阈值（时间框架更长、样本更少 → 门槛略低于短线）
_RETIRE_ABS_IC = float(os.getenv("MIDLONG_ACTIVE_RETIRE_ABS_IC", "0.012"))
_HORIZON = "midlong"


def _is_midlong(rec: Dict[str, Any]) -> bool:
    return str((rec.get("extra") or {}).get("horizon") or "scalp").lower() == _HORIZON


class MidLongActiveFactorSet:
    """中长线活跃因子集管理（单例）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 查询 ──
    def get_active_factors(self) -> List[Dict[str, Any]]:
        """返回中长线活跃因子 + 运行时 IC 动态权重。"""
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
        except Exception:
            return []
        active = [r for r in custom_factor_store.list_active() if _is_midlong(r)]
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
            try:
                import json
                path = os.path.join("data", "factor_runtime_weights.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        return {str(k): float(v) for k, v in json.load(f).items()}
            except Exception:
                pass
            return {}

    # ── 衰减复检退役 ──
    def recheck_and_prune(self) -> Dict[str, Any]:
        """对中长线活跃因子在各自时间框架重跑样本外回测，衰减者退役/降级。"""
        if not bool(self._cfg("MIDLONG_FACTOR_RESEARCH_ENABLED", True)):
            return {"checked": 0, "retired": 0, "reduced": 0, "skipped": "disabled"}
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
            from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
        except Exception as e:
            return {"checked": 0, "retired": 0, "error": str(e)}

        active = [r for r in custom_factor_store.list_active() if _is_midlong(r)]
        checked = retired = reduced = 0
        for rec in active:
            fid = rec.get("factor_id")
            formula = rec.get("formula")
            if not fid or not formula:
                continue
            tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
            try:
                sr = factor_backtest_scorer.score_formula(
                    fid, formula,
                    interval=tf,
                    lookback=int(self._cfg("FACTOR_SCORER_MIDLONG_LOOKBACK", 900)),
                    fwd=int(self._cfg("FACTOR_SCORER_MIDLONG_FWD_1D", 3)) if tf == "1d"
                        else int(self._cfg("FACTOR_SCORER_MIDLONG_FWD_4H", 6)),
                    min_sharpe=float(self._cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4)),
                    redundancy_pool=[r for r in active if r.get("factor_id") != fid],
                )
                checked += 1
                try:
                    from backend.services.factor_engine.factor_decay_monitor import decay_monitor
                    decay_monitor.record_ic(fid, sr.ic_mean)
                except Exception:
                    pass

                abs_ic = abs(sr.ic_mean)
                if abs_ic < _RETIRE_ABS_IC or sr.grade in ("D", "F"):
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="rejected",
                    )
                    self._detach_from_engine(fid)
                    retired += 1
                    logger.info(f"[MidLongFactorSet] 退役衰减因子 {fid} tf={tf} (|IC|={abs_ic:.3f} grade={sr.grade})")
                elif sr.grade == "C":
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="candidate",
                    )
                    self._detach_from_engine(fid)
                    reduced += 1
                    logger.info(f"[MidLongFactorSet] 降级因子 {fid} tf={tf} (grade=C)")
                else:
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="active",
                    )
            except Exception as e:
                logger.debug(f"[MidLongFactorSet] 复检 {fid} 跳过: {e}")

        return {"checked": checked, "retired": retired, "reduced": reduced}

    # ── 因子读数快照（注入中长线决策）──
    def build_snapshot(self, symbol: str) -> Dict[str, Any]:
        """在 4h/1d 上算出活跃中长线因子的当前值，供注入 market_data。

        Returns:
            {"4h": {factor_id: value, ...}, "1d": {...}, "count": n}
        """
        out: Dict[str, Any] = {"4h": {}, "1d": {}, "count": 0}
        if not bool(self._cfg("MIDLONG_FACTOR_RESEARCH_ENABLED", True)):
            return out
        active = self.get_active_factors()
        if not active:
            return out
        try:
            from backend.services.factor_engine.factor_service import factor_service
        except Exception as e:
            logger.debug(f"[MidLongFactorSet] factor_service 不可用: {e}")
            return out

        by_tf: Dict[str, List[str]] = {"4h": [], "1d": []}
        for rec in active:
            tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
            if tf in by_tf:
                by_tf[tf].append(rec["factor_id"])
        n = 0
        for tf, fids in by_tf.items():
            if not fids:
                continue
            try:
                fv = factor_service.compute(symbol, timeframe=tf, factor_ids=fids)
                if isinstance(fv, dict):
                    out[tf] = {k: v for k, v in fv.items() if k in fids}
                    n += len(out[tf])
            except Exception as e:
                logger.debug(f"[MidLongFactorSet] {symbol} {tf} compute 跳过: {e}")
        out["count"] = n
        return out

    @staticmethod
    def _cfg(name: str, default):
        from backend.config import settings as _s
        return getattr(_s, name, default)

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
        all_active = custom_factor_store.list_active()
        active = [r for r in all_active if _is_midlong(r)]
        candidates = [r for r in custom_factor_store.list_candidates() if _is_midlong(r)]
        rejected = [r for r in custom_factor_store.list(status="rejected") if _is_midlong(r)]
        weights = self._runtime_weights()
        ics = [r.get("scores", {}).get("ic_mean") for r in active if r.get("scores")]
        ics = [x for x in ics if isinstance(x, (int, float))]
        return {
            "active": len(active),
            "candidate": len(candidates),
            "rejected": len(rejected),
            "avg_active_ic": round(sum(ics) / len(ics), 4) if ics else None,
            "by_timeframe": {
                tf: len([r for r in active if str((r.get("extra") or {}).get("timeframe") or "4h").lower() == tf])
                for tf in ("4h", "1d")
            },
            "top_active": sorted(
                [
                    {
                        "factor_id": r["factor_id"],
                        "grade": r.get("grade"),
                        "timeframe": (r.get("extra") or {}).get("timeframe"),
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
midlong_active_factor_set = MidLongActiveFactorSet()
