# -*- coding: utf-8 -*-
"""轮116 收尾：运行中的后端 API 读到的车道配额（证明改的是**活进程**读的那份）。"""
import io
import json
import urllib.request

OUT = io.open("reports/_verify116_live.txt", "w", encoding="utf-8")


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


w("== /api/full-auto/tier-status/fa_7e12e7a1b6 ==")
try:
    d = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8000/api/full-auto/tier-status/fa_7e12e7a1b6", timeout=25).read().decode())
    w("   tier_budget_allocation =", d.get("tier_budget_allocation"))
    for t, v in (d.get("tiers") or {}).items():
        w(f"   {t:6s} label={v.get('label')} budget={v.get('budget')} "
          f"max_margin={v.get('max_margin')} margin_used={v.get('margin_used')} "
          f"positions={v.get('position_count')}")
except Exception as e:
    w("   (失败)", type(e).__name__, str(e)[:200])

w("\n== /api/health boot ==")
try:
    b = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=20)
                   .read().decode())["boot_fingerprint"]
    w("   ", b["boot_git_hash"], b["code_git_head"], "matches=", b["matches_disk"])
except Exception as e:
    w("   (失败)", e)

OUT.close()
print("written reports/_verify116_live.txt")
