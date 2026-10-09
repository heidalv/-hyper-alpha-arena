"""h582 — ② 试跑的**累积体检**（只读，R88）：判决前确认"该有的东西在长"。

用法：python scripts/h582_trial_accumulation.py
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
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402


def main() -> int:
    print("=" * 88)
    print("②（h463）试跑累积体检")
    print("=" * 88)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        t = dict(meta.get("h463_trial") or {})
        since, judge_at = t.get("started_at"), t.get("judge_at")
        now = dt.datetime.now(dt.timezone.utc)
        print(f"  起点 {since}  判定 {judge_at}  当前 {now.isoformat(timespec='seconds')}")
        hours = (now - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
        print(f"  窗长 {hours:.2f}h（判定时将有 12.00h）")
        cov, ratio = h._covered_hours(since, now.isoformat(), syms)
        arr = h._per_leg(cur, since, now.isoformat(), syms)
        rate = len(arr) / cov if cov else None
        print(f"  累计腿数 {len(arr)}  可交易 {cov:.2f}h（覆盖 {ratio:.0%}）"
              f"  可交易腿速 {rate:.1f}/h")
        need = 12.0 * rate if rate else None
        print(f"  ⇒ 按此速率，判定时预计约 {need:.0f} 腿" if need else "  ⇒ 速率不可算")
        # 每半小时的腿数（看是否稳定、有无断流）
        cur.execute(
            "SELECT to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'),"
            " 'HH24') AS hh, count(*) FROM lane_ledger WHERE lane_id=%s"
            " AND ts > %s::timestamptz GROUP BY 1 ORDER BY 1", (h.LANE, since))
        print("\n  逐小时出腿（本地时）：")
        for hh, n in cur.fetchall():
            print(f"    {hh} 时: {n:>4} 腿")
        # [R119] 判定将要用的**机制指标**（`legs_per_trip`）的当前状态：
        # 该指标在短窗会被"未平往返"灌水 ✗ ⇒ 需连同**出场腿数**一起看才有意义 ✓。
        try:
            sub = h._sub_stats(cur, "h463", since, now.isoformat(), syms)
            hw = dict((sub.get("hold_window") or {}))
            print("\n  机制口径（判定判据 C 的代理）：")
            print(f"    腿={hw.get('legs')} 出场腿={hw.get('exit_legs')} "
                  f"加仓腿={hw.get('add_legs')} ⇒ **腿/趟={hw.get('legs_per_trip')}**")
            print(f"    同体制参照（0.5 体制 + 45s，R72）= 3.6263 ⇒ "
                  f"当前 {(hw.get('legs_per_trip') or 0) / 3.6263:.2f}× 参照")
            print("    ⚠️ 出场腿少时该指标不可用（未平往返灌水）⇒ 12h 窗（出场腿 ≈100+）才稳 ✓")
        except Exception as exc:  # noqa: BLE001
            print(f"  机制口径取数失败（不阻塞）：{type(exc).__name__}")
        # [R163] 判定的"市场解释"分支要看**活跃度/波动对比**（`_market_activity`）——
        # 提前算出来，就能预判今晚走哪条分支（frequency_below_mandate vs 市场解释）✓。
        try:
            _t0 = dt.datetime.fromisoformat(since)
            _m_t = h._market_activity(since, now.isoformat(), syms)
            _b0 = (_t0 - dt.timedelta(hours=24)).isoformat()
            _b1 = (_t0 - dt.timedelta(hours=12)).isoformat()
            _m_b = h._market_activity(_b0, _b1, syms)
            _cold = (_m_t.get("trades_per_h", 0) / _m_b["trades_per_h"]
                     if _m_b.get("trades_per_h") else None)
            _hot = (_m_t.get("vol_bp", 0) / _m_b["vol_bp"]
                    if _m_b.get("vol_bp") else None)
            print("\n  市场归因对照（判定在该分支用的就是这两个比值）：")
            print(f"    活跃度 试跑 {_m_t.get('trades_per_h'):.0f} 笔/h vs 基线 "
                  f"{_m_b.get('trades_per_h'):.0f} ⇒ **{_cold:.2f}×**"
                  f"{'（<0.85 ⇒ 若频率失败会走市场解释 ✓）' if _cold and _cold < 0.85 else ''}")
            print(f"    波动代理 试跑 {_m_t.get('vol_bp'):.2f} vs 基线 {_m_b.get('vol_bp'):.2f}"
                  f" ⇒ **{_hot:.2f}×**"
                  f"{'（>1.2 ⇒ 同样走市场解释 ✓）' if _hot and _hot > 1.2 else ''}")
        except Exception as exc:  # noqa: BLE001
            print(f"  市场归因取数失败（不阻塞）：{type(exc).__name__}")
        # [R90] 分钟级断流检查（本项目的停摆史 ⇒ 两步：先看最近 N 分钟有没有腿）
        ap = argparse.ArgumentParser()
        ap.add_argument("--minutes", type=int, default=15,
                        help="分钟级断流检查的回看窗口（默认 15）")
        _a = ap.parse_args()
        cur.execute(
            "SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'HH24:MI'), count(*)"
            " FROM lane_ledger WHERE lane_id=%s"
            " AND ts > now() - make_interval(mins => %s) GROUP BY 1 ORDER BY 1",
            (h.LANE, _a.minutes))
        rows = cur.fetchall()
        total = sum(int(r[1]) for r in rows)
        print(f"\n  近 {_a.minutes} 分钟：有腿的分钟数 {len(rows)}、共 {total} 腿"
              f" ⇒ {total / (_a.minutes / 60.0):.1f}/h")
        print("  " + "  ".join(f"{r[0]}={r[1]}" for r in rows))
        if total == 0:
            print("  ⚠️ 近 %d 分钟零腿 ⇒ 疑似断流，查 `h536_lane_alarm.py`" % _a.minutes)
        elif len(rows) < _a.minutes * 0.2:
            print("  ⚠️ 出腿分钟稀疏（<20%%）⇒ 建议查行情数据滞后与代理隧道")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
