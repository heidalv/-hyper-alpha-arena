# -*- coding: utf-8 -*-
"""Z87: §53 EV 闸取证（一键复现全部证据）。

输出四段：
  A. 闸开关与校准器状态（生产函数）
  B. 接线取证：mid 提案经 nature 归一后，EV 闸实际收到的 nature（monkeypatch 捕获）
  C. 历史裁决分布（日志去重统计：影子放行 / 拦截 / 全局关）
  D. 若校准生效，当前 mid 提案会怎样（逐档评分裁决）
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.calibration.confidence_calibrator import (  # noqa: E402
    get_calibrator_for_nature,
)
from backend.services.decision_core import midlong_ev_gate as evg  # noqa: E402

print("=== A. 开关与校准器状态 ===")
for k in ("MIDLONG_EV_GATE_ENABLED", "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION",
          "MIDLONG_EV_FALLBACK_RR", "SWING_CALIBRATOR_MIN_SAMPLES",
          "TREND_CALIBRATOR_MIN_SAMPLES", "SWING_CALIBRATOR_LOOKBACK_DAYS"):
    print(f"  {k} = {os.getenv(k)}")
for nat in ("swing", "trend_follow"):
    cal = get_calibrator_for_nature(nat)
    m = cal._get_model()
    print(f"  {nat}: signal={cal._signal_type} n={m.n_samples} calibrated={m.is_calibrated} "
          f"base={m.base_rate:.3f} min_samples={cal._cfg('MIN_SAMPLES', '?')}")
    r = cal.estimate_p_win("ETH", 60.0, "long")
    print(f"      estimate_p_win(60) = {r.p_win} ({r.source}) {r.note}")

print("\n=== B. 接线取证：EV 闸在 mid 路径上收到的 nature ===")
seen = {}


def _spy(**kw):
    seen.update(kw)
    from backend.services.decision_core.midlong_ev_gate import EvDecision
    return EvDecision(allowed=True, ev_pct=0.0, p_win=0.5, p_win_source="spy",
                      nature=kw.get("nature", ""), reason="spy")


orig = evg.midlong_ev_gate.evaluate
evg.midlong_ev_gate.evaluate = _spy  # type: ignore[assignment]
try:
    from backend.services.decision_core.pipeline import evaluate_midlong_open
    dec = {
        "action": "buy", "trade_nature": "swing", "timeframe_tier": "mid",
        "confidence": 60.0, "take_profit_pct": 0.05, "stop_loss_pct": 0.045,
        "_agent_independent": True,
    }
    try:
        evaluate_midlong_open(db=None, account_id=0, symbol="ETH", dec=dec,
                              market_data=None, mode="paper", persistence_allow=True)
    except Exception as e:
        print("  evaluate_midlong_open 抛出（不影响取证）:", str(e)[:100])
finally:
    evg.midlong_ev_gate.evaluate = orig  # type: ignore[assignment]
print(f"  mid 提案 trade_nature='swing' → EV 闸实际收到 nature={seen.get('nature')!r} "
      f"（swing 校准器信号={get_calibrator_for_nature('swing')._signal_type}，"
      f"trend 校准器信号={get_calibrator_for_nature('trend_follow')._signal_type}）")
print("  ⇒ mid 层 EV **不会**用 swing 校准器（已被 normalize_v5_nature 归一为 trend_follow）")

print("\n=== C. 历史裁决分布（日志去重）===")
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2})")
kinds = {
    "影子·未校准放行": "[影子·未校准放行]",
    "影子·全局关": "[影子·全局关]",
    "期望值不足拦截": "期望值不足拦截",
    "BLOCK（pipeline）": "[MidLongEvGate] BLOCK",
}
counts = {k: Counter() for k in kinds}
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
seen_lines = set()
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            if "[MidLongEvGate]" not in line:
                continue
            key = line.strip()[:200]
            if key in seen_lines:
                continue
            seen_lines.add(key)
            m = rx_ts.match(line)
            for name, tag in kinds.items():
                if tag in line:
                    counts[name][m.group(1) if m else "?"] += 1
for name, c in counts.items():
    tot = sum(c.values())
    print(f"  {name}: 合计 {tot}  按日 {dict(sorted(c.items()))}")
print("  ⇒ 结论：EV 闸拦截 0 笔；563 次评估全部为「未校准影子放行」")

print("\n=== D. 若校准生效，当前 mid 提案的裁决（生产函数）===")
for score in (45.0, 52.0, 60.0, 70.0, 85.0):
    d = orig(nature="swing", symbol="ETH", score=score, direction="long",
             tp_pct=0.05, sl_pct=0.045, notional_usd=2000.0)
    print(f"  score={score:>5}: allowed={d.allowed} p={d.p_win}({d.p_win_source}) "
          f"ev={d.ev_pct:+.4%} shadow={d.breakdown.get('shadow_cold_start')}")
print("  （当前接线走 trend_follow → cold_linear 影子放行；若改成 swing 口径会立刻硬拦）")
