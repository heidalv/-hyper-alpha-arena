# -*- coding: utf-8 -*-
"""Agent 群的注册、评分器挂载与定时任务（v3 方向 3，p1-agents-a）。

三件事：
  1. `ensure_registered()`   把三个 Agent 挂进 base 的工厂注册表（进程内幂等）
  2. `ensure_evaluators()`   把 kind → outcome 评估器注册到 analysis.ledgers，
                             这样 `score_due()`（每 15 分钟）能给 Agent 预测打分。
                             **必须在 score_due 之前执行**，因此 analysis/scheduling.py 的评分任务
                             也会调用它一次（双保险：即使 AGENTS_ENABLED=false 也要能评分历史预测）。
  3. `register_agent_jobs()` 注册三个 Agent 的定时任务（非峰时；确定性核心不调 LLM，成本≈0）

节奏（北京时间）：
  agent_anomaly        每 30 分钟      异常需要及时，但只产出建议不落地
  agent_signal_review  每日 08:00      紧随日度简报（07:00），复盘前一日新评分的信号
  agent_timing         周一 05:30      紧随双模型择时（05:00），给出下一周 regime 与三桶建议
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_registered = False
_evaluators_ready = False
_lock = threading.Lock()

SIGNAL_REVIEW_HOUR_LOCAL = 8
TIMING_HOUR_LOCAL = 5
TIMING_MINUTE_LOCAL = 30
ANOMALY_INTERVAL_SEC = int(__import__("os").getenv("AGENT_ANOMALY_INTERVAL_SEC", "900"))
# p2-agents-b：都排在非峰时，且彼此错开半小时，避免同时抢 DB
EVENT_IMPACT_HOUR_LOCAL = 6
EXEC_QA_HOUR_LOCAL = 7
PARAM_SEARCH_HOUR_LOCAL = 4          # 周日，最重（要跑多组回测）
EXPERIMENT_INTERVAL_SEC = 3600


def agents_enabled() -> bool:
    from backend.services.agents.base import env_true

    return env_true("AGENTS_ENABLED", True)


def ensure_registered() -> List[str]:
    """把三个 Agent 挂进注册表（幂等）。"""
    global _registered
    if _registered:
        from backend.services.agents.base import registered_agents

        return registered_agents()
    with _lock:
        if not _registered:
            from backend.services.agents import (
                anomaly_agent,
                event_impact,
                execution_qa,
                param_search,
                signal_review,
                timing_agent,
            )
            from backend.services.agents.base import register_agent

            register_agent(signal_review.AGENT_ID, signal_review.build)
            register_agent(anomaly_agent.AGENT_ID, anomaly_agent.build)
            register_agent(timing_agent.AGENT_ID, timing_agent.build)
            # p2-agents-b
            register_agent(event_impact.AGENT_ID, event_impact.build)
            register_agent(param_search.AGENT_ID, param_search.build)
            register_agent(execution_qa.AGENT_ID, execution_qa.build)
            _registered = True
    from backend.services.agents.base import registered_agents

    return registered_agents()


ALL_KINDS = ["source_edge", "trading_state", "regime", "event_impact", "param_shift", "exec_quality"]


def ensure_evaluators() -> List[str]:
    """注册 kind → outcome 评估器（幂等）。返回已注册的 kind 列表。"""
    global _evaluators_ready
    if _evaluators_ready:
        return list(ALL_KINDS)
    with _lock:
        if not _evaluators_ready:
            try:
                from backend.services.agents import (
                    anomaly_agent,
                    event_impact,
                    execution_qa,
                    param_search,
                    signal_review,
                    timing_agent,
                )
                from backend.services.analysis.ledgers import register_outcome_evaluator

                register_outcome_evaluator(signal_review.KIND_SOURCE_EDGE, signal_review.score_source_edge)
                register_outcome_evaluator(anomaly_agent.KIND_TRADING_STATE, anomaly_agent.score_trading_state)
                register_outcome_evaluator(timing_agent.KIND_REGIME, timing_agent.score_regime)
                register_outcome_evaluator(event_impact.KIND_EVENT_IMPACT, event_impact.score_event_impact)
                register_outcome_evaluator(param_search.KIND_PARAM_SHIFT, param_search.score_param_shift)
                register_outcome_evaluator(execution_qa.KIND_EXEC_QUALITY, execution_qa.score_exec_quality)
                _evaluators_ready = True
                logger.info("[agents] 到期评分器已注册: %s", " / ".join(ALL_KINDS))
            except Exception as exc:
                logger.warning("[agents] 评分器注册失败: %s", exc)
                return []
    return list(ALL_KINDS)


# ─────────────────────────── 运行入口 ───────────────────────────
def run_agent(agent_id: str, *, dry_run: bool = False, mode: Optional[str] = None) -> Dict[str, Any]:
    """跑单个 Agent。mode 用于人工临时覆盖（仍受 max_mode 与可信度门限制）。"""
    ensure_registered()
    ensure_evaluators()
    from backend.services.agents.base import get_agent

    agent = get_agent(agent_id)
    if agent is None:
        return {"agent": agent_id, "ok": False, "error": "未注册的 Agent"}
    if mode:
        from backend.services.agents.base import min_mode, parse_mode

        agent.configured_mode = min_mode(parse_mode(mode, agent.configured_mode), agent.max_mode)
    return agent.run(dry_run=dry_run).to_dict()


def run_all_agents(*, dry_run: bool = False) -> Dict[str, Any]:
    ensure_registered()
    from backend.services.agents.base import registered_agents

    out: Dict[str, Any] = {}
    for aid in registered_agents():
        try:
            out[aid] = run_agent(aid, dry_run=dry_run)
        except Exception as exc:
            logger.exception("[agents] %s 运行失败", aid)
            out[aid] = {"agent": aid, "ok": False, "error": str(exc)[:200]}
    return {"ok": True, "agents": out}


def _summary(res: Dict[str, Any]) -> Dict[str, Any]:
    return {k: res.get(k) for k in
            ("agent", "ok", "mode", "configured_mode", "downgraded", "downgrade_reason",
             "predictions", "advice", "errors", "elapsed_sec")}


def scheduled_signal_review() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("signal_review")
    logger.info("[agents] signal_review mode=%s preds=%d advice=%d",
                res.get("mode"), len(res.get("predictions") or []), len(res.get("advice") or []))
    return _summary(res)


def scheduled_anomaly() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("anomaly")
    f = res.get("findings") or {}
    logger.info("[agents] anomaly stress=%s suggested=%s coverage=%s",
                f.get("stress_score"), f.get("suggested_state"), f.get("channel_coverage"))
    out = _summary(res)
    out.update({"stress_score": f.get("stress_score"), "suggested_state": f.get("suggested_state"),
                "current_state": f.get("current_state"), "hard_triggers": f.get("hard_triggers")})
    return out


def scheduled_timing() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("timing")
    f = res.get("findings") or {}
    logger.info("[agents] timing regime=%s buckets=%s", f.get("regime"), f.get("bucket_weights"))
    out = _summary(res)
    out.update({"regime": f.get("regime"), "bucket_weights": f.get("bucket_weights")})
    return out


def scheduled_event_impact() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("event_impact")
    f = res.get("findings") or {}
    logger.info("[agents] event_impact 类型=%s 显著=%s 翻转=%s",
                f.get("n_types"), len(f.get("significant") or []), len(f.get("flips") or []))
    out = _summary(res)
    out.update({"n_types": f.get("n_types"), "n_significant": len(f.get("significant") or []),
                "flips": f.get("flips"), "experiments": res.get("experiments")})
    return out


def scheduled_param_search() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("param_search")
    f = res.get("findings") or {}
    logger.info("[agents] param_search 候选=%s", f.get("n_candidates"))
    out = _summary(res)
    out.update({"n_candidates": f.get("n_candidates"), "experiments": res.get("experiments")})
    return out


def scheduled_execution_qa() -> Dict[str, Any]:
    if not agents_enabled():
        return {"skipped": True, "reason": "AGENTS_ENABLED=false"}
    res = run_agent("execution_qa")
    f = res.get("findings") or {}
    logger.info("[agents] execution_qa 可用账户=%s 问题=%s",
                f.get("n_usable"), len(f.get("issues") or []))
    out = _summary(res)
    out.update({"n_usable": f.get("n_usable"), "issues": f.get("issues"),
                "experiments": res.get("experiments")})
    return out


def scheduled_experiment_advance() -> Dict[str, Any]:
    """实验卡生命周期推进。**与 AGENTS_ENABLED 解耦**：已经落库的卡片必须走完，
    不能因为关掉 Agent 就永远挂在 running。"""
    from backend.services.experiments.lifecycle import advance_experiments

    out = advance_experiments()
    if out.get("started") or out.get("decided"):
        logger.info("[experiments] 开始 %d、判定 %d", len(out["started"]), len(out["decided"]))
    return out


# ─────────────────────────── 定时任务注册 ───────────────────────────
def register_agent_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    """由 analysis/scheduling.py 的扩展注册链调用。评分器无论开关都会注册。"""
    ensure_registered()
    ensure_evaluators()
    registered: List[str] = []
    if not agents_enabled():
        logger.info("[agents] AGENTS_ENABLED=false，跳过定时任务注册（评分器仍已挂载）")
        return registered

    from backend.services.analysis.scheduling import off_peak_cron

    try:
        job("agent_anomaly", f"interval {ANOMALY_INTERVAL_SEC}s",
            "Anomaly Agent：六通道 z-score/CUSUM → stress 与 TradingState 建议（观察模式不生效）",
            owner="agents", runner=scheduled_anomaly, expected_interval_sec=ANOMALY_INTERVAL_SEC)
        task_scheduler.add_interval_task(
            task_func=wrap("agent_anomaly", scheduled_anomaly),
            interval_seconds=ANOMALY_INTERVAL_SEC, task_id="v3_agent_anomaly", max_instances=1,
        )
        registered.append("agent_anomaly")
    except Exception as exc:
        logger.warning("[agents] agent_anomaly 注册失败: %s", exc)

    try:
        cron = off_peak_cron(SIGNAL_REVIEW_HOUR_LOCAL, 0)
        job("agent_signal_review", f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ {SIGNAL_REVIEW_HOUR_LOCAL}:00)",
            "SignalReview Agent：各信号源命中率/IC/半衰期/regime 分层 → source_edge 预测与权重假设",
            owner="agents", runner=scheduled_signal_review, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("agent_signal_review", scheduled_signal_review),
            task_id="v3_agent_signal_review", hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("agent_signal_review")
    except Exception as exc:
        logger.warning("[agents] agent_signal_review 注册失败: %s", exc)

    # [2026-09-05] agent_timing 与 analysis_timing 重复，主脑改造后停观察侧，不再注册。
    logger.info("[agents] agent_timing 已停用（与 analysis_timing 重复，不再注册）")

    # ── p2-agents-b：EventImpact / ParamSearch / ExecutionQA ──
    try:
        cron_e = off_peak_cron(EVENT_IMPACT_HOUR_LOCAL, 30)
        job("agent_event_impact", f"cron {cron_e['hour']:02d}:{cron_e['minute']:02d} (BJ {EVENT_IMPACT_HOUR_LOCAL}:30)",
            "EventImpact Agent：事件冲击显著性跟踪与翻转检测 → event_impact 预测 + 接入/下架实验卡",
            owner="agents", runner=scheduled_event_impact, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("agent_event_impact", scheduled_event_impact),
            task_id="v3_agent_event_impact", hour=cron_e["hour"], minute=cron_e["minute"],
        )
        registered.append("agent_event_impact")
    except Exception as exc:
        logger.warning("[agents] agent_event_impact 注册失败: %s", exc)

    try:
        from backend.config.settings import AGENT_PARAM_SEARCH_ENABLED
        if AGENT_PARAM_SEARCH_ENABLED:
            cron_p = off_peak_cron(PARAM_SEARCH_HOUR_LOCAL, 0, day_of_week="sun")
            job("agent_param_search", f"cron sun {cron_p['hour']:02d}:{cron_p['minute']:02d} (BJ {PARAM_SEARCH_HOUR_LOCAL}:00)",
                "ParamSearch Agent：样本外切分 + Bonferroni 校正的参数扫描 → param_shift 预测 + 参数实验卡",
                owner="agents", runner=scheduled_param_search, expected_interval_sec=604800)
            kw_p = {"task_func": wrap("agent_param_search", scheduled_param_search),
                    "task_id": "v3_agent_param_search", "hour": cron_p["hour"], "minute": cron_p["minute"]}
            if cron_p.get("day_of_week"):
                kw_p["day_of_week"] = cron_p["day_of_week"]
            task_scheduler.add_cron_task(**kw_p)
            registered.append("agent_param_search")
        else:
            logger.info("[agents] agent_param_search 已停用（AGENT_PARAM_SEARCH_ENABLED=false）")
    except Exception as exc:
        logger.warning("[agents] agent_param_search 注册失败: %s", exc)

    try:
        cron_q = off_peak_cron(EXEC_QA_HOUR_LOCAL, 15)
        job("agent_execution_qa", f"cron {cron_q['hour']:02d}:{cron_q['minute']:02d} (BJ {EXEC_QA_HOUR_LOCAL}:15)",
            "ExecutionQA Agent：滑点 / 拒单率 / 部分成交 / 实际费率体检 → exec_quality 预测 + 治理实验卡",
            owner="agents", runner=scheduled_execution_qa, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("agent_execution_qa", scheduled_execution_qa),
            task_id="v3_agent_execution_qa", hour=cron_q["hour"], minute=cron_q["minute"],
        )
        registered.append("agent_execution_qa")
    except Exception as exc:
        logger.warning("[agents] agent_execution_qa 注册失败: %s", exc)

    logger.info("[agents] 定时任务已注册: %s", registered)
    return registered


def register_experiment_jobs(task_scheduler, wrap: Callable[..., Any], job: Callable[..., Any]) -> List[str]:
    """实验卡生命周期任务。**独立于 AGENTS_ENABLED 注册**——已落库的卡片必须能走完一生。"""
    registered: List[str] = []
    try:
        job("experiment_advance", f"interval {EXPERIMENT_INTERVAL_SEC}s",
            "实验卡生命周期：proposed→running→evaluating→adopted/rejected/extended（按 expected_metrics 真实求值）",
            owner="agents", runner=scheduled_experiment_advance,
            expected_interval_sec=EXPERIMENT_INTERVAL_SEC)
        task_scheduler.add_interval_task(
            task_func=wrap("experiment_advance", scheduled_experiment_advance),
            interval_seconds=EXPERIMENT_INTERVAL_SEC, task_id="v3_experiment_advance", max_instances=1,
        )
        registered.append("experiment_advance")
    except Exception as exc:
        logger.warning("[agents] experiment_advance 注册失败: %s", exc)
    return registered
