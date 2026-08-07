"""查看中长线独立 tick / TrendAgent 独立 / thesis 线程日志"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

print('总行数:', len(lines))

keys = re.compile(r'\[MidLongAgent独立\]|\[MidLong\]|_trend_one|_thesis_llm_one|TrendAgent独立|run_mlto_tick|MLTO.*skip|non-fixed|qual_layer|thesis_update|无 LLM 配置|thesis.*LLM|MLTO.*失败|MLTO.*error')
tail = lines[-25000:]
out = []
for i, line in enumerate(tail):
    if keys.search(line):
        out.append(line.rstrip())
print('匹配条数:', len(out))
for line in out[-80:]:
    print(line[:450])
