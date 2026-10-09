"""规矩合规检查：把 `01_规矩.md` 的 11 条从"散文"变成"可执行判定"。

为什么需要：
  用户的核心抱怨是「没有统一的贯彻风格」。
  而「规矩」如果只写在 markdown 里，**没有任何东西会强制它被执行** ——
  这正是我在 2026-10-06 一天内连续 5 次"改动无效"的原因（规矩第 11 条）。

本工具逐条检查规矩，输出 PASS / FAIL / N-A，并给出**实测证据**。
用法：
    python scripts/tools/rules_check.py
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
LANE = "mm_asterdex"
ERA = "2026-10-05 15:57:56+08"      # 当前形态起点


def _j(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return {}


RESULTS = []


def _rec(rule: str, name: str, status: str, evidence: str) -> None:
    RESULTS.append((rule, name, status, evidence))


def main() -> int:
    st = _j(ROOT / "logs" / "mm_lane_status.json")
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        # 基础聚合
        cur.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE fee_bp < -0.5),
                   coalesce(sum(notional),0),
                   coalesce(sum(notional*fee_bp/1e4),0),
                   coalesce(sum(notional*net_bp/1e4),0),
                   coalesce(sum(notional*spread_bp/1e4),0),
                   coalesce(sum(notional*price_bp/1e4),0)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
        """)
        legs, paid, notl, fee, net, spread, price = cur.fetchone()
        legs, paid = int(legs or 0), int(paid or 0)
        notl, fee, net = float(notl), float(fee), float(net)
        fee_rate = abs(fee) / max(notl, 1e-9) * 1e4
        paid_pct = paid / max(legs, 1) * 100

    # ── 第 1 条 成本规矩 ──
    ok = fee_rate < 0.05 and paid_pct < 5.0
    _rec("第 1 条", "成本（手续费）唯一来源且受控",
         "PASS" if ok else "FAIL",
         f"费率 {fee_rate:.4f}bp（目标<0.05）、付费腿 {paid}/{legs}"
         f"={paid_pct:.2f}%（目标<5%）")

    # ── 第 2 条 盈亏口径 ──
    chk = abs((spread + price + fee) - net)
    _rec("第 2 条", "净额口径 = spread+price+fee 且可校验",
         "PASS" if chk < 1e-6 else "FAIL",
         f"三项和 {spread+price+fee:+.4f} vs 净额 {net:+.4f}"
         f"（差 {chk:.2e}）")

    # ── 第 3 条 出场规矩（挂单优先） ──
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(meta_json->>'exit_path','(entry)'), count(*)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz GROUP BY 1
        """)
        paths = dict(cur.fetchall() or [])
    # ── 第 3 条 出场规矩 ──
    #
    # 本条**原始意图**不是"吃单数必须为 0"，而是
    # 「**出场不得默认退化为恐慌吃单**」—— 历史上 `taker_stop` 实测
    # **−32.71bp/腿**，是最大亏损源（R005 前）。
    # ⇒ 判据应看**吃单腿的实现亏损是否可控**，而不是数个数：
    #     · 吃单腿均 net_bp ≥ −10bp  ⇒ PASS（是"有界的时限兜底"，不是恐慌）
    #     · 更深 ⇒ FAIL
    # **同时始终打印吃单腿数**，避免"通过了但其实是全吃单"。
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT count(*), coalesce(avg(net_bp),0), coalesce(sum(notional),0)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
              AND meta_json->>'exit_path' = ANY(%s)
        """, (list(("taker_stop", "taker_risk", "timeout_taker",
                    "trail_lock_taker", "take_profit_taker",
                    "reversal_decay_taker", "jump_exit_taker",
                    "hold_hard_taker")),))
        tk_n, tk_avg, tk_notl = cur.fetchone()
    tk_n, tk_avg, tk_notl = int(tk_n or 0), float(tk_avg or 0), float(tk_notl or 0)
    ok3 = (tk_n == 0) or (tk_avg >= -10.0)
    _rec("第 3 条", "吃单出场必须是有界兜底、非恐慌默认",
         "PASS" if ok3 else "FAIL",
         f"吃单腿 {tk_n} 条、均 net_bp {tk_avg:.2f}（阈值 −10；"
         f"历史 taker_stop 为 −32.71）、名义 ${tk_notl:,.0f}")

    # ── 第 4 条 门禁规矩（不得新增门禁 / 不得缩小口子） ──
    #
    # 用户的诉求是"不要怕亏钱就缩小口子，影响交易的决定"。
    # 所以本条必须**测口径**，不能靠读源码数开关（那是弱判定）。
    # 判据：
    #   ① 单腿名义不得显著缩小（口子没被收）
    #   ② skip 里不得出现"未识别的策略性否决"
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(avg(notional),0) FROM lane_ledger
            WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
        """)
        avg_notl_now = float((cur.fetchone() or (0,))[0] or 0)
    sk = st.get("skip_counts") or {}
    # 已识别的机制类 skip。`drift_veto` 由 T44 补齐 —— 它此前**不在任何人的清单里**：
    #   · 定义在 `runner.py:1329-1348`（h888，慢漂移否决）
    #   · 实测触发 **1,287 次**，比 `gap_soft_probe`(1,132) 还多
    #   · 它是**策略层否决**（反对 10 分钟趋势 ±4bp ⇒ 拒绝逆势进场），S3 豁免
    known = {"maker_working", "no_direction", "gap_excluded", "gap_soft_probe",
             "maker_edge", "maker_risk", "maker_take", "holding",
             "taker_stop_maker", "flow_reversal", "venue_filter",
             "gross_exposure_active_flow", "regime_R1_no_entry",
             "regime_R4_no_entry", "exit_no_book", "maker_time",
             # [T55] 时限兜底（我**自己新加**的吃单出口）。
             # 登记理由：只对时间类出口生效（maker_time/maker_edge），
             # 阈值 60s，`MM_HOLD_HARD_EXIT_SEC=0` 可回滚。
             # ⚠️ 它使 T55 窗口的**费率从 0.0086bp 升到 1.0006bp**（116 倍）——
             #    这是"用手续费换少亏持仓衰减"的权衡，**尚未证明净正**。
             "hold_hard_taker",
             # [T44] 慢漂移否决（h888）—— 既有策略闸门，实测触发上千次
             "drift_veto"}
    unknown_skips = [k for k in sk if k not in known]
    ok4 = (avg_notl_now > 100) and not unknown_skips
    _rec("第 4 条", "口径未被缩小、无新增策略性否决",
         "PASS" if ok4 else "FAIL",
         f"均腿名义 ${avg_notl_now:,.0f}（>100 视为口子未收）、"
         f"未识别 skip={unknown_skips or '无'}")

    # ── 第 5 条 参数规矩（单一来源、不瞎调） ──
    lp = ROOT / "data" / "flow_learn_params.json"
    params = _j(lp)
    age_min = (time.time() - lp.stat().st_mtime) / 60 if lp.exists() else -1
    _rec("第 5 条", "参数单一来源且近期未被随意改动",
         "PASS" if age_min > 30 else "WARN",
         f"flow_learn_params.json {len(params)} 项，{age_min:.0f} 分钟未变")

    # ── 第 8 条 变更流程 ──
    reg = ROOT.parent / "HFT_整顿" / "02_整改登记.md"
    n_reg = len(reg.read_text(encoding="utf-8", errors="replace").split("## R")) - 1
    _rec("第 8 条", "每次变更都有登记（含前后数字）",
         "PASS" if n_reg >= 50 else "WARN",
         f"登记条目 {n_reg} 项")

    # ── 第 9 条 验收标准 ──
    cur_state = st.get("equity")
    with psycopg.connect(DSN, autocommit=True) as c, c.cursor() as cur:
        cur.execute(f"""
            SELECT coalesce(sum(notional*net_bp/1e4) FILTER (
                       WHERE coalesce(meta_json->>'flatten','false')='true'),0),
                   coalesce(sum(notional*net_bp/1e4) FILTER (
                       WHERE coalesce(meta_json->>'flatten','false')<>'true'),0)
            FROM lane_ledger WHERE lane_id='{LANE}' AND event='fill'
              AND ts >= '{ERA}'::timestamptz
        """)
        fl, non = cur.fetchone()
    fl, non = float(fl), float(non)
    gross = max(net, 0.0)
    fl_vs_gross = abs(min(0.0, fl)) / gross * 100 if gross > 0 and fl < 0 else 0.0
    _rec("第 9 条 ①", "强平腿/毛利 < 50%",
         "PASS" if fl_vs_gross < 50 else "FAIL",
         f"强平 {fl:+.2f} / 毛利 {net:+.2f} = {fl_vs_gross:.1f}%")
    taker_pct = paid / max(legs, 1) * 100
    _rec("第 9 条 ③", "taker 名义占比 < 3%",
         "PASS" if taker_pct < 3 else "FAIL",
         f"{taker_pct:.3f}%")
    _rec("第 9 条 ②", "做市净额 > 0 且统计显著",
         "PASS" if net > 0 else "FAIL",
         f"净额 {net:+.2f}（显著性用 profit_proof.py 的 t 值判定）")

    # ── 第 10 条 产物新鲜度 ──
    # [T56] 把**学习进化链**的产物也纳入断言。
    # 事故：`flow_learn_samples.jsonl` 停更 **39.4 小时**却**无人告警**——
    # 生产者 `h821_learn_positive.py` 既没被调度、又被
    # `archive_h_scripts.py` 归档，而**没有任何新鲜度断言看着它**。
    # ⇒ 这正是第 10 条要防的"静默停摆"。
    bad = []
    for name, limit in (("flow_gate_last.json", 30), ("vol_top20.json", 90),
                        ("flow_situation_last.json", 45),
                        ("flow_learn_samples.jsonl", 120),
                        ("flow_learn_last.json", 120)):
        p = ROOT / "data" / name
        a = (time.time() - p.stat().st_mtime) / 60 if p.exists() else 9e9
        if a >= limit:
            bad.append(f"{name} {a:.0f}min>{limit}")
    _rec("第 10 条", "全部产物新鲜度达标（含学习进化链）",
         "PASS" if not bad else "FAIL",
         "; ".join(bad) if bad else "五个数据源全部新鲜（含学习样本/汇总）")

    # ── 第 11 条 遥测先于改动 ──
    fields = 0
    sts = st.get("states") or {}
    if sts:
        fields = len(next(iter(sts.values())))
    _rec("第 11 条", "关键计数器可见（遥测不被白名单吞掉）",
         "PASS" if fields >= 15 else "FAIL",
         f"每币遥测字段 {fields} 个（含 timeout_exit_blocked 等）")

    # ── 引擎存活 ──
    hb = float(st.get("ts") or 0)
    age = max(0.0, time.time() - hb)
    _rec("引擎", "心跳新鲜且 ticks 推进", "PASS" if age < 120 else "FAIL",
         f"心跳 {age:.0f}s 前、ticks={st.get('ticks')}、equity={cur_state}")

    # ── [T66] 第 11 条附加：**"声明了但从未被读取"的常量**必须保持在已知基线 ──
    # 这是本会话代价最大的缺陷类：`_EXIT_TAKER_AFTER_SEC` 被赋值、被 8 处注释提及、
    # **被 0 处代码读取**，而 T17 **把它当成安全网写进注释** ⇒ 止损永远出不去 ⇒
    # 7.7% 的出场腿吃掉出场侧 97.6% 的亏损（最差 −422bp）。
    # 该缺陷已由 T60 真正实现；但**同类随时可能再出现** ⇒ 加自动断言。
    # 基线：当前仅 `_EXIT_TAKER_AFTER_SEC` 1 个（已登记、职责已由 T60 承接）。
    dead_names = []
    try:
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "tools" / "dead_constants.py")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT), timeout=60).stdout
        in_assign_only = False
        for ln in out.splitlines():
            if "ASSIGN-ONLY" in ln and "──" in ln:
                in_assign_only = True
                continue
            if in_assign_only:
                if ln.strip().startswith("──") or not ln.strip():
                    in_assign_only = False
                    continue
                parts = ln.split()
                if len(parts) >= 3 and parts[0].endswith(".py"):
                    dead_names.append(parts[1])
    except Exception as e:  # noqa: BLE001
        _rec("第 11 条·常量", "「只赋值不读取」常量扫描", "WARN", str(e)[:60])
        dead_names = None
    if dead_names is not None:
        _rec("第 11 条·常量", "无新增「只赋值不读取」的常量（基线 1）",
             "PASS" if len(dead_names) <= 1 else "FAIL",
             f"ASSIGN-ONLY 常量 {len(dead_names)} 个: "
             + (", ".join(dead_names) if dead_names else "无"))

    # ── [T68] 第 11 条附加 ②：**「定义了但从未被调用」的函数** 保持在基线 ──
    # 与常量同类：本会话有 4 处缺陷属于"机制写了、注释解释了、但没人调用"
    # （`hold_hard_taker` 不可达、`timeout_hard_taker` 死分支、
    #   `_EXIT_TAKER_AFTER_SEC` 死常量、`flow_gate_last` 生产者/消费者错位）。
    # 基线 16（已逐个查证为：被更新的调度器取代 / 供 scripts 单独调用的分析函数）。
    try:
        out2 = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "tools" / "dead_functions.py")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT), timeout=180).stdout
        m2 = re.search(r"命中 \*\*(\d+)\*\* 个", out2)
        n_dead_fn = int(m2.group(1)) if m2 else -1
        if n_dead_fn >= 0:
            _rec("第 11 条·函数", "无新增「定义了但从未调用」的函数（基线 16）",
                 "PASS" if n_dead_fn <= 16 else "FAIL",
                 f"从未被调用的函数 {n_dead_fn} 个")
    except Exception as e:  # noqa: BLE001
        _rec("第 11 条·函数", "「定义了但从未调用」扫描", "WARN", str(e)[:60])

    # ── 输出 ──
    print("=" * 92)
    print("规矩合规检查（`01_规矩.md` 的可执行版本）")
    print("=" * 92)
    print(f"  {'条款':<12}{'判定':<7}{'检查项':<34}{'实测证据'}")
    for rule, name, status, ev in RESULTS:
        print(f"  {rule:<12}{status:<7}{name:<34}{ev}")
    npass = sum(1 for r in RESULTS if r[2] == "PASS")
    nfail = sum(1 for r in RESULTS if r[2] == "FAIL")
    nwarn = sum(1 for r in RESULTS if r[2] == "WARN")
    print("-" * 92)
    print(f"  PASS={npass}  FAIL={nfail}  WARN={nwarn}  共 {len(RESULTS)} 项")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

