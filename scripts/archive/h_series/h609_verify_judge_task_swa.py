"""h609 — 验 `_schedule_judge` 补的 **StartWhenAvailable**（R196；不碰生产任务）。

背景：`schtasks /Create` 没有 StartWhenAvailable 开关 ⇒ 新建判定任务默认 False；
R114 是手工把**当时已存在**的四个关键任务改成 True 的（实测 `H463_JUDGE`=True，
而更早由本函数建的 `H425_JUDGE`=False ✗）⇒ 明天 ③/④ 部署出来的判定任务会**退回 False**，
而那两个时刻我不在场。已给 `_schedule_judge` 补一条设置；本脚本**用哑任务名**验证它真的生效。

安全性（为什么这不是"动关键路径"）：
  · 用**哑任务名** `DSH_HFT_SELFTEST_SWA`，触发时间排到 **2027**（永不会跑）；
  · 传的 trial key 是 `SELFTEST`（不在 SPECS 里）⇒ 即便真跑了也只会报"未知试跑" ✗，无害；
  · 全程不触碰任何 `DSH_HFT_H*` 生产任务的**设置**（`/F` 只作用于哑任务名）；
  · 结束时**删除**哑任务；并断言生产任务（H463_JUDGE / 两条链）的设置与 Next Run 未变 ✓。

用法：python scripts/h609_verify_judge_task_swa.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DUMMY = "DSH_HFT_SELFTEST_SWA"
PROD = ["DSH_HFT_H463_JUDGE", "DSH_HFT_H464_CHAIN", "DSH_HFT_H527_CHAIN", "DSH_HFT_H425_JUDGE"]


def query_xml(task: str) -> str:
    r = subprocess.run(["schtasks", "/Query", "/TN", task, "/XML"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    return r.stdout or ""


def swa_of(task: str) -> str:
    m = re.search(r"<StartWhenAvailable>(\w+)</StartWhenAvailable>", query_xml(task))
    return m.group(1) if m else "(缺)"


def snap() -> dict:
    out = {}
    for t in PROD:
        xml = query_xml(t)
        m_next = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", xml)
        out[t] = (swa_of(t), m_next.group(1) if m_next else "(缺)")
    return out


def main() -> int:
    print("=" * 88)
    print("h609 — 判定任务 StartWhenAvailable 的验证（哑任务名，不碰生产）")
    print("=" * 88)
    spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)  # type: ignore[union-attr]

    before = snap()
    print("  生产任务快照（SWA, StartBoundary）：")
    for k, v in before.items():
        print(f"    {k:<22} {v}")
    fails = []

    subprocess.run(["schtasks", "/Delete", "/TN", DUMMY, "/F"],
                   capture_output=True, text=True, timeout=60)   # 清掉可能的残留

    at = dt.datetime(2027, 1, 1, 3, 0)
    rc = h._schedule_judge(DUMMY, "h425_repair_trial.py", "SELFTEST", at)
    print(f"\n  _schedule_judge rc={rc}（哑任务 {DUMMY} @ {at:%Y-%m-%d %H:%M}）")
    got = swa_of(DUMMY)
    ok = (got == "true")
    print(f"  {'✓' if ok else '✗'} 哑任务 StartWhenAvailable={got}（期望 true）")
    if not ok:
        fails.append(f"StartWhenAvailable 未生效（得到 {got}）")

    xml = query_xml(DUMMY)
    # [R196b] 原来用 **XML 正则**查 ExecutionTimeLimit ⇒ 得到"(缺)"并判失败 ✗ —— 但那是
    # **口径问题**：`schtasks /Query /XML` 在取默认值时不写该元素，而设置对象是权威的。
    # 用 PowerShell 的 `Settings` 对象核对（`h610` 已用哑任务证明它与生产基准逐字一致）。
    ps = ("$s=Get-ScheduledTask -TaskName '" + DUMMY + "'; "
          "'SWA='+$s.Settings.StartWhenAvailable; "
          "'Limit='+$s.Settings.ExecutionTimeLimit; "
          "'Multi='+$s.Settings.MultipleInstances")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    vals = dict(
        (ln.split("=", 1)[0], ln.split("=", 1)[1].strip())
        for ln in (r.stdout or "").splitlines() if "=" in ln)
    for tag, want in (("Limit", "PT72H"), ("Multi", "IgnoreNew")):
        v = vals.get(tag, "(缺)")
        good = (v == want)
        print(f"  {'✓' if good else '✗'} {tag}={v}（期望 {want}，按 Settings 对象读）")
        if not good:
            fails.append(f"{tag}={v} 期望 {want}")

    subprocess.run(["schtasks", "/Delete", "/TN", DUMMY, "/F"],
                   capture_output=True, text=True, timeout=60)
    gone = query_xml(DUMMY).strip()
    print(f"  {'✓' if not gone else '✗'} 哑任务已删除")

    after = snap()
    same = (before == after)
    print(f"\n  {'✓' if same else '✗'} 生产任务快照未变")
    if not same:
        for k in before:
            if before[k] != after[k]:
                print(f"    ✗ {k}: {before[k]} → {after[k]}")
        fails.append("生产任务被改动")

    print("-" * 88)
    if fails:
        print(f"✗ 失败 {len(fails)} 项：")
        for f in fails:
            print(f"    · {f}")
        return 1
    print("✓ 通过：明天 ③/④ 部署出来的判定任务会带上 StartWhenAvailable（错过触发可补跑）✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
