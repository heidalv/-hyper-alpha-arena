"""h630 — **量化采集停更对 ② 窗口的影响**（只读；R233）。

背景：2026-09-29 17:26–18:28L 采集器停更（原件死亡 + 替换件 `--seconds 30` 只跑半分钟 ✗），
期间车道没有行情 ⇒ **不出腿** ✗。但 ② 的频率判据用的是**可交易覆盖小时**（`_covered_hours`
基于 `market_trades_aggregated` ✓），而那张表由 **market_data_center** 的另一路源喂
⇒ 停更**没有**被记为"不可交易" ✗ ⇒ 窗口均值被拉低 ⇒ 判定会以
`frequency_below_mandate` **回滚 90s** ✗——而真正原因是**基础设施**，既不是市场、也不是参数 ✓✗。

本脚本把这件事量化成三条数（供文档与决策使用）：
  1. 停更前后各自的腿速（用 `lane_ledger` 分段统计 ✓）；
  2. 停更窗口内**是否真的一条都没出**（验证"数据断 ⇒ 腿断"的因果 ✓）；
  3. 覆盖口径的**盲区证据**：同一时段 `_covered_hours` 仍报约 100% ✓（说明它看不见采集停更 ✗）。

用法：python scripts/h630_outage_impact.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

CUT = "2026-09-29T09:26:00+00:00"      # 本地 17:26L = 采集停更起点
RESUME = "2026-09-29T10:26:00+00:00"   # 本地 18:26L ≈ 数据恢复


def main() -> int:
    print("=" * 92)
    print("h630 — 采集停更对 ② 窗口的影响（只读）")
    print("=" * 92)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        t = dict(meta.get("h463_trial") or {})
        since = t.get("started_at")
        now = dt.datetime.now(dt.timezone.utc).isoformat()

        def legs(a: str, b: str) -> int:
            cur.execute("SELECT count(*) FROM lane_ledger WHERE lane_id=%s"
                        " AND ts > %s::timestamptz AND ts <= %s::timestamptz AND symbol = ANY(%s)",
                        (h.LANE, a, b, syms))
            return int(cur.fetchone()[0] or 0)

        n_all = legs(since, now)
        n_pre = legs(since, CUT)
        n_out = legs(CUT, RESUME)
        n_post = legs(RESUME, now)
        cov, ratio = h._covered_hours(since, now, syms)
        hrs = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
        print(f"\n  窗口 {since} → {now}（{hrs:.2f}h）")
        print(f"  停更前 {n_pre} 腿 / 停更中 **{n_out} 腿** / 停更后 {n_post} 腿（合计 {n_all}）")
        print(f"  ⇒ **停更期间是否真的一条都没出**：{'✓ 是（数据断 ⇒ 腿断，因果清楚）' if n_out == 0 else f'✗ 否（还有 {n_out} 条）'}")
        h_pre = (dt.datetime.fromisoformat(CUT) - dt.datetime.fromisoformat(since)).total_seconds() / 3600
        h_post = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(RESUME)).total_seconds() / 3600
        print(f"  停更前腿速 = {n_pre / h_pre:6.1f}/h（{h_pre:.2f}h）；"
              f"停更后腿速 = {n_post / max(h_post, 0.01):6.1f}/h（{h_post:.2f}h）")
        print(f"\n  **覆盖口径**：`_covered_hours` 报 可交易 {cov:.2f}h（覆盖 {ratio:.1%}）"
              f" ⇒ {'✗ **它看不见这次采集停更**（否则应扣掉约 1.0h）' if ratio > 0.97 else '✓ 已扣除停更'}")
        rate = n_all / cov if cov else 0.0
        print(f"  **判定会看到的腿速** = {n_all}/{cov:.2f}h = **{rate:.1f}/h**"
              f" ⇒ {'✗ 低于 60/h ⇒ 会以 frequency_below_mandate 回滚' if rate < 60 else '✓ 仍在 60/h 之上'}")
    print("\n" + "-" * 92)
    print("  ⚠️ **R234 更正（本脚本初版结论是错的 ✗）**：我最初写「这次频率缺口是采集停更造成的」✗，")
    print("     但同一份输出就否证了它：**停更期间仍有 19 腿** ✓、而**停更前**已经只有 **43.2/h** ✗")
    print("     ⇒ 减速**早于**停更、且腿从未真正停过（车道读的是 `market_orderbook_snapshots`，")
    print("       那条来自 data-center 的另一路源 ✓，停更期间一直新鲜 ✓）。")
    print("  ⇒ **正确归因（`h582` 实算，框架自己的分支输入）**：")
    print("     · **活跃度 = 0.66×**（试跑 1112 笔/h vs 基线 1694）✗ ⇒ **< 0.85**")
    print("       ⇒ 若频率失败，判定会走 **`freq_shortfall_market_explained` ⇒ INCONCLUSIVE** ✓")
    print("       （**不是**回滚 ✓）；波动 0.93× ✓ 正常。")
    print("     · `h602`：趋势闸近 3h **拦 0 次**、成交 303（102/h）⇒ 也不是闸门 ✗。")
    print("     · 近 15 分钟 **68/h** ✓ ⇒ 车道本身正常 ✓。")
    print("  ⇒ 结论：这次频率偏低是**市场变冷**（活跃度 0.66×）✓，与采集停更**无关** ✓、")
    print("     与 90s 参数也无关 ✓ —— 判定若因此判 INCONCLUSIVE，是**正确**的结果 ✓。")
    print("  ⚠️ 仍记一条**口径观察**：`_covered_hours` 依赖 `market_trades_aggregated`（data-center 源），")
    print("     所以**采集器停更它看不见** ✗（本次报 99.6% 覆盖 ✗）。本次因为腿没停、所以无实际影响 ✓；")
    print("     但若**本机采集长时间停更且车道真停摆**，R34 的外部停摆保护会失效 ✗ ⇒ 登记为待办 ✓。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
