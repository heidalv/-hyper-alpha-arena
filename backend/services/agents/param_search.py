# -*- coding: utf-8 -*-
"""ParamSearch Agent（v3 方向 3，p2-agents-b）：参数扫描，但**默认不相信自己的结果**。

参数搜索是整套系统里最容易自欺的一环：在全样本上扫 20 组参数，总能找到一组"显著"的，
然后上线就失效。本 Agent 的设计目标不是"找到最优参数"，而是**让每一个候选参数都必须
先通过样本外检验、再通过多重比较惩罚、最后还要以实验卡的形式接受前瞻验证**，任何一关
不过就不提议。

三道关卡：
  1. **样本外切分**：按时间切 train / test（默认 70/30）。参数在 train 上选，指标在 test 上报。
     只在 train 上好、test 上不好的候选直接丢弃。
  2. **多重比较惩罚**：扫了 k 组参数就相当于做了 k 次检验。用 Bonferroni 把显著性水平收紧到
     α/k，对应把置信区间从 95% 拉宽——`_z_for` 按 k 反算 z 值。扫得越多，要求越严。
  3. **实验卡前瞻验证**：即使前两关都过，也只产 `proposed` 实验卡，由生命周期任务在
     **未来窗口**上用真实账本复核。历史上好 ≠ 未来有效。

当前接的搜索目标是 E5 事件策略（它们已有 `backtest(days, ...)` 接口且不下单，扫描零风险）。
`param_shift` 预测：提议的参数在未来窗口的表现仍优于当前参数。
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.agents.base import (
    ACTION_PROPOSE_EXPERIMENT,
    Advice,
    ObservationAgent,
    Prediction,
    env_float,
    env_int,
    env_true,
    now_ms,
)

logger = logging.getLogger(__name__)

AGENT_ID = "param_search"
KIND_PARAM_SHIFT = "param_shift"

COST_BP = 14.0


def _z_for(k_trials: int, base_z: float = 1.96) -> float:
    """Bonferroni 校正后的 z 值：k 次检验把 α 收紧到 α/k。

    正态分位数没有初等闭式解，这里用 Acklam 的有理逼近（误差 < 1e-9，足够定门槛）。
    k=1 时返回 1.96（即不校正）。
    """
    if k_trials <= 1:
        return base_z
    alpha = 2.0 * (1.0 - _norm_cdf(base_z))     # 双侧 α，≈0.05
    p = 1.0 - (alpha / k_trials) / 2.0
    return _norm_ppf(p)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """标准正态分位数（Acklam 逼近）。"""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def split_excess(excess_bp: Sequence[float], train_frac: float = 0.7) -> Tuple[List[float], List[float]]:
    """按顺序切 train / test（时间序列不能随机切，会泄漏未来信息）。"""
    xs = list(excess_bp)
    cut = int(len(xs) * train_frac)
    return xs[:cut], xs[cut:]


def summarize(xs: Sequence[float], *, z: float = 1.96) -> Dict[str, Any]:
    """给定超额 bp 序列 → 均值 / 置信下界 / 命中率。z 由多重比较校正决定。"""
    n = len(xs)
    if n < 2:
        return {"n": n, "mean_bp": None, "lower_bp": None, "hit_rate": None}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    return {
        "n": n,
        "mean_bp": round(mean, 3),
        "lower_bp": round(mean - z * se, 3),
        "hit_rate": round(sum(1 for x in xs if x > 0) / n, 4),
    }


# ─────────────────────────── 搜索目标 ───────────────────────────
def e5_funding_grid() -> List[Dict[str, Any]]:
    """E5-2：真正的决策点是 OI 确认门。实测 180 天里 1348 条 z 命中中有 1340 条
    被"缺 OI 或 OI 未同向增"丢弃——在 OI 门卡死的前提下扫 z 阈值毫无意义，
    所以两个维度一起扫。扫描零风险：策略在影子期，回测不下单不改状态。"""
    return [{"require_oi": oi, "z_threshold": z}
            for oi in (True, False) for z in (3.0, 4.0, 5.0)]


def e5_liq_grid() -> List[Dict[str, Any]]:
    return [{"side_ratio": r} for r in (0.60, 0.70, 0.80, 0.90)]


def e5_news_grid() -> List[Dict[str, Any]]:
    return [{"min_direction": d} for d in (0.3, 0.5, 0.6)]


SEARCH_TARGETS: Dict[str, Dict[str, Any]] = {
    "e5_2_funding_shock": {"grid": e5_funding_grid, "horizon_h": 4, "days": 180},
    "e5_3_liq_cascade": {"grid": e5_liq_grid, "horizon_h": 2, "days": 20},
    "e5_5_news_hedge": {"grid": e5_news_grid, "horizon_h": 6, "days": 20},
}

# 可评分币池是**当下**的属性（"现在这个币能不能取到价格"），与回测跨度无关。
# 跟着回测天数走会让币池随窗口漂移，同一组参数在不同天数下不可比。
UNIVERSE_DAYS = 30


def _apply_params(strat: Any, params: Dict[str, Any]) -> bool:
    for k, v in params.items():
        if not hasattr(strat, k):
            return False
        setattr(strat, k, v)
    return True


def scan_grid(strategy_id: str, grid: List[Dict[str, Any]], *, days: int,
              horizon_h: int) -> Tuple[List[Tuple[Dict[str, Any], List[float]]], List[str]]:
    """对一组参数跑扫描 → [(params, 超额 bp 序列)]。

    K 线**只加载一次**（先把所有参数组的信号收齐、取 symbol 并集），
    否则 12 组参数就要拉 12 次全量 1h K 线，一轮两分多钟。
    """
    from backend.research.event_study import HOUR, load_hourly_closes
    from backend.services.strategies.event import get_strategy

    notes: List[str] = []
    until = now_ms()
    since = until - int(days) * 86400 * 1000

    detected: List[Tuple[Dict[str, Any], List[Any]]] = []
    symbols: set = {"BTC"}
    for params in grid:
        strat = get_strategy(strategy_id)
        if strat is None:
            notes.append(f"策略 {strategy_id} 未注册")
            return [], notes
        if not _apply_params(strat, params):
            notes.append(f"参数 {params} 不适用于 {strategy_id}")
            continue
        local: List[str] = []
        sigs = strat.detect_scorable(since_ms=since, until_ms=until, limit=20000,
                                     notes=local, universe_days=UNIVERSE_DAYS)
        detected.append((params, sorted(sigs, key=lambda x: x.ts_ms)))
        symbols |= {s.symbol for s in sigs}

    if not any(sigs for _, sigs in detected):
        notes.append("全部参数组都没有产生信号")
        return [(p, []) for p, _ in detected], notes

    closes = load_hourly_closes(sorted(symbols), since // 1000 - 2 * HOUR,
                                until // 1000 + (horizon_h + 2) * HOUR)

    def px(sym: str, ts_s: int) -> Optional[float]:
        return (closes.get(sym) or {}).get((ts_s // HOUR) * HOUR)

    out: List[Tuple[Dict[str, Any], List[float]]] = []
    for params, sigs in detected:
        xs: List[float] = []
        for s in sigs:
            t = int(s.ts_ms // 1000)
            p0, p1 = px(s.symbol, t), px(s.symbol, t + horizon_h * HOUR)
            b0, b1 = px("BTC", t), px("BTC", t + horizon_h * HOUR)
            if not p0 or not p1:
                continue
            ret = p1 / p0 - 1.0
            if s.symbol != "BTC" and b0 and b1:
                ret -= (b1 / b0 - 1.0)
            xs.append(s.direction * ret * 1e4)
        out.append((params, xs))
    return out, notes


class ParamSearchAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "参数扫描：样本外切分 + 多重比较惩罚 + 实验卡前瞻验证，三关全过才提议"
    kinds = (KIND_PARAM_SHIFT,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.train_frac = env_float("AGENT_PARAM_TRAIN_FRAC", 0.7)
        self.min_n_test = env_int("AGENT_PARAM_MIN_N_TEST", 30)
        self.window_h = env_float("AGENT_PARAM_WINDOW_H", 336.0)
        self.targets = [t.strip() for t in
                        (os.getenv("AGENT_PARAM_TARGETS", ",".join(SEARCH_TARGETS)) or "").split(",")
                        if t.strip() in SEARCH_TARGETS]
        self.enabled = env_true("AGENT_PARAM_SEARCH_ENABLED", True)

    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "AGENT_PARAM_SEARCH_ENABLED=false", "targets": []}

        results: List[Dict[str, Any]] = []
        for sid in self.targets:
            cfg = SEARCH_TARGETS[sid]
            grid = cfg["grid"]()
            z = _z_for(len(grid))
            try:
                scanned, notes = scan_grid(sid, grid, days=cfg["days"], horizon_h=cfg["horizon_h"])
            except Exception as exc:
                errors.append(f"{sid}: {exc}")
                continue
            for n in notes:
                errors.append(f"{sid}: {n}")

            trials: List[Dict[str, Any]] = []
            for params, xs in scanned:
                train, test = split_excess(xs, self.train_frac)
                trials.append({
                    "params": params,
                    "n_all": len(xs),
                    "train": summarize(train, z=z),
                    "test": summarize(test, z=z),
                })

            # 三道关卡
            passed: List[Dict[str, Any]] = []
            for t in trials:
                tr, te = t["train"], t["test"]
                if te["n"] < self.min_n_test:
                    t["verdict"] = f"样本外仅 {te['n']} 条，不足 {self.min_n_test}"
                    continue
                if tr.get("lower_bp") is None or tr["lower_bp"] <= COST_BP:
                    t["verdict"] = "训练集未过成本门"
                    continue
                if te.get("lower_bp") is None or te["lower_bp"] <= COST_BP:
                    t["verdict"] = "样本外未过成本门（训练集好但外推失败）"
                    continue
                t["verdict"] = "通过样本外 + 多重比较校正"
                passed.append(t)

            results.append({
                "strategy": sid, "k_trials": len(grid), "z_corrected": round(z, 3),
                "days": cfg["days"], "horizon_h": cfg["horizon_h"],
                "trials": trials, "passed": passed,
            })

        return {
            "targets": self.targets, "results": results,
            "train_frac": self.train_frac, "min_n_test": self.min_n_test,
            "cost_bp": COST_BP, "universe_days": UNIVERSE_DAYS,
            "n_candidates": sum(len(r["passed"]) for r in results),
            "note": "任何候选都只提 proposed 实验卡，必须再过前瞻窗口才谈采纳",
        }

    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        out: List[Prediction] = []
        for r in findings.get("results") or []:
            for t in r.get("passed") or []:
                out.append(Prediction(
                    kind=KIND_PARAM_SHIFT,
                    subject=f"{r['strategy']}:{json.dumps(t['params'], sort_keys=True)}",
                    prediction={
                        "strategy": r["strategy"], "params": t["params"],
                        "horizon_h": r["horizon_h"],
                        "expect_lower_bp_gt": COST_BP,
                        "test_lower_bp": t["test"].get("lower_bp"),
                    },
                    horizon_ms=int(self.window_h * 3600 * 1000),
                    confidence=0.5,
                ))
        return out

    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        out: List[Advice] = []
        for r in findings.get("results") or []:
            for t in (r.get("passed") or [])[:2]:
                sid, params = r["strategy"], t["params"]
                out.append(Advice(
                    action=ACTION_PROPOSE_EXPERIMENT,
                    target=sid,
                    severity=2,
                    reason=f"{sid} 参数 {params} 通过样本外检验："
                           f"train 下界 {t['train'].get('lower_bp')}bp、test 下界 {t['test'].get('lower_bp')}bp"
                           f"（{r['k_trials']} 组扫描，z 已校正到 {r['z_corrected']}）",
                    params={
                        "title": f"{sid} 参数调整 {params}",
                        "hypothesis": f"把 {sid} 的参数改为 {params} 后，样本外超额 95% 下界为 "
                                      f"{t['test'].get('lower_bp')}bp（> {COST_BP}bp 成本门）；"
                                      "若该优势不是过拟合，未来窗口内影子信号的超额下界应继续高于成本门。",
                        "change": {"apply": False, "kind": "param",
                                   "strategy": sid, "params": params,
                                   "train": t["train"], "test": t["test"],
                                   "k_trials": r["k_trials"], "z_corrected": r["z_corrected"]},
                        "expected_metrics": [
                            {"metric": "excess_lower_bp", "op": ">", "threshold": COST_BP,
                             "scope": f"source:{sid}", "min_n": self.min_n_test},
                        ],
                        "window_hours": int(self.window_h),
                        "rollback_condition": "若前瞻窗口内超额下界跌破 0，恢复原参数并记录为过拟合案例",
                        "notes": f"扫描 {r['k_trials']} 组，Bonferroni 校正后 z={r['z_corrected']}",
                    },
                ))
        return out


def score_param_shift(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`param_shift` 到期评分：该策略在预测窗口内的真实影子超额下界是否仍过成本门。"""
    pred = row.get("prediction") or {}
    if isinstance(pred, str):
        try:
            pred = json.loads(pred)
        except Exception:
            return None
    sid = pred.get("strategy")
    thr = float(pred.get("expect_lower_bp_gt") or COST_BP)
    created = int(row.get("created_ms") or 0)
    expires = int(row.get("expires_ms") or 0)
    if not sid or not created or not expires:
        return None
    try:
        from backend.services.experiments.metrics import m_excess_lower_bp

        res = m_excess_lower_bp(f"source:{sid}", created, expires, min_n=20)
    except Exception as exc:
        logger.warning("[%s] 评分取数失败: %s", AGENT_ID, exc)
        return None
    if not res.get("ok"):
        return None          # 样本不足 → 保持 open
    lower = float(res["value"])
    return {"score": 1.0 if lower > thr else 0.0,
            "outcome": {"strategy": sid, "actual_lower_bp": lower, "threshold_bp": thr,
                        "n": res.get("n")}}


def build() -> ParamSearchAgent:
    return ParamSearchAgent()
