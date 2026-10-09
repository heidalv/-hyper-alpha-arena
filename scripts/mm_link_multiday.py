# -*- coding: utf-8 -*-
"""[F294 2026-09-16] LINK 澶氫氦鏄撴棩澶嶆牳锛? 鏃?脳 w鈭坽25,30,35} @31.8s锛夈€?
F293锛歀INK 鍦?3 涓獥鍙ｃ€? 鏉″欢杩熶笂鍑€棰濈殕姝ｏ紙w=30 鍚堣 +0.72~0.88$锛宮arkout90 +21bp锛夛紝
浣嗘瘡绐楀彛浠?3~7 绗?鈬?蹇呴』鎵╁埌**澶氫釜浜ゆ槗鏃?*鐪嬬ǔ瀹氭€с€傚垽鎹紙浜嬪厛瀹氭锛岄伩鍏嶆寫绐楀彛锛夛細
  路 姝ｅぉ鏁?鈮?/7锛涘悎璁″噣棰?> 0锛沵arkout90 鈮?0锛堝叏鏍锋湰鍧囧€硷級銆?鏁版嵁涓嶈冻鐨勪氦鏄撴棩锛?200 蹇収锛夎嚜鍔ㄨ烦杩囧苟鍒楀嚭鈥斺€?*璺宠繃鐨勬棩瀛愪笉琛ャ€佷笉鐚?*銆?"""
from __future__ import annotations

import dataclasses
import io
import os
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"
SYM = "LINK"
CST = "+08:00"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _markout(fills, ots, mids, lag=6):
    vals = []
    for f in fills or []:
        try:
            px = float(f.get("px") or 0.0)
            t = float(f.get("ts") or 0.0) * 1000.0
            if px <= 0:
                continue
            i = int(np.searchsorted(ots, t, "left")) + lag
            if i >= len(mids):
                continue
            m = float(mids[i])
            if m <= 0:
                continue
            vals.append((m - px) / px * 1e4 if str(f.get("side")) == "buy"
                        else (px - m) / px * 1e4)
        except Exception:
            continue
    return float(np.mean(vals)) if vals else float("nan")


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp0 = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    dd = _load_all([SYM], venue).get(SYM)
    if not dd:
        print("鏃?LINK 鏁版嵁")
        return 1
    # [F299] 鐢ㄦ敞鍐岃〃**閿氬畾**鐨勬尝鍔ㄥ熀鍑嗭紙涓嶅啀鎸夌獥鍙ｇ幇绠楋級锛欶296 宸叉妸 LINK 閿氫负 3.2984bp
    # 鈬?鍙湁杩欐牱锛岃瘉鎹彛寰勬墠涓?*绾夸笂姝ｅ湪璺戠殑閭ｅ閰嶇疆**閫愬瓧涓€鑷达紙F108c 鏁欒锛夈€?    vb_all = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    vb_use = ({SYM: float(vb_all[SYM])} if SYM in vb_all else None)
    print(f"[F299] vol_baseline={'閿氬畾 ' + str(vb_use) if vb_use else '绐楀彛鐜扮畻锛堟敞鍐岃〃鏃犻敋鍊硷級'}")
    days = [(_d.strftime("%Y-%m-%d")) for _d in
            (datetime(2026, 9, 10) + timedelta(days=i) for i in range(7))]
    widths = tuple(float(x) for x in (os.getenv("F295_WIDTHS", "25,30,35")).split(",") if x)
    delays = tuple(float(x) for x in (os.getenv("F295_DELAYS", "31800")).split(",") if x)
    print(f"[F294] {SYM} 脳 w{widths} 脳 {days[0]}~{days[-1]} delays={[d/1000 for d in delays]}s")
    print(f"{'day':<12}{'snaps':>7}{'w':>5}{'delay':>7}{'fills':>6}{'net$':>9}{'net_bp':>8}"
          f"{'maker':>7}{'flat':>8}{'dd%':>6}{'mk90':>8}")
    tot = {(w, d): 0.0 for w in widths for d in delays}
    pos = {(w, d): 0 for w in widths for d in delays}
    n_day = {(w, d): 0 for w in widths for d in delays}
    mk_all = {(w, d): [] for w in widths for d in delays}
    for day in days:
        s0, s1 = f"{day}T08:00:00{CST}", f"{day}T23:59:00{CST}"
        since_ms, until_ms = _ms(s0), _ms(s1)
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
        t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
        if a3 - a2 < 200:
            # [2026-09-20 修复] 此行原本是一个**未闭合的 f-string**
            # （`print(f"{day:<12}{a3-a2:>7}  数据不足，跳过)` —— 缺收尾引号与右括号），
            # 且中文部分已因 GBK/UTF-8 双重编码变成乱码。
            # 后果：整个文件 `py_compile` 直接失败 ⇒ 这是一个**从未被成功执行过**的脚本。
            print(f"{day:<12}{a3 - a2:>7}  数据不足，跳过")
            continue
        sub = {SYM: {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[SYM][k] = dd[k][t2:t3]
        mids = (dd["bb"][a2:a3] + dd["ba"][a2:a3]) / 2.0
        lo_i = max(0, a2 - 240)
        seed = {SYM: [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                  for k in range(lo_i, a2)) if x > 0]}
        for w in widths:
            for dl in delays:
                qp = dataclasses.replace(qp0, w_base_bp=float(w))
                r = replay_portfolio([SYM], venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=vb_use,
                                     enforce_lane_limits=True, tick_delay_ms=float(dl),
                                     fill_notional_ratio=0.1, mid_hist_seed=seed)
                mk = _markout(r.get("fills_log"), sub[SYM]["ots"], mids)
                net = float(r.get("net_usd") or 0.0)
                k = (w, dl)
                tot[k] += net
                n_day[k] += 1
                pos[k] += 1 if net > 0 else 0
                if mk == mk:      # not NaN
                    mk_all[k].append(mk)
                print(f"{day:<12}{a3-a2:>7}{w:>5.0f}{dl/1000:>6.1f}s{r.get('fills'):>6}{net:>+9.3f}"
                      f"{(r.get('net_bp') or 0):>+8.3f}{(r.get('maker_net_bp') or 0):>+7.2f}"
                      f"{(r.get('flatten_net_bp') or 0):>+8.2f}{(r.get('max_dd_pct') or 0):>6.2f}{mk:>+8.2f}")
    print("\n=== 鍒ゆ嵁锛堜簨鍏堝畾姝伙細姝ｅぉ鏁扳墺5/7 涓?鍚堣>0 涓?markout90鈮?锛?==")
    for w in widths:
        for dl in delays:
            k = (w, dl)
            mk = float(np.mean(mk_all[k])) if mk_all[k] else float("nan")
            ok = (pos[k] >= 5 and tot[k] > 0 and (mk == mk and mk >= 0)) if n_day[k] else False
            print(f"  w={w:>4.0f} @{dl/1000:.1f}s: 鏈夋晥澶?{n_day[k]} 姝ｅぉ鏁?{pos[k]} "
                  f"鍚堣={tot[k]:+.3f}$ markout90鍧囧€?{mk:+.2f}bp 鈬?{'閫氳繃 鉁? if ok else '涓嶉€氳繃 鉁?}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

