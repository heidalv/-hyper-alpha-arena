"""部署审计 ②（只读）：用 meta 里各 `*_trial` / `*_rollback` 子字典自带的时间戳
重建**超过 ops(20 条)上限**的历史时间线。

动机：h465 发现 **09-28 12:00→13:00 UTC（本地 20:00→21:00）** 腿速 225/h→65/h、
每腿名义 232$→106$ 的断崖，但 `ops_changes` 只留最近 20 条（最早 23:46L）⇒ 看不到那一次。

用法：python scripts/h467_meta_timeline.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h467_meta_timeline.json"
TZ = dt.timezone(dt.timedelta(hours=8))


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def parse_ts(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json, updated_at FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            row = cur.fetchone()
    m = row[0] or {}
    print("registry updated_ts:", row[1])
    for k in ("stats_since", "account_reset_at", "universe", "symbols", "shadow_equity",
              "edge_source", "selection_note", "paper_account_id"):
        print(f"  {k} = {json.dumps(m.get(k), ensure_ascii=False)[:160]}")
    print("=" * 104)
    events = []
    ts_keys = ("ts", "started_at", "deployed_at", "rolled_back_at", "judged_at",
               "at", "when", "created_at")
    for k, v in m.items():
        if not isinstance(v, dict):
            continue
        t = None
        for tk in ts_keys:
            t = parse_ts(v.get(tk))
            if t:
                break
        if t is None:
            # 有些子字典把时间写在别的字段名里（如 judge_at / rollback_at）
            for tk, tv in v.items():
                if isinstance(tv, str) and ("_at" in tk or tk == "ts"):
                    t = parse_ts(tv)
                    if t:
                        break
        events.append({"key": k, "ts": t, "data": v})
    events.sort(key=lambda e: (e["ts"] is None, e["ts"] or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc)))
    for e in events:
        if e["ts"] is None:
            print(f"{'—':26s} {e['key']:34s} (无时间戳)")
            continue
        local = e["ts"].astimezone(TZ)
        print(f"{local:%m-%d %H:%M:%S}L/{e['ts']:%H:%M}Z  {e['key']:34s} "
              f"{json.dumps({kk: vv for kk, vv in e['data'].items() if kk not in ts_keys}, ensure_ascii=False)[:150]}")
    print("=" * 104)
    print("按时间排序的**关键参数变更点**（只看 20:00L 前后 ±3h）：")
    for e in events:
        if e["ts"] is None:
            continue
        local = e["ts"].astimezone(TZ)
        if dt.datetime(2026, 9, 28, 17, 0, tzinfo=TZ) <= local <= \
                dt.datetime(2026, 9, 29, 0, 0, tzinfo=TZ):
            print(f"  {local:%m-%d %H:%M:%S}L  {e['key']}  "
                  f"{json.dumps(e['data'], ensure_ascii=False)[:300]}")
    OUT.write_text(json.dumps(
        [{"key": e["key"], "ts": e["ts"].isoformat() if e["ts"] else None,
          "data": e["data"]} for e in events], ensure_ascii=False, indent=2,
        default=str), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
