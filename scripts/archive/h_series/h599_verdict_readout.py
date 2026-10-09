"""h599 — ② 判决的**一键速读**（只读，R149）。

速读卡（文档 R122）列了五格判读，但需要人依次去开产物、跑 `h577`/`h583`/`h568` ✗。
本脚本把**同一套数字一次算齐**，判决落地后只需一条命令：

    python scripts/h599_verdict_readout.py            # 读 research_l1/out/h463_verdict.json
    python scripts/h599_verdict_readout.py --dry      # 读 *_verdict_dryrun.json（预演）

输出五格：
  1) 频率硬闸（可交易口径 ≥60/h？门槛由绝对地板决定 ✓）
  2) verdict / why（并提示"窗口内断点=0 ⇒ 无降级保护"）
  3) 组成匹配性（该比较是 A 白天 vs 试跑白天 ⇒ 判负要认真对待 ✓）
  4) 尾部归因（止损腿名义比 vs 1.15× 阈值 ✓，以及非止损腿均值/中位数）
  5) 机制判据（legs_per_trip ÷ 同体制参照 3.6263 ✓）

用法：见上。可选 `--trial h464`。**只读**：不写任何库、不改产物 ✓。
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import datetime as dt
import importlib.util
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

REF_LPT = 3.6263      # 同体制参照（0.5 体制 + 45s，h572 的 N3 窗）——⚠️ 那是**夜间**窗，
                      # 与白天的试跑窗**钟点不对齐**（R194）；同时刻对照见 SAME_CLOCK_45S
SAME_CLOCK_45S = 6.629   # h606：09-28 13:00→21:48L（白天、45s、0.15 体制、89 出场腿）
REF_NAME = "A 内置基线"
NOM_RATIO_THRESHOLD = 1.15   # R86b：止损腿名义比 >1.15× ⇒ 机制在放大尾部


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="h463")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    # [R150] 测试钩子：离线用例必须能把产物指向临时目录（R41/R57/R73 的纪律 ✓），
    # 否则"终局分支（PASS/ROLLBACK）"永远无法在离线环境被验证 ✗。
    import os
    hook = os.environ.get("H599_VERDICT_PATH")
    if hook:
        vp = Path(hook)
    else:
        name = f"{a.trial}_verdict_dryrun.json" if a.dry else f"{a.trial}_verdict.json"
        vp = ROOT / "research_l1" / "out" / name
    if not vp.exists():
        print(f"✗ 还没有判定产物：{vp.name}（判定任务尚未运行，或还没落地）")
        return 1
    v = json.loads(vp.read_text(encoding="utf-8"))
    print("=" * 96)
    print(f"② 判决速读 · {vp.name}（hours={v.get('hours')}，judged_at={v.get('judged_at')}）")
    print("=" * 96)

    cov = v.get("covered") or {}
    t = v.get("trial") or {}
    print("\n[格 1] 频率硬闸（约束项 = 绝对地板 60/h）")
    print(f"  可交易腿速 = {cov.get('legs_per_hour_covered')}/h"
          f"（墙钟 {cov.get('legs_per_hour_wall')}）  基线 = "
          f"{cov.get('baseline_legs_per_hour_covered')}/h")
    print(f"  可交易 {cov.get('trial_h')}h（覆盖 {cov.get('trial_ratio')}）"
          f"  freq_ok={v.get('freq_ok')}")
    print("  ⇒ 若不过：<24/h ⇒ 无条件回滚；市场更冷/波动更高 ⇒ INCONCLUSIVE；否则回滚")

    print("\n[格 2] 判决")
    print(f"  verdict = {v.get('verdict')}   why = {str(v.get('why'))[:160]}")
    _brk = v.get("breaks_in_window")
    _vd = str(v.get("verdict") or "")
    if _vd == "PASS":
        print(f"  breaks_in_window = {_brk}（PASS ⇒ 该提示不适用 ✓）")
    else:
        print(f"  breaks_in_window = {_brk}"
              f"  ⇒ {'**无降级保护**：若为负显著即为 ROLLBACK' if not _brk else '有断点 ⇒ 负向 Δ 会降级为 INCONCLUSIVE'}")

    w = v.get("welch") or {}
    print("\n[格 3] 组成匹配性")
    # [R195] 原版**无条件**打印「试跑窗（白天，同钟点区间）⇒ 组成匹配 ✓」——那在 12h 窗是对的，
    # 但 ② 若在 21:48 判成 INCONCLUSIVE，框架会把窗口**延长 13h 到次日 10:48**（跨度 25h）
    # ⇒ 试跑窗变成"白天+夜间"混合，而 A 基线是**纯白天 12h** ⇒ 组成**不再匹配** ✗。
    # 产物里没有 started_at，但有 `hours` 与 `two_halves.mid`（中点）⇒ 两者可**反推**窗口边界 ✓。
    _span = ""
    try:
        _h_win = float(v.get("hours") or 0.0)
        _mid = ((v.get("two_halves") or {}).get("mid"))
        if _h_win > 0 and _mid:
            _m = dt.datetime.fromisoformat(str(_mid))
            _a = _m - dt.timedelta(hours=_h_win / 2.0)
            _b = _m + dt.timedelta(hours=_h_win / 2.0)
            _tz = dt.timezone(dt.timedelta(hours=8))
            _a_l, _b_l = _a.astimezone(_tz), _b.astimezone(_tz)

            def _night_share(x0: "dt.datetime", x1: "dt.datetime") -> float:
                """夜间占比（本地 21:00-09:00，15 分钟一格）。"""
                n = tot = 0
                t = x0
                while t < x1:
                    tot += 1
                    if t.astimezone(_tz).hour >= 21 or t.astimezone(_tz).hour < 9:
                        n += 1
                    t += dt.timedelta(minutes=15)
                return (n / tot) if tot else 0.0

            _night = _night_share(_a, _b)
            # [R195b] **自校准**：拿"参照白天带"（试跑起点当天 09:48→21:48L）用**同一口径**
            # 算一次夜间占比当基线 —— 固定阈值会被边界效应坑到：纯白天 12h 窗在
            # "hour>=21" 的口径下也有 6% ⇒ 首版把它误报成"跨昼夜" ✗（h608 夹具当场抓到）。
            _ra = _a.astimezone(_tz).replace(hour=9, minute=48, second=0, microsecond=0)
            _ref_night = _night_share(_ra, _ra + dt.timedelta(hours=12))
            _span = (f"  试跑窗（反推）= {_a_l:%m-%d %H:%M} → {_b_l:%m-%d %H:%M}L"
                     f"（{_h_win:.1f}h，夜间占 {_night:.0%}；参照白天带 {_ref_night:.0%}）")
            print(_span)
            if _night <= _ref_night + 0.10:
                print("  该比较 = A（白天 12h）vs 试跑窗（白天，同钟点区间）⇒ **组成匹配** ✓")
            elif _night <= 0.35:
                print(f"  ⚠️ 试跑窗已**跨昼夜**（夜间占 {_night:.0%}）⇒ A 基线是**纯白天**，"
                      f"组成**不再严格匹配** ⇒ Welch 的 Δ 需按昼夜分层复读 ✓")
            else:
                print(f"  ✗ 试跑窗已是**昼夜混合窗**（夜间占 {_night:.0%}）⇒ ① A 基线组成不匹配 ✗；"
                      f"② 判据 D 的「两半」≈ **昼 vs 夜** 两段 ⇒ **不能当稳定性检验**，"
                      f"那是 R194 抓过的昼夜混淆 ✗")
        else:
            print("  （产物缺 hours/two_halves.mid ⇒ 无法反推窗口边界，按 12h 白天窗读 ✓）")
    except Exception as _e:  # noqa: BLE001
        print(f"  （窗口反推失败，不阻塞：{type(_e).__name__}）")
    print(f"  Welch: p={w.get('p')} delta={w.get('delta')}bp n={w.get('n_trial')}/{w.get('n_base')}")
    # [R198] **基线与试跑窗之间的缝隙变更**（判定不扫这一段）——主判据是"试跑窗 vs 基线窗"，
    # 只要缝隙里改过参数，Δ 就不是单变量。② 实测：缝隙 12h 内**至少 13 项**变更
    # （含 h448 的确认阈值 0.15→0.5、h427 对 `max_one_side_seconds` 本身的回滚、
    #  以及 ③ 在 09:10–09:24L 的部署与回滚）⇒ 记录里必须写明"Δ = 90s + 这些变更" ✗。
    try:
        with psycopg.connect(h.read_env_dsn()) as _c, _c.cursor() as _cur:
            _meta = h._load_meta(_cur)
            _t = dict(_meta.get(f"{a.trial}_trial") or {})
            _since = _t.get("started_at")
            if _since:
                _sd = dt.datetime.fromisoformat(str(_since))
                _b1 = _sd - dt.timedelta(hours=12)
                _b0 = _sd - dt.timedelta(hours=24)
                # [R198b] **三段全覆盖**：判定只扫试跑窗（`(since, now]`）✗；而对比涉及三段 ——
                #   ① 基线窗内（`since−24h → since−12h`）② 缝隙（`since−12h → since`）
                #   ③ 试跑窗内（cell 2 的 `breaks_in_window` ✓）。① 与 ② 此前**无人看守**；
                #   ③ 的基线窗会被"上一项试跑的判定动作"切到（例：② 若在 21:48 回滚，
                #   ③ 的基线窗 09-29 09:58→21:58 就是"90s 11.8h + 45s 0.2h"的混合 ✗）。
                _segs = [("基线窗内", _b0, _b1), ("缝隙（基线末→试跑始）", _b1, _sd)]
                _bad = 0
                for _label, _x0, _x1 in _segs:
                    _hits = []
                    for _op in (_meta.get("ops_changes") or []):
                        _ts = _op.get("ts")
                        if not _ts:
                            continue
                        try:
                            _tt = dt.datetime.fromisoformat(str(_ts))
                        except Exception:  # noqa: BLE001
                            continue
                        if _tt.tzinfo is None:
                            _tt = _tt.replace(tzinfo=dt.timezone.utc)
                        _act = str(_op.get("action") or "")
                        if not (_x0 < _tt <= _x1) or _act.startswith(f"{a.trial}_"):
                            continue
                        if any(k in _act for k in ("deploy", "rollback", "restore")):
                            _hits.append(f"{_tt.astimezone():%m-%d %H:%M} {_act}")
                    if _hits:
                        _bad += len(_hits)
                        print(f"  ⚠️ {_label}：**{len(_hits)} 项**参数变更"
                              f"（判定**不扫**这一段）⇒ 对比不均匀 ✗")
                        for _x in _hits[:5]:
                            print(f"       {_x}")
                    else:
                        print(f"  ✓ {_label}：无其它参数变更 ✓")
                if _bad:
                    print(f"  ⇒ 三段里共 **{_bad} 项**未看守的变更（下界；"
                          f"`ops_changes` 只留最近 20 条 ✗）⇒ **本判据不是单变量对比**："
                          f"Δ = 被测参数 + 上述变更的合计效应 ✗（不会被降级，但归因必须写明 ✓）")
    except Exception as _e:  # noqa: BLE001
        print(f"  （缝隙变更检查失败，不阻塞：{type(_e).__name__}）")

    print("\n[格 4] 尾部归因（止损腿名义比 vs 1.15×）")
    try:
        with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
            meta = h._load_meta(cur)
            syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            s = dict(meta.get(f"{a.trial}_trial") or {})
            since = s.get("started_at")
            # 基线窗：since-24h → since-12h（与判定同口径 ✓）
            t0 = dt.datetime.fromisoformat(since)
            b0 = (t0 - dt.timedelta(hours=24)).isoformat()
            b1 = (t0 - dt.timedelta(hours=12)).isoformat()
            out = {}
            for tag, (x, y) in (("试跑", (since, v.get("judged_at"))),
                                (REF_NAME, (b0, b1))):
                cur.execute(
                    "SELECT count(*), COALESCE(avg(notional),0)::float8,"
                    " COALESCE(avg(net_bp),0)::float8 FROM lane_ledger"
                    " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
                    " AND symbol = ANY(%s) AND meta_json->>'exit_path' LIKE 'stop_loss%%'",
                    (h.LANE, x, y, syms))
                n, nom, bp = cur.fetchone()
                cur.execute(
                    "SELECT COALESCE(avg(net_bp),0)::float8 FROM lane_ledger"
                    " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
                    " AND symbol = ANY(%s) AND COALESCE(meta_json->>'exit_path','')"
                    " NOT LIKE 'stop_loss%%'", (h.LANE, x, y, syms))
                nonstop = cur.fetchone()[0]
                out[tag] = (int(n), float(nom), float(bp), float(nonstop))
                print(f"  {tag:<12} 止损腿={int(n):>3} 名义均值={float(nom):>7.1f}$ "
                      f"止损均值={float(bp):>7.2f}bp  非止损腿均值={float(nonstop):>+6.2f}bp")
            if out["试跑"][0] and out[REF_NAME][0]:
                r = out["试跑"][1] / out[REF_NAME][1]
                print(f"  ⇒ 名义比 = {r:.2f}×  ⇒ "
                      f"{'**机制在放大尾部 ⇒ 判负可信** ✓' if r > NOM_RATIO_THRESHOLD else '行情驱动 ⇒ 弱归因（但参数仍会被自动回滚）'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  （取数失败，不阻塞：{type(exc).__name__}: {str(exc)[:60]}）")

    print("\n[格 5] 机制判据（腿/趟 ÷ 同体制参照）")
    hw = ((v.get("sub") or {}).get("trial") or {}).get("hold_window", {})
    lpt = hw.get("legs_per_trip")
    print(f"  腿={hw.get('legs')} 出场腿={hw.get('exit_legs')} 腿/趟={lpt}"
          f" ⇒ {(lpt / REF_LPT):.2f}× 参照（{REF_LPT}）"
          if lpt else "  （产物缺 hold_window）")
    # [R194] **参照窗的钟点不对齐**（我自己在 h606 里抓到的）：3.6263 取自 h572 的 N3 窗，
    # 那是**夜间**（23:17→09:00L、0.5 体制、45s）；而试跑窗是**白天**（09:48→21:48L）。
    # era 内两个**取样充分**的 45s 窗对比：白天 D1 = 6.629（89 出场腿）vs 夜间 N3 = 3.626
    # （99 出场腿）⇒ 腿/趟本身就有 ≈1.8× 的昼夜差 ⇒ 拿 3.6263 当分母会把**昼夜**算成参数效应 ✗。
    # 同钟点对照（白天 45s 6.629 → 白天 90s 试跑）才是这个参数该看的比值。
    if lpt:
        print(f"  ⇒ 同钟点 45s 对照（h606：09-28 13:00→21:48L、45s、0.15 体制）= {SAME_CLOCK_45S}"
              f" ⇒ **×{float(lpt) / SAME_CLOCK_45S:.2f}**")
        print("  ⇒ 读法（R194 提出、**R197 更正归因**）：**×3.6263 的比值不可当机制证据** ✓")
        print("     理由（精确版）：参照窗 N3 与试跑窗**同时差两样** —— ① 钟点（夜 vs 昼）、"
              "② 该窗还横跨 02:33L 的 h472 执行方式变更；")
        print(f"     而同钟点的 45s 备选（{SAME_CLOCK_45S}）又**跨了确认体制**（0.15 vs 试跑窗的 0.5）⇒"
              f"两个分母都**不是干净对照** ✗")
        print("     ⇒ 正确结论：**era 内不存在「同时钟且同体制」的 45s 窗 ⇒ 本格无法干净评估** ✓；"
              "两个备选分母给的是**区间上界/下界**（×1.94 与 ×1.06）")
        print("     ⇒ 要干净评估，需一个**同时钟同体制**的 45s 窗（例如 ② 回滚后、体制未变时的白天窗）✓")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
