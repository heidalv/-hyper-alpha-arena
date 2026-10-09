"""h527 串行链 —— ③（`ofi_confirm_threshold` 0.9）终局后部署 ④（逐币单笔规模）。

为什么存在：`h464_chain` 只覆盖 ②→③。④（h527，逐币规模 BNB×0.5 + NEAR/ARB/ARB/ENA×0.35）
属跨文件的独立工作项。没有它，③ 判完之后队列就**停在原地**，直到有人手动启动。

为什么**独立成文件**而不是复用 `h464_chain`：后者明早 09:58 要真跑（②→③），
改它会给关键路径引入风险。本文件按同样（且已被 R69/R73/R74 加固过的）模式写：

  · **隔离闸**：只有 ③ 的判决**终局**（PASS/ROLLBACK）才部署 ④；否则把自己改期到
    ③ 的下次判定之后（`_window_end` = `max(judge_at, extend_until)`，R74）。
  · **有界等待**：③ 延长 ≥2 次（窗跨度 ≥38h）仍未终局 ⇒ 放行（防 ④ 被永久饿死）。
  · **终局判决不过期**（R69）：复用时不再要求"2h 内"，避免链改期到次日时**重跑③判定**
    把它的窗口覆盖成昼夜混合窗。
  · **改期目标永不落在过去**（`schtasks /change /st <过去>` 会被拒或排到明天 ⇒ 静默饿死）。
  · **子进程用绝对路径 + 显式 cwd**（`run-quiet.vbs` 不设工作目录 ⇒ cwd=System32，R75）。
  · **钩子覆盖全部落盘/读取路径**（R41/R57/R73）：`H527_VERDICT_PATH` / `H527_LOG_PATH`。

用法：
    python scripts/h527_chain.py            # 由计划任务调用
    python scripts/h527_chain.py --dry      # 只打印决策，不改期、不部署（人工预检用）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
TRIAL = ROOT / "scripts" / "h425_repair_trial.py"
# 依赖：③ 的判定产物；自身：④ 的判定产物
DEP_VERDICT = pathlib.Path(os.environ.get("H527_DEP_VERDICT_PATH")
                           or (ROOT / "research_l1" / "out" / "h464_verdict.json"))
LOG = pathlib.Path(os.environ.get("H527_LOG_PATH")
                   or (ROOT / "research_l1" / "out" / "h527_chain.log"))
CHAIN = "DSH_HFT_H527_CHAIN"
DEP_KEY = "h464_trial"      # ③ 在登记表里的键
SELF_KEY = "h527_trial"     # ④


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(args: list) -> tuple:
    p = subprocess.run([str(PY), str(TRIAL), *args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_script(rel: str, args: list) -> tuple:
    p = subprocess.run([str(PY), str(ROOT / rel), *args], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _resolve_dep() -> None:
    """[R204] **按状态**解析本链要等的上游试跑（不是按写死的名字 ✗）。

    背景：本链原来等的是 ③ = `h464_trial`（`ofi_confirm_threshold` 0.9）。但 R201/R203 查明
    那个闸在当前配置下**结构性地不可能生效**（被趋势闸抢先）⇒ 用户裁决改为 **`h529`**
    （`ofi_require_threshold`，"要求"口径的新字段）。若本链继续等 `h464`，它**永远不会**
    满足（`h464_verdict.json` 不会出现）⇒ 只能每 30 分钟空转改期 ✗。
    ⇒ 规则：**`h529_trial` 一旦开始，就改等它**；否则沿用 `h464`（向后兼容 ✓）。
    测试钩子 `H527_DEP_VERDICT_PATH` 优先（离线夹具不受影响 ✓）。
    """
    global DEP_KEY, DEP_VERDICT
    if os.environ.get("H527_DEP_VERDICT_PATH"):
        log("依赖：由 H527_DEP_VERDICT_PATH 指定（测试钩子）⇒ 不做状态解析 ✓")
        return
    key, path = "h464_trial", ROOT / "research_l1" / "out" / "h464_verdict.json"
    try:
        import psycopg
        sys.path.insert(0, str(ROOT))
        from scripts.h425_repair_trial import LANE, read_env_dsn
        with psycopg.connect(read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT meta_json->'h529_trial' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
        t529 = dict((row[0] if row else None) or {})
        if t529.get("started_at"):
            key = "h529_trial"
            path = ROOT / "research_l1" / "out" / "h529_verdict.json"
            log("依赖切换：③ 已由 **h529**（要求口径）承接 ⇒ 本链改为等 h529 的终局 ✓")
        else:
            log("依赖：仍是 h464（h529 尚未开始）✓")
    except Exception as exc:  # noqa: BLE001
        log(f"依赖解析失败（沿用默认 h464）：{type(exc).__name__}: {exc}")
    DEP_KEY, DEP_VERDICT = key, path


def _dep_state() -> dict:
    """读 ③ 的试跑状态（权威源：登记表 `h464_trial`）。"""
    try:
        import psycopg
        sys.path.insert(0, str(ROOT))
        from scripts.h425_repair_trial import LANE, read_env_dsn
        with psycopg.connect(read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT meta_json->%s FROM lane_registry WHERE lane_id=%s",
                        (DEP_KEY, LANE))
            row = cur.fetchone()
        return dict(row[0] or {}) if row else {}
    except Exception as exc:  # noqa: BLE001
        log(f"读 {DEP_KEY} 失败（视为未知）：{type(exc).__name__}: {exc}")
        return {}


def _window_end(st: dict) -> "dt.datetime | None":
    """③ 当前窗口的实际结束时刻 = `max(judge_at, extend_until)`（R74）。"""
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


def _gate(st: dict, now: "dt.datetime | None" = None) -> tuple:
    """③ 终局了吗？没终局就拦下（单变量隔离）。

    ⚠️ **R83 现场缺口（dry 预检当场抓到）**：注册表里的 `verdict` 可能是**上一段插曲**
    留下的终局——当前 `h464_trial.verdict='ROLLBACK'`，那是 **R29 把今早那次提前部署
    回滚掉**的记录，而 ③ 的正式试跑要到次日 09:58 才部署 ✗。只判"终局"就放行 ⇒
    **④ 会在 ③ 还没试跑前就部署**，既破坏串行、又污染 ③ 的窗口（两边都判不出来）。
    ⇒ 终局**还必须伴随真实判定产物**（`*_verdict.json`）：人工 `--rollback` 不写产物，
      那属于"有人在场"的情形，应等人工确认，而不是自动推进。
    """
    v = str(st.get("verdict") or "")
    _now = now or dt.datetime.now(dt.timezone.utc)
    _has_artifact = DEP_VERDICT.exists()
    if v in ("PASS", "ROLLBACK"):
        if not _has_artifact:
            return False, (f"⚠️ ③ 的 verdict={v} 但**没有判定产物** `{DEP_VERDICT.name}` "
                           f"⇒ 这是人工回滚/上一段插曲留下的终局，不是本回合的判定 "
                           f"⇒ 等人工确认后再推进 ④（不自动放行）")
        return True, f"③ 判决已终局（{v}）且有判定产物 ✓"
    if not st:
        return True, "读不到 h464_trial（回退旧行为：直接推进）"
    try:
        ja0 = dt.datetime.fromisoformat(str(st.get("judge_at")))
        if ja0.tzinfo is None:
            ja0 = ja0.replace(tzinfo=dt.timezone.utc)
        if not v and _now > ja0 + dt.timedelta(minutes=30):
            return True, (f"⚠️ ③ 的判定时刻 {ja0.astimezone():%H:%M} 已过 30 分钟而仍无 "
                          f"verdict ⇒ 判定疑似未落地 ⇒ 放行，由链自带的补判路径处理")
    except Exception:  # noqa: BLE001
        pass
    try:
        sa = dt.datetime.fromisoformat(str(st.get("started_at")))
        if sa.tzinfo is None:
            sa = sa.replace(tzinfo=dt.timezone.utc)
        _we = _window_end(st) or dt.datetime.fromisoformat(str(st.get("judge_at")))
        span_h = (_we - sa).total_seconds() / 3600.0
        if span_h >= 38.0:
            return True, (f"⚠️ ③ 已延长 ≥2 次（窗跨度 {span_h:.1f}h）仍未终局 ⇒ 放行 ④"
                          f"（避免永久饿死；代价：③ 的延长窗会被 ④ 的部署污染，已记日志）")
    except Exception:  # noqa: BLE001
        pass
    return False, (f"③ 尚未终局（verdict={v or 'None'}）⇒ 现在部署 ④ 会落进 ③ 的窗口内，"
                   f"两边都判不出来")


def _fresh_verdict() -> "dict | None":
    """③ 的判定产物（终局不过期，R69）。"""
    if not DEP_VERDICT.exists():
        return None
    try:
        v = json.loads(DEP_VERDICT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if v.get("dry_run"):
        return None
    if str(v.get("verdict") or "") in ("PASS", "ROLLBACK"):
        return v
    age_h = (time.time() - DEP_VERDICT.stat().st_mtime) / 3600.0
    return v if (age_h <= 2.0 and float(v.get("hours") or 0) >= 10.0) else None


def _defer_target(st: dict, now: "dt.datetime | None" = None) -> dt.datetime:
    now = now or dt.datetime.now().astimezone()
    t = _window_end(st)
    t = (t.astimezone() + dt.timedelta(minutes=10)) if t else (now + dt.timedelta(minutes=30))
    if t <= now + dt.timedelta(minutes=1):
        t = now + dt.timedelta(minutes=30)
    return t


def _parse_next_run(stdout: str) -> "dt.datetime | None":
    """从 `schtasks /query /fo LIST /v` 的输出里解析 Next Run Time（纯函数，可离线测）。

    ⚠️ **R84 修**：首版用字符串匹配复核改期结果——
        `nxt.strftime("%Y/%m/%d")` = `2026/09/30`（月/日**补零**）
        而 schtasks 实际输出 `2026/9/30 22:08:00`（**不补零**）
        ⇒ 匹配必然失败 ⇒ **每次改期都误报"复核未通过"** ✗（假警报会把真问题淹掉）。
    现改为**解析后比较**（容忍补零/不补零、容忍缺秒）。
    """
    for line in (stdout or "").splitlines():
        if line.strip().startswith("Next Run Time:"):
            raw = line.split(":", 1)[1].strip()
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    return dt.datetime.strptime(raw, fmt)
                except Exception:  # noqa: BLE001
                    continue
            return None
    return None


def _defer_chain(st: dict, dry: bool = False) -> int:
    nxt = _defer_target(st)
    if dry:
        log(f"[dry] 会改期到 {nxt:%Y-%m-%d %H:%M}（不执行）")
        return 0
    subprocess.run(["schtasks", "/change", "/tn", CHAIN,
                    "/st", nxt.strftime("%H:%M"), "/sd", nxt.strftime("%Y/%m/%d")],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=60)
    # **必须复核**：run-as 口令为空时 schtasks 会打警告，"命令没报错"≠"真排上了"
    q = subprocess.run(["schtasks", "/query", "/tn", CHAIN, "/fo", "LIST", "/v"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    got = _parse_next_run(q.stdout or "")
    log(f"已请求改期到 {nxt:%Y-%m-%d %H:%M}；任务现在报 Next Run Time="
        f"{'(读不到)' if got is None else got.strftime('%Y-%m-%d %H:%M')}")
    if got is None or abs((got - nxt.replace(tzinfo=None)).total_seconds()) > 120:
        log("⚠️ 改期复核未通过 ⇒ 人工检查该任务（③ 可能被饿死）")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印决策（不改期、不部署）")
    a = ap.parse_args()
    log("=== h527 链开始（③终局 ⇒ 部署 ④：逐币单笔规模）===")
    _resolve_dep()          # [R204] 先按状态解析依赖（h529 优先），再读状态 ✓
    st = _dep_state()
    log(f"{DEP_KEY}: verdict={st.get('verdict')!r} "
        f"started_at={st.get('started_at')} judge_at={st.get('judge_at')} "
        f"extend_until={st.get('extend_until')}")
    gate_ok, why = _gate(st)
    log(f"[隔离闸] {'放行' if gate_ok else '拦下'}：{why}")
    if not gate_ok:
        return _defer_chain(st, dry=a.dry)

    v = _fresh_verdict()
    if v:
        log(f"复用 ③ 的终局判定（{DEP_VERDICT.name}）：verdict={v.get('verdict')} "
            f"why={str(v.get('why'))[:100]}")
    else:
        rc, out = run(["--trial", "h464", "--judge"])
        log(f"h464 判定 rc={rc}\n{out.strip()[-1500:]}")
        if rc not in (0, 1, 2) or not DEP_VERDICT.exists():
            log("h464 判定未产出 verdict，终止链（人工检查）")
            return 1

    if a.dry:
        log("[dry] 到此为止（不部署 ④）")
        return 0
    rc2, out2 = run(["--trial", "h527", "--deploy", "--force"])
    log(f"h527 部署 rc={rc2}\n{out2.strip()[-1200:]}")
    if rc2 != 0:
        log("✗ h527 部署失败 ⇒ 跳过运行态验证")
        return 1
    log("等待 75s 让热采用生效，然后读运行态回显…")
    time.sleep(75.0)
    rc3, out3 = run_script("scripts/h481_runtime_param_echo.py",
                           ["--key", "per_symbol_size_mult"])
    log(f"运行态验证 rc={rc3}\n{out3.strip()[-1500:]}")
    if "✓ 一致" not in out3:
        log("⚠ 运行态回显未显示『✓ 一致』⇒ 部署可能未生效（检查 env 白名单/是否需重启；"
            "逐币 dict 参数是**首次**在生产部署，重点看这里）")
    else:
        log("✓ h527 运行态验证通过（注册表 = 运行态）")
    log("=== 链结束 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
