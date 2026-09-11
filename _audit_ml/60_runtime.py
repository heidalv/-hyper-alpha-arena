import json
for f in ['data/runtime_tuning.json']:
    d=json.load(open(f,encoding='utf-8'))
    print("=== ",f)
    print(json.dumps(d,ensure_ascii=False,indent=2)[:3000])
