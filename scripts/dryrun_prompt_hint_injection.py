# -*- coding: utf-8 -*-
"""干跑：主脑的【风控硬约束】注入条件是否会对各标的成立（只读，不改任何状态）。

brain.py 的注入条件（与线上同一份判定）：
    _tier3(tier) != "short" and _short_mode() == "regime_gated" and _daily_regime(sym) not in ("", "down")
成立 ⇒ user prompt 末尾会追加硬约束文本。本脚本把该文本**照原样复现**，便于人读与核对。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto.midlong_circuit_gate import (  # noqa: E402
    _daily_regime,
    _short_mode,
)
from backend.services.mlto import brain as B  # noqa: E402

SYMS = ["VIRTUAL", "DOGE", "SUI", "PLAY", "ASTER", "UNI", "ZEC", "XRP", "SOL", "BNB", "BTC"]
TIER = "mid"

print("=" * 92)
print(f"干跑：调用**真实函数** brain._short_gate_hint(tier={TIER})   _short_mode()={_short_mode()}")
print("=" * 92)
print(f"{'symbol':10s} {'日线regime':>10s} {'注入?':>6s}")
hits = []
for s in SYMS:
    reg = str(_daily_regime(s) or "")
    txt = B._short_gate_hint(s, TIER)
    if txt:
        hits.append((s, reg or "(不可判)"))
    print(f"{s:10s} {(reg or '(空/不可判)'):>10s} {'是' if txt else '否':>6s}")

print(f"\n会注入的标的 = {len(hits)}/{len(SYMS)}：{hits}")
if hits:
    s, reg = hits[0]
    print(f"\n注入文本（真实函数输出，以 {s}/{reg} 为例）：")
    print("   " + B._short_gate_hint(s, TIER))
print("\n说明：注入是**确定性**的（给定 regime 必然注入）；LLM 是否照做属行为层，"
      "需要重启后的新样本累积才能统计（见 scripts/verify_direction_fix_after_restart.py）。")
