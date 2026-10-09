"""h620 — ⑤（`max_quote_age_sec` 90→45）的**可达性检查**：挂单年龄分布（只读；R213）。

背景：R212 的旋钮审计发现 `stale_quote_cleared` 计数器为 **0** ⇒ ⑤ 可能又是一次"空转" ✗
（R201 的教训：先证明"这根旋钮有机会生效"，再花 12h 窗口）。
⑤ 的机制是"**丢弃年龄 > 45s 的挂单**" ⇒ 它能不能生效，直接取决于一个问题：
**成交时的挂单年龄到底有没有超过 45s？超过多少？那些腿是不是更亏？**

口径（账本自带，无需行情重建 ✓）：
  入场腿（`exit_path` 为空）的 `meta_json.quote_ts`（挂单时刻，epoch 秒）
  ⇒ `age = ts − to_timestamp(quote_ts)`。
  ⚠️ 覆盖率不是 100%（历史读数约 52%）⇒ 本脚本**先报覆盖率**，只在覆盖到的腿上作结论 ✓。

用法：python scripts/h620_quote_age_distribution.py [--hours 12]
"""
from __future__ import annotations

import argparse
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

AGE = "EXTRACT(EPOCH FROM (ts - to_timestamp((meta_json->>'quote_ts')::float8)))"
WIN = "ts > now() - make_interval(hours => %s::int)"
# [R213b] **量级自检**（§7 第 3 条）：首版直接把 `quote_ts` 当 epoch 秒算 ⇒ 得到
# p90≈1.79e9 秒这种不可能的"年龄" ✗ —— 根因是**部分腿的 `quote_ts` 是 0/哨兵值**
# （`to_timestamp(0)` ⇒ 年龄≈当前 epoch 秒），它们还正好主宰了 ">45s" 桶 ⇒ 结论全错 ✗。
# ⇒ 只保留**可信区间**：`quote_ts ≥ 1e9`（≈2001 年之后）且 `0 ≤ age ≤ 3600`。
VALID = ("(meta_json->>'quote_ts')::float8 >= 1.0e9"
         " AND " + AGE + " BETWEEN 0 AND 3600")
QTS = "meta_json->>'quote_ts' IS NOT NULL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=12,
                    help="回看小时数（**整数**：make_interval 的 hours 只接受 int ✗）")
    a = ap.parse_args()
    print("=" * 96)
    print(f"h620 — 挂单年龄分布（入场腿，近 {a.hours}h）—— ⑤ 能不能生效？")
    print("=" * 96)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        cur.execute(f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE meta_json->>'quote_ts' IS NOT NULL) AS with_q
            FROM lane_ledger
            WHERE lane_id=%s AND {WIN}
              AND symbol = ANY(%s) AND COALESCE(meta_json->>'exit_path','') = ''""",
            (h.LANE, a.hours, syms))
        total, with_q = (int(x or 0) for x in cur.fetchone())
        cov = (with_q / total) if total else 0.0
        print(f"  入场腿 {total} 条，其中带 `quote_ts` {with_q} 条 ⇒ **覆盖率 {cov:.0%}**"
              f"{'（偏低 ⇒ 结论只对覆盖到的腿成立）' if cov < 0.9 else ' ✓'}")
        # 哨兵值剔除（量级自检）：告诉读者有多少条被排除，避免"悄悄改了样本"✗
        cur.execute(f"""
            SELECT count(*) FROM lane_ledger
            WHERE lane_id=%s AND {WIN} AND symbol = ANY(%s)
              AND COALESCE(meta_json->>'exit_path','') = '' AND {QTS}
              AND NOT ({VALID})""", (h.LANE, a.hours, syms))
        bad = int(cur.fetchone()[0] or 0)
        print(f"  ⚠️ 剔除**哨兵/越界** `quote_ts` {bad} 条"
              f"（`quote_ts` < 1e9 或年龄 > 3600s ⇒ 那不是真实挂单时刻 ✗；"
              f"首版没剔 ⇒ p90 出现 1.79e9 秒的假年龄 ✗）")
        if not with_q:
            print("  ✗ 没有可算的腿 ⇒ 无法判定 ⑤ 的可达性")
            return 1
        cur.execute(f"""
            SELECT
              percentile_cont(0.5) WITHIN GROUP (ORDER BY {AGE})::float8,
              percentile_cont(0.9) WITHIN GROUP (ORDER BY {AGE})::float8,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY {AGE})::float8,
              percentile_cont(0.99) WITHIN GROUP (ORDER BY {AGE})::float8,
              max({AGE})::float8
            FROM lane_ledger
            WHERE lane_id=%s AND {WIN}
              AND symbol = ANY(%s) AND COALESCE(meta_json->>'exit_path','') = ''
              AND {QTS} AND {VALID}""", (h.LANE, a.hours, syms))
        p50, p90, p95, p99, mx = cur.fetchone()
        print(f"\n  挂单年龄（秒，**已剔哨兵**）：p50={p50:.1f}  p90={p90:.1f}  p95={p95:.1f}  "
              f"p99={p99:.1f}  max={mx:.1f}")
        cur.execute(f"""
            SELECT count(*) FILTER (WHERE {AGE} > 45) AS over45,
                   count(*) AS n,
                   COALESCE(avg(net_bp) FILTER (WHERE {AGE} > 45),0)::float8 AS bp_over,
                   COALESCE(avg(net_bp) FILTER (WHERE {AGE} <= 45),0)::float8 AS bp_under,
                   count(*) FILTER (WHERE {AGE} > 90) AS over90
            FROM lane_ledger
            WHERE lane_id=%s AND {WIN}
              AND symbol = ANY(%s) AND COALESCE(meta_json->>'exit_path','') = ''
              AND {QTS} AND {VALID}""", (h.LANE, a.hours, syms))
        over45, n, bp_over, bp_under, over90 = cur.fetchone()
        over45, n, over90 = int(over45 or 0), int(n or 0), int(over90 or 0)
        share = (over45 / n) if n else 0.0
        print(f"\n  **年龄 > 45s 的入场腿：{over45} / {n} = {share:.1%}**"
              f"（>90s 的 = {over90}）")
        print(f"    净/腿：>45s 桶 {float(bp_over):+.3f}bp  vs  ≤45s 桶 {float(bp_under):+.3f}bp"
              f"  ⇒ 差 **{float(bp_over) - float(bp_under):+.3f}bp**")
    print("\n" + "-" * 96)
    if share <= 0.005:
        print(f"⇒ ✗ **⑤ 很可能是空转**：只有 {share:.1%} 的入场腿来自 >45s 的挂单 ⇒"
              " 把上限 90 降到 45 几乎不改变任何成交 ✗（与 R212 计数器=0 一致）")
        print("   建议：**先不要给 ⑤ 立项**；若仍想做，先查「为什么挂单不会变老」"
              "（tick 是否每次重挂？）——那更可能是**报价刷新机制**的问题，而不是这个上限 ✓")
    elif share <= 0.03:
        print(f"⇒ ⚠️ ⑤ 属**边缘可达**：>45s 占 {share:.1%} ⇒ 预期影响很小（会落在噪声里）✗")
        print("   建议：优先做 ⑥（trend_only_q，活闸）✓；⑤ 若要立项，判据应以「踢掉最毒的"
              "那一小撮」为目标，而不是看总体频率 ✓")
    else:
        print(f"⇒ ✓ ⑤ **可达**：>45s 占 {share:.1%} ⇒ 改动会真实影响成交批次 ✓")
        print(f"   且 >45s 桶比 ≤45s 桶 {'更亏' if bp_over < bp_under else '更好'}"
              f"（{float(bp_over):+.3f} vs {float(bp_under):+.3f}bp）"
              f"⇒ {'机制前提成立 ✓' if bp_over < bp_under else '机制前提**不成立** ✗（丢弃它们不会改善净额）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
