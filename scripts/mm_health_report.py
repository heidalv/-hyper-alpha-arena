# -*- coding: utf-8 -*-
"""L1 被动做市 · 一页体检报告（只读）。

为什么需要：做市的健康度分散在 6 个地方（注册表参数、运行态挂单/库存、账本归因、
双账一致性、行情数据健康、闸门拦截分布），排查时要逐个查表。本脚本一次打印全部，
并给出**统计显著性**（用回放逐笔标准差 8.06bp 估 95% 区间）——避免把噪声当结论。

用法:
    python scripts/mm_health_report.py                 # 默认车道 mm_asterdex
    python scripts/mm_health_report.py --lane mm_asterdex --hours 3
    python scripts/mm_health_report.py --replay        # 附带回放期望（较慢，约 30s）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402
from backend.services import lane_ledger, lane_registry  # noqa: E402

PER_FILL_SD_BP = 8.06      # 回放逐笔 net_bp 标准差（13558 笔实测），用于置信区间


def _hr(t=""):
    print("\n" + ("─" * 4 + f" {t} " if t else "") + "─" * max(0, 60 - len(t)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--replay", action="store_true", help="附带回放期望（慢）")
    args = ap.parse_args()
    lane_id = args.lane
    lane = lane_registry.get_lane(lane_id) or {}
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})
    symbols = list(meta.get("symbols") or [])

    print(f"L1 做市体检 · 车道 {lane_id} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式={lane.get('mode')} 状态={lane.get('status')} 币种={symbols} "
          f"时代起点={meta.get('stats_since')}")

    _hr("① 参数（0 的特殊含义：多数字段 0 = 显式关闭）")
    keys = ("w_base_bp", "k_vol", "k_vol_sigma_cap", "k_inv", "frozen_width_bp",
            "frozen_max_move_bp", "frozen_lookback", "fill_notional", "compound_ratio")
    print("  " + "  ".join(f"{k}={params.get(k)}" for k in keys if k in params))
    lkeys = ("max_symbol_notional_ratio", "max_net_directional_ratio",
             "max_net_exposure_ratio", "max_gross_notional_ratio", "max_one_side_seconds",
             "stop_loss_bp", "trend_pause_bp", "vol_pause_mult", "vol_pause_sigma",
             "ofi_block_threshold", "ofi_flatten_threshold", "max_quote_age_sec",
             "toxic_streak", "daily_loss_stop_pct")
    print("  " + "  ".join(f"{k}={params.get(k)}" for k in lkeys if k in params))
    print(f"  波动基准（注册表锚定）: "
          f"{ {k: round(float(v), 4) for k, v in (meta.get('replay_baseline') or {}).get('vol_baseline_bp', {}).items()} }")

    _hr("② 运行态（挂单/库存/产能）")
    try:
        import urllib.request
        st = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/trading/lanes/{lane_id}/shadow", timeout=20).read())
        print(f"  tick={st.get('ticks')} 成交={st.get('fills')} 本进程速率={st.get('fills_per_hour')}/h "
              f"腿量={round(float(st.get('fill_notional') or 0), 2)} "
              f"权益={round(float(st.get('account_equity') or 0), 2)}")
        aw = st.get("avg_width_bp") or {}
        print(f"  挂宽(半宽bp): 买={aw.get('bid')} 卖={aw.get('ask')} 平均σ={st.get('avg_sigma')} "
              f"报价决策={st.get('quoted_decisions')}")
        print(f"  报价侧分布: {st.get('side_counts')}")
        print(f"  闸门拦截: {st.get('skip_counts')}")
        print(f"  孤儿持仓: {st.get('orphan_inventory')}  错误: {st.get('last_error') or '无'}")
    except Exception as e:
        print(f"  （无法读取运行态: {e}）")

    _hr(f"③ 时代表现（近 {args.hours:g} 小时 + 全时代）")
    since_era = meta.get("stats_since")
    # [F99] 近期窗口必须**与时代取交集**：否则「近 2 小时」会把时代之前的旧配置成交
    # （今天含 −$13.7 的修复前亏损）算进来——正是用户反复抱怨的「旧数据污染新数据」。
    era_ts = None
    if since_era:
        try:
            from datetime import datetime as _dt
            era_ts = _dt.fromisoformat(str(since_era)).timestamp()
        except Exception:
            era_ts = None
    wins = []
    if era_ts is not None and (time.time() - era_ts) < args.hours * 3600.0:
        wins.append((f"时代内 {max(1.0, (time.time() - era_ts) / 3600.0):.2f}h",
                     max(1.0, (time.time() - era_ts) / 3600.0), since_era))
    else:
        wins.append((f"近 {args.hours:g}h", args.hours, since_era))
    wins.append(("全时代", 30.0 * 24.0, since_era))
    # [F149] 必须同时给出**自最近一次配置变更以来**的窗口：`stats_since`（时代起点）
    # 可能早于今天的全部修复（实测 11:50 ⇒ 把坏管道的亏损也算进"本时代"，
    # 界面读数是 −0.19bp 而配置变更后的真实窗口是 +0.18bp ✗）。注意：**不能**直接把
    # `stats_since` 前移来"修正"显示——它同时是日亏闸的裁剪依据（改动会放松风控 ✗）。
    try:
        from backend.services import lane_registry as _reg
        _meta = (_reg.get_lane(lane_id) or {}).get("meta") or {}
        _cands = [x.get("ts") for x in (_meta.get("ops_changes") or []) if x.get("ts")]
        _ev = _meta.get("evolution") or {}
        if _ev.get("last_change_ts"):
            _cands.append(_ev["last_change_ts"])
        if _cands:
            import datetime as _dt
            _lc = max(_dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                      for t in _cands).astimezone()
            _hrs = max(0.05, (time.time() - _lc.timestamp()) / 3600.0)
            wins.append((f"自变更({_lc:%H:%M})", _hrs, _lc.isoformat()))
    except Exception as _e:  # pragma: no cover
        print(f"  （配置变更窗口计算失败: {_e}）")
    for tag, hrs, since in wins:
        a = lane_ledger.attribution(days=hrs / 24.0, lane_id=lane_id, since=since)
        t = a.get("total") or {}
        n = int(t.get("n") or 0)
        bp = float(t.get("net_bp") or 0.0)
        usd = float(t.get("net_usd") or 0.0)
        ci = 1.96 * PER_FILL_SD_BP / math.sqrt(n) if n else 0.0
        sig = "（含 0，噪声）" if abs(bp) < ci else "（显著）"
        print(f"  {tag:<10} 成交={n:<5} 净额={usd:+8.3f}$ 净={bp:+7.3f}bp ±{ci:.2f} {sig}")
        if n:
            print(f"         价差={float(t.get('spread_bp') or 0):+.3f} "
                  f"价格={float(t.get('price_bp') or 0):+.3f} "
                  f"费={float(t.get('fee_bp') or 0):+.4f} 名义=${float(t.get('notional') or 0):,.0f}")
    fs = lane_ledger.flatten_stats(lane_id, days=args.hours / 24.0,
                                   since=since_era) or {}
    print(f"  平仓占比={fs.get('flatten_share')} 平仓笔数={fs.get('flattens')}")

    # [F168] 风控闸门余量：夜间/长跑累积最怕"日亏闸被触发 ⇒ 车道停车 ⇒ 证据链中断" ✗
    # 这一节给出阈值、当日已实现净额与余量，让"会不会被打断"一眼可见 ✓。
    try:
        from backend.services.market_maker.runner import (
            lane_day_pnl_usd, lane_limits_enforce_enabled)
        _thr = -float(st.get("account_equity") or st.get("equity") or 300.0) * float(
            params.get("daily_loss_stop_pct", 10.0)) / 100.0
        _day = lane_day_pnl_usd(lane_id)
        _eq = float(st.get("account_equity") or st.get("equity") or 300.0)
        _hr("④b 风控闸门余量（保护长时间累积不被打断）")
        print(f"  车道闸门武装={lane_limits_enforce_enabled()}  "
              f"日亏阈值={_thr:+.2f}$  当日已实现={_day:+.3f}$  "
              f"余量={_day - _thr:+.3f}$（{(_day - _thr)/max(1.0, _eq)*100:.2f}% 权益）")
    except Exception as e:  # pragma: no cover
        print(f"  （闸门余量读取失败: {e}）")

    _hr("④ 双账一致性（运行态 vs 账本重建）")
    try:
        from backend.services.market_maker.reconcile import compare_lane_books
        rc = compare_lane_books(lane_id=lane_id)
        print(f"  {'✅ 一致' if rc['ok'] else '❌ 分叉'}  检查 {rc['checked']} 币种"
              + (f"  分叉: {[(m['symbol'], m['diff_usd']) for m in rc['mismatches']]}" if not rc["ok"] else ""))
        if rc.get("rt_only"):
            print(f"  ⚠️ 宇宙外仍有运行态: {rc['rt_only']}")
    except Exception as e:
        print(f"  （对账失败: {e}）")

    _hr("⑤ 行情数据健康（近 1 小时缺口）")
    try:
        now_ms = int(time.time() * 1000)
        with system_identity():
            with MarketSessionLocal() as db:
                for s in symbols:
                    arr = [int(x[0]) for x in db.execute(text(
                        "SELECT timestamp FROM market_orderbook_snapshots"
                        " WHERE exchange=:e AND symbol=:s AND timestamp >= :lo ORDER BY timestamp"),
                        {"e": str(meta.get("venue") or "asterdex"), "s": s,
                         "lo": now_ms - 3600_000}).fetchall()]
                    if not arr:
                        print(f"  {s:<5} 近 1 小时无快照 ⚠️")
                        continue
                    age = (now_ms - arr[-1]) / 1000.0
                    gaps = sum(1 for a, b in zip(arr, arr[1:]) if b - a > 60_000)
                    print(f"  {s:<5} 快照={len(arr):<4} 最新年龄={age:5.1f}s "
                          f"间隔中位={sorted(b - a for a, b in zip(arr, arr[1:]))[len(arr)//2]//1000 if len(arr)>1 else 0}s "
                          f">60s 缺口={gaps}")
    except Exception as e:
        print(f"  （行情检查失败: {e}）")

    if args.replay:
        _hr("⑥ 回放期望（同配置、近 8 天数据）")
        try:
            from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
            from backend.services.market_maker.portfolio_replay import (
                replay_portfolio, _load_all)
            from backend.services.market_maker.runner import get_runner
            r = get_runner(lane_id)
            data = _load_all(symbols, str(meta.get("venue") or "asterdex"))
            lo = int(time.time() * 1000) - 8 * 86400 * 1000
            sub = {}
            for s in symbols:
                d = data[s]
                m = d["ots"] >= lo
                sub[s] = {k: d[k][m] for k in ("ots", "bb", "ba")}
                tm = d["tts"] >= lo
                for k in ("tts", "lo", "hi", "sv", "bv"):
                    sub[s][k] = d[k][tm]
            rep = replay_portfolio(symbols, venue=r.venue, equity=r.equity, params=r.params,
                                   limits=r.limits, fill_notional=r.fill_notional,
                                   fill_notional_ratio=max(0.0, r.compound_ratio), data=sub)
            print(f"  成交={rep['fills']} 净额={rep['net_usd']:+.2f}$ 净={rep['net_bp']:+.3f}bp "
                  f"dd={rep.get('max_dd_pct')}% 期末=${rep['final_equity']} "
                  f"真实净峰=${rep['max_net_usd']:.0f}")
            print(f"  正收益折数: {rep.get('lane_pause_counts') or '—'}  "
                  f"最大日亏=${rep.get('max_daily_loss_usd')}")
        except Exception as e:
            print(f"  （回放失败: {e}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
