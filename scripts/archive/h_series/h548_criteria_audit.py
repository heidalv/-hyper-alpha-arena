"""h548：**判据实现审计**——所有 SPEC 的 `criteria` 是否真有代码去算？

为什么需要：R32 连续撞到两个"判据写了但没实现"的缺口
（h463 的「30–60s 桶」、h527 的逐币机制指标）——判定时会**静默不报**这些口径，
验收就只剩总体净/腿，等于判据形同虚设。同类缺陷应当一次查全。

本脚本（只读）对每个 SPEC：
  1. 抽出 `criteria` 里的**机制项**（含"机制/子口径/占比/数/应"等关键词的子句）；
  2. 检查 `_sub_stats` 是否有该 key 的分支；
  3. 若有分支，列出它实际产出的**顶层键**（与判据里的名词对照）；
  4. 打出"机制判据无实现"的清单（这些是必须在判定前补的）。

用法：python scripts/h548_criteria_audit.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIAL = ROOT / "scripts" / "h425_repair_trial.py"
MECH_HINT = ("机制", "子口径", "占比", "腿数", "净额", "markout", "taker", "陈旧",
             "往返", "P50", "名义", "止损", "入场腿")
# [R66] 已作废的 SPEC：判据缺口无需再补（h448 的取值问题已由 h464 链条上的 0.9
# 部署取代——0.7 是 h447 网格里的**不存在点**，p=0.766）。
SUPERSEDED = {
    "h448": "已由 h464 的 ofi_confirm_threshold 0.5→0.9 部署取代（h447 网格无 0.70 点）",
}


def main() -> int:
    src = TRIAL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 1) 取 SPECS 字面量（AST 求值，避免正则踩注释）
    specs = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "SPECS" for t in node.targets):
            specs = ast.literal_eval(node.value)
            break
    if not specs:
        print("✗ 未能解析 SPECS")
        return 1
    # 2) 取 _sub_stats 里出现的 key 分支与其产出的顶层键
    # [R66 2026-09-29] 两处探测盲区修正：
    #   ① 原实现只认 `key == "h4xx"`（Eq + Constant）⇒ `key in ("h426","h429")`
    #      这种**元组分支**（为合并同口径实现而写）被判成"无实现"。现同时支持
    #      Eq/Constant 与 In/(Tuple|List|Set)[Constant]。（h548 就这么把已实现的
    #      h426/h429 报成"✗ 缺"。）
    #   ② 原实现从 `Compare` 节点往下 walk 取 `out[...]`，而 `out[...]` 在**外层 If
    #      的 body** 里 ⇒ 该列恒为空。现改为遍历 `If` 节点，从 `If.body` 取。
    branches: dict[str, set[str]] = {}

    def _keys_of(test: ast.AST) -> list[str]:
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "key"):
            return []
        out_keys: list[str] = []
        for op, comp in zip(test.ops, test.comparators):
            if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                    and isinstance(comp.value, str):
                out_keys.append(comp.value)
            elif isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                out_keys += [e.value for e in comp.elts
                             if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return out_keys

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_sub_stats":
            for sub in ast.walk(node):
                if not isinstance(sub, ast.If):
                    continue
                keys = _keys_of(sub.test)
                if not keys:
                    continue
                outs = {t.slice.value for t in ast.walk(sub)
                        if isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Name) and t.value.id == "out"
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)}
                for k in keys:
                    branches.setdefault(k, set()).update(outs)
    print("=" * 96)
    print("判据实现审计（SPEC.criteria 的机制项 vs `_sub_stats` 实现）")
    print("=" * 96)
    print(f"{'试跑':<7} {'机制项数':<8} {'有实现?':<8} 实际产出的口径键")
    gaps = []
    for key in sorted(specs):
        crit = str(specs[key].get("criteria") or "")
        # 机制项 = 带关键词的子句（按 ；/; 切）
        clauses = [c.strip() for c in re.split(r"[；;]", crit) if c.strip()]
        mech = [c for c in clauses if any(h in c for h in MECH_HINT)]
        has = key in branches
        outs = "、".join(sorted(branches.get(key, ()))) or "—"
        print(f"{key:<7} {len(mech):<8} {'✓' if has else ('—' if not mech else '✗ 缺'):<8} {outs[:60]}")
        if mech and not has:
            gaps.append((key, mech))
    print("\n" + "=" * 96)
    if not gaps:
        print("✓ 所有带机制判据的 SPEC 都有 `_sub_stats` 实现。")
        return 0
    # [R49] **区分"在役缺口"与"休眠项"**：若该 SPEC 的判定任务从未排期（next=N/A 或不存在），
    # 且试跑 meta 里没有 verdict ⇒ 它根本不会被判定 ⇒ 属休眠，**不是当前缺口**。
    # 不区分就会制造假警报（一个总在报"5 个缺口"的工具会被忽略 ✗）。
    import subprocess
    dormant, live = [], []
    for key, mech in gaps:
        task = f"DSH_HFT_{key.upper()}_JUDGE"
        nxt = ""
        try:
            q = subprocess.run(["schtasks", "/query", "/tn", task, "/fo", "LIST", "/v"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=45)
            for line in (q.stdout or "").splitlines():
                if line.strip().startswith("Next Run Time:"):
                    nxt = line.split(":", 1)[1].strip()
                    break
        except Exception:  # noqa: BLE001
            nxt = ""
        (dormant if nxt in ("", "N/A") else live).append((key, mech, nxt))
    if live:
        print(f"✗ **{len(live)} 个在役 SPEC 有机制判据但没有实现**（判定时不会出现在产物里）：")
        for key, mech, nxt in live:
            print(f"\n  [{key}] {specs[key].get('title', '')[:64]}  next={nxt}")
            for m in mech:
                print(f"      · {m[:86]}")
    if dormant:
        # [R66] 休眠项里再分"待补判据"与"**已作废**"：作废的 SPEC 不该继续显示成缺口
        # （否则一个永远清不掉的列表会被当成噪声忽略 ✗）。
        sup = [(k, m, n) for k, m, n in dormant if k in SUPERSEDED]
        todo = [(k, m, n) for k, m, n in dormant if k not in SUPERSEDED]
        if todo:
            print(f"\n· 休眠项 {len(todo)} 个（判定任务从未排期 ⇒ 不会被判定，"
                  f"**不构成当前缺口**；重开其窗口前必须先补判据）：")
            for key, _mech, _nxt in todo:
                print(f"    [{key}] {specs[key].get('title', '')[:58]}")
        if sup:
            print(f"\n· 已作废 {len(sup)} 个（判据缺口**无需**再补）：")
            for key, _mech, _nxt in sup:
                print(f"    [{key}] {SUPERSEDED[key]}")
    print("\n⇒ 处置：在役项在判定前补 `_sub_stats` 分支；休眠项登记在案。")
    return 1 if live else 0


if __name__ == "__main__":
    raise SystemExit(main())
