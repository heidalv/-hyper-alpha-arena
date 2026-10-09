# -*- coding: utf-8 -*-
"""统一策略 · 周外循环（QuantAgent 外循环范式：thesis准确率 × 因子贡献盈亏 × 模拟实盘偏差）。

[统一策略 2026-09-17·流C] 内循环（factors_lab 每日）负责产出因子；本外循环每周把
三个系统的战绩拉到一张表上对齐，产出"周进化报告"与下周策略倾向指令——
这是"值得延续和学习的统一主题"的载体。
数据纪律：全部只读；RLS 敏感查询走应用内 SessionLocal（与 auto_coin_roi 周报同模式）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "unified_strategy"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def latest_report_path() -> Path:
    reports = sorted(data_dir().glob("weekly_*.json"))
    return reports[-1] if reports else data_dir() / "weekly_none.json"


def _thesis_channel_block(db=None) -> Dict[str, object]:
    """thesis 通道（混合打分 A1 臂）近期战绩：从 score_log 已回填 outcome 的条目统计。"""
    try:
        from backend.services.hybrid_scoring import config as _hcfg

        path = _hcfg.score_log_path()
        if not path.exists():
            return {"available": False}
        hit = n = 0
        by_tier = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            o = e.get("outcome") or {}
            y = o.get("ret_24h")
            if y is None or not (e.get("thesis") or {}).get("present"):
                continue
            if (e.get("arm_fields") or {}).get("A1") is None:
                continue
            n += 1
            if float(y) > 0:
                hit += 1
            tier = (e.get("thesis") or {}).get("tier") or "?"
            t = by_tier.setdefault(str(tier), {"n": 0, "hit": 0})
            t["n"] += 1
            t["hit"] += 1 if float(y) > 0 else 0
        for t in by_tier.values():
            t["hit_rate"] = round(t["hit"] / t["n"], 3) if t["n"] else 0.0
        try:
            # [F320 2026-09-18 复查修复] 原为 `from backend.services.hybrid_scoring import common as _hcommon`
            # —— 该模块不存在，且 _hcommon 在本函数内从未使用；它的 ImportError 会把下面真正的
            # ic 读取一起带进 except ⇒ 周报 ic_thesis_mixed / ic_fused / ic_days 恒为 null（W38 报告实证）。
            ic = json.loads(_hcfg.ic_stats_path().read_text(encoding="utf-8"))
        except Exception:
            ic = {}
        # [2026-09-18] LLM 分析师分数战绩（swing/trend agent 等非因子信号）
        agent_scores = {}
        try:
            if db is not None:
                from backend.services.signal_feedback_tracker import signal_feedback_tracker as _sft
                _all_sig = _sft.analyze_all_signal_contribution(db) or {}
                agent_scores = {k: v for k, v in _all_sig.items()
                                if not k.startswith("factor:") and abs(v) >= 0.001}
        except Exception:
            agent_scores = {}
        # [F320 2026-09-18 复查修复] 原为 `d["agent_scores"] = ...`，而 `d` 在本函数内**从未定义**
        # ⇒ NameError 被下方 except 吞掉 ⇒ 只要 agent_scores 非空就返回坏块
        # （实测 {"available": false, "error": "name 'd' is not defined"}，周报永久坏块）。
        _agent_top = (dict(sorted(agent_scores.items(), key=lambda kv: kv[1])[:10])
                      if agent_scores else {})
        return {"available": True, "n_outcomed": n,
                "hit_rate": round(hit / n, 3) if n else None,
                "by_tier": by_tier, "agent_scores": _agent_top,
                "ic_thesis_mixed": ic.get("ic_thesis_mixed"),
                "ic_fused": ic.get("ic_fused"), "ic_days": ic.get("days")}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:120]}


def _factor_contribution_block(db) -> Dict[str, object]:
    """因子逐单归因（近N日）top/bottom。"""
    try:
        from backend.services.signal_feedback_tracker import signal_feedback_tracker

        contrib = signal_feedback_tracker.analyze_factor_contribution(db) or {}
        if not contrib:
            return {"available": False, "note": "样本不足（<10 单因子投票记录）"}
        ranked = sorted(contrib.items(), key=lambda kv: kv[1])
        return {"available": True, "n_factors": len(contrib),
                "top3": ranked[-3:][::-1], "bottom3": ranked[:3]}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:120]}


def _paper_live_divergence_block() -> Dict[str, object]:
    """模拟 vs 实盘偏差（外循环核心信号之一）。"""
    try:
        p = Path(__file__).resolve().parents[1] / "data" / "paper_live_divergence.json"
        if not p.exists():
            return {"available": False}
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"available": True, "summary": {k: d.get(k) for k in list(d.keys())[:8]}}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:120]}


def _factors_lab_block() -> Dict[str, object]:
    try:
        from backend.services.factors_lab import common as _fcommon, config as _fcfg

        rounds = _fcommon.read_jsonl(_fcfg.rounds_index_path(), limit=3)
        hyps = _fcommon.read_jsonl(_fcfg.hypotheses_path(), limit=500)
        done = [h for h in hyps if isinstance(h.get("outcome"), dict)]
        pass_n = sum(1 for h in done if (h.get("outcome") or {}).get("verdict") == "pass")
        return {"available": True, "recent_rounds": rounds,
                "hypotheses_total": len(hyps), "hypotheses_outcomed": len(done),
                "candidates_passed": pass_n}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:120]}


def _guidance(thesis: Dict, contrib: Dict, diverg: Dict) -> str:
    lines = []
    hr = thesis.get("hit_rate") if thesis.get("available") else None
    if hr is not None:
        lines.append(f"thesis通道命中率{hr:.0%}（{'可信，可加重参考' if hr >= 0.5 else '偏弱，打折扣'}）。")
    if contrib.get("available"):
        top3 = contrib.get("top3") or []
        bot3 = contrib.get("bottom3") or []
        if top3:
            lines.append("正贡献因子：" + "、".join(f"{k}({v:+.1%})" for k, v in top3[:3]) + "。")
        if bot3:
            lines.append("拖累因子：" + "、".join(f"{k}({v:+.1%})" for k, v in bot3[:3]) + "。")
    if diverg.get("available"):
        lines.append("模拟-实盘偏差档案已更新，检查执行层滑点/延迟是否恶化。")
    if not lines:
        lines.append("三表数据仍在积累期，本周无强指令，维持既有纪律。")
    return " ".join(lines)


def _backtest_factor_attr_block(limit: int = 5) -> Dict[str, object]:
    """[F344 2026-09-18 · B4 phase 2 step 3b] 把**回测侧因子归因**接进周报（回测 ↔ 学习 关联）。

    数据源：`live_pipeline_backtest_engine.load_factor_attr()`（append-only JSONL，由
    `BACKTEST_FACTOR_ATTR=1` 的回测写入）。本块与 `_factor_contribution_block`（**线上**逐单归因）
    **并列**呈现——两者口径不同（线上=实盘成交归因；回测=历史回放归因），**差异本身就是对拍信号**，
    不应互相覆盖（报告 §6.4 B4）。
    ⚠️ 回测 `by_name` 是**覆盖度归因**，不是增量 alpha；`attr_note` 字段随数据落盘，本块原样透出。
    """
    try:
        from backend.services.live_pipeline_backtest_engine import load_factor_attr

        recs = load_factor_attr(limit=limit)
        if not recs:
            return {"available": False,
                    "note": "无回测归因记录（需以 BACKTEST_FACTOR_ATTR=1 跑一次回测）"}
        last = recs[-1]
        return {
            "available": True,
            "runs": len(recs),
            "latest": {
                "run_id": last.get("run_id"), "symbol": last.get("symbol"),
                "tier": last.get("tier"), "ts": last.get("ts"),
                "by_dir": last.get("by_dir") or {},
                "by_name_top": dict(list((last.get("by_name") or {}).items())[:8]),
                "attr_note": last.get("attr_note"),
            },
            "note": "回测侧归因；by_name 为覆盖度归因（非增量 alpha），与线上 factor_contribution 并列比对",
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:120]}


def run_weekly() -> Dict[str, object]:
    db = None
    try:
        from backend.database.connection import SessionLocal

        db = SessionLocal()
    except Exception as e:  # noqa: BLE001
        logger.warning("[UnifiedStrategy] DB 会话获取失败（归因块缺席）: %s", str(e)[:120])

    thesis = _thesis_channel_block(db)
    contrib = _factor_contribution_block(db) if db else {"available": False, "error": "no_db"}
    diverg = _paper_live_divergence_block()
    lab = _factors_lab_block()
    bt_attr = _backtest_factor_attr_block()
    guidance = _guidance(thesis, contrib, diverg)

    report = {
        "ok": True, "generated_at": _now_iso(), "week": datetime.now(timezone.utc).isocalendar()[1],
        "thesis_channel": thesis, "factor_contribution": contrib,
        "paper_live_divergence": diverg, "factors_lab": lab,
        # [F344] 回测侧因子归因（与 factor_contribution 并列 ⇒ 回测/实盘可对拍）
        "backtest_factor_attr": bt_attr,
        "guidance": guidance,
    }
    try:
        if db:
            db.close()
    except Exception:
        pass
    out = data_dir() / f"weekly_{datetime.now(timezone.utc).strftime('%G-W%V')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # 指导语同步写 v7 教训池（成功配方/门禁教训通道，让主控 prompt 的 v7 块也能捞到）
    try:
        from backend.services.factors_lab.agent5_feedback import write_v7_lesson

        write_v7_lesson("gate_lesson", f"[统一策略] W{report['week']} 周外循环指导",
                        guidance[:380], quality=0.6)
    except Exception as e:  # noqa: BLE001
        logger.debug("[UnifiedStrategy] v7 写入跳过: %s", str(e)[:100])
    logger.info("[UnifiedStrategy] 周报完成 → %s", out.name)
    return report


def latest() -> Dict[str, object]:
    p = latest_report_path()
    if not p.exists():
        return {"ok": False, "error": "no_report", "hint": "POST /api/unified-strategy/run 生成"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "parse_error"}
