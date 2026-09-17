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

# [2026-08-13 短线因子根因修复 P1-7] 退役阈值收紧 0.015→0.02：
# 原阈值太松，|IC|~0.02 的近零因子长期滞留实盘。联合 ICIR 条件防误杀
# （|IC| 与 |ICIR| 双低才退役；grade D/F 不受 ICIR 约束直接退役）。
_RETIRE_ABS_IC = float(os.getenv("SCALP_ACTIVE_RETIRE_ABS_IC", "0.02"))
_RETIRE_ICIR = float(os.getenv("SCALP_ACTIVE_RETIRE_ICIR", "0.3"))


def _is_scalp(rec: Dict[str, Any]) -> bool:
    """非 midlong 标签的（含未标记）都归短线，避免与中长线因子集混淆。

    [轮48 2026-09-17 目标④] 「日内」档（`horizon=intraday`）**不再算作短线**：
    该档由中线车道消费（实测中线中位持仓 3.2h = 日内），若继续归入短线池，
    就会落进已判死（SCALP_OPEN_DISABLED=true / SCALP_RESEARCH_ENABLED=false）的车道
    —— 这正是 15m 因子 0 candidate / 0 active 的结构性原因之一。
    语义依据见 `backend/config/cycle_semantics.py`。
    默认无任何记录带 intraday 标签时，本函数行为与改动前逐位一致。
    """
    h = str((rec.get("extra") or {}).get("horizon") or "scalp").lower()
    return h not in ("midlong", "intraday")


def _resolve_tenant_id() -> Optional[int]:
    """[2026-08-13 P1-9] 解析管理员租户 id。

    custom_factor_store 按 t{tenant_id}:factor_id 隔离存储，list_* 不传租户时
    返回空列表（防误共享）。这里显式取管理员租户，恢复 AI 因子的退役/晋升管理。
    """
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


class ScalpActiveFactorSet:
    """短线活跃因子集管理（单例）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 查询 ──
    def get_active_factors(self) -> List[Dict[str, Any]]:
        """返回活跃因子 + 运行时权重（[M4] FACTOR_COMBO_MODE=icir → ICIR 加权）。

        [2026-09-01 断层根治] 合并进化仓 TRADABLE 的短线 AST 因子（factor_active_set
        表，s5m_/15m 前缀或 source 带 horizon=scalp）——此前本类只读
        custom_factor_store，进化链（GP/MCTS）产物只落 factor_active_set →
        短线策略注入（strategy_library.create_strategy_from_template）永远拿不到
        挖掘因子（"挖了也没进策略"的断层）。桥接记录带 role=paper 权重封顶，
        与中线 _tradable_ast_bridge 同口径。
        """
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
        except Exception:
            return []
        active = [r for r in custom_factor_store.list_active(tenant_id=_resolve_tenant_id()) if _is_scalp(r)]
        active.extend(self._tradable_ast_bridge())
        weights = self._runtime_weights()
        try:
            from backend.services.factor_engine.combo_weights import resolve_combo_weights
            wmap = resolve_combo_weights(active, weights)
        except Exception as err:
            # [2026-09-02 消除 fail-open] 原分支静默退化为等权 1.0，权重体系出错时
            # 系统照常满权出信号。改为显式告警 + 全零（fail-closed）：下游
            # weight_sum<=0 会跳过本轮，宁可不开仓也不按不可信权重开仓。
            logger.error(
                "[ScalpActiveSet] 组合权重解析失败 → 本轮因子权重全零、不出信号: %s",
                err, exc_info=True,
            )
            wmap = {str(r.get("factor_id") or ""): 0.0 for r in active}
        # [M0-F1 2026-08-22] role=paper 影子因子权重封顶（与中线同一口径）。
        try:
            from backend.config.settings import PAPER_FACTOR_WEIGHT_CAP as _PWC
            _paper_cap = float(_PWC or 0.5)
        except Exception:
            _paper_cap = 0.5
        for rec in active:
            # wmap 由 resolve_combo_weights 产出、必然覆盖全部 records，缺失即异常
            # → 用 0.0 而非 1.0（1.0 会让未定权的因子满权投票）。
            rec["runtime_weight"] = wmap.get(rec.get("factor_id"), 0.0)
            if str((rec.get("extra") or {}).get("role") or "") == "paper":
                # 只在缺失(None)时取中性 1.0；显式 0.0 必须保持 0（避免 `or` 陷阱
                # 把归零权重还原成满权）
                _rw = rec.get("runtime_weight")
                rec["runtime_weight"] = min(
                    1.0 if _rw is None else float(_rw), _paper_cap)
        return active

    @staticmethod
    def _tradable_ast_bridge() -> List[Dict[str, Any]]:
        """[2026-09-01 断层根治] 进化仓 TRADABLE 短线 AST → 短线 kind="ast" 记录。

        过滤：排除 seed_bootstrap 占位种子；只保留短线档（s5m_ 前缀或 source 带
        horizon=scalp）。方向以 icir 符号锁定 expected_sign，权重幅度用 |icir|。
        """
        try:
            from backend.services.factor_engine.active_set_policy import (
                ActiveSetRole,
                load_factor_active_rows,
            )
            rows = load_factor_active_rows(ActiveSetRole.TRADABLE, parse_expr=True, limit=100)
        except Exception:
            return []
        try:
            from backend.config.settings import SCALP_AST_BRIDGE_MAX
            _cap = max(0, int(SCALP_AST_BRIDGE_MAX))
        except Exception:
            _cap = 10
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            fid = str(r.get("factor_id") or "")
            ast = r.get("expr_ast")
            if not fid or not ast:
                continue
            if str(r.get("source") or "").startswith("seed_bootstrap"):
                continue
            # 只收短线档：s5m_ 前缀（短周期标记）或 source 带 horizon=scalp。
            # 中线档（4h 无前缀）归 midlong 桥，避免同一 AST 因子双集重复投票。
            src = str(r.get("source") or "")
            if not (fid.startswith("s5m_") or "horizon=scalp" in src):
                continue
            icir = float(r.get("icir") or 0.0)
            out.append({
                "factor_id": f"evo_{fid}",
                "formula": None,
                "extra": {
                    "horizon": "scalp",
                    "timeframe": str(r.get("period") or "5m"),
                    "kind": "ast",
                    "expr_ast": ast,
                    **({"role": "paper"} if str(r.get("state") or "") == "PAPER" else {}),
                },
                "scores": {
                    "ic_mean": icir,
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
            # 直接读文件兜底
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

    # ── 衰减复检（2.4）──
    def recheck_and_prune(self) -> Dict[str, Any]:
        """对活跃因子重跑单因子回测，IC 衰减到阈值以下的自动退役/降级。"""
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
            from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
        except Exception as e:
            return {"checked": 0, "retired": 0, "error": str(e)}

        active = [r for r in custom_factor_store.list_active(tenant_id=_resolve_tenant_id()) if _is_scalp(r)]
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
                abs_icir = abs(sr.icir or 0.0)
                # [2026-08-13 P1-7] 联合条件：|IC| 与 ICIR 双低才退役；
                # grade D/F 仍直接退役（独立证据，不依赖 ICIR）。
                if (abs_ic < _RETIRE_ABS_IC and abs_icir < _RETIRE_ICIR) or sr.grade in ("D", "F"):
                    # 退役：从实时 FACTORS 摘除 + 目录标记 rejected
                    # [2026-08-14 P1-B1 修复] 补 tenant_id（此前漏传 → _resolve_key
                    # 找不到 t{tid}:factor_id 前缀键 → 写回静默失败，目录仍 active，
                    # 热加载/重启后"已退役"因子复活震荡）。并检查返回值。
                    _ok = custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="rejected",
                        tenant_id=_resolve_tenant_id(),
                    )
                    if not _ok:
                        logger.warning(
                            "[ActiveFactorSet] 退役写回失败（目录未更新）: %s", fid
                        )
                    self._detach_from_engine(fid)
                    retired += 1
                    logger.info(
                        f"[ActiveFactorSet] 退役衰减因子 {fid} "
                        f"(|IC|={abs_ic:.3f} |ICIR|={abs_icir:.3f} grade={sr.grade})"
                    )
                elif sr.grade == "C":
                    # 降级为候选（暂不参与实时，等下次闸门复议）
                    _ok = custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="candidate",
                        tenant_id=_resolve_tenant_id(),
                    )
                    if not _ok:
                        logger.warning(
                            "[ActiveFactorSet] 降级写回失败（目录未更新）: %s", fid
                        )
                    self._detach_from_engine(fid)
                    reduced += 1
                    logger.info(f"[ActiveFactorSet] 降级因子 {fid} (grade=C)")
                else:
                    # 仍达标：更新分数保持 active
                    _ok = custom_factor_store.update_scores(
                        fid, grade=sr.grade, scores=self._scores_dict(sr), status="active",
                        tenant_id=_resolve_tenant_id(),
                    )
                    if not _ok:
                        logger.warning(
                            "[ActiveFactorSet] 保级写回失败（目录未更新）: %s", fid
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
        _tid = _resolve_tenant_id()
        active = [r for r in custom_factor_store.list_active(tenant_id=_tid) if _is_scalp(r)]
        weights = self._runtime_weights()
        ics = [r.get("scores", {}).get("ic_mean") for r in active if r.get("scores")]
        ics = [x for x in ics if isinstance(x, (int, float))]
        return {
            "active": len(active),
            "candidate": len([r for r in custom_factor_store.list_candidates(tenant_id=_tid) if _is_scalp(r)]),
            "rejected": len([r for r in custom_factor_store.list(status="rejected", tenant_id=_tid) if _is_scalp(r)]),
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
