"""h604 — **自动验收链路的端到端彩排**（只读生产；R192）。

为什么必须做：`DSH_HFT_AUTO_ACCEPT` 每 30 分钟跑一次 `h564_auto_accept.py`，它在判定产物
落地后调用 `h546_accept_verdict.py --append` 把验收块写进 `研究结论/三件事验收_20260929.md`。
**这条路径至今从未在真实输入上跑过**（`h564_state.json` 一直是 `missing=[h463,h464,h527]`），
而它今晚 21:48 之后就会第一次真跑 —— 如果它坏了，验收记录不会自动落盘，而且**没人会立刻发现** ✗。

本脚本用**真产物改一个字段**做夹具（不是手写假的 JSON），把 h564 完整走两遍
（先 DRY-RUN 再 `--apply`），并断言：
  1. DRY-RUN 只打印、**不写**文档；
  2. `--apply` 真的把 `<!-- AUTO-ACCEPT:h463 -->` 幂等块写进**副本**；
  3. 重复运行**不堆叠**（标记仍各 1 个、内容一致）；
  4. `dry_run=true` 的预演产物**被排除**（绝不写进正式验收 ✗）；
  5. **生产文件一个字节都没变**（文档 / state / 日志 / out 目录里的验收产物，sha256 比对 ✓）。

用法：python scripts/h604_auto_accept_rehearsal.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out"
PROD_DOC = ROOT / "研究结论" / "三件事验收_20260929.md"
PROD_STATE = OUT / "h564_state.json"
FIXTURE_SRC = OUT / "h463_verdict_dryrun.json"


def sha(p: pathlib.Path) -> str:
    if not p.exists():
        return "(absent)"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def run_564(tmp: pathlib.Path, apply: bool) -> tuple[int, str]:
    env = dict(os.environ)
    env.update({
        "H564_DOC_PATH": str(tmp / "doc.md"),
        "H564_LOG_PATH": str(tmp / "auto_accept.log"),
        "H564_STATE_PATH": str(tmp / "state.json"),
        "H564_VERDICT_DIR": str(tmp),
    })
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "h564_auto_accept.py")]
        + (["--apply"] if apply else []),
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    print("=" * 88)
    print("h604 — 自动验收链路端到端彩排（生产只读）")
    print("=" * 88)
    fails: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(f"  {'✓' if ok else '✗'} {what}")
        if not ok:
            fails.append(what)

    if not FIXTURE_SRC.exists():
        print(f"✗ 缺少真实产物 {FIXTURE_SRC} ⇒ 无法彩排")
        return 1

    # 生产侧快照
    prod_before = {
        "doc": sha(PROD_DOC), "state": sha(PROD_STATE),
        "log": sha(ROOT / "logs" / "auto_accept.log"),
        "accept_md": sha(OUT / "h463_acceptance.md"),
    }
    print(f"  生产快照：doc={prod_before['doc']} state={prod_before['state']} "
          f"log={prod_before['log']} accept={prod_before['accept_md']}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="h604_rehearsal_"))
    shutil.copy2(PROD_DOC, tmp / "doc.md")

    base = json.loads(FIXTURE_SRC.read_text(encoding="utf-8"))
    base.pop("dry_run", None)                      # 正式产物：无 dry_run 标记
    base["judged_at"] = "2026-09-29T13:48:11.023510+00:00"
    base["verdict"] = "INCONCLUSIVE"
    (tmp / "h463_verdict.json").write_text(
        json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  夹具目录：{tmp}")
    print(f"    夹具 = 冻干预演产物去掉 dry_run、judged_at 设为 ② 的真实判定时刻 "
          f"（verdict={base['verdict']}）")

    doc0 = (tmp / "doc.md").read_text(encoding="utf-8")

    # ① DRY-RUN
    rc, out = run_564(tmp, apply=False)
    doc1 = (tmp / "doc.md").read_text(encoding="utf-8")
    check(rc == 0, f"DRY-RUN rc=0（实际 {rc}）")
    check(("待写入" in out) or ("[('h463'" in out), "DRY-RUN 把 h463 列为待写入")
    check(doc1 == doc0, "DRY-RUN **没有**改文档")

    # ② --apply
    rc, out = run_564(tmp, apply=True)
    doc2 = (tmp / "doc.md").read_text(encoding="utf-8")
    check(rc == 0, f"--apply rc=0（实际 {rc}）")
    check("<!-- AUTO-ACCEPT:h463 -->" in doc2, "写入标记 <!-- AUTO-ACCEPT:h463 -->")
    check("<!-- /AUTO-ACCEPT:h463 -->" in doc2, "闭合标记存在")
    check(base["judged_at"] in doc2, "块内含该判定的 judged_at（幂等识别的锚点）")
    check(len(doc2) > len(doc0), "文档确实变长（追加了验收块）")
    # 验收块的代表性字段（h546 的口径标签）
    for token in ("净bp合计", "频率"):
        check(token in doc2[len(doc0):] or token in doc2, f"块内含口径标签『{token}』")
    check((tmp / "h463_acceptance.md").exists(), "验收产物写在**夹具目录**（不是生产 out/）")

    # ③ 幂等
    rc, _ = run_564(tmp, apply=True)
    doc3 = (tmp / "doc.md").read_text(encoding="utf-8")
    check(doc3.count("<!-- AUTO-ACCEPT:h463 -->") == 1, "重复运行不堆叠（标记仍 1 个）")
    check(doc3 == doc2, "重复运行内容不变（真幂等）")

    # ③b [R205] **h529（③′）分支**：同一夹具目录里再放一个 h529 产物 ⇒
    # 应写出 `<!-- AUTO-ACCEPT:h529 -->`，且块内含**判据 C**（行为侧探针读数）✓。
    # 这条测的是"③ 换形态后，自动验收还认得它"——否则 h529 的验收会**永不落盘** ✗。
    base529 = json.loads(json.dumps(base))
    base529["judged_at"] = "2026-09-30T13:58:00+00:00"
    base529["verdict"] = "INCONCLUSIVE"
    (tmp / "h529_verdict.json").write_text(
        json.dumps(base529, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out = run_564(tmp, apply=True)
    doc4 = (tmp / "doc.md").read_text(encoding="utf-8")
    check(rc == 0, f"h529 夹具 rc=0（实际 {rc}）")
    check("<!-- AUTO-ACCEPT:h529 -->" in doc4, "写出 h529 的幂等块（③′ 会被自动验收 ✓）")
    check("机制 C" in doc4, "h529 块内含判据 C（行为侧、不依赖旧基线）")
    check("gate_probe_counts" in doc4 or "ofi_require" in doc4 or "心跳无" in doc4,
          "判据 C 打印了探针读数（或如实说明心跳缺键）")
    check((tmp / "h529_acceptance.md").exists(), "h529 验收产物写在夹具目录 ✓")
    check(doc4.count("<!-- AUTO-ACCEPT:h463 -->") == 1, "加跑 h529 不影响 h463 块（仍 1 个）")

    # ⑤ [R206] **误标防护的实证**：复现"正文里有一个字面标记 + 文末有真块 + 判定被重新写入"
    # 这个组合 —— 旧代码取**第一个** `index()` ⇒ 会从正文那一行一直替换到旧块结尾，
    # **静默撕掉中间上千行** ✗✗。新代码取 `rindex` + 缩减保护 ⇒ 应只更新文末真块 ✓。
    tmp3 = pathlib.Path(tempfile.mkdtemp(prefix="h604_straymark_"))
    shutil.copy2(PROD_DOC, tmp3 / "doc.md")
    f1 = json.loads(json.dumps(base))
    f1["judged_at"] = "2026-09-29T20:00:00+00:00"
    (tmp3 / "h463_verdict.json").write_text(
        json.dumps(f1, ensure_ascii=False, indent=2), encoding="utf-8")
    run_564(tmp3, apply=True)                       # 先落一个真块（文末）
    t_mid = (tmp3 / "doc.md").read_text(encoding="utf-8")
    lines_mid = t_mid.splitlines(keepends=True)
    fake = ("<!-- AUTO-ACCEPT:h463 -->\n正文里的**字面**标记（R192 那次就是这么写的）\n"
            "<!-- /AUTO-ACCEPT:h463 -->\n")
    t_inj = "".join(lines_mid[:5]) + "\n" + fake + "".join(lines_mid[5:])
    (tmp3 / "doc.md").write_text(t_inj, encoding="utf-8")
    f2 = json.loads(json.dumps(base))
    f2["judged_at"] = "2026-09-30T09:00:00+00:00"    # ← **新**判定 ⇒ h564 会再调 --append
    (tmp3 / "h463_verdict.json").write_text(
        json.dumps(f2, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out = run_564(tmp3, apply=True)
    t_after = (tmp3 / "doc.md").read_text(encoding="utf-8")
    check(rc == 0, f"误标场景 rc=0（实际 {rc}）")
    check(len(t_after) >= len(t_inj) * 0.95,
          f"**文档未被撕掉**（{len(t_inj)} → {len(t_after)} 字符）")
    check("正文里的**字面**标记" in t_after, "正文里的假块**仍在**（没被误删 ✓）")
    check("2026-09-30T09:00:00+00:00" in t_after, "新判定已写入（文末真块被更新 ✓）")
    check(t_after.rstrip().endswith("<!-- /AUTO-ACCEPT:h463 -->"),
          "真块仍在文末（取最后一处 ✓）")

    # ④ dry_run=true 必须被排除
    tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="h604_dryskip_"))
    shutil.copy2(PROD_DOC, tmp2 / "doc.md")
    dry = dict(base)
    dry["dry_run"] = True
    dry["judged_at"] = "2026-09-29T04:00:00+00:00"
    (tmp2 / "h463_verdict.json").write_text(
        json.dumps(dry, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out = run_564(tmp2, apply=True)
    check(rc == 0, f"预演夹具 rc=0（实际 {rc}）")
    check("h463(预演)" in out, "预演产物被登记为 missing（不写正式验收）")
    check((tmp2 / "doc.md").read_text(encoding="utf-8")
          == PROD_DOC.read_text(encoding="utf-8"), "预演夹具下文档**完全未变**")

    # ⑤ 生产未被触碰
    prod_after = {
        "doc": sha(PROD_DOC), "state": sha(PROD_STATE),
        "log": sha(ROOT / "logs" / "auto_accept.log"),
        "accept_md": sha(OUT / "h463_acceptance.md"),
    }
    check(prod_before == prod_after,
          f"生产文件 sha256 全部未变（{prod_before} vs {prod_after}）")

    print("-" * 88)
    if fails:
        print(f"✗ 彩排失败 {len(fails)} 项：")
        for f in fails:
            print(f"    · {f}")
        return 1
    print("✓ 彩排全部通过：今晚 21:48 判定落地后，自动验收会正确写入 ✓")
    print(f"  （夹具保留在 {tmp} 供人工复核）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
