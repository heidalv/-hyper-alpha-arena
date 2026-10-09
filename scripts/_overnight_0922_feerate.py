import json, pathlib, collections
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
f=pathlib.Path("logs/mm_fill_basis.jsonl")
fr=collections.Counter(); flat=collections.Counter()
n=0
for line in f.read_text(encoding="utf-8",errors="replace").splitlines():
    if not line.strip(): continue
    try: d=json.loads(line)
    except Exception: continue
    n+=1
    fr[d.get("fee_rate")]+=1
    flat[(bool(d.get("flatten")), d.get("fee_rate"))]+=1
p(f"mm_fill_basis.jsonl 行数 {n}")
p("fee_rate 取值分布:", dict(fr))
p("(flatten, fee_rate) 组合:", {f"{k[0]}/{k[1]}": v for k,v in flat.items()})
pathlib.Path("logs/_tmp_timeline/feerate.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
