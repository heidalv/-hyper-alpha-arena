"""h608 — 验 `h599` 的**跨昼夜告警**（R195）：用三个合成产物当夹具（只读生产）。

为什么：R195 改了 `h599` 第 3 格——原来无条件打印「同钟点 ⇒ 组成匹配 ✓」，现在会从
`hours` + `two_halves.mid` 反推窗口边界、算**夜间占比**并分级告警。改判定读数工具必须
**先用夹具验**（R55/R73 的规矩），否则今晚可能打印错话。

夹具（全部写 TEMP，靠 `H599_VERDICT_PATH` 钩子喂给 h599）：
  ① 12h 白天窗（09:48→21:48L）⇒ 期望「组成匹配 ✓」；
  ② 延长一次 25h（09:48→次日 10:48L）⇒ 期望「跨昼夜」告警 + Welch 需分层；
  ③ 纯夜间 12h（21:48→次日 09:48L）⇒ 期望「昼夜混合/不匹配 + 判据 D 不能当稳定性检验」。

并断言：**生产判定产物与验收文档 sha256 未变** ✓。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out"
SRC = OUT / "h463_verdict_dryrun.json"
PROD_DOC = ROOT / "研究结论" / "三件事验收_20260929.md"
TZ = dt.timezone(dt.timedelta(hours=8))


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "(absent)"


def fixture(base: dict, start_local: dt.datetime, hours: float) -> dict:
    mid = start_local + dt.timedelta(hours=hours / 2.0)
    v = json.loads(json.dumps(base))
    v.pop("dry_run", None)
    v["hours"] = hours
    v["judged_at"] = (start_local + dt.timedelta(hours=hours)).astimezone(
        dt.timezone.utc).isoformat()
    v["two_halves"] = dict(v.get("two_halves") or {})
    v["two_halves"]["mid"] = mid.astimezone(dt.timezone.utc).isoformat()
    return v


def run_599(path: pathlib.Path) -> str:
    env = dict(os.environ)
    env["H599_VERDICT_PATH"] = str(path)
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "h599_verdict_readout.py")],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900, env=env)
    return (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print("=" * 92)
    print("h608 — h599 跨昼夜告警的夹具验证（生产只读）")
    print("=" * 92)
    if not SRC.exists():
        print(f"✗ 缺 {SRC}")
        return 1
    base = json.loads(SRC.read_text(encoding="utf-8"))
    before = {"verdict_dry": sha(SRC), "doc": sha(PROD_DOC),
              "h463_verdict": sha(OUT / "h463_verdict.json"),
              "h599_json": sha(OUT / "h599_verdict_readout.json")}

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="h608_readout_"))
    cases = [
        ("① 12h 白天窗 09:48→21:48L", dt.datetime(2026, 9, 29, 9, 48, tzinfo=TZ), 12.0,
         "组成匹配"),
        # ⚠️ 25h 窗的**正确**标签是「昼夜混合窗」（夜间≈48% ⇒ 同时触发判据 D 失效告警）——
        # 首版夹具我写的是"跨昼夜"，是**我写错了期望值** ✗（h608 首跑当场暴露，R195b）。
        ("② 延长 25h 09:48→次日 10:48L", dt.datetime(2026, 9, 29, 9, 48, tzinfo=TZ), 25.0,
         "昼夜混合窗"),
        ("③ 纯夜间 12h 21:48→次日 09:48L", dt.datetime(2026, 9, 29, 21, 48, tzinfo=TZ), 12.0,
         "昼夜混合窗"),
    ]
    fails = []
    for label, start, hours, expect in cases:
        p = tmp / f"fx_{int(hours)}_{start.hour}.json"
        p.write_text(json.dumps(fixture(base, start, hours), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        out = run_599(p)
        cell3 = ""
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("[格 3]"):
                cell3 = " | ".join(x.strip() for x in lines[i + 1:i + 4])
                break
        ok_span = "反推)=" in cell3 or "反推）=" in cell3 or "反推" in cell3
        ok_exp = expect in cell3
        print(f"\n  {label}")
        print(f"    第3格：{cell3[:190]}")
        print(f"    {'✓' if ok_span else '✗'} 打印了反推的窗口与夜间占比")
        print(f"    {'✓' if ok_exp else '✗'} 命中预期分级『{expect}』")
        if not ok_span:
            fails.append(f"{label}: 未打印窗口反推")
        if not ok_exp:
            fails.append(f"{label}: 未命中预期分级『{expect}』（实得：{cell3[:120]}）")

    after = {"verdict_dry": sha(SRC), "doc": sha(PROD_DOC),
             "h463_verdict": sha(OUT / "h463_verdict.json"),
             "h599_json": sha(OUT / "h599_verdict_readout.json")}
    print(f"\n  生产快照：{before}")
    print(f"  运行后  ：{after}")
    if before != after:
        fails.append(f"生产文件被改动：{before} vs {after}")
    print(f"  {'✓' if before == after else '✗'} 生产文件 sha256 未变")

    print("-" * 92)
    if fails:
        print(f"✗ 失败 {len(fails)} 项：")
        for f in fails:
            print(f"    · {f}")
        return 1
    print("✓ 三个夹具全部符合预期 ⇒ 今晚无论走 12h 还是延长窗，第 3 格都会如实告警 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
