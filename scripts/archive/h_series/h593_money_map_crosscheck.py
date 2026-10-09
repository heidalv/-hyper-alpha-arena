"""h593 — 钱图数字的**独立复算**（只读，R126）。

动机：总账 §1.1 的表格（纪元 21.82h 快照）是用户做决策的依据，但它是"用 `h524` 算出来的"，
**从未被独立复算过** ✗ —— 转录/口径错误最隐蔽 ✗。本脚本用**另一条路径**（直接 SQL +
显式口径）重算同样几个数，并与文档值并列打印，让差异一眼可见 ✓。

用法：python scripts/h593_money_map_crosscheck.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

ERA_SINCE = "2026-09-28T05:00:00+00:00"   # 本地 09-28 13:00
# 文档 §1.1 里写的值（快照：纪元 21.82h）
DOC = {"legs": 1128, "rate_wall": 51.7, "net_usd": -19.57, "net_per_h": -0.897,
       "stops_usd": -20.958, "stop_share": 1.07,
       "per_coin_usd_per_h": {"BNB": -0.405, "NEAR": -0.244, "ARB": -0.150,
                              "ENA": -0.122, "XRP": 0.028}}


def main() -> int:
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE 'stop_loss%%'),0)::float8"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz",
            (h.LANE, ERA_SINCE))
        n, net, stops = cur.fetchone()
        # 真实覆盖率（可交易小时）用于把"速率"换算成可交易口径
        import datetime as dt
        cov, ratio = h._covered_hours(ERA_SINCE, dt.datetime.now(dt.timezone.utc).isoformat(),
                                      ["BNB", "NEAR", "ARB", "XRP", "ENA"])
        wall_h = (dt.datetime.now(dt.timezone.utc)
                  - dt.datetime.fromisoformat(ERA_SINCE)).total_seconds() / 3600.0
        print("=" * 96)
        print("钱图独立复算（现在的账本 vs 文档 §1.1 的快照值）")
        print("=" * 96)
        print(f"  {'项':<22}{'现算':>16}{'文档值':>16}")
        print(f"  {'腿数':<22}{n:>16}{DOC['legs']:>16}")
        print(f"  {'净额 $':<22}{net:>16.2f}{DOC['net_usd']:>16.2f}")
        print(f"  {'止损 $':<22}{stops:>16.2f}{DOC['stops_usd']:>16.2f}")
        print(f"  {'止损占净额':<22}{(abs(stops/net) if net else 0):>16.2%}"
              f"{DOC['stop_share']:>16.2%}")
        print(f"  {'墙钟腿速':<22}{n/wall_h:>16.1f}{DOC['rate_wall']:>16.1f}")
        print(f"  {'可交易腿速':<22}{n/cov:>16.1f}{'（未写）':>16}")
        print(f"  窗口：{wall_h:.2f}h 墙钟 / {cov:.2f}h 可交易（覆盖 {ratio:.0%}）")
        print("\n  逐币（净 $/h，可交易口径）：")
        cur.execute(
            "SELECT symbol, count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
            (h.LANE, ERA_SINCE, ["BNB", "NEAR", "ARB", "XRP", "ENA"]))
        for sym, k, s in cur.fetchall():
            docv = DOC["per_coin_usd_per_h"].get(sym)
            print(f"    {sym:<6}{k:>6} 腿  {s/cov:>+8.3f}$/h   （文档 {docv:+.3f}）")
        print("=" * 96)
        print("说明：现算值会随纪元增长而变化（文档是 21.82h 的快照）⇒ "
              "看**数量级与符号**是否一致，而不是逐位相等 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
