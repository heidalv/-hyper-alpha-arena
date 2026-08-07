# -*- coding: utf-8 -*-
"""扫描 logs 下所有候选日志文件中 FactorEvo 启动/调度相关的行（带时间戳）"""
import os, io, sys, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOGS = r"d:\001Alpha\Hyper-Alpha-Arena\logs"
patterns = [
    (r"FactorEvo.{0,10}(启动|═══)", "启动"),
    (r"FactorEvo.{0,20}(三段切分|退化)", "切分"),
    (r"FactorEvo.{0,20}(GP 挖掘|MCTS 挖掘)", "挖掘"),
    (r"FactorEvo.{0,20}(阶段3|阶段2|阶段1)", "阶段"),
    (r"FactorEvo.{0,30}(初筛|purge|DSR|PBO|晋升|promote)", "清洗晋升"),
    (r"FactorEvo.{0,20}(DSR|PBO|dsr_sig)", "DSR/PBO"),
    (r"factor_evolution_daily|factor_online_weight_hourly", "调度"),
    (r"IC-WFO|run_factor_wfo|WFO", "WFO"),
    (r"FactorEvo.{0,20}(ICIR|初筛拒绝|不满足|淘汰)", "初筛"),
]
files = ["backend.log", "backend.log.1", "backend.out.final.log", "backend_restart_stdout.log", "backend.out.new.log", "backend.out.log", "backend_restart_out.txt"]
for fname in files:
    p = os.path.join(LOGS, fname)
    if not os.path.isfile(p):
        continue
    print("=" * 30, fname, "=" * 30)
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            for pat, tag in patterns:
                if re.search(pat, line):
                    ts = line[:22].strip()
                    # 只打印 08-0x 且包含关键行为的行
                    if "2026-08-0" in line or "2026-08-06" in line or "FactorEvo" in line:
                        print(f"[{tag}] {ts} {line.strip()[:230]}")
                    break
