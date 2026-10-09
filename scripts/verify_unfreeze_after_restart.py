# -*- coding: utf-8 -*-
"""重启后**运行态验收**：A/C/E 是否真的生效（只读）。

用法（重启后端后立刻跑）：
    backend\\.venv\\Scripts\\python.exe scripts\verify_unfreeze_after_restart.py

判据（每条都给"生效前 / 生效后应看到什么"）：
  A  位置闸：日志/台账里 `location_gate_veto … 追高天花板70% 硬否决` **应停止新增**，
     改为出现 `location_gate: paper 缩仓×0.25 放行（live 仍 veto）: …≥70% 高位追多`。
  A' 开仓面：tier=mid 的开仓标的应**不再只有 XRP**（日线 chop 豁免者）。
  C  冷却：`[MidLongCooldown] BLOCK … 未满120分钟` 应变为 `未满30分钟`。
  E  台账：`mlto_thesis_events.open_execute_false` 的 payload **应带 reason**（不再是 `{}`）。
"""
from __future__ import annotations

import io
import re
import sys
import time
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TAIL = 8_000_000
t0 = time.time()
p = ROOT / "logs" / "backend.log"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - TAIL))
    lines = f.read().decode("utf-8", errors="replace").splitlines()
_RAW_LINES = list(lines)          # 未过滤副本（用于定位重启标记）


def backend_start_time():
    """取本次后端启动完成的时刻（"以重启为界"判定的边界）。

    为什么不查进程：第一版用 `subprocess` 调 PowerShell 取进程 CreationDate，
    结果 **GBK 解码崩在读取线程里**（`UnicodeDecodeError: 'gbk' codec can't decode byte 0xb4`）
    ⇒ 取不到时间 ⇒ 悄悄退回全窗口口径 ⇒ **三条判定全错**（重启前数据把结论淹没）。
    改为直接读日志里的 uvicorn 启动标记：`Application startup complete.`（最后一次出现）。
    """
    marker = "Application startup complete."
    hits = [ln[:19] for ln in _RAW_LINES if marker in ln]
    if hits:
        from datetime import datetime
        try:
            return datetime.strptime(hits[-1], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


_RS = backend_start_time()
_RS_STR = _RS.strftime("%Y-%m-%d %H:%M:%S") if _RS else ""
ts = [ln[:19] for ln in lines if re.match(r"^\d{4}-\d{2}-\d{2}", ln)]
print("=" * 92)
print("解冻验收（A/C/E）—— 只读")
print(f"日志尾部 {len(lines):,} 行，时间跨度 {ts[0] if ts else '?'} → {ts[-1] if ts else '?'}")
if _RS_STR:
    print(f"**后端当前进程启动于 {_RS_STR}** ⇒ 下列判定**只统计该时刻之后的事件**")
else:
    print("⚠️ 未能取到后端启动时刻 ⇒ 退回全窗口口径（结论可能被重启前数据污染）")

if _RS_STR:
    lines = [ln for ln in lines if ln[:19] >= _RS_STR]
    print(f"过滤后（重启后）日志行数 = {len(lines):,}")
else:
    print("**未能定位重启标记 ⇒ 下面一律只报事实、不给『生效/未生效』结论**"
          "（避免用被污染的口径下判断）")
print("=" * 92)

# ── A：硬否决 vs 缩仓放行 ──
hard = [ln for ln in lines if "追高天花板" in ln and "硬否决" in ln]
# ⚠️ 匹配口径要覆盖**两种文案**：闸函数侧 `…paper 缩仓×0.25 放行（live 仍 veto）…`，
# 与执行器侧 `[MidLong] stage=fuse symbol=X 位置闸 paper 缩仓×0.25（location_gate_veto: …）`
# —— 后者**不含"放行"二字**。我第一版只匹配"含放行"，于是把已生效的 A 判成"未生效"。
shrink = [ln for ln in lines
          if ("位置闸 paper 缩仓" in ln) or ("paper 缩仓" in ln and "放行" in ln)]
_lg = [ln for ln in lines if "location_gate" in ln or "位置闸" in ln]
print(f"\n[A] 重启后：追高天花板**硬否决** = {len(hard)}    "
      f"paper **缩仓放行** = {len(shrink)}    位置闸相关行 = {len(_lg)}")
# ⚠️ 判定必须用 `not hard and not shrink`（列表真值），**不能写 `hard == 0`**
# —— `[] == 0` 在 Python 里是 **False**（不报错！），会把"无数据"错判成走 else 分支的"未生效"。
# 我自己就踩了这个：明明 len=0，却打出"⚠️ 未生效"。
if not hard and not shrink:
    print("    ⇒ ⏳ 重启后位置闸**尚未产生可判定事件**（既未放行也未硬否决）—— "
          "现在既不能说生效、也不能说未生效，请过几分钟再跑")
elif shrink and not hard:
    print("    ⇒ ✅ A 生效：只见缩仓放行、**零硬否决**")
elif shrink and hard:
    print(f"    ⇒ 🟡 混合：缩仓放行 {len(shrink)} 条、硬否决 {len(hard)} 条 —— "
          "请核对后者时间是否早于边界")
else:
    print("    ⇒ ⚠️ A **未生效**：重启后只有硬否决、零缩仓放行 ⇒ "
          "检查进程是否真在边界之后启动、`.env` 是否被覆盖")

# ── A'：mid 开仓标的多样性 ──
opens = [ln for ln in lines if "MidLongBrain] opened" in ln]
by_sym = Counter()
for ln in opens:
    m = re.search(r"opened (\S+)", ln)
    if m:
        by_sym[m.group(1)] += 1
print(f"\n[A'] 重启后 `[MidLongBrain] opened` = {len(opens)} 次，按标的: {dict(by_sym)}")
if by_sym:
    print(f"    ⇒ 标的数 = {len(by_sym)}（解冻后预期不再只集中在 chop 标的：XRP/XPL）")
else:
    print("    ⇒ ⏳ 重启后尚无成交（中线周期未跑到或当前无合规标的）")

# ── C：冷却档位 ──
cd = [ln for ln in lines if "[MidLongCooldown] BLOCK" in ln]
mins = [int(m) for m in re.findall(r"未满(\d+)分钟", "\n".join(cd))]
c = Counter(mins)
print(f"\n[C] 重启后冷却拦截 = {len(cd)} 次，档位分布 = {dict(sorted(c.items()))}")
if not c:
    print("    ⇒ ⏳ 重启后还没有冷却拦截事件（需要出现一次 SL 平仓后再同向尝试）")
else:
    if c.get(120):
        print(f"    ⇒ ⚠️ 重启后仍有 {c[120]} 条 120 分钟档 ⇒ C **未生效**")
    if c.get(30):
        print(f"    ⇒ ✅ 重启后出现 {c[30]} 条 30 分钟档 ⇒ C 生效")

# ── E：台账 payload 是否带 reason（只看重启后的行）──
print("\n[E] 台账 mlto_thesis_events.open_execute_false 是否带 reason（仅重启后）")
try:
    from sqlalchemy import text
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        if _RS_STR:
            rows = db.execute(text("""
                SELECT ts, payload_json FROM mlto_thesis_events
                WHERE event_type = 'open_execute_false' AND ts >= :rs
                ORDER BY ts DESC LIMIT 30
            """), {"rs": _RS_STR}).fetchall()
        else:
            rows = db.execute(text("""
                SELECT ts, payload_json FROM mlto_thesis_events
                WHERE event_type = 'open_execute_false'
                ORDER BY ts DESC LIMIT 30
            """)).fetchall()
        with_reason = sum(1 for (_t, r) in rows if r and '"reason"' in str(r))
        print(f"    重启后 {len(rows)} 条中带 reason 的 = {with_reason}")
        if rows:
            print(f"    最新一条 [{rows[0][0]}]: {str(rows[0][1])[:220]}")
        if with_reason:
            print("    ⇒ ✅ reason 已落地（选项E 生效）"
                  f"（{with_reason}/{len(rows)}）")
        elif rows:
            print("    ⇒ ⚠️ 重启后已有被否事件但**没有 reason** ⇒ E 未生效（检查代码是否被覆盖）")
        else:
            print("    ⇒ ⏳ 重启后还没有被否的开仓尝试（尚未产生该事件）")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"    （无法读台账: {type(exc).__name__}: {str(exc)[:100]}）")

print(f"\n（耗时 {time.time()-t0:.1f}s）")
print("\n口径提示：本脚本**以当前后端进程的启动时刻为界**统计（见脚本内 backend_start_time）。")
print("      · 出现 ⏳ = 重启成功但事件还没产生，**不能据此判定未生效**；")
print("      · 出现 ⚠️ = 重启后仍是旧行为，才是真问题。")
