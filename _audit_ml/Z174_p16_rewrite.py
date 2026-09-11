# -*- coding: utf-8 -*-
"""Z174（P16 执行器）：把非限定导入 `services.*` / `config.*` … 统一成 `backend.*`。

安全性设计：
  1. 只处理 **AST 认定** 的 `Import` / `ImportFrom`（level==0）节点所在行，避免误改注释/字符串；
  2. 逐行只做**前缀替换**，不做全文替换；
  3. 混合导入（`import os, services.x`）与动态字符串导入**不自动改**，单独列出来人工处理；
  4. 改动前把每个文件备份到 `_audit_ml/p16_backup_<ts>/`（保持相对路径），可一键还原；
  5. 排除隔离区/归档区/死树/__pycache__。

用法：
  python _audit_ml/Z174_p16_rewrite.py --dry-run    # 只报告
  python _audit_ml/Z174_p16_rewrite.py --apply      # 执行 + 备份
  python _audit_ml/Z174_p16_rewrite.py --restore <ts>  # 还原某次备份
"""
from __future__ import annotations

import argparse
import ast
import codecs
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
BE = ROOT / "backend"

ROOTS = ("services", "config", "models", "repositories", "version", "utils", "api", "workers",
         "factors", "migrate_to_postgresql")
EXCLUDE_PARTS = ("__pycache__", "_ai_gen_archive", "_ai_gen_quarantine", "_pytest_tmp",
                 ".venv", "site-packages", "node_modules")
# 死树：无 __init__.py、无引用（§64.4），不参与本次改写
EXCLUDE_PREFIXES = (str(BE / "factor_engine") + os.sep,)

FROM_RX = re.compile(r"^(\s*from\s+)(" + "|".join(ROOTS) + r")(\.[\w.]*)?(\s+import\b)")
IMPORT_RX = re.compile(r"^(\s*import\s+)(" + "|".join(ROOTS) + r")(\.[\w.]*)?(\s*(?:as\s+\w+)?\s*(?:,|$))")
DYNAMIC_RX = re.compile(r"""["']((?:""" + "|".join(ROOTS) + r"""))\.[\w.]+["']""")


def iter_files() -> list[Path]:
    out = []
    for py in sorted(BE.rglob("*.py")):
        p = str(py)
        if any(part in p for part in EXCLUDE_PARTS):
            continue
        if any(p.startswith(pre) for pre in EXCLUDE_PREFIXES):
            continue
        out.append(py)
    return out


def read_source(py: Path) -> tuple[str, bool]:
    """读取源码，**剥离 UTF-8 BOM**（否则 ast.parse 抛 SyntaxError → 文件被静默跳过）。

    [§66 实测] `backend/services/ai_decision_service.py` 带 BOM，导致第一轮改写
    与"完整性测试"用了同一套读取逻辑 ⇒ **双双漏掉该文件**（假绿）。裸导入
    `from repositories import prompt_repo` 就是这么活下来的。
    """
    raw = py.read_bytes()
    has_bom = raw.startswith(codecs.BOM_UTF8)
    src = raw[3:].decode("utf-8", errors="replace") if has_bom else raw.decode("utf-8", errors="replace")
    return src, has_bom


def analyse(py: Path, src: str | None = None) -> tuple[list[int], list[int], list[str]]:
    """返回 (可安全改的行号, 混合导入行号, 动态字符串提示)。"""
    if src is None:
        src, _ = read_source(py)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], [], []
    lines = src.splitlines()
    simple: list[int] = []
    mixed: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if node.module.split(".", 1)[0] not in ROOTS:
                continue
            ln = node.lineno
            if FROM_RX.match(lines[ln - 1]):
                simple.append(ln)
            else:
                mixed.append(ln)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ROOTS:
                    continue
                ln = node.lineno
                if IMPORT_RX.match(lines[ln - 1]):
                    simple.append(ln)
                else:
                    mixed.append(ln)
                break
    dyn = [m.group(0) for m in DYNAMIC_RX.finditer(src)]
    return sorted(set(simple)), sorted(set(mixed)), sorted(set(dyn))


def rewrite_file(py: Path) -> int:
    src, has_bom = read_source(py)
    lines = src.splitlines(keepends=True)
    simple, mixed, _ = analyse(py, src)
    changed = 0
    for ln in simple:
        i = ln - 1
        new = FROM_RX.sub(r"\1backend.\2\3\4", lines[i], count=1)
        if new == lines[i]:
            new = IMPORT_RX.sub(r"\1backend.\2\3\4", lines[i], count=1)
        if new != lines[i]:
            lines[i] = new
            changed += 1
    if changed:
        body = "".join(lines)
        py.write_bytes((codecs.BOM_UTF8 + body.encode("utf-8")) if has_bom else body.encode("utf-8"))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", default="")
    args = ap.parse_args()

    if args.restore:
        backup = ROOT / "_audit_ml" / f"p16_backup_{args.restore}"
        if not backup.is_dir():
            print("备份不存在:", backup)
            return 2
        n = 0
        for f in backup.rglob("*.py"):
            rel = f.relative_to(backup)
            shutil.copy2(f, ROOT / rel)
            n += 1
        print(f"已还原 {n} 个文件")
        return 0

    total_lines = total_files = 0
    mixed_all: list[str] = []
    dyn_all: list[str] = []
    touched: list[Path] = []
    for py in iter_files():
        src, _bom = read_source(py)
        simple, mixed, dyn = analyse(py, src)
        if simple:
            total_lines += len(simple)
            total_files += 1
            touched.append(py)
        if mixed:
            body = src.splitlines()
            for ln in mixed:
                mixed_all.append(f"{py.relative_to(ROOT)}:{ln}: {body[ln-1].strip()[:110]}")
        if dyn:
            for d in dyn:
                dyn_all.append(f"{py.relative_to(ROOT)}: {d}")

    print(f"待改：{total_files} 个文件 / {total_lines} 行")
    print("受影响文件（前 25 个）:")
    for py in touched[:25]:
        print("   ", py.relative_to(ROOT))
    print(f"混合导入行（需人工）: {len(mixed_all)}")
    for m in mixed_all[:15]:
        print("   ", m)
    print(f"动态字符串导入: {len(dyn_all)}")
    for d in dyn_all[:15]:
        print("   ", d)

    if args.dry_run or not args.apply:
        print("\n（dry-run，未写入）")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = ROOT / "_audit_ml" / f"p16_backup_{ts}"
    n_changed = 0
    for py in touched:
        dst = backup / py.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(py, dst)
        n_changed += rewrite_file(py)
    print(f"\n已备份到 {backup}\n已改写 {n_changed} 行（{len(touched)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
