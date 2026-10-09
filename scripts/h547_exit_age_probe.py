"""h547：账本里**出场腿是否自带持仓年龄**——决定"30–60s 桶"能否一行 SQL 算出来。

背景：② 的 SPEC 判据 C 是「30–60s 桶净额转正」（来自 h461：被动出场在 30–60s 档
净 −0.83bp，更长窗更好）。但 `_sub_stats` 对 h463 返回空 `{}` ⇒ 该判据**没有实现**，
判定时无法逐条对照。

实现它需要"每笔出场时该仓位持有了多久"。两条路：
  (A) 账本已记年龄（`meta_json` 里某个键）⇒ 一行 SQL；
  (B) 没记 ⇒ 必须用 h494 的"带符号数量累计归零"重建往返，再算年龄（成本高）。
本脚本先查 (A)：列出出场腿 `meta_json` 的全部键，并看有没有 age/hold 类字段。

用法：python scripts/h547_exit_age_probe.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h425_repair_trial import LANE, read_env_dsn  # noqa: E402


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT k, count(*) FROM (
                  SELECT jsonb_object_keys(meta_json) AS k
                  FROM lane_ledger
                  WHERE lane_id=%s AND ts > now() - interval '6 hours'
                    AND COALESCE(meta_json->>'exit_path','') <> ''
                ) t GROUP BY k ORDER BY count(*) DESC""", (LANE,))
            keys = cur.fetchall()
            print("出场腿 meta_json 的键（近 6h）")
            for k, n in keys:
                flag = ""
                if any(x in k.lower() for x in ("age", "hold", "sec", "opened", "dur")):
                    flag = "   ← 可能含持仓时长"
                print(f"  {k:28s} {n:6d}{flag}")
            cur.execute("""
                SELECT meta_json FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - interval '6 hours'
                  AND COALESCE(meta_json->>'exit_path','') <> ''
                ORDER BY ts DESC LIMIT 2""", (LANE,))
            print("\n样本（最近 2 条出场腿）：")
            for (mj,) in cur.fetchall():
                print("  " + json.dumps(mj, ensure_ascii=False)[:300])
            # 顺带看 exit_path 构成，供选择机制代理口径
            cur.execute("""
                SELECT COALESCE(meta_json->>'exit_path','') AS p, count(*),
                       COALESCE(avg(net_bp),0)::float8
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - interval '6 hours'
                GROUP BY 1 ORDER BY 2 DESC""", (LANE,))
            print("\n近 6h 出场构成（供选择机制代理）")
            for p, n, nb in cur.fetchall():
                print(f"  {(p or '(入场腿)'):26s} {n:5d}  净均 {nb:+7.2f}bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
