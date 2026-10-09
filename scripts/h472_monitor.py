"""h472 中途监视（只读，可定时跑）：机制判据是否持续成立。

判定窗口 12h 太长，中途用本脚本看三件事（不构成裁决，只做预警）：
  1. 出场腿构成：`ofi_flatten_maker`（被动，费 0）是否取代 `ofi_flatten_taker`（费 −4bp）；
  2. 每腿手续费是否下降（taker 占比下降的直接体现）；
  3. **合规风险**：`timeout_hard_taker`（300s 硬顶）是否因被动不成交而变多、
     是否出现 >300s 的持仓（被动减仓的已知代价）。

用法：python scripts/h472_monitor.py           # 追加到 research_l1/out/h472_monitor.log
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
LOG = ROOT / "research_l1" / "out" / "h472_monitor.log"


def main() -> int:
    out = [f"\n{'='*84}", f"[h472 monitor] {dt.datetime.now():%Y-%m-%d %H:%M:%S}"]
    for args in (["scripts/h473_verify_h472.py"], ["scripts/h471_fee_attribution.py",
                                                   "--hours", "3"]):
        r = subprocess.run([str(PY), *args], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out.append(f"--- {' '.join(args)} rc={r.returncode} ---")
        out.append((r.stdout or "").strip())
        if r.returncode != 0:
            out.append((r.stderr or "").strip()[-800:])
    # 合规：当前持仓年龄（运行态）
    r = subprocess.run([str(PY), "scripts/h463_premise4.py"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out.append("--- 运行态（持仓年龄，合规 30s–300s）---")
    out.append("\n".join((r.stdout or "").strip().splitlines()[:12]))
    txt = "\n".join(out)
    print(txt)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
