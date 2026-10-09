import json, pathlib, psycopg
def dsn():
    env={}
    for line in pathlib.Path(".env").read_text(encoding="utf-8",errors="replace").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1); env[k.strip()]=v.strip().strip('"').strip("'")
    u=env["DATABASE_URL"]
    for j in ("+psycopg2","+psycopg","+asyncpg"): u=u.replace(j,"")
    return u
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
with psycopg.connect(dsn()) as c:
    cur=c.cursor()
    cur.execute("select meta_json, risk_json, edge_json, health_json, updated_at from lane_registry where lane_id='mm_asterdex'")
    meta, risk, edge, health, upd = cur.fetchone()
    p("registry updated_at:", upd)
    p("\n== meta_json ==")
    for k,v in meta.items():
        if k in ("params","limits"):
            p(f"  {k}:")
            for kk,vv in sorted(v.items()): p(f"      {kk:<34}= {vv}")
        else:
            p(f"  {k}: {str(v)[:300]}")
    p("\n== risk_json =="); p(" ", risk)
    p("\n== health_json =="); p(" ", json.dumps(health, ensure_ascii=False)[:600])
OUT.append("\n== mm_lane_status.json (运行时自报 limits/params) ==")
st=json.loads(pathlib.Path("logs/mm_lane_status.json").read_text(encoding="utf-8"))
for k in ("ts","ok","ticks","fills","flattens","equity","fill_notional","quoted_decisions","fills_per_hour","avg_width_bp","avg_sigma","avg_sigma_all","avg_base_bp"):
    OUT.append(f"  {k} = {st.get(k)}")
OUT.append("  limits:")
for k,v in sorted((st.get("limits") or {}).items()): OUT.append(f"      {k:<34}= {v}")
pathlib.Path("logs/_tmp_timeline/params.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
