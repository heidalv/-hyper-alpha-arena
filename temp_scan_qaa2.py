"""搜索 analyst 相关日志"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

keys = re.compile(r'analyst|分析师|QAA|qaa', re.I)
tail = lines[-30000:]
out = []
for line in tail:
    if keys.search(line):
        out.append(line.rstrip())
print('匹配条数:', len(out))
for line in out[-30:]:
    print(line[:450])
