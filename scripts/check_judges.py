# -*- coding: utf-8 -*-
"""判决链核对：h411(07:27)/h428(07:45)/h356 延长/各判定任务状态。只读。"""
import sys
import json
import pathlib
import subprocess
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]

print("== 判决链状态 ==")
for k in ("h411_trial", "h356_trial", "h392_trial", "h429_trial", "h425_trial",
          "h426_trial", "h427_trial", "h432_trial"):
    t = m.get(k) or {}
    if not t:
        print(f"  {k}: 未部署")
        continue
    print(f"  {k}: verdict={t.get('verdict','(进行中)')} judge={t.get('judge_at','')[:16]}"
          f" {('why='+str(t.get('why'))[:60]) if t.get('why') else ''}")

print("\n== 判定任务排程 ==")
for tn in ("DSH_HFT_H392_JUDGE", "DSH_HFT_H429_JUDGE", "DSH_HFT_H425_JUDGE",
           "DSH_HFT_H426_JUDGE", "DSH_HFT_H427_JUDGE", "DSH_HFT_H432_JUDGE",
           "DSH_HFT_H428_QUEUE"):
    r = subprocess.run(["schtasks", "/query", "/tn", tn, "/fo", "list"],
                       capture_output=True, text=True, timeout=30)
    nxt = [x.strip() for x in r.stdout.splitlines() if "Next Run" in x]
    last = [x.strip() for x in r.stdout.splitlines() if "Last Result" in x]
    print(f"  {tn:<26} {nxt[0] if nxt else 'N/A'} | {last[0] if last else ''}")

print("\n== ops_changes 末 4 条 ==")
for e in (m.get("ops_changes") or [])[-4:]:
    print("  ", json.dumps(e, ensure_ascii=False)[:150])
