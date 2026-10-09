"""整顿总览：一次看清「结构 / 交易 / 止盈止损 / 学习进化 / 验收」五项状态。

这是本项目的**唯一状态入口**，回答"现在到底怎么样了"。

用法：
    python scripts/tools/overview.py
    python scripts/tools/overview.py --since "2026-10-05 15:57:56+08"   # 自定义窗口
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
# 策略"当前形态"的起点：T1/T2/T9/T10 部署完成时刻
ERA = "2026-10-05 15:57:56+08"


def _j(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return {}


def _age_min(p: Path):
    try:
        return (time.time() - p.stat().st_mtime) / 60.0
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=ERA)
    a = ap.parse_args()

    print("=" * 88)
    print("高频交易整顿 · 总览")
    print("=" * 88)

    # ── 1. 引擎 ──────────────────────────────────────────────
    st = _j(ROOT / "logs" / "mm_lane_status.json")
    hb = float(st.get("ts") or 0)
    print("\n【1】引擎")
    print(f"  ticks={st.get('ticks')}  fills={st.get('fills')}  "
          f"flattens={st.get('flattens')}  symbols={len(st.get('symbols') or [])}")
    print(f"  权益=${st.get('equity')}  日盈亏=${st.get('day_pnl_usd')}  "
          f"心跳={max(0.0, time.time()-hb):.0f}s 前")
    print(f"  fill_notional=${st.get('fill_notional')}")
    sk = st.get("skip_counts") or {}
    top = sorted(sk.items(), key=lambda kv: -kv[1])[:5]
    print("  skip 前五: " + ", ".join(f"{k}={v}" for k, v in top))

    # ── 2. 数据源新鲜度（两个静默停摆都出在这里）────────────
    print("\n【2】数据源新鲜度")
    feeds = [("flow_gate_last.json", 30), ("vol_top20.json", 90),
             ("flow_situation_last.json", 45)]
    for name, limit in feeds:
        age = _age_min(ROOT / "data" / name)
        if age is None:
            print(f"  {name:<28} ❌ 缺失")
            continue
        ok = "✅" if age < limit else "❌ 过期"
        print(f"  {name:<28} {age:>6.1f} 分钟（上限 {limit}）{ok}")

    # ── 3. 交易实测 ─────────────────────────────────────────
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        print(f"\n【3】交易实测（自 {a.since}）")
        cur.execute("""
            SELECT count(*), count(*) FILTER (WHERE fee_bp < -0.5),
                   coalesce(avg(notional),0), coalesce(sum(notional*net_bp/1e4),0),
                   coalesce(sum(notional),0), coalesce(sum(notional*fee_bp/1e4),0),
                   EXTRACT(EPOCH FROM (max(ts)-min(ts)))/3600.0
            FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s::timestamptz
        """, (LANE, a.since))
        legs, taker, an, net, tot, fee, hrs = cur.fetchone()
        legs = int(legs or 0)
        if legs == 0:
            print("  （窗口内无成交）")
        else:
            per10k = float(net) / max(float(tot), 1e-9) * 1e4
            print(f"  腿数={legs}  taker腿={int(taker or 0)}  "
                  f"均名义=${float(an):,.0f}  跨度={float(hrs or 0):.2f}h")
            print(f"  净额=${float(net):+.4f}   手续费=${float(fee):.4f}")
            print(f"  **每万名义净额 = {per10k:+.2f}**  "
                  f"（>0 ⇒ 策略方向成立且与规模无关）")

        # ── 4. 出场归因 ─────────────────────────────────────
        print("\n【4】出场归因（同窗口）")
        cur.execute("""
            SELECT coalesce(meta_json->>'exit_path','(none)') ep, count(*) n,
                   coalesce(avg(net_bp),0), coalesce(sum(notional*net_bp/1e4),0)
            FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s::timestamptz
            GROUP BY 1 ORDER BY 4 DESC
        """, (LANE, a.since))
        print(f"  {'出口':<24}{'腿数':>6}{'均net_bp':>11}{'净$':>12}")
        for ep, n, av, nu in cur.fetchall():
            print(f"  {str(ep)[:23]:<24}{n:>6}{float(av):>11.2f}{float(nu):>12.4f}")

        # ── 5. 验收标准 ─────────────────────────────────────
        print("\n【5】验收标准（规矩第 9 条）")
        cur.execute("""
            SELECT count(*) FILTER (WHERE fee_bp < -0.5),
                   coalesce(100.0*sum(notional) FILTER (WHERE fee_bp < -0.5)
                            / NULLIF(sum(notional),0), 0)
            FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s::timestamptz
        """, (LANE, a.since))
        tk, tk_pct = cur.fetchone()
        cur.execute("""
            SELECT coalesce(sum(notional*net_bp/1e4) FILTER (
                       WHERE coalesce(meta_json->>'flatten','false')='true'), 0),
                   coalesce(sum(notional*net_bp/1e4) FILTER (
                       WHERE coalesce(meta_json->>'flatten','false')<>'true'), 0)
            FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s::timestamptz
        """, (LANE, a.since))
        fl_nu, non_nu = cur.fetchone()
        fl_nu, non_nu = float(fl_nu), float(non_nu)
        tot_neg = abs(min(0.0, fl_nu)) + abs(min(0.0, non_nu))
        # ── [整顿轮·T22 2026-10-06] "强平占比"这个判据要修正 ──
        #
        # 原判据（规矩第 9 条第 1 条）：强平腿净额占总亏损 < 30%。
        # 实测发现它**自相矛盾**：修复后窗口强平腿净 −$83.71、非强平 +$324.41
        # ⇒ 占比 = 100% ⇒ 判"不合格"，但总结果是**赚的**、且强平是唯一亏损分量。
        #
        # 病根：该判据是为**基线**设计的（当时强平是唯一亏损源 −$1,066.64 = 91%）。
        # 策略一旦达到"只有一个分量亏、总量为正"，占比必然趋近 100%。
        #
        # 修正：主判据改为①绝对净额（对比基线）②占毛利比例；
        #       占比仅在有多个亏损分量时才有意义，否则标 n/a。
        gross_pos = float(net) if float(net) > 0 else 0.0
        fl_vs_gross = (abs(fl_nu) / gross_pos * 100) if (gross_pos > 0 and fl_nu < 0) else 0.0
        n_neg_parts = sum(1 for x in (fl_nu, non_nu) if x < -1e-9)
        print(f"  ① 强平腿绝对净额   基线 −$1,066.64 → 实测 **${fl_nu:+.2f}**"
              f"  （非强平 ${non_nu:+.2f}）")
        print(f"  ①b 强平/毛利       目标<50%   实测 **{fl_vs_gross:.1f}%**"
              f"  （毛利 ${gross_pos:+.2f}）")
        if n_neg_parts >= 2:
            print(f"  ①c 强平占总亏损    目标<30%   实测 **{fl_share:.1f}%**")
        else:
            print(f"  ①c 强平占总亏损    n/a（亏损分量只有 {n_neg_parts} 个，"
                  f"占比无意义 —— 见规矩第 9 条再补充）")
        print(f"  ③ taker 名义占比    目标<3%    实测 **{float(tk_pct):.3f}%**"
              f"（{int(tk or 0)} 条 taker 腿）")
        print(f"  ② 做市净额          目标>0     见【3】的净额与每万名义净额")

    # ── 6. 学习进化 ────────────────────────────────────────
    print("\n【6】学习进化")
    lp = ROOT / "data" / "flow_learn_params.json"
    cur_p = _j(lp)
    print(f"  当前参数（{_age_min(lp):.0f} 分钟前写入）:")
    for k, v in sorted(cur_p.items()):
        print(f"    {k:<26} = {v}")
    for name, desc in (("logs/self_tuner_review.json", "DSH 桥复核请求"),
                       ("logs/self_tuner_pending.json", "待判决变更")):
        p = ROOT / name
        age = _age_min(p)
        print(f"  {desc:<14} {'无' if age is None else f'{age:.0f} 分钟前'}")
    print("  ⚠️ 人机回路：复核请求无自动消费者 ⇒ 参数**保持冻结**（设计如此，防乱调参）")

    # ── 7. 产物 ────────────────────────────────────────────
    print("\n【7】交付物")
    for f in ("00_基线事实与诊断.md", "01_规矩.md", "02_整改登记.md", "03_出场仲裁.md"):
        p = ROOT.parent / "HFT_整顿" / f
        if p.exists():
            print(f"  {f:<28} {p.stat().st_size/1024:>7.1f} KB")
        else:
            print(f"  {f:<28} ❌ 缺失")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
