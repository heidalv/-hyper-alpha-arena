"""h612 — ③ 的**行为侧**机制读数：`ofi_confirm` 闸门拦截占比（只读；R201）。

为什么需要（而不是只靠 h481 的参数回显）：`h481` 证明的是"**运行态里这个键等于 0.9**" ✓，
但它证明不了"**闸门真的按 0.9 在拦**" ✗。这个仓库栽过 F189（"改了但没生效"）——
参数进了类、代码路径却没消费它。行为侧读数能独立回答这个问题 ✓。

口径（**不依赖任何旧基线** ⇒ 天然免疫 R198/R198b 的"缝隙变更"问题 ✓）：
  worker 心跳 `logs/mm_lane_status.json` 里的 `skip_counts` 是按 skip 原因聚合的计数器
  （`runner.py:1962` 在 confirm 未达阈值时置 `dec.skip = "ofi_confirm"`）。
  ⇒ 取 `ofi_confirm` 的**增量率**（次/分钟）与**占全部 skip 的比例**，两次采样求差。
  ⇒ ③ 把阈值 0.5 → 0.9 后，`ofi_confirm` 拦截率应当**明显上升**（更多 tick 被拦）；
     若纹丝不动 ⇒ 按 F189 处置：**部署没生效**，该试跑作废（与经济学无关）✗。

顺带打印 `trend_only_flat`（趋势闸）与总决策数，便于辨认"拦截变多"是 confirm 还是趋势闸。

用法：python scripts/h612_confirm_gate_share.py [--watch 90]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"
KEYS = ("ofi_confirm_buy", "ofi_confirm_sell", "ofi_confirm",
        "trend_only_flat", "trend_only", "daily_loss", "stale_data")


def sample() -> dict:
    raw = json.loads(STATUS.read_text(encoding="utf-8"))
    ts = float(raw.get("ts") or 0.0)
    return {"ts": ts, "age": time.time() - ts if ts else -1.0, "raw": raw}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0.0,
                    help="第二次采样的等待秒数（0=只采一次）")
    a = ap.parse_args()
    print("=" * 84)
    print("h612 — ofi_confirm 闸门拦截占比（③ 的行为侧机制读数，只读）")
    print("=" * 84)
    s1 = sample()
    print(f"  心跳：{STATUS.name}  年龄 {s1['age']:.1f}s  "
          f"{'✓ 新鲜' if 0 <= s1['age'] <= 90 else '✗ 陈旧'}")
    sk1 = dict(s1["raw"].get("skip_counts") or {})
    q1 = int(s1["raw"].get("quoted_decisions") or 0)
    t1 = int(s1["raw"].get("ticks") or 0)
    tot1 = sum(int(v) for v in sk1.values()) or 0

    def show(tag: str, sk: dict, tot: int, q: int, ticks: int) -> None:
        # ⚠️ 真实键名是 `ofi_confirm_buy` / `ofi_confirm_sell`（`runner.py:2077`
        # `dec.skip = (why_buy or why_sell or "blocked").split("(")[0]`，而 confirm 闸设的是
        # `why_sell = "ofi_confirm_buy(+0.62)"`）——我首版只查 `ofi_confirm` ⇒ 误判成
        # "计数器被遮蔽、闸门从不触发" ✗（R201 自查抓到）。
        oc = int(sk.get("ofi_confirm_buy") or 0) + int(sk.get("ofi_confirm_sell") or 0)
        print(f"\n  [{tag}] ticks={ticks} quoted_decisions={q} skip合计={tot}")
        print(f"    ofi_confirm 合计 = {oc:>6}（buy={int(sk.get('ofi_confirm_buy') or 0)} "
              f"sell={int(sk.get('ofi_confirm_sell') or 0)}）"
              f" ⇒ 占 skip 的 {(oc / tot if tot else float('nan')):.1%}、占决策的 "
              f"{(oc / q if q else float('nan')):.1%}")
        for k in KEYS:
            if k in sk and not k.startswith("ofi_confirm"):
                print(f"    {k:<18} {int(sk[k]):>6}")
        top = sorted(((k, int(v)) for k, v in sk.items()), key=lambda x: -x[1])[:8]
        print("    全部 skip 前 8：" + "  ".join(f"{k}={v}" for k, v in top))

    show("① 采样", sk1, tot1, q1, t1)
    # [R203] **闸门探针**（`GATE_PROBES`，模块级、不被 `dec.skip` 遮蔽、也不被前 12 截断）——
    # 这是 h529（饱和要求闸）判据 C 的读数：它证明"新闸真的有机会执行" ✓
    gp1 = dict(s1["raw"].get("gate_probe_counts") or {})
    print(f"\n  闸门探针（gate_probe_counts）：{gp1 if gp1 else '（键不存在 ⇒ worker 未重启 / 旧代码）'}")
    if "ofi_require_hit" in gp1 or "ofi_require_blocked" in gp1:
        hit = int(gp1.get("ofi_require_hit") or 0)
        blk = int(gp1.get("ofi_require_blocked") or 0)
        print(f"    ofi_require_hit={hit} blocked={blk}"
              f" ⇒ 命中率 {(blk / hit if hit else float('nan')):.1%}")
        print("    （θ=0 时两者都应为 0：闸关闭 ⇒ 不计数 ✓；θ=0.9 部署后应显著 >0）")

    if a.watch > 0:
        print(f"\n  等 {a.watch:.0f}s 再采一次…")
        time.sleep(a.watch)
        s2 = sample()
        sk2 = dict(s2["raw"].get("skip_counts") or {})
        q2 = int(s2["raw"].get("quoted_decisions") or 0)
        t2 = int(s2["raw"].get("ticks") or 0)
        tot2 = sum(int(v) for v in sk2.values()) or 0
        show("② 采样", sk2, tot2, q2, t2)
        dt_min = max(1e-9, (s2["ts"] - s1["ts"]) / 60.0)

        def _oc(sk: dict) -> int:
            return int(sk.get("ofi_confirm_buy") or 0) + int(sk.get("ofi_confirm_sell") or 0)

        d_oc = _oc(sk2) - _oc(sk1)
        d_q = q2 - q1
        d_flat = (int(sk2.get("trend_only_flat") or 0)
                  - int(sk1.get("trend_only_flat") or 0))
        print(f"\n  Δ（{dt_min:.2f} 分钟）：")
        print(f"    ofi_confirm 拦截 {d_oc:+d} ⇒ **{d_oc / dt_min:.1f} 次/分钟**")
        print(f"    决策数 {d_q:+d} ⇒ {d_q / dt_min:.1f}/分钟")
        print(f"    trend_only_flat {d_flat:+d}")
        print(f"    ⇒ confirm 拦截占比（本次增量）= "
              f"{(d_oc / d_q if d_q else float('nan')):.1%}")

    print("\n判读（③ 专用，判定前预注册 ✓）：")
    print("  · **0.5 时代（现在）**的 ofi_confirm 拦截占比如上 ⇒ 记作**参照** ✓；")
    print("  · ③ 部署（0.5→0.9）后应**明显上升**：若占比与参照同量级、而 h481 回显是 0.9")
    print("    ⇒ **按 F189 处置：部署没生效，试跑作废**（与经济学无关）✗；")
    print("  · 若 `trend_only_flat` 同步暴涨 ⇒ 那是**趋势闸/行情**在动，不是 confirm 闸 ✗。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
