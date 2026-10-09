# -*- coding: utf-8 -*-
"""⑤ 反馈 agent —— 归因 → 写经验记忆 → 修正下一轮；多样性体检（ADR-21 评估）。

记忆两处：v7_lessons（sqlite 直插 wrapper，与进化循环共享教训池）+
factors_lab 假设 outcome 回填（agent② 下轮检索用）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from backend.services.factors_lab import agent4_backtester, common, config

logger = logging.getLogger(__name__)

_V7_DB = Path(__file__).resolve().parents[2] / "data" / "factor_evolution_memory_v7.db"


def write_v7_lesson(kind: str, title: str, summary: str, quality: float = 0.5,
                    period: str = "4h") -> bool:
    """v7_lessons 直插（增量模块纪律：不改 schema，只按既有列写入）。"""
    try:
        con = sqlite3.connect(str(_V7_DB), timeout=10)
        try:
            con.execute(
                "INSERT OR IGNORE INTO v7_lessons "
                "(created_at, kind, cycle, period, title, summary, report_json, quality, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'active')",
                (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), kind, "L", period,
                 title[:120], summary[:400], "{}", float(quality)),
            )
            con.commit()
            return True
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorsLab⑤] v7 教训写入失败: %s", str(e)[:120])
        return False


def _verdict(rep: Dict[str, object]) -> Dict[str, str]:
    """归因分类（AlphaAgent 失败模式 + 门禁阈值）。"""
    if not rep.get("ok"):
        return {"verdict": "fail", "reason": str(rep.get("reason") or "unknown")[:80],
                "mode": "backtest_fail"}
    ic = abs(float(rep.get("mean_rank_ic") or 0.0))
    if ic < config.ic_pass_threshold():
        return {"verdict": "reject", "mode": "weak_ic",
                "reason": f"|RankIC|={ic:.4f} < {config.ic_pass_threshold()}"}
    if float(rep.get("turnover") or 0) > 0.9:
        return {"verdict": "reject", "mode": "hot_turnover",
                "reason": f"turnover={rep.get('turnover')}"}
    return {"verdict": "pass", "mode": "pass",
            "reason": f"|RankIC|={ic:.4f} ICIR={rep.get('icir')} spread={rep.get('quantile_spread')}"}


def _max_corr_with_accepted(ast: dict) -> Optional[float]:
    """与已 pass 候选的价值序列最大相关（同宇宙重算太贵 → 用 AST 结构近似 + 相关留接口）。

    v1 用 AST 相似度作 crowded 代理（>阈值判 crowded）；真实价值序列相关在多样性体检里算。
    """
    from backend.services.factors_lab.agent3_engineer import ast_similarity
    acc = [c for c in common.read_jsonl(config.candidates_path(), limit=120)
           if c.get("verdict") == "pass" and c.get("ast")]
    if not acc:
        return None
    return max(ast_similarity(ast, c["ast"]) for c in acc)


def attribute_and_remember(round_results: List[Dict[str, object]]) -> Dict[str, object]:
    """对一轮的全部候选：判定 → 回填候选/假设 → 写 v7 教训 → 下轮指导。"""
    hyps = {h.get("hyp_id"): h for h in common.read_jsonl(config.hypotheses_path(), limit=200)}
    stats = {"pass": 0, "reject": 0, "fail": 0}
    lessons: List[str] = []

    # 逐候选判定（含 crowded 检查）
    for r in round_results:
        cand = r.get("cand") or {}
        v = _verdict(r.get("report") or {})
        if v["verdict"] == "pass" and isinstance(cand.get("ast"), dict):
            mc = _max_corr_with_accepted(cand["ast"])
            if mc is not None and mc > config.crowded_corr_threshold():
                v = {"verdict": "reject", "mode": "crowded",
                     "reason": f"与已接受因子 AST 相似 {mc:.2f} > {config.crowded_corr_threshold()}"}
        r["verdict"] = v
        stats[v["verdict"]] = stats.get(v["verdict"], 0) + 1
        # 回填候选文件最后一条同 id 记录
        _update_candidate(cand.get("cand_id"), {"verdict": v["verdict"], "mode": v["mode"]})
        h = hyps.get(cand.get("hyp_id"))
        if h:
            _update_hypothesis(cand.get("hyp_id"), {"verdict": v["verdict"], "mode": v["mode"],
                                                    "reason": v["reason"]})
        lessons.append(f"{cand.get('expr_id')}:{v['mode']}({v['reason'][:40]})")

    # 经验记忆
    if stats.get("pass"):
        write_v7_lesson("success_recipe", f"[factors_lab] 通过候选 x{stats['pass']}",
                        "；".join(lessons[:6]), quality=0.6)
    if stats.get("reject"):
        modes = [r["verdict"]["mode"] for r in round_results if r["verdict"]["verdict"] == "reject"]
        write_v7_lesson("gate_lesson", f"[factors_lab] 拒绝 {len(modes)} 例（{','.join(sorted(set(modes)))}）",
                        "；".join(lessons[:8]), quality=0.5)
    if stats.get("fail"):
        write_v7_lesson("failure_case", f"[factors_lab] 失败 {stats['fail']} 例（编译/单测/回测）",
                        "；".join(lessons[:8]), quality=0.4)

    # 下轮指导（规则版；LLM 增强留接口）
    modes = [r["verdict"]["mode"] for r in round_results]
    if modes.count("weak_ic") >= max(1, len(modes) // 2):
        guidance = "多数候选 IC 不足：下一轮偏向更长窗口/横截面异质性更强的机制（流量-订单簿/资金费率类），避免纯价量动量变体。"
    elif modes.count("crowded") >= 2:
        guidance = "拥挤拒收偏多：下一轮显式规避与现有池高结构重叠的算子组合（ts_rank/ts_mean 链），尝试 corr/cov/argmax 族。"
    elif stats.get("pass"):
        guidance = "存在通过候选：下一轮在通过机制的正交方向延伸（不同数据字段或不同窗口尺度），而非同族加密。"
    else:
        guidance = "按当前假设池分布自由探索，注意与近 30 天假设的异质性。"
    common.append_jsonl(config.guidance_path(), {"ts": time.time(), "guidance": guidance,
                                                 "round_stats": stats})
    logger.info("[FactorsLab⑤] 归因完成 %s", stats)
    return {"stats": stats, "guidance": guidance}


def _rewrite_jsonl(path, updater, key_field: str) -> None:
    rows = common.read_jsonl(path)
    changed = False
    for r in rows:
        new = updater(r)
        if new:
            r.update(new)
            changed = True
    if changed:
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")


def _update_candidate(cand_id, patch: Dict) -> None:
    if not cand_id:
        return
    _rewrite_jsonl(config.candidates_path(),
                   lambda r: patch if r.get("cand_id") == cand_id else None, "cand_id")


def _update_hypothesis(hyp_id, patch: Dict) -> None:
    if not hyp_id:
        return
    outcome = {"verdict": patch.get("verdict"), "mode": patch.get("mode"),
               "reason": patch.get("reason")}

    def _upd(r):
        if r.get("hyp_id") == hyp_id:
            return {"outcome": outcome}
        return None

    _rewrite_jsonl(config.hypotheses_path(), _upd, "hyp_id")


def diversity_report() -> Dict[str, object]:
    """多样性体检（ADR-21 评估）：AST 相似分布 + 假设簇纯度 + 价值序列相关（pass 候选）。"""
    from backend.services.factors_lab.agent3_engineer import ast_similarity

    cands = common.read_jsonl(config.candidates_path(), limit=200)
    acc = [c for c in cands if c.get("verdict") == "pass" and c.get("ast")]
    sims: List[float] = []
    for i in range(len(acc)):
        for j in range(i + 1, len(acc)):
            sims.append(round(ast_similarity(acc[i]["ast"], acc[j]["ast"]), 3))
    hyps = [h for h in common.read_jsonl(config.hypotheses_path(), limit=100) if h.get("hypothesis")]
    clusters: Dict[str, int] = {}
    for h in hyps:
        text = f"{h.get('hypothesis')} {h.get('diversity_note')}"
        key = "动量" if any(k in text for k in ("动量", "momentum", "趋势")) else (
              "反转" if any(k in text for k in ("反转", "回归", "reversal")) else (
              "波动" if any(k in text for k in ("波动", "vol")) else "其他"))
        clusters[key] = clusters.get(key, 0) + 1
    # 价值序列相关（重算 pass 候选的因子序列两两相关，宇宙缩到 12 控成本）
    val_corr_max = None
    try:
        if len(acc) >= 2:
            universe = agent4_backtester._universe(12)
            series = {}
            for c in acc[:6]:
                panel = agent4_backtester.gpfactor(c["ast"], universe, bars=200)
                if not panel.empty:
                    series[c["expr_id"]] = panel.groupby(level="date")["factor"].mean()
            if len(series) >= 2:
                df = pd.DataFrame(series)
                cm = df.corr().abs().values
                tri = [cm[i][j] for i in range(len(series)) for j in range(i + 1, len(series))]
                val_corr_max = round(max(tri), 3) if tri else None
    except Exception as e:  # noqa: BLE001
        logger.debug("[FactorsLab⑤] 价值序列相关计算跳过: %s", str(e)[:100])
    total = sum(clusters.values()) or 1
    purity = max(clusters.values()) / total if clusters else 0.0
    return {
        "ts": time.time(),
        "accepted_factors": len(acc),
        "ast_pair_sims": sims[:20],
        "ast_sim_max": max(sims) if sims else None,
        "hypothesis_clusters": clusters,
        "hypothesis_purity": round(purity, 3),
        "value_series_corr_max": val_corr_max,
        "verdict": ("healthy" if (purity < 0.6 and (val_corr_max is None or val_corr_max < 0.85))
                    else "attention"),
    }

