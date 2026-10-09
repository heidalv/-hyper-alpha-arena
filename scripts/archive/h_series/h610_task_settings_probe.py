"""h610 — 用 PowerShell 的**设置对象**（而非 XML 正则）核对哑判定任务的设置（R196b）。

为什么还要一个脚本：`h609` 用 XML 正则查 `ExecutionTimeLimit` 得到"(缺)"，而生产任务
（`Get-ScheduledTask` 的 `Settings.ExecutionTimeLimit`）是 **PT72H**。两者不一致时必须搞清：
  · 是我**正则/口径**的问题（XML 里本来就不写默认值），还是
  · 我补的 `Set-ScheduledTask -Settings (New-ScheduledTaskSettingsSet ...)` **真的把时长改了** ✗
    —— 后者会让"卡住的判定任务"一直跑下去，属于**引入风险**，必须修。

本脚本：建哑任务（永不过期的时间）→ 用 PowerShell 打印**设置对象**的每个关键字段 →
与生产 `H463_JUDGE` 并排对比 → 删除哑任务。不碰任何生产任务。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DUMMY = "DSH_HFT_SELFTEST_SWA2"
PROD = "DSH_HFT_H463_JUDGE"

PS = (
    "$ErrorActionPreference='Stop'; "
    "$s = Get-ScheduledTask -TaskName '{t}'; "
    "[pscustomobject]@{ "
    "SWA=$s.Settings.StartWhenAvailable; "
    "Limit=$s.Settings.ExecutionTimeLimit; "
    "Multi=$s.Settings.MultipleInstances; "
    "AllowBatt=$s.Settings.DisallowStartIfOnBatteries; "
    "StopBatt=$s.Settings.StopIfGoingOnBatteries; "
    "Logon=$s.Principal.LogonType; "
    "Enabled=$s.Settings.Enabled } | Format-List"
)


def ps_fields(task: str) -> str:
    # ⚠️ 不能用 `str.format`：命令里有 `{` `}`（pscustomobject 字面量）⇒ 会 KeyError ✗
    cmd = PS.replace("{t}", task)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    return (r.stdout or "").strip() or f"(无输出 rc={r.returncode}: {r.stderr[:120]})"


def main() -> int:
    print("=" * 84)
    print("h610 — 判定任务设置对象核对（哑任务 vs 生产）")
    print("=" * 84)
    spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)  # type: ignore[union-attr]

    subprocess.run(["schtasks", "/Delete", "/TN", DUMMY, "/F"],
                   capture_output=True, text=True, timeout=60)
    rc = h._schedule_judge(DUMMY, "h425_repair_trial.py", "SELFTEST",
                           dt.datetime(2027, 1, 1, 3, 0))
    print(f"  建哑任务 rc={rc}\n")
    print(f"  [哑任务 {DUMMY}]（经我补丁后的 _schedule_judge）")
    print("  " + ps_fields(DUMMY).replace("\n", "\n  "))
    print(f"\n  [生产 {PROD}]（R114 手工改过，作为基准）")
    print("  " + ps_fields(PROD).replace("\n", "\n  "))
    subprocess.run(["schtasks", "/Delete", "/TN", DUMMY, "/F"],
                   capture_output=True, text=True, timeout=60)
    print("\n  （哑任务已删除）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
