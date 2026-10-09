# -*- coding: utf-8 -*-
"""一次性修正 agent_runtime_monitor 的引用点：把 _KNOWN_AGENTS 改用 known_agents()。"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "backend" / "services" / "agent_runtime_monitor.py"
src = p.read_text(encoding="utf-8")

head = src.split("class ")[0]
print("Optional 导入:", bool(re.search(r"from typing import[^\n]*Optional", head)) or "Optional" in head)
print("有 __future__ annotations:", "from __future__ import annotations" in head)

src = src.replace("for aid in _KNOWN_AGENTS", "for aid in known_agents()")
src = src.replace('"agents": list(_KNOWN_AGENTS)', '"agents": known_agents()')
src = src.replace("return list(_KNOWN_AGENTS)", "return known_agents()")
src = src.replace("aid: AgentRuntimeStats(agent_id=aid) for aid in _KNOWN_AGENTS",
                  "aid: AgentRuntimeStats(agent_id=aid) for aid in known_agents()")

# 保险：把 typing 导入补全（避免 Optional 未导入时注解在 def 期求值报错）
if "from typing import" in head and not re.search(r"from typing import[^\n]*Optional", head):
    src = re.sub(r"from typing import ([^\n]+)",
                 lambda m: f"from typing import {m.group(1)}, Optional"
                 if "Optional" not in m.group(1) else m.group(0), src, count=1)

p.write_text(src, encoding="utf-8")
left = len(re.findall(r"_KNOWN_AGENTS(?!\s*:)", src))
print("替换后 _KNOWN_AGENTS 非定义引用数:", left)
import ast
ast.parse(src)
print("AST OK")
