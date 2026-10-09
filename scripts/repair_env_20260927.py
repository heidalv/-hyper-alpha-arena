# -*- coding: utf-8 -*-
"""`.env` 修复脚本（[新目标 R4]，2026-09-27）：还原编码 + 拆回被并行的行 + 恢复被吞赋值。

## 事故与现象
2026-09-27 20:35 我用
    (Get-Content .env -Raw) -replace 'A','B' | Set-Content .env -NoNewline -Encoding utf8
把 `.env` 从 ≥2610 行压成 2129 行：约 481 处换行被吃掉，大量 `KEY=value` 被并进**上一行注释**
⇒ dotenv 视作注释 ⇒ 该键静默失效、代码回落默认值。附带副作用是整文件中文变乱码
（PowerShell 用 ANSI 代码页解码 UTF-8 字节后按 UTF-8 重写）。

## 两步修复
1. **编码还原**（无损，可逆）：当前文本 = 原 UTF-8 字节经 cp936 解码后的字符串。
   逆变换 = 用 **Windows cp936 编码器**把字符串编回字节（必须用 Windows 的编码器：
   Python 的 gbk 缺 PUA 映射，228 个字符编不回去）。命令：
       $enc=[Text.Encoding]::GetEncoding(936)
       $t=[IO.File]::ReadAllText('env_damaged.txt',[Text.Encoding]::UTF8)
       [IO.File]::WriteAllBytes('env_restored_bytes.bin', $enc.GetBytes($t))
   再用 UTF-8 解码 → 中文注释恢复可读（极少数字节已永久丢失，表现为 U+FFFD，仅出现在注释里）。
2. **结构还原**：
   a) 在句子终止符后的 `#` 前补换行（还原被并掉的注释行边界；只在 `#` 后有空格时做，
      避免切断 `KEY=value#x` 这类值内 `#`）。
   b) 对被吞掉的赋值：取**同一行内该键的最后一次出现**为生效值（本仓注释约定是
      「现置 X；回滚：KEY=Y」），且仅当该键在整个文件里**没有任何干净赋值行**时才补回。
      风险参数（SL/仓位/权重类）默认**不自动补**，另列清单人工确认 —— 见 VETO_PREFIXES。

## 为什么不是从备份还原
仓库里最新的 `.env.bak_*` 是 2026-09-17 17:21，比事故早 10 天；整文件回滚会丢掉 10 天的
配置变更。所以走"就地修复 + 备份受损文件"路线。

用法：
    python scripts/repair_env_20260927.py --dry-run     # 只打印将要做的改动
    python scripts/repair_env_20260927.py --apply       # 落盘（自动写 .env.bak_*）
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
RESTORED = ROOT / "data/tmp_env_repair/env_repaired.txt"

ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{3,})=([A-Za-z0-9_.+-]+)")
SENT_END = "。！？）」』】%!?"

#: 只在"补回不改变行为"或有**独立证据**时补回。
#: 后者必须逐个人工列入下表（证据写在第二项），绝不允许脚本自行推断生效值 ——
#: 实测脚本抽取会把 `LLM_HTTP_PROXY=http://...` 截成 `http:`、把文档里的 `6s→10s` 当成值。
ALLOW_RESTORE = {
    "KLINE_P0_PERIOD_ALIGN": ("true", "P0 周期对齐，单测 test_p0_period_align_20260924.py（5 passed）"),
    "KLINE_P0_REQ_TIMEOUT_S": ("10", "P0 采集率审计后 6s→10s（文档只写了 6s→10s，取生效值 10）"),
    "MLTO_LEARNING_TRACE": ("true", "R12 埋点（mlto_block_skip=no_thesis 等）"),
    "MLTO_OWM_SPLIT_DEDUPE": ("true", "R22 单测 test_owm_split_dedupe_20260924.py"),
    "MIDLONG_THESIS_INV_REQUIRE_CLOSE": ("true", "回归测试 test_thesis_inv_semantics_20260916.py 断言 true"),
    "MLTO_LEARNING_THESIS_FALLBACK": ("true", "本轮 R4 兜底，代码默认亦 true"),
    "SIGNAL_FACTOR_INJECT_MAX_AGE_MIN": ("45", "台账 §因子注入时效 45min"),
    "V3_FACTOR_ROTATE_ENABLED": ("true", "台账 §轮转游标持久化（随 V3 因子管道）"),
    "MLTO_THESIS_FALLBACK_MAX_AGE_H": ("24", "本轮 R4 跨会话兜底时效"),
    "MLTO_PM_DEDUPE_ON_BUS": ("true", "本轮 R4 postmortem 双写去重"),
}

#: 这些前缀属于**交易风险参数**，自动补回可能直接改变实盘/模拟仓风控 ⇒ 只报告不自动补
VETO_PREFIXES = (
    "MIDLONG_SL_MAX_PCT", "MIDLONG_MAX_SL_PCT", "PC_MAX_WEIGHT", "PC_RISK_PER_TRADE",
    "MIDLONG_MAX_OPEN_POSITIONS", "MIDLONG_MAX_LONG_LANE_POSITIONS", "MIDLONG_CORR_CLUSTER",
    "TIER_MID_MAX_HOLD", "TIER_LONG_MAX_HOLD", "REENTRY_", "SYMBOL_RISK_BAN",
    "LONG_LOSS_CAP", "MIDLONG_LOCATION_", "MIDLONG_PULLBACK", "V5_TREND_MIN_RR",
    "MM_AUTO_EVOLVE", "MIDLONG_AI_", "MIDLONG_SHORT_", "MIDLONG_LONG_", "MIDLONG_CHART_GATE",
    "LIVE_", "MIDLONG_DEBATE", "WFO_IC_MAX_P", "TREND_", "HEALTH_TEMPLATE_REUSE",
    "MIDLONG_REGIME_TTL", "MIDLONG_THESIS_INV", "MLTO_OWM", "MIDLONG_BRAIN",
)


def known_keys() -> dict:
    """代码里真正读取过的键 → 默认值集合（用于剔除被乱码截断的假键）。"""
    import importlib.util
    helper = Path(__file__).with_name("audit_env_swallowed_assignments_20260927.py")
    spec = importlib.util.spec_from_file_location("_env_audit_helper", helper)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.code_defaults()


def split_comment_lines(text: str) -> tuple:
    """在"句子终止符 + # + 空格"处补回换行（注释边界）。返回 (新文本, 补行数)。"""
    out = []
    added = 0
    pat = re.compile(rf"(?<=[{re.escape(SENT_END)}])(?=#\s)")
    for line in text.split("\n"):
        if line.startswith("#") and "#" in line[1:]:
            new = pat.sub("\n", line)
            added += new.count("\n")
            out.append(new)
        else:
            out.append(line)
    return "\n".join(out), added


def plan_activations(lines: list) -> tuple:
    """找出"被吞掉的赋值"并给出补回计划：只补 ① 有独立证据的 ALLOW_RESTORE
    ② 或"值与代码默认一致"（NEUTRAL，补回不改变行为）。返回 (计划, 风险清单, 全部被吞键)。
    """
    assigned = {ASSIGN.match(l).group(1) for l in lines if ASSIGN.match(l)}
    defaults = known_keys()
    swallowed = {}
    vetoed = []
    for i, l in enumerate(lines, 1):
        if not l.lstrip().startswith("#"):
            continue
        last = {}
        for k, v in TOKEN.findall(l):
            if k in assigned:
                continue
            if k not in defaults:
                continue  # 代码里没读过 ⇒ 多半是乱码截断出来的假键（如 IDLONG_xxx）
            last[k] = v
        for k, v in last.items():
            swallowed[k] = (i, v)
            if any(k.startswith(p) for p in VETO_PREFIXES) and k not in ALLOW_RESTORE:
                vetoed.append((k, i, v))
    plan = {}
    neutral = {}
    for k, (ln, v) in swallowed.items():
        if (k, ln, v) in vetoed:
            continue
        if k in ALLOW_RESTORE:
            plan[k] = (ln, ALLOW_RESTORE[k][0], ALLOW_RESTORE[k][1])
            continue
        dset = {(x or "").strip().strip("\"'").lower() for x in defaults.get(k, []) if x}
        if v.lower() in dset:
            neutral[k] = (ln, v)
    return plan, vetoed, swallowed, neutral


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not RESTORED.exists():
        print(f"[err] 缺少编码还原后的文件 {RESTORED}（先跑 docstring 里的 PowerShell 逆变换）")
        return 2
    text = RESTORED.read_text(encoding="utf-8")
    old_lines = text.split("\n")
    print(f"输入：{RESTORED.name} 行数={len(old_lines)} 干净赋值={sum(1 for l in old_lines if ASSIGN.match(l))}")

    text2, added = split_comment_lines(text)
    lines2 = text2.split("\n")
    plan, vetoed, swallowed, neutral = plan_activations(lines2)
    print(f"① 注释边界补行 = {added}  ⇒ 行数 {len(old_lines)} → {len(lines2)}")
    print(f"② 被吞赋值键（代码确实读取且文件里无干净赋值）= {len(swallowed)}")
    print(f"   有独立证据、按生效值补回 = {len(plan)}：")
    for k, (ln, v, why) in sorted(plan.items(), key=lambda x: x[1][0]):
        print(f"      line {ln:>5}  {k}={v}   证据：{why}")
    print(f"   与代码默认一致（补回不改变行为）= {len(neutral)}：")
    for k, (ln, v) in sorted(neutral.items(), key=lambda x: x[1][0]):
        print(f"      line {ln:>5}  {k}={v}")
    print(f"   风险/无证据、本轮不动 = {len(vetoed)}：")
    for k, ln, v in sorted(vetoed, key=lambda x: x[1]):
        print(f"      line {ln:>5}  {k}={v}")

    restore = dict(plan)
    for k, (ln, v) in neutral.items():
        restore[k] = (ln, v, "与代码默认一致 ⇒ 仅恢复文档语义")

    if restore:
        text2 = text2.rstrip("\n") + "\n\n# ── [2026-09-27 R4] 事故修复：以下键的赋值曾被并行吞进注释，按『行内最后值』补回 ──\n"
        text2 += "# 事故与判据见 scripts/repair_env_20260927.py 与台账 §100。\n"
        for k, (ln, v, why) in sorted(restore.items()):
            text2 += f"#   {k}: {why}\n"
        for k, (ln, v, why) in sorted(restore.items()):
            text2 += f"{k}={v}\n"

    if not args.apply:
        print("\n[dry-run] 未落盘。加 --apply 执行。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = ROOT / f".env.bak_prerepair_{stamp}"
    shutil.copy2(ENV, bak)
    ENV.write_text(text2, encoding="utf-8", newline="\n")
    print(f"\n[apply] 已备份 {bak.name}；新文件行数 = {len(text2.splitlines())}")
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(str(ENV))
        print(f"[apply] dotenv 解析键数 = {len(vals)}")
        for k in ("KLINE_P0_PERIOD_ALIGN", "MIDLONG_THESIS_INV_REQUIRE_CLOSE", "MLTO_LEARNING_THESIS_FALLBACK"):
            print(f"   {k} = {vals.get(k)!r}")
    except Exception as exc:
        print("[warn] dotenv 校验失败:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
