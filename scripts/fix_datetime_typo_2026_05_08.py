"""
2026-05-08: 批量修复 `datetime.now(datetime.timezone.utc)` typo

正确写法应为 `datetime.now(timezone.utc)`，前提是文件已
`from datetime import datetime, timezone`。

如果文件只 `from datetime import datetime`（没有 timezone），脚本会
自动把 import 行补全为 `from datetime import datetime, timezone`。

如果文件用的是 `import datetime`（顶层 module），则只把 typo 改成正确的
`datetime.timezone.utc` 用法 = `datetime.timezone.utc`（这个本身是合法的，
错就错在前面多了个 `datetime.now(...)` 才会抛错）— 不过 grep 出的都是
`datetime.now(datetime.timezone.utc)`，所以只需要批量替换。

使用：
    python3 scripts/fix_datetime_typo_2026_05_08.py
"""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

WRONG = "datetime.now(datetime.timezone.utc)"
RIGHT = "datetime.now(timezone.utc)"


EXCLUDE_DIR_PARTS = {".venv", "site-packages", "node_modules", "__pycache__"}


def _is_project_file(path: Path) -> bool:
    return not any(part in EXCLUDE_DIR_PARTS for part in path.parts)


def find_target_files() -> list[Path]:
    """用 ripgrep 找到所有受影响文件，避免 walk 整个仓库；排除 .venv/site-packages"""
    try:
        out = subprocess.run(
            [
                "rg", "-l", "--no-messages", "-F", WRONG,
                "-g", "!**/.venv/**", "-g", "!**/site-packages/**",
                "-g", "!**/node_modules/**", "-g", "!**/__pycache__/**",
                str(BACKEND),
            ],
            capture_output=True, text=True, check=False,
        )
        return [Path(l) for l in out.stdout.splitlines() if l and _is_project_file(Path(l))]
    except FileNotFoundError:
        files = []
        for p in BACKEND.rglob("*.py"):
            if not _is_project_file(p):
                continue
            if WRONG in p.read_text(encoding="utf-8", errors="ignore"):
                files.append(p)
        return files


def ensure_timezone_imported(text: str) -> tuple[str, bool]:
    """如 from datetime import 后面没有 timezone，则补加"""
    pat = re.compile(r"^from datetime import ([^\n]+)$", re.MULTILINE)
    hit = pat.search(text)
    if not hit:
        return text, False
    parts = [s.strip() for s in hit.group(1).split(",")]
    if "timezone" in parts:
        return text, False
    parts.append("timezone")
    new_line = "from datetime import " + ", ".join(parts)
    new_text = text[:hit.start()] + new_line + text[hit.end():]
    return new_text, True


def main() -> int:
    files = find_target_files()
    print(f"待修复文件: {len(files)}")
    fixed = 0
    import_patched = 0
    failed: list[tuple[Path, Exception]] = []
    for path in files:
        try:
            old = path.read_text(encoding="utf-8")
            new = old.replace(WRONG, RIGHT)
            new, patched = ensure_timezone_imported(new)
            if patched:
                import_patched += 1
            path.write_text(new, encoding="utf-8")
            fixed += 1
            count = old.count(WRONG)
            tag = " (+import)" if patched else ""
            print(f"  ✓ {path.relative_to(ROOT)} : {count} 处替换{tag}")
        except Exception as e:
            failed.append((path, e))
            print(f"  ✗ {path.relative_to(ROOT)} : {e}")
    print(f"\n汇总: 修复 {fixed} 个文件，补 import {import_patched} 个，失败 {len(failed)}")

    print("\n=== 编译检查 ===")
    py_compile_ok = 0
    py_compile_fail = []
    for path in files:
        ret = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        if ret.returncode == 0:
            py_compile_ok += 1
        else:
            py_compile_fail.append((path, ret.stderr))
    print(f"py_compile 通过: {py_compile_ok}/{len(files)}")
    if py_compile_fail:
        print("失败列表:")
        for p, err in py_compile_fail[:10]:
            print(f"  {p.relative_to(ROOT)}: {err[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
