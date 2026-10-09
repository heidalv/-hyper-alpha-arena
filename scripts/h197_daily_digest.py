# -*- coding: utf-8 -*-
"""H197 长跑每日摘要 —— 一个月无人值守期间的唯一观测入口。

# 为什么要先建这个

用户决定"冻结参数，跑一个月"。一个月长跑里**人不会天天盯**，
所以必须有一个**自动落盘**的摘要，否则一个月后只剩一堆原始表，
而"这一个月发生了什么"无法回答。

更重要的：长跑期间**任何一次参数改动都会污染整段样本**。
⇒ 摘要里必须显式记录**参数指纹**，一旦变化就能立刻看出来。

# 它记录什么（每一项都有明确用途）

  1. **权益与已实现盈亏** —— 唯一的成绩单
  2. **参数指纹** —— 检测"有人偷偷改了参数"（否则样本作废）
  3. **宇宙** —— 选币器每 30 分钟可能改，记录变化历史
  4. **maker / flatten 拆分** —— 本会话已证明：亏全在 flatten
  5. **强平穿价率** —— 出库成本的核心指标（基线 100%）
  6. **按日分解** —— 避免用累计值掩盖 regime 变化
  7. **数据健康** —— 心跳新鲜度 / 采集断流，断了则整段样本不可信

# 用法

    python scripts/h197_daily_digest.py                 # 打印 + 追加到日志
    python scripts/h197_daily_digest.py --no-append     # 只打印
    python scripts/h197_daily_digest.py --days 7        # 只看近 7 天

输出同时追加到 `logs/monthly_digest.jsonl`（一行一条，便于事后分析）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
DIGEST = ROOT / "logs" / "monthly_digest.jsonl"


def dsn(which: str = "alpha_arena") -> str:
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
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def heartbeat() -> dict:
    for p in (ROOT / "logs" / "mm_lane_status.json",):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def param_fingerprint(params: dict, limits: dict) -> str:
    """只取**会影响决策**的键做指纹，避免无关字段造成假变更。"""
    import hashlib
    keys = ("spread_mult", "spread_mult_reduce", "min_edge_frac", "w_base_bp",
            "min_width_bp", "min_width_reduce_bp", "k_inv", "k_vol",
            "compound_ratio", "side_mode", "k_trend")
    lkeys = ("stop_loss_bp", "stop_loss_vol_min", "stop_loss_fast_mult",
             "stop_maker_grace_sec", "take_profit_bp",
             "take_profit_maker_grace_sec", "timeout_exit_maker_only",
             "reduce_quote_disabled", "max_one_side_seconds", "min_hold_seconds",
             "max_net_directional_ratio", "max_net_exposure_ratio",
             "vol_pause_sigma", "trend_pause_bp", "ofi_block_threshold")
    blob = json.dumps({"p": {k: params.get(k) for k in keys},
                       "l": {k: limits.get(k) for k in lkeys}},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def collect(days: int) -> dict:
    import psycopg
    hb = heartbeat()
    reg = {}
    try:
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json, updated_at FROM lane_registry"
                            " WHERE lane_id=%s", (LANE,))
                r = cur.fetchone()
                if r:
                    reg = dict(r[0] or {})
                    reg["_updated_at"] = str(r[1])
    except Exception as e:
        reg = {"_error": f"{type(e).__name__}: {str(e)[:80]}"}

    out = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "heartbeat_ok": hb.get("ok"),
        "heartbeat_reason": hb.get("reason"),
        "heartbeat_ts": hb.get("ts"),
        "ticks": hb.get("ticks"),
        "fills": hb.get("fills"),
        "flattens": hb.get("flattens"),
        "equity": hb.get("equity"),
        "param_authority": hb.get("param_authority"),
        "symbols_live": hb.get("symbols"),
        "symbols_registry": reg.get("symbols"),
        "registry_updated_at": reg.get("_updated_at"),
        "params": hb.get("params") or {},
        "limits": hb.get("limits") or {},
    }
    out["param_fp"] = param_fingerprint(out["params"], out["limits"])

    # ── 账本统计（按日 + 累计）──
    try:
        with psycopg.connect(dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT date_trunc('day', ts AT TIME ZONE 'Asia/Shanghai') AS d,
                           count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')) AS mk_n,
                           count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')) AS fl_n,
                           coalesce(sum(net_bp*notional/1e4) FILTER
                             (WHERE meta_json->>'flatten' NOT IN ('true','True')),0) AS mk_usd,
                           coalesce(sum(net_bp*notional/1e4) FILTER
                             (WHERE meta_json->>'flatten' IN ('true','True')),0) AS fl_usd,
                           coalesce(sum(net_bp*notional/1e4),0) AS net_usd,
                           coalesce(sum(notional),0) AS notional
                    FROM lane_ledger
                    WHERE lane_id=%s AND ts > now() - make_interval(days => %s)
                    GROUP BY 1 ORDER BY 1
                """, (LANE, days))
                daily = [{"day": str(d)[:10], "maker_n": int(mk), "flat_n": int(fl),
                          "maker_usd": round(float(a), 4), "flat_usd": round(float(b), 4),
                          "net_usd": round(float(n), 4), "notional": round(float(no), 2)}
                         for d, mk, fl, a, b, n, no in cur.fetchall()]
                out["daily"] = daily

                cur.execute("""
                    SELECT count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),
                           count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')),
                           coalesce(sum(net_bp*notional/1e4) FILTER
                             (WHERE meta_json->>'flatten' NOT IN ('true','True')),0),
                           coalesce(sum(net_bp*notional/1e4) FILTER
                             (WHERE meta_json->>'flatten' IN ('true','True')),0),
                           coalesce(sum(net_bp*notional/1e4),0),
                           count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')
                             AND (meta_json->>'fill_px') IS NOT NULL
                             AND (meta_json->>'mid_px') IS NOT NULL
                             AND (CASE WHEN lower(meta_json->>'side')='buy' THEN 1 ELSE -1 END)
                                 * ((meta_json->>'fill_px')::float - (meta_json->>'mid_px')::float)
                                 / NULLIF((meta_json->>'mid_px')::float,0) * 1e4 > 0)
                    FROM lane_ledger
                    WHERE lane_id=%s AND ts > now() - make_interval(days => %s)
                """, (LANE, days))
                mk, fl, mku, flu, net, paid = cur.fetchone()
                out["window"] = {
                    "days": days, "maker_n": int(mk), "flat_n": int(fl),
                    "maker_usd": round(float(mku), 4), "flat_usd": round(float(flu), 4),
                    "net_usd": round(float(net), 4),
                    "flat_share_pct": round(100.0 * fl / max(mk + fl, 1), 2),
                    "paid_cross_pct": (round(100.0 * paid / fl, 1) if fl else None),
                }
    except Exception as e:
        out["ledger_error"] = f"{type(e).__name__}: {str(e)[:120]}"

    # ── 逐币记分卡 + 出口分解（按天积累，避免从单窗口定论）──
    try:
        out["symbols"] = symbol_scorecard(days)
    except Exception as e:
        out["symbols_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    try:
        out["exit_reasons"] = exit_reason_breakdown(days)
    except Exception as e:
        out["exit_reasons_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def symbol_scorecard(days: int) -> list:
    """逐币记分卡 —— 按天积累，回答"哪个币真的更好"。

    # 为什么必须按天积累，而不是从单窗口定论

    2026-09-22 实测教训（H204）：我观察到 XRP 的 maker 腿加权 `price_bp` 是
    **+0.2092**（其余三个币全负），于是去查"XRP 为什么特殊" ——
    排除了点差宽度、波动、深度之后，结论只能是"**最可能是那 16 小时窗口的噪声**"。

    当天的验证：配置 A 之后 4.5 小时里，**XRP 变成了最差之一（−$15.40）**。
    ⇒ 单窗口的币间差异**不可信**，必须按天累积。

    # 口径（每一项都有明确用途）

      · `maker_n / maker_usd` —— 该币的做市腿产出
      · `wsp / wpx / wnet`     —— 加权 spread / price / net（bp）
        `wpx` 是**逆向选择的直接度量**；`wnet = wsp + wpx + wfee`
      · `flat_n / flat_usd`    —— 强平腿
      · `avg_notl`             —— 笔均名义（对比不同币的腿量是否可比）
      · `flat_rate`            —— 强平占比（结构性风险的指示）
    """
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol,
                       count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')) AS mk_n,
                       coalesce(sum(notional) FILTER
                         (WHERE meta_json->>'flatten' NOT IN ('true','True')), 0) AS mk_notl,
                       coalesce(sum(net_bp*notional/1e4) FILTER
                         (WHERE meta_json->>'flatten' NOT IN ('true','True')), 0) AS mk_usd,
                       coalesce(sum(spread_bp*notional) FILTER
                         (WHERE meta_json->>'flatten' NOT IN ('true','True')), 0) AS mk_wsp,
                       coalesce(sum(price_bp*notional) FILTER
                         (WHERE meta_json->>'flatten' NOT IN ('true','True')), 0) AS mk_wpx,
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')) AS fl_n,
                       coalesce(sum(net_bp*notional/1e4) FILTER
                         (WHERE meta_json->>'flatten' IN ('true','True')), 0) AS fl_usd
                FROM lane_ledger WHERE lane_id=%s
                  AND ts > now() - make_interval(days => %s)
                GROUP BY symbol ORDER BY mk_usd
            """, (LANE, days))
            rows = []
            for (sym, mk_n, mk_notl, mk_usd, mk_wsp, mk_wpx,
                 fl_n, fl_usd) in cur.fetchall():
                mk_n, fl_n = int(mk_n or 0), int(fl_n or 0)
                notl = float(mk_notl or 0)
                w = (lambda v: round(float(v or 0) / notl, 4)) if notl > 0 else (lambda v: None)
                rows.append({
                    "symbol": sym, "maker_n": mk_n, "flat_n": fl_n,
                    "maker_usd": round(float(mk_usd or 0), 4),
                    "flat_usd": round(float(fl_usd or 0), 4),
                    "net_usd": round(float(mk_usd or 0) + float(fl_usd or 0), 4),
                    "wsp": w(mk_wsp), "wpx": w(mk_wpx),
                    "avg_notl": round(notl / mk_n, 1) if mk_n else 0.0,
                    "flat_rate": round(100.0 * fl_n / (mk_n + fl_n), 2) if (mk_n + fl_n) else 0.0,
                })
            return rows


def exit_reason_breakdown(days: int) -> list:
    """按出口原因分解 flatten —— `exit_reason` 落盘（F335）之后才可能。

    这是回答"哪条出口在亏"的**唯一直接口径**。
    在 F335 之前只能从 `price_bp` 的双峰分布反推，
    而反推分不清"止损触发"与"超时强平"（两者修法相反）。
    """
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT coalesce(meta_json->>'exit_reason', '(无/旧记录)') AS rs,
                       count(*) AS n,
                       coalesce(sum(net_bp*notional/1e4), 0) AS usd,
                       avg(price_bp) AS avg_px
                FROM lane_ledger WHERE lane_id=%s
                  AND ts > now() - make_interval(days => %s)
                  AND meta_json->>'flatten' IN ('true','True')
                GROUP BY 1 ORDER BY 2 DESC
            """, (LANE, days))
            return [{"reason": r[0], "n": int(r[1] or 0),
                     "usd": round(float(r[2] or 0), 4),
                     "avg_price_bp": round(float(r[3] or 0), 2) if r[3] is not None else None}
                    for r in cur.fetchall()]


def data_health() -> dict:
    """数据健康：心跳新鲜度 + 行情采集覆盖。断流会让整段样本失效。"""
    import time
    hb = heartbeat()
    now = time.time()
    age = (now - float(hb.get("ts") or 0)) if hb.get("ts") else None
    h = {"heartbeat_age_s": round(age, 1) if age is not None else None}
    try:
        import psycopg
        with psycopg.connect(dsn("alpha_market")) as c:
            with c.cursor() as cur:
                cur.execute("SELECT count(*), max(timestamp) FROM market_orderbook_snapshots"
                            " WHERE exchange='asterdex' AND timestamp >"
                            " (extract(epoch FROM now()-interval '10 minutes')*1000)")
                n, last = cur.fetchone()
                h["book_rows_10m"] = int(n or 0)
                h["book_age_s"] = round((time.time() * 1000 - int(last)) / 1000.0, 1) if last else None
    except Exception as e:
        h["book_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return h


def disk() -> dict:
    import shutil
    try:
        u = shutil.disk_usage(str(ROOT))
        return {"total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1),
                "used_pct": round(100.0 * u.used / u.total, 1)}
    except Exception as e:
        return {"error": str(e)[:60]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-append", action="store_true")
    a = ap.parse_args()

    d = collect(a.days)
    h = data_health()
    dk = disk()
    d["health"] = h
    d["disk"] = dk

    print("=" * 96)
    print(f"H197  长跑摘要   as_of {d['as_of'][:19]}Z")
    print("=" * 96)
    ok = d.get("heartbeat_ok")
    print(f"  车道心跳   ok={ok}   reason={d.get('heartbeat_reason')!r}   "
          f"age={h.get('heartbeat_age_s')}s")
    print(f"  权益       ${d.get('equity')}   ticks={d.get('ticks')}   "
          f"进程内 fills={d.get('fills')} flattens={d.get('flattens')}")
    print(f"  参数权威   {d.get('param_authority')}   指纹 = {d['param_fp']}")
    print(f"  宇宙(实盘) {d.get('symbols_live')}")
    print(f"  宇宙(注册表) {d.get('symbols_registry')}   updated={d.get('registry_updated_at')}")
    print(f"  数据健康   行情行数(10m)={h.get('book_rows_10m')}  age={h.get('book_age_s')}s")
    print(f"  磁盘       剩余 {dk.get('free_gb')}GB / {dk.get('total_gb')}GB "
          f"({dk.get('used_pct')}% 已用)")

    w = d.get("window") or {}
    if w:
        print(f"\n  ── 近 {w['days']} 天账本 ──")
        print(f"    maker 腿 {w['maker_n']:>7}  ${w['maker_usd']:>+10.3f}")
        print(f"    flatten 腿 {w['flat_n']:>5}  ${w['flat_usd']:>+10.3f}"
              f"   （占比 {w['flat_share_pct']}%，穿价率 {w['paid_cross_pct']}%）")
        print(f"    **净额 ${w['net_usd']:>+10.3f}**")

    # ── 出口原因分解（F335 之后才可能；这是"哪条出口在亏"的唯一直接口径）──
    er = d.get("exit_reasons") or []
    if er:
        print(f"\n  ── flatten 按出口原因 ──")
        print(f"    {'出口原因':<26}{'条数':>6}{'净额$':>11}{'avg price_bp':>14}")
        print("    " + "-" * 58)
        for r in er:
            print(f"    {str(r['reason'])[:25]:<26}{r['n']:>6}{r['usd']:>+11.4f}"
                  f"{(r['avg_price_bp'] if r['avg_price_bp'] is not None else 0):>14.2f}")

    # ── 逐币记分卡（按天积累，回答"哪个币真的更好"）──
    sc = d.get("symbols") or []
    if sc:
        print(f"\n  ── 逐币记分卡（近 {w.get('days', '?')} 天）──")
        print(f"    {'币':<9}{'maker腿':>8}{'强平':>6}{'强平率':>8}"
              f"{'笔均名义$':>11}{'加权spread':>11}{'加权price':>11}{'净额$':>10}")
        print("    " + "-" * 76)
        for r in sc:
            wsp = r["wsp"] if r["wsp"] is not None else 0.0
            wpx = r["wpx"] if r["wpx"] is not None else 0.0
            print(f"    {r['symbol']:<9}{r['maker_n']:>8}{r['flat_n']:>6}"
                  f"{r['flat_rate']:>7.1f}%{r['avg_notl']:>11.1f}"
                  f"{wsp:>11.4f}{wpx:>11.4f}{r['net_usd']:>+10.3f}")
        print("    ⚠️ 单日样本下**币间差异不可信**（已实测：XRP 在 16h 窗口是唯一正的，")
        print("       4.5h 后变成最差之一）⇒ 必须按多日累积看趋势，不要挑单窗口的赢家。")

    dl = d.get("daily") or []
    if dl:
        print(f"\n  ── 按日 ──")
        print(f"    {'日期':<12}{'maker':>8}{'flat':>7}{'maker$':>11}{'flat$':>11}"
              f"{'净额$':>11}{'累计净额$':>12}")
        print("    " + "-" * 70)
        cum = 0.0
        for r in dl:
            cum += r["net_usd"]
            print(f"    {r['day']:<12}{r['maker_n']:>8}{r['flat_n']:>7}"
                  f"{r['maker_usd']:>+11.3f}{r['flat_usd']:>+11.3f}"
                  f"{r['net_usd']:>+11.3f}{cum:>+12.3f}")

    # 参数变更检测（与上一条摘要比）
    if DIGEST.exists():
        try:
            lines = DIGEST.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                prev = json.loads(lines[-1])
                if prev.get("param_fp") and prev["param_fp"] != d["param_fp"]:
                    print(f"\n  ⚠️⚠️ **参数已变更**：{prev['param_fp']} → {d['param_fp']}")
                    print(f"      ⇒ 长跑样本被污染，跨越该时点的对比不可用")
                    d["param_changed_from"] = prev["param_fp"]
                if prev.get("symbols_live") and prev["symbols_live"] != d.get("symbols_live"):
                    print(f"\n  ⚠️ **宇宙已变更**：{prev['symbols_live']} → {d.get('symbols_live')}")
                    d["symbols_changed_from"] = prev["symbols_live"]
        except Exception:
            pass

    if not a.no_append:
        DIGEST.parent.mkdir(parents=True, exist_ok=True)
        with DIGEST.open("a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
        print(f"\n  已追加到 {DIGEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
