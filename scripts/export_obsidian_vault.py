"""导出量化实验状态到 Obsidian Vault（知识视图，事实源仍在 PG）。

生成：
- 00-实验仪表台.md        ：星图 MOC + 心跳 + 中断告警 + 最新报告
- 01-分析报告/日报/YYYY-MM-DD.md ：当日汇总
- 05-因子实验/{experiment_id}.md ：每个实验的档案
- _bases/实验索引.md      ：全量双链索引

用法：python scripts/export_obsidian_vault.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "obsidian_vault"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db_rows(sql: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(sql), params or {}).mappings().all()
    return [dict(r) for r in rows]


def _load_experiments() -> List[Dict[str, Any]]:
    try:
        return _db_rows(
            "SELECT id, experiment_id, change_desc, switches_json, baseline_json, "
            "status, started_at, completed_at, result_json, verdict, rollback_note, "
            "owner, created_at FROM scalp_experiment_log ORDER BY id"
        )
    except Exception:
        return []


def _load_heartbeats() -> Dict[str, Dict[str, Any]]:
    from backend.services.scalp.scalp_heartbeat import get_heartbeats
    return get_heartbeats()


def _load_candidates() -> List[Dict[str, Any]]:
    try:
        return _db_rows(
            "SELECT symbol, period, factor_set, params_json, metrics_json, "
            "gate_verdict, generated_at FROM pair_strategy_candidates "
            "ORDER BY generated_at DESC LIMIT 50"
        )
    except Exception:
        return []


def _load_bindings() -> List[Dict[str, Any]]:
    try:
        from backend.services.scalp.scalp_bindings import list_bindings
        return list_bindings()
    except Exception:
        return []


def _latest_report(directory: str, pattern: str) -> Path | None:
    d = ROOT / "reports" / directory
    if not d.is_dir():
        return None
    files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    return str(v)


def _json_dump(v: Any) -> str:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return v
    return json.dumps(v or {}, ensure_ascii=False, indent=2)


def main() -> int:
    now = _now()
    today = now.strftime("%Y-%m-%d")
    experiments = _load_experiments()
    heartbeats = _load_heartbeats()
    candidates = _load_candidates()
    bindings = _load_bindings()

    # 中断判定：链路巡检 >15min、日报 >36h
    stale: List[str] = []
    hb_rules = {"scalp_chain_health": 15 * 60, "scalp_daily_health": 36 * 3600,
                "scalp_symbol_profile": 36 * 3600}
    for tid, rule_sec in hb_rules.items():
        hb = heartbeats.get(tid)
        if not hb or not hb.get("last_ok_at"):
            stale.append(tid)
            continue
        try:
            last = datetime.fromisoformat(hb["last_ok_at"])
            if (now - last).total_seconds() > rule_sec:
                stale.append(tid)
        except Exception:
            stale.append(tid)

    dashboard = [
        "---",
        "tags: [quant, dashboard, moc]",
        "---",
        "",
        "# 量化实验仪表台（星图 MOC）",
        "",
        "> 自动生成：%s UTC" % now.isoformat(),
        "",
    ]
    if stale:
        dashboard += ["## ⚠️ 中断/滞后任务", ""]
        for tid in stale:
            dashboard += ["- [[%s]] ❌ 心跳超时" % tid, ""]
    else:
        dashboard += ["## ✅ 心跳正常", ""]
    dashboard += [
        "## 实验状态",
        "",
        "| 实验 | 状态 | 判定 | 创建时间 |",
        "|---|---|---|---|",
    ]
    for e in experiments:
        dashboard.append(
            "| [[%s]] | %s | %s | %s |"
            % (e["experiment_id"], e.get("status"), _fmt(e.get("verdict")),
               _fmt(e.get("created_at")))
        )
    dashboard += ["", "## 候选策略", ""]
    if candidates:
        dashboard += ["| 交易对 | 周期 | 因子集 | 门禁 | 生成时间 |", "|---|---|---|---|---|"]
        for c in candidates:
            dashboard.append(
                "| %s | %s | %s | %s | %s |"
                % (c.get("symbol"), c.get("period"), c.get("factor_set"),
                   c.get("gate_verdict"), _fmt(c.get("generated_at")))
            )
    else:
        dashboard += ["（暂无候选，等待 AI 选币触发快速策略生成器）", ""]
    dashboard += ["", "## 运行中绑定", ""]
    if bindings:
        dashboard += ["| 策略 | 交易对 | 状态 | 停止原因 |", "|---|---|---|---|"]
        for b in bindings:
            dashboard.append(
                "| %s | %s/%s/%s | %s | %s |"
                % (b.get("strategy_id"), b.get("symbol"), b.get("period"),
                   b.get("factor_set"), b.get("status"), b.get("stop_reason") or "-")
            )
    else:
        dashboard += ["（暂无运行中绑定）", ""]
    dashboard += ["", "## 最新报告", ""]
    latest = {
        "每日体检": _latest_report("scalp_daily", "scalp_health_*.md"),
        "成本审计": _latest_report("scalp_cost", "scalp_cost_audit_*.json"),
        "回测": _latest_report("scalp_backtest", "kline_factor_*.json"),
        "真实参数回放": _latest_report("scalp_real_params", "replay_real_*.json"),
    }
    for label, p in latest.items():
        if p:
            dashboard.append("- %s：`%s`" % (label, p.name))
    dashboard += ["", "## 心跳明细", ""]
    for tid, hb in heartbeats.items():
        dashboard.append("- `%s` last_ok=%s status=%s" % (tid, hb.get("last_ok_at"), hb.get("last_status")))
    dashboard += ["", "## 相关入口", "", "- [[实验索引]]", "- [[Agent进化中心]]", ""]

    # 日报
    daily = [
        "---",
        "tags: [quant, daily]",
        "date: %s" % today,
        "---",
        "",
        "# 量化日报 %s" % today,
        "",
        "## 实验/任务心跳",
        "",
    ]
    for tid, hb in heartbeats.items():
        daily.append("- `%s`：%s（%s）" % (tid, hb.get("last_status"), hb.get("last_ok_at")))
    daily += ["", "## 报告", ""]
    for label, p in latest.items():
        if p:
            daily.append("- %s：`%s`" % (label, p.name))
    daily += ["", "## 实验", ""]
    for e in experiments:
        daily.append("- [[%s]] %s" % (e["experiment_id"], e.get("status")))

    # 实验档案
    exp_dir = VAULT / "05-因子实验"
    exp_dir.mkdir(parents=True, exist_ok=True)
    for e in experiments:
        lines = [
            "---",
            "tags: [quant, experiment]",
            "experiment: %s" % e["experiment_id"],
            "status: %s" % e.get("status"),
            "---",
            "",
            "# %s" % e["experiment_id"],
            "",
            "## 改动",
            "",
            e.get("change_desc") or "-",
            "",
            "## 开关",
            "",
            "```json",
            _json_dump(e.get("switches_json")),
            "```",
            "",
            "## 基线",
            "",
            "```json",
            _json_dump(e.get("baseline_json")),
            "```",
            "",
            "## 状态时间线",
            "",
            "- 创建：%s" % _fmt(e.get("created_at")),
            "- 开始：%s" % _fmt(e.get("started_at")),
            "- 完成：%s" % _fmt(e.get("completed_at")),
            "- 判定：%s" % _fmt(e.get("verdict")),
            "- 回滚说明：%s" % _fmt(e.get("rollback_note")),
            "",
        ]
        (exp_dir / ("%s.md" % e["experiment_id"])).write_text(
            "\n".join(lines), encoding="utf-8"
        )

    index = ["# 实验索引", ""]
    for e in experiments:
        index.append("- [[%s]]" % e["experiment_id"])
    index += ["", "## 相关", "", "- [[实验仪表台]]", ""]
    (VAULT / "_bases" / "实验索引.md").write_text("\n".join(index), encoding="utf-8")

    (VAULT / "00-实验仪表台.md").write_text("\n".join(dashboard), encoding="utf-8")
    daily_dir = VAULT / "01-分析报告" / "日报"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / ("%s.md" % today)).write_text("\n".join(daily), encoding="utf-8")
    (exp_dir / "README.md").write_text(
        "# 因子实验\n\n自动生成，勿手改。\n\n- [[实验仪表台]]\n- [[实验索引]]\n",
        encoding="utf-8",
    )
    print("Obsidian 导出完成: experiments=%d heartbeats=%d candidates=%d stale=%s"
          % (len(experiments), len(heartbeats), len(candidates), stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
