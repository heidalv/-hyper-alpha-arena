# -*- coding: utf-8 -*-
"""[工作流②③] 影子对比报表的"诚实门控"测试（2026-10-04）。

实测输出（本轮）：
```
vol_target: n=0  status=insufficient_samples  min_samples=30
rl:         n=1  status=insufficient_samples  min_samples=30  actions_seen=["hold"]
```
⇒ 样本不足时**只输出样本数与门槛，不产出任何比率/结论**（防止"看着有报表、其实没数据"）。

## 为什么门控重要
本会话已多次踩到"数据假象"：快照只留 8 天、时间口径不一致（本地 naive vs UTC epoch）、
重置删持仓导致只回填 8 条 —— 任何一处都会让"比率"看起来合理但完全错误。
故报表层强制：`n < min_samples` ⇒ 只报样例状态。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/learning_core/shadow_compare.py").read_text(encoding="utf-8")


def test_report_is_read_only():
    """报表不得写库/改配置（只允许 sqlite3 SELECT）。"""
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "commit()", "set_initial_balance("):
        assert forbidden not in SRC, f"报表层不得出现 {forbidden}"


def test_insufficient_samples_gate_returns_no_ratios():
    assert "insufficient_samples" in SRC
    seg = SRC.split("if len(rows) < MIN_VT_SAMPLES:")[1][:600]
    assert "return out" in seg, "样本不足必须提前返回"
    for ratio_key in ("over_budget_ratio", "scale_median", "actual_notional_median"):
        assert ratio_key not in seg, f"样本不足时不得输出 {ratio_key}"


def test_thresholds_are_env_tunable():
    assert "SHADOW_RL_MIN_SAMPLES" in SRC and "SHADOW_VT_MIN_SAMPLES" in SRC
    assert "SHADOW_REPORT_LIMIT" in SRC


def test_pending_metrics_are_declared_not_silently_missing():
    """爆仓/回撤对比在成交积累前无法计算 —— 必须显式声明为 pending。"""
    assert "pending" in SRC
    assert "爆仓次数对比" in SRC and "最大回撤对比" in SRC


def test_report_runs_and_reports_sample_counts():
    """行为级：真实运行必须给出 n / min_samples / status（本会话实测 n=0 与 n=1）。"""
    from backend.services.learning_core.shadow_compare import combined

    res = combined(limit=50)
    for key in ("vol_target", "rl"):
        blk = res[key]
        assert "n" in blk and "min_samples" in blk and "status" in blk, f"{key} 缺字段"
        assert blk["min_samples"] >= 1
        if blk["n"] < blk["min_samples"]:
            assert blk["status"] == "insufficient_samples"
            assert "hint" in blk
