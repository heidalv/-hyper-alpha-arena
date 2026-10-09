"""h615 — h529（饱和要求闸）的**上线前置体检**（只读；R203）。

为什么需要：h529 要改的 `limits.ofi_require_threshold` 是一个**新字段** ——
它只有在 **worker 重启后**才存在于运行态；否则 `LaneRiskLimits(**{k: v for k in
__dataclass_fields__})` 会**静默丢弃**它 ⇒ 部署看似成功、实际空转 ✗（h472 的教训）。
本脚本把"能不能安全上线"逐条查清，并打印**激活顺序**（不执行任何变更 ✓）。

查什么：
  1. **② 是否已终局** —— 未终局就部署 = 落进 ② 的窗口、污染今天刚查清的对比 ✗；
  2. **运行态是否已有该字段** —— 读心跳的 `limits`（`/shadow` 同源）⇒ 直接回答"要不要先重启"；
  3. 心跳新鲜度 + `gate_probe_counts` 是否已发布（旧代码没有）；
  4. ③ 的旧链（`DSH_HFT_H464_CHAIN`）是否仍处于 **Disabled**（避免它抢先部署 h464）✓；
  5. 类里是否有该字段（静态检查，默认路径）✓。

用法：python scripts/h615_activation_preflight.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
STATUS = ROOT / "logs" / "mm_lane_status.json"
FIELD = "ofi_require_threshold"

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

from backend.services.market_maker.core import LaneRiskLimits  # noqa: E402


def task_state(name: str) -> str:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        for ln in (r.stdout or "").splitlines():
            if ln.strip().lower().startswith("status:"):
                return ln.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return "(未知)"


def main() -> int:
    print("=" * 90)
    print("h615 — h529 上线前置体检（只读；不执行任何变更）")
    print("=" * 90)
    blockers = []

    # 1) ② 是否终局
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        t463 = dict(meta.get("h463_trial") or {})
    v463 = t463.get("verdict")
    started = t463.get("started_at")
    live = bool(started) and v463 in (None, "INCONCLUSIVE")
    print(f"\n  [1] ② h463_trial：verdict={v463!r} started_at={started}")
    if live:
        print("      ✗ **仍在窗口内** ⇒ 现在部署会把变更插进 ② 的窗口 ⇒ 必须等它终局")
        blockers.append("② 未终局")
        # [R204] 把"最可能的那个分支"写清楚：21:48 判成 INCONCLUSIVE 时，框架会**延长 13h**
        # （judge_at → 次日 ~10:48）⇒ ② 仍算 live ⇒ 本条**继续挡** h529 ✓（这是对的：
        # 单变量纪律要求等它真终局）。**不要**为赶进度强推部署 ✗。
        print(f"      分支提醒：judge_at={t463.get('judge_at')} "
              f"extend_until={t463.get('extend_until')}")
        print("      · 若 21:48 判成 INCONCLUSIVE ⇒ 窗口延长到次日 ~10:48 ⇒ **本条继续挡** ✓"
              "（等真终局，别强推 ✗）；")
        print("      · 若判成 PASS/ROLLBACK ⇒ 前置立即满足 ⇒ 按下面顺序激活 ✓。")
    else:
        print("      ✓ 已终局（或未开始）⇒ 可以推进")

    # 2) 运行态是否已有新字段（决定性：决定要不要先重启）
    print(f"\n  [2] 运行态字段 `{FIELD}`（读心跳 limits，与 /shadow 同源）")
    try:
        raw = json.loads(STATUS.read_text(encoding="utf-8"))
        import time as _t
        age = _t.time() - float(raw.get("ts") or 0.0)
        lim = dict(raw.get("limits") or {})
        has = FIELD in lim
        print(f"      心跳年龄 {age:.1f}s；limits 键 {len(lim)} 个；"
              f"含 `{FIELD}` = **{has}**")
        gp = raw.get("gate_probe_counts")
        print(f"      `gate_probe_counts` = "
              f"{'（已发布 ✓）' if gp is not None else '（**缺失 ⇒ worker 还是旧代码** ✗）'}")
        if not has:
            print("      ✗ 运行态**没有**该字段 ⇒ **必须先重启 worker**，否则部署=静默空转 ✗")
            blockers.append("运行态缺字段（需重启 worker）")
        else:
            print("      ✓ 运行态已有该字段 ⇒ 可以直接热采用 ✓")
    except Exception as e:  # noqa: BLE001
        print(f"      ✗ 读心跳失败：{type(e).__name__}: {e}")
        blockers.append("心跳不可读")

    # 3) 类里是否有该字段（静态）
    print(f"\n  [3] 类定义 `LaneRiskLimits.__dataclass_fields__`")
    in_cls = FIELD in LaneRiskLimits.__dataclass_fields__
    print(f"      含 `{FIELD}` = **{in_cls}**"
          f"{'' if in_cls else ' ✗（代码没保存？）'}")
    if not in_cls:
        blockers.append("类里没有该字段")

    # 4) ③ 旧链是否仍禁用
    st = task_state("DSH_HFT_H464_CHAIN")
    print(f"\n  [4] ③ 旧链 DSH_HFT_H464_CHAIN 状态 = **{st}**")
    if st.lower().startswith("disabled"):
        print("      ✓ 仍禁用（不会抢先部署 h464 = ofi_confirm_threshold 0.9）✓")
    else:
        print("      ⚠️ 非禁用 ⇒ 它可能在 09:58 部署 h464（那个闸在本配置下空转 ✗）"
              "⇒ 如要保留冻结：Disable-ScheduledTask -TaskName DSH_HFT_H464_CHAIN")
        blockers.append("③ 旧链未禁用")

    # 5) SPEC 就绪
    print(f"\n  [5] SPEC `h529`：{'已注册 ✓' if 'h529' in h.SPECS else '缺失 ✗'}")
    if "h529" in h.SPECS:
        sp = h.SPECS["h529"]
        print(f"      {sp['field']} → {sp['to']}（回滚 {sp['rollback_to']}）；"
              f"任务 {sp['task']}")
    else:
        blockers.append("SPEC h529 缺失")

    print("\n" + "-" * 90)
    if blockers:
        print("✗ 尚有前置未满足：")
        for b in blockers:
            print(f"    · {b}")
    else:
        print("✓ 前置全部满足 ⇒ 可按下面顺序激活 h529")
    print("\n  激活顺序（**等 ② 终局后**；每步都要复核 ✓）：")
    print("    ① 重启 worker（新字段进运行态）——用**现成的窄口径脚本** `h218_restart_worker.py` ✓：")
    print("       `python scripts/h218_restart_worker.py --check`   # 先看：匹配到的链根数应为 1 ✓")
    print("       `python scripts/h218_restart_worker.py`           # 真重启：只杀 `mm_lane_worker` 链")
    print("         （**只按命令行匹配 `mm_lane_worker`** ⇒ 不会碰行情采集器等其它 python 链 ✓✓")
    print("          —— 这一点在本项目是硬要求：采集器脚本已被误删、**进程一停就再起不来** ✗）")
    print("       → 删锁 → `schtasks /Run /Tn DSH_MM_WORKER` → 等心跳 ≤90s（脚本自带复核 ✓）")
    print("       `python scripts/h481_runtime_param_echo.py --key ofi_require_threshold` 应显示")
    print("       『注册表未指定 / 运行态=0.0』且**运行态缺键 0** ✓；")
    print("    ② 影子：θ 仍为 0，跑 `python scripts/h612_confirm_gate_share.py --watch 120` ⇒")
    print("       `gate_probe_counts` 应出现且 **hit=blocked=0**（闸关闭 ⇒ 不计数）✓；")
    print("    ③ 部署：`python scripts/h425_repair_trial.py --trial h529 --deploy --force`")
    print("       ⚠️ **必须带 `--force`**（R207 实测）：框架的部署守卫有**两个**条件，其中")
    print("       `h411_trial.verdict='COMPLIANCE_BACKSTOP'` **永远**不满足 ⇒ 不带 `--force`")
    print("       一定被拒 ✗；而 `--force` **同时绕过**「串行守卫」⇒ 所以**第 [1] 项是唯一的")
    print("       2 窗口保护**，必须先看到它 ✓（`h616_guard_preview.py` 可复核这一结论）")
    print("    ④ 读运行态：`h481 --key ofi_require_threshold` ⇒ 注册表=运行态=0.9 且缺键 0 ✓；")
    print("    ④b **腿量守门（R216，R217 改写理由）**：部署后 **30 分钟内**跑")
    print("        `python scripts/h582_trial_accumulation.py` 看腿速 —— 若 **< 60/h** 立刻回滚：")
    print("        `python scripts/h425_repair_trial.py --trial h529 --rollback --force`")
    print("        （这是**便宜的保险**，不是已知风险的对策 ✓：权威读数 `h483_confirm_oos.py` 给出")
    print("         θ=0.90 ⇒ 两半 **71/h、66/h**、全样本 **67.9/h**，都在 60/h 之上 ✓；")
    print("         我 R216 曾用错口径（取了绝对值、且拿分钟样本比例硬乘腿速）算出 22/h ✗，")
    print("         那个工具已删除 —— 要判 θ 的腿量，**只跑 h483** ✓）")
    print("    ⑤ 行为侧：`h612` ⇒ `ofi_require_blocked` 显著 >0（判据 C ✓）；若为 0 而回显 0.9 ⇒")
    print("       按 F189 处置（试跑作废）✗；")
    print("    ⑥ 判定按 SPEC 的 A–E 走（链/任务由框架调度）。")
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
