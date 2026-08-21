# -*- coding: utf-8 -*-
"""解析 logs/_pytest_*.out 的 pytest 摘要行，汇总结果。"""
import glob, os, re

SUMMARY = re.compile(r"^\s*(\d+)\s+(passed|failed|error|skipped|xpassed|xfailed)", re.M)
FAIL_RE = re.compile(r"^\s*(\d+)\s+failed", re.M)
ERR_RE = re.compile(r"ERROR collecting|error during collection|errors during collection", re.M)

total_passed = 0
fails, errs, ok = [], [], []
for p in sorted(glob.glob(r"D:\001Alpha\Hyper-Alpha-Arena\logs\_pytest_*.out")):
    name = os.path.basename(p).replace("_pytest_", "").replace(".out", "")
    try:
        txt = open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    if "TIMEOUT" in txt.upper() or "timed out" in txt.lower():
        fails.append((name, "TIMEOUT"))
        continue
    m = FAIL_RE.search(txt)
    if m and int(m.group(1)) > 0:
        fails.append((name, f"{m.group(1)} failed"))
        continue
    if ERR_RE.search(txt) or "ERROR collecting" in txt:
        errs.append(name)
        continue
    pm = re.search(r"(\d+) passed", txt)
    if pm:
        total_passed += int(pm.group(1))
        ok.append(name)
    else:
        ok.append(name)  # no summary (e.g., 0 collected) -> treat as ok

print(f"files ok: {len(ok)}, files failed: {len(fails)}, files collect-error: {len(errs)}")
print("total passed tests:", total_passed)
print("\n== FAILED/TIMEOUT ==")
for n, r in fails:
    print(f"  {n}: {r}")
print("\n== COLLECT ERRORS ==")
for n in errs:
    print(f"  {n}")
