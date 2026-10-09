"""h479：从采样数据**反推运行态的有效持有期**（h463 采纳的第二种独立证明）。

原理（runner.py:1289-1327）：
  每个 tick，若 `age > _effective_hold_sec(state, limits)` 成立，
  `state.timeout_exit_blocked += 1`。该计数被持久化在 `lane_runtime_state`
  ⇒ 采样器每分钟记一次 (tag, age, teb) 就能观察到"增量发生的那一刻"：
      上一采样 age ≤ 阈值 < 本采样 age
  即阈值落在两次采样之间 ⇒ 多个样本取**下界的最小值**即可夹逼阈值。

判读（无形态标记的仓位走 `max_one_side_seconds`）：
  · 若"无标记"增量的**前一次 age** 最小值 ≥ ~80s ⇒ 进程内阈值 ≈ 90（h463 已生效）；
  · 若出现 ~45s 附近的增量 ⇒ 进程内仍是 45（注册表改了但没采用）。
  （`tag == "P1"` 走 p1_hold_sec=60，`P45` 走 300，可作内部对照：
     P1 增量应出现在 60s 附近 —— 若 P1 出现在 60 而无标记出现在 90，
     说明两条分支的阈值都如预期。）

注意：teb 也是"每个超期 tick 都 +1"，所以增量>1 说明跨了多个 tick；本脚本只看
**首次**超期（即上一次采样 teb 与 age 均未超期）的样本对，避免把持续超期算进来。

用法：python scripts/h479_infer_hold_threshold.py
"""
from __future__ import annotations

import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAP = ROOT / "research_l1" / "out" / "h463_samples.jsonl"
OUT = ROOT / "research_l1" / "out" / "h479_hold_threshold.json"
RESTART_TS = 1790620335.0     # 02:32:15L（重启，新进程）


def main() -> int:
    if not SNAP.exists():
        print("无采样文件")
        return 1
    recs = [json.loads(x) for x in SNAP.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    recs = [r for r in recs if float(r["ts"]) >= RESTART_TS]
    if len(recs) < 3:
        print(f"样本太少（{len(recs)}）")
        return 1
    # 逐币配对相邻采样
    prev: dict = {}
    events = {"无标记": [], "P1": [], "P45": []}
    for r in recs:
        for sym, v in (r.get("syms") or {}).items():
            p = prev.get(sym)
            if p and p.get("in_pos") and v.get("in_pos") and \
                    v.get("qty", 0) * p.get("qty", 0) > 0:      # 同一持仓方向
                d = int(v.get("teb") or 0) - int(p.get("teb") or 0)
                if d > 0 and float(p.get("age_s") or 0) <= 300.0:
                    tag = (v.get("tag") or "")
                    key = "无标记" if tag == "" else ("P1" if tag == "P1"
                                                 else ("P45" if tag == "P45" else tag))
                    events.setdefault(key, []).append(
                        {"sym": sym, "d": d,
                         "prev_age": round(float(p["age_s"]), 1),
                         "cur_age": round(float(v["age_s"]), 1),
                         "prev_teb": int(p["teb"]), "iso": r["iso"]})
            prev[sym] = v
    print(f"采样 {len(recs)} 次（重启后）")
    print("=" * 92)
    summary = {}
    for key, evs in events.items():
        if not evs:
            print(f"{key:6s}: 无“首次超期”事件")
            continue
        # 去重：同一持仓连续多次 +1 只取首次（prev_age 未超期者已由 teb 条件保证）
        lows = sorted(e["prev_age"] for e in evs)
        highs = sorted(e["cur_age"] for e in evs)
        summary[key] = {"n": len(evs), "prev_age_min": lows[0],
                        "prev_age_med": st.median(lows),
                        "cur_age_min": highs[0], "cur_age_med": st.median(highs)}
        print(f"{key:6s}: n={len(evs):3d}  阈值下界(prev_age) 最小={lows[0]:6.1f}s "
              f"中位={st.median(lows):6.1f}s | 阈值上界(cur_age) 最小={highs[0]:6.1f}s "
              f"中位={st.median(highs):6.1f}s")
        print(f"        样本：{[e['sym'] + ':' + str(e['prev_age']) + '→' + str(e['cur_age']) for e in evs[:6]]}")
    print("=" * 92)
    verdict = "样本不足，无法夹逼"
    u = summary.get("无标记")
    if u:
        # ⚠️ 判读口径修正（本脚本第一版写错了，必须记下来）：
        #   `prev_age` 只是**下界**——它排除"阈值更小"，**不能**排除"阈值=90"：
        #   若阈值=45 而采样恰好落在那一 tick 之前几毫秒，我们会看到
        #   prev_age=46.9 也照样出现增量。反过来 `cur_age` 是**上界**。
        #   而 tick=15s、采样=60s ⇒ 夹逼宽度 ≥ 15s，足以分辨 45 vs 90 吗？
        #   只有当下界的最小值 ≳ 75s（>45+15+余量）时，才能排除 45。
        lo_ = u["prev_age_min"]
        hi_ = u["cur_age_min"]
        width = hi_ - lo_
        if lo_ >= 75.0:
            verdict = (f"⇒ 进程内 `max_one_side_seconds` **≈ 90（已生效）**："
                       f"无标记仓位首次超期的年龄下界 {lo_:.0f}s ≥ 75s，"
                       f"若阈值为 45 则必然出现过 ~45–60s 的下界")
        elif width <= 18.0:
            verdict = (f"⇒ 夹逼较紧：阈值 ∈ ({lo_:.0f}, {hi_:.0f}] s "
                       f"（宽度 {width:.0f}s，与 tick 15s 同量级）")
        else:
            verdict = (f"⇒ **夹逼不紧**：阈值 ∈ ({lo_:.0f}, {hi_:.0f}] s，"
                       f"下界 {lo_:.0f}s 无法排除 90、上界 {hi_:.0f}s 无法确认 45 ⇒ "
                       f"60s 采样 + 15s tick 的分辨率不足。"
                       f"改用 scripts/h480_side_block_probe.py（15s 采样 + 直接看加仓侧是否被撤）")
    print(verdict)
    p1 = summary.get("P1")
    if p1:
        print(f"（内部对照 P1：下界 {p1['prev_age_min']:.0f}s —— p1_hold_sec=60 ⇒ 应 ≈60s 附近；"
              f"若它也偏高，说明是采样分辨率问题而非参数未生效）")
    OUT.write_text(json.dumps({"samples": len(recs), "events": events,
                               "summary": summary, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
