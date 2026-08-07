"""查看 master 长线 stub/超时 + TrendAgent score 来源 + 22:04 stopped 问题"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

print('总行数:', len(lines))

# 1. master 长线 stub / 超时 / 降级
keys1 = re.compile(r'\[long tier stub\]|深度分析超时|降级为保守|master.*stub|MasterController.*hold|master_execute.*hold|数据门控.*禁止')
tail = lines[-60000:]
out1 = []
for line in tail:
    if keys1.search(line):
        out1.append(line.rstrip())
print('\n===== master stub/超时 (%d) =====' % len(out1))
for line in out1[-15:]:
    print(line[:400])

# 2. TrendAgent 分析 LLM 调用
keys2 = re.compile(r'\[TrendAgent:direction\]|\[TrendAgent\]|analyze_direction|score 计算|trend_score|TREND.*LLM|TrendAgent.*reasoning')
out2 = []
for line in tail:
    if keys2.search(line):
        out2.append(line.rstrip())
print('\n===== TrendAgent 分析 (%d) =====' % len(out2))
for line in out2[-20:]:
    print(line[:400])

# 3. status=stopped 的 tick
keys3 = re.compile(r'\[MidLongAgent独立\] 启动.*stopped|status=stopped')
out3 = []
for line in tail:
    if keys3.search(line):
        out3.append(line.rstrip())
print('\n===== status=stopped tick (%d) =====' % len(out3))
for line in out3[-10:]:
    print(line[:400])
