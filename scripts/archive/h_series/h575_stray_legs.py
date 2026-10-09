"""h575 — 查"非现役币种"的零星腿是什么（只读，R77）。

背景：宇宙 09-28 13:00 收窄到 5 币（BNB/NEAR/ARB/XRP/ENA），但 `h524` 的钱图里出现了
**ETH 2 腿 / BTC 1 腿 / DOGE 1 腿**（共 −0.09$）。两种解释的处置完全不同：
  · **收窄时遗留持仓的平仓腿**（exit）⇒ 无害、一次性，说明收窄执行干净 ✓
  · **仍在报价/开仓**（entry、且时间点散布在收窄之后很久）⇒ 宇宙没收干净 ✗（真问题）
本脚本把这几条腿逐条打出来判定。

用法：python scripts/h575_stray_legs.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
import json
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

ERA_SINCE = "2026-09-28T05:00:00+00:00"     # 本地 09-28 13:00（收窄时刻）
LIVE = ("BNB", "NEAR", "ARB", "XRP", "ENA")


def main() -> int:
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "SELECT ts, symbol, COALESCE(meta_json->>'side',''),"
            " COALESCE(meta_json->>'exit_path',''),"
            " COALESCE(meta_json->>'source',''),"
            " COALESCE(meta_json->>'exit_action',''),"
            " round((net_bp*notional/1e4)::numeric, 3),"
            " round(notional::numeric, 1)"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND NOT (symbol = ANY(%s)) ORDER BY ts",
            (h.LANE, ERA_SINCE, list(LIVE)))
        rows = cur.fetchall()
        print("=" * 96)
        print(f"非现役币种的腿（纪元内，共 {len(rows)} 条）")
        print("=" * 96)
        print(f"{'本地时间':<20}{'币':<6}{'side':<6}{'exit_path':<18}"
              f"{'source':<10}{'net$':>8}{'名义$':>9}")
        exits = entries = 0
        for ts, sym, side, ep, src, ea, net_usd, notional in rows:
            loc = ts.astimezone().strftime("%m-%d %H:%M:%S")
            is_exit = bool(ep or ea)
            exits += 1 if is_exit else 0
            entries += 0 if is_exit else 1
            print(f"{loc:<20}{sym:<6}{side:<6}{(ep or '-'):<18}"
                  f"{(src or '-'):<10}{net_usd if net_usd is not None else 0:>8}"
                  f"{notional if notional is not None else 0:>9}")
        print("-" * 96)
        print(f"出场腿={exits}  入场腿={entries}")
        if entries == 0:
            print("⇒ ✓ 全是**遗留持仓的平仓腿** ⇒ 收窄执行干净、无残留报价")
        else:
            print("⇒ ⚠️ 存在入场腿 ⇒ 宇宙可能没收干净，需查采集器/引擎的币种清单")
    p = ROOT / "research_l1" / "out" / "h575_stray_legs.json"
    p.write_text(json.dumps(
        [{"ts": str(r[0]), "symbol": r[1], "side": r[2], "exit_path": r[3],
          "source": r[4], "exit_action": r[5], "net_usd": float(r[6] or 0),
          "notional": float(r[7] or 0)} for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
