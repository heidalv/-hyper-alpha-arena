"""h563：**趋势闸上线前后的腿速对比**——它是不是"141 → 64 腿/h"的原因？

为什么关键：若趋势闸把腿速压掉近一半，那么**关掉它（h520，已在队列）就能解除腿量枷锁**
⇒ h527 的"剔除/缩小亏损币"才可能在不破 ≥60/h 的前提下成立 ⇒ 整个"腿量 vs 盈利"死结解开。
这比改宇宙（R51，已因 h356 判决与不可归因降级）干净得多。

口径（**必须带市场对照**，否则会把"市场变冷"误读成"闸门压制"）：
  · 分段以**登记表的 `h454_trial.started_at`** 为界（不手写时刻，避免时区错）；
  · 每段给出：车道腿/h、**市场真实成交笔/h（对照）**、每市场笔对应的腿数（"捕获率"代理）。
     若"腿/h 下降"与"市场笔/h 下降"同比例 ⇒ 是市场原因；若腿/h 独降 ⇒ 才是闸门。

用法：python scripts/h563_trend_gate_ab.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h425_repair_trial import (  # noqa: E402
    LANE, _covered_hours, _market_activity, read_env_dsn,
)


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0] or {}
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            row = cur.fetchone()
            syms = [str(x) for x in (row[0] or [])] if row else []
    gate_at = dt.datetime.fromisoformat(
        str((m.get("h454_trial") or {}).get("started_at"))).astimezone()
    print(f"趋势闸（trend_only_q=0.35）上线于 **{gate_at:%m-%d %H:%M}**（本地，取自登记表）")
    print(f"当前 trend_only_q = {(m.get('params') or {}).get('trend_only_q')}\n")

    gate_utc = gate_at.astimezone(dt.timezone.utc)
    segs = [
        ("A 闸前（5 币，无闸）", gate_utc - dt.timedelta(hours=10.7), gate_utc),
        ("B 闸后（到停摆）", gate_utc, dt.datetime(2026, 9, 28, 20, 32,
                                                tzinfo=dt.timezone.utc)),
        ("C 恢复后（闸仍在）", dt.datetime(2026, 9, 29, 1, 9, tzinfo=dt.timezone.utc),
         dt.datetime.now(dt.timezone.utc)),
    ]
    print(f"{'段':<22} {'墙钟h':>6} {'可交易h':>7} {'腿数':>6} {'腿/h(可交易)':>12} "
          f"{'市场笔/h':>9} {'腿/千笔':>8} {'净$/h':>8}")
    out = []
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        for name, t0, t1 in segs:
            with c.cursor() as cur:
                cur.execute("SELECT count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8 "
                            "FROM lane_ledger WHERE lane_id=%s AND ts > %s AND ts <= %s",
                            (LANE, t0, t1))
                legs, net = cur.fetchone()
            wall_h = (t1 - t0).total_seconds() / 3600.0
            cov, ratio = _covered_hours(t0.isoformat(), t1.isoformat(), syms)
            mkt = _market_activity(t0.isoformat(), t1.isoformat(), syms) or {}
            tph = mkt.get("trades_per_h") or 0.0
            legs_h = (legs / cov) if cov else (legs / wall_h)
            per_k = (1000.0 * legs / (tph * cov)) if (tph and cov) else None
            print(f"{name:<22} {wall_h:6.2f} {(cov or 0):7.2f} {legs:6d} {legs_h:12.1f} "
                  f"{tph:9.0f} {(per_k if per_k is None else round(per_k,1)):>8} "
                  f"{(net/cov if cov else 0):+8.2f}")
            out.append({"segment": name, "wall_h": round(wall_h, 2),
                        "covered_h": (None if cov is None else round(cov, 2)),
                        "legs": int(legs), "legs_per_covered_h": round(legs_h, 1),
                        "market_trades_per_h": round(tph, 1),
                        "legs_per_1k_market_trades": (None if per_k is None
                                                     else round(per_k, 2)),
                        "net_usd_per_h": round((net / cov) if cov else 0.0, 3)})
    print("\n判读：比较 A→B 的『腿/千笔』（已用市场活跃度归一）——")
    a, b = out[0], out[1]
    if a["legs_per_1k_market_trades"] and b["legs_per_1k_market_trades"]:
        d = (b["legs_per_1k_market_trades"] / a["legs_per_1k_market_trades"] - 1) * 100
        print(f"  A={a['legs_per_1k_market_trades']} → B={b['legs_per_1k_market_trades']}"
              f"（{d:+.0f}%）")
        if d < -15:
            print("  ⇒ **闸门确实压低了单位市场活跃度下的腿量** ⇒ h520（关闸）值得按队列推进")
        elif d > 15:
            print("  ⇒ 单位腿量反而上升 ⇒ 闸门不是腿速下降的原因（更可能是市场/配置）")
        else:
            print("  ⇒ 变化不显著 ⇒ 不能把腿速变化归给闸门")
    p = ROOT / "research_l1" / "out" / "h563_trend_gate_ab.json"
    p.write_text(json.dumps({"gate_at": gate_at.isoformat(), "segments": out},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写:", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
