# -*- coding: utf-8 -*-
"""轮125：修上一轮 AgentWall 的三处（用户截图：中线卡空了 / 长线重复 / 关联缺失）。

## 上一轮的问题（我的）

1. 我把"中线执行"卡的过滤词改成 `\\[MidLong\\].*tier=mid`，**但只有 6 处日志补了 tier、
   13 处没补** ⇒ 命中≈0 ⇒ **中线卡变空**（截图实测 0 行）。过滤词不该依赖"未来的日志格式"。
2. 边（关联）的补丁锚点没匹配 ⇒ **一条都没加**（`trend_e1_engine` 仍是孤儿、长线执行无边）。
3. 长线相关三张卡命名相似（长线持仓复核 / 长线 E1 开仓 / 长线执行），两张没数据 ⇒ 看着像重复。

## 本次改动（都可回滚：只动 agent_wall 的卡片定义与日志格式串）

A. **过滤词改成"现在就能命中"**：
   * 中线执行卡：`\\[MidLong\\](?!.*tier=long)` —— 所有非 long 的执行行（立刻有数据）；
   * 长线执行卡：`\\[MidLong\\].*tier=long` —— 带 tier 的新行（逐步填充）。
B. **补齐剩余 tier 标记**：把所有还缺 `tier=%s` 的 `[MidLong] stage=` 日志补上（机械替换）。
C. **补边**：以稳定的单行锚点插入（长线执行、E1、持仓复核的关系）。
D. **命名去歧义**：`midlong_exec` → 「长线执行（execute_midlong_open · tier=long）」。
"""
import io
import re

ROOT = "."
AW = "backend/services/agent_wall.py"
EX = "backend/services/full_auto/midlong_executor.py"

# ── A + D：过滤词与命名 ────────────────────────────────────────────────
s = io.open(AW, encoding="utf-8", errors="surrogateescape", newline="").read()
n = 0

o = '"filter": r"\\[MidLong\\].*tier=mid"'
if o in s:
    s = s.replace(o, '"filter": r"\\[MidLong\\](?!.*tier=long)"', 1)
    n += 1
    print("[A1] 中线卡过滤词改为『所有非 long 的 [MidLong] 行』——不再依赖未来日志格式")

o = '"label": "长线执行（tier=long）"'
if o in s:
    s = s.replace(o, '"label": "长线执行（execute_midlong_open · tier=long）"', 1)
    n += 1
    print("[D] 长线执行卡已改名（与『长线持仓复核』『长线 E1 开仓』明确区分）")

# ── C：补边（锚在一条确定存在的边上）────────────────────────────────────
ANCHOR = '    {"from": "factor_engine", "to": "brain_long", "kind": "data", "label": "因子"},'
NEW_EDGES = ANCHOR + '''
    # [轮125 2026-09-19 补关联] 上一轮的补边锚点没匹配、一条都没进图（用户："关联关系没有"）：
    # 长线执行与中线执行是**同一个 Writer 的两个 tier**，E1 是长线另一条独立通道，
    # 持仓复核(revtrend_agent 的 review_position)挂在长线执行之后。
    {"from": "thesis_store", "to": "midlong_exec", "kind": "data", "label": "长线论题"},
    {"from": "midlong_loop", "to": "midlong_exec", "kind": "trigger", "label": "tick(tier=long)"},
    {"from": "midlong_exec", "to": "trend_agent", "kind": "trigger", "label": "持仓复核/加仓"},
    {"from": "data_center", "to": "trend_e1_engine", "kind": "data", "label": "日线行情"},
    {"from": "trend_e1_engine", "to": "position_sizing", "kind": "trigger", "label": "E1 直接下单"},
    {"from": "coin_select", "to": "midlong_exec", "kind": "data", "label": "AI 长线候选"},'''
if ANCHOR in s and '"to": "midlong_exec"' not in s:
    s = s.replace(ANCHOR, NEW_EDGES, 1)
    n += 1
    print("[C] 已补 6 条边（长线执行/E1/持仓复核/AI 长线候选）")
else:
    print("[!] 补边锚点问题:", ANCHOR in s, '"to": "midlong_exec"' in s)

io.open(AW, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)

# ── B：补齐剩余 tier 标记 ──────────────────────────────────────────────
e = io.open(EX, encoding="utf-8", errors="surrogateescape", newline="").read()
fixed = 0
out_lines = []
for line in e.split("\n"):
    # 只处理 [MidLong] stage= 且尚未带 tier 的格式串
    if "[MidLong] stage=" in line and "tier=%s" not in line:
        m = re.search(r'(\[MidLong\] stage=\w+\s*)', line)
        if m:
            line = line[:m.end(1)] + "tier=%s " + line[m.end(1):]
            fixed += 1
    out_lines.append(line)
e2 = "\n".join(out_lines)
# 实参：紧跟格式串之后的第一个实参位置插入 tier
def _insert_args(text: str) -> int:
    cnt = 0
    pat = re.compile(r'(\[MidLong\] stage=\w+ tier=%s[^"]*"\s*,\s*)')
    res, i = [], 0
    while True:
        m = pat.search(text, i)
        if not m:
            res.append(text[i:])
            break
        res.append(text[i:m.end()])
        _tail = text[m.end():m.end() + 12]
        if not _tail.startswith("tier,"):
            res.append("tier, ")
            cnt += 1
        i = m.end()
    return "".join(res), cnt

e2, nargs = _insert_args(e2)
io.open(EX, "w", encoding="utf-8", errors="surrogateescape", newline="").write(e2)
print(f"[B] 执行器：补齐 tier 格式串 {fixed} 处、实参 {nargs} 处")
print("[OK] 共改动", n, "处（agent_wall）")
