# -*- coding: utf-8 -*-
"""[h809 2026-10-04 用户"全面执行 + 算法整合"] 模型门(flow gate):把证据接进引擎。

整合逻辑(h808 的样本外证据驱动):
  · 每 15 分钟:为每币用近 N 小时数据重训模型(walk-forward 样本内拟合),
    预测**当前时刻**的 90s 期望漂移 pred_bp;
  · 门规则:pred_bp ≥ 成本(6bp)⇒ allow=True(可进场);否则 allow=False(不进场,
    只允许既有仓按 SL/TP/翻转/硬顶离场);
  · 输出 data/flow_gate_last.json,runner 的 active_flow 分支读取它 ⇒
    **没有证据就不交易**(而不是用符号启发式乱打)。

依据:h807/h808 的 walk-forward 样本外结论 —— 现分辨率下 6 币的
90s 期望漂移从未达到 6bp 成本,启发式进场 = 噪音交易 = 全部亏损。
"""
from __future__ import annotations

import io
import json
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
import psycopg  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402

COINS = ["NEAR", "PENGU", "CBRS", "RESOLV", "1000SHIB", "COIN", "BTW", "LYN"]
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
STEP_MS = 15000
HORIZON_MS = 90000
COST_BP = 6.0
# [h812 2026-10-04] 稳健验证过的"高波趋势流"币:模型在 900s 时限上有稳健 edge
# (BTW 中位 +60bp 6/6、LYN +18bp 6/6,见 data/flow_edge_robust.json)。
# 它们走 900s 时限的模型与持仓;其余币仍走 90s。
TREND_COINS = {"BTW": 900.0, "LYN": 900.0}


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - HOURS * 3600 * 1000
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "hours": HOURS, "cost_bp": COST_BP, "gates": {}}
    with psycopg.connect(_market_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM asterdex_book_ticker"
            " WHERE symbol='BTCUSDT' AND event_ts_ms > %s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms", (lo_ms,))
        ref = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
        ref = ref.astype({"bp": float, "ap": float})
        ref["mid"] = (ref["bp"] + ref["ap"]) / 2.0
        for sym in COINS:
            _hz_s = float(TREND_COINS.get(sym, HORIZON_MS / 1000.0))
            _hz_ms = int(_hz_s * 1000)
            try:
                cur.execute(
                    "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty FROM"
                    " asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s"
                    " AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                    (sym + "USDT", lo_ms))
                bk = pd.DataFrame(cur.fetchall(), columns=["ts", "bp", "bq", "ap", "aq"])
                if len(bk) < 500:
                    out["gates"][sym] = {"allow": False, "reason": "no_data"}
                    continue
                bk = bk.astype({"bp": float, "bq": float, "ap": float, "aq": float})
                bk["mid"] = (bk["bp"] + bk["ap"]) / 2.0
                cur.execute(
                    "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
                    " WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms %% 15000 < 400"
                    " ORDER BY event_ts_ms", (sym + "USDT", lo_ms))
                drows = []
                for ts, bids, asks in cur.fetchall():
                    bq = sum(_f(l[1]) for l in (bids or []))
                    aq = sum(_f(l[1]) for l in (asks or []))
                    b1 = _f((bids or [["0", "0"]])[0][1])
                    a1 = _f((asks or [["0", "0"]])[0][1])
                    drows.append({"ts": int(ts), "dbid": bq, "dask": aq, "b1": b1, "a1": a1})
                dp = pd.DataFrame(drows)
                grid = np.arange(lo_ms + 300000, now_ms - STEP_MS, STEP_MS)
                t = pd.DataFrame({"t": grid})
                m = pd.merge_asof(t, bk[["ts", "mid", "bp", "ap"]].rename(
                    columns={"ts": "t"}), on="t", direction="backward")
                mref = pd.merge_asof(t, ref[["ts", "mid"]].rename(
                    columns={"ts": "t", "mid": "ref_mid"}), on="t", direction="backward")
                dd = (pd.merge_asof(t, dp.rename(columns={"ts": "t"}), on="t",
                                    direction="backward", tolerance=60000)
                      if len(dp) else pd.DataFrame(index=range(len(t))))
                df = pd.DataFrame({"t": grid, "mid": m["mid"].values,
                                   "ref_mid": mref["ref_mid"].values})
                df["spread_bp"] = ((m["ap"].values - m["bp"].values)
                                   / df["mid"].replace(0, np.nan) * 1e4)
                if len(dd):
                    df["dbid"] = dd["dbid"].values
                    df["dask"] = dd["dask"].values
                    df["b1"] = dd["b1"].values
                    df["a1"] = dd["a1"].values
                    df["dep_imb"] = (df["dbid"] - df["dask"]) / (df["dbid"] + df["dask"] + 1e-9)
                    df["dep_imb_chg"] = df["dep_imb"].diff()
                    df["top_imb"] = (df["b1"] - df["a1"]) / (df["b1"] + df["a1"] + 1e-9)
                    df["ofi_depth"] = df["b1"].diff() - df["a1"].diff()
                else:
                    for c in ("dep_imb", "dep_imb_chg", "top_imb", "ofi_depth"):
                        df[c] = 0.0
                df["rel_mid_bp"] = (df["mid"] / df["ref_mid"] - 1.0) * 1e4
                df["ret_60s_bp"] = df["mid"].pct_change(4, fill_method=None) * 1e4
                df["ret_300s_bp"] = df["mid"].pct_change(20, fill_method=None) * 1e4
                df["vol_300s"] = (df["mid"].pct_change(fill_method=None) * 1e4).rolling(
                    20, min_periods=5).std()
                cur.execute(
                    "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                    " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms",
                    (sym + "USDT", lo_ms))
                tr = pd.DataFrame(cur.fetchall(), columns=["ts", "px", "qty", "bm"])
                if len(tr):
                    tr = tr.astype({"px": float, "qty": float})
                    tr["signed"] = np.where(tr["bm"], -tr["qty"] * tr["px"],
                                            tr["qty"] * tr["px"])
                    sgn = tr.set_index("ts")["signed"]
                    df["ofi_60s"] = [float(sgn[(sgn.index >= x - 60000)
                                               & (sgn.index < x)].sum()) for x in grid]
                    g = float(tr["px"].mul(tr["qty"]).sum())
                    df["ofi_60n"] = df["ofi_60s"] / (g / max(1, HOURS * 240) + 1e-9)
                else:
                    df["ofi_60s"] = df["ofi_60n"] = 0.0
                feats = ["rel_mid_bp", "ret_60s_bp", "ret_300s_bp", "dep_imb",
                         "dep_imb_chg", "top_imb", "ofi_depth", "ofi_60s", "ofi_60n",
                         "spread_bp", "vol_300s"]
                df = df.replace([np.inf, -np.inf], np.nan)
                df["target_bp"] = (df["mid"].shift(-int(_hz_ms / STEP_MS))
                                   - df["mid"]) / df["mid"] * 1e4
                train = df.dropna(subset=feats + ["target_bp"])
                live = df[feats].iloc[[-2]].fillna(0.0) if len(df) >= 2 else None
                if len(train) < 300 or live is None:
                    out["gates"][sym] = {"allow": False, "reason": "insufficient"}
                    continue
                reg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.07,
                                                    max_depth=4, min_samples_leaf=40,
                                                    random_state=7)
                reg.fit(train[feats], train["target_bp"])
                pred = float(reg.predict(live)[0])
                # 这里的 pred 是中间价漂移，不是「挂单成交之后还赚不赚」。
                # 做多挂在买一，要成交必须有人往下卖；做空挂在卖一，要成交必须有人往上买。
                # 用漂移的符号去挂这一边，成交条件刚好和预测相反，进场腿会先亏。
                # 所以这份结果只留作研究，不允许它打开进场。
                out["gates"][sym] = {
                    "allow": False,
                    "side": None,
                    "pred_bp": round(pred, 2),
                    "n_train": int(len(train)), "horizon_sec": _hz_s,
                    "max_hold_sec": _hz_s,
                    "reason": "mid_drift_not_entry_prob",
                }
                print(f"  {sym:<9} pred_{_hz_s:.0f}s={pred:+7.2f}bp "
                      f"只记录、不开门(中间价漂移不是进场概率)")
            except Exception as e:  # noqa: BLE001
                out["gates"][sym] = {"allow": False, "reason": f"err:{str(e)[:40]}"}
                print(f"  {sym:<9} 失败: {str(e)[:70]}")
    # [h821 2026-10-04 审计后续] **不再写生产门文件**:新协议门的唯一生产者是
    # scripts/h817_flow_trainer.py(挂单可执行标签 + 60/20/20 三段)。
    # 本脚本只作研究记录(mid 漂移口径),写 data/flow_gate_research.json ——
    # 否则两个任务互相覆盖:即使 h817 找到正期望档位,h809 也会在下一个 15 分钟
    # 用 allow=false 把它盖掉 ⇒ 门永远开不了。
    (ROOT / "data" / "flow_gate_research.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n_allow = sum(1 for v in out["gates"].values() if v.get("allow"))
    print(f"  ✓ 已写 data/flow_gate_research.json(研究件;开门 {n_allow}/{len(out['gates'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
