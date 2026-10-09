# -*- coding: utf-8 -*-
"""探针：回测→学习 的那条链路（F386）在生产里到底通不通。

只读：不写任何文件、不改 env、不连业务库。
判别点：`BACKTEST_FACTOR_ATTR`（生产引擎的写入门控）在 .env 里是什么，
以及边车文件是谁写出来的（生产引擎 vs 我的手工脚本）。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "(未设置)"

print("=" * 78)
print("F386 探针：回测 → 学习 链路（只读）")
print("=" * 78)

print("\n[1] .env 里的相关开关")
env_txt = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
WATCH = ["BACKTEST_FACTOR_ATTR", "BACKTEST_FACTOR_ATTR_PATH",
         "LEARNING_READBACK_ENABLED", "BACKTEST_WISDOM_TOP_N", "V7_LESSONS_IN_MASTER"]
for k in WATCH:
    hits = [ln.strip() for ln in env_txt.splitlines()
            if ln.strip().startswith(k + "=")]
    print(f"  {k:30s} -> {hits[0] if hits else PLACEHOLDER}")

print("\n[2] 边车文件（回测→学习的载体）")
for rel in ["data/backtest_factor_attr.jsonl", "data/backtest_counterfactual.jsonl"]:
    q = ROOT / rel
    if not q.exists():
        print(f"  {rel}: 不存在")
        continue
    lines = [ln for ln in q.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"  {rel}: {len(lines)} 行 / {q.stat().st_size} bytes")
    if lines:
        try:
            rec = json.loads(lines[-1])
            print(f"     最后一条 run_id={rec.get('run_id')!r} symbol={rec.get('symbol')!r} "
                  f"tier={rec.get('tier')!r}")
            print(f"     字段: {sorted(rec.keys())}")
        except Exception as exc:  # noqa: BLE001
            print(f"     最后一条解析失败: {exc}")

print("\n[3] 门控常量本身")
src = (ROOT / "backend/services/live_pipeline_backtest_engine.py").read_text(encoding="utf-8")
i = src.index("_FACTOR_ATTR_ENABLED = ")
print("  " + src[i:i + 150].split("\n")[0])
print("  读取时机: 模块级（**导入时**求值一次）-> 运行时改 env 不生效，需重启")

print("\n[4] env_registry 登记情况")
reg = (ROOT / "backend/config/env_registry.py").read_text(encoding="utf-8")
for k in ["BACKTEST_FACTOR_ATTR", "BACKTEST_FACTOR_ATTR_PATH",
          "BACKTEST_COUNTERFACTUAL", "LEARNING_READBACK_ENABLED"]:
    print(f"  {k:30s} -> {'已登记' if chr(34) + k + chr(34) in reg else '**未登记**'}")

print("\n[5] 谁是写入方（生产引擎 / 手工脚本）")
calls = []
for rel in ["backend/services/live_pipeline_backtest_engine.py",
            "scripts/run_backtest_factor_attr.py",
            "scripts/run_backtest_counterfactual.py",
            "backend/services/unified_strategy/weekly_loop.py",
            "backend/services/learning_readback.py"]:
    t = (ROOT / rel).read_text(encoding="utf-8")
    w = "persist_factor_attr(" in t
    r = "load_factor_attr(" in t
    print(f"  {rel:58s} 写={w} 读={r}")
    if w:
        calls.append(rel)
print(f"  => 写入方: {calls or '无'}")
