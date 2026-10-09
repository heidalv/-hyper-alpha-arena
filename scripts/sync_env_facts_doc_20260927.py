# -*- coding: utf-8 -*-
"""把 `docs/RUNTIME_CONFIG_FACTS.md` 里与 `.env` 不一致的行，按**运行值**校正。

`test_config_governance_20260902.py::test_no_drift_between_env_and_facts` 要求文档与 `.env` 一致。
实测 10 个键不一致，两类原因：
  · **文档陈旧**（SL 上限）：`.env` 里 09-24「R8」那条带着完整反事实依据写的是
    `MIDLONG_MAX_SL_PCT_LONG=0.08`（long 层 0.03→0.08，两个样本过全部五条预登记判据，
    对照组 mid 仍 0.03），而 FACTS 文档还停在轮23 的 0.015/0.03 —— 以 `.env` 为准，
    同时保留旧值痕迹便于追溯；
  · **历史遗留不一致**（LLM 传输/额度表、两个车道开关）：`git` 无从判断哪侧是意图，
    本脚本**只改文档、不改运行配置**，并把差异原因标在备注里，供人工确认。

⚠️ 本脚本**绝不**修改 `.env`：改运行配置需要单独的证据与授权。
用法：.venv\\Scripts\\python.exe scripts\\sync_env_facts_doc_20260927.py [--apply]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/RUNTIME_CONFIG_FACTS.md"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
STAMP = "[2026-09-27 校对]"


def main() -> int:
    from check_config_drift import _same, load_env, load_facts

    facts = load_facts(DOC)
    env, duplicates = load_env(ROOT / ".env")   # 注意：load_env 返回 (dict, duplicates)
    if duplicates:
        print(f"[warn] .env 存在重复键（会静默覆盖，必须先修）: {sorted(set(duplicates))}")
    drifts = [(k, v, note) for k, v, note in facts if k in env and not _same(env[k], v)]
    print(f"漂移键 = {len(drifts)}")
    for k, v, _ in drifts:
        print(f"   {k}: 文档={v}  →  运行={env[k]}")

    text = DOC.read_text(encoding="utf-8")
    changed = 0
    for k, old, note in drifts:
        new = env[k]
        pat = re.compile(rf"^(\|\s*{re.escape(k)}\s*\|[^|]*\|\s*)([^|]*?)(\s*\|)(.*)$", re.M)
        m = pat.search(text)
        if not m:
            print(f"   [skip] 找不到行 {k}")
            continue
        tail = m.group(4)
        new_tail = f" {STAMP} 文档值此前为 `{old}`，按运行值 `{new}` 校正。{tail.strip()}"
        text = text[: m.start()] + m.group(1) + f" {new} " + m.group(3) + new_tail + text[m.end():]
        changed += 1
    if "--apply" not in sys.argv:
        print(f"\n[dry-run] 将改 {changed} 行；加 --apply 落盘。")
        return 0
    DOC.write_text(text, encoding="utf-8")
    print(f"\n[apply] 已更新 {changed} 行 → {DOC.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
