"""h555：**h472（ofi_flatten 改被动执行）的机制是否仍成立**——同一口径分三段测。

为什么做：h472 的判定任务在停摆期被冻结（窗口被污染），**目前没有人盯着它**；
而它的实测价值是 **+0.32$/h**（105 腿的 ofi_flatten 路径由 −3.12$ 翻成 +1.87$）。
停摆恢复后应当确认：taker 出场占比是否真的降下来了。

⚠️ 口径陷阱（我自己先踩了一次）：`taker 腿 / 全部腿` ≈ 20%，而
`taker 出场腿 / 出场腿` ≈ 90%+ —— 因为绝大多数腿是**入场腿**（maker 免费）。
两者不可混比。本脚本一律用**出场腿口径**，并同时报出"每腿费用"。

三段：
  ① 修复前     ：09-28 13:00 → 09-28 18:33（h472 部署前）
  ② 修复后停摆前：09-28 18:33 → 09-29 04:32
  ③ 恢复后     ：09-29 09:09 → now
判据：若 ②/③ 的 `taker 出场占比` 与 `费/腿` 显著低于 ①，则修复成立。

用法：python scripts/h555_h472_mechanism.py
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
from scripts.h425_repair_trial import LANE, read_env_dsn  # noqa: E402

Q = """
SELECT
  count(*) AS legs,
  count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') <> '') AS exits,
  count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') LIKE '%%taker') AS taker_exits,
  count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') LIKE '%%maker') AS maker_exits,
  count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') LIKE 'ofi_flatten_taker')
    AS ofi_taker,
  count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') LIKE 'ofi_flatten_maker')
    AS ofi_maker,
  COALESCE(avg(fee_bp),0)::float8 AS fee_all,
  COALESCE(sum(fee_bp*notional/1e4),0)::float8 AS fee_usd,
  COALESCE(sum(net_bp*notional/1e4),0)::float8 AS net_usd
FROM lane_ledger
WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= COALESCE(%s::timestamptz, now())
"""


def _segments() -> list:
    """三段边界：**从登记表读 h472 的部署时刻**，不手写本地时刻。

    ⚠️ 我第一版把文档里的「18:33Z」当**本地时间**用（它其实是 UTC = 本地 02:33）
    ⇒ 段②几乎全是"修复前"，得出"未见改善"的**假结论** ✗。改为读
    `h472_trial.started_at` 并显式时区转换 ⇒ 从根上消除这类错误。
    """
    import psycopg
    boundary = "2026-09-29 02:33"
    try:
        with psycopg.connect(read_env_dsn(), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'h472_trial'->>'started_at' "
                            "FROM lane_registry WHERE lane_id=%s", (LANE,))
                row = cur.fetchone()
        if row and row[0]:
            boundary = (dt.datetime.fromisoformat(str(row[0]))
                        .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as exc:  # noqa: BLE001
        print(f"（读 h472 部署时刻失败，用回退值 {boundary}：{str(exc)[:60]}）")
    print(f"h472 部署时刻（本地）= {boundary}   停摆 = 04:32–09:09")
    return [
        ("① 修复前", "2026-09-28 13:00", boundary),
        ("② 修复后·停摆前", boundary, "2026-09-29 04:32"),
        ("③ 恢复后", "2026-09-29 09:09", None),
    ]


def main() -> int:
    out = []
    segments = _segments()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            print(f"\n{'段':<18} {'腿数':>6} {'出场':>5} {'taker出场':>8} {'占比':>7} "
                  f"{'maker出场':>9} {'ofi_t/m':>9} {'费bp/腿':>8} {'费$':>8} {'净$':>9}")
            for name, t0, t1 in segments:
                cur.execute(Q, (LANE, t0, t1))
                r = cur.fetchone()
                legs, ex, tk, mk, ot, om, fee_all, fee_usd, net = r
                share = (tk / ex) if ex else float("nan")
                print(f"{name:<18} {legs:6d} {ex:5d} {tk:8d} {share:7.1%} {mk:9d} "
                      f"{str(ot)+'/'+str(om):>9} {fee_all:+8.3f} {fee_usd:+8.2f} {net:+9.2f}")
                out.append({"segment": name, "legs": legs, "exits": ex, "taker_exits": tk,
                            "maker_exits": mk, "ofi_taker": ot, "ofi_maker": om,
                            "taker_share_of_exits": (round(share, 4)
                                                     if ex else None),
                            "fee_bp_per_leg": round(fee_all, 3),
                            "fee_usd": round(fee_usd, 2), "net_usd": round(net, 2)})
    a, b, c3 = out
    print("\n判读：")
    if a["taker_share_of_exits"] and b["taker_share_of_exits"]:
        d = a["taker_share_of_exits"] - b["taker_share_of_exits"]
        print(f"  ①→② taker 出场占比变化：{a['taker_share_of_exits']:.1%} → "
              f"{b['taker_share_of_exits']:.1%}（Δ={d:+.1%}）"
              f"{'  ⇒ 修复**成立** ✓' if d > 0.05 else '  ⇒ 未见明显改善 ⚠️'}")
    print(f"  ①→② 费/腿：{a['fee_bp_per_leg']:+.3f} → {b['fee_bp_per_leg']:+.3f} bp")
    print(f"  ③ 恢复后：taker 出场占比 "
          f"{c3['taker_share_of_exits'] if c3['taker_share_of_exits'] is None else format(c3['taker_share_of_exits'], '.1%')}"
          f"，费/腿 {c3['fee_bp_per_leg']:+.3f}bp，净 {c3['net_usd']:+.2f}$")
    print("  ⚠️ 注意 ③ 的样本很小（恢复后仅约 1.5h），只作机制方向的旁证。")
    p = ROOT / "research_l1" / "out" / "h555_h472_mechanism.json"
    p.write_text(json.dumps({"segments": out, "generated_at": dt.datetime.now().isoformat()},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
