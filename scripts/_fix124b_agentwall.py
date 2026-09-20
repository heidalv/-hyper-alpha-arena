# -*- coding: utf-8 -*-
"""轮124 补：AgentWall 三处改动（单行锚点，避开 CRLF 匹配问题）。

1. E1 卡的源 → `json_file: data/trend_drift/e1_last_run.json`（原来过滤 `TrendE1|trend_e1`
   只会捞到卡片自己的轮询访问日志）；
2. 执行卡拆成 `tier=mid` / `tier=long` 两张（中/长线是同一个 Writer，日志现在带 tier 了）；
3. `_node_lines` 支持 `json_file`（读运行产物，按字段分行）。
"""
import io

AW = "backend/services/agent_wall.py"
s = io.open(AW, encoding="utf-8", errors="surrogateescape", newline="").read()
n = 0

# ── 1. E1 卡的源 ──
old = '     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"TrendE1|trend_e1"},'
new = ('     # [轮124 2026-09-19] 原过滤 `TrendE1|trend_e1` 取 `logs/backend.log`，实测命中\n'
       '     # 124 行**全是本卡自己的轮询访问日志**（`uvicorn.access ... "/api/agent-wall/tail?\n'
       '     # nodes=...,trend_e1_engine,..."`），真实 E1 业务行 0 条 ⇒ 卡片内容与长线无关。\n'
       '     # 改为读 **E1 最近一次运行产物**（cron 每天 08:20 写一次）。\n'
       '     "source": {"kind": "json_file", "path": "data/trend_drift/e1_last_run.json"},')
if old in s:
    s = s.replace(old, new, 1)
    n += 1
    print("[1] E1 卡源已改")

# ── 2. 执行卡拆分（用单行锚点替换 label / filter）──
o2 = '{"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（midlong_executor）",'
n2 = '{"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（tier=mid）",'
o3 = '     "cadence_label": "随 tick", "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\]"},'
n3 = ('     "cadence_label": "随 tick",\n'
      '     # [轮124 2026-09-19 拆车道] 中/长线是**同一个 Writer**（execute_midlong_open），\n'
      '     # 此前只有一张"中线执行"卡、且混着两条车道的行（`tier=mid`=0、`tier=long`=1，\n'
      '     # 因为执行日志没带 tier；轮124 已给日志补上 tier）⇒ 长线执行在画布上无处可见。\n'
      '     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\].*tier=mid"},')
NEW_CARD = ('    {"id": "midlong_exec", "group": "G4", "size": "L", "label": "长线执行（tier=long）",\n'
            '     "role": "**长线**新开：与中线共用同一 Writer（`execute_midlong_open`），按 tier 分卡显示；"\n'
            '             "长线另有独立通道 `trend_e1_engine`（cron 直连 place_order，绕过 Single Writer）",\n'
            '     "cadence_label": "随 tick",\n'
            '     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLong\\].*tier=long"},\n'
            '     "deps": ["thesis_store"]},\n')
o4 = '     "deps": ["thesis_store"]},\n    {"id": "direction_audit"'
if o2 in s and o3 in s and o4 in s:
    s = s.replace(o2, n2, 1)
    s = s.replace(o3, n3, 1)
    s = s.replace(o4, '     "deps": ["thesis_store"]},\n' + NEW_CARD + '    {"id": "direction_audit"', 1)
    n += 1
    print("[2] 执行卡已拆成 tier=mid / tier=long 两张")
else:
    print("[!] 拆分锚点未全部命中:", o2 in s, o3 in s, o4 in s)

# ── 3. json_file 支持 ──
anchor = '    if kind == "json_latest":'
BLOCK = '''    if kind == "json_file":
        # [轮124] 读**运行产物 JSON** 并按字段分行（E1 这类"每天跑一次"的节点，
        # 日志里在非运行时段本来就没有业务行，只能看最近一次运行结果）。
        rel = str(src.get("path") or "")
        p = (LOGS / rel.split("/", 1)[1]) if rel.startswith("logs/") else (ROOT / rel)
        try:
            import json as _json

            _d = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        _out: List[Dict[str, Any]] = []
        if isinstance(_d, dict):
            for _k in sorted(_d.keys()):
                _v = _d[_k]
                if _v in (None, "", [], {}):
                    continue
                if isinstance(_v, (dict, list)):
                    _v = _json.dumps(_v, ensure_ascii=False)[:240]
                _out.append({"ts": "", "text": f"{_k}｜{_v}", "raw": f"{_k}={_v}"})
        return _out[:25]
'''
if anchor in s and "kind == \"json_file\"" not in s:
    s = s.replace(anchor, BLOCK + anchor, 1)
    n += 1
    print("[3] json_file 已支持")

io.open(AW, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
print("[OK] 改动", n, "处")
