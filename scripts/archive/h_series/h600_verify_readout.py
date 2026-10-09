"""h600 — `h599_verdict_readout.py` 的**离线用例**（R150）。

为什么要：`h599` 只在"INCONCLUSIVE 预演产物"上跑过 ✗，而它最可能被用到的路径是
**终局判决（PASS / ROLLBACK）** ⇒ 必须把那条分支也验证掉 ✓。

做法：用 `H599_VERDICT_PATH` 钩子把产物指向**临时文件**，合成三种终局形态：
  A) PASS（Welch 正显著）        → 格 2 应打印 PASS
  B) ROLLBACK（Welch 负显著）    → 格 2 应打印 ROLLBACK，且**无降级保护**提示
  C) 无效文件（缺 verdict 字段）  → 应优雅降级（不抛异常）
并断言**生产产物未被触碰** ✓（R41/R57/R73 纪律）。

用法：python scripts/h600_verify_readout.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "research_l1" / "out" / "h463_verdict.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


def _base() -> dict:
    return {
        "trial": "h463", "hours": 12.0, "judged_at": "2026-09-29T13:50:00+00:00",
        "covered": {"trial_h": 12.0, "trial_ratio": 1.0,
                    "baseline_h": 12.017, "legs_per_hour_covered": 70.0,
                    "baseline_legs_per_hour_covered": 62.33,
                    "legs_per_hour_wall": 70.0, "baseline_legs_per_hour_wall": 62.42},
        "freq_ok": True, "breaks_in_window": [],
        "trial": {"legs": 840, "net_bp": -600.0}, "baseline": {"legs": 749},
        "sub": {"trial": {"hold_window": {"legs": 840, "exit_legs": 105,
                                          "legs_per_trip": 8.0}}},
    }


def main() -> int:
    before = _sha(PROD)
    ok = True
    with tempfile.TemporaryDirectory() as td:
        vp = Path(td) / "v.json"
        cases = [
            ("PASS（正显著）", {**_base(), "verdict": "PASS",
                            "why": "welch_p=0.031 delta=+0.42bp",
                            "welch": {"p": 0.031, "delta": 0.42, "n_trial": 840,
                                      "n_base": 749}}, "PASS"),
            ("ROLLBACK（负显著）", {**_base(), "verdict": "ROLLBACK",
                                "why": "welch_p=0.045 delta=-0.55bp",
                                "welch": {"p": 0.045, "delta": -0.55, "n_trial": 840,
                                          "n_base": 749}}, "ROLLBACK"),
            ("缺 verdict 字段", {**_base()}, None),
        ]
        env = {**os.environ, "H599_VERDICT_PATH": str(vp), "PYTHONIOENCODING": "utf-8"}
        for label, payload, want in cases:
            vp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            p = subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"),
                                str(ROOT / "scripts" / "h599_verdict_readout.py")],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=120)
            out = (p.stdout or "") + (p.stderr or "")
            good_rc = p.returncode == 0
            good_txt = (want is None) or (f"verdict = {want}" in out)
            hit_g3 = "[格 3]" in out and "[格 5]" in out
            ok &= good_rc and good_txt and hit_g3
            print(f"  {'✓' if (good_rc and good_txt and hit_g3) else '✗'} {label}: "
                  f"rc={p.returncode} 含'verdict = {want}'={good_txt} 五格齐={hit_g3}")
            for line in out.splitlines():
                if line.strip().startswith("verdict =") or "无降级保护" in line \
                        or line.strip().startswith("⇒ 名义比") or "腿/趟" in line:
                    print(f"        {line.strip()[:110]}")
    after = _sha(PROD)
    same = before == after
    ok &= same
    print(f"\n  {'✓' if same else '✗'} 生产产物未被触碰（sha256 {before[:12]}…）")
    print("=" * 84)
    print("✓ 全部通过" if ok else "✗ 有失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
