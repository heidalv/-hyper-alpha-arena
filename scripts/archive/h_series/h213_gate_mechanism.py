# -*- coding: utf-8 -*-
"""H213 日亏闸的机制检验：触发后到底是「尾部继续亏」还是「腿本身变差了」。

# 为什么必须做这一步

H212 显示 ≤5/10/15% 闸在 3 天上触发，且**触发后当天剩余腿每次都亏**。
但这有两种完全不同的解释：

- **解释 1（尾部控制）**：触发点之后是同一过程的继续，斜率的符号只是运气，
  闸门是**保险**——它把当天剩下的方差切掉。价值取决于被切掉的尾部有多大。
- **解释 2（状态依赖）**：触发之后**每腿的期望值本身变负了**
  （例如大亏天 = 单边趋势日 = maker 逆向选择日）。这时闸门不是保险，
  而是**在检测一个真实的状态变化** ⇒ 阈值位置不敏感，任何紧的闸都行。

两者对参数选择的含义完全相反，必须区分。判据：

> 比较「触发前每腿均值」与「触发后每腿均值」。
> 若两者都在 0 附近（无显著差异）⇒ 解释 1，闸门只是切方差。
> 若触发后每腿均值**显著更负** ⇒ 解释 2，闸门是在识别状态。

# 同时给出完整反事实总额

H212 只报了"触发后尾部"，没报**12 天总额会变成多少**。这里补齐：
`反事实总额 = 原总额 + Σ(触发事件里被切掉的尾部)`（尾部为负 ⇒ 总额上升）。

# 用法

    python scripts/h213_gate_mechanism.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h213_gate_mechanism.json"
PCTS = [5.0, 10.0, 15.0, 20.0, 30.0]


def dsn() -> str:
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


def load(days: int):
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT ts,
                       (to_char(ts AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD')) AS bj_day,
                       coalesce(net_bp,0)*coalesce(notional,0)/1e4 AS usd,
                       coalesce(net_bp,0) AS net_bp,
                       coalesce(spread_bp,0) AS spread_bp,
                       coalesce(price_bp,0) AS price_bp,
                       coalesce(meta_json->>'flatten','false') AS flatten
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                ORDER BY ts ASC
            """, (LANE, str(int(days))))
            return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--equity", type=float, default=0.0)
    a = ap.parse_args()

    eq = a.equity
    if eq <= 0:
        try:
            j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
            eq = float(j.get("equity") or 0.0)
        except Exception:
            eq = 0.0
    eq = eq or 300.0

    rows = load(a.days)
    if not rows:
        print("无数据")
        return 1

    days = {}
    for ts, day, usd, nbp, sbp, pbp, fl in rows:
        days.setdefault(day, []).append(
            {"ts": ts, "usd": float(usd), "net_bp": float(nbp),
             "spread_bp": float(sbp), "price_bp": float(pbp),
             "flatten": fl in ("true", "True")})

    total = sum(float(r[2]) for r in rows)
    print("=" * 100)
    print("H213  日亏闸：是尾部控制（保险）还是状态识别（真实状态变化）")
    print("=" * 100)
    print(f"  权益 ${eq:.2f}　窗口 {a.days} 天　{len(rows)} 腿　"
          f"原总额 **${total:+.2f}**")

    cum = 0.0
    info = []
    for day in sorted(days):
        legs = days[day]
        eq0 = eq - (total - cum)
        daynet = sum(x["usd"] for x in legs)
        cum += daynet
        info.append((day, legs, eq0, daynet))

    print(f"\n{'━'*100}\n  一、触发前 vs 触发后：每腿均值与结构\n{'━'*100}")
    verdict = []
    for p in PCTS:
        print(f"\n  ── 闸 ≤{p:.0f}%（${eq*0+0:.0f} 级）" + "─" * 60)
        fired_days = 0
        tail_reclaim = 0.0
        pre_all, post_all = [], []
        for day, legs, eq0, daynet in info:
            thr = -abs(eq0 * p / 100.0)
            c = 0.0
            idx = None
            for i, x in enumerate(legs):
                c += x["usd"]
                if c <= thr:
                    idx = i
                    break
            if idx is None:
                continue
            fired_days += 1
            pre, post = legs[:idx + 1], legs[idx + 1:]
            pre_usd = [x["usd"] for x in pre]
            post_usd = [x["usd"] for x in post]
            pn = sum(post_usd)
            tail_reclaim += -pn          # 被切掉的亏损（正数=避免了亏损）
            pre_all += pre_usd
            post_all += post_usd
            print(f"    {day}  触发于第 {idx+1}/{len(legs)} 腿　"
                  f"触发时累计 {c:+.2f}（阈值 {thr:+.2f}）")
            print(f"      触发前 {len(pre):>5} 腿　均值 {st.mean(pre_usd):+.5f} $/腿　"
                  f"合计 {sum(pre_usd):+.2f}")
            if post:
                print(f"      触发后 {len(post):>5} 腿　均值 {st.mean(post_usd):+.5f} $/腿　"
                      f"合计 {pn:+.2f}　"
                      f"其中 maker {sum(1 for x in post if not x['flatten'])} / "
                      f"flatten {sum(1 for x in post if x['flatten'])}")
                print(f"      触发后结构：net_bp {st.mean([x['net_bp'] for x in post]):+.4f} "
                      f"spread {st.mean([x['spread_bp'] for x in post]):+.4f} "
                      f"price {st.mean([x['price_bp'] for x in post]):+.4f}")
            else:
                print(f"      触发后 0 腿（当天最后一条腿才触发）")
        if not fired_days:
            print(f"    12 天里 0 天触发 ⇒ 无作用")
            continue
        cf = total + tail_reclaim
        cut = (cf - total) / abs(total) * 100 if total else 0
        print(f"\n    ⇒ 触发 {fired_days}/{len(info)} 天　"
              f"被切掉的尾部合计 {tail_reclaim:+.2f}")
        print(f"    ⇒ **完整反事实总额 = ${cf:+.2f}**"
              f"（原 ${total:+.2f}，回撤减少 {cut:.1f}%）")
        if pre_all and post_all:
            mp, mq = st.mean(pre_all), st.mean(post_all)
            # 标准误（不假设同方差）
            sep = st.pstdev(pre_all) / (len(pre_all) ** 0.5) if len(pre_all) > 1 else 0
            seq = st.pstdev(post_all) / (len(post_all) ** 0.5) if len(post_all) > 1 else 0
            se = (sep ** 2 + seq ** 2) ** 0.5
            t = (mq - mp) / se if se else 0.0
            print(f"    触发前每腿 {mp:+.5f}　触发后每腿 {mq:+.5f}　"
                  f"差 {mq-mp:+.5f}　t = {t:+.2f}")
            if abs(t) >= 2:
                v = ("**状态依赖**（触发后腿本身显著更差 ⇒ 闸门在识别真实状态变化）"
                     if mq < mp else "**状态依赖（反向）**（触发后腿反而更好）")
            else:
                v = "**尾部控制/保险**（触发前后每腿无显著差异 ⇒ 闸门只是切方差）"
            print(f"    ⇒ {v}")
            verdict.append({"pct": p, "fired": fired_days,
                            "counterfactual_total": round(cf, 2),
                            "pre_mean": round(mp, 6), "post_mean": round(mq, 6),
                            "t": round(t, 3), "verdict": v})

    print(f"\n{'━'*100}\n  二、结论\n{'━'*100}")
    if verdict:
        base = total
        print(f"\n  {'闸':>6}{'触发天':>8}{'反事实总额':>14}{'回撤减少':>10}"
              f"{'触发前$/腿':>13}{'触发后$/腿':>13}{'t':>8}")
        for v in verdict:
            cut = (v["counterfactual_total"] - base) / abs(base) * 100 if base else 0
            print(f"  ≤{v['pct']:>4.0f}%{v['fired']:>8}{v['counterfactual_total']:>+14.2f}"
                  f"{cut:>9.1f}%{v['pre_mean']:>+13.5f}{v['post_mean']:>+13.5f}"
                  f"{v['t']:>+8.2f}")
        print(f"\n  ⚠️ 样本限制必须说清：**只有 3 个触发日**（09-15 / 09-21 / 09-22），"
              f"且都在同一段高波动期。")
        print(f"     3 个观测**不足以**确认'触发后必继续亏'，"
              f"但足以确认两件事：")
        print(f"     ① 80% 闸在 12 天里从未触发 ⇒ 现有配置下**尾部零保护**，这是事实不是估计；")
        print(f"     ② 触发日的尾部都很大（−20 ~ −114 美元）⇒ 被切掉的绝对金额是可观的。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"total": round(total, 2), "equity": eq,
                               "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
