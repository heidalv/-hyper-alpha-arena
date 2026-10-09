# -*- coding: utf-8 -*-
"""闭环编排 —— 假设→实现→验证→记忆 无人值守循环（状态机 + 预算 + 轮报告）。

阶段：LITERATURE(scan) → HYPOTHESIZE → ENGINEER → BACKTEST → ATTRIBUTE/MEMORY
预算：假设数 / 每假设候选数 / 轮超时 config.round_timeout_sec()
失败处理：单阶段异常降级继续（缺文献→无知识卡上下文；LLM 缺席→该阶段产出空并记录）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from backend.services.factors_lab import (
    agent1_literature, agent2_hypothesis, agent3_engineer, agent4_backtester,
    agent5_feedback, common, config,
)

logger = logging.getLogger(__name__)

_running = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_round(*, scan: bool = True, force: bool = False) -> Dict[str, object]:
    """一轮完整闭环。返回轮报告（同时落盘 round_reports/）。"""
    global _running
    if _running and not force:
        return {"ok": False, "error": "busy"}
    _running = True
    t0 = time.time()
    try:
        report: Dict[str, object] = {"ok": True, "round_ts": time.time(), "iso": _now_iso(),
                                     "stages": {}, "timeout_sec": config.round_timeout_sec()}

        # ① LITERATURE
        if scan:
            report["stages"]["literature"] = agent1_literature.scan_arxiv()
        else:
            report["stages"]["literature"] = {"ok": True, "skipped": "scan=False"}

        # ② HYPOTHESIZE
        hyp_res = agent2_hypothesis.generate()
        hypotheses: List[Dict] = hyp_res.get("hypotheses") or []
        report["stages"]["hypothesize"] = {k: hyp_res.get(k) for k in ("ok", "accepted", "rejected")}
        if not hypotheses:
            report["ok"] = False
            report["error"] = "no_hypotheses"
            return _finish(report, t0)

        # ③ ENGINEER + ④ BACKTEST（逐假设逐候选）
        results: List[Dict[str, object]] = []
        for hyp in hypotheses:
            if time.time() - t0 > config.round_timeout_sec():
                report["stages"]["budget"] = "round_timeout"
                break
            eng = agent3_engineer.realize(hyp)
            for cand in eng.get("candidates") or []:
                rep = agent4_backtester.evaluate_candidate(cand)
                results.append({"cand": cand, "report": rep})
        report["stages"]["engineer"] = {
            "candidates": len(results),
            "unit_pass": sum(1 for r in results if r["cand"].get("status") == "unit_pass"),
        }
        report["stages"]["backtest"] = {
            "evaluated": sum(1 for r in results if r["report"].get("ok")),
            "best_ic": max([abs(float(r["report"].get("mean_rank_ic") or 0))
                            for r in results if r["report"].get("ok")] or [0.0]),
        }

        # ⑤ ATTRIBUTE / MEMORY
        fb = agent5_feedback.attribute_and_remember(results)
        report["stages"]["feedback"] = fb

        report["results"] = [
            {"cand_id": r["cand"].get("cand_id"), "expr_id": r["cand"].get("expr_id"),
             "hyp": str((r["cand"].get("hyp_id"))), "note": r["cand"].get("note"),
             "unit": r["cand"].get("status"),
             **{k: (r["report"].get(k) if isinstance(r["report"], dict) else None)
                for k in ("ok", "mean_rank_ic", "icir", "quantile_spread", "turnover",
                          "ic_half_life_bars", "reason")},
             "verdict": r.get("verdict")}
            for r in results
        ]
        return _finish(report, t0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorsLab] 轮异常: %s", str(e)[:200])
        return {"ok": False, "error": f"round_error: {str(e)[:160]}",
                "round_ts": time.time(), "iso": _now_iso()}
    finally:
        _running = False


def _finish(report: Dict[str, object], t0: float) -> Dict[str, object]:
    report["elapsed_sec"] = round(time.time() - t0, 1)
    try:
        fname = config.reports_dir() / f"round_{int(report['round_ts'])}.json"
        fname.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")
        common.append_jsonl(config.rounds_index_path(), {
            "round_ts": report["round_ts"], "ok": report.get("ok"),
            "elapsed_sec": report.get("elapsed_sec"),
            "stats": (report.get("stages") or {}).get("feedback", {}).get("stats"),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorsLab] 轮报告落盘失败: %s", str(e)[:120])
    logger.info("[FactorsLab] 轮完成 ok=%s elapsed=%ss", report.get("ok"), report.get("elapsed_sec"))
    return report


def status() -> Dict[str, object]:
    rounds = common.read_jsonl(config.rounds_index_path(), limit=5)
    latest = {}
    try:
        reports = sorted(config.reports_dir().glob("round_*.json"))
        if reports:
            latest = json.loads(reports[-1].read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "enabled": config.enabled(),
        "period": config.PANEL_PERIOD,
        "counts": {
            "knowledge_cards": len(common.read_jsonl(config.knowledge_path())),
            "hypotheses": len(common.read_jsonl(config.hypotheses_path())),
            "candidates": len(common.read_jsonl(config.candidates_path())),
        },
        "recent_rounds": rounds,
        "latest_round_ts": latest.get("round_ts"),
        "latest_round_stats": (latest.get("stages") or {}).get("feedback", {}).get("stats"),
    }
