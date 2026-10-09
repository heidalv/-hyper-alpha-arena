"""h576 — 止损"大亏腿"的单笔名义是否异常（风控上限体检，只读，R78）。

背景：§5.1 留了一条未完成的子任务——"止损溢出（4.4bp）的天花板 +0.215$/h，
但需先查 4 条 −60bp 以下的腿（其单笔名义是均值 1.8 倍）"。

两种解释处置完全不同：
  · **名义没有被任何上限管住**（某条腿的名义远超同币分布、且超 `limit_notional`/硬顶）
    ⇒ **风控缺陷** ✗（且 ④ 正要动逐币规模 ⇒ 必须先弄清上限是否真的生效）
  · **名义正常、只是价格逆行大** ⇒ 只是行情，不必动风控 ✓

做法：取本纪元 `net_bp` 最差的腿，逐条给出：币、时间、出场路径、名义、该币的
名义分布（P50/P90/P99/max）、以及**是否超过该币 P99**。同时把 `lane_registry.params`
里与名义相关的上限打出来对照。

用法：python scripts/h576_oversized_stop_legs.py [--n 12]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
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

ERA_SINCE = "2026-09-28T05:00:00+00:00"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="列出最差的 N 条腿")
    a = ap.parse_args()
    out: dict = {}
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        params = dict(meta.get("params") or {})
        out["params_notional_related"] = {
            k: v for k, v in params.items()
            if any(t in k.lower() for t in ("notional", "size", "cap", "limit", "max"))}
        print("=" * 100)
        print("与名义/上限相关的注册表参数：")
        for k, v in out["params_notional_related"].items():
            print(f"  {k} = {v}")
        # 逐币名义分布
        cur.execute(
            "SELECT symbol, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY notional),"
            " percentile_cont(0.9) WITHIN GROUP (ORDER BY notional),"
            " percentile_cont(0.99) WITHIN GROUP (ORDER BY notional), max(notional)"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
            (h.LANE, ERA_SINCE, syms))
        dist = {r[0]: {"n": int(r[1]), "p50": float(r[2]), "p90": float(r[3]),
                       "p99": float(r[4]), "max": float(r[5])} for r in cur.fetchall()}
        out["per_coin_notional"] = dist
        print("\n逐币单笔名义分布（$）：")
        print(f"  {'币':<6}{'腿':>6}{'P50':>9}{'P90':>9}{'P99':>9}{'max':>9}")
        for s, d in dist.items():
            print(f"  {s:<6}{d['n']:>6}{d['p50']:>9.1f}{d['p90']:>9.1f}"
                  f"{d['p99']:>9.1f}{d['max']:>9.1f}")
        # 最差的腿
        cur.execute(
            "SELECT ts, symbol, net_bp, notional,"
            " COALESCE(meta_json->>'exit_path','-'),"
            " COALESCE(meta_json->>'exit_reason','-')"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND symbol = ANY(%s) ORDER BY net_bp ASC LIMIT %s",
            (h.LANE, ERA_SINCE, syms, a.n))
        rows = cur.fetchall()
        print(f"\n最差的 {a.n} 条腿：")
        print(f"  {'本地时间':<18}{'币':<6}{'净bp':>9}{'名义$':>9}"
              f"{'该币名义分位':>14}  {'出场路径'}")
        worst = []
        for ts, sym, nb, notional, ep, er in rows:
            d = dist.get(sym) or {}
            n = float(notional or 0)
            # 粗分位：只与 P50/P90/P99 比
            if d:
                if n > d["p99"]:
                    q = ">P99 ⚠️"
                elif n > d["p90"]:
                    q = "P90–P99"
                elif n > d["p50"]:
                    q = "P50–P90"
                else:
                    q = "≤P50"
            else:
                q = "-"
            ratio = (n / d["p50"]) if d.get("p50") else None
            worst.append({"ts": str(ts), "symbol": sym, "net_bp": float(nb),
                          "notional": n, "q": q, "ratio_to_p50": ratio,
                          "exit_path": ep, "exit_reason": er})
            print(f"  {ts.astimezone():%m-%d %H:%M:%S}  {sym:<6}{float(nb):>9.1f}{n:>9.1f}"
                  f"{q:>14}  {ep}")
        out["worst_legs"] = worst
        big = [w for w in worst if w["q"].startswith(">P99")]
        print("-" * 100)
        print(f"其中名义 >该币 P99 的：{len(big)}/{len(worst)} 条")
        if not big:
            print("⇒ ✓ 最差腿的名义都在该币 P99 以内（多为 P50–P90）"
                  "⇒ 亏损来自**价格逆行**，不是名义失控 ⇒ 不必动风控上限")
        else:
            print("⇒ ⚠️ 有腿的名义超出该币 P99 ⇒ 需对照上限参数确认是否被管住")
    p = ROOT / "research_l1" / "out" / "h576_oversized_stop_legs.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
