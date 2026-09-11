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

import numpy as np

logger = logging.getLogger(__name__)

# 中长线 IC 退役阈值（时间框架更长、样本更少 → 门槛略低于短线）
_RETIRE_ABS_IC = float(os.getenv("MIDLONG_ACTIVE_RETIRE_ABS_IC", "0.012"))
_HORIZON = "midlong"


def _resolve_tenant_id() -> "int | None":
    """[2026-08-13 P1-9] 租户修复：custom_factor_store 按租户隔离存储，
    list_active() 不传 tenant_id 返回空列表（防误共享设计）→ 中长线因子集的
    查询/复检/退役链路从未真正运行。显式传 admin tenant_id 恢复管理闭环。"""
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


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
        """返回中长线活跃因子 + 运行时权重（[M4] FACTOR_COMBO_MODE=icir → ICIR 加权）。

        [item14 2026-08-21] AST 桥接：合并进化仓 TRADABLE 的 4h AST 因子
        （factor_active_set，短线 s5m_ 前缀/horizon=scalp 标记排除）为
        kind="ast" 记录——GP 挖出的中线弹药此前只喂短线 evo_*，中线
        活跃数永远靠公式/registry 两条旁路（M8 提门槛的前置）。
        方向以 icir 符号锁定 expected_sign；权重幅度用 |icir|（AST 仓无
        ic_mean，ICIR 是更稳的稳定性度量；与公式因子在 combo_weights 归一
        后可比）。
        """
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
        except Exception:
            return []
        active = [r for r in custom_factor_store.list_active(tenant_id=_resolve_tenant_id()) if _is_midlong(r)]
        active.extend(self._tradable_ast_bridge())
        weights = self._runtime_weights()
        try:
            from backend.services.factor_engine.combo_weights import resolve_combo_weights
            wmap = resolve_combo_weights(active, weights)
        except Exception as err:
            # [2026-09-02 消除 fail-open] 原分支静默退化为等权 1.0，权重体系出错时
            # 系统照常满权出信号。改为显式告警 + 全零（fail-closed）：下游
            # midlong_factor_route 的 weight_sum<=0 会给 no_valid_votes 跳过本轮。
            logger.error(
                "[MidLongActiveSet] 组合权重解析失败 → 本轮因子权重全零、不出信号: %s",
                err, exc_info=True,
            )
            wmap = {str(r.get("factor_id") or ""): 0.0 for r in active}
        # [M0-F1 2026-08-22] role=paper 因子（held-out 未过但 A/B 级晋升的影子因子）
        # 权重封顶 PAPER_FACTOR_WEIGHT_CAP，与短线 PAPER 因子同一上限口径，
        # 让影子因子参与投票但不主导决策。
        try:
            from backend.config.settings import PAPER_FACTOR_WEIGHT_CAP as _PWC
            _paper_cap = float(_PWC or 0.5)
        except Exception:
            _paper_cap = 0.5
        for rec in active:
            _fid = str(rec.get("factor_id") or "")
            # wmap 由 resolve_combo_weights 产出、必然覆盖全部 records，缺失即异常
            # → 用 0.0 而非 1.0（1.0 会让未定权的因子满权投票）。
            rec["runtime_weight"] = wmap.get(_fid, 0.0)
            if str((rec.get("extra") or {}).get("role") or "") == "paper":
                # 只在缺失(None)时取中性 1.0；显式 0.0 必须保持 0（避免 `or` 陷阱
                # 把归零权重还原成满权）
                _rw = rec.get("runtime_weight")
                rec["runtime_weight"] = min(
                    1.0 if _rw is None else float(_rw), _paper_cap)
        return active

    @staticmethod
    def _tradable_ast_bridge() -> List[Dict[str, Any]]:
        """[item14] 进化仓 TRADABLE AST → 中线 kind="ast" 记录（上限可配）。"""
        try:
            from backend.services.factor_engine.active_set_policy import (
                ActiveSetRole,
                load_factor_active_rows,
            )
            rows = load_factor_active_rows(ActiveSetRole.TRADABLE, parse_expr=True, limit=50)
        except Exception:
            return []
        try:
            from backend.config.settings import MIDLONG_AST_BRIDGE_MAX
            _cap = max(0, int(MIDLONG_AST_BRIDGE_MAX))
        except Exception:
            _cap = 10
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            fid = str(r.get("factor_id") or "")
            ast = r.get("expr_ast")
            if not fid or not ast:
                continue
            # [2026-08-31 生成层审计 G3] seed_bootstrap 是 icir=0.05 的硬编码
            # 占位种子（6 个 seed_*），并非真实进化产物——此前经桥接混进中线
            # 活跃集冒充"活跃因子"（"中线 10 个活跃"实为 3 公式+1 rev50+6 占位）。
            # 排除后中长线活跃=真实因子；FACTOR_OVERSIGHT 晋升放开后由真因子补位。
            if str(r.get("source") or "").startswith("seed_bootstrap"):
                continue
            # 短线档排除：s5m_ 前缀（短周期标记）或 source 带 horizon=scalp
            src = str(r.get("source") or "")
            if fid.startswith("s5m_") or "horizon=scalp" in src:
                continue
            icir = float(r.get("icir") or 0.0)
            out.append({
                "factor_id": f"evo_{fid}",
                "formula": None,
                "extra": {
                    "horizon": "midlong",
                    "timeframe": "4h",
                    "kind": "ast",
                    "expr_ast": ast,
                    # [2026-08-23 M0-F2] P1-C4 设计契约：TRADABLE 含 PAPER 状态，
                    # 补偿机制①（PAPER 因子在线权重 ≤ PAPER_FACTOR_WEIGHT_CAP）
                    # 必须同样作用于 AST 桥接因子——此前漏标 role 导致
                    # get_active_factors 的封顶循环跳过它们（公式因子路径
                    # 在 factor_evaluation_pipeline 强制，AST 路径无此保护）。
                    **({"role": "paper"} if str(r.get("state") or "") == "PAPER" else {}),
                },
                "scores": {
                    "ic_mean": icir,   # 权重幅度代理（见 docstring）
                    "icir": icir,
                    "expected_sign": 1 if icir >= 0 else -1,
                },
            })
            if len(out) >= _cap:
                break
        return out

    @staticmethod
    def _runtime_weights() -> Dict[str, float]:
        try:
            from backend.services.factor_ic_evaluator import load_runtime_factor_weights
            return load_runtime_factor_weights() or {}
        except Exception:
            try:
                import json
                # [2026-09-02] 两处修正：①原先遍历 JSON 顶层键（updated_at /
                # lookback_days / weights / stats），float("2026-09-02T...") 必抛
                # ValueError → 这条兜底路径实际上永远返回 {}，从未生效过；
                # ②相对路径改为基于 __file__，不再依赖进程 cwd。
                from pathlib import Path as _P
                path = str(_P(__file__).resolve().parents[3] / "data"
                           / "factor_runtime_weights.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        _raw = json.load(f) or {}
                    return {str(k): float(v)
                            for k, v in (_raw.get("weights") or {}).items()}
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

        active = [r for r in custom_factor_store.list_active(tenant_id=_resolve_tenant_id()) if _is_midlong(r)]
        checked = retired = reduced = 0
        for rec in active:
            fid = rec.get("factor_id")
            formula = rec.get("formula")
            if not fid:
                continue
            if not formula:
                # [2026-08-15 因子化闭环] registry 因子（无公式）此前被跳过、
                # 永无复检/退役路径。现用与 scan_registry_midlong 相同的打分器
                # 复评：衰减/降级同样收敛回 rejected/candidate。
                try:
                    from backend.services.factor_engine.midlong_registry_factors import (
                        _score_one_registry_factor,
                    )
                    _reg_fid = str((rec.get("extra") or {}).get("registry_factor_id") or fid)
                    _tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
                    r = _score_one_registry_factor(fid, _reg_fid, _tf)
                    if not r or str(r.get("reason") or "") == "有效样本不足":
                        continue
                    _g = str(r.get("grade") or "F")
                    _ic = float(r.get("ic_mean") or 0.0)
                    checked += 1
                    _scores = {
                        "ic_mean": r.get("ic_mean", 0.0),
                        "icir": r.get("icir", 0.0),
                        "ic_decay_halflife": r.get("ic_decay_halflife", 0),
                        "monotonicity": r.get("monotonicity", 0.0),
                        "oos_net_return": r.get("oos_net_return", 0.0),
                        "oos_sharpe": r.get("oos_sharpe", 0.0),
                        "oos_win_rate": r.get("oos_win_rate", 0.0),
                        "oos_trades": r.get("oos_trades", 0),
                        "per_symbol": r.get("per_symbol") or {},
                        "reason": r.get("reason", ""),
                    }
                    _failed = abs(_ic) < _RETIRE_ABS_IC or _g in ("D", "F")
                    # [2026-09-02 根因修复] held-out 判决 reject 的纸面影子因子：
                    # 晋升时以「实盘 IC 反馈学习」为由放行，但反馈回路从未接回 →
                    # OOS 死因子永久滞留 active（实证 recheck_fails=21 仍 active、
                    # heldout ic=0）。reject 计入失败计数，连续 2 次 → 降级。
                    try:
                        _ho = dict((rec.get("extra") or {}).get("heldout") or {})
                        if str(_ho.get("verdict")) == "reject":
                            _failed = True
                    except Exception:
                        pass
                    _weak = _g == "C"
                    # [2026-08-15 去抖] 单次复检受窗口滑动 1-2 根 K 线影响即可 A↔C
                    # 翻跳（macd@4h：IC 同 -0.117，sharpe 0.83→0.21）。降级/退役
                    # 须连续两次复检失败，避免因子池震荡、路由反复换因子。
                    _extra = rec.setdefault("extra", {})
                    # [2026-09-01 方向翻转隔离] 晋升时锁定的 expected_sign 与复检
                    # IC 反向（且 |IC| 超过弱带 0.03）→ 计数 sign_flips。连续 2 次
                    # 翻转 = 方向不稳定（不是衰减也不是失效，是噪声/过拟合信号），
                    # 降级回 candidate 重新验证，不让路由拿着锁定的方向逆着新 IC 交易。
                    _flip_abs_min = float(_cfg("MIDLONG_ACTIVE_FLIP_ABS_IC", 0.03))
                    _sign_flip = False
                    try:
                        _locked = float(((rec.get("scores") or {}).get("expected_sign")) or 0.0)
                        if _locked != 0 and abs(_ic) >= _flip_abs_min and _locked * _ic < 0:
                            _sign_flip = True
                    except Exception:
                        pass
                    if _sign_flip:
                        _extra["sign_flips"] = int(_extra.get("sign_flips") or 0) + 1
                    else:
                        _extra["sign_flips"] = 0
                    if _failed or _weak or _sign_flip:
                        _fails = int(_extra.get("recheck_fails") or 0) + 1
                        _extra["recheck_fails"] = _fails
                    else:
                        _extra["recheck_fails"] = 0
                    if _failed and _extra["recheck_fails"] >= 2:
                        custom_factor_store.update_scores(
                            fid, grade=_g, scores=_scores, status="rejected",
                            tenant_id=_resolve_tenant_id(),
                        )
                        self._detach_from_engine(fid)
                        retired += 1
                        logger.info(
                            "[MidLongFactorSet] 退役 registry 因子 %s tf=%s (|IC|=%.3f grade=%s)",
                            fid, _tf, abs(_ic), _g,
                        )
                    elif _weak and _extra["recheck_fails"] >= 2:
                        custom_factor_store.update_scores(
                            fid, grade=_g, scores=_scores, status="candidate",
                            tenant_id=_resolve_tenant_id(),
                        )
                        self._detach_from_engine(fid)
                        reduced += 1
                        logger.info(
                            "[MidLongFactorSet] 降级 registry 因子 %s tf=%s (grade=C, 连续%d次)",
                            fid, _tf, _extra["recheck_fails"],
                        )
                    elif _sign_flip and int(_extra.get("sign_flips") or 0) >= 2:
                        # [2026-09-01 方向翻转隔离] 锁定方向连续 2 次被复检 IC 反向
                        # → 方向不稳定，降级回 candidate 重新验证（不交易、不反手）。
                        custom_factor_store.update_scores(
                            fid, grade=_g, scores=_scores, status="candidate",
                            tenant_id=_resolve_tenant_id(),
                        )
                        self._detach_from_engine(fid)
                        reduced += 1
                        logger.info(
                            "[MidLongFactorSet] 方向翻转隔离 %s tf=%s (锁定sign×复检IC反向, 连续%d次) → candidate",
                            fid, _tf, _extra["sign_flips"],
                        )
                    else:
                        # 保留 active 但刷新分数（单次波动不摘牌）
                        custom_factor_store.update_scores(
                            fid, grade=rec.get("grade") or _g, scores=_scores,
                            status="active", tenant_id=_resolve_tenant_id(),
                        )
                        logger.info(
                            "[MidLongFactorSet] registry 复检保持 %s tf=%s grade=%s fails=%d",
                            fid, _tf, _g, _extra["recheck_fails"],
                        )
                except Exception as e:
                    logger.debug("[MidLongFactorSet] registry 复检 %s 跳过: %s", fid, e)
                continue
            tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
            try:
                from backend.services.factor_engine.factor_backtest_scorer import midlong_lookback_for
                sr = factor_backtest_scorer.score_formula(
                    fid, formula,
                    interval=tf,
                    lookback=midlong_lookback_for(tf),
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
                _failed = abs_ic < _RETIRE_ABS_IC or sr.grade in ("D", "F")
                # [2026-09-02 根因修复] 公式因子分支同 registry 分支：
                # held-out reject 计入失败计数（纸面影子无反馈回路，OOS 死因子
                # 不得永久滞留 active），连续 2 次 → 降级 candidate。
                try:
                    _ho = dict((rec.get("extra") or {}).get("heldout") or {})
                    if str(_ho.get("verdict")) == "reject":
                        _failed = True
                except Exception:
                    pass
                _weak = sr.grade == "C"
                # [2026-08-15 去抖扩展] 公式因子与 registry 分支同一套规则：
                # 单次复检受窗口滑动 1-2 根 K 线影响即可 A↔C 翻跳
                # （ai_a101_macd_sig_4h 曾 A→C 单次降级清空因子池）。
                # 降级/退役须连续两次复检失败，避免 validate/prune 拉锯。
                _extra = rec.setdefault("extra", {})
                if _failed or _weak:
                    _fails = int(_extra.get("recheck_fails") or 0) + 1
                    _extra["recheck_fails"] = _fails
                else:
                    _extra["recheck_fails"] = 0
                if _failed and _extra["recheck_fails"] >= 2:
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="rejected",
                        tenant_id=_resolve_tenant_id(),
                    )
                    self._detach_from_engine(fid)
                    retired += 1
                    logger.info(
                        "[MidLongFactorSet] 退役公式因子 %s tf=%s (|IC|=%.3f grade=%s, 连续%d次)",
                        fid, tf, abs_ic, sr.grade, _extra["recheck_fails"],
                    )
                elif _weak and _extra["recheck_fails"] >= 2:
                    custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="candidate",
                        tenant_id=_resolve_tenant_id(),
                    )
                    self._detach_from_engine(fid)
                    reduced += 1
                    logger.info(
                        "[MidLongFactorSet] 降级公式因子 %s tf=%s (grade=C, 连续%d次)",
                        fid, tf, _extra["recheck_fails"],
                    )
                else:
                    # 保留 active 但刷新分数（单次波动不摘牌）
                    custom_factor_store.update_scores(
                        fid, grade=rec.get("grade") or sr.grade,
                        scores=self._scores_dict(sr), status="active",
                        tenant_id=_resolve_tenant_id(),
                    )
                    logger.info(
                        "[MidLongFactorSet] 公式复检保持 %s tf=%s grade=%s fails=%d",
                        fid, tf, sr.grade, _extra["recheck_fails"],
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
        # [2026-08-14 弹药扩源] kind=registry 记录用 extra.registry_factor_id 计算
        # （store 键为 f"{fid}@{tf}"，registry 真实 id 另行存放）。
        compute_ids: Dict[str, str] = {}
        formula_recs: List[Dict[str, Any]] = []
        for rec in active:
            tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
            if tf not in by_tf:
                continue
            _extra = rec.get("extra") or {}
            # [2026-08-16 修复] 公式因子不在 registry：factor_service.compute 会报
            # "not found in registry" 刷 ERROR；单独走公式计算路径。
            # [2026-08-23 M0-E1e] kind=ast（进化仓 TRADABLE 桥接，expr_ast 求值）
            # 同样不在 registry，误走 compute 会按因子逐个刷 KeyError。
            # _factor_history 内部已支持 AST 求值（与路由投票同一路径）。
            if str(rec.get("formula") or "").strip() or (
                str(_extra.get("kind") or "") == "ast" and _extra.get("expr_ast")
            ):
                formula_recs.append(rec)
                continue
            by_tf[tf].append(rec["factor_id"])
            compute_ids[rec["factor_id"]] = str(
                (rec.get("extra") or {}).get("registry_factor_id") or rec["factor_id"]
            )
        n = 0
        for tf, fids in by_tf.items():
            if not fids:
                continue
            try:
                _real_ids = [compute_ids[f] for f in fids]
                fv = factor_service.compute(symbol, timeframe=tf, factor_ids=_real_ids)
                if isinstance(fv, dict):
                    for k, v in fv.items():
                        if k not in _real_ids:
                            continue
                        # [2026-08-16 修复] FactorValue 对象不可 JSON 序列化，
                        # 注入 market_summary 后导致交易循环落库崩溃
                        # （TypeError: Object of type FactorValue is not JSON serializable）。
                        # 快照只存 float。
                        _val = getattr(v, "value", None)
                        if _val is None and isinstance(v, (int, float)):
                            _val = float(v)
                        if _val is not None:
                            out[tf][k] = float(_val)
                    n += len(out[tf])
            except Exception as e:
                logger.debug(f"[MidLongFactorSet] {symbol} {tf} compute 跳过: {e}")
        # 公式因子：走 midlong_factor_route 的公式历史路径（同样返回 float）
        if formula_recs:
            try:
                from backend.services.factor_engine.midlong_factor_route import _factor_history
                for rec in formula_recs:
                    tf = str((rec.get("extra") or {}).get("timeframe") or "4h").lower()
                    vals = _factor_history(rec, symbol)
                    if vals is None:
                        continue
                    finite = vals[np.isfinite(vals)]
                    if len(finite):
                        out[tf][str(rec["factor_id"])] = float(finite[-1])
                        n += 1
            except Exception as e:
                logger.debug(f"[MidLongFactorSet] {symbol} 公式因子快照失败: {e}")
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
            # [item13 2026-08-21] 复检同样锁定 expected_sign（与晋升口径一致）
            "expected_sign": 1 if float(sr.ic_mean or 0) >= 0 else -1,
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
        all_active = custom_factor_store.list_active(tenant_id=_resolve_tenant_id())
        active = [r for r in all_active if _is_midlong(r)]
        candidates = [r for r in custom_factor_store.list_candidates(tenant_id=_resolve_tenant_id()) if _is_midlong(r)]
        rejected = [r for r in custom_factor_store.list(status="rejected", tenant_id=_resolve_tenant_id()) if _is_midlong(r)]
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
