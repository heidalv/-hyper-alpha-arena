# -*- coding: utf-8 -*-
"""实验卡生命周期推进（v3 方向 3，p2-agents-b）。

    proposed ──start──> running ──窗口到期──> evaluating ──求值──> adopted / rejected
                                                            └──样本不足──> extended ──> running

**观察型实验**（`change.apply` 缺省 false）：不改任何生效配置，只是把假设锁进卡片、
到期用真实账本判定。Phase 2 的三个 Agent 只产这一类，所以 `auto_start` 默认开——
自动开始一张不改配置的观察卡没有风险，而让卡片烂在 proposed 才是真问题。

**改配置型实验**（`change.apply=true`）：`auto_start` 一律拒绝，必须人工调
`POST /api/experiments/{id}/start`。这条线不会因为 Agent 升到 advise 就被跨过。

样本不足时判 `extended` 而非 `rejected`：把"没数据"当成"没效果"会误杀好想法。
延长次数上限 `EXPERIMENT_MAX_EXTENSIONS`（默认 3），超限记 rejected 并在 result 里
标 `inconclusive=true`，与"真的没效果"区分开。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def now_ms() -> int:
    return int(time.time() * 1000)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _changes_config(exp: Dict[str, Any]) -> bool:
    change = exp.get("change")
    if isinstance(change, str):
        try:
            change = json.loads(change)
        except Exception:
            change = {}
    return bool((change or {}).get("apply"))


def _extensions(exp: Dict[str, Any]) -> int:
    res = exp.get("result")
    if isinstance(res, str):
        try:
            res = json.loads(res)
        except Exception:
            res = {}
    return int((res or {}).get("extensions") or 0)


def start_experiment(experiment_id: str, *, by: str = "auto", force: bool = False) -> Dict[str, Any]:
    """proposed → running。改配置型实验拒绝自动开始。"""
    from backend.services.analysis import ledgers

    rows = [e for e in ledgers.list_experiments(limit=1000) if e.get("id") == experiment_id]
    if not rows:
        return {"ok": False, "reason": "实验卡不存在"}
    exp = rows[0]
    if str(exp.get("status")) != "proposed":
        return {"ok": False, "reason": f"状态为 {exp.get('status')}，只有 proposed 可以 start"}
    if _changes_config(exp) and not force:
        return {"ok": False, "reason": "该卡会改动生效配置（change.apply=true），必须人工确认后 force=true 才能开始"}
    ok = ledgers.transition_experiment(experiment_id, "running", decided_by=by)
    return {"ok": bool(ok), "id": experiment_id, "status": "running" if ok else exp.get("status")}


def evaluate_experiment(experiment_id: str, *, by: str = "auto") -> Dict[str, Any]:
    """按 expected_metrics 求值并落地终态。可对 running（提前看）/ evaluating 调用。"""
    from backend.services.analysis import ledgers
    from backend.services.experiments.metrics import evaluate_expected_metrics

    rows = [e for e in ledgers.list_experiments(limit=1000) if e.get("id") == experiment_id]
    if not rows:
        return {"ok": False, "reason": "实验卡不存在"}
    exp = rows[0]
    started = int(exp.get("started_ms") or exp.get("created_ms") or 0)
    ends = int(exp.get("ends_ms") or now_ms())
    specs = exp.get("expected_metrics") or []
    if isinstance(specs, str):
        try:
            specs = json.loads(specs)
        except Exception:
            specs = []

    ev = evaluate_expected_metrics(specs, since_ms=started, until_ms=min(ends, now_ms()))
    verdict = ev["verdict"]
    extensions = _extensions(exp)
    max_ext = _env_int("EXPERIMENT_MAX_EXTENSIONS", 3)

    if verdict == "pass":
        status, decision = "adopted", ev["reason"]
    elif verdict == "fail":
        status, decision = "rejected", ev["reason"]
    elif extensions >= max_ext:
        status = "rejected"
        decision = f"延长 {extensions} 次仍无足够样本，按无结论结案：{ev['reason']}"
    else:
        status = "extended"
        decision = f"样本不足，延长第 {extensions + 1} 次观察：{ev['reason']}"

    result = {
        "verdict": verdict,
        "inconclusive": verdict == "inconclusive",
        "extensions": extensions + (1 if status == "extended" else 0),
        "window": {"since_ms": started, "until_ms": min(ends, now_ms())},
        "metrics": ev["results"],
        "evaluated_ms": now_ms(),
    }
    ok = ledgers.transition_experiment(experiment_id, status, result=result,
                                       decision=decision[:500], decided_by=by)
    # extended 需要重新开跑：再置 running 会刷新 started/ends 窗口
    if ok and status == "extended":
        ledgers.transition_experiment(experiment_id, "running", decided_by=by)
    return {"ok": bool(ok), "id": experiment_id, "status": status,
            "verdict": verdict, "decision": decision, "metrics": ev["results"]}


def advance_experiments(*, auto_start: Optional[bool] = None, limit: int = 200) -> Dict[str, Any]:
    """定时任务：把所有卡片各推进一步。

      proposed  → running     （仅观察型；`EXPERIMENT_AUTO_START` 可关）
      running   → evaluating  （窗口到期）
      evaluating→ 终态        （求值）
    """
    from backend.core.tenant import set_system_identity
    from backend.services.analysis import ledgers

    set_system_identity()
    auto = _env_true("EXPERIMENT_AUTO_START", True) if auto_start is None else bool(auto_start)
    out: Dict[str, Any] = {"started": [], "to_evaluating": [], "decided": [],
                           "skipped_manual": [], "errors": []}
    now = now_ms()

    try:
        cards = ledgers.list_experiments(limit=limit)
    except Exception as exc:
        out["errors"].append(f"list_experiments: {exc}")
        return out

    for exp in cards:
        eid, status = exp.get("id"), str(exp.get("status"))
        try:
            if status == "proposed":
                if not auto:
                    continue
                if _changes_config(exp):
                    out["skipped_manual"].append(eid)
                    continue
                if start_experiment(eid, by="scheduler").get("ok"):
                    out["started"].append(eid)

            elif status == "running":
                ends = int(exp.get("ends_ms") or 0)
                if ends and now >= ends:
                    if ledgers.transition_experiment(eid, "evaluating", decided_by="scheduler"):
                        out["to_evaluating"].append(eid)
                        r = evaluate_experiment(eid, by="scheduler")
                        out["decided"].append({"id": eid, "status": r.get("status"),
                                               "verdict": r.get("verdict")})

            elif status == "evaluating":
                r = evaluate_experiment(eid, by="scheduler")
                out["decided"].append({"id": eid, "status": r.get("status"),
                                       "verdict": r.get("verdict")})
        except Exception as exc:
            logger.warning("[experiments.lifecycle] 推进 %s 失败: %s", eid, exc)
            out["errors"].append(f"{eid}: {str(exc)[:150]}")

    if out["started"] or out["decided"]:
        logger.info("[experiments] 推进：开始 %d、判定 %d、待人工 %d",
                    len(out["started"]), len(out["decided"]), len(out["skipped_manual"]))
    return out


def summary(days: int = 90) -> Dict[str, Any]:
    """看板用：按状态与来源的实验卡统计 + 采纳率。"""
    from backend.services.analysis import ledgers

    cards = ledgers.list_experiments(limit=1000)
    since = now_ms() - days * 86400000
    recent = [c for c in cards if int(c.get("created_ms") or 0) >= since]
    by_status: Dict[str, int] = {}
    for c in recent:
        s = str(c.get("status"))
        by_status[s] = by_status.get(s, 0) + 1
    decided = by_status.get("adopted", 0) + by_status.get("rejected", 0)
    return {
        "days": days, "n": len(recent), "by_status": by_status,
        "adoption_rate": (by_status.get("adopted", 0) / decided) if decided else None,
        "by_source": ledgers.experiment_source_stats(days),
        "auto_start": _env_true("EXPERIMENT_AUTO_START", True),
        "max_extensions": _env_int("EXPERIMENT_MAX_EXTENSIONS", 3),
    }
