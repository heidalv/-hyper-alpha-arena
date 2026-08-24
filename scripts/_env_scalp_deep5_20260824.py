# -*- coding: utf-8 -*-
"""短线深挖参数落盘 5: FUSION_RR_FLOOR 对齐 MR 新结构 (UTF-8 安全)"""
import io

ENV = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
CHANGES = {
    # 融合仲裁 RR 地板 1.2→0.9：新 MR 结构 tp=sl=1.2% (rr=1.0) 靠胜率赚钱，
    # 被 1.2 地板恒拦（8/24 01:00 SOL short rr=1.0 → rr_below_floor）。
    # trend/lane 路径 min_rr 1.3 不受影响。回滚：改回 1.2。
    'FUSION_RR_FLOOR': '0.9',
}

with io.open(ENV, 'r', encoding='utf-8', errors='replace') as fh:
    lines = fh.read().splitlines()

seen = set()
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
        seen.add(k)
        out.append(f'{k}={CHANGES[k]}')
        print(f'[set] {k}={CHANGES[k]}  (原: {s[:60]})')
    else:
        out.append(ln)

for k in CHANGES:
    if k not in seen:
        out.append(f'{k}={CHANGES[k]}')
        print(f'[add] {k}={CHANGES[k]}')

with io.open(ENV, 'w', encoding='utf-8', newline='\n') as fh:
    fh.write('\n'.join(out) + '\n')
print('DONE env written')
