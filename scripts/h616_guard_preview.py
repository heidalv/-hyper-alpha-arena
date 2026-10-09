"""h616 — **部署守卫预览**（只读；R207）：不部署，也能知道"现在部署 h529 会不会被拒、为什么"。

为什么需要：`h425 --deploy` 的守卫有**两个**独立条件（`scripts/h425_repair_trial.py:649-662`）：
  ① 任一 `SERIAL_KEYS` 试跑进行中（`started_at` 有值且 `verdict ∈ {None, INCONCLUSIVE}`）⇒ 拒；
  ② `h411_trial.verdict` 不是 PASS/ROLLBACK（它是"合规兜底"试跑）⇒ **也拒** ✗。
两者都只打一句"部署守卫拒绝" ⇒ 看不出**是哪一条**在挡，也看不出"②终局后是否就放行"。
本脚本把这套判据**照抄一遍**（同一份 `SERIAL_KEYS`、同一份 meta），逐条打印 ✓，
并据此给出 h529 的正确激活命令（要不要 `--force`）。**不写库、不部署** ✓。

用法：python scripts/h616_guard_preview.py [--trial h529]
"""
from __future__ import annotations

import argparse
import importlib.util
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
    ap.add_argument("--trial", default="h529")
    a = ap.parse_args()
    print("=" * 92)
    print(f"h616 — 部署守卫预览：现在部署 `{a.trial}` 会怎样？（只读）")
    print("=" * 92)
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)

    print(f"\n  ① 串行守卫（`SERIAL_KEYS` 共 {len(h.SERIAL_KEYS)} 项）")
    live = []
    for k in h.SERIAL_KEYS:
        t = dict(meta.get(k) or {})
        if t.get("started_at") and t.get("verdict") in (None, "INCONCLUSIVE"):
            live.append((k, t.get("verdict"), t.get("judge_at"), t.get("extend_until")))
    if live:
        print(f"     ✗ **{len(live)} 项进行中** ⇒ 非 `--force` 部署会被拒：")
        for k, v, jd, ex in live:
            print(f"       · {k}: verdict={v!r} judge_at={jd} extend_until={ex}")
    else:
        print("     ✓ 没有进行中的串行试跑 ⇒ 这一条不挡 ✓")

    print("\n  ② 合规兜底守卫（`h411_trial.verdict` 需 PASS/ROLLBACK）")
    v411 = dict(meta.get("h411_trial") or {}).get("verdict")
    ok411 = v411 in ("PASS", "ROLLBACK")
    print(f"     h411_trial.verdict = {v411!r} ⇒ {'✓ 不挡' if ok411 else '✗ **会挡**'}")
    if not ok411:
        print("     （它是「用户硬约束兜底」试跑，标记为 COMPLIANCE_BACKSTOP 之类就永远不满足 ⇒"
              " **任何非 --force 部署都会被它拒** ✗）")

    blocked = bool(live) or not ok411
    print("\n" + "-" * 92)
    print(f"  结论：部署 `{a.trial}` 不带 `--force` ⇒ "
          f"{'**会被拒**（原因见上）✗' if blocked else '**会放行** ✓'}")
    if blocked:
        print("  ⇒ 若要部署，只能用 `--force`；**但 `--force` 会同时绕过①** ⇒"
              " 用之前必须自己确认「没有进行中的串行试跑」：")
        print("     python scripts/h615_activation_preflight.py   # 看 [1] 是否 '已终局 ✓'")
        print(f"     python scripts/h425_repair_trial.py --trial {a.trial} --deploy --force"
              "   # 仅在 [1] 已满足后执行")
    else:
        print(f"  ⇒ 直接：python scripts/h425_repair_trial.py --trial {a.trial} --deploy"
              "（无需 --force ✓）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
