"""h478：h463 前提量化（读 `h463_samples.jsonl`，可随时跑，不必等采样结束）。

判据（回答"h463 到底触及了多少仓位时间"）：
  · `in_pos` 样本中 `pattern_tag == ""` 的占比 = **不受 P1/P45 保护**的仓位时间；
  · 其中 `age_s > 45` 的占比 = **旧值 45s 会封锁、新值 90s 不封锁**的那段
    （即 h463 真正改变的暴露面）；
  · `age_s > 90` 的占比 = 即使 90s 也仍被封锁的部分（h463 未覆盖）。

对照（h463 部署于 2026-09-28T18:20:35Z，worker 02:32:15L 重启）：
  `timeout_maker_only` 心跳速率 = "在仓且超有效持有期"的 tick 占比，
  改前基线 **20.64 / 100 tick**（51,135 tick 样本）⇒ 改后该速率应下降，
  下降幅度 ≈ 本脚本算出的 `untagged ∩ age>45` 占比。

用法：python scripts/h478_h463_premise_summary.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAP = ROOT / "research_l1" / "out" / "h463_samples.jsonl"
OUT = ROOT / "research_l1" / "out" / "h478_h463_premise.json"


def main() -> int:
    if not SNAP.exists():
        print("无采样文件（先跑 scripts/h463_sampler.py）")
        return 1
    recs = [json.loads(x) for x in SNAP.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    # 只统计 h463 生效之后的样本（部署 18:20:35Z）
    recs = [r for r in recs if float(r["ts"]) >= 1790619635.0]
    tot = inpos = untag = untag45 = untag90 = tagged = tag45 = tag90 = 0
    ages_all = []
    for r in recs:
        for v in (r.get("syms") or {}).values():
            tot += 1
            if not v.get("in_pos"):
                continue
            inpos += 1
            ages_all.append(float(v.get("age_s") or 0.0))
            if (v.get("tag") or "") == "":
                untag += 1
                untag45 += 1 if float(v["age_s"]) > 45 else 0
                untag90 += 1 if float(v["age_s"]) > 90 else 0
            else:
                tagged += 1
                tag45 += 1 if float(v["age_s"]) > 45 else 0
                tag90 += 1 if float(v["age_s"]) > 90 else 0
    if inpos == 0:
        print(f"样本 {len(recs)} 次采样、无在仓样本（在仓率低）")
        return 1
    ages_all.sort()

    def q(p):
        return ages_all[min(len(ages_all) - 1, int(p * (len(ages_all) - 1)))]
    print(f"采样 {len(recs)} 次（h463 生效后）  币×采样点 {tot}  在仓 {inpos}"
          f"（{100.0*inpos/tot:.1f}%）")
    print(f"在仓年龄分位 p50/p75/p90/max = {q(.5):.0f}/{q(.75):.0f}/{q(.9):.0f}/"
          f"{ages_all[-1]:.0f} s")
    print("=" * 76)
    print(f"无形态标记（不受 p1/p45 保护）: {untag}/{inpos} "
          f"= {100.0*untag/inpos:.1f}%")
    print(f"  其中 age>45s（**h463 改变的那段**）: {untag45} "
          f"= {100.0*untag45/inpos:.1f}% of 在仓（{100.0*untag45/max(untag,1):.1f}% of 无标记）")
    print(f"  其中 age>90s（90s 仍封锁）        : {untag90} "
          f"= {100.0*untag90/inpos:.1f}% of 在仓")
    print(f"有形态标记: {tagged}/{inpos} = {100.0*tagged/inpos:.1f}%"
          f"（age>45s {tag45}，age>90s {tag90}）")
    exposed = 100.0 * untag45 / inpos
    print("=" * 76)
    print(f"⇒ **h463 触及的在仓时间占比 ≈ {exposed:.1f}%**")
    print("   对照：改前 `timeout_maker_only` 速率 20.64/100 tick（= 在仓且超有效期）")
    print(f"   预期改后速率 ≈ 20.64 × (1 − {exposed/100.0:.3f}) ≈ "
          f"{20.64*(1-exposed/100.0):.1f}/100 tick（若其余分量不变）")
    if exposed < 5.0:
        verdict = "触及面很小（<5%）⇒ 该参数对整体净额影响有限，判定大概率 INCONCLUSIVE"
    elif exposed < 15.0:
        verdict = "触及面中等 ⇒ 值得等 12h 判定，但预期效应温和"
    else:
        verdict = "触及面显著 ⇒ 该参数是真实杠杆，判定应能给出明确结论"
    print("⇒ 前提裁决:", verdict)
    OUT.write_text(json.dumps(
        {"samples": len(recs), "points": tot, "in_pos": inpos, "untagged": untag,
         "untagged_gt45": untag45, "untagged_gt90": untag90, "tagged": tagged,
         "age_p50": q(.5), "age_p90": q(.9), "age_max": ages_all[-1],
         "exposed_share_pct": round(exposed, 2), "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
