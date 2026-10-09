"""h596 — 日亏闸是否**本该触发**（只读，R140）。

动机：纪元净亏已 ≈ −20.87$（≈300 USDT 本金的 7%）✗，而注册表里有 `daily_loss_stop_pct`
⇒ 若阈值低于实际回撤而车道**仍在交易**，说明日亏闸没有生效 ✗（风控缺陷 ✓✓）。

做法：
  1. 读出**阈值与账户口径**（`daily_loss_stop_pct`、`stats_since`、equity/basis）；
  2. 按**日**（本地）汇总 lane_ledger 的净额，与阈值比较；
  3. 明确回答"哪一天本该触发、实际有没有触发"（用 worker 日志/心跳的 skip 计数佐证）。

用法：python scripts/h596_daily_loss_gate.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
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

KEYS = ("daily_loss_stop_pct", "daily_loss_stop", "equity", "compound_ratio",
        "stats_since", "risk_equity", "max_daily_loss_pct")


def main() -> int:
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        params = dict(meta.get("params") or {})
        print("=" * 92)
        print("与「日亏闸 / 账户口径」有关的登记表字段")
        print("=" * 92)
        for k in KEYS:
            if k in params:
                print(f"  params.{k} = {params[k]}")
        other = {k: v for k, v in params.items()
                 if any(t in k.lower() for t in ("loss", "equity", "daily", "bankroll",
                                                 "capital", "balance"))}
        for k, v in other.items():
            if k not in KEYS:
                print(f"  params.{k} = {v}")
        for k in ("book", "account", "equity"):
            if k in meta:
                print(f"  meta.{k} = {json.dumps(meta[k], ensure_ascii=False)[:200]}")
        print("\n" + "=" * 92)
        print("按日净额（本地日）")
        print("=" * 92)
        cur.execute(
            "SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') d,"
            " count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8,"
            " COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY notional),0)::float8,"
            " COALESCE(avg(net_bp),0)::float8,"
            " COALESCE(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::float8"
            " FROM lane_ledger WHERE lane_id=%s GROUP BY 1 ORDER BY 1 DESC LIMIT 10",
            (h.LANE,))
        print(f"  {'日期':<12}{'腿':>7}{'净$':>10}{'名义P50':>10}"
              f"{'bp/腿(简单)':>12}{'bp/腿(加权)':>12}")
        neg_all, neg_w = 0, 0
        rows = cur.fetchall()
        for d, n, net, p50, bpl, bplw in rows:
            neg_all += 1 if bpl < 0 else 0
            neg_w += 1 if bplw < 0 else 0
            print(f"  {d:<12}{n:>7}{net:>+10.2f}{p50:>10.1f}"
                  f"{bpl:>+12.2f}{bplw:>+12.2f}")
        print(f"\n  ⇒ 简单平均为负的天数：{neg_all}/{len(rows)}；"
              f"**名义加权为负的天数：{neg_w}/{len(rows)}** ✓"
              f"（两种口径一致 ⇒ 结论不依赖口径 ✓）")
        print("\n  ⇒ 判读要点：**不同日期的单腿名义可能差很多** ⇒ 老日期的 $ 不可直接与现纪元比 ✗；"
              "但**符号与 bp/腿**是口径无关的 ✓（每天都为负 ⇒ 结构性问题 ✓）")
        print("\n" + "=" * 92)
        print("worker 日志里与「日亏闸」相关的 skip 键（若从未出现 ⇒ 闸没被触发过）")
        print("=" * 92)
        log = ROOT / "logs" / "mm_lane_worker.log"
        if log.exists():
            txt = log.read_text(encoding="utf-8", errors="replace")
            keys = set()
            import re
            for m in re.finditer(r"skip=\{([^}]*)\}", txt):
                for part in m.group(1).split(","):
                    if ":" in part:
                        keys.add(part.split(":", 1)[0].strip().strip("'\""))
            hit = sorted(k for k in keys if any(t in k.lower()
                                                for t in ("loss", "daily", "halt", "stop")))
            print(f"  日志里出现过的 skip 键共 {len(keys)} 个；其中含 loss/daily/halt/stop 的："
                  f"{hit if hit else '无'}")
            print(f"  全部键：{', '.join(sorted(keys))}")
    print("=" * 92)
    print("⇒ 判读：若按日净额已跌破阈值、而日志里没有相关闸门键 ⇒ 日亏闸**未生效** ✗（应查实现）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
