import json
d=json.load(open('data/seed_factor_eval.json',encoding='utf-8'))
print("n_candidates:", d["n_candidates"], "n_passed:", d["n_passed"])
print(f"{'候选':<32}{'IC':>9}{'ICIR':>8}{'Sharpe':>9}{'OOS净':>11}{'笔数':>7}{'每笔净(bp)':>11}")
for r in d["rows"]:
    if "error" in r: 
        print(f"{r['id']:<32} ERROR {r['error'][:50]}"); continue
    n=max(1,int(r.get("oos_trades") or 1))
    per=r["oos_net_return"]/n*10000
    print(f"{r['id']:<32}{r['ic_mean']:>9.4f}{r['icir']:>8.3f}{r['oos_sharpe']:>9.3f}{r['oos_net_return']:>11.5f}{n:>7}{per:>11.2f}")
