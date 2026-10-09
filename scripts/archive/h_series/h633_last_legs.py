"""h633 — 腿量"停摆"证据：最后一腿在什么时候 + 近几小时分段腿数（只读）。

为什么要：用户 23:05 报"没有交易了"。心跳只能给"累计 fills"这类**没有时间轴**的
数（`fills=75` 卡住 ✗），无法回答"从几点开始停的、停之前速度是多少"。
本脚本按 15 分钟分段读 `lane_ledger`，把停摆时刻和停摆前速度一起摆出来。

只读；不写任何表。列名先探测（h190 的教训：硬编码列名会查出"不可能的数" ✗）。

[R235 2026-09-29 · 本脚本初版的错，留在这里当反面教材]
初版把时长写成 `interval '%s hours'`（占位符落在**字符串字面量**里）⇒ psycopg
不做替换、也不报错，查询**静默返回 0 行** ✗✗，于是打印出"近 6 小时无腿"——
和 `max(ts)` 的 19:02 自相矛盾。改用 `make_interval(secs => %s)` ✓。
教训：**任何"看起来不可能"的数都要先怀疑口径，再怀疑世界**（h190 同型）。
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 连接与车道名一律借 h425 的口径（R94：不要自己拼 DSN/车道名）。
# 教训：h633 初版用 os.getenv('DATABASE_URL') 直连，结果连到了 alpha_market，
# `lane_ledger` 在那里根本不存在 ⇒ 打印"表不存在"，看着像"没有腿"✗。
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]


def _conn():
    import psycopg
    return psycopg.connect(h.read_env_dsn(), autocommit=True)


def main() -> int:
    hours = 6.0
    for i, a in enumerate(sys.argv):
        if a == "--hours" and i + 1 < len(sys.argv):
            hours = float(sys.argv[i + 1])

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'lane_ledger' ORDER BY ordinal_position")
        cols = [r[0] for r in cur.fetchall()]
        print("=" * 88)
        print("h633 — 腿量停摆证据（lane_ledger）")
        print("=" * 88)
        print(f"  lane = {h.LANE}")
        print(f"  lane_ledger 列 = {cols}")
        if not cols:
            print("✗ 表不存在或没有列")
            return 1

        ts_col = next((x for x in ("ts", "created_at", "event_ts", "opened_ts",
                                   "open_ts", "fill_ts") if x in cols), None)
        sym_col = next((x for x in ("symbol", "sym") if x in cols), None)
        notional_col = next((x for x in ("notional", "fill_notional", "notional_usd")
                             if x in cols), None)
        if not ts_col:
            print("✗ 找不到时间列 ⇒ 拒绝猜（h190）")
            return 1
        print(f"  用列：ts={ts_col} symbol={sym_col} notional={notional_col}")

        cur.execute(f"SELECT max({ts_col}) FROM lane_ledger WHERE lane_id=%s",
                    (h.LANE,))
        last = cur.fetchone()[0]
        print(f"\n  最后一腿时间 = {last}")
        cur.execute(
            f"SELECT now() - max({ts_col}) FROM lane_ledger WHERE lane_id=%s",
            (h.LANE,))
        gap = cur.fetchone()[0]
        print(f"  距今停滞     = {gap}  {'✗ 停摆' if gap and gap.total_seconds() > 600 else '✓'}")

        print(f"\n  近 {hours:g} 小时按 15 分钟分段：")
        sel_sym = f", {sym_col}" if sym_col else ""
        cur.execute(
            f"SELECT to_timestamp(floor(extract(epoch from {ts_col})/900)*900) AS b, "
            f"count(*){sel_sym} "
            f"FROM lane_ledger "
            f"WHERE lane_id=%s AND {ts_col} > now() - make_interval(secs => %s) "
            f"GROUP BY b{', ' + sym_col if sym_col else ''} "
            f"ORDER BY b DESC LIMIT 60", (h.LANE, hours * 3600.0))
        rows = cur.fetchall()
        if not rows:
            print(f"    （近 {hours:g} 小时无腿 ✗✗）")
        for r in rows:
            b = r[0]
            n = r[1]
            sym = r[2] if len(r) > 2 else ""
            rate = n * 4.0
            flag = "✓" if rate >= 60 else "✗"
            print(f"    {b:%Y-%m-%d %H:%M}  {n:>4} 腿  ≈{rate:>6.1f}/h {flag}  {sym}")

        cur.execute(
            f"SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
            f"AND {ts_col} > now() - interval '1 hour'", (h.LANE,))
        n1 = cur.fetchone()[0]
        cur.execute(
            f"SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
            f"AND {ts_col} > now() - interval '24 hours'", (h.LANE,))
        n24 = cur.fetchone()[0]
        print(f"\n  近 1 小时 = {n1} 腿（{n1}/h，硬约束 ≥60/h {'✓' if n1 >= 60 else '✗✗'}）")
        print(f"  近 24 小时 = {n24} 腿（{n24/24:.1f}/h）")

    print("\n" + "=" * 88)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
