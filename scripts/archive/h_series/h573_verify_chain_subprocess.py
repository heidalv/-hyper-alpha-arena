"""h573 — 实测链的子进程封装 `run()` / `run_script()`（只读，R75）。

为什么：`h464_chain` 明早第一次真跑，而它的**子进程调用**从未被实测过。
计划任务经 `run-quiet.vbs` 启动 ⇒ **cwd = C:\\Windows\\System32**（任务 `Start In: N/A`
且 vbs 的 `sh.Run cmd,0,False` 不设工作目录）⇒ 若链里用了相对路径就会失败。

本脚本在**同一模块**上直接调用这两个封装（它们内部用绝对路径 + `cwd=ROOT`）：
  1) `run_script("scripts/h481_runtime_param_echo.py", ["--key","max_one_side_seconds"])`
     —— 这正是链在部署 ③ 之后读"运行态回显"的那一步；
  2) `run(["--trial","h463","--judge","--dry-run"])`
     —— 链调用试跑脚本的方式（此处加 `--dry-run` 保证**只读、不落库**）。
两者都不改任何参数 ✓。

用法：python scripts/h573_verify_chain_subprocess.py   （务必以 System32 为 cwd 跑一次）
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    # 钩子：避免任何写生产路径的可能（本脚本只调用只读命令，双保险）
    os.environ.setdefault("H464_LOG_PATH", str(pathlib.Path(os.environ.get("TEMP", "."))
                                               / "h573_chain_probe.log"))
    spec = importlib.util.spec_from_file_location("h464c", ROOT / "scripts" / "h464_chain.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    print(f"cwd={os.getcwd()}")
    print(f"链模块解析：PY={m.PY.name} TRIAL={m.TRIAL.name} ROOT={m.ROOT.name}")
    ok = True

    rc1, out1 = m.run_script("scripts/h481_runtime_param_echo.py",
                             ["--key", "max_one_side_seconds"])
    hit = "✓ 一致" in out1
    print(f"[1] run_script(h481) rc={rc1} 含『✓ 一致』={hit}")
    for ln in out1.strip().splitlines()[-4:]:
        print(f"      {ln.strip()[:110]}")
    ok &= (rc1 == 0 and hit)

    rc2, out2 = m.run(["--trial", "h463", "--judge", "--dry-run"])
    has_verdict = '"verdict"' in out2
    print(f"[2] run(h463 --judge --dry-run) rc={rc2} 含 verdict={has_verdict}")
    ok &= (rc2 == 0 and has_verdict)

    print("=" * 78)
    print("✓ 链的子进程封装在计划任务的真实 cwd 下可用" if ok else "✗ 有失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
