# -*- coding: utf-8 -*-
"""实验卡指标求值器（v3 方向 3，p2-agents-b）。

`experiments.expected_metrics` 的格式是
    [{"metric": "excess_bp", "op": ">", "threshold": 14, "scope": "source:e5_2_funding_shock"}, ...]
但此前没有任何代码能把它算成数值 —— 卡片写得再规范也只能永远挂在 proposed。本模块补上这一环。

**scope 语法**（`前缀:标识`）：
    source:<name>     signal_ledger 里该信号源（E5 策略、因子、Master 等都以 source 入账）
    agent:<id>        agent_predictions 里该 Agent 的预测得分
    exec:<account_id> paper_orders 的执行质量（滑点 / 拒单 / 部分成交 / 费率）
    account:<id>      edge_ledger 的持仓级净边际
    global            不限定（目前等价于全部 source）

**metric 名**见 `METRIC_REGISTRY`。每个求值器返回 `{"value", "n", "ok", "detail"}`：
`ok=False` 表示**样本不足以下结论**（不是"不达标"）——调用方应让实验 `extended` 而不是 `rejected`。
这条区分很重要：把"没数据"判成"没效果"会让好想法被误杀。

**baseline 对比**：指标项里带 `"baseline": "before"` 时，同时计算实验开始前等长窗口的同一指标，
`value` 改为 `after − before`（差值），`detail.before/after` 保留原值。适合"改动是否带来提升"类假设。
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_N_DEFAULT = 20


def now_ms() -> int:
    return int(time.time() * 1000)


def _fail(reason: str, n: int = 0) -> Dict[str, Any]:
    return {"value": None, "n": n, "ok": False, "detail": {"reason": reason}}


def parse_scope(scope: Optional[str]) -> Tuple[str, str]:
    """'source:abc' → ('source', 'abc')；无前缀视为 global。"""
    s = (scope or "global").strip()
    if ":" not in s:
        return ("global", s) if s != "global" else ("global", "")
    kind, _, ident = s.partition(":")
    return kind.strip().lower(), ident.strip()


# ─────────────────────────── signal_ledger 系 ───────────────────────────
def _load_signals(scope: str, since_ms: int, until_ms: int, limit: int = 5000) -> List[Dict[str, Any]]:
    from backend.services.analysis import ledgers

    kind, ident = parse_scope(scope)
    rows = ledgers.list_signals(source=ident if kind == "source" and ident else None,
                               since_ms=since_ms, limit=limit)
    return [r for r in rows
            if str(r.get("status")) == "scored" and int(r.get("created_ms") or 0) <= until_ms]


def m_excess_bp(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """信号平均相对 BTC 超额（bp），含 95% CI 下界。"""
    from backend.research.event_study import mean_se_interval

    rows = _load_signals(scope, since_ms, until_ms)
    xs = [float(r["excess_bp"]) for r in rows if r.get("excess_bp") is not None]
    if len(xs) < int(kw.get("min_n") or MIN_N_DEFAULT):
        return _fail(f"已评分信号仅 {len(xs)} 条，不足 {kw.get('min_n') or MIN_N_DEFAULT}", len(xs))
    mean, se, lo, hi = mean_se_interval([x / 1e4 for x in xs])
    return {"value": round(mean * 1e4, 3), "n": len(xs), "ok": True,
            "detail": {"ci_bp": [round(lo * 1e4, 2), round(hi * 1e4, 2)], "lower_bp": round(lo * 1e4, 2)}}


def m_excess_lower_bp(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """超额的 95% 置信下界——晋升类判定该用这个，而不是均值。"""
    base = m_excess_bp(scope, since_ms, until_ms, **kw)
    if not base["ok"]:
        return base
    return {"value": base["detail"]["lower_bp"], "n": base["n"], "ok": True, "detail": base["detail"]}


def m_hit_rate(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    from backend.research.event_study import wilson_interval

    rows = _load_signals(scope, since_ms, until_ms)
    hits = [int(r["hit"]) for r in rows if r.get("hit") is not None]
    if len(hits) < int(kw.get("min_n") or MIN_N_DEFAULT):
        return _fail(f"已评分信号仅 {len(hits)} 条", len(hits))
    lo, hi = wilson_interval(sum(hits), len(hits))
    return {"value": round(sum(hits) / len(hits), 4), "n": len(hits), "ok": True,
            "detail": {"ci": [round(lo, 4), round(hi, 4)]}}


def m_signal_n(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """样本量本身也常是门槛（"至少要跑出 30 个事件"）。"""
    rows = _load_signals(scope, since_ms, until_ms)
    return {"value": len(rows), "n": len(rows), "ok": True, "detail": {}}


def m_brier(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    rows = _load_signals(scope, since_ms, until_ms)
    xs = [float(r["brier"]) for r in rows if r.get("brier") is not None]
    if len(xs) < int(kw.get("min_n") or MIN_N_DEFAULT):
        return _fail(f"有 Brier 的样本仅 {len(xs)} 条", len(xs))
    return {"value": round(sum(xs) / len(xs), 4), "n": len(xs), "ok": True, "detail": {}}


# ─────────────────────────── agent_predictions 系 ───────────────────────────
def m_agent_score(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """该 Agent 已评分预测的平均得分（0–1）。"""
    from backend.services.analysis import ledgers

    kind, ident = parse_scope(scope)
    if kind != "agent" or not ident:
        return _fail("scope 必须是 agent:<id>")
    # list_predictions 不支持时间过滤，取回后自己按 created_ms 卡窗口
    rows = ledgers.list_predictions(agent=ident, status="scored", limit=2000)
    xs = [float(r["score"]) for r in rows
          if r.get("score") is not None
          and since_ms <= int(r.get("created_ms") or 0) <= until_ms]
    if len(xs) < int(kw.get("min_n") or 10):
        return _fail(f"该 Agent 已评分预测仅 {len(xs)} 条", len(xs))
    return {"value": round(sum(xs) / len(xs), 4), "n": len(xs), "ok": True, "detail": {}}


# ─────────────────────────── 执行质量系（paper_orders）───────────────────────────
def _exec_stats(account_id: Optional[int], since_ms: int, until_ms: int) -> Dict[str, Any]:
    """paper_orders 的执行质量原始统计。与 ExecutionQA Agent 共用同一口径。"""
    from backend.services.agents.execution_qa import execution_stats

    return execution_stats(account_id=account_id, since_ms=since_ms, until_ms=until_ms)


def _exec_metric(field: str, scope: str, since_ms: int, until_ms: int, min_n: int, **kw) -> Dict[str, Any]:
    kind, ident = parse_scope(scope)
    acct = None
    # `exec:all`（或空标识）= 不限账户看全库；单账户 ledger 未配置时就是这个形态
    if kind == "exec" and ident and ident.lower() not in ("all", "none", "*"):
        try:
            acct = int(ident)
        except ValueError:
            return _fail(f"exec scope 需要账户 id 或 all，收到 {ident!r}")
    st = _exec_stats(acct, since_ms, until_ms)
    if st.get("error"):
        return _fail(str(st["error"]))
    n = int(st.get("n_orders") or 0)
    if n < min_n:
        return _fail(f"订单仅 {n} 笔，不足 {min_n}", n)
    val = st.get(field)
    if val is None:
        return _fail(f"{field} 无法计算（{st.get('notes')}）", n)
    return {"value": round(float(val), 4), "n": n, "ok": True,
            "detail": {k: st.get(k) for k in ("n_filled", "n_rejected", "n_partial", "notes")}}


def m_slippage_bp(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """滑点用**中位数**：分布尾部极厚，均值不代表典型一笔的执行成本。"""
    return _exec_metric("median_slippage_bp", scope, since_ms, until_ms, int(kw.get("min_n") or 20))


def m_reject_rate(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    return _exec_metric("reject_rate", scope, since_ms, until_ms, int(kw.get("min_n") or 20))


def m_partial_rate(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    return _exec_metric("partial_rate", scope, since_ms, until_ms, int(kw.get("min_n") or 20))


def m_fee_bp(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    return _exec_metric("avg_fee_bp", scope, since_ms, until_ms, int(kw.get("min_n") or 20))


# ─────────────────────────── edge_ledger 系 ───────────────────────────
def m_net_bp(scope: str, since_ms: int, until_ms: int, **kw) -> Dict[str, Any]:
    """持仓级净边际 bp（扣费后）——来自 edge_ledger 的真实成交，不是信号超额。"""
    kind, ident = parse_scope(scope)
    if kind != "account" or not ident:
        return _fail("scope 必须是 account:<id>")
    try:
        acct = int(ident)
    except ValueError:
        return _fail(f"account scope 需要数字 id，收到 {ident!r}")
    days = max(1, int(math.ceil((until_ms - since_ms) / 86400000)))
    try:
        from backend.database.connection import SessionLocal
        from backend.services.ledger.edge_ledger import load_trade_rows

        db = SessionLocal()
        try:
            rows = load_trade_rows(db, acct, days)
        finally:
            db.close()
    except Exception as exc:
        return _fail(f"edge_ledger 读取失败: {exc}")
    xs = [float(r["net_bp"]) for r in rows if r.get("net_bp") is not None]
    if len(xs) < int(kw.get("min_n") or MIN_N_DEFAULT):
        return _fail(f"平仓样本仅 {len(xs)} 笔", len(xs))
    from backend.research.event_study import mean_se_interval

    mean, se, lo, hi = mean_se_interval([x / 1e4 for x in xs])
    return {"value": round(mean * 1e4, 3), "n": len(xs), "ok": True,
            "detail": {"ci_bp": [round(lo * 1e4, 2), round(hi * 1e4, 2)], "lower_bp": round(lo * 1e4, 2)}}


METRIC_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "excess_bp": m_excess_bp,
    "excess_lower_bp": m_excess_lower_bp,
    "hit_rate": m_hit_rate,
    "signal_n": m_signal_n,
    "brier": m_brier,
    "agent_score": m_agent_score,
    "slippage_bp": m_slippage_bp,
    "reject_rate": m_reject_rate,
    "partial_rate": m_partial_rate,
    "fee_bp": m_fee_bp,
    "net_bp": m_net_bp,
}


# ─────────────────────────── 求值与判定 ───────────────────────────
_OPS: Dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: abs(a - b) < 1e-9,
    "!=": lambda a, b: abs(a - b) >= 1e-9,
}


def evaluate_metric(spec: Dict[str, Any], *, since_ms: int, until_ms: int) -> Dict[str, Any]:
    """求值单个指标项 → {metric, scope, op, threshold, value, n, ok, passed, detail}。

    `ok=False`（样本不足 / 取数失败）时 `passed` 恒为 None —— 调用方必须区分
    "没结论"和"不达标"。
    """
    name = str(spec.get("metric") or "").strip()
    scope = str(spec.get("scope") or "global")
    op = str(spec.get("op") or ">").strip()
    out: Dict[str, Any] = {"metric": name, "scope": scope, "op": op,
                           "threshold": spec.get("threshold"), "value": None,
                           "n": 0, "ok": False, "passed": None, "detail": {}}
    fn = METRIC_REGISTRY.get(name)
    if fn is None:
        out["detail"] = {"reason": f"未知指标 {name}，可用: {sorted(METRIC_REGISTRY)}"}
        return out
    if op not in _OPS:
        out["detail"] = {"reason": f"未知比较符 {op}"}
        return out

    kwargs = {k: v for k, v in spec.items() if k in ("min_n",)}
    try:
        res = fn(scope, since_ms, until_ms, **kwargs)
    except Exception as exc:
        logger.warning("[experiments.metrics] %s 求值失败: %s", name, exc)
        out["detail"] = {"reason": f"求值异常: {exc}"}
        return out

    out.update({"value": res.get("value"), "n": res.get("n") or 0,
                "ok": bool(res.get("ok")), "detail": res.get("detail") or {}})

    # baseline=before：与实验开始前等长窗口比差值
    if out["ok"] and str(spec.get("baseline") or "").lower() == "before":
        span = max(1, until_ms - since_ms)
        try:
            prev = fn(scope, since_ms - span, since_ms, **kwargs)
        except Exception as exc:
            prev = {"ok": False, "detail": {"reason": str(exc)}}
        if not prev.get("ok"):
            out["ok"] = False
            out["detail"] = {**out["detail"], "baseline": "before",
                             "reason": f"基线窗口样本不足：{(prev.get('detail') or {}).get('reason')}"}
            return out
        after, before = float(out["value"]), float(prev["value"])
        out["detail"] = {**out["detail"], "baseline": "before", "before": before, "after": after}
        out["value"] = round(after - before, 4)

    thr = out["threshold"]
    if out["ok"] and thr is not None:
        try:
            out["passed"] = _OPS[op](float(out["value"]), float(thr))
        except (TypeError, ValueError):
            out["ok"] = False
            out["detail"] = {**out["detail"], "reason": f"阈值不可比: {thr!r}"}
    return out


def evaluate_expected_metrics(specs: List[Dict[str, Any]], *, since_ms: int,
                              until_ms: int) -> Dict[str, Any]:
    """求值整张卡的指标组 → {results, all_ok, all_passed, any_failed, verdict}。

    verdict：
      pass        全部有结论且全部达标 → 可 adopted
      fail        有结论但至少一项不达标 → 可 rejected
      inconclusive 至少一项样本不足 → 应 extended（继续观察，不误判）
    """
    results = [evaluate_metric(s, since_ms=since_ms, until_ms=until_ms) for s in (specs or [])]
    if not results:
        return {"results": [], "all_ok": False, "all_passed": False,
                "any_failed": False, "verdict": "inconclusive",
                "reason": "实验卡没有可求值的指标项"}
    all_ok = all(r["ok"] for r in results)
    passed = [r for r in results if r["passed"] is True]
    failed = [r for r in results if r["passed"] is False]
    if not all_ok:
        missing = [f"{r['metric']}@{r['scope']}({(r['detail'] or {}).get('reason')})"
                   for r in results if not r["ok"]]
        verdict, reason = "inconclusive", "样本不足：" + "；".join(missing[:3])
    elif failed:
        verdict = "fail"
        reason = "未达标：" + "；".join(
            f"{r['metric']}@{r['scope']} = {r['value']}（需 {r['op']} {r['threshold']}）" for r in failed[:3])
    else:
        verdict = "pass"
        reason = "全部达标：" + "；".join(
            f"{r['metric']}@{r['scope']} = {r['value']}" for r in passed[:3])
    return {"results": results, "all_ok": all_ok, "all_passed": bool(all_ok and not failed),
            "any_failed": bool(failed), "verdict": verdict, "reason": reason}
