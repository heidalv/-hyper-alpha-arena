# -*- coding: utf-8 -*-
"""清理孤儿 smart-signal 集群（2026-08-17 审计执行）：
- prompt_routes.py: 移除 SmartPromptGenerator/pattern 相关 handler + 模型
- main.py: 注销 smart_signal_routes / ai_signal_prompt_integration_routes
- 删除 6 个服务/路由文件
- 测试盘点表移除 smart_signal_routes 条目
用法: .venv/Scripts/python scripts/purge_smart_signal_cluster.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def cut_lines(lines, start_marker, end_marker=None, keep_start=False, keep_end=True):
    """按行内容标记切块；返回新行列表。"""
    si = ni = None
    for i, ln in enumerate(lines):
        if si is None and start_marker in ln:
            si = i
        if end_marker is not None and si is not None and ni is None and end_marker in ln:
            ni = i
            break
    if si is None:
        raise SystemExit(f"start marker not found: {start_marker!r}")
    if end_marker is not None and ni is None:
        raise SystemExit(f"end marker not found: {end_marker!r}")
    if end_marker is None:
        return lines[:si]
    out = lines[:si] + (lines[si:si + 1] if keep_start else [])
    out += lines[ni:] if keep_end else lines[ni + 1:]
    return out


# ── 1. prompt_routes.py ─────────────────────────────────────────────
p = os.path.join("backend", "api", "prompt_routes.py")
with open(p, "r", encoding="utf-8") as f:
    lines = f.read().splitlines(keepends=True)

# 1a. 注释头 + smart_prompt_generator 导入 → 新注释 + market_regime 导入
a_start = None
for i, ln in enumerate(lines):
    if "Smart Prompt Generation APIs" in ln:
        a_start = i
        break
a_end = None
for i, ln in enumerate(lines):
    if "get_multi_timeframe_regime_consensus," in ln:
        a_end = i
        break
assert a_start is not None and a_end is not None, "smart section head not found"
replacement = [
    "# ============================================================================\n",
    "# 自适应参数 APIs（依赖 market_regime_service，活跃服务）\n",
    "# ============================================================================\n",
    "\n",
    "from backend.services.market_regime_service import (\n",
    "    get_adaptive_trading_parameters,\n",
    "    get_multi_timeframe_regime_consensus,\n",
    ")\n",
]
lines = lines[:a_start] + replacement + lines[a_end + 1:]

# 1b. 删除 3 个模型类（保留 AdaptiveParametersResponse）
lines = cut_lines(lines, "class SmartPromptRequest(BaseModel):",
                  "class AdaptiveParametersResponse(BaseModel):")

# 1c. 删除 generate_smart_prompt handler
lines = cut_lines(lines, '@router.post("/generate-smart-prompt"',
                  '@router.post("/generate-signal-linked-prompt"')

# 1d. 删除 generate_signal_linked_prompt handler
lines = cut_lines(lines, '@router.post("/generate-signal-linked-prompt"',
                  '@router.get("/adaptive-parameters/{symbol}"')

# 1e. 删除文件尾部 generate_adaptive_rules handler
lines = cut_lines(lines, '@router.post("/generate-adaptive-rules/{symbol}")',
                  end_marker=None)

# 收尾：去掉文件末尾多余的连续空行
while lines and lines[-1].strip() == "":
    lines.pop()
lines.append("\n")

with open(p, "w", encoding="utf-8") as f:
    f.writelines(lines)
print(f"[ok] prompt_routes.py → {len(lines)} lines")

# ── 2. main.py 注销两个 router ──────────────────────────────────────
p = os.path.join("backend", "main.py")
with open(p, "r", encoding="utf-8") as f:
    lines = f.read().splitlines(keepends=True)
drop = {
    "from .api.ai_signal_prompt_integration_routes import router as ai_signal_prompt_integration_router",
    "from .api.smart_signal_routes import router as smart_signal_router",
    "app.include_router(ai_signal_prompt_integration_router)",
    "app.include_router(smart_signal_router)",
}
removed = 0
out = []
for ln in lines:
    if ln.strip() in drop:
        removed += 1
        continue
    out.append(ln)
assert removed == 4, f"main.py expected 4 lines removed, got {removed}"
with open(p, "w", encoding="utf-8") as f:
    f.writelines(out)
print(f"[ok] main.py → removed {removed} router lines")

# ── 3. 删除 6 个文件 ────────────────────────────────────────────────
targets = [
    "backend/api/smart_signal_routes.py",
    "backend/api/ai_signal_prompt_integration_routes.py",
    "backend/services/smart_signal_generator.py",
    "backend/services/smart_prompt_generator.py",
    "backend/services/ai_signal_prompt_integration_service.py",
    "backend/services/pattern_recognition_service.py",
]
for t in targets:
    if os.path.exists(t):
        os.remove(t)
        print(f"[ok] deleted {t}")
    else:
        print(f"[skip] not found {t}")

# ── 4. 测试盘点表移除 smart_signal_routes 条目 ──────────────────────
p = os.path.join("backend", "tests", "integration", "test_blocking_io_offloaded.py")
with open(p, "r", encoding="utf-8") as f:
    content = f.read()
needle = '    ("backend/api/smart_signal_routes.py", "ai_deep_analysis", "def"),\n'
if needle in content:
    content = content.replace(needle, "")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    print("[ok] test_blocking_io_offloaded.py → smart_signal_routes entry removed")
else:
    print("[warn] test entry not found (already removed?)")

print("DONE")
