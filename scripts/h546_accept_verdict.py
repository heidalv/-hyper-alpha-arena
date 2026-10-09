"""h546：**判定验收助手**——把"预注册预期 ↔ 实测"逐条对照，生成可归档的验收记录。

为什么需要：三件事的最后一步是"记录验收"，而判定落地时（21:24 / 次日 ~09:30）
人工去翻 SPEC、翻 JSON、对照口径，很容易事后找理由或漏项。本脚本把
**预注册预期**（取自 `h425_repair_trial.py` 的 SPEC `criteria` 与验收文档）与
判定产物 `research_l1/out/<key>_verdict.json` 逐条并排，并落一份 markdown。

用法：
  python scripts/h546_accept_verdict.py --trial h463
  python scripts/h546_accept_verdict.py --trial h464
  python scripts/h546_accept_verdict.py --trial h527
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
# ⚠️ 路径可被环境变量覆盖（**仅供离线测试**，见 h565）。同时认 `H564_*`：
# 自动验收守卫（h564）会以子进程方式调本脚本，测试时只设一套环境变量即可覆盖两边
# ——R41/R57 的教训：**钩子必须覆盖（子）进程的全部落盘路径**，否则测试会污染生产。
OUTDIR = pathlib.Path(os.environ.get("H546_VERDICT_DIR")
                      or os.environ.get("H564_VERDICT_DIR")
                      or (ROOT / "research_l1" / "out"))
DOCPATH = pathlib.Path(os.environ.get("H546_DOC_PATH")
                       or os.environ.get("H564_DOC_PATH")
                       or (ROOT / "研究结论" / "三件事验收_20260929.md"))


def _rel(p: pathlib.Path) -> str:
    """尽量给相对路径；**在仓库之外时退回绝对路径**。

    R57 修：原先直接用 `p.relative_to(ROOT)`，路径不在仓库内时抛 `ValueError`
    ⇒ 整个脚本 rc=1（测试用临时目录时暴露；文档若被移出仓库同样会炸）。
    """
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# 预注册预期（判定时逐条对照；写在这里 = 判定前就固定，避免事后改口径）
#
# ⚠️ 取值器必须与**实际产物字段**对齐（R35 用 `h552_verdict_schema.py` 静态核对过）：
#   顶层：trial / baseline / welch / freq_ok / sub / breaks_in_window / hours / verdict / why
#   sub：h463→`hold_window.legs_per_trip`；h464→`entry_vs_exit` 与 `round_trips`；
#        h527→`watch_legs` / `watch_stop_usd` / `watch_notional_p50_med`。
#   取不到的项会打印"(取不到: …)"——那说明映射又漂了，判定前应先跑 `h552` 核对。
_ROOT546 = pathlib.Path(__file__).resolve().parents[1]


def _probe_line(_v=None) -> str:
    """[R205] h529 判据 C 的**行为侧**读数：worker 心跳里的 `GATE_PROBES`。

    为什么要它：h529 改的是**新字段**，而"参数写进去了"（`h481` 回显）不等于"闸真的在拦" ✗
    （F189）。这个读数**不依赖任何旧基线** ⇒ 天然免疫 R198 的"缝隙变更"污染 ✓。
    θ=0（未部署）时 hit/blocked 都应为 0；部署后 `blocked` 应显著 >0。
    """
    try:
        raw = json.loads((_ROOT546 / "logs" / "mm_lane_status.json")
                         .read_text(encoding="utf-8"))
        gp = dict(raw.get("gate_probe_counts") or {})
        if not gp:
            return "（心跳无 gate_probe_counts ⇒ worker 未重启/旧代码 ✗）"
        hit = int(gp.get("ofi_require_hit") or 0)
        blk = int(gp.get("ofi_require_blocked") or 0)
        if not hit:
            return "hit=0 blocked=0（θ=0 未部署 ⇒ 预期为 0 ✓）"
        return f"hit={hit} blocked={blk}（拦截占比 {blk / hit:.1%}）"
    except Exception as e:  # noqa: BLE001
        return f"(取不到: {type(e).__name__})"


def _halves_note(v) -> str:
    """两半窗口的读数（R55）。h529 的产物可能不含 `hold_window`
    ⇒ 退化为"两半各自的净/腿"，仍能回答'方向是否一致' ✓。"""
    th = v.get("two_halves") or {}
    out = []
    for k in ("first_half", "second_half"):
        part = th.get(k) or {}
        hw = (part.get("hold_window") or {}).get("legs_per_trip")
        ev = (part.get("entry_vs_exit") or {}).get("net_bp_per_entry_leg")
        out.append(f"{k.split('_')[0]}=" + (f"腿/趟{hw}" if hw is not None
                                            else (f"净/腿{ev}" if ev is not None else "（无）")))
    return "｜".join(out) if out else "（产物未含 two_halves）"


EXPECT = {
    "h529": {
        "name": "③′ 饱和要求闸 ofi_require_threshold 0→0.9（h483 的要求口径）",
        "items": [
            ("腿速 ≥60/h（硬约束，可交易口径）", lambda v: v.get("freq_ok")),
            ("Welch p（α=0.10）", lambda v: (v.get("welch") or {}).get("p")),
            ("机制 C（**行为侧、不依赖旧基线**）：探针 hit/blocked", _probe_line),
            ("机制 D：两半方向一致（R55）", _halves_note),
            ("窗口内无其它变更（干净对比）", lambda v: not v.get("breaks_in_window")),
        ],
    },
    "h463": {
        "name": "② 加仓窗口 max_one_side_seconds 45→90",
        "items": [
            ("腿速 ≥60/h（硬约束，可交易口径）", lambda v: v.get("freq_ok")),
            ("Welch p（α=0.10）",
             lambda v: round(float((v.get("welch") or {}).get("p")), 4)
             if (v.get("welch") or {}).get("p") is not None else None),
            ("机制 C：legs_per_trip（腿数÷平仓腿数）",
             lambda v: ((v.get("sub") or {}).get("trial") or {})
             .get("hold_window", {}).get("legs_per_trip")),
            # [R73] **同体制参照**（判定前预注册，见文档 R72）：内置基线的 `legs_per_trip`
            # 是 **0.15 体制**的值（`ofi_confirm` 在 09-28 23:17 才改 0.5，而基线窗整段
            # 在它之前）⇒ 阈值本身把该指标压低 ≈40%（h572：6.0→3.6）⇒ 拿试跑窗比 6.75
            # 会把"阈值的效应"读成"90s 失败"✗。故必须同时给出**同体制（0.5/45s）参照**：
            # 3.6263 = `h572` 的 N3 窗（09-28 23:17→09-29 09:48，夜间、0.5 体制、45s）；
            # 证据文件 `research_l1/out/h572_daynight_separability.json`。
            ("机制 C 的**同体制参照**（0.5 体制 + 45s，R72；判 90s 成败看这条）", 3.6263),
            ("（上一行是冻结的参照值）内置基线同口径——**跨体制，仅供参考**",
             lambda v: ((v.get("sub") or {}).get("baseline") or {})
             .get("hold_window", {}).get("legs_per_trip")),
            ("机制 D：两半方向一致（R55 新增计算）",
             lambda v: "｜".join(
                 f"{k.split('_')[0]}={((v.get('two_halves') or {}).get(k) or {})
                    .get('hold_window', {}).get('legs_per_trip')}"
                 for k in ("first_half", "second_half"))),
            ("窗口内无其它变更（干净对比）",
             lambda v: not v.get("breaks_in_window")),
        ],
    },
    "h464": {
        "name": "③ ofi_confirm_threshold 0.5→0.9",
        "items": [
            ("腿速 ≥60/h（硬约束，可交易口径）", lambda v: v.get("freq_ok")),
            ("Welch p（α=0.10）", lambda v: (v.get("welch") or {}).get("p")),
            ("机制：入场腿净/腿（应上升）",
             lambda v: ((v.get("sub") or {}).get("trial") or {})
             .get("entry_vs_exit", {}).get("net_bp_per_entry_leg")),
            ("机制：出场腿数（往返代理，应 ≥0.85× 基线）",
             lambda v: ((v.get("sub") or {}).get("trial") or {})
             .get("entry_vs_exit", {}).get("exit_legs")),
            ("基线 出场腿数（对比用）",
             lambda v: ((v.get("sub") or {}).get("baseline") or {})
             .get("entry_vs_exit", {}).get("exit_legs")),
            ("机制 D：两半方向一致（R54 新增计算）",
             lambda v: "｜".join(
                 f"{k.split('_')[0]}={((v.get('two_halves') or {}).get(k) or {})
                    .get('entry_vs_exit', {}).get('net_bp_per_entry_leg')}"
                 for k in ("first_half", "second_half"))),
            ("窗口内无其它变更（干净对比）",
             lambda v: not v.get("breaks_in_window")),
        ],
    },
    "h527": {
        "name": "h527 逐币单笔规模 NEAR/ARB/ENA ×0.35",
        "items": [
            ("腿速 ≥60/h（硬约束，可交易口径）", lambda v: v.get("freq_ok")),
            ("机制1：被缩三币 单笔名义 P50（应 ≥40% 降幅）",
             lambda v: ((v.get("sub") or {}).get("trial") or {})
             .get("watch_notional_p50_med")),
            ("机制1 基线",
             lambda v: ((v.get("sub") or {}).get("baseline") or {})
             .get("watch_notional_p50_med")),
            ("机制2：被缩三币 腿数（应 ≥0.85× 基线）",
             lambda v: ((v.get("sub") or {}).get("trial") or {}).get("watch_legs")),
            ("机制2 基线",
             lambda v: ((v.get("sub") or {}).get("baseline") or {}).get("watch_legs")),
            ("机制3：被缩三币 止损 USD（应下降）",
             lambda v: ((v.get("sub") or {}).get("trial") or {}).get("watch_stop_usd")),
            ("机制3 基线",
             lambda v: ((v.get("sub") or {}).get("baseline") or {}).get("watch_stop_usd")),
            ("净 bp/腿 Δ（不应显著变差）",
             lambda v: (v.get("welch") or {}).get("delta")),
        ],
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", required=True, choices=sorted(EXPECT))
    ap.add_argument("--dry", action="store_true",
                    help="读 `_verdict_dryrun.json`（预演产物）而不是正式产物")
    ap.add_argument("--append", action="store_true",
                    help="把验收块**幂等**写进 `研究结论/三件事验收_20260929.md`"
                         "（AUTO-ACCEPT 标记之间整体替换，可重复运行）")
    a = ap.parse_args()
    key = a.trial
    p = OUTDIR / (f"{key}_verdict_dryrun.json" if a.dry else f"{key}_verdict.json")
    if not p.exists():
        print(f"✗ 还没有判定产物：{_rel(p)}")
        print("  （判定任务尚未运行；先跑 `h425_repair_trial.py --trial "
              f"{key} --judge --dry-run` 看中途快照）")
        return 1
    v = json.loads(p.read_text(encoding="utf-8"))
    spec = EXPECT[key]
    print("=" * 88)
    print(f"验收对照 · {spec['name']}")
    print("=" * 88)
    print(f"判定：**{v.get('verdict')}**   原因：{str(v.get('why'))[:110]}")
    t, b = v.get("trial") or {}, v.get("baseline") or {}

    def _ppl(d: dict):
        """[R73] 每腿净额 = 合计 ÷ 腿数。原先把**合计**标成"净bp/腿"⇒ 误导读者 ✗。"""
        try:
            return round(float(d.get("net_bp")) / float(d.get("legs")), 3)
        except Exception:  # noqa: BLE001
            return None

    _hrs = v.get("hours")
    print(f"窗口：{None if _hrs is None else round(float(_hrs), 2)}h   "
          f"试跑 腿数={t.get('legs')} 净bp合计={t.get('net_bp')}（每腿 {_ppl(t)}）"
          f" 腿速={None if t.get('legs_per_hour') is None else round(t['legs_per_hour'],1)}/h")
    print(f"                     基线 腿数={b.get('legs')} 净bp合计={b.get('net_bp')}"
          f"（每腿 {_ppl(b)}）"
          f" 腿速={None if b.get('legs_per_hour') is None else round(b['legs_per_hour'],1)}/h")
    w = v.get("welch") or {}

    def _welch_s(d: dict) -> str:
        """[R73] 原先直接把 Welch 的原始 dict 写进文档 ⇒ 一长串浮点噪声。"""
        if not d:
            return "（无）"
        _p = d.get("p")
        return (f"p={None if _p is None else round(float(_p), 4)}"
                f" delta={None if d.get('delta') is None else round(float(d['delta']), 3)}bp"
                f" n={d.get('n_trial')}/{d.get('n_base')}"
                f"（{'显著' if (_p or 1) <= 0.10 else '不显著'}）")

    def _cov_s(c: dict) -> str:
        """[R73] 频率必须**两个口径并排**：`freq_ok` 判的是可交易口径，而 `legs_per_hour`
        是墙钟口径；停摆窗里两者可差一倍（38.6 vs 62.6）⇒ 只显示墙钟会误导。"""
        if not c:
            return "（产物未含 `covered` 字段）"
        _r = c.get("trial_ratio")
        return (f"试跑 可交易 {c.get('trial_h')}h"
                f"（覆盖 {None if _r is None else format(float(_r), '.1%')}）"
                f" ⇒ **{c.get('legs_per_hour_covered')}/h**；"
                f"基线 可交易 {c.get('baseline_h')}h ⇒ {c.get('baseline_legs_per_hour_covered')}/h"
                f"（墙钟 {c.get('legs_per_hour_wall')} / {c.get('baseline_legs_per_hour_wall')}）")

    if w:
        print(f"Welch：{_welch_s(w)}")
    print(f"腿速达标：{v.get('freq_ok')}   窗口内断点：{v.get('breaks')}")
    print(f"可交易时长口径：{_cov_s(v.get('covered') or {})}")
    print("-" * 88)
    print("预注册预期 vs 实测：")
    lines = []
    for name, getter in spec["items"]:
        try:
            # [R73] 允许**常量**条目（冻结的参照值），不只是取值函数
            val = getter(v) if callable(getter) else getter
        except Exception as e:  # noqa: BLE001
            val = f"(取不到: {str(e)[:40]})"
        print(f"  · {name:<44} → {val}")
        lines.append(f"  · {name} → `{val}`")
    md = [f"### 验收对照（{key}，自动生成）", ""]
    if a.dry:
        # ⚠️ 预演产物必须**显式标注**：否则写进文档后会被当成正式判定（R44 自查发现）
        md += [f"> ⚠️ **本块来自预演产物 `{p.name}`（`--dry`），不是正式判定**"
               f"；正式判定落地后请再跑一次（不带 `--dry`）覆盖本块。", ""]
    md += [f"- 判定：**{v.get('verdict')}**；原因：{v.get('why')}",
          f"- 判定时刻：`{v.get('judged_at')}`（本行供自动验收去重，勿手改）",
          f"- 窗口 {None if _hrs is None else round(float(_hrs), 2)}h；"
          f"试跑 腿数={t.get('legs')} 净bp合计={t.get('net_bp')}（**每腿 {_ppl(t)}**）"
          f" 腿速（墙钟）={None if t.get('legs_per_hour') is None else round(t['legs_per_hour'], 1)}"
          f"；基线 腿数={b.get('legs')} "
          f"净bp合计={b.get('net_bp')}（**每腿 {_ppl(b)}**）",
          f"- Welch：{_welch_s(w)}",
          f"- freq_ok={v.get('freq_ok')}（**判据口径见下行**） "
          f"breaks={v.get('breaks_in_window')}",
          f"- 可交易时长口径：{_cov_s(v.get('covered') or {})}", ""]
    md += lines + [""]
    outp = OUTDIR / f"{key}_acceptance.md"
    outp.write_text("\n".join(md), encoding="utf-8")
    print(f"\n已写：{_rel(outp)}（可直接贴进研究结论/三件事验收）")

    if a.append:
        # [R44] 把验收块**幂等**写进验收文档：`<!-- AUTO-ACCEPT:key -->` 之间内容整体替换，
        # 重复运行不会堆叠（避免"同一试跑在文档里出现三份、口径还不一致"）。
        doc = DOCPATH
        if not doc.exists():
            print(f"✗ 找不到 {_rel(doc)} ⇒ 跳过 --append")
            return 1
        text = doc.read_text(encoding="utf-8")
        begin, end = f"<!-- AUTO-ACCEPT:{key} -->", f"<!-- /AUTO-ACCEPT:{key} -->"
        body = f"{begin}\n" + "\n".join(md).rstrip() + f"\n{end}\n"
        # [R206] 两道防线，都是被真事故逼出来的：
        #  ① 取**最后一处**标记（`rindex`）：自动写入的块**永远在文末** ✓，而正文里若
        #    出现**字面**标记（我自己在 R192 的记录里就写了一个 ✗），第一个 `index()` 会指向
        #    那一行 ⇒ 替换会把"从正文那一行到旧块结尾"的**中间上千行静默删掉** ✗✗；
        #  ② **缩减保护**：若替换后文档反而**大幅变短**（< 原长的 60%），说明匹配到的是
        #    错误区间 ⇒ 放弃替换、改为追加，并打印告警（宁可多一块，也不撕掉上千行 ✓）。
        if begin in text and end in text:
            i, j = text.rindex(begin), text.rindex(end) + len(end)
            cand = text[:i] + body.rstrip("\n") + text[j:]
            if len(cand) < len(text) * 0.6:
                print(f"⚠️ 替换区间可疑（文档 {len(text)} → {len(cand)} 字符）⇒ "
                      f"拒绝就地替换，改为追加新块（防误删 ✓）")
                text = text.rstrip("\n") + "\n\n---\n\n" + body
                act = "已追加（缩减保护触发）"
            else:
                text = cand
                act = "已替换"
        else:
            # 落到文末（保持"自动段落集中在文档尾部"的可预期位置）
            text = text.rstrip("\n") + "\n\n---\n\n" + body
            act = "已追加"
        doc.write_text(text, encoding="utf-8")
        print(f"✓ {act}到 {_rel(doc)}（标记 {begin} … {end}，可重复运行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
