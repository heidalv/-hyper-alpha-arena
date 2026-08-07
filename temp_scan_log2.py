"""查看 backend.log 最后 N 行关键事件"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

# 只取最近 3000 行中的关键事件
lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

print('总行数:', len(lines))
tail = lines[-4000:]

keys = re.compile(r'数据过期|MLTO|thesis|TrendAgent|MasterController|synthesize|无 LLM 配置|无归属用户|规则回退|skip|缺失|数据门控|get_llm_config_for_analysis|PEO|hold.*score|score_low|分析超时|stub')
for line in tail:
    if keys.search(line):
        print(line.rstrip()[:400])
