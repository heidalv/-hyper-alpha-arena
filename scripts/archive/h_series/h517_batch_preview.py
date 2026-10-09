"""h517：批量判定预演（只读）——在多个判定落地前，先看它们**若现在判会怎样**。

动机：03（h464）部署前还有若干判定会改参数（h442 `stop_ref_last_leg`、
h443 `compound_ratio` 等）。若某条的走向会显著恶化风险或成本，应提前发现并干预，
而不是等它落地后被动应对。

用法：python scripts/h517_batch_preview.py [--trials h442,h443,h435]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
OUT = ROOT / "research_l1" / "out" / "h517_batch_preview.json"
DEFAULT = ("h442", "h443", "h435", "h436", "h437", "h438", "h452", "h454",
           "h463", "h472")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", default="")
    a = ap.parse_args()
    keys = [x.strip() for x in a.trials.split(",") if x.strip()] or list(DEFAULT)
    res = {}
    print(f"{'试跑':>8s} {'判定':>13s} {'腿速 trial/base':>18s} {'freq':>5s} "
          f"{'Δbp':>8s} {'p':>7s}  备注")
    for k in keys:
        p = subprocess.run([str(PY), str(ROOT / "scripts" / "h425_repair_trial.py"),
                            "--trial", k, "--judge", "--dry-run"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        vp = ROOT / "research_l1" / "out" / f"{k}_verdict_dryrun.json"
        if not vp.exists():
            print(f"{k:>8s} {'—':>13s}   （无预演产物：{(p.stdout or '')[-80:].strip()}）")
            continue
        j = json.loads(vp.read_text(encoding="utf-8"))
        w = j.get("welch") or {}
        t = j.get("trial") or {}
        b = j.get("baseline") or {}
        res[k] = {"verdict": j.get("verdict"), "why": j.get("why"),
                  "legs": round(float(t.get("legs_per_hour") or 0), 1),
                  "base_legs": round(float(b.get("legs_per_hour") or 0), 1),
                  "freq_ok": j.get("freq_ok"),
                  "delta": round(float(w.get("delta") or 0), 3),
                  "p": round(float(w.get("p") or 1), 3),
                  "breaks": len(j.get("breaks_in_window") or [])}
        print(f"{k:>8s} {str(j.get('verdict')):>13s} "
              f"{f'{res[k]['legs']}/{res[k]['base_legs']}':>18s} "
              f"{str(j.get('freq_ok')):>5s} {res[k]['delta']:+8.2f} {res[k]['p']:7.3f}  "
              f"断点×{res[k]['breaks']}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
