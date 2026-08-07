"""查看 MasterController synthesize 调用最终结果"""
import re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

lines = []
with open(LOG, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# 找 22:08:13 那次 synthesize 之后的 stream done / 最终输出
keys = re.compile(r'MasterController.*\[DONE\]|MasterController.*stream_done|MasterController.*usage|MasterController.*synthesize.*完成|MasterController.*decoded|MasterController.*解析|MasterController.*决策|MasterController.*json|MasterController.*error|MasterController.*失败|MasterController.*fallback|MasterController.*回退|effective_content|synthesize done')
tail = lines[-20000:]
out = []
for line in tail:
    if 'MasterController' in line and keys.search(line):
        out.append(line.rstrip())
print('匹配条数:', len(out))
for line in out[-40:]:
    print(line[:450])
