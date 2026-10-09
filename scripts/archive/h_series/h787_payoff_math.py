# -*- coding: utf-8 -*-
"""[h787] 盈亏比算术:TP 30 vs 止损 150 的盈亏比 = 1:5,需要 83% 胜率才打平。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT count(*), AVG(net_bp),"
        " percentile_disc(0.5) WITHIN GROUP (ORDER BY net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '3 hours' AND COALESCE(meta_json->>'exit_path','') = ''")
    n_m, avg_m, med_m = cur.fetchone()
    cur.execute(
        "SELECT count(*), AVG(net_bp),"
        " percentile_disc(0.5) WITHIN GROUP (ORDER BY net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '3 hours' AND net_bp < 0"
        " AND COALESCE(meta_json->>'exit_path','') <> ''")
    n_l, avg_l, med_l = cur.fetchone()
    cur.execute(
        "SELECT count(*), AVG(net_bp),"
        " percentile_disc(0.5) WITHIN GROUP (ORDER BY net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '3 hours' AND net_bp > 0"
        " AND COALESCE(meta_json->>'exit_path','') <> ''")
    n_w, avg_w, med_w = cur.fetchone()

print(f"近 3h 按腿:")
print(f"  自然成交(maker): n={n_m} 均 {float(avg_m or 0):+.1f}bp 中位 {float(med_m or 0):+.1f}bp")
print(f"  出场赢腿:        n={n_w} 均 {float(avg_w or 0):+.1f}bp 中位 {float(med_w or 0):+.1f}bp")
print(f"  出场亏腿:        n={n_l} 均 {float(avg_l or 0):+.1f}bp 中位 {float(med_l or 0):+.1f}bp")
print()
win_all = n_m + n_w
loss_all = n_l
wr = win_all / (win_all + loss_all) * 100 if (win_all + loss_all) else 0
print(f"  总胜率 ≈ {wr:.0f}%({win_all} 赢 vs {loss_all} 亏)")
print(f"  盈亏比结构(TP30:止损150 = 1:5)⇒ 打平需要胜率 150/(150+30) = 83.3%")
print(f"  我们 ≈ {wr:.0f}% ⇒ **结构性略负**(还没算跳空尾巴)")
print(f"  若 TP 提到 60bp(盈亏比 60:150=1:2.5)⇒ 打平只需 71.4%")
print(f"  若 TP 提到 80bp(盈亏比 80:150=1:1.9)⇒ 打平只需 65.2%")
