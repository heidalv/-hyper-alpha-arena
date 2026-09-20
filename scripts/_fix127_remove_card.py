# -*- coding: utf-8 -*-
"""轮127：按用户指令删除画布上的「中线因子路线 A/B（已停用）」空卡。

用户截图 + 原话：「这个没用就去掉吧」。
删卡时同时删掉指向/来自它的边（否则会留下悬空引用），并在原处留一行说明，
避免下次有人以为"这里漏了一个节点"。
"""
import io

p = "backend/services/agent_wall.py"
lines = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read().split("\n")

# ① 找到节点定义块：[起始注释 ... 到该 dict 的结束行]
start = end = None
for i, ln in enumerate(lines):
    if '{"id": "factor_route_ab"' in ln:
        end = i
        while end < len(lines) and '"deps"' not in lines[end]:
            end += 1
        start = i
        # 往上吃掉紧邻的注释行
        while start - 1 >= 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        break

removed_node = 0
if start is not None:
    note = ('    # [轮127 2026-09-19 用户指令] 「中线因子路线 A/B（已停用）」节点**已删除**：\n'
            '    #   它当时只是个"已停用"占位（source=kind:none、0 行），用户看过截图后要求去掉。\n'
            '    #   因子路线本身仍只产证据、不开仓（.env MIDLONG_MID_FACTOR_ROUTE_AB=false）。')
    lines[start:end + 1] = note.split("\n")
    removed_node = end - start + 1
    print(f"[OK] 已删除 factor_route_ab 节点（{removed_node} 行）")

# ② 删边
before = len(lines)
lines = [ln for ln in lines if "factor_route_ab" not in ln or ln.strip().startswith("#")]
print(f"[OK] 删除引用 factor_route_ab 的边 {before - len(lines)} 行")

io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write("\n".join(lines))
