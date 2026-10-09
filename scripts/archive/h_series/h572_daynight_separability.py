"""h572 — `legs_per_trip` 的"昼夜 vs 阈值"**可分离性**检验（只读，R71）。

为什么要做：R68 我用"同为夜间"的 E(0.15, 21:48→23:17) 与 C(0.5, 23:17→09:48) 对比，
断言 6.75→3.7 的落差是**昼夜**、不是阈值。但这两个窗**小时构成不同**
（E 覆盖 22:00/23:00 两个薄小时；C 覆盖 00:00–02:00 三个最忙小时，见 `h571`）
⇒ 该对比同样被小时构成混淆。而 0.15 与 0.5 两个体制在**现纪元内没有同钟点重叠**
（0.15 = 09-28 13:00→23:17 的昼/傍晚；0.5 = 23:17→今晨的夜/晨）⇒ 现纪元内**不可分离**。

本脚本用**纪元前的夜间**（09-27/28，10 币纪元，`ofi_confirm=0.15`）作为夜间 0.15 的样本，
与昨天的夜间 0.5 做**同钟点**对比：
  · 若 N1(夜,0.15,10币) 也 ≈2.8–3.7 ⇒ 夜间本身就低 ⇒ R68 的昼夜结论成立 ✓
  · 若 N1 ≈6–7 ⇒ 低值是体制/阈值造成的 ⇒ **R68 必须撤回** ✗
（纪元前的 5 币仍在报价，但整机同时报 10 币 ⇒ 仍有"宇宙构成"混淆，一并登记。）

用法：python scripts/h572_daynight_separability.py
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
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))


def _iso(y, mo, d, hh, mm=0) -> str:
    return dt.datetime(y, mo, d, hh, mm, tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()


# (标签, 起点, 终点, 该窗内的 ofi_confirm 体制, 币宇宙)
# ⚠️ 前提：`ofi_confirm=0.15 → 0.5` 的变更发生在 09-28 23:17L（h448），故此前的窗都是 0.15
#    （`ops_changes` 只留最近 20 条，更早的变更无法回查 ⇒ 本前提属"推定"，已登记）。
WINDOWS = [
    ("N0_夜_0.15_10币(纪元前,长)", _iso(2026, 9, 27, 22), _iso(2026, 9, 28, 9), "0.15", "10币"),
    ("N1_夜_0.15_10币(纪元前)", _iso(2026, 9, 28, 1), _iso(2026, 9, 28, 9), "0.15", "10币"),
    ("N2_夜_0.15→0.5_5币", _iso(2026, 9, 28, 22), _iso(2026, 9, 29, 9), "0.15→0.5", "5币"),
    ("N3_夜_0.5_5币(纯)", _iso(2026, 9, 28, 23, 17), _iso(2026, 9, 29, 9), "0.5", "5币"),
    ("D1_昼_0.15_5币", _iso(2026, 9, 28, 13), _iso(2026, 9, 28, 21, 48), "0.15", "5币"),
    ("D2_昼_0.15_10币(纪元前)", _iso(2026, 9, 27, 10), _iso(2026, 9, 27, 20), "0.15", "10币"),
]


def main() -> int:
    out = {"windows": {}}
    with psycopg.connect(h.read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            out["symbols"] = syms
            print(f"{'窗':<28}{'腿':>6}{'出场腿':>7}{'腿/趟':>8}{'趟/h(可交易)':>13}")
            for name, a, b, regime, universe in WINDOWS:
                s = h._sub_stats(cur, "h463", a, b, syms)
                hw = dict(s.get("hold_window") or {})
                exits = int(hw.get("exit_legs") or 0)
                legs = int(hw.get("legs") or 0)
                lpt = hw.get("legs_per_trip")
                # 含 pre-era 币宇宙差异 ⇒ 单独用同一 syms 过滤，保证口径一致
                cov, covr = h._covered_hours(a, b, syms)
                trips_h = (exits / cov) if (cov and exits) else None
                out["windows"][name] = {
                    "local": (dt.datetime.fromisoformat(a).astimezone(TZ).isoformat(),
                              dt.datetime.fromisoformat(b).astimezone(TZ).isoformat()),
                    "regime_ofi": regime, "universe": universe,
                    "legs": legs, "exit_legs": exits, "legs_per_trip": lpt,
                    "covered_h": round(cov, 2) if cov else None,
                    "trips_per_covered_h": (round(trips_h, 2) if trips_h else None),
                }
                print(f"{name:<28}{legs:>6}{exits:>7}"
                      f"{(f'{lpt:.2f}' if lpt is not None else '-'):>8}"
                      f"{(f'{trips_h:.1f}' if trips_h else '-'):>13}")
            # 【数据质量】`exit_path` 的可用性随时间的分布：D2 出现 25.77 的异常值
            # （567 腿仅 22 出场腿 ⇒ 出场标记疑似缺失）⇒ 先确认哪些时段的该字段可信。
            cur.execute(
                "SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24') AS hr,"
                " count(*) AS legs,"
                " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') <> '')"
                "   AS with_exit"
                " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
                " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 1",
                (h.LANE, _iso(2026, 9, 27, 0), syms))
            cov_rows = [{"hour": str(r[0]), "legs": int(r[1]), "with_exit": int(r[2])}
                        for r in cur.fetchall()]
            out["exit_path_coverage_by_hour"] = cov_rows
            print("\n[exit_path 覆盖率] 仅列出覆盖率 <10% 的小时（其余正常）：")
            _bad = [r for r in cov_rows
                    if r["legs"] and r["with_exit"] / r["legs"] < 0.10]
            if not _bad:
                print("  （无）")
            for r in _bad:
                print(f"  {r['hour']}  腿={r['legs']} 有出场标记={r['with_exit']}"
                      f" ⇒ {r['with_exit'] / r['legs']:.1%}")
    p = ROOT / "research_l1" / "out" / "h572_daynight_separability.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
