"""h611 — 探明 `lane_ledger` 的**入场腿**里到底记了哪些字段（R201）。

目的：③ 的判据需要"**饱和 confirm 态占比**"这类**不依赖旧基线**的机制指标（因为 R198/R198b
已证明主判据 Δ 会被"缝隙变更"污染）。问题：账本里有没有逐腿的 `fo = OFI×d` / `ofi` 记录？
  · 有 ⇒ 该指标可以**直接算**（且天然免疫缝隙问题 ✓）；
  · 没有 ⇒ 需要从行情库重建 OFI（成本高、且重建错了会造假结论 ✗）⇒ 改用别的口径。

只读。打印：入场腿/出场腿各取几条的 `meta_json` 键名与若干关键值，以及各键的出现频次。
"""
from __future__ import annotations

import collections
import importlib.util
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

SINCE = "2026-09-29T01:48:11+00:00"      # ② 试跑起点
INTERESTING = ("fo", "ofi", "confirm", "state", "trend", "gate", "skip", "reason",
               "w_bid", "w_ask", "spread", "quote_ts", "age")


def main() -> int:
    print("=" * 88)
    print("h611 — lane_ledger 入场腿的字段探查（② 窗口内）")
    print("=" * 88)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz",
            (h.LANE, SINCE))
        print(f"  ② 窗口内腿数：{cur.fetchone()[0]}")
        cur.execute("""
            SELECT COALESCE(meta_json->>'exit_path','') AS path, count(*)
            FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz
            GROUP BY 1 ORDER BY 2 DESC""", (h.LANE, SINCE))
        print("  按 exit_path 分组：")
        for p, n in cur.fetchall():
            print(f"    {str(p or '(空)'):<28} {n}")
        cur.execute("""
            SELECT meta_json FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz
            AND COALESCE(meta_json->>'exit_path','') = '' LIMIT 400""", (h.LANE, SINCE))
        keys = collections.Counter()
        sample = None
        for (mj,) in cur.fetchall():
            if not isinstance(mj, dict):
                continue
            if sample is None:
                sample = mj
            for k in mj:
                keys[k] += 1
        print(f"\n  入场腿（exit_path 为空）样本 meta_json 键频次（最多 400 条）：")
        for k, n in keys.most_common(40):
            print(f"    {k:<28} {n}")
        hit = [k for k in keys if any(s in k.lower() for s in INTERESTING)]
        print(f"\n  ⇒ 与 confirm/ofi/fo/gate 相关的键：{hit or '（无）✗'}")
        if sample:
            print(f"\n  一条入场腿的完整 meta_json（截断 600 字符）：")
            print(f"    {str(sample)[:600]}")
    print("\n  判读：若上面「相关键」为空 ⇒ 账本**不记** fo/ofi ⇒ 饱和态占比必须**从行情重建** ✗"
          "（成本高、且重建错了会造假结论）⇒ 应改用账本内已有的口径 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
