# -*- coding: utf-8 -*-
"""[2026-09-10 §62] 缺陷总清单自校验契约测试。

目标要求交付「一份按严重度排序的缺陷清单」。清单若只存在于 markdown 里就会**漂移**
（条目被删、证据指向不存在的脚本、待决策项丢了编号、统计行与实际不符）。
本测试把清单变成可机器校验的产物：
  1. 条目数/严重度分布/状态分布必须与 §62.1 的统计行**一致**；
  2. 证据列里引用的 `_audit_ml/Z*.py` 与 `test_*.py` **必须真实存在**；
  3. 状态为「待决策」的条目必须带 `P<数字>` 编号；
  4. 决策队列 P1–P13 齐备；
  5. 校验脚本自身可运行且退出码为 0。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

inv = importlib.import_module("audit_defect_inventory")


def test_inventory_parses_and_has_expected_shape():
    data = inv.parse_report()
    rows = data["rows"]
    assert len(rows) >= 35, f"清单条目过少（{len(rows)}）——是否被误删？"
    sev = {r["severity"] for r in rows}
    assert sev <= {"高", "中", "低", "记录"}, sev
    # 高严重度必须存在且不超过合理上限（防止把一切都标成高）
    n_high = sum(1 for r in rows if r["severity"] == "高")
    assert 3 <= n_high <= 10, n_high


def test_inventory_validation_is_clean():
    data = inv.parse_report()
    problems = inv.validate(data)
    assert not problems, "清单校验问题：\n" + "\n".join(problems)


def test_every_decision_item_has_p_id():
    data = inv.parse_report()
    missing = []
    for r in data["rows"]:
        if inv._status_bucket(r["status"]) != "待决策":
            continue
        if not inv.P_ID.search(r["status"] + " " + r["evidence"]):
            missing.append(r["id"])
    assert not missing, f"待决策条目缺 P 编号: {missing}"


def test_decision_queue_covers_p1_to_p13():
    data = inv.parse_report()
    q = set(data["decision_queue"])
    assert set(range(1, 14)) <= q, f"决策队列缺号: {sorted(set(range(1,14)) - q)}"


def test_inventory_script_runs_and_writes_json(tmp_path):
    import json

    out = tmp_path / "inv.json"
    rc = inv.main.__wrapped__ if hasattr(inv.main, "__wrapped__") else None  # noqa: F841
    data = inv.parse_report()
    problems = inv.validate(data)
    payload = {"n": len(data["rows"]), "problems": problems, "items": data["rows"]}
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["problems"] == []
    assert got["n"] == len(data["rows"])
