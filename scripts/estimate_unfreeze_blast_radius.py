# -*- coding: utf-8 -*-
"""解冻 A/C 的**解锁面**估算（只读，基于日志事实，不推测行情）。

口径（必须写明）：
  · "被天花板拦掉的多头尝试" = 日志里 `location_gate_veto: …≥追高天花板70% 硬否决` 的次数
    （该分支只在 paper + act=buy 时进入 ⇒ 全部是多头尝试）。
  · "当前能过的多头尝试" = `[MidLong] stage=exec … action=buy` 的次数（已通过位置闸等前置）。
  · 比值 = 前者/后者 ⇒ 解冻 A 后**多头进入执行层的尝试量大约放大多少倍**（上界估计：
    解冻后仍要过后端组合预算/冷却/MTF 等闸，不等于最终成交数）。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend.log"
NB = 36_000_000
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - NB))
    lines = f.read().decode("utf-8", errors="replace").splitlines()

ts = [ln[:19] for ln in lines if re.match(r"^\d{4}-\d{2}-\d{2}", ln)]
hours = 0.0
try:
    from datetime import datetime
    a = datetime.strptime(ts[0], "%Y-%m-%d %H:%M:%S")
    b = datetime.strptime(ts[-1], "%Y-%m-%d %H:%M:%S")
    hours = (b - a).total_seconds() / 3600.0
except Exception:  # noqa: BLE001
    pass

ceil_blocks = [ln for ln in lines if "追高天花板" in ln and "硬否决" in ln]
exec_buy = [ln for ln in lines if "[MidLong] stage=exec" in ln and "action=buy" in ln]
cooldown = [ln for ln in lines if "[MidLongCooldown] BLOCK" in ln]
short_block = [ln for ln in lines if "midlong_short_regime_block" in ln]

def syms(rows):
    s = Counter()
    for ln in rows:
        m = re.search(r"symbol=(\S+)", ln)
        if m:
            s[m.group(1)] += 1
    return s

print("=" * 92)
print(f"窗口 {ts[0]} → {ts[-1]}  （{hours:.2f} 小时）")
print("=" * 92)
print(f"\n[A-1] 被追高天花板硬否决的多头尝试 = {len(ceil_blocks)}")
print(f"      按 symbol 前 10: {dict(syms(ceil_blocks).most_common(10))}")
print(f"      速率 = {len(ceil_blocks)/max(hours,0.01):.1f} 次/小时 "
      f"⇒ 折合 {len(ceil_blocks)/max(hours,0.01)*24:.0f} 次/天")

print(f"\n[A-2] 当前能进执行层的多头尝试（stage=exec action=buy） = {len(exec_buy)}")
print(f"      按 symbol: {dict(syms(exec_buy).most_common(10))}")
print(f"      速率 = {len(exec_buy)/max(hours,0.01):.1f} 次/小时 "
      f"⇒ 折合 {len(exec_buy)/max(hours,0.01)*24:.0f} 次/天")

if exec_buy:
    print(f"\n[A-3] 解锁倍数（**扫描事件**口径，上界） = "
          f"{len(ceil_blocks)/len(exec_buy):.1f}×")
    print("      ⚠️ 该比值是'扫描事件'口径：同一 symbol 每 ~1 分钟被重评一次，"
          "故 892 次并不等于 892 个独立机会（ASTER 一个币就被否 352 次）。")

# ── 去重口径：币×小时（更接近"机会"）──
def sym_hour(rows):
    out = set()
    for ln in rows:
        m = re.search(r"^(\d{4}-\d{2}-\d{2} \d{2})", ln)
        s = re.search(r"symbol=(\S+)", ln)
        if m and s:
            out.add((s.group(1), m.group(1)))
    return out

cb_sh = sym_hour(ceil_blocks)
eb_sh = sym_hour(exec_buy)
print(f"\n[A-3b] 去重口径（symbol×小时，更接近'机会'）：")
print(f"      被天花板否决: {len(cb_sh)} 个币·小时（涉及 {len({s for s,_ in cb_sh})} 个币）")
print(f"      当前通过:     {len(eb_sh)} 个币·小时（涉及 {len({s for s,_ in eb_sh})} 个币）")
if eb_sh:
    print(f"      ⇒ 币·小时口径的解锁倍数 ≈ {len(cb_sh)/len(eb_sh):.1f}×"
          f"（低于扫描事件口径，因为是同一批币被反复评估）")
print("\n      **结论**：解冻 A 打开的是'**同一批已持有信号的币**在 24h 高位时的缩仓尝试'，"
      "不是新增标的；币·小时口径约 **+40%**（25 vs 18），每笔仅 0.25× 仓 "
      "⇒ 名义敞口增量有限、样本量温和增加。"
      "\n      （我第一版此处写'样本量会有量级提升'，与上面的 1.4× 自相矛盾 —— 已按数字改正。）")

print(f"\n[A-4] 参照：今日 mid 实际开仓 = 2 笔（均 XRP）")
print(f"      （见 scripts/probe_mid_open_state2.py；昨日 18 笔）")

print(f"\n[C-1] mid 冷却拦截次数 = {len(cooldown)}")
mins = [int(m) for m in re.findall(r"未满(\d+)分钟", "\n".join(cooldown))]
if mins:
    print(f"      日志中出现的冷却档位: {sorted(set(mins))} 分钟"
          f"   （120 档 {mins.count(120)} 次 / 30 档 {mins.count(30)} 次）")
print("      改 1800s 后：SL 后同向锁从 120 分钟降到 30 分钟 ⇒ 锁定时长 **×0.25**")

print(f"\n[D-1] 空头 regime 闸拦截 = {len(short_block)}（解冻 A/C **不改变**它，仍由日线 regime 决定）")
print("      ⇒ 解冻后多头侧打开；空头侧仍取决于日线 regime（当前 up ⇒ 仍禁）。")
