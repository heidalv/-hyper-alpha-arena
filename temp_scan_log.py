"""扫描 backend.log 关键模式"""
import os, re

LOG = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'

patterns = {
    '数据缺失/门控': re.compile(r'数据缺失|缺失:|禁止.*假方向|数据门控|数据过期|stale', re.I),
    '规则回退': re.compile(r'规则回退|无 LLM 配置|无归属用户|fallback', re.I),
    'MLTO/长线': re.compile(r'MLTO|mlto|thesis|TrendAgent独立|trend_independent|midlong', re.I),
    'master决策': re.compile(r'\[Master|master_execute|MasterController|synthesize', re.I),
    '错误/异常': re.compile(r'ERROR|Traceback|Exception|LockNotAvailable|OperationalError', re.I),
    'LLM调用': re.compile(r'\[LLM\]|llm_usage|call_llm_api', re.I),
}

counts = {k: 0 for k in patterns}
samples = {k: [] for k in patterns}

with open(LOG, encoding='utf-8', errors='replace') as f:
    for line in f:
        for k, pat in patterns.items():
            if pat.search(line):
                counts[k] += 1
                if len(samples[k]) < 6:
                    samples[k].append(line.rstrip()[:500])

for k in patterns:
    print('\n===== %s: %d 条 =====' % (k, counts[k]))
    for s in samples[k]:
        print('  ', s)
