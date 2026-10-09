"""h569 — ② 的**基线窗候选与混淆对照**（只读，R68 修正 h568 的锚点错误）。

R68 实测发现：判定代码的基线锚点是 **`since`（试跑起点 = 09-29 09:48L）**，
不是判定时刻 T（`h425_repair_trial.py:1115-1123`）：
    base_start = since − 24h ; base_end = since − 12h
    ⇒ 09-28 09:48L → 21:48L（若该窗 ≥200 腿则采用，否则退回 since−12h → since）

我上一轮（h568）按 T 锚点算成了 09-28 21:48 → 09-29 09:48L ⇒ **错了**。
更关键：真实基线窗整段落在 `ofi_confirm_threshold=0.15` 时代（23:17L 才改 0.5）
⇒ ② 的对比实际是「(45s, 0.15) vs (90s, 0.5)」**双变量**，不是单变量。

本脚本给出：权威参数变更时间线 + 4 个候选基线窗的读数，供**判定前**预注册选窗规则。

用法：python scripts/h569_baseline_candidates.py
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
TRIAL_START_LOCAL = dt.datetime(2026, 9, 29, 9, 48)


def _iso(d: dt.datetime) -> str:
    return d.replace(tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()


def main() -> int:
    s = TRIAL_START_LOCAL
    out: dict = {"trial_start_local": s.isoformat(), "windows": {}, "timeline": []}
    with psycopg.connect(h.read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(x) for x in (meta.get("symbols") or []) if str(x)]
            out["symbols"] = syms
            # 权威时间线：注册表里的参数变更审计（不靠记忆/文档）
            for e in (meta.get("ops_changes") or []):
                out["timeline"].append({
                    "at": str(e.get("at") or e.get("ts") or ""),
                    "kind": str(e.get("kind") or ""),
                    "key": str(e.get("key") or e.get("trial") or ""),
                    "old": e.get("old"), "new": e.get("new")})
            for k in ("h448_trial", "h463_trial", "h472_trial", "h464_trial"):
                t = dict(meta.get(k) or {})
                out.setdefault("trials", {})[k] = {
                    "started_at": t.get("started_at"), "verdict": t.get("verdict")}
            cands = {
                # 判定代码**实际使用**的窗（`since` 锚点，regime 对齐）
                "A_judge_aligned": (s - dt.timedelta(hours=24), s - dt.timedelta(hours=12)),
                # 判定代码的**回退**窗（`since` 锚点，紧邻 12h）
                "B_judge_fallback": (s - dt.timedelta(hours=12), s),
                # 干净窗：`ofi_confirm` 0.5 生效之后（09-28 23:17L）→ 试跑起点
                "C_clean_0p5": (dt.datetime(2026, 9, 28, 23, 17), s),
                # 参考：试跑起点前 24h
                "D_prev24h": (s - dt.timedelta(hours=24), s),
                # 【R68 决定性对照】同为**夜间 + 5 币纪元**，只差 `ofi_confirm`：
                #   E = 0.15 时代（09-28 21:48→23:17，1.5h）
                #   C = 0.5 时代（09-28 23:17→09:48）
                # 若 E 的 `legs_per_trip` ≈ C ⇒ 6.75→3.7 的落差是**昼夜**造成的，
                # 阈值混淆 ≈ 0 ⇒ ② 的机制判据干净；若 E ≈ A(6.75) ⇒ 阈值混淆真实且巨大。
                "E_night_0p15": (dt.datetime(2026, 9, 28, 21, 48),
                                 dt.datetime(2026, 9, 28, 23, 17)),
            }
            for name, (a, b) in cands.items():
                legs = h._per_leg(cur, _iso(a), _iso(b), syms)
                w = h._window(cur, _iso(a), _iso(b), (b - a).total_seconds() / 3600.0, syms)
                cov, covr = h._covered_hours(_iso(a), _iso(b), syms)
                wall = float(w["legs_per_hour"])
                rate = (w["legs"] / max(cov, 0.01)) if cov else wall
                cur.execute(
                    "SELECT symbol, count(*) FROM lane_ledger WHERE lane_id=%s"
                    " AND ts > %s::timestamptz AND ts <= %s::timestamptz"
                    " AND symbol = ANY(%s) GROUP BY 1 ORDER BY 2 DESC",
                    (h.LANE, _iso(a), _iso(b), syms))
                out["windows"][name] = {
                    "local": [a.isoformat(), b.isoformat()],
                    "wall_h": round((b - a).total_seconds() / 3600.0, 3),
                    "legs": int(w["legs"]), "per_leg_n": len(legs),
                    "covered_h": round(cov, 3) if cov else None,
                    "coverage": round(covr, 4) if covr is not None else None,
                    "rate_covered": round(rate, 2), "rate_wall": round(wall, 2),
                    "per_coin": {str(r[0]): int(r[1]) for r in cur.fetchall()},
                }
                o = out["windows"][name]
                # 机制口径（判据 C 的 `legs_per_trip` 等）——用于量化基线侧混淆的**大小**
                try:
                    o["h463_mech"] = h._sub_stats(cur, "h463", _iso(a), _iso(b), syms)
                except Exception as exc:  # noqa: BLE001
                    o["h463_mech"] = {"error": str(exc)}
                print(f"[{name}] {o['local'][0]} → {o['local'][1]}  "
                      f"腿={o['legs']} 可交易={o['covered_h']}h({o['coverage']:.1%}) "
                      f"腿速={o['rate_covered']}/h(墙钟 {o['rate_wall']}) "
                      f"腿/趟={o['h463_mech'].get('hold_window', {}).get('legs_per_trip')}")
    p = ROOT / "research_l1" / "out" / "h569_baseline_candidates.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {p.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
