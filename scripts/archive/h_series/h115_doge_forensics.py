"""H115：收缩后 DOGE 的逐笔明细 —— 判定「孤儿退出」还是「重新建仓」。

plan_orphan_exit 的设计是一次性全平（单笔 taker），但 H114 看到收缩后
DOGE 出现 **10 笔跨越 0 的周期** ⇒ 与设计不符，必须逐笔查。

用法：
    .venv\\Scripts\\python.exe scripts\\h115_doge_forensics.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
CUTOFF_ISO = "2026-09-21T09:11:00"


def main():
    rows = []
    for line in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("ts") or 0)

    print("=" * 100)
    print("H115  收缩后 DOGE / UNI 逐笔明细")
    print("=" * 100)

    for sym in ("DOGE", "UNI"):
        sub = [r for r in rows if r.get("symbol") == sym]
        pre = [r for r in sub if (r.get("iso") or "") < CUTOFF_ISO]
        post = [r for r in sub if (r.get("iso") or "") >= CUTOFF_ISO]
        print(f"\n{'─'*100}")
        print(f"{sym}：收缩前 {len(pre)} 笔   收缩后 **{len(post)}** 笔")
        print(f"{'─'*100}")
        # 收缩前最后 6 笔（作为上下文）
        print("  收缩前最后 4 笔：")
        for r in pre[-4:]:
            print(f"    {r['iso'][:19]}  {r['side']:<5} qty={r['qty']:>12.4f} "
                  f"px={r['fill_px']:<12.6f} flat={str(r.get('flatten')):<5} "
                  f"fee={r.get('fee_rate')} edge={r.get('edge_bp')}")
        print(f"\n  收缩后全部 {len(post)} 笔：")
        cum = 0.0
        peak = 0.0
        for r in post:
            q = float(r.get("qty") or 0.0)
            cum += q if str(r.get("side")).lower() == "buy" else -q
            peak = max(peak, abs(cum))
            print(f"    {r['iso'][:19]}  {r['side']:<5} qty={q:>12.4f} "
                  f"px={r['fill_px']:<12.6f} 持仓后={cum:>12.4f} "
                  f"flat={str(r.get('flatten')):<5} fee={r.get('fee_rate')} "
                  f"edge={r.get('edge_bp')}")
        # 收缩时刻的净持仓（从全部历史累加）
        net = 0.0
        for r in sub:
            if (r.get("iso") or "") >= CUTOFF_ISO:
                break
            q = float(r.get("qty") or 0.0)
            net += q if str(r.get("side")).lower() == "buy" else -q
        print(f"\n  ⇒ 收缩时刻的 {sym} 净持仓 = {net:+.4f}")
        print(f"     收缩后峰值 |持仓| = {peak:.4f}")

    # 判定
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    print("  plan_orphan_exit 的语义：一次全平（**单笔** taker），然后从运行态删除。")
    print("  若收缩后某币出现 **多笔、且 |持仓| 先增后减** ⇒ 不是孤儿退出，是**在建仓**。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
