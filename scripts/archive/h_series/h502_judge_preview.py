"""h502：两个待判定试跑的**判定预演对照**（h463 / h472），只读不改库。

用途：在 14:20/14:33 的自动判定落地前，先把"若现在判会得到什么"记录下来，
便于事后核对判据是否按预期工作（尤其是我刚加的 h500 频率市场调整与
h484 已知断点护栏）。

用法：python scripts/h502_judge_preview.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
OUT = ROOT / "research_l1" / "out" / "h502_judge_preview.json"


def main() -> int:
    res = {}
    for key in ("h463", "h472", "h464"):
        p = subprocess.run([str(PY), str(ROOT / "scripts" / "h425_repair_trial.py"),
                            "--trial", key, "--judge", "--dry-run"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        vp = ROOT / "research_l1" / "out" / f"{key}_verdict_dryrun.json"
        if not vp.exists():
            print(f"{key}: 无预演产物（rc={p.returncode}）{(p.stdout or '')[-200:]}")
            continue
        j = json.loads(vp.read_text(encoding="utf-8"))
        sub = j.get("sub") or {}
        tm = sub.get("trial") or {}
        line = {
            "verdict": j.get("verdict"), "why": (j.get("why") or "")[:120],
            "hours": round(float(j.get("hours") or 0), 2),
            "legs_per_hour": round(float((j.get("trial") or {}).get("legs_per_hour") or 0), 1),
            "baseline_legs_per_hour": round(
                float((j.get("baseline") or {}).get("legs_per_hour") or 0), 1),
            "freq_ok": j.get("freq_ok"),
            "welch_p": round(float((j.get("welch") or {}).get("p") or 1), 3),
            "delta_bp": round(float((j.get("welch") or {}).get("delta") or 0), 3),
            "breaks_in_window": j.get("breaks_in_window"),
        }
        if "taker_mix" in tm:
            line["taker_share_trial"] = tm["taker_mix"].get("taker_share")
        if "entry_vs_exit" in tm:
            line["entry_net_bp"] = tm["entry_vs_exit"].get("net_bp_per_entry_leg")
            line["exit_net_bp"] = tm["entry_vs_exit"].get("net_bp_per_exit_leg")
        res[key] = line
        print(f"── {key} ──")
        for k, v in line.items():
            print(f"   {k:26s} {v}")
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
