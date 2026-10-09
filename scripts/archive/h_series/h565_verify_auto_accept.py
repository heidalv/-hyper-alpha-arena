"""h565：离线验证**自动验收守卫**（`h564`）——不碰生产文档/日志。

为什么必须验：`h564` 有一条**会写正式验收文档**的路径（`h546 --append`）。
按 R41 的教训（测试钩子必须覆盖**全部**落盘路径），先给 h564 加了
`H564_DOC_PATH` / `H564_LOG_PATH` / `H564_STATE_PATH` / `H564_VERDICT_DIR` 四个钩子，
本脚本用临时目录喂它合成判定，验证：

  ① 无正式判定 ⇒ 不动作；
  ② 有**预演**产物（`dry_run: true`）⇒ **绝不写入** ✗（防预演数据进正式验收）；
  ③ 有正式判定 ⇒ 写入，且 `judged_at` 出现在文档里；
  ④ **幂等**：再跑一次不再重复写入（同一 `judged_at` 已在文档中）；
  ⑤ **生产文档/日志未被触碰**（大小与 mtime 不变）。

用法：python scripts/h565_verify_auto_accept.py
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
SCRIPT = ROOT / "scripts" / "h564_auto_accept.py"
REAL_DOC = ROOT / "研究结论" / "三件事验收_20260929.md"
REAL_LOG = ROOT / "logs" / "auto_accept.log"


def run(env_extra: dict) -> tuple:
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([str(PY), str(SCRIPT), "--apply"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def fingerprint(p: pathlib.Path) -> tuple:
    if not p.exists():
        return (0, 0.0)
    st = p.stat()
    return (st.st_size, round(st.st_mtime, 1))


def main() -> int:
    fails = 0
    doc_before, log_before = fingerprint(REAL_DOC), fingerprint(REAL_LOG)
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        vdir, doc, log, state = tdp / "out", tdp / "doc.md", tdp / "log.txt", tdp / "st.json"
        vdir.mkdir()
        doc.write_text("# 测试文档\n", encoding="utf-8")
        env = {"H564_VERDICT_DIR": str(vdir), "H564_DOC_PATH": str(doc),
               "H564_LOG_PATH": str(log), "H564_STATE_PATH": str(state)}

        print("① 无正式判定 ⇒ 不动作")
        rc, out = run(env)
        ok = rc == 0 and "无需动作" in out and doc.read_text(encoding="utf-8") == "# 测试文档\n"
        print(f"  {'✓' if ok else '✗'} rc={rc}")
        fails += 0 if ok else 1

        print("\n② 预演产物（dry_run=true）⇒ 绝不写入")
        (vdir / "h463_verdict.json").write_text(json.dumps(
            {"trial": "h463", "verdict": "PASS", "why": "test", "hours": 12.0,
             "trial": {"legs": 1, "net_bp": 0.0, "legs_per_hour": 70.0},
             "baseline": {"legs": 1, "net_bp": 0.0, "legs_per_hour": 62.0},
             "welch": {"p": 0.01, "delta": 1.0}, "freq_ok": True, "sub": {},
             "two_halves": {}, "breaks_in_window": [], "dry_run": True,
             "judged_at": "2026-09-29T13:48:00+00:00"}), encoding="utf-8")
        rc, out = run(env)
        ok = "AUTO-ACCEPT" not in doc.read_text(encoding="utf-8")
        print(f"  {'✓' if ok else '✗'} 文档未被写入（预演被正确排除）")
        fails += 0 if ok else 1

        print("\n③ 正式判定 ⇒ 写入")
        (vdir / "h463_verdict.json").write_text(json.dumps(
            {"trial": "h463", "verdict": "PASS", "why": "test-formal", "hours": 12.0,
             "trial": {"legs": 800, "net_bp": 1.0, "legs_per_hour": 70.0},
             "baseline": {"legs": 800, "net_bp": 0.5, "legs_per_hour": 62.0},
             "welch": {"p": 0.01, "delta": 1.0}, "freq_ok": True, "sub": {},
             "two_halves": {}, "breaks_in_window": [], "dry_run": False,
             "judged_at": "2026-09-29T13:49:00+00:00"}), encoding="utf-8")
        rc, out = run(env)
        txt = doc.read_text(encoding="utf-8")
        ok = ("AUTO-ACCEPT:h463" in txt and "2026-09-29T13:49:00+00:00" in txt)
        print(f"  {'✓' if ok else '✗'} rc={rc}，文档含验收块与 judged_at")
        fails += 0 if ok else 1

        print("\n④ 幂等：再跑一次不重复写入")
        before = fingerprint(doc)
        rc, out = run(env)
        after = fingerprint(doc)
        ok = before == after and "无需动作" in out
        print(f"  {'✓' if ok else '✗'} 文档未变化（{before} == {after}）")
        fails += 0 if ok else 1

    print("\n⑤ 生产文档/日志未被触碰")
    doc_after, log_after = fingerprint(REAL_DOC), fingerprint(REAL_LOG)
    ok = (doc_after == doc_before) and (log_after == log_before)
    print(f"  {'✓' if ok else '✗'} 文档 {doc_before}→{doc_after}；日志 {log_before}→{log_after}")
    fails += 0 if ok else 1

    print("\n" + "=" * 80)
    if fails:
        print(f"✗ {fails} 项未通过")
        return 1
    print("✓ 全部通过：预演被排除、正式判定自动写入、幂等、且生产文件零触碰。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
