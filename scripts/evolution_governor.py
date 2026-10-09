# -*- coding: utf-8 -*-
r"""[h901] 统一进化总督驱动 —— 每 30 分钟一个周期。

用法:.venv\Scripts\python.exe scripts\evolution_governor.py [小时数=6]
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.evolution.governor import run_cycle  # noqa: E402

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

print("== 统一进化总督 ==" , flush=True)
rec = run_cycle(hours=HOURS)
perf = rec["perf"]
print(f"表现: 净{perf['pnl']:+.2f}USD  n={perf['n']}  胜率{perf['win_rate']:.0%}  "
      f"盈亏比{perf['pl_ratio']:.2f}", flush=True)
print(f"归因发现: {rec['findings_n']} 条  最亏: {rec['top_finding']}", flush=True)
if rec.get("anomalies"):
    print(f"⚠ 实时异常: {[a['subj'] for a in rec['anomalies']]}", flush=True)
lead = rec.get("leading") or {}
if lead:
    print(f"领先指标: markout30s={lead.get('markout_30s_bp')}bp "
          f"成交趋势={lead.get('fill_trend')}(近1h {lead.get('fills_last_1h')} vs 前1h {lead.get('fills_prior_1h')})",
          flush=True)
print(f"旧改动裁决: {rec['verdict']}", flush=True)
if rec["proposal"]:
    p = rec["proposal"]
    print(f"新提案: {p['param']} {p['old']}→{p['new']}  ({p['reason']})", flush=True)
    print(f"  已实施: {rec['applied']}", flush=True)
else:
    print("新提案: 无(证据不足或无需调整)", flush=True)
print(f"✓ 状态已写 data/evolution_governor_state.json", flush=True)
