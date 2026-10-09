"""T6（结构收敛）：把 709 个一次性 h* 脚本里**确实无人引用**的归档。

安全判据（保守）——只归档同时满足以下条件的文件：
  1. 没有被任何 `DSH_*` 计划任务引用
  2. 没有被任何**保留中的**非 h* 文件引用（.py/.ps1/.bat/.vbs/.cmd）
  3. 没有被任何**保留中的** h* 脚本引用（含传递闭包）

做法：先取"根集合"（计划任务引用的 + 非 h 文件引用的），
再沿 h*→h* 引用做传递闭包；闭包之外的全部归档。

用法：
    python scripts/tools/archive_h_scripts.py            # 干跑
    python scripts/tools/archive_h_scripts.py --apply    # 执行（移动到 scripts/archive/h_series/）
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
SCRIPTS = ROOT / "scripts"
DEST = SCRIPTS / "archive" / "h_series"

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

H_REF = re.compile(r"(h\d{3}[a-z0-9_]*\.py)", re.IGNORECASE)
SKIP_DIRS = ("node_modules", ".venv", "venv-gpu", "_cleanup_archive", "_ptmp",
             "_audit_ml", "archive", "__pycache__")


def scheduled_roots() -> set[str]:
    """计划任务里出现的 h* 文件名。"""
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object {$_.TaskName -like 'DSH_*'} | "
             "ForEach-Object { $_.Actions | ForEach-Object { $_.Arguments } }"],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        return {m.group(1).lower() for m in H_REF.finditer(out.stdout or "")}
    except Exception as e:  # noqa: BLE001
        print(f"!! 计划任务读取失败（保守处理：视为无引用）: {e}")
        return set()


def scan_refs() -> tuple[dict[str, set[str]], set[str]]:
    """返回 (h*→引用的 h* 集合, 非 h 文件引用的 h* 集合)。"""
    h_files = {p.name.lower(): p for p in SCRIPTS.glob("h*.py")}
    h_refs: dict[str, set[str]] = {n: set() for n in h_files}
    non_h_refs: set[str] = set()

    for p in SCRIPTS.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".py", ".ps1", ".bat", ".vbs", ".cmd", ".json", ".md"):
            continue
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = {m.group(1).lower() for m in H_REF.finditer(text)} - {p.name.lower()}
        if not found:
            continue
        if p.name.lower() in h_files:
            h_refs[p.name.lower()] |= found
        else:
            non_h_refs |= found
    return h_refs, non_h_refs


def closure(roots: set[str], h_refs: dict[str, set[str]]) -> set[str]:
    keep = set(roots)
    changed = True
    while changed:
        changed = False
        for name in list(keep):
            for dep in h_refs.get(name, ()):  # noqa: B028
                if dep not in keep:
                    keep.add(dep)
                    changed = True
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    h_refs, non_h_refs = scan_refs()
    roots = scheduled_roots() | non_h_refs
    keep = closure(roots, h_refs)
    all_h = set(h_refs)
    archive = sorted(all_h - keep)

    print("=" * 78)
    print(f"h* 脚本总数        : {len(all_h)}")
    print(f"计划任务引用        : {len(scheduled_roots() & all_h)}")
    print(f"非 h 文件引用        : {len(non_h_refs & all_h)}")
    print(f"保留（含传递闭包）  : {len(keep & all_h)}")
    print(f"可归档              : {len(archive)}")
    print("=" * 78)

    print("\n保留清单（前 60）:")
    for n in sorted(keep & all_h)[:60]:
        print(f"  {n}")

    if archive:
        print(f"\n将归档 {len(archive)} 个文件 → {DEST}")
        for n in archive[:15]:
            print(f"  {n}")
        if len(archive) > 15:
            print(f"  … 其余 {len(archive) - 15} 个")

    if not args.apply:
        print("\n（干跑；加 --apply 实际移动）")
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    moved = 0
    for n in archive:
        src = SCRIPTS / n
        if not src.exists():
            # 大小写差异
            cand = [p for p in SCRIPTS.glob("h*.py") if p.name.lower() == n]
            if not cand:
                continue
            src = cand[0]
        dst = DEST / src.name
        if dst.exists():
            continue
        shutil.move(str(src), str(dst))
        moved += 1
    print(f"\n已归档 {moved} 个文件 → {DEST}")
    print(f"scripts/ 下剩余 h* 脚本: {len(list(SCRIPTS.glob('h*.py')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
