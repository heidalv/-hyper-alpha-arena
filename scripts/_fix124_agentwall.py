# -*- coding: utf-8 -*-
"""轮124：修 AgentWall 两张卡（用户截图投诉）。

## 用户三点

1. 「怎么还不一样呢」——中线卡有真实执行行，长线卡全是 HTTP 访问行；
2. 「怎么没有长线执行，有的是长线开仓」——画布缺"长线执行"节点；
3. 「他还几乎没有和其他的关联」——长线卡内容与业务无关。

## 现场（`logs/backend.log` 尾 4 万行实测）

* `TrendE1|trend_e1` 命中 **124 行，全是该卡自己的轮询访问日志**
  （`uvicorn.access ... "GET /api/agent-wall/tail?nodes=...,trend_e1_engine,..."`）
  ⇒ 卡片在放自己的心跳，真实 E1 业务行 0 条；
* `[MidLong]` 命中 **308 行**，但 `[MidLong].*tier=mid` = **0**、`tier=long` = **1**
  ⇒ **中/长线是同一个 Writer，而它的执行日志没带 tier** ⇒ 画布只能有"中线执行"，
  长线执行无处可见（用户问的"没有长线执行"）。

## 本脚本做三件

A. `midlong_executor` 的 `[MidLong] stage=...` 日志**加 tier**（这样画布能按车道分卡，
   也让审计里"这一行属于哪条车道"不再靠猜）；
B. `agent_wall`：执行卡拆成 **tier=mid / tier=long** 两张；E1 卡的源从"日志过滤"
   改成 **E1 最近一次运行产物**（日志里本来就没有业务行，过滤只能捞到自己的访问日志）；
C. `_read_incremental` 全局剔除**访问日志噪音**（uvicorn.access / `"GET /api/`）——
   这类行永远不是 agent 活动。
"""
import io
import re

ROOT = "."

# ── A. 执行器日志加 tier ──────────────────────────────────────────────
EXEC = "backend/services/full_auto/midlong_executor.py"
src = io.open(EXEC, encoding="utf-8", errors="surrogateescape", newline="").read()

A_PATCHES = [
    # 最常见的那几条：stage=fuse / stage=exec / stage=open_ready
    ('"[MidLong] stage=exec symbol=%s authority=%s source=%s action=%s "',
     '"[MidLong] stage=exec tier=%s symbol=%s authority=%s source=%s action=%s "'),
    ('"[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold reason=%s"',
     '"[MidLong] stage=fuse tier=%s symbol=%s authority=%s source=%s action=hold reason=%s"'),
    ('"[MidLong] stage=fuse symbol=%s authority=%s source=%s action=hold "',
     '"[MidLong] stage=fuse tier=%s symbol=%s authority=%s source=%s action=hold "'),
    ('"[MidLong] stage=fuse symbol=%s regime=ranging action=%s size×%.2f (probe)"',
     '"[MidLong] stage=fuse tier=%s symbol=%s regime=ranging action=%s size×%.2f (probe)"'),
    ('"[MidLong] stage=fuse symbol=%s regime=unknown action=%s size×%.2f"',
     '"[MidLong] stage=fuse tier=%s symbol=%s regime=unknown action=%s size×%.2f"'),
    ('"[MidLong] stage=fuse symbol=%s regime=extreme action=hold reason=regime_block"',
     '"[MidLong] stage=fuse tier=%s symbol=%s regime=extreme action=hold reason=regime_block"'),
]
n_a = 0
for old, new in A_PATCHES:
    if old in src:
        src = src.replace(old, new)
        n_a += 1

# 每条被改过的 logger 调用：把 tier 作为第一个实参插进去
# 形如 logger.info("...[MidLong] stage=exec tier=%s symbol=%s ...", sym_u, auth, ...)
# → logger.info(..., tier, sym_u, auth, ...)
def _add_tier_arg(text: str) -> int:
    fixed = 0
    out = []
    i = 0
    pat = re.compile(r'(\[MidLong\] stage=(?:fuse|exec)[^"]*tier=%s[^"]*"\s*,\s*)')
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.end()])
        # 紧跟的实参是 symbol 变量（sym_u / symbol / _sym_u）
        out.append("tier, ")
        i = m.end()
        fixed += 1
    return "".join(out), fixed

src, n_a2 = _add_tier_arg(src)
io.open(EXEC, "w", encoding="utf-8", errors="surrogateescape", newline="").write(src)
print(f"[A] midlong_executor: 格式串改 {n_a} 处，实参插入 {n_a2} 处")

# ── B. agent_wall：执行卡按 tier 拆分 + E1 卡改源 ───────────────────────
AW = "backend/services/agent_wall.py"
aw = io.open(AW, encoding="utf-8", errors="surrogateescape", newline="").read()

OLD_E1 = '''     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"TrendE1|trend_e1"},
     "deps": ["thesis_store"]},'''
NEW_E1 = '''     # [轮124 2026-09-19 修「卡片在放自己的心跳」] 原来过滤 `TrendE1|trend_e1` 去
     # `logs/backend.log` 取行，实测命中 124 行**全是本卡自己的轮询访问日志**
     # （`uvicorn.access ... "GET /api/agent-wall/tail?nodes=...,trend_e1_engine,..."`），
     # 真实 E1 业务行 0 条 ⇒ 卡片内容与长线毫无关系（用户截图投诉）。
     # 改为读 **E1 最近一次运行产物**（那是它唯一可核对的真实输出）。
     "source": {"kind": "json_file", "path": "data/trend_drift/e1_last_run.json"},
     "deps": ["thesis_store", "midlong_exec"]},'''
if OLD_E1 in aw:
    aw = aw.replace(OLD_E1, NEW_E1, 1)
    print("[B1] E1 卡源已改为 e1_last_run.json")

OLD_MID = '''    {"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（midlong_executor）",
     "role": "**中线**开仓唯一 Writer（authority=llm_thesis）：读论题库 → 决定开/拒 → 写审计漏斗",
     "cadence_label": "随 tick", "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\]"},
     "deps": ["thesis_store"]},'''
NEW_MID = '''    # [轮124 2026-09-19 拆车道] 中/长线是**同一个 Writer**（`execute_midlong_open`），
    # 此前画布只有"中线执行"一张卡、它同时混着两条车道的行（`tier=mid`=0、`tier=long`=1，
    # 因为执行日志**没带 tier**；本脚本已给日志补上 tier）。现在按车道拆成两张对称的卡。
    {"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（tier=mid）",
     "role": "**中线**新开唯一 Writer（authority=llm_thesis）：读论题库 → 决定开/拒 → 写审计漏斗",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\].*tier=mid"},
     "deps": ["thesis_store"]},
    {"id": "midlong_exec", "group": "G4", "size": "L", "label": "长线执行（tier=long）",
     "role": "**长线**新开同一 Writer 的 long 车道（与中线共用 `execute_midlong_open`，"
             "按 tier 分卡显示）；长线还有一条独立通道 `trend_e1_engine`（cron 直连 place_order）",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\].*tier=long"},
     "deps": ["thesis_store"]},'''
if OLD_MID in aw:
    aw = aw.replace(OLD_MID, NEW_MID, 1)
    print("[B2] 执行卡已拆成 tier=mid / tier=long 两张")
else:
    print("[!] 未匹配到中线执行卡定义（跳过）")

# 边：给新卡与 E1 卡接上关系
OLD_EDGE_ANCHOR = '''    {"from": "coordinator", "to": "trend_agent", "kind": "trigger", "label": "持仓复核"},'''
NEW_EDGE_ANCHOR = OLD_EDGE_ANCHOR + '''
    # [轮124] 补关联：长线执行与 E1 两条通道都必须挂进图里（此前 E1 卡几乎是孤儿节点）
    {"from": "thesis_store", "to": "midlong_exec", "kind": "data", "label": "长线论题"},
    {"from": "midlong_loop", "to": "midlong_exec", "kind": "trigger", "label": "tick(tier=long)"},
    {"from": "trend_e1_engine", "to": "position_sizing", "kind": "trigger", "label": "E1 直接下单"},
    {"from": "data_center", "to": "trend_e1_engine", "kind": "data", "label": "日线行情"},'''
if OLD_EDGE_ANCHOR in aw:
    aw = aw.replace(OLD_EDGE_ANCHOR, NEW_EDGE_ANCHOR, 1)
    print("[B3] 已补 4 条边")

# ── C. 访问日志噪音过滤 ───────────────────────────────────────────────
OLD_NOISE_ANCHOR = "_ENVELOPE_RE = re.compile("
NOISE_BLOCK = '''# [轮124 2026-09-19] 访问日志噪音：`uvicorn.access` / `"GET /api/...` 这类行**永远不是**
# agent 活动，却会命中"按 URL 关键词过滤"的卡片（实测长线卡 124 行全是它自己的轮询）。
# 在取行处统一剔除，比逐卡改过滤词更可靠（新增卡片不会再踩同一个坑）。
_ACCESS_NOISE_RE = re.compile(r"uvicorn\\.access|\\"\\s*(?:GET|POST|PUT|DELETE|PATCH) /api/|^INFO:\\s+\\d")


def _is_access_noise(line: str) -> bool:
    try:
        return bool(_ACCESS_NOISE_RE.search(line or ""))
    except Exception:
        return False


'''
if "_ACCESS_NOISE_RE" not in aw:
    i = aw.index(OLD_NOISE_ANCHOR)
    aw = aw[:i] + NOISE_BLOCK + aw[i:]
    # 两处取行判断都加噪音剔除
    aw = aw.replace(
        "            hits = [ln for ln in parts if (not pat or pat.search(ln))]",
        "            hits = [ln for ln in parts\n"
        "                    if (not pat or pat.search(ln)) and not _is_access_noise(ln)]", 1)
    aw = aw.replace(
        "                if not line or (pat and not pat.search(line)):\n                    continue",
        "                if not line or (pat and not pat.search(line)) or _is_access_noise(line):\n"
        "                    continue", 1)
    print("[C] 访问日志噪音过滤已加入（首读 + 增量两处）")

io.open(AW, "w", encoding="utf-8", errors="surrogateescape", newline="").write(aw)
print("[OK] agent_wall.py 已更新")
