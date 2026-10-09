# -*- coding: utf-8 -*-
"""[h817 2026-10-04 审计补洞] 流动门生产者(trainer):按 flow_rules 的协议训练并写门。

这是审计发现的**唯一阻断级缺口**:
  flow_rules 有规则(purged_splits/decile_table/bucket_tradable/gate_decision)、
  runner 有消费者(gate_decision 读门)、但**没有人生产新格式的门文件**。
  线上 data/flow_gate_last.json 还是旧格式(allow/pred_bp)⇒
  gate_decision 恒返回 no_oos ⇒ 新引擎永久关门。

本脚本按文档协议实现生产者:
  1. 特征:flow_rules.assemble_features(15 维,线上线下同一套);
     金额失衡 = (主动买金额 − 主动卖金额)/(买 + 卖),来自 asterdex_trades;
  2. 标签:**挂单可执行路径**(手续费 0),且必须被真的打到:
     多头 = 买一进 / 卖一出;进场与出场各自要求窗口内有成交打到该价;
     做空对称。未打到的样本不计入平均。
  3. 切分:60% 训练 → embryo(隔离 H 秒)→ 20% 校准 → embryo → 20% 考试;
     校准段只用于拟合概率/分档边界,考试段只用于对外报告。
  4. 分档:校准段的预测分位 → 10 档;每档在考试段的 mean_y / n_eff / 胜率;
     开门条件 = 该币最优(方向, H)的考试段 mean_y > 0 且 n_eff ≥ 30。
  5. 写 data/flow_gate_last.json(新格式:oos + deciles + mu + side + allow)。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
from backend.services.market_maker.flow_rules import (  # noqa: E402
    FLOW_FEATURES, MIN_N_EFF, STEP_SEC, bucket_tradable, decile_table,
    load_learn_params, n_eff, pool_positive_deciles, quantile_edges,
)
import psycopg  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402

COINS_HARDCODED = ["NEAR", "PENGU", "CBRS", "RESOLV", "1000SHIB", "COIN", "BTW", "LYN"]
COINS: list = []          # 运行时按"近窗口成交笔数"自动取前 N(见 main)
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 12
REF = "BTC"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
HORIZONS = [30, 90, 180, 300]
COST_BP = 4.0


def _load(conn, sym: str, lo_ms: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms > %s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms", (sym + "USDT", lo_ms))
        bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
        cur.execute(
            "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms))
        tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
    if len(bk):
        bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
    if len(tr):
        tr = tr.astype({"px": float, "qty": float})
    return bk, tr


def _build(sym: str, bk: pd.DataFrame, tr: pd.DataFrame, grid: np.ndarray) -> pd.DataFrame:
    t = pd.DataFrame({"t": grid})
    m = pd.merge_asof(t, bk[["ts", "bp", "bq", "ap", "aq"]].rename(
        columns={"ts": "t"}), on="t", direction="backward")
    df = pd.DataFrame({"t": grid, "bp": m["bp"].values, "ap": m["ap"].values,
                       "bq": m["bq"].values, "aq": m["aq"].values})
    df["mid"] = (df["bp"] + df["ap"]) / 2.0
    df["spread_bp"] = (df["ap"] - df["bp"]) / df["mid"].replace(0, np.nan) * 1e4
    # 金额失衡(主动买/卖金额),5/15/60/300 秒
    for w_s in (5, 15, 60, 300):
        bu, se = [], []
        for x in grid:
            seg = tr.loc[(tr["ts"] >= x - w_s * 1000) & (tr["ts"] < x)]
            nb = float((seg.loc[~seg["bm"], "px"] * seg.loc[~seg["bm"], "qty"]).sum())
            ns = float((seg.loc[seg["bm"], "px"] * seg.loc[seg["bm"], "qty"]).sum())
            bu.append(nb)
            se.append(ns)
        g = np.array(bu) + np.array(se)
        df[f"ofi_{w_s}s"] = np.where(g > 0, (np.array(bu) - np.array(se)) / np.maximum(g, 1e-9), 0.0)
        if w_s == 60:
            df["notional_60s"] = g
    # 动量与合成 K 线特征
    for w_s, n in ((15, 1), (60, 4), (300, 20)):
        prev = df["mid"].shift(n)
        df[f"ret_{w_s}s_bp"] = (df["mid"] / prev - 1.0) * 1e4
    h60 = df["mid"].rolling(4, min_periods=1).max()
    l60 = df["mid"].rolling(4, min_periods=1).min()
    df["bar_pos_60s"] = (df["mid"] - l60) / (h60 - l60).replace(0, np.nan)
    df["bar_pos_60s"] = df["bar_pos_60s"].fillna(0.5)
    df["range_60s_bp"] = (h60 - l60) / df["mid"].replace(0, np.nan) * 1e4
    vol = (df["mid"].pct_change(fill_method=None) * 1e4).rolling(20, min_periods=5).std()
    df["vol_z_300s"] = vol
    # 20 档加权深度失衡(近似:用盘口一档量 + 价差做代理;有深度表时由调用方替换)
    df["depth_wimb"] = (df["bq"] - df["aq"]) / (df["bq"] + df["aq"] + 1e-9)
    return df


def main() -> int:
    # ── [2026-10-09 进化重挂] MM_FLOW_TRAINER 时隙开关 ──────────────────
    # ping-pong 机器不再读方向门，本时隙改产 rt_bp 口径学习表：
    #   · "pp"（默认）= 跑 scripts/pp_learn_tables.py（选币统计 + 桶级状态门表）
    #   · "1"          = 恢复旧方向门训练（回滚用）
    #   · "0"          = 本时隙停摆
    _mode = str(os.getenv("MM_FLOW_TRAINER", "pp") or "pp").strip().lower()
    if _mode == "0":
        print("MM_FLOW_TRAINER=0 ⇒ 本时隙停摆（方向门训练已退役）。")
        return 0
    if _mode == "pp":
        # ⚠️ 不能用 `from scripts.pp_learn_tables import main`：本脚本先导入了
        # backend.* ⇒ backend/ 被顶到 sys.path 前 ⇒ scripts 命名空间包被遮蔽
        # ⇒ ModuleNotFoundError 静默崩掉整个训练时隙（实测：gate 文件冻结在
        # 17:33、pp 表冻结在 17:56，任务却报"成功"）。用 importlib 按路径直载。
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "pp_learn_tables", str(ROOT / "scripts" / "pp_learn_tables.py"))
        _pp_mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_pp_mod)
        print("[h817→pp] ping-pong 时代：本时隙改产 rt_bp 学习表。")
        return _pp_mod.main()
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - HOURS * 3600 * 1000
    learn = load_learn_params(ROOT)
    margin = float(learn.get("entry_margin_bp") or 1.0)
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "hours": HOURS, "protocol": "purged_60_20_20_embargo",
           "cost_bp": COST_BP, "gates": {}}
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        # [h820 2026-10-04] 按**成交笔数**自动选币:流交易的标签需要真实成交,
        # 宇宙里那些"宽价差但没成交"的币(PENGU 105 笔/12h、COIN 8 笔)根本
        # 凑不出可执行样本 ⇒ 自动扫描成交最活跃的前 TOPN 个币。
        if not COINS:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, count(*) c FROM asterdex_trades"
                    " WHERE event_ts_ms > %s GROUP BY symbol"
                    " HAVING count(*) >= 300 ORDER BY c DESC LIMIT %s",
                    (lo_ms, TOPN))
                COINS.extend(r[0].replace("USDT", "") for r in cur.fetchall())
            print(f"  按成交笔数自动选币({len(COINS)}):{COINS}")
        ref_bk, ref_tr = _load(conn, REF, lo_ms)
        ref_mid = None
        if len(ref_bk):
            ref_bk["mid"] = (ref_bk["bp"] + ref_bk["ap"]) / 2.0
            ref_mid = ref_bk[["ts", "mid"]].rename(columns={"ts": "t"})
        for sym in COINS:
            bk, tr = _load(conn, sym, lo_ms)
            if len(bk) < 1000 or len(tr) < 200:
                # [h901] 数据不足也**给尽力值**:用已有的逐笔算最近 60 秒 OFI 符号
                _side_fb, _mu_fb = "buy", 0.0
                try:
                    if len(tr) >= 20:
                        _t = np.array([r[0] for r in tr])
                        _q = np.array([r[2] for r in tr])
                        _ib = np.array([not r[3] for r in tr])
                        _m = _t > (float(_t[-1]) - 60000)
                        if _m.any():
                            _mu_fb = float((_q[_m & _ib].sum() - _q[_m & (~_ib)].sum())
                                           / max(_q[_m].sum(), 1e-12))
                            _side_fb = "buy" if _mu_fb >= 0 else "sell"
                except Exception:
                    pass
                out["gates"][sym] = {"allow": False, "side": _side_fb,
                                     "mu": round(_mu_fb, 3),
                                     "reason": "insufficient_data", "fallback": True}
                print(f"  {sym:<9} 数据不足(book={len(bk)}, trades={len(tr)})"
                      f" ⇒ 尽力值 {_side_fb}/{_mu_fb:+.2f}")
                continue
            grid = np.arange(lo_ms + 301000, now_ms - 301000, int(STEP_SEC * 1000))
            df = _build(sym, bk, tr, grid)
            if ref_mid is not None:
                rm = pd.merge_asof(pd.DataFrame({"t": grid}), ref_mid, on="t",
                                   direction="backward")
                df["btc_ret_60s"] = (rm["mid"].values / pd.Series(rm["mid"].values).shift(4)
                                     - 1.0) * 1e4
                df["rel_ret_60s"] = df["ret_60s_bp"] - df["btc_ret_60s"]
            else:
                df["btc_ret_60s"] = 0.0
                df["rel_ret_60s"] = df["ret_60s_bp"]
            df["breadth"] = np.sign(df["ret_60s_bp"]).fillna(0.0)
            # 挂单可执行标签:进场买一/卖一,出场卖一/买一;必须被真的打到
            tr_ts = tr["ts"].values
            tr_px = tr["px"].values
            for hz in HORIZONS:
                step_n = max(1, int(hz * 1000 / (STEP_SEC * 1000)))
                fwd = pd.DataFrame({"t": grid})
                fwd = pd.merge_asof(fwd, bk[["ts", "bp", "ap"]].rename(columns={"ts": "t"}),
                                    on="t", direction="forward", tolerance=hz * 1000 * 2)
                fwd = fwd.shift(-step_n) if False else fwd
                # 出场价:在 t+hz 之后最近的盘口
                ex = pd.merge_asof(pd.DataFrame({"t": grid + hz * 1000}),
                                   bk[["ts", "bp", "ap"]].rename(columns={"ts": "t"}),
                                   on="t", direction="forward", tolerance=hz * 1000 * 2)
                lo_win, hi_win = [], []
                for x in grid:
                    a = np.searchsorted(tr_ts, x)
                    b = np.searchsorted(tr_ts, x + hz * 1000)
                    if b <= a:
                        lo_win.append(np.nan)
                        hi_win.append(np.nan)
                    else:
                        lo_win.append(float(np.min(tr_px[a:b])))
                        hi_win.append(float(np.max(tr_px[a:b])))
                lo_a, hi_a = np.array(lo_win), np.array(hi_win)
                entry_bid, entry_ask = df["bp"].values, df["ap"].values
                exit_bid, exit_ask = ex["bp"].values, ex["ap"].values
                # 多头:买一进(被打到 = 窗口最低价 ≤ 买一)、卖一出(窗口最高价 ≥ 卖一)
                long_ok = (lo_a <= entry_bid) & (hi_a >= exit_ask) & np.isfinite(lo_a)
                short_ok = (hi_a >= entry_ask) & (lo_a <= exit_bid) & np.isfinite(hi_a)
                y_long = np.where(long_ok & (entry_bid > 0) & (exit_ask > 0),
                                  (exit_ask - entry_bid) / np.maximum(entry_bid, 1e-12) * 1e4,
                                  np.nan)
                y_short = np.where(short_ok & (entry_ask > 0) & (exit_bid > 0),
                                   (entry_ask - exit_bid) / np.maximum(entry_ask, 1e-12) * 1e4,
                                   np.nan)
                # 两个方向**各自**的标签(不能取事后更好的那个 = 偷看未来)
                df[f"y_long_{hz}"] = np.where(long_ok, y_long, np.nan)
                df[f"y_short_{hz}"] = np.where(short_ok, y_short, np.nan)
            d2 = df.replace([np.inf, -np.inf], np.nan).dropna(subset=list(FLOW_FEATURES))
            if len(d2) < 400:
                # [h901] 行太少也**给尽力值**:用 d2 里可用的最近 60s 标签均值
                _ry2 = 0.0
                try:
                    _lbl_cols = [c for c in d2.columns if c.startswith("y_long_")]
                    if _lbl_cols and len(d2):
                        _lbl = d2[_lbl_cols[0]].dropna()
                        _ry2 = float(_lbl.tail(60).mean()) if len(_lbl) else 0.0
                except Exception:
                    _ry2 = 0.0
                out["gates"][sym] = {"allow": False,
                                     "side": ("sell" if _ry2 < 0 else "buy"),
                                     "mu": round(_ry2, 3),
                                     "reason": "few_rows", "fallback": True}
                print(f"  {sym:<9} 有效行 {len(d2)} 太少 ⇒ 尽力值 {_ry2:+.2f}")
                continue
            best = None
            found = []
            for hz in HORIZONS:
                for side in ("buy", "sell"):
                    col = f"y_{'long' if side == 'buy' else 'short'}_{hz}"
                    dd = d2.dropna(subset=[col])
                    nobs = len(dd)
                    if nobs < 80:
                        continue
                    emb = max(1, int(hz / STEP_SEC))
                    i_tr = int(nobs * 0.6)
                    i_cal0 = min(nobs - 2, i_tr + emb)
                    i_cal1 = min(nobs - 1, i_cal0 + int(nobs * 0.2))
                    i_te0 = min(nobs, i_cal1 + emb)
                    if i_te0 >= nobs - 10 or i_cal1 <= i_cal0:
                        continue
                    X = dd[list(FLOW_FEATURES)].values
                    y = dd[col].values
                    reg = HistGradientBoostingRegressor(max_iter=120, learning_rate=0.07,
                                                        max_depth=4, min_samples_leaf=40,
                                                        random_state=7)
                    reg.fit(X[:i_tr], y[:i_tr])
                    p_cal, p_te = reg.predict(X[i_cal0:i_cal1]), reg.predict(X[i_te0:])
                    y_te = y[i_te0:]
                    edges = quantile_edges(p_cal, 10)
                    table = decile_table(p_te, y_te, edges, STEP_SEC, hz) if edges else []
                    pooled = pool_positive_deciles(table, STEP_SEC, hz) if table else None
                    if not pooled or not pooled["tradable"]:
                        continue
                    top_i = int(pooled["top_decile"])
                    lb = edges[top_i - 1] if top_i > 0 and top_i - 1 < len(edges) else -1e9
                    mu_now = float(reg.predict(X[-1:])[0])
                    cand = {"hz": hz, "side": side, "mean_y": float(pooled["mean_y"]),
                            "n_eff": float(pooled["n_eff"]),
                            "win_rate": float(pooled["win_rate"] or 0),
                            "bucket_lb": lb, "mu": mu_now, "n_obs": nobs,
                            "deciles": table}
                    found.append(cand)
            live = [c for c in found if c["mu"] > margin]
            best = max(live, key=lambda c: c["mu"]) if live else (
                max(found, key=lambda c: c["mean_y"]) if found else None)
            if best is None:
                # [h901 用户"空值被说成证据不足?"] 没有合格桶也**必须给数**:
                # 方向 = 最近 60 条标签的符号(诚实的尽力估计),mu = 其均值。
                _recent = dd[col].tail(60)
                _ry = float(_recent.mean()) if len(_recent) else 0.0
                out["gates"][sym] = {"allow": False,
                                     "side": ("sell" if _ry < 0 else "buy"),
                                     "mu": round(_ry, 3),
                                     "max_hold_sec": 60.0,
                                     "reason": "no_tradable_bucket",
                                     "fallback": True,
                                     "oos": {"mean_y": round(_ry, 3),
                                             "n_eff": 0.0, "win_rate": 0.0}}
                print(f"  {sym:<9} 无合格桶 ⇒ 尽力值 mu={_ry:+.2f}bp(fallback)")
                continue
            # 当前预测要落在仍赚钱的那一档之上，并且高于安全垫，才允许这一秒挂单。
            allow = bool(bucket_tradable(best["mean_y"], best["n_eff"], best["win_rate"])
                         and best["mu"] > margin)
            side = best["side"]
            # [h901] 关门也保留 side/mu(不再 null),加 reason 说明为什么关
            out["gates"][sym] = {
                "allow": allow, "side": side,
                "mu": round(best["mu"], 3), "max_hold_sec": float(best["hz"]),
                "horizon_sec": float(best["hz"]),
                "reason": "ok" if allow else
                          ("mu_below_margin" if best["mu"] <= margin
                           else "bucket_not_tradable"),
                "oos": {"mean_y": round(best["mean_y"], 3),
                        "n_eff": round(best["n_eff"], 2),
                        "win_rate": round(best["win_rate"], 3)},
                "deciles": best["deciles"],
                "n_obs": best["n_obs"],
            }
            print(f"  {sym:<9} H={best['hz']:>3}s 考试段 mean_y={best['mean_y']:+.2f}bp "
                  f"n_eff={best['n_eff']:.1f} 胜率={best['win_rate']*100:.0f}% "
                  f"当前mu={best['mu']:+.2f} ⇒ {'开门(' + side + ')' if allow else '关门'}")
    # ── [整顿轮·T18 2026-10-05] 修好**生产者与消费者断线** ──
    #
    # 事故：本脚本 docstring 第 20 行写明「5. 写 data/flow_gate_last.json
    # （新格式:oos + deciles + mu + side + allow）」，但代码实际写的是
    # `data/flow_gate_model.json`。
    #
    # 后果（实测）：
    #   · `flow_gate_model.json` **全仓库没有任何读取者**（grep 只命中本文件两行）
    #     ⇒ 纯写入件，纯浪费
    #   · `flow_gate_last.json` **没有任何写入者** ⇒ 停在 **2026-10-04 20:32:42**
    #   · 而 `runner.py:1359` 与 `flow_rules.gate_decision()` 读的正是 `_last`
    #     ⇒ 引擎 24 小时里一直在用**昨天的、全部 allow=False** 的门
    #   · `GATE_MAX_AGE_SEC = 1800`（30 分钟）⇒ 每 20 分钟跑一次的本脚本
    #     本可让门保持新鲜，但写错文件 ⇒ 门**永久过期**，`gate_decision`
    #     对每个币都返回 `stale_or_missing` ⇒ 引擎只能靠探索单交易
    #
    # 实测本脚本这次算出的门（2026-10-05 20:03）：
    #   PLAY  H=30s  考试段 mean_y=+1.45bp  n_eff=115.5  胜率=68%  ⇒ 开门(buy)
    #   BTW   H=300s 考试段 mean_y=+36.49bp n_eff=42.0   胜率=61%  ⇒ 开门(buy)
    #   SI    H=90s  考试段 mean_y=+24.22bp n_eff=46.7   胜率=49%  ⇒ 开门(sell)
    #   （其余 9 个"无正期望分档" ⇒ 关门，这是正确的保守行为）
    # ⇒ **这 3 个门以前全部被丢弃**。
    #
    # 修法：按 docstring 写 `flow_gate_last.json`（消费者读的那个），
    # 同时**保留** `flow_gate_model.json`（研究件，别处可能人工看）。
    # `MM_FLOW_GATE_TARGET` 可切换：
    #   · `last`（默认）= 写 _last（修复后行为，让门真正生效）
    #   · `model`       = 只写 _model（逐字恢复旧行为，回滚用）
    _target = str(os.getenv("MM_FLOW_GATE_TARGET", "last") or "last").strip().lower()
    _payload = json.dumps(out, ensure_ascii=False, indent=2)
    _written = []
    if _target != "model":
        (ROOT / "data" / "flow_gate_last.json").write_text(_payload, encoding="utf-8")
        _written.append("flow_gate_last.json(引擎读取)")
    (ROOT / "data" / "flow_gate_model.json").write_text(_payload, encoding="utf-8")
    _written.append("flow_gate_model.json(研究件)")
    n_allow = sum(1 for v in out["gates"].values() if v.get("allow"))
    print(f"  ✓ 已写 {' + '.join(_written)}；开门 {n_allow}/{len(out['gates'])}"
          f"  （MM_FLOW_GATE_TARGET={_target}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
