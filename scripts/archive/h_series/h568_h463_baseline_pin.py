"""h568 — 把 ②（`max_one_side_seconds` 45→90）的**基线窗冻结读数**提前算出来（只读）。

为什么需要：② 的硬闸判据 A = 「可交易口径腿速 ≥60/h（绝对地板）**且** ≥0.8×基线」。
基线窗整段在过去 ⇒ 现在就能算出来；提前钉死它，21:48 的判决就只剩试跑窗一个未知量。

⚠️ **R68 修正**：锚点是 **`since`（试跑起点）**，不是判定时刻 T！
    判定代码（`h425_repair_trial.py:1115-1123`）：
        base_start = since − 24h ; base_end = since − 12h
        若该窗 ≥200 腿则采用，否则退回 `since − 12h → since`
    本脚本首版按 T 锚点算 ⇒ **钉错了窗口**（钉成 `T−24h→T−12h`）。现改为**逐行复刻**
    判定代码的选窗逻辑，避免"工具与判定口径不一致"这种最阴的错。
    （实机验证方式：以计划任务的真实 cwd `C:\\Windows\\System32` 跑一次
     `--judge --dry-run`，比对这里打印的基线腿数/腿速。）

用法：
    python scripts/h568_h463_baseline_pin.py                    # ②（h463，用 meta 里的起点）
    python scripts/h568_h463_baseline_pin.py h464 "2026-09-29 21:58"   # ③（部署前预演）
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
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


def main() -> int:
    ap = argparse.ArgumentParser(description="试跑基线窗冻结读数（只读）")
    ap.add_argument("trial", nargs="?", default="h463", choices=["h463", "h464"],
                    help="试跑键（默认 h463 = ②）")
    ap.add_argument("planned_start", nargs="?", default="",
                    help='部署前的**计划**起点（本地，如 "2026-09-29 21:58"）；'
                         "留空则读 meta 里的 started_at")
    a = ap.parse_args()
    key = a.trial
    meta_key = f"{key}_trial"
    out: dict = {"trial": key}
    with psycopg.connect(h.read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            trial = dict(meta.get(meta_key) or {})
            since = trial.get("started_at")
            out["meta_started_at"] = since
            if a.planned_start:
                _p = dt.datetime.strptime(a.planned_start, "%Y-%m-%d %H:%M")
                since = _p.replace(tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()
                out["planned_start_used"] = since
            if not since:
                print(f"✗ meta.{meta_key}.started_at 缺失（用 planned_start 参数预演）")
                return 1
            _t0 = dt.datetime.fromisoformat(since)
            base_start = (_t0 - dt.timedelta(hours=24)).isoformat()
            base_end = (_t0 - dt.timedelta(hours=12)).isoformat()
            out["trial_started_at"] = since
            out["symbols"] = syms
            # —— 逐行复刻判定代码的选窗逻辑 ——
            b_legs = h._per_leg(cur, base_start, base_end, syms)
            if len(b_legs) >= 200:
                base_cut, base_until, win_h, which = base_start, base_end, 12.0, "aligned"
            else:
                base_cut = (_t0 - dt.timedelta(hours=12)).isoformat()
                base_until, win_h, which = since, 12.0, "fallback"
            bsum = h._window(cur, base_cut, base_until, win_h, syms)
            cov_b, covr_b = h._covered_hours(base_cut, base_until, syms)
            bph_wall = float(bsum["legs_per_hour"])
            bph = (bsum["legs"] / max(cov_b, 0.01)) if cov_b else bph_wall
            out["baseline"] = {
                "which": which, "window": [base_cut, base_until],
                "local": [dt.datetime.fromisoformat(base_cut).astimezone(TZ).isoformat(),
                          dt.datetime.fromisoformat(base_until).astimezone(TZ).isoformat()],
                "aligned_probe_legs": len(b_legs),
                "legs": int(bsum["legs"]),
                "covered_hours": round(cov_b, 3) if cov_b else None,
                "coverage": round(covr_b, 4) if covr_b is not None else None,
                "legs_per_hour_covered": round(bph, 2),
                "legs_per_hour_wall": round(bph_wall, 2),
            }
            # [R69] 回退窗 = **昼夜错位**风险：aligned 窗与试跑窗同钟点，
            # 回退窗则是"试跑起点前紧邻 12h" ⇒ 若试跑在夜间，回退窗就是白天，
            # 而昼夜对 `legs_per_trip` 的影响 ≈2.4×（R68）⇒ 判决会被昼夜差支配。
            _t_local = dt.datetime.fromisoformat(since).astimezone(TZ)
            out["daynight_alignment"] = {
                "trial_window_local_hours": [f"{_t_local:%H:%M}",
                                             f"{(_t_local + dt.timedelta(hours=12)):%H:%M}"],
                "aligned_ok": which == "aligned",
                "warning": ("" if which == "aligned" else
                            "⚠️ 探针 <200 腿 ⇒ 退回紧邻窗 ⇒ **基线与试跑窗昼夜错位**，"
                            "结论不可用（R68：昼夜效应 ≈2.4×，远大于待测效应）"),
            }
            out["gate"] = {
                "abs_floor": 60.0,
                "relative_requirement": round(0.8 * bph, 2),
                "trial_must_be_at_least": round(max(60.0, 0.8 * bph), 2),
                "binding": ("absolute_floor" if 60.0 >= 0.8 * bph else "relative"),
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
    p = ROOT / "research_l1" / "out" / f"h568_{key}_baseline_pin.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
