"""h566：调试 `h564` 的子进程失败（一次性排查脚本，保留以便复查）。

现象：`h565` 的用例 ③ 报 rc=1，但文档没被写入 ⇒ 子进程 `h546 --append` 失败。
本脚本搭一个临时环境、跑一次 `h564 --apply`，并把**子进程的输出**原样打出来，
直接看它为什么失败（不猜）。

用法：python scripts/h566_debug_auto_accept.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        vdir, doc = tdp / "out", tdp / "doc.md"
        vdir.mkdir()
        doc.write_text("# 测试文档\n", encoding="utf-8")
        (vdir / "h463_verdict.json").write_text(json.dumps({
            "trial": "h463", "verdict": "PASS", "why": "dbg", "hours": 12.0,
            "trial": {"legs": 800, "net_bp": 1.0, "legs_per_hour": 70.0},
            "baseline": {"legs": 800, "net_bp": 0.5, "legs_per_hour": 62.0},
            "welch": {"p": 0.01, "delta": 1.0}, "freq_ok": True, "sub": {},
            "two_halves": {}, "breaks_in_window": [], "dry_run": False,
            "judged_at": "2026-09-29T13:49:00+00:00"}), encoding="utf-8")
        env = dict(os.environ)
        env.update({"H564_VERDICT_DIR": str(vdir), "H564_DOC_PATH": str(doc),
                    "H564_LOG_PATH": str(tdp / "log.txt"),
                    "H564_STATE_PATH": str(tdp / "st.json"),
                    "PYTHONIOENCODING": "utf-8"})
        # 先直接跑一次 h546（同样带钩子），看它自己的报错
        print("=== 直接跑 h546 --trial h463 --append（带钩子）===")
        p = subprocess.run([str(PY), str(ROOT / "scripts" / "h546_accept_verdict.py"),
                            "--trial", "h463", "--append"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=300)
        print(f"rc={p.returncode}")
        print("stdout:", (p.stdout or "").strip()[:600])
        print("stderr:", (p.stderr or "").strip()[:600])
        print("\n文档内容：", repr(doc.read_text(encoding="utf-8")[:200]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
