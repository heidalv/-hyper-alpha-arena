# -*- coding: utf-8 -*-
"""近期日志扫描：FactorEvo/MCTS/GP/card/WFO/DSR 关键行 + 触发上下文"""
import os, glob, io, sys, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOGS = r"d:\001Alpha\Hyper-Alpha-Arena\logs"

# 近 2 天
cutoff = datetime.datetime.now() - datetime.timedelta(days=2)
keys = ["[FactorEvo]", "GP 挖掘", "MCTS 挖掘", "MCTS 挖掘(scale", "card_generated", "报告卡",
        "WFO", "wfo", "测试集终审", "DSR/PBO", "因子进化完成", "AI因子", "Slimming",
        "factor_slimming", "因子发现", "mcts_chain", "在线权重", "从DB加载", "MCTS 挖掘", "阶段2 挖掘"]
files = []
for f in glob.glob(os.path.join(LOGS, "*")):
    try:
        if os.path.isfile(f) and os.path.getmtime(f) > cutoff.timestamp():
            files.append(f)
    except Exception:
        pass
files.sort(key=os.path.getmtime, reverse=True)

print("== 最近修改的日志文件(近2天) ==")
for f in files[:20]:
    print(" ", os.path.basename(f), datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%m-%d %H:%M:%S"))

print("")
print("== 含关键字的行（按时间排序） ==")
hits = []
for f in files:
    try:
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                for k in keys:
                    if k in line and "[FactorEvo]" in line:
                        hits.append((f, line.strip()[:300]))
                        break
    except Exception:
        continue
# 简单按文件名+行号排（文件已按时间排序，追加顺序即时间顺序）
for f, line in hits[-120:]:
    print(os.path.basename(f)[:30], "|", line)
