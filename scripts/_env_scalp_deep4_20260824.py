# -*- coding: utf-8 -*-
"""短线深挖参数落盘 4: .env (UTF-8 安全)"""
import io

ENV = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
CHANGES = {
    # Paper 组合预算检查：evaluate_open 历史 45s+ 挂起热点。scalp_loop 注释默认
    # paper 跳过（PB_PAPER_SKIP=true），但 .env 被显式改为 false → 每次开单尝试
    # 都跑 6s 超时放行，白白拖慢热路径。Paper 有日亏损熔断兜底，恢复跳过。
    'PB_PAPER_SKIP': 'true',
    # 融合仲裁 pwin 主阈值 0.55→0.30：0.55 是旧 TP/SL 结构回放证据
    # （<0.55 桶 WR 40.7%）；新 MR 结构(tp=sl=1.2%)+EV地板放行下，模拟盘需要
    # 重新给 meta 模型喂新结构样本。放行后由 SIZE_OBSERVE 小仓 + EV Governor
    # 缩仓兜底。回滚：改回 0.55。
    'FUSION_SCALP_PWIN_MIN': '0.30',
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
