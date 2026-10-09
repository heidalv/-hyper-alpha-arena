# -*- coding: utf-8 -*-
"""[h760] 选币池逐级漏斗诊断:候选→可行→打分→合格,看池子在哪一级被卡死。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
spec = importlib.util.spec_from_file_location(
    "v5", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h329_selector_v5.py")
v5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v5)
p = v5.evaluate("mm_asterdex", slots=8, rev_slots=3, hours=v5.ROLL_HOURS)
print("candidates_n =", p.get("candidates_n"))
print("feasible_n   =", p.get("feasible_n"))
print("scored n     =", len(p.get("scoreboard") or []))
print("proposed     =", p.get("proposed"))
print("dc_snapshot_stale =", p.get("dc_snapshot_stale"))
print("osi_excluded =", p.get("osi_excluded"))
print("decayed_out  =", p.get("decayed_out"))
print("q_decayed    =", p.get("q_decayed"), "| exposure_decayed =", p.get("exposure_decayed"))
print("候选池(前 20):", [x.get("symbol") for x in (p.get("scoreboard") or [])][:20])
