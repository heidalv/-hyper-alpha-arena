# -*- coding: utf-8 -*-
"""短线深挖参数落盘 2: .env (UTF-8 安全)"""
import io

ENV = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
CHANGES = {
    # 区间过滤: 恢复 2026-07-01 修复值(0.97/0.03)。settings 默认仍是旧的 0.72/0.28
    # ——0.72 在上涨行情里恒触发"禁追多"，是趋势市短线停摆的隐藏元凶。
    'SCALP_RANGE_MAX_LONG': '0.97',
    'SCALP_RANGE_MIN_SHORT': '0.03',
    # Paper EV 地板: 旧校准口径下 EV 略负也放行攒样本（EV Governor 缩仓兜底）
    'SCALP_EV_MIN_PCT_PAPER': '-0.0030',
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
