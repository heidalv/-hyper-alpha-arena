import json, shutil, os, datetime
p = 'data/runtime_tuning.json'
bak = p + '.bak_20260909_midhold'
if not os.path.exists(bak):
    shutil.copy2(p, bak)
    print("backup ->", bak)
d = json.load(open(p, encoding='utf-8'))
old = d.get('tier_max_hold_sec', {}).get('mid')
d.setdefault('tier_max_hold_sec', {})['mid'] = 604800
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print("tier_max_hold_sec.mid:", old, "->", d['tier_max_hold_sec']['mid'])
