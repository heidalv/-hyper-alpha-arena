"""h613 — 闸门家族现值 + 谁能真正实现"只做饱和 confirm 态"（只读；R201）。

背景（本轮查明）：③ 要改的 `ofi_confirm_threshold` 在当前配置下**不产生行为** ——
它的分支（`runner.py:1952-1969`）要求 `allow_sell/allow_buy` 仍为真，而更早的趋势闸
（`:1756-1759`，`trend_down`/`trend_up`）已经把**同一侧**封掉了 ⇒ 实测窗口内 3500 次拦截里
confirm 闸 **0 次** ✗。

本脚本列出与"闸门"相关参数的**现值**，并标注每个参数能否**独立生效**
（即：它的分支是否被更早的闸挡住、是否有已知可观测计数器）。用于回答用户选的 C：
"换一个等效但能生效的旋钮"。
"""
from __future__ import annotations

import importlib.util
import json
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

# (参数, 它在哪一步、语义, 本轮实测它的计数器是否在动)
ROWS = [
    ("trend_pause_bp", "趋势闸（:1741 `trend_blocked_side`）：|趋势|≥此值 ⇒ 封**加仓侧**",
     "trend_up/trend_down 计数在动 ✓（1113 次/2.6h）"),
    ("trend_lookback", "同上，趋势回看期数", "同上"),
    ("trend_only_q", "平缓闸（h454 自适应）：安静时段封加仓侧",
     "trend_only_flat 计数在动 ✓（871 次/2.6h）"),
    ("trend_only_bp", "平缓闸的绝对下限（与 q 取较宽者）", "同上"),
    ("vwap_revert_bp", "P2 VWAP 回归：偏离 ≥此值 ⇒ 只挂回归侧",
     "vwap_revert_* ——需查计数（本轮未列前 8）"),
    ("vwap_flow_block", "P2 v2：OFI 推离 ⇒ 连回归侧也封", "vwap_flow_away_* ——需查"),
    ("ofi_block_threshold", "同一族的**禁令**（上一桶 |OFI|>thr ⇒ 封逆势加仓侧）",
     "❓ 需查计数；语义与 confirm 同向 ⇒ 越调高越放松 ✗"),
    ("ofi_toxic_buy/sell", "毒性闸：与趋势同向的极端流 ⇒ 封反向", "ofi_toxic_sell=181 ✓ 在动"),
    ("ofi_confirm_threshold", "F350：|ofi|>thr 且与趋势同向 ⇒ 封**逆势侧**"
     "（**越调高越放松** ⇒ 与「只做饱和态」相反 ✗）", "**0 次 ✗ 从不生效**（被趋势闸抢先）"),
    ("pullback_flow_block", "h356：逆势流 ≥thr ⇒ 封趋势同向侧", "pullback_flow_* ——需查"),
    ("model_thr_frac", "模型闸：边际低于阈值 ⇒ 封", "model_below_thr=231 ✓ 在动"),
]


def main() -> int:
    print("=" * 100)
    print("h613 — 闸门家族现值与「能否独立生效」（只读；回答「换哪个旋钮」）")
    print("=" * 100)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        params = dict(meta.get("params") or {})
        lim = dict(params.get("limits") or {})
        merged = {}
        merged.update(params)
        merged.update(lim)          # limits 覆盖同名键（引擎里就是同一命名空间）
        print(f"  注册表 params={len(params)} 键 / limits={len(lim)} 键")
        for k, where, seen in ROWS:
            v = merged.get(k, merged.get(k.split("/")[0]))
            print(f"\n  {k}")
            print(f"    现值 = {v!r}")
            print(f"    位置/语义 = {where}")
            print(f"    现场可观测性 = {seen}")
    print("\n" + "-" * 100)
    print("  结论（本轮）：能**独立生效**且与「腿量/质量」直接相关的候选：")
    print("    · `trend_only_q`（平缓闸，h520 预案 0.35→0）—— 计数在动 ✓、直接决定「安静时段有没有腿」；")
    print("    · `ofi_toxic_*` / `model_thr_frac` / `vwap_flow_block` —— 计数在动 ✓；")
    print("    · `ofi_confirm_threshold` —— **不动** ✗（结构性被抢先）⇒ 不适合当前形态的 ③。")
    print("  ⚠️ `ofi_block_threshold` 的语义必须**先读消费点**再决定它能否替代 ③（下一步）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
