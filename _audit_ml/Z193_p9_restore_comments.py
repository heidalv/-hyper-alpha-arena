# -*- coding: utf-8 -*-
"""Z193（P9 执行）：把**可验证**的注释恢复进 `.env`（仅限完好来源 + 键值未变）。

原则：
  * 只从**完好**来源取注释：`.env.example`、`backend/.env.example`、
    `.env.framework_rollout_backup_20260710`（84 行，UTF-8 完好）；
  * **仅当该键在当前 `.env` 里的值与来源里的值相同**时才写注释（避免注释与值矛盾）；
  * 写入时标注出处 `[恢复自 <source>]`，不冒充原文；
  * 先备份整个 `.env`，再逐行替换；最后用解析对比确认**任何键值都没变**。

用法：python _audit_ml/Z193_p9_restore_comments.py           # dry-run
      python _audit_ml/Z193_p9_restore_comments.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
ENV = ROOT / ".env"
SOURCES = [".env.example", "backend/.env.example", ".env.framework_rollout_backup_20260710"]


def parse_kv(path: Path) -> dict[str, str]:
    out = {}
    for ln in path.read_text(encoding="utf-8-sig", errors="replace").split("\n"):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            out[s.split("=", 1)[0].strip()] = s.split("=", 1)[1].strip()
    return out


def load_comments() -> dict[str, tuple[str, str, str]]:
    """key -> (comment, source_name, source_value)"""
    out: dict[str, tuple[str, str, str]] = {}
    for name in SOURCES:
        p = ROOT / name
        if not p.exists():
            continue
        kv = parse_kv(p)
        pending: list[str] = []
        for ln in p.read_text(encoding="utf-8-sig", errors="replace").split("\n"):
            s = ln.strip()
            if s.startswith("#"):
                t = s.lstrip("# ").strip()
                if t and not re.fullmatch(r"[-=*#\s]+", t):
                    pending.append(t)
                continue
            if s and "=" in s:
                k = s.split("=", 1)[0].strip()
                if pending and k not in out:
                    out[k] = (" / ".join(pending[-2:]), name, kv.get(k, ""))
                pending = []
            elif not s:
                pending = []
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    lines = ENV.read_text(encoding="utf-8-sig", errors="replace").split("\n")
    kv_now = parse_kv(ENV)
    comments = load_comments()

    planned: list[tuple[int, str, str, str]] = []  # (lineno0, key, comment, source)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        meta = comments.get(k)
        if not meta:
            continue
        # 该键上方 3 行内是否有 '?' 注释
        has_damaged = any(lines[j].strip().startswith("#") and "?" in lines[j]
                          for j in range(max(0, i - 3), i))
        if not has_damaged:
            continue
        if kv_now.get(k, "") != meta[2]:
            continue  # 值不同 ⇒ 描述可能过时，跳过（口径：宁可少恢复也不写错）
        planned.append((i, k, meta[0], meta[1]))

    print(f"可恢复（值一致 + 完好来源）：{len(planned)} 条")
    for i, k, c, src in planned:
        print(f"  {k}: [{src}] {c[:80]}")

    if not args.apply:
        print("\n（dry-run，未写入；加 --apply 执行）")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = ROOT / f".env.bak_p9_{ts}"
    shutil.copy2(ENV, backup)
    print(f"\n已备份 -> {backup.name}")

    for i, k, c, src in planned:
        # 把该键上方连续的 '?' 注释行替换为一条带出处的恢复行
        j = i - 1
        replaced = 0
        while j >= 0 and lines[j].strip().startswith("#") and "?" in lines[j]:
            if replaced == 0:
                lines[j] = f"# [恢复自 {src}] {c}"
            else:
                lines[j] = ""
            j -= 1
            replaced += 1
    ENV.write_text("\ufeff" + "\n".join(lines), encoding="utf-8")

    kv_after = parse_kv(ENV)
    same = kv_now == kv_after
    print(f"键值对是否完全未变：{'✅ 是' if same else '❌ 否'}"
          f"（前 {len(kv_now)} 个 / 后 {len(kv_after)} 个）")
    if not same:
        diff = {k: (kv_now.get(k), kv_after.get(k)) for k in set(kv_now) | set(kv_after)
                if kv_now.get(k) != kv_after.get(k)}
        print("  差异:", list(diff.items())[:5])
        print("  ⚠ 已回滚，请检查脚本")
        shutil.copy2(backup, ENV)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
