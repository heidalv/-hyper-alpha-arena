# -*- coding: utf-8 -*-
"""短线深挖参数落盘: 修改 .env (UTF-8 安全)"""
import io, os

ENV = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
CHANGES = {
    'SCALP_FACTOR_CONFIRM_THRESHOLD': '25',   # 30→25: 对齐 veto 带下限, 25-29 分进入样本积累
    'SCALP_SIZE_PCT': '0.90',                 # 0.50→0.90: 单笔基准仓位提升 (用户: 保证金太小=刷手续费)
    'SCALP_MIN_MARGIN_PCT': '0.08',           # 0.05→0.08: 保证金下限 1.57u→2.5u
    'SCALP_MR_MIN_RR': '0.75',                # 1.0→0.75: MR 靠胜率不靠盈亏比, 宽SL不再被RR钳回猎杀区
}

with io.open(ENV, 'r', encoding='utf-8', errors='replace') as fh:
    lines = fh.read().splitlines()

seen = {}
out = []
for ln in lines:
    s = ln.strip()
    if s.startswith('#') or '=' not in s:
        out.append(ln)
        continue
    k = s.split('=', 1)[0].strip()
    if k in CHANGES:
        if k in seen:
            print(f'[dup] {k} 重复行已跳过: {ln[:80]}')
            continue
        seen[k] = CHANGES[k]
        out.append(f'{k}={CHANGES[k]}')
        print(f'[set] {k}={CHANGES[k]}  (原: {s[:60]})')
    else:
        out.append(ln)

missing = [k for k in CHANGES if k not in seen]
for k in missing:
    out.append(f'{k}={CHANGES[k]}')
    print(f'[add] {k}={CHANGES[k]}')

with io.open(ENV, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write('\n'.join(out) + '\n')
print('DONE env written')
