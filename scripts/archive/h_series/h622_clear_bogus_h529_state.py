"""h622 — **清除误操作留下的 `h529_trial` 状态块**（R223 事故的收尾；只改派生状态，不动审计流）。

事故（2026-09-29 13:20:27L，我的错 ✗）：我把"检查命令行 flag 能不能解析"当成了无害的 dry-check，
实际运行了 `h425 --trial h529 --deploy --force` ⇒ 它**真的部署了**（registry 0.0→0.9、写下
`h529_trial` 块、并建了判定任务 `DSH_HFT_H529_JUDGE`），随后我又跑 `--rollback` ✗。
已做的回退：① 值已回滚为 0.0 ✓；② 判定任务已删除 ✓；③ 已验证**行为零影响** ✓
（运行态根本没有该字段 ⇒ 该次部署被 `__dataclass_fields__` 静默丢弃 ⇒ 车道行为未变 ✓）。

本脚本收尾**最后一项**：删掉那个**从未真正跑过**的 `h529_trial` 状态块 ——
因为它的存在会让 `h527_chain` 的依赖解析（按 `h529_trial.started_at` 判定）**误以为 ③′ 已开始**，
于是 ④ 会去等一个永远不存在的 `h529_verdict.json` ⇒ **永久空转** ✗✗。

**边界（为什么不改 `ops_changes`）**：`ops_changes` 是**审计流**，那 3 条 `h529_deploy/rollback`
**如实记录了发生过的事** ⇒ **不删、不改** ✓（删它才是真正的错 ✗）。它们会造成今晚 ② 的
"窗口内含断点 ⇒ 负向 Δ 降级为 INCONCLUSIVE" —— 这是**我污染的机械后果**，已如实登记在验收文档里 ✓。
本脚本因此**也不追加新的 ops 记录**（那只会让 ② 的窗口再多一条断点 ✗），清除动作只写进文档 ✓。

用法：python scripts/h622_clear_bogus_h529_state.py [--apply]   # 默认预演
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只预演）")
    a = ap.parse_args()
    print("=" * 92)
    print("h622 — 清除误操作留下的 `h529_trial` 状态块（不动 ops_changes）")
    print("=" * 92)
    with psycopg.connect(h.read_env_dsn(), autocommit=False) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        t = dict(meta.get("h529_trial") or {})
        ops = list(meta.get("ops_changes") or [])
        val = ((meta.get("params") or {}).get("limits") or {}).get("ofi_require_threshold")
        print(f"  现状：h529_trial 存在={'h529_trial' in meta} "
              f"started_at={t.get('started_at')} verdict={t.get('verdict')!r}")
        print(f"        params.limits.ofi_require_threshold = {val!r}（应为 0.0 ✓）")
        print(f"        ops_changes 共 {len(ops)} 条（**保持原样** ✓，含我误操作那 3 条）")
        if "h529_trial" not in meta:
            print("\n  ⇒ 无需处理（状态块已不存在）✓")
            return 0
        if val not in (None, 0.0, 0):
            print(f"\n  ✗ 参数值 {val!r} 不是 0.0 ⇒ **先回滚**再运行本脚本（不要带着配置清状态）")
            return 1
        if not a.apply:
            print("\n  [预演] 将执行：删除 meta['h529_trial']，其余字段与 ops_changes 一律不动 ✓")
            print("  ⇒ 加 --apply 真写（会取 h425 的跨进程 meta 锁 ✓）")
            return 0
        with h._MetaLock():
            meta2 = h._load_meta(cur)
            if "h529_trial" in meta2:
                meta2.pop("h529_trial", None)
                h._save(cur, c, meta2)
        meta3 = h._load_meta(cur)
        gone = "h529_trial" not in meta3
        print(f"\n  ✓ 已清除：h529_trial 存在={'h529_trial' in meta3}（期望 False）")
        print(f"  ✓ ops_changes 仍为 {len(meta3.get('ops_changes') or [])} 条"
              f"（**未被改动** ⇒ 审计流完整 ✓）")
        if not gone:
            print("  ✗ 清除失败")
            return 1
    print("\n  下一步核对：`h527_chain --dry` 的依赖解析应回到 **h464**（不再误以为 h529 已开始）✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
