"""查看 QAA v3 / analyst 线程 / maintain_mlto 最近日志"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

keys = re.compile(r'QAA v3|QAA_v3|analyst_system_v3|AnalystV3|maintain_mlto|analyst_v3|run_analyst_system|分析报告|Analyst.*report|\[QAA\]|\[Analyst\]')
tail = lines[-30000:]
out = []
for line in tail:
    if keys.search(line):
        out.append(line.rstrip())
print('匹配条数:', len(out))
for line in out[-40:]:
    print(line[:450])
