"""h472 采纳验证（只读）：部署时刻之后的出场路径构成 + 心跳里的新 skip 标签。

通过标准（机制层，不依赖 12h 统计）：
  1. 心跳 `skip_counts` 出现 **`ofi_flatten_maker`** ⇒ 新代码分支真的被进入；
  2. 部署后**不再新增** `ofi_flatten_taker` 腿（被动减仓不产生 taker 腿）；
  3. 出场腿的每腿手续费 `fee_bp` 绝对值下降（taker 占比下降）。

用法：python scripts/h473_verify_h472.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
LOG = ROOT / "logs" / "mm_lane_worker.log"
OUT = ROOT / "research_l1" / "out" / "h473_verify_h472.json"


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
            tr = dict(m.get("h472_trial") or {})
            started = tr.get("started_at")
            print("h472_trial:", json.dumps(
                {k: tr.get(k) for k in ("started_at", "judge_at", "to", "from")},
                ensure_ascii=False))
            p = dict(m.get("params") or {})
            print("registry ofi_flatten_maker_only =", p.get("ofi_flatten_maker_only"),
                  "| ofi_flatten_threshold =", p.get("ofi_flatten_threshold"))
            if not started:
                print("✗ 未找到 started_at")
                return 1
            cur.execute(
                "SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(入场腿)'), count(*), "
                "COALESCE(avg(net_bp),0)::float8, COALESCE(avg(fee_bp),0)::float8, "
                "COALESCE(sum(notional),0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz "
                "GROUP BY 1 ORDER BY 2 DESC", (LANE, started))
            rows = cur.fetchall()
    print("=" * 92)
    print("部署后账本（自", started, "）：")
    print(f"{'exit_path':>26s} {'腿数':>6s} {'净bp/腿':>9s} {'费bp/腿':>9s} {'名义$':>10s}")
    tot = sum(int(r[1]) for r in rows)
    for pth, n, net, fee, noti in rows:
        print(f"{pth:>26s} {int(n):6d} {float(net):9.2f} {float(fee):9.2f} "
              f"{float(noti):10.0f}")
    print(f"合计 {tot} 腿")
    taker = sum(int(r[1]) for r in rows if str(r[0]).endswith("taker"))
    print(f"taker 腿 {taker} / {tot}"
          f"（{100.0*taker/max(tot,1):.1f}%）")

    # 心跳里的新标签
    hits, last = 0, ""
    if LOG.exists():
        for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            if "[mm-worker]" in ln and "skip=" in ln:
                last = ln
                if "ofi_flatten_maker" in ln:
                    hits += 1
    print("=" * 92)
    print("心跳里含 `ofi_flatten_maker` 的行数 =", hits)
    mm = re.search(r"skip=\{(.*)\}", last)
    if mm:
        kv = re.findall(r"'([^']+)':\s*(\d+)", mm.group(1))
        print("最新心跳 skip_counts:", dict(kv))
    print("最后心跳:", last[:200])
    OUT.write_text(json.dumps(
        {"started_at": started, "by_path": [
            {"path": r[0], "legs": int(r[1]), "net_bp": float(r[2]),
             "fee_bp": float(r[3]), "notional": float(r[4])} for r in rows],
         "taker_legs": taker, "total_legs": tot,
         "heartbeat_with_new_label": hits}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
