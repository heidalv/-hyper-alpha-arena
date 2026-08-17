import os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

t0 = time.time()
try:
    from backend.services.scalp.scalp_daily_health import run_scalp_daily_health
    rep = run_scalp_daily_health(days=30)
    print("① scalp_daily_health:", {k: rep.get(k) for k in ("status", "summary", "total_checks") if k in rep} or "done",
          f"({time.time()-t0:.0f}s)")
except Exception as e:
    print("① scalp_daily_health 失败:", e)

t1 = time.time()
try:
    from backend.services.scalp.scalp_symbol_profile import build_symbol_scalp_profile
    rep2 = build_symbol_scalp_profile()
    print("② scalp_symbol_profile: done", f"({time.time()-t1:.0f}s)")
except Exception as e:
    print("② scalp_symbol_profile 失败:", e)

from backend.services.scalp.scalp_heartbeat import get_heartbeats
hbs = get_heartbeats() or {}
for tid in ("scalp_daily_health", "scalp_symbol_profile", "pair_selector_watcher"):
    print(tid, "→", hbs.get(tid, {}).get("last_ok_at"))
