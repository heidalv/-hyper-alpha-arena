# -*- coding: utf-8 -*-
"""Z195：把 test_portfolio_budget.py 从旧判据入口迁到 P17 新增的 metric 入口。

P17 之后 `evaluate_open()` 读的是 `_strategy_drawdown_metric()`（带样本新鲜度），
旧的 `_strategy_drawdown_sigma()` 只是兼容包装 ⇒ 老用例 patch 旧方法已不再生效。
本脚本做机械替换并注入 `_dd()` 助手（默认样本新鲜，避免触发 stale 自愈）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\tests\unit\test_portfolio_budget.py")
s = io.open(p, encoding="utf-8").read()

REPL = [
    ("b._strategy_drawdown_sigma = lambda *a, **kw: None",
     "b._strategy_drawdown_metric = lambda *a, **kw: None"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: None",
     "budget._strategy_drawdown_metric = lambda *a, **kw: None"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: 7.54",
     "budget._strategy_drawdown_metric = lambda *a, **kw: _dd(7.54)"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: 5.2",
     "budget._strategy_drawdown_metric = lambda *a, **kw: _dd(5.2)"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: 6.0",
     "budget._strategy_drawdown_metric = lambda *a, **kw: _dd(6.0)"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: 8.0",
     "budget._strategy_drawdown_metric = lambda *a, **kw: _dd(8.0)"),
    ("budget._strategy_drawdown_sigma = lambda *a, **kw: 5.0",
     "budget._strategy_drawdown_metric = lambda *a, **kw: _dd(5.0)"),
]
for a, b in REPL:
    s = s.replace(a, b)

HELPER = '''
def _dd(ratio: float, *, age_hours: float = 1.0, n: int = 168) -> dict:
    """[P17] 回撤判据 metric 的形状（样本默认"新鲜"，避免触发 stale 自愈规则）。"""
    return {
        "ratio": round(float(ratio), 4), "sigma": 9.87, "drawdown": 187.47,
        "peak": 41.19, "last_value": -146.28, "n_samples": n,
        "last_sample_ts": "2026-09-10 10:08:29", "age_hours": age_hours,
    }


'''
if "def _dd(" not in s:
    lines = s.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("def test_"):
            lines.insert(i, HELPER.strip("\n") + "\n")
            break
    s = "\n".join(lines)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("仍含旧方法名 '_strategy_drawdown_sigma' 次数:", s.count("_strategy_drawdown_sigma"))
print("新方法名 '_strategy_drawdown_metric' 次数:", s.count("_strategy_drawdown_metric"))
print("_dd helper 已注入:", "def _dd(" in s)
