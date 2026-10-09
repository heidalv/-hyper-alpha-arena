# -*- coding: utf-8 -*-
"""[h721 阶段2 2026-10-02] bandit 影子对比:24h 后回溯评估提案质量。

设计文档 §阶段2:bandit 提案先影子对比 v5 现状 24h,显著更优才落库。
本脚本读 `data/bandit_proposal_history.jsonl`(h716 每 6h 追加),对 ≥24h 前的
每条提案,用**当前** h714 每币后验评估:
  - 提案组 mean(每币后验均值,仅看 ≥10 样本的币)
  - 现役组 mean(同口径)
  - 若提案组 mean − 现役组 mean > +0.5bp ⇒ 该提案"判胜";
  - 累计胜率 = bandit 相对 v5 的影子战绩。
输出 data/bandit_shadow_compare_last.json。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HIST = ROOT / "data" / "bandit_proposal_history.jsonl"
MIN_N = 10
WIN_BP = 0.5


def main() -> int:
    try:
        lines = HIST.read_text(encoding="utf-8").splitlines()
    except Exception:
        print("无 bandit 提案历史(等 h716 首跑)")
        return 0
    try:
        post = json.loads((ROOT / "data" / "coin_posterior_last.json").read_text(encoding="utf-8"))
    except Exception:
        print("无 coin_posterior_last.json")
        return 1
    pcoins = post.get("per_coin") or {}
    now = time.time()
    judged = []
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if now - float(e.get("ts") or 0.0) < 24 * 3600:
            continue
        def set_mean(syms):
            ms = [pcoins[s]["mean_bp"] for s in syms
                  if s in pcoins and pcoins[s].get("n", 0) >= MIN_N]
            return (sum(ms) / len(ms)) if ms else None
        m_prop = set_mean(e.get("proposed") or [])
        m_cur = set_mean(e.get("current") or [])
        if m_prop is None or m_cur is None:
            continue
        d = m_prop - m_cur
        judged.append({"ts": e["ts"], "current": e["current"],
                       "proposed": e["proposed"], "current_mean_bp": round(m_cur, 3),
                       "proposed_mean_bp": round(m_prop, 3), "delta_bp": round(d, 3),
                       "win": d > WIN_BP})
    wins = sum(1 for j in judged if j["win"])
    out = {"ts": now, "judged": judged, "wins": wins, "n": len(judged),
           "win_rate": (wins / len(judged)) if judged else None}
    (ROOT / "data" / "bandit_shadow_compare_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"24h 前提案 {len(judged)} 条已判,胜 {wins} 条"
          + (f" (胜率 {wins/len(judged)*100:.0f}%)" if judged else ""))
    for j in judged:
        print(f"  {j['ts']:.0f} 提案均值 {j['proposed_mean_bp']:+.2f} vs "
              f"现役 {j['current_mean_bp']:+.2f} ⇒ Δ{j['delta_bp']:+.2f}bp "
              f"{'胜' if j['win'] else '负'}")
    print("✓ 已写 data/bandit_shadow_compare_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
