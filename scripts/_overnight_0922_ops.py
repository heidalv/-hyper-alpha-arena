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
    cur.execute("select meta_json from lane_registry where lane_id='mm_asterdex'")
    meta=cur.fetchone()[0]
    p("== ops_changes 全量（按时间） ==")
    for e in meta.get("ops_changes") or []:
        p(f"\n  [{e.get('ts') or e.get('applied_at')}] by={e.get('by')}")
        p(f"     new={e.get('new') or e.get('params')}")
        p(f"     old={e.get('old')}")
        r=str(e.get('reason') or '')
        p(f"     reason={r[:500]}")
    p("\n== 其它可能含改动的键 ==")
    for k in sorted(meta):
        v=meta[k]
        s=json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v,str) else v
        if any(t in s for t in ("rollback","apply","applied_at","2026-09-21T1","2026-09-21T2","2026-09-22")):
            p(f"  {k}: {s[:400]}")
pathlib.Path("logs/_tmp_timeline/ops.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved", len(OUT))
