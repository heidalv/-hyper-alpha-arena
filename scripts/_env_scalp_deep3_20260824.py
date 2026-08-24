# -*- coding: utf-8 -*-
"""短线深挖参数落盘 3: .env (UTF-8 安全)"""
import io

ENV = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
CHANGES = {
    # 探索期豁免样本阈值 50→200：50 个样本摊到 20+ 币种/10 个分数桶根本不够
    # 校准器上修 p_win（8/24 实测 p_win 仍 0.342 旧口径），200 才能让新参数
    # 真正喂饱校准。
    'SCALP_NEW_PARAM_MIN_SAMPLES': '200',
    # Paper EV 地板 -0.30% → -0.60%：KAITO MR EV=-0.42%（p_win 0.450 旧口径）
    # 仍被恒拦；-0.60% 放行此类，仍拦 BNB 类 EV=-0.81% 的真负期望信号。
    'SCALP_EV_MIN_PCT_PAPER': '-0.0060',
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
