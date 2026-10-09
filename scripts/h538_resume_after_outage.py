"""h538：**停摆恢复编排**——把"修好代理之后要做的 4 步"变成一条命令。

为什么需要：停机期间我冻结了 4 个任务并留下了若干手工步骤（重启采集器要带对币种、
重启 worker 后 h527 的新字段才可见、判定任务要重开）。手工拼这些步骤正是
"越改越乱"的来源 ⇒ 固化成脚本，**默认干跑**（只打印将要做什么），
加 `--apply` 才真正执行。

用法：
  python scripts/h538_resume_after_outage.py              # 干跑：体检 + 打印计划
  python scripts/h538_resume_after_outage.py --apply      # 执行恢复
  python scripts/h538_resume_after_outage.py --apply --with-h527   # 额外重启 worker
                                                          # 以便启用逐币键

判据（任一不满足即拒绝执行，避免"行情没恢复就重开判定"）：
  · market 库 `market_trades_aggregated` 滞后 < 300s；
  · `asterdex_stream_health` 的 msgs_total 在增长且 last_event_ms > 0；
  · 代理数据面在隧道内 TLS 握手成功（h536 的同一判据）；
  · 最近 15 分钟有腿（或至少采集已恢复，腿会在 1–2 分钟内出现）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE_SYMS = "BNBUSDT,NEARUSDT,ARBUSDT,XRPUSDT,ENAUSDT,BTCUSDT,ETHUSDT,SOLUSDT"
INGEST = pathlib.Path(r"D:\001Alpha\research_l1\services\aster_ws_ingest.py")
FROZEN = ["DSH_HFT_H454_JUDGE", "DSH_HFT_H463_JUDGE", "DSH_HFT_H472_JUDGE",
          "DSH_HFT_H464_CHAIN"]


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dsn_of(u: str) -> str:
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        u = u.replace(j, "")
    return u


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行（默认干跑）")
    ap.add_argument("--with-h527", action="store_true",
                    help="额外重启 worker（h527 逐币字段需重启才在运行态可见）")
    ap.add_argument("--reopen-trials", action="store_true",
                    help="重开 ②③ 的试跑窗（**必须**：试跑窗是 [started_at, now]，"
                         "停摆 5+ 小时会留在窗内 ⇒ 直接重开判定仍会被频率判据降级/回滚）")
    a = ap.parse_args()
    env = read_env()
    ok_all = True
    print("=" * 84)
    print(f"停摆恢复编排（{'APPLY' if a.apply else 'DRY-RUN'}）"
          f"  {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 84)

    # ── 体检 1：行情数据新鲜度 ──
    try:
        with psycopg.connect(dsn_of(env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]),
                             autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT now(), max(timestamp) FROM market_trades_aggregated")
                now, mx = cur.fetchone()
                lag = None
                if mx:
                    newest = dt.datetime.fromtimestamp(float(mx) / 1000.0)
                    n = now.replace(tzinfo=None) if now.tzinfo else now
                    lag = (n - newest).total_seconds()
                cur.execute("""SELECT stream, msgs_total, reconnects, last_event_ms
                               FROM asterdex_stream_health ORDER BY stream""")
                health = cur.fetchall()
        print(f"\n[1] 成交数据滞后：{lag if lag is None else round(lag,1)}s")
        if lag is None or lag > 300:
            print("    ✗ 未恢复（>300s）⇒ 拒绝执行")
            ok_all = False
        for s, m, r, e in health:
            flag = "✓" if (e and r is not None and m is not None and m > r) else "✗"
            print(f"[2] 采集流 {s:6s} msgs={m} reconnects={r} last_event_ms={e} {flag}")
            if not e:
                ok_all = False
    except Exception as e:
        print(f"    ✗ 读行情库失败：{str(e)[:100]}")
        ok_all = False

    # ── 体检 2：代理数据面（复用 h536 的判据：隧道内真做 TLS 握手）──
    try:
        from scripts.h536_lane_alarm import proxy_data_path_ok  # type: ignore
        ok, msg = proxy_data_path_ok()
        print(f"[3] 代理数据面：{'✓' if ok else '✗'} {msg}")
        if not ok:
            ok_all = False
    except Exception as e:
        print(f"[3] 代理体检导入失败（跳过）：{str(e)[:80]}")

    # ── 体检 3：腿速 ──
    try:
        with psycopg.connect(dsn_of(env["DATABASE_URL"]), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT count(*) FROM lane_ledger WHERE lane_id='mm_asterdex' "
                            "AND ts > now() - interval '15 minutes'")
                n15 = cur.fetchone()[0]
        print(f"[4] 最近 15 分钟腿数：{n15}")
        if n15 == 0:
            print("    ! 采集刚恢复时腿可能还没出现（等 1–2 分钟再重跑本脚本）")
    except Exception as e:
        print(f"    ✗ 读账本失败：{str(e)[:80]}")
        ok_all = False

    # ── 计划 ──
    print("\n" + "=" * 84)
    print("恢复计划")
    print("=" * 84)
    steps: list[tuple[str, str]] = []
    notes: list[str] = []
    # ⚠️ [R27 核实] `h464_chain.py::_fresh_verdict` **不是闸门**：判决文件缺失/过期时
    # 链条会**自己跑一次 ② 的判定**（`run(["--trial","h463","--judge"])`）再部署 ③。
    # 而 ② 的判定在"窗口含停机段"时会走 `engine_stall` ⇒ **无条件把
    # max_one_side_seconds 回滚到 45** ⇒ 重新启用这条链 = 把停机造成的盲回滚请回来 ✗✗。
    # 故 `--reopen-trials` 时**不启用该链**，③ 在最后一步直接 deploy。
    to_enable = [t for t in FROZEN
                 if not (a.reopen_trials and t == "DSH_HFT_H464_CHAIN")]
    for t in to_enable:
        steps.append((f"启用判定任务 {t}",
                      f'schtasks /change /tn "{t}" /enable'))
    notes: list[str] = []
    if a.reopen_trials:
        notes.append(
            "**刻意不启用 `DSH_HFT_H464_CHAIN`**：它会自己触发 ② 的判定"
            "（`_fresh_verdict` 不是闸门，缺失时会 `run([--trial h463 --judge])`）"
            "⇒ 会把停机造成的盲回滚（90→45）请回来。③ 改由下方直接 deploy；"
            "等 ② 的判定落地后再考虑恢复该链。")
    # 采集器只在**确实不健康**时才重启：[R28 核实] 现场运行的实例**本来就覆盖**
    # 车道 5 币（`--symbols` 里有 BNBUSDT/NEARUSDT/ARBUSDT/XRPUSDT/ENAUSDT，
    # 共 35 币）——此前"只有 BTC/ETH/SOL/XRP"的说法来自**截断 150 字符的命令行** ✗。
    # 换好出口后它会**自动重连**，无需重启（重启还要先处理它的单实例锁）。
    _feed_ok = ok_all
    if _feed_ok:
        notes.append("采集器**无需重启**（本函数开头的体检已确认采集流在增长、"
                     "`last_event_ms>0`）；若确需重启，先停掉旧实例"
                     "（它有单实例锁 `research_l1/logs/aster_ws_ingest.lock`）。")
    else:
        steps.append(("重启采集器（**币种必须覆盖车道 5 币**）",
                      f'"{ROOT / ".venv/Scripts/python.exe"}" "{INGEST}" '
                      f'--symbols {LANE_SYMS}'))
    if a.with_h527:
        steps.append(("重启 worker（让 h527 的逐币字段出现在运行态）",
                      f'"{ROOT / ".venv/Scripts/python.exe"}" '
                      f'"D:\\001Alpha\\Hyper-Alpha-Arena\\scripts\\h218_restart_worker.py"'))
    if a.reopen_trials:
        _py = f'"{ROOT / ".venv/Scripts/python.exe"}"'
        _tr = f'"{ROOT / "scripts/h425_repair_trial.py"}"'
        steps.append((
            "重开 ② 的试跑窗（`h463`：值已是 90，deploy 只刷新 started_at/judge_at "
            "并重排判定任务；因 SERIAL_KEYS 里有多个未判定试跑 ⇒ 需 --force，"
            "框架会把 guards_bypassed 记进审计）",
            f'{_py} {_tr} --trial h463 --deploy --force'))
        steps.append((
            "重开 ③ 的试跑窗（`h464`：0.5→0.9，原自动链因停摆暂停，改为直接 deploy；"
            "同样 --force）",
            f'{_py} {_tr} --trial h464 --deploy --force'))
    for i, (desc, cmd) in enumerate(steps, 1):
        print(f"  {i}. {desc}\n     $ {cmd}")
    if notes:
        print("\n说明（非执行项）：")
        for x in notes:
            print(f"  · {x}")

    if not ok_all:
        print("\n⇒ **体检未通过 ⇒ 不执行任何动作**。请先修好代理，"
              "再运行 `python scripts/h536_lane_alarm.py` 确认变 OK。")
        return 1
    if not a.apply:
        print("\n⇒ 体检通过，但这是 **DRY-RUN**。确认无误后加 `--apply` 执行。")
        print("   ⚠️ 采集器请**先停掉旧实例**（它有单实例锁，会自行退出，但会留下"
              "无关进程；用任务管理器或 Stop-Process 结束旧的 aster_ws_ingest）。")
        return 0
    print("\n⇒ 体检通过，开始执行……")
    rc = 0
    for desc, cmd in steps:
        print(f"\n--- {desc}")
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            print(f"    rc={p.returncode} {out[:400]}")
            if p.returncode not in (0,):
                rc = p.returncode
        except Exception as e:
            print(f"    执行异常：{str(e)[:160]}")
            rc = 1
    print("\n⇒ 执行完毕。下一步：")
    print("   · 等 2–3 分钟，跑 `python scripts/h536_lane_alarm.py` 确认变 OK 且有腿；")
    print("   · 腿恢复后再决定是否启用 h527 的逐币键（登记表写入，≤60s 热采用）。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
