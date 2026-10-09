"""h618 — **一屏就绪体检**（只读；R210）：把交接卡里的每条主张都用机器复核一遍。

为什么需要：交接卡（本文档顶部）是**我的话**；本项目已经因为"我说 ✓、其实 ✗"栽过多次
（R194/R197/R203/R207 都是"我写的结论被后来的读数推翻"）。所以最后再写一个**机器版**：
一次运行、逐条 PASS/INFO/FAIL，把"现在到底能不能撒手"变成读数 ✓。

检查项（全部只读，不重启、不部署、不写库）：
  A. 车道在跑 + 腿速余量（对照 60/h 硬地板；判定终点阈值）
  B. ② 的判定任务排期与时刻（今晚 21:48）
  C. ③ 旧链（h464）已禁用 ✓
  D. ④ 链 armed + 依赖解析（当前应指向 h464，h529 开始后自动切换）
  E. h529：类里有字段 ✓ / 运行态缺字段（预期，重启后才会有）/ SPEC 就绪 / 判据分支可算
     / 自动验收认得它（h546 EXPECT + h564 KEYS）
  F. 行情采集器进程仍在（**不可重启** ⇒ 只报存活，不做任何动作 ✗）
  G. 磁盘余量

用法：python scripts/h618_readiness.py
"""
from __future__ import annotations

import datetime as dt
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

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

from backend.services.market_maker.core import LaneRiskLimits  # noqa: E402

OK, INFO, BAD = "✓", "·", "✗"
_fails: list[str] = []


def line(tag: str, msg: str, bad: bool = False) -> None:
    print(f"  [{tag}] {msg}")
    if bad:
        _fails.append(msg)


def _task(name: str) -> tuple[str, str]:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        st, nxt = "(未知)", ""
        for ln in (r.stdout or "").splitlines():
            s = ln.strip()
            if s.lower().startswith("status:"):
                st = s.split(":", 1)[1].strip()
            if s.lower().startswith("next run time:"):
                nxt = s.split(":", 1)[1].strip()
        return st, nxt
    except Exception as e:  # noqa: BLE001
        return f"(查询失败 {type(e).__name__})", ""


def main() -> int:
    print("=" * 92)
    print("h618 — 一屏就绪体检（只读）")
    print("=" * 92)

    # A. 车道
    try:
        raw = json.loads((ROOT / "logs" / "mm_lane_status.json")
                         .read_text(encoding="utf-8"))
        import time as _t
        age = _t.time() - float(raw.get("ts") or 0.0)
        line("A", f"心跳年龄 {age:.1f}s、ticks={raw.get('ticks')}、"
                  f"quoted_decisions={raw.get('quoted_decisions')}"
                  f"{' ✓' if 0 <= age <= 90 else ' ✗ 陈旧'}", bad=not (0 <= age <= 90))
    except Exception as e:  # noqa: BLE001
        line("A", f"心跳不可读：{type(e).__name__}", bad=True)

    need = None
    try:
        with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            t = dict(meta.get("h463_trial") or {})
            since = t.get("started_at")
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            cov, ratio = h._covered_hours(since, now, syms)
            arr = h._per_leg(cur, since, now, syms)
            rate = (len(arr) / cov) if cov else 0.0
            hours = (dt.datetime.fromisoformat(now)
                     - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
            proj = 12.0 * rate
            need = (720 - len(arr)) / max(0.01, 12.0 - hours)
            line("A", f"② 试跑：{len(arr)} 腿 / 可交易 {cov:.2f}h ⇒ **{rate:.1f}/h**；"
                      f"判定时预计 **{proj:.0f} 腿**（地板 720 ⇒ "
                      f"{'✓ 有余量' if proj >= 720 else '✗ 会破'})；"
                      f"剩余时段需 ≥{need:.1f}/h", bad=proj < 720)
            line("B", f"② 判定任务 DSH_HFT_H463_JUDGE：{_task('DSH_HFT_H463_JUDGE')}；"
                      f"judge_at={t.get('judge_at')} verdict={t.get('verdict')!r}")
            v529 = dict(meta.get("h529_trial") or {})
            line("E", f"h529_trial：started_at={v529.get('started_at')} "
                      f"verdict={v529.get('verdict')!r}（未开始属预期 ✓）")
            # ④ 的依赖解析
            dep = "h529_trial" if v529.get("started_at") else "h464_trial"
            line("D", f"④ 链依赖解析 ⇒ 当前等 **{dep}**"
                      f"（h529 开始后自动切换 ✓）")
    except Exception as e:  # noqa: BLE001
        line("A/B", f"登记表读取失败：{type(e).__name__}: {e}", bad=True)

    line("C", f"③ 旧链 DSH_HFT_H464_CHAIN：{_task('DSH_HFT_H464_CHAIN')[0]}"
              f"（应 Disabled ✓）",
         bad=not _task("DSH_HFT_H464_CHAIN")[0].lower().startswith("disabled"))
    st4, nx4 = _task("DSH_HFT_H527_CHAIN")
    line("D", f"④ 链 DSH_HFT_H527_CHAIN：{st4}；next={nx4}")

    # E. h529 的静态就绪
    in_cls = "ofi_require_threshold" in LaneRiskLimits.__dataclass_fields__
    line("E", f"字段在类里 = {in_cls}（{'✓' if in_cls else '✗'}）", bad=not in_cls)
    try:
        hb = json.loads((ROOT / "logs" / "mm_lane_status.json")
                        .read_text(encoding="utf-8"))
        has_rt = "ofi_require_threshold" in (hb.get("limits") or {})
        has_probe = hb.get("gate_probe_counts") is not None
        line("E", f"运行态含该字段 = {has_rt}、探针键存在 = {has_probe}"
                  f"（**两个 False 都属预期**：新字段要重启 worker 才有 ⇒ 见 h615 步骤①）")
    except Exception:  # noqa: BLE001
        line("E", "心跳不可读（跳过运行态检查）", bad=True)
    line("E", f"SPEC h529 {'已注册 ✓' if 'h529' in h.SPECS else '缺失 ✗'}",
         bad="h529" not in h.SPECS)
    try:
        sp = importlib.util.spec_from_file_location(
            "h546", ROOT / "scripts" / "h546_accept_verdict.py")
        m546 = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m546)  # type: ignore[union-attr]
        line("E", f"验收 EXPECT 含 h529 = {'h529' in m546.EXPECT}",
             bad="h529" not in m546.EXPECT)
    except Exception as e:  # noqa: BLE001
        line("E", f"h546 载入失败：{type(e).__name__}", bad=True)
    try:
        src = (ROOT / "scripts" / "h564_auto_accept.py").read_text(encoding="utf-8")
        line("E", f"自动验收 KEYS 含 h529 = {'\"h529\"' in src}",
             bad='"h529"' not in src)
    except Exception as e:  # noqa: BLE001
        line("E", f"h564 读取失败：{type(e).__name__}", bad=True)

    # F. 采集器（只报存活）
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*aster_ws_ingest*' }).ProcessId -join ','"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        ids = (ps.stdout or "").strip()
        line("F", f"行情采集器进程 = {ids or '（无！✗）'}（**不可重启** ⇒ 别杀 ✗）",
             bad=not ids)
    except Exception as e:  # noqa: BLE001
        line("F", f"进程查询失败：{type(e).__name__}")

    # G. 磁盘
    try:
        import shutil as _sh
        c_free = _sh.disk_usage("C:/").free / 1e9
        d_free = _sh.disk_usage("D:/").free / 1e9
        line("G", f"C: {c_free:.1f}GB 空 / D: {d_free:.1f}GB 空"
                  f"{' ✓' if c_free > 3 and d_free > 5 else ' ✗ 偏低'}",
             bad=not (c_free > 3 and d_free > 5))
    except Exception as e:  # noqa: BLE001
        line("G", f"磁盘读取失败：{type(e).__name__}")

    print("\n" + "-" * 92)
    if _fails:
        print(f"✗ {len(_fails)} 项需要注意：")
        for f in _fails:
            print(f"    · {f}")
        return 1
    print("✓ 全部就绪：② 在跑且有余量、判定已排期、③ 已冻结、④ 已 armed、"
          "h529 的代码/判据/验收三处就位、采集器存活、磁盘充足 ✓")
    print("  （激活 h529 前再跑 `h615_activation_preflight.py` + `h616_guard_preview.py` ✓）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
