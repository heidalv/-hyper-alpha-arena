"""h480：h463 采纳的**决定性运行态验证**（15s 采样 + 直接观察"加仓侧被撤"）。

为什么换方法：
  `h479` 用 60s 采样去夹逼 `_effective_hold_sec` 的阈值，但 tick=15s、采样=60s
  ⇒ 夹逼宽度 ≥15s，**分不清 45 与 90**（下界 46.9s 与"阈值 45 且采样恰在 tick 前"
  完全一致）。必须直接观察**阈值触发后的行为**，而不是反推时刻。

本脚本观察的行为（runner.py:1289-1333、1599-1608）：
  持仓年龄 > `_effective_hold_sec()` ⇒
    · **封锁加仓侧**（多头封买、空头封卖）——`_tmo_add_block`；
    · 置 `state.timeout_exit_blocked += 1`（每 tick）。
  运行态 `state_json` 里 `quote_bid` / `quote_ask` 是**当前挂单价**（0 = 该侧无挂单）
  ⇒ 对**无形态标记**的仓位，观察"加仓侧是否在 age≈45–90s 之间仍然存在"：
    · 若阈值=90：age 60~85s 的多头**仍有 quote_bid>0**，到 ~90s 后才变 0；
    · 若阈值=45：age 60~85s 的多头**已经** quote_bid==0。

采样 15s（= 一个 tick），持续 `--minutes` 分钟，逐样本记录
(sym, qty符号, age, tag, quote_bid, quote_ask, teb)，结束后按 tag×age 桶统计
"加仓侧存在率"。

用法：python scripts/h480_side_block_probe.py --minutes 24
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h480_side_block.jsonl"
SUM = ROOT / "research_l1" / "out" / "h480_side_block_summary.json"


def read_env_dsn() -> str:
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
    return url


def grab(dsn: str) -> list:
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT symbol, state_json, updated_ts FROM lane_runtime_state "
                        "WHERE lane_id=%s", (LANE,))
            return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=24.0)
    ap.add_argument("--period", type=float, default=15.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    n = max(1, int(a.minutes * 60 / a.period))
    print(f"采样 {n} 次 × {a.period:.0f}s ≈ {a.minutes:.0f} min", flush=True)
    for i in range(n):
        t0 = time.time()
        try:
            rows = grab(dsn)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}] 读失败: {exc}", flush=True)
            time.sleep(a.period)
            continue
        rec = {"ts": round(t0, 3), "iso": time.strftime("%H:%M:%S"), "s": {}}
        for sym, st, upd in rows:
            st = st or {}
            qty = float(st.get("qty") or 0.0)
            opened = float(st.get("opened_ts") or 0.0)
            in_pos = abs(qty) > 1e-12
            rec["s"][str(sym)] = {
                "long": in_pos and qty > 0,
                "in_pos": in_pos,
                "age": round(t0 - opened, 1) if (in_pos and opened > 0) else 0.0,
                "tag": str(st.get("pattern_tag") or ""),
                "bid": round(float(st.get("quote_bid") or 0.0), 8),
                "ask": round(float(st.get("quote_ask") or 0.0), 8),
                "teb": int(st.get("timeout_exit_blocked") or 0),
            }
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if i % 8 == 0:
            print(f"[{i}/{n}] {rec['iso']}", flush=True)
        dt_ = a.period - (time.time() - t0)
        if dt_ > 0 and i < n - 1:
            time.sleep(dt_)

    # ── 汇总：无标记仓位的"加仓侧存在率" vs 年龄桶 ──
    recs = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    buckets = [(0, 30), (30, 45), (45, 60), (60, 75), (75, 90), (90, 120), (120, 300)]
    stat: dict = {}
    for r in recs:
        for sym, v in r["s"].items():
            if not v["in_pos"] or v["age"] <= 0:
                continue
            add_side = v["bid"] if v["long"] else v["ask"]
            key = (v["tag"] or "无标记")
            for lo, hi in buckets:
                if lo <= v["age"] < hi:
                    d = stat.setdefault((key, lo, hi), [0, 0])
                    d[0] += 1
                    d[1] += 1 if add_side > 0 else 0
                    break
    print("=" * 92)
    print(f"{'tag':>8s} {'年龄桶':>10s} {'样本':>6s} {'加仓侧存在':>10s} {'存在率':>8s}")
    for (key, lo, hi), (n_, ok) in sorted(stat.items(),
                                          key=lambda kv: (kv[0][0], kv[0][1])):
        print(f"{key:>8s} {f'{lo}-{hi}':>10s} {n_:6d} {ok:10d} "
              f"{100.0*ok/max(n_,1):7.1f}%")
    print("=" * 92)
    # ── 判读（含**本方法自身有效性**的检查）──────────────────────────────────
    # 教训：本探针第一版直接把"无标记仓位在 60–90s 加仓侧缺失"判成"h463 未生效"，
    # 但数据显示**无标记仓位在 0–30s 就已经没有加仓侧**（0/6）⇒ 加仓侧的缺失
    # 被 trend_* / ofi_toxic / model_below_thr 等**别的闸门**主导，
    # 根本不能归因于超期闸 ⇒ 该指标**不可判读**，不能用来证明或否证 h463。
    early_u = [v for k, v in stat.items() if k[0] == "无标记" and k[1] < 30]
    n_eu = sum(v[0] for v in early_u)
    ok_eu = sum(v[1] for v in early_u)
    early_p45 = [v for k, v in stat.items() if k[0] == "P45" and k[1] < 30]
    n_ep = sum(v[0] for v in early_p45)
    ok_ep = sum(v[1] for v in early_p45)
    mid = [v for k, v in stat.items() if k[0] == "无标记" and 60 <= k[1] < 90]
    n_mid = sum(v[0] for v in mid)
    ok_mid = sum(v[1] for v in mid)
    if n_eu >= 3 and ok_eu / n_eu < 0.35:
        verdict = (f"**不可判读（方法无效）**：无标记仓位在 0–30s 的加仓侧存在率只有 "
                   f"{100.0*ok_eu/n_eu:.0f}%（n={n_eu}）⇒ 加仓侧缺失由**其它闸门**"
                   f"（trend/ofi/model）主导，无法归因于超期闸。 "
                   f"对照 P45 仓位 0–30s 存在率 {100.0*ok_ep/max(n_ep,1):.0f}% "
                   f"(n={n_ep}) ⇒ 口径本身有区分度，但**无标记子集**在构造上就是"
                   f"『被其它闸门压住』的仓位。")
    elif n_mid >= 5:
        rate_mid = ok_mid / n_mid
        verdict = ((f"⇒ h463 已生效：无标记仓位 60–90s 仍有加仓侧 "
                    f"({100*rate_mid:.0f}%, n={n_mid})") if rate_mid > 0.5 else
                   (f"⇒ h463 未生效：无标记仓位 60–90s 加仓侧已撤 "
                    f"({100*rate_mid:.0f}%, n={n_mid})"))
    else:
        verdict = "样本不足"
    print(verdict)
    print("★ 副产品洞见：**无标记（pattern_tag=''）仓位本身就是『加仓侧被其它闸门压住』"
          "的那一群** ⇒ h463 把加仓窗口 45→90s 对它们的**实际作用被高估**："
          "h478 量到的 24.2%（无标记∩age>45s 的在仓时间占比）只是**上界**，"
          "真正被 h463 放开的只是其中『当时加仓侧没被别的闸门压住』的那部分。")
    SUM.write_text(json.dumps(
        {"samples": len(recs), "verdict": verdict,
         "table": {f"{k[0]}|{k[1]}-{k[2]}": {"n": v[0], "add_side_present": v[1]}
                   for k, v in stat.items()}}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", SUM.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
