"""查看 TrendAgent 独立路径 / midlong 循环 最新日志"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

print('总行数:', len(lines))

# TrendAgent 独立路径 & score 相关 & gate 相关
keys = re.compile(r'\[TrendAgent|score_low|score=|why=|开仓|门控|gate|GATE|_trend_one|trend_action|hold.*dir=|独立')
tail = lines[-15000:]
out = []
for i, line in enumerate(tail):
    if keys.search(line):
        out.append(line.rstrip())
# 只保留最近 60 条
for line in out[-60:]:
    print(line[:420])
