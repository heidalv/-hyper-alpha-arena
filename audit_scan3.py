# -*- coding: utf-8 -*-
"""扫描轮转日志：1) 每日 03:01 调度执行历史  2) DSR/PBO 结果日志  3) purge/初筛日志"""
import os, io, sys, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOGS = r"d:\001Alpha\Hyper-Alpha-Arena\logs"
pat_launch = re.compile(r"FactorEvo.{0,8}(启动|═══|进化闭环)")
pat_dsr = re.compile(r"FactorEvo.{0,40}(dsr_sig|DSR|PBO|best_icir|初筛|门禁|硬门)")
files = ["backend.log.1", "backend.log.2", "backend.log.3", "backend.log.4", "backend.log.5",
         "backend.log.6", "backend.log.7", "backend.log.8", "backend.log.9", "backend.log.10",
         "backend.error.log", "backend.error.log.1"]
for fname in files:
    p = os.path.join(LOGS, fname)
    if not os.path.isfile(p):
        continue
    found = []
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "FactorEvo" not in line:
                continue
            if pat_launch.search(line):
                found.append("LAUNCH " + line.strip()[:200])
            elif pat_dsr.search(line):
                found.append("DSRPBO " + line.strip()[:230])
    if found:
        print("=" * 20, fname, "=" * 20)
        for l in found[-40:]:
            print(" ", l)
