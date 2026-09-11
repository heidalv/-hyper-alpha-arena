import json, statistics as st
d = json.load(open('data/mm_param_sweep.json', encoding='utf-8'))
rows = d['rows']
print("配置数:", len(rows))
# 按净 bp 排序最佳，同时看金额最佳（净 USD 最大）
rows.sort(key=lambda r: -r['net_bp'])
print("\n=== 按净 bp 最佳 8 ===")
for r in rows[:8]:
    print(f"  w={r['w_base_bp']} k={r['k_inv']} hold={r['max_one_side_sec']}s sl={r['stop_loss_bp']} "
          f"net={r['net_bp']:+.3f}bp usd={r['net_usd']:+.2f} fills={r['fills']}")
rows.sort(key=lambda r: -r['net_usd'])
print("\n=== 按净 USD 最佳 8 ===")
for r in rows[:8]:
    print(f"  w={r['w_base_bp']} k={r['k_inv']} hold={r['max_one_side_sec']}s sl={r['stop_loss_bp']} "
          f"net={r['net_bp']:+.3f}bp usd={r['net_usd']:+.2f} fills={r['fills']}")
# 影子跑现行默认 (w=5, k=0.3, hold=112s, sl=25) 近似对比
for r in rows:
    if r['w_base_bp']==5.0 and r['k_inv']==0.3 and r['max_one_side_sec']==120.0:
        print(f"\n近似影子默认(w5/k0.3/h120): net={r['net_bp']:+.3f}bp usd={r['net_usd']:+.2f} fills={r['fills']} sl={r['stop_loss_bp']}")
        break
# per-symbol 最佳配置的币种表现
best = max(rows, key=lambda r: r['net_bp'])
print("\n最佳配置逐币:")
for s, v in best['per_symbol'].items():
    print(f"  {s:<6} net={v['net_bp']:+.3f}bp fills={v['fills']} flatten%={v['flatten_share']} hold_snaps={v['avg_hold_snaps']}")
