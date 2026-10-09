"""h463 → h464 串行链（单变量隔离下的自动推进）。

为什么需要串行：
  两个试跑都改**同一本账**的净/腿（h463 改加仓封锁年龄上限，h464 改顺势确认阈）。
  若同时上线，判定窗口互相污染 ⇒ 谁都说不清是哪个参数起的作用。
  所以：h463 判定（14:20）落地后，本链再部署 h464。

本脚本做三件事（幂等，可重跑）：
  1. 若 `research_l1/out/h463_verdict.json` 仍早于 h463 部署时刻 ⇒ 自己跑一次判定；
  2. 打印 h463 判定摘要（verdict / 关键指标）；
  3. `--deploy --force` 部署 h464（并自动排它的判定任务）。

用法：python scripts/h464_chain.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TRIAL = ROOT / "scripts" / "h425_repair_trial.py"
# [R69] 测试钩子：离线用例必须能改判**输出**路径，否则测试会读/写生产产物
# （R41/R57 的教训：钩子要覆盖执行者的**全部**落盘/读取路径）。默认仍是生产路径。
VERDICT = pathlib.Path(os.environ.get("H464_VERDICT_PATH")
                       or (ROOT / "research_l1" / "out" / "h463_verdict.json"))
LOG = pathlib.Path(os.environ.get("H464_LOG_PATH")
                   or (ROOT / "research_l1" / "out" / "h464_chain.log"))


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(args: list) -> tuple:
    p = subprocess.run([str(PY), str(TRIAL), *args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_script(rel: str, args: list) -> tuple:
    """跑仓库里任意脚本（用于部署后的运行态验证 h481）。"""
    p = subprocess.run([str(PY), str(ROOT / rel), *args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _fresh_verdict() -> dict | None:
    """若 h463 的判定已在别处（14:20L 的任务）落地且足够新鲜，则复用、不重复跑。

    为什么：DSH_HFT_H463_JUDGE 排在 14:20L，本链在 14:35L ⇒ 若再跑一次判定，
    既浪费也会把 INCONCLUSIVE 的"+12h 再判定"排第二遍（同名 /F 覆盖，噪声但无益）；
    更糟的是若第一次判定已回滚参数，第二次会拿同一窗口再判一次，结论虽同但
    审计里出现两条 judge 记录，归因变脏。
    """
    if not VERDICT.exists():
        return None
    age_h = (time.time() - VERDICT.stat().st_mtime) / 3600.0
    try:
        v = json.loads(VERDICT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    # [R69] **终局判决不过期**：`PASS`/`ROLLBACK` 是定论，重跑判定不会得到更好的结论，
    # 只会**把原窗口覆盖成更长的窗**。现场缺口：若本链因隔离闸改期（例如 ③ 为避开
    # 薄夜而挪到次日白天），触发时 `age_h` 早已 >2h ⇒ 旧逻辑判为"不新鲜"⇒ 链自己跑
    # 一次 `--trial h463 --judge`，而此刻 `since` 未变、`now` 已是次日 ⇒ ② 的**干净
    # 12h 白天窗被覆盖成 24h 昼夜混合窗**（R68 已量化昼夜对 `legs_per_trip` 影响 ≈2.4×）
    # ⇒ ② 的验收记录失真。故：终局判决无条件复用；只有**非终局**判决才要求新鲜度。
    if str(v.get("verdict") or "") in ("PASS", "ROLLBACK"):
        return v
    if age_h <= 2.0 and float(v.get("hours") or 0) >= 10.0:
        return v
    return None


def _trial_state() -> dict:
    """读 ② 的试跑状态——**权威源是登记表 `h463_trial`**（判决写在那里）。

    R37：`do_judge` 在判定后会把 `{**trial, "verdict": ...}` 写回 `meta[h463_trial]`，
    并在 INCONCLUSIVE 时把 `judge_at` 延长 +13h（`EXTEND_HOURS`）⇒ 这两项合起来
    足以判断"② 是否还开着"。
    """
    try:
        import psycopg
        sys.path.insert(0, str(ROOT))
        from scripts.h425_repair_trial import LANE, read_env_dsn
        with psycopg.connect(read_env_dsn(), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'h463_trial' FROM lane_registry "
                            "WHERE lane_id=%s", (LANE,))
                row = cur.fetchone()
        return dict(row[0] or {}) if row else {}
    except Exception as exc:  # noqa: BLE001
        log(f"读 h463_trial 失败（视为未知）：{type(exc).__name__}: {exc}")
        return {}


def _gate(st: dict, now: "dt.datetime | None" = None) -> tuple[bool, str]:
    """**单变量隔离闸**：③ 现在能不能部署？

    [R37 2026-09-29] 缺口现场：判定框架在 INCONCLUSIVE 时会把窗口**延长 +13h 并重排判定**
    （`h425_repair_trial.py:1240`）。若此时部署 ③，③ 的部署就落进 ② 的**延长窗**内
    ⇒ ② 的再判定会把 ③ 的部署算作窗口内变更（`h521` 自动断点检测）⇒ 混淆
    ⇒ **两边都判不出来**（正是本链存在的意义被破坏：单变量隔离）。

    规则：只有 ② 的判决是**终局**（PASS / ROLLBACK）才推进 ③；
    其余（未判定 / INCONCLUSIVE 且窗口已延长）⇒ 把本链**改期到 ② 下次判定之后**。

    **有界等待**（防饿死）：若 ② 已延长 ≥2 次（窗跨度 ≥ 38h = 12h 原始 + 2×13h）
    仍未终局，说明它的效应本身可能就是"测不出"（Welch 永远不显著），
    再等也不会变 ⇒ **放行 ③** 并在日志里写明代价（② 的延长窗会被污染）。
    没有这一条，③ 可能被永久饿死（本仓库另一种失败模式：过度保守=什么都不做）。
    """
    v = str(st.get("verdict") or "")
    _now = now or dt.datetime.now(dt.timezone.utc)
    if v in ("PASS", "ROLLBACK"):
        return True, f"② 判决已终局（{v}）"
    if not st:
        return True, "读不到 h463_trial（回退旧行为：直接推进）"
    # [R45] **判定未落地的判别**（否则是新的饿死路径）：若 `verdict` 仍为空、而
    # `judge_at` 早已过去（>30min）⇒ 说明 ② 的判定任务**崩了/没写产物**，
    # 此时应**放行**让链走它自带的补判路径（`_fresh_verdict` 为空 ⇒ 链自己跑一次
    # ② 判定并要求产物存在）——否则闸会永远看到 None、无限改期，③ 永不部署 ✗。
    # `now` 可注入 ⇒ 本函数可离线确定性测试（否则用例会随真实时间翻面）。
    try:
        ja0 = dt.datetime.fromisoformat(str(st.get("judge_at")))
        if ja0.tzinfo is None:
            ja0 = ja0.replace(tzinfo=dt.timezone.utc)
        if not v and _now > ja0 + dt.timedelta(minutes=30):
            return True, (f"⚠️ ② 的判定时刻 {ja0.astimezone():%H:%M} 已过 30 分钟而"
                          f"仍无 verdict ⇒ 判定疑似未落地 ⇒ 放行，由链自带的补判路径处理")
    except Exception:  # noqa: BLE001
        pass
    try:
        sa = dt.datetime.fromisoformat(str(st.get("started_at")))
        if sa.tzinfo is None:
            sa = sa.replace(tzinfo=dt.timezone.utc)
        # [R74] 用**实际窗口结束时刻**（含延长），否则安全网永远看到 12h ⇒ 永不触发
        _we = _window_end(st) or dt.datetime.fromisoformat(str(st.get("judge_at")))
        span_h = (_we - sa).total_seconds() / 3600.0
        if span_h >= 38.0:
            return True, (f"⚠️ ② 已延长 ≥2 次（窗跨度 {span_h:.1f}h）仍未终局 ⇒ 放行 ③"
                          f"（避免永久饿死；代价：② 的延长窗会被 ③ 的部署污染，已记日志）")
    except Exception:  # noqa: BLE001
        pass
    return False, (f"② 尚未终局（verdict={v or 'None'}，窗长/延长见 judge_at）"
                   f"⇒ 现在部署 ③ 会落进 ② 的窗口内，两边都判不出来")


def _window_end(st: dict) -> "dt.datetime | None":
    """② **当前窗口的实际结束时刻** = `max(judge_at, extend_until)`。

    [R74] 缺口现场：本文件的 `_trial_state` docstring 一直写着"INCONCLUSIVE 时把
    `judge_at` 延长 +13h"，但判定代码（`h425_repair_trial.py:1318-1323`）写的是
    **`extend_until`，从不更新 `judge_at`** ⇒ 消费方按一个不存在的行为写的：
      · `_gate` 的**有界等待**（跨度 ≥38h ⇒ 放行，防永久饿死）算的是
        `judge_at − started_at` ⇒ 永远是 12h ⇒ **安全网永远不触发** ✗
      · `_defer_target` 拿到的 `judge_at` 早已过期 ⇒ 每次都退化成 `now+30min` 轮询
    ⇒ 改为取两者**较晚**者；任一字段缺失/非法则退回另一个（防御式，两侧都算过）。
    """
    best = None
    for k in ("judge_at", "extend_until"):
        try:
            t = dt.datetime.fromisoformat(str(st.get(k)))
        except Exception:  # noqa: BLE001
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        if best is None or t > best:
            best = t
    return best


def _defer_target(st: dict, now: "dt.datetime | None" = None) -> dt.datetime:
    """算出链下次触发时刻（纯函数，可离线测）。

    规则：② **当前窗口结束时刻**（`max(judge_at, extend_until)`，见 `_window_end`）
    + 10 分钟缓冲；**若该时刻已过期**（② 早就判过了、或字段缺失/非法）
    ⇒ 改为 `now + 30min`。
    为什么必须挡"过期"：`schtasks /change /st <过去时刻> /sd <今天>` 要么被拒、
    要么排到明天，两种都会让 ③ **静默饿死**（而它正是隔离闸拦下后唯一的复活机制）。
    [R74] 用 `_window_end` 后，INCONCLUSIVE 延长过窗口的试跑会排到**下次判定之后**，
    而不是每 30 分钟空转轮询。
    """
    now = now or dt.datetime.now().astimezone()
    t = _window_end(st)
    t = (t.astimezone() + dt.timedelta(minutes=10)) if t else \
        (now + dt.timedelta(minutes=30))
    if t <= now + dt.timedelta(minutes=1):
        t = now + dt.timedelta(minutes=30)
    return t


def _defer_chain(st: dict) -> int:
    """把本链改期到 ② 下次判定之后（+10 分钟缓冲），并**核实真的排上了**。"""
    nxt = _defer_target(st)
    try:
        subprocess.run(
            ["schtasks", "/change", "/tn", "DSH_HFT_H464_CHAIN",
             "/st", nxt.strftime("%H:%M"), "/sd", nxt.strftime("%Y/%m/%d")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        # **必须复核**：schtasks /change 在 run-as 口令为空时会打警告，
        # 只信"命令没报错"可能得到"其实没排上"⇒ ③ 静默饿死。
        q = subprocess.run(["schtasks", "/query", "/tn", "DSH_HFT_H464_CHAIN",
                            "/fo", "LIST", "/v"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        got = ""
        for line in (q.stdout or "").splitlines():
            if line.strip().startswith("Next Run Time:"):
                got = line.split(":", 1)[1].strip()
                break
        log(f"已请求改期到 {nxt:%Y-%m-%d %H:%M}；任务现在报 Next Run Time={got}")
        if not got or got.upper().startswith("N/A"):
            log("✗ 改期未生效 ⇒ 需人工处理（否则 ③ 不会被部署）")
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"改期失败（需人工处理）：{type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    log("=== h463→h464 链启动 ===")
    st = _trial_state()
    log(f"h463_trial: verdict={st.get('verdict')} judge_at={st.get('judge_at')} "
        f"started_at={st.get('started_at')}")
    gate_ok, gate_why = _gate(st)
    log(f"[隔离闸] {'放行' if gate_ok else '拦下'}：{gate_why}")
    if not gate_ok:
        return _defer_chain(st)
    v = _fresh_verdict()
    if v:
        log(f"复用 14:20L 已落地的 h463 判定（{VERDICT.name}，"
            f"hours={v.get('hours'):.1f}）：verdict={v.get('verdict')} why={v.get('why')}")
    else:
        rc, out = run(["--trial", "h463", "--judge"])
        log(f"h463 判定 rc={rc}\n{out.strip()[-2000:]}")
        if rc not in (0, 1, 2) or not VERDICT.exists():
            log("h463 判定未产出 verdict，终止链（人工检查）")
            return 1
        try:
            v = json.loads(VERDICT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            v = {}
    try:
        log("h463 verdict 摘要: " + json.dumps(
            {k: v.get(k) for k in ("verdict", "why", "freq_ok", "welch")},
            ensure_ascii=False)[:600])
    except Exception as exc:  # noqa: BLE001
        log(f"读 verdict 失败（不阻塞）：{exc}")

    rc2, out2 = run(["--trial", "h464", "--deploy", "--force"])
    log(f"h464 部署 rc={rc2}\n{out2.strip()[-1200:]}")
    if rc2 != 0:
        log("✗ h464 部署失败 ⇒ 跳过运行态验证")
        return 1
    # [h481] 部署后**必须**读运行态验证（热采用 ≤60s，等 75s 再读更稳）
    log("等待 75s 让热采用生效，然后读运行态回显…")
    time.sleep(75.0)
    rc3, out3 = run_script("scripts/h481_runtime_param_echo.py",
                           ["--key", "ofi_confirm_threshold"])
    log(f"运行态验证 rc={rc3}\n{out3.strip()[-1500:]}")
    if "✓ 一致" not in out3:
        log("⚠ 运行态回显未显示『✓ 一致』⇒ 部署可能未生效（检查 env 白名单/是否需重启）")
    else:
        log("✓ h464 运行态验证通过（注册表 = 运行态）")
    log("=== 链结束 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
