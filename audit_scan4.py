# -*- coding: utf-8 -*-
"""扫描全部日志找 DSR/PBO/初筛/purge 关键行"""
import os, io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOGS = r"d:\001Alpha\Hyper-Alpha-Arena\logs"
pats = [re.compile(r"dsr_sig|DSR=|pbo=|best_icir|PBO=|初筛|硬门|门禁|淘汰|purge|Purge|PURGE|pool|Pool"),
        re.compile(r"purge_pipeline|run_purge_pipeline|stage[1-8]|正交|去重|CPCV")]
files = ["backend.log", "backend.log.1", "backend.log.2", "backend.log.3", "backend.log.4",
         "backend.log.5", "backend.log.6", "backend.log.7", "backend.log.8", "backend.log.9",
         "backend.log.10", "backend.error.log", "backend.error.log.1"]
for fname in files:
    p = os.path.join(LOGS, fname)
    if not os.path.isfile(p):
        continue
    hits = []
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "FactorEvo" not in line:
                continue
            for pat in pats:
                if pat.search(line):
                    hits.append(line.strip()[:235])
                    break
    if hits:
        print("=" * 20, fname, fname, "=" * 20)
        for h in hits[-50:]:
            print(" ", h)
