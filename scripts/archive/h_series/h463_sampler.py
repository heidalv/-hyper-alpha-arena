"""h463 前提采样器（只读）：量出"无形态标记 + 年龄>45s"的仓位时间占比。

为什么需要（这是 h463 的**存在性前提**）：
  `max_one_side_seconds` 只对 **未被打上 P1/P45 形态标记**的仓位生效
  （P1→`p1_hold_sec`=60s，P45→`p45_hold_sec`=300s，见 runner.py:615-625）。
  若"无标记"仓位时间占比很低，则 45→90 这个改动几乎不约束任何腿，
  12h 试跑只是在烧时间 ⇒ 先用采样把占比量出来，再决定是否继续等判定。

采样口径（每 `period` 秒一次，逐币）：
  · `qty` 非零才算"在仓"；
  · `age = now − opened_ts`；
  · 记 `pattern_tag`、`timeout_exit_blocked`（累计计数，可算增速）。

输出：research_l1/out/h463_samples.jsonl（一行一次采样）+ 结束时的汇总打印。

用法：python scripts/h463_sampler.py --minutes 60 --period 60
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h463_samples.jsonl"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def grab(dsn: str) -> list:
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT symbol, state_json FROM lane_runtime_state "
                        "WHERE lane_id=%s", (LANE,))
            return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--period", type=float, default=60.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    n = max(1, int(a.minutes * 60 / a.period))
    print(f"采样 {n} 次 × {a.period:.0f}s ≈ {a.minutes:.0f} min", flush=True)
    with OUT.open("a", encoding="utf-8") as fh:
        for i in range(n):
            t0 = time.time()
            now = t0
            try:
                rows = grab(dsn)
            except Exception as exc:  # noqa: BLE001
                print(f"[{i}] 读失败: {exc}", flush=True)
                time.sleep(a.period)
                continue
            rec = {"ts": round(now, 3), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "syms": {}}
            for sym, st in rows:
                st = st or {}
                qty = float(st.get("qty") or 0.0)
                opened = float(st.get("opened_ts") or 0.0)
                in_pos = abs(qty) > 1e-12
                rec["syms"][str(sym)] = {
                    "qty": round(qty, 6), "in_pos": in_pos,
                    "age_s": round(now - opened, 1) if (in_pos and opened > 0) else 0.0,
                    "tag": str(st.get("pattern_tag") or ""),
                    "teb": int(st.get("timeout_exit_blocked") or 0),
                }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 5 == 0:
                _live = sum(1 for v in rec["syms"].values() if v["in_pos"])
                print(f"[{i}/{n}] {rec['iso']} 在仓 {_live} 币", flush=True)
            dt = a.period - (time.time() - t0)
            if dt > 0 and i < n - 1:
                time.sleep(dt)
    # 汇总
    recs = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    recs = recs[-n:] if len(recs) > n else recs
    tot = inpos = untag = untag45 = untag90 = tagged = tag45 = tag90 = 0
    for r in recs:
        for v in r["syms"].values():
            tot += 1
            if not v["in_pos"]:
                continue
            inpos += 1
            if v["tag"] == "":
                untag += 1
                untag45 += 1 if v["age_s"] > 45 else 0
                untag90 += 1 if v["age_s"] > 90 else 0
            else:
                tagged += 1
                tag45 += 1 if v["age_s"] > 45 else 0
                tag90 += 1 if v["age_s"] > 90 else 0
    print("=" * 70)
    print(f"采样点 {tot}  在仓 {inpos} ({100.0*inpos/max(tot,1):.1f}%)")
    print(f"  无标记 {untag} ({100.0*untag/max(inpos,1):.1f}% of 在仓)"
          f"  其中 age>45s {untag45} ({100.0*untag45/max(untag,1):.1f}%)"
          f"  age>90s {untag90}")
    print(f"  有标记 {tagged} ({100.0*tagged/max(inpos,1):.1f}%)"
          f"  其中 age>45s {tag45}  age>90s {tag90}")
    print(f"⇒ **h463 触及的在仓时间占比 = {100.0*untag45/max(inpos,1):.1f}%** "
          f"(= 无标记∩age>45s / 全部在仓样本)")
    summary = {"samples": len(recs), "points": tot, "in_pos": inpos,
               "untagged": untag, "untagged_gt45": untag45, "untagged_gt90": untag90,
               "tagged": tagged, "tagged_gt45": tag45, "tagged_gt90": tag90,
               "h463_exposed_share": round(100.0 * untag45 / max(inpos, 1), 2)}
    (ROOT / "research_l1" / "out" / "h463_premise_samples_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", (ROOT / "research_l1" / "out" /
                    "h463_premise_samples_summary.json").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
