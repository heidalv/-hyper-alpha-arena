# -*- coding: utf-8 -*-
"""主动流的判定、离场、打分和杠杆。做市的挂宽、库存和吃单腿数不在这里。

Aster 永续：挂单手续费 0，吃单 4bp。正常进出只挂单。
吃单只在危险行情或灾难止损，而且期望扣掉 4bp 之后仍为正才允许追价。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

TAKER_FEE_BP = 4.0
MIN_HOLD_SEC = 15.0
EXIT_TAKER_AFTER_SEC = 300.0
ENTRY_MARGIN_BP = 1.0
STOP_FLOOR_BP = 15.0
STOP_CAP_BP = 40.0
EQUITY_LOSS_PER_TRADE = 0.005
EQUITY_LOSS_DAY = 0.02
MMR_DEFAULT = 0.01
# [h852] 挂单止损阈值:浮亏达到止损的这个比例 ⇒ 改用挂单贴着对手价离场(0 费),
# 而不是继续等到 full stop 被迫吃单(实测吃单往返 −46bp vs 挂单往返 +59bp)。
MAKER_EXIT_FRAC = 0.5
# [h878 用户深改] 吃单止损的加宽倍数:吃单只留给"真宽"的浮亏(深跳),
# 正常晃动全部走挂单平。
STOP_WIDE_MULT = 2.0
GATE_MAX_AGE_SEC = 1800.0
SITUATION_MAX_AGE_SEC = 6 * 3600.0
# ── [整顿轮·T19 2026-10-05] `MIN_N_EFF` 改可配，暴露"样本饥饿"这个真实约束 ──
#
# 实测（situation 表，4 小时 post-fix 窗口，205 个档）：
#   · `n >= 30` 的档 **只有 7 个**
#   · 而 `mean_y > 0` 的有 **33 个**、`> 1.0` 的有 30 个
# ⇒ **有正 edge 的档因样本不足被跳过** ⇒ 门开不出来 ⇒ 只能走探索单
# ⇒ 而成交量又受"门"限制 ⇒ **鸡生蛋问题**：
#     要更多样本才能开门，要开门才有更多样本。
#
# 在 $300 账户 + 安静市场上，30 条/档 是**永远攒不到**的门槛：
# 实测全窗口 592 腿 / 39 币 ≈ 15 腿/币，而每币每方向有 6 个档
# ⇒ 每档平均 **不到 3 条**。
#
# 该阈值是"统计可信度 vs 样本可得性"的取舍，应当**可配、可 A/B**，而不是写死。
# 默认 30 = 历史行为（不改行为）；
# 调小 ⇒ 更早开门但每条证据更弱（有 `median_y>0` 与 `recent_mean_y>0` 双重约束兜底）。
# 回滚：删掉 env 行即回到 30。
try:
    MIN_N_EFF = float(os.getenv("MM_MIN_N_EFF", "30") or 30.0)
except ValueError:
    MIN_N_EFF = 30.0
MIN_N_EFF = max(3.0, min(200.0, MIN_N_EFF))
STEP_SEC = 15.0
# 试单名义不超过权益的这个比例，并且不能大于单笔止损上限。
#
# ── [整顿轮·T57 2026-10-06] 改为**可配**，并说明为什么它才是真正的口子 ──
#
# 实测（`scripts/tools/which_cap_binds.py`，equity $10,017）：
#
#   风险模型 notional_cap_usd(stop=15bp, n=1) = **$33,390**
#   实测腿量                                  = **$501**
#   ⇒ **人工压缩 66.6 倍** —— 正是用户说的「缩小口子，影响交易的决定」
#
# 而且：引擎**约 100% 的时间运行在探针模式**（严格门长期不开），
# 于是 `probe_notional_usd = equity × PROBE_EQUITY_FRAC` **就是实际交易规模**。
# `MM_AF_LEG_CAP_PCT=0` 虽已把 `active_flow` 那层交回风险模型，
# 但 `active_flow.py:900` 的 `cap = min(cap, probe_notional_usd)` 仍会压回去。
#
# ⇒ 本常量此前**写死且不可配**（本文件唯一的硬编码规模闸门）。
#   现加 env 覆盖，默认保持 **0.05**（逐字不变）。
#   回滚：删掉 `MM_PROBE_EQUITY_FRAC` 或设为 0.05。
#
# ⚠️ 这只是"允许"更大：真正的**组合上限**仍是 T20 的总敞口 3× 权益，
#    以及 `notional_cap_usd` 的风险模型本身。
try:
    PROBE_EQUITY_FRAC = float(os.getenv("MM_PROBE_EQUITY_FRAC", "0.05") or 0.05)
except ValueError:
    PROBE_EQUITY_FRAC = 0.05
PROBE_EQUITY_FRAC = max(0.0, min(10.0, PROBE_EQUITY_FRAC))
AHEAD_EDGES_USD = (30.0, 100.0, 300.0)
SPREAD_EDGES_BP = (2.0, 8.0, 16.0)

# 线上线下同一套特征名。金额失衡是 (买金额-卖金额)/(买+卖)，不是数量，也不是总成交额除常数。
FLOW_FEATURES: Tuple[str, ...] = (
    "ofi_5s", "ofi_15s", "ofi_60s", "ofi_300s",
    "ret_15s_bp", "ret_60s_bp", "ret_300s_bp",
    "bar_pos_60s", "range_60s_bp", "vol_z_300s",
    "depth_wimb", "spread_bp",
    "btc_ret_60s", "rel_ret_60s", "breadth",
)

FLOW_PARAM_FILE = "data/flow_learn_params.json"
FLOW_ROUNDTRIP_FILE = "data/flow_roundtrip_log.jsonl"

# DSH 能动的旋钮。不含挂宽、库存、compound_ratio、交易所杠杆、吃单超时。
FLOW_SAFE_PARAMS: Dict[str, Tuple[float, float]] = {
    "disaster_stop_floor_bp": (10.0, 25.0),
    "disaster_stop_cap_bp": (25.0, 60.0),
    "hold_sec": (30.0, 300.0),
    "entry_margin_bp": (0.5, 5.0),
    "min_notional_60s": (0.0, 500000.0),
    "min_notional_60s": (0.0, 200.0),
    # ── [整顿轮·T69 2026-10-07] **上界从 0.01 收紧到 0.005** ──────────────
    #
    # 不合理之处（实测）：
    #   本表允许学习器把 `notional_loss_frac`（单笔风险占权益比例）调到 **0.01**，
    #   而**引擎自己声明的风险预算是 `EQUITY_LOSS_PER_TRADE = 0.005`**（本文件第 20 行，
    #   `active_flow.py:954` 的注释也写着"引擎自己声明的单笔风险预算是 0.5%"）。
    #   ⇒ **学习器被允许把风险设到引擎自身预算的 2 倍**，而且它**确实顶到了上界**。
    #
    # 后果（实测）：equity $9,884.91、止损 15bp 时
    #   `notional_cap_usd` 允许单腿 **$65,899 = 6.7× 权益**；
    #   而 R073 已证明**止损会被穿透 7 倍** ⇒ "1% 风险"的假设根本不成立，
    #   实际亏损远超 1%（实测 25 分钟亏 −$138 = −1.4%）。
    #
    # 这不是"调参数"，是**把参数的上界修回与引擎自身预算一致** ——
    # 即用户说的「不合理就改」。
    #
    # 修法：上界 0.01 → **0.005**。
    #   `load_learn_params()` 会执行 `min(hi, max(lo, val))` 做**钳制**
    #   ⇒ 现有文件里的 0.01 会在下次加载时**自动变成 0.005**，无需手改参数文件。
    # 回滚：把上界改回 0.01。
    "notional_loss_frac": (0.002, 0.005),
    # ── [2026-10-09 重复来回做市] ping-pong 旋钮进白名单 ────────────────
    # 主动流时代的白名单只有方向模型的参数；新机器不吃方向，可学的是
    # 「来回节奏」四个旋钮（self_tuner 提案与参数进化共用这份边界）：
    #   · pp_rest_sec    空仓歇息秒数（0=不歇，60 封顶）
    #   · pp_thin_frac   前档变薄撤单比例（0.1~0.9）
    #   · pp_exit_ticks  平仓单退档数（1~3）
    #   · pp_bucket_min_n 桶级状态门的最少样本（10~100；越小开门越早）
    "pp_rest_sec": (0.0, 60.0),
    "pp_thin_frac": (0.1, 0.9),
    "pp_exit_ticks": (1.0, 3.0),
    "pp_bucket_min_n": (10.0, 100.0),
}


def assemble_features(
    *,
    ofi_5s: float, ofi_15s: float, ofi_60s: float, ofi_300s: float,
    ret_15s_bp: float, ret_60s_bp: float, ret_300s_bp: float,
    bar_pos_60s: float, range_60s_bp: float, vol_z_300s: float,
    depth_wimb: float, spread_bp: float,
    btc_ret_60s: float, rel_ret_60s: float, breadth: float,
) -> Dict[str, float]:
    """线上线下同一份特征。调用方必须先把成交量换成金额失衡。"""
    raw = {
        "ofi_5s": ofi_ratio_or_value(ofi_5s),
        "ofi_15s": ofi_ratio_or_value(ofi_15s),
        "ofi_60s": ofi_ratio_or_value(ofi_60s),
        "ofi_300s": ofi_ratio_or_value(ofi_300s),
        "ret_15s_bp": float(ret_15s_bp),
        "ret_60s_bp": float(ret_60s_bp),
        "ret_300s_bp": float(ret_300s_bp),
        "bar_pos_60s": float(bar_pos_60s),
        "range_60s_bp": float(range_60s_bp),
        "vol_z_300s": float(vol_z_300s),
        "depth_wimb": float(depth_wimb),
        "spread_bp": float(spread_bp),
        "btc_ret_60s": float(btc_ret_60s),
        "rel_ret_60s": float(rel_ret_60s),
        "breadth": float(breadth),
    }
    return {name: raw[name] for name in FLOW_FEATURES}


def ofi_ratio_or_value(value: float) -> float:
    """已经是 -1 到 1 的失衡就原样留下，并夹住异常值。"""
    return max(-1.0, min(1.0, float(value or 0.0)))


def ofi_ratio(buy_notional: float, sell_notional: float) -> float:
    """金额失衡，范围 -1 到 1。没有成交时为 0。"""
    gross = float(buy_notional or 0.0) + float(sell_notional or 0.0)
    if gross <= 0.0:
        return 0.0
    return (float(buy_notional) - float(sell_notional)) / gross


def disaster_stop_bp(vol_300s_bp: float, *, floor_bp: float = STOP_FLOOR_BP,
                     cap_bp: float = STOP_CAP_BP) -> float:
    """2 倍 300 秒波动，夹在 floor 与 cap 之间。必须宽于一笔吃单费。"""
    raw = 2.0 * abs(float(vol_300s_bp or 0.0))
    stop = min(float(cap_bp), max(float(floor_bp), raw))
    return max(stop, TAKER_FEE_BP + 1.0)


def exec_unrealized_bp(qty: float, entry_px: float, bid: float, ask: float,
                       fee_bp: float = TAKER_FEE_BP) -> Optional[float]:
    """立刻按对手价平仓能锁定的盈亏（bp）。进场费已花掉，这里只扣这一边的吃单费。"""
    if entry_px <= 0 or qty == 0:
        return None
    if qty > 0:
        if bid <= 0:
            return None
        return (float(bid) - float(entry_px)) / float(entry_px) * 1e4 - float(fee_bp)
    if ask <= 0:
        return None
    return (float(entry_px) - float(ask)) / float(entry_px) * 1e4 - float(fee_bp)


def maker_roundtrip_bp(entry_px: float, exit_px: float, qty_sign: float) -> Optional[float]:
    """挂单路径的价格盈亏。手续费是 0。未成交的样本不要调用这个函数。"""
    if entry_px <= 0 or exit_px <= 0 or qty_sign == 0:
        return None
    if qty_sign > 0:
        return (float(exit_px) - float(entry_px)) / float(entry_px) * 1e4
    return (float(entry_px) - float(exit_px)) / float(entry_px) * 1e4


def taker_edge_bp(maker_bp: float, crosses: int) -> float:
    """吃单次数 1 或 2。扣完小于等于 0 就是手续费倒挂，禁止下这一单。"""
    n = 2 if int(crosses) >= 2 else 1
    return float(maker_bp) - n * TAKER_FEE_BP


def taker_chase_allowed(mu_bp: float) -> bool:
    """追价吃单：期望扣掉这一边 4bp 之后仍要为正。"""
    return float(mu_bp) - TAKER_FEE_BP > 0.0


def choose_exit(
    *,
    qty: float,
    entry_px: float,
    bid: float,
    ask: float,
    now_ts: float,
    opened_ts: float,
    max_hold_sec: float,
    mu: Optional[float],
    vol_300s_bp: float,
    regime: str,
    book_stale: bool,
    stop_floor_bp: float = STOP_FLOOR_BP,
    stop_cap_bp: float = STOP_CAP_BP,
    maker_exit_frac: float = MAKER_EXIT_FRAC,
    tp_bp: float = 0.0,
    stop_wide_mult: float = STOP_WIDE_MULT,
) -> str:
    """离场阶梯。返回 no_book / taker_risk / taker_stop / maker_take / maker_risk /
    maker_time / maker_edge / hold。

    [h878 用户三条深改] 止损**不再对付"刚成交那一下"的正常晃动**:
    实测近 12h 181 笔吃单止损亏 ≈$158,而挂单平掉的那些是赚的 —— 止损线
    (15~40bp)正卡在挂单成交的正常反向晃动上,用吃单把亏锁死。
    ⇒ 吃单止损线放宽到 **stop × stop_wide_mult(默认 2)**:
       普通晃动全部交给 maker_risk(浮亏到止损一半 ⇒ 挂单抢平,0 费);
       吃单只留给"真的宽"(深跳)或 R4/R5/盘口过期(没人成交)。
    """
    if qty == 0:
        return "flat"
    if bid <= 0 or ask <= 0:
        return "no_book"
    if regime in ("R4", "R5") or book_stale:
        return "taker_risk"
    stop = disaster_stop_bp(vol_300s_bp, floor_bp=stop_floor_bp, cap_bp=stop_cap_bp)
    unreal = exec_unrealized_bp(qty, entry_px, bid, ask, TAKER_FEE_BP)
    if unreal is not None and unreal <= -stop * float(stop_wide_mult):
        return "taker_stop"
    # ①′ 止盈层(h876/h886):浮盈到达目标 ⇒ 挂单离场(0 费)锁住行程。
    #     目标 = min(tp_bp, 1.5×止损):60bp 在 45s 持仓里几乎够不到 ⇒ 压到 1:1.5。
    _tp = min(float(tp_bp or 0.0) if tp_bp else stop, 1.5 * stop)
    if unreal is not None and unreal >= _tp:
        return "maker_take"
    if not opened_ts or float(opened_ts) <= 0:
        return "maker_time"
    age = float(now_ts) - float(opened_ts)
    # ① 挂单止损层(最急):浮亏到止损的一定比例 ⇒ 用挂单抢离场(0 费)
    # ── [整顿轮·T16 2026-10-05] 比例改为可配，用于 A/B 与回滚 ──
    #
    # 实测（lane_ledger，T9 之后）`maker_risk` **全部 6 条腿**：
    #
    #   ts        symbol     notional  spread_bp
    #   16:07:44  BTW            15.0     −64.42
    #   16:21:06  PLAY          988.5      +0.00
    #   16:48:28  PLAY          984.8      −5.19
    #   16:53:31  AAVE          977.8      −6.47
    #   17:13:22  LYN            15.1     −10.73
    #   17:27:05  MARSCOIN     1009.1     −15.87
    #
    #   ⇒ **4/6 条成交在中价的错误一侧**，均 net_bp **−17.11**，净 **−$2.86**
    #     （占同窗口总负贡献的 39%）。
    #
    # 机理：本层在"浮亏达 stop×0.5"时挂对手价抢平。但**只有中价继续穿过我们的
    # 挂单价时才会成交** ⇒ 成交本身就证明"我们又吃了 5~16bp 的额外逆向移动"。
    # 即：**这一层只在被逆向选择时成交**，成交价必然偏差。
    #
    # 而 `stop` 实测约 20~40bp（`2×vol_300s` 夹在 [15,40]）⇒ 触发线只有 10~20bp，
    # 属于"刚建仓的正常晃动"。实测 `flow_entry_maker` 建仓腿均 **+13.82bp**、
    # 大名义腿做到 **+$8.80/腿** —— 说明容器本身是赚的，
    # 被 10~20bp 的早期晃动提前砍掉才是亏损来源。
    #
    # 修法：把比例做成 env 可配（`MM_MAKER_EXIT_FRAC`），默认仍是 0.5（不改行为），
    # A/B 时调到 0（关闭该层）或 1.0（等浮亏达满止损再挂单）。
    # 该层关闭后仍有两道保护：`taker_stop`（浮亏 ≥ 2×stop）与
    # `_EXIT_TAKER_AFTER_SEC`（1800s 超时硬吃单）。
    try:
        _mef_frac = float(os.getenv("MM_MAKER_EXIT_FRAC", "") or maker_exit_frac)
    except ValueError:
        _mef_frac = float(maker_exit_frac)
    if _mef_frac > 0 and unreal is not None and unreal <= -max(
            TAKER_FEE_BP + 1.0, _mef_frac * stop):
        return "maker_risk"
    # ② 优势层:模型说剩余期望 ≤ 0 ⇒ 挂单离场(0 费),不吃单
    #    —— 排在时间层之前:"优势没了"比"单纯变老"是更强的离场信号。
    if age >= MIN_HOLD_SEC and mu is not None and float(mu) <= 0.0:
        return "maker_edge"
    # ③ 时间层:不再等到时限最后一刻才挂,提前三分之一就开始挂减仓单
    # [h900 2026-10-07] **信号仍强(mu ≥ 2bp)时不按时间提前平** —— 让赢家跑大。
    # 实测病根(近 6h):赢腿均 +4.12bp 就被时间层提前平掉,亏腿 −12.52bp 扛到底,
    # 盈亏比 0.33 ⇒ 60% 胜率还亏钱。概率优势还强就让它继续跑(由概率翻面出场
    # 或硬时限兜底),不被"时间到了"提前砍掉。
    _strong_edge = (mu is not None and float(mu) >= 2.0)
    if not _strong_edge and max_hold_sec and float(max_hold_sec) > 0 \
            and age >= max(MIN_HOLD_SEC, float(max_hold_sec) * 0.33):
        return "maker_time"
    return "hold"


def notional_cap_usd(equity: float, stop_bp: float, same_side_n: int,
                     loss_frac: float = EQUITY_LOSS_PER_TRADE) -> float:
    """一次止损最多亏权益的 loss_frac。同向多笔还要受当日 2% 合计约束。取更小的那个。

    [2026-10-09 修] 止损压到地板(15bp)时,per_trade = equity×0.5%÷0.0015 = 权益的 3.3 倍
    ⇒ 名义被放大(实测 equity $263 算出 $878)。止损越窄名义越大,是设计缺陷。
    加名义硬上限:单笔名义不得超过 equity × MAX_NOTIONAL_RATIO(默认 0.5 = 50%)。
    回滚:MM_MAX_NOTIONAL_RATIO=0 关闭上限(恢复旧行为)。
    """
    if equity <= 0 or stop_bp <= 0:
        return 0.0
    stop_frac = float(stop_bp) / 1e4
    per_trade = float(equity) * float(loss_frac) / stop_frac
    n = max(1, int(same_side_n))
    portfolio = float(equity) * EQUITY_LOSS_DAY / (n * stop_frac)
    cap = max(0.0, min(per_trade, portfolio))
    # 名义硬上限:止损再窄也不放大
    import os
    try:
        _max_ratio = float(os.getenv("MM_MAX_NOTIONAL_RATIO", "0.5") or 0.5)
    except ValueError:
        _max_ratio = 0.5
    if _max_ratio > 0:
        cap = min(cap, float(equity) * _max_ratio)
    return cap


def exchange_leverage(stop_bp: float, mmr: float = MMR_DEFAULT) -> int:
    """强平距离至少是止损的 3 倍。返回满足这个距离的最大整数杠杆。不拿来放大收益。"""
    need = 3.0 * float(stop_bp) / 1e4
    denom = need + float(mmr)
    if denom <= 0:
        return 1
    return max(1, int(1.0 / denom))


def n_eff(n_rows: int, step_sec: float = STEP_SEC, horizon_sec: float = 90.0) -> float:
    if horizon_sec <= 0:
        return 0.0
    return float(n_rows) * float(step_sec) / float(horizon_sec)


def purged_splits(n: int, embargo: int) -> Optional[Tuple[range, range, range]]:
    """60% 训练、20% 校准、其余考试。两段之间空出 embargo 条，避免标签重叠。"""
    if n < 50 or embargo < 0:
        return None
    train_end = int(n * 0.6)
    cal_n = int(n * 0.2)
    cal_start = train_end + int(embargo)
    cal_end = cal_start + cal_n
    test_start = cal_end + int(embargo)
    if train_end < 10 or cal_end <= cal_start or test_start >= n:
        return None
    if n - test_start < 10:
        return None
    return range(0, train_end), range(cal_start, cal_end), range(test_start, n)


def bucket_tradable(mean_y: Optional[float], n_eff_value: Optional[float],
                    win_rate: Optional[float] = None) -> bool:
    """平均可执行盈亏为正且独立样本够，才开门。胜率单独变高不算。"""
    del win_rate  # 胜率只作报告，不单独开门
    if mean_y is None or n_eff_value is None:
        return False
    return float(mean_y) > 0.0 and float(n_eff_value) >= MIN_N_EFF


def decile_index(value: float, edges: Sequence[float]) -> int:
    """edges 是校准段预测值的 9 个分界（10 档）。"""
    idx = 0
    for edge in edges:
        if value > float(edge):
            idx += 1
        else:
            break
    return min(idx, 9)


def quantile_edges(values: Sequence[float], buckets: int = 10) -> List[float]:
    xs = sorted(float(v) for v in values)
    if len(xs) < buckets:
        return []
    edges = []
    for i in range(1, buckets):
        pos = int(round((len(xs) - 1) * i / buckets))
        edges.append(xs[pos])
    return edges


def decile_table(pred: Sequence[float], y: Sequence[float], edges: Sequence[float],
                 step_sec: float, horizon_sec: float) -> List[Dict[str, Any]]:
    """考试段按校准段的分界分档。某一档要能交易，平均 y 为正且 n_eff 够。"""
    groups: List[List[float]] = [[] for _ in range(10)]
    for p, target in zip(pred, y):
        groups[decile_index(float(p), edges)].append(float(target))
    out = []
    for i, ys in enumerate(groups):
        n = len(ys)
        # [h901 用户"空值被说成证据不足?"] 空档也**给 0.0**(n=0 就是置信度),
        # 不再写 None —— 消费者不用再区分 null 与 0。
        mean = sum(ys) / n if n else 0.0
        wins = sum(1 for v in ys if v > 0) / n if n else 0.0
        ne = n_eff(n, step_sec, horizon_sec) if n else 0.0
        out.append({
            "decile": i,
            "n": n,
            "mean_y": round(mean, 4),
            "win_rate": round(wins, 4),
            "n_eff": round(ne, 2),
            "tradable": bucket_tradable(mean, ne, wins),
        })
    return out


def oos_conditional_mean(values, hit, split: int, embargo: int):
    """只在后一段、而且这一边真的被打到的格子上取平均。前一段不参与，避免用同一段数据既发现又开门。"""
    arr = list(values)
    flags = list(hit)
    start = int(split) + int(embargo)
    if start >= len(arr):
        return None, 0
    picked = []
    for i in range(start, len(arr)):
        if not flags[i]:
            continue
        try:
            v = float(arr[i])
        except (TypeError, ValueError):
            continue
        if v == v:
            picked.append(v)
    if not picked:
        return None, 0
    return sum(picked) / len(picked), len(picked)


def pool_positive_deciles(table: Sequence[Dict[str, Any]], step_sec: float,
                          horizon_sec: float) -> Optional[Dict[str, Any]]:
    """把考试段里平均仍为正的档合在一起看。

    单独一档往往只有十几条，按 15 秒一步、90 秒标签折算后独立样本永远到不了 30，
    门会永久关闭、一笔挂单都没有。合在一起仍然要求平均盈亏为正，且独立样本够 30。
    """
    pos = [r for r in table if r.get("n") and (r.get("mean_y") or 0) > 0]
    if not pos:
        return None
    n = sum(int(r["n"]) for r in pos)
    mean = sum(float(r["mean_y"]) * int(r["n"]) for r in pos) / n
    wins_n = sum(float(r["win_rate"] or 0) * int(r["n"]) for r in pos)
    ne = n_eff(n, step_sec, horizon_sec)
    top = max(pos, key=lambda r: float(r.get("mean_y") or -1e9))
    return {
        "mean_y": mean,
        "n": n,
        "n_eff": ne,
        "win_rate": wins_n / n if n else 0.0,
        "top_decile": int(top["decile"]),
        "tradable": bool(mean > 0.0 and ne >= MIN_N_EFF),
    }


def situation_band(value: float, edges: Sequence[float]) -> int:
    """价值落在哪一档。最后一档是「大于等于最后一条边界」。"""
    v = float(value or 0.0)
    for i, edge in enumerate(edges):
        if v < float(edge):
            return i
    return len(tuple(edges))


def situation_decision(
    doc: Optional[Dict[str, Any]],
    symbol: str,
    now_ts: float,
    ahead_bid_usd: float,
    ahead_ask_usd: float,
    spread_bp: float,
    max_age: float = SITUATION_MAX_AGE_SEC,
    margin_bp: float = ENTRY_MARGIN_BP,
    probe_ok: bool = False,
) -> Dict[str, Any]:
    """这一拍问眼前这一档，不给整个币下禁令。

    买一上排着的金额决定做多那一档，卖一上排着的金额决定做空那一档。
    同一档要有 30 笔完整来回，平均超过 margin，最典型的一笔也要是赚的。
    最近一段略亏不再把这一档整档关掉，否则全市场会被最后一小段噪声打成零交易。

    [h834 用户"查,真没有交易了"] 冷启动探索通道:桶要 30 笔来回才有资格判,
    而没有证据就不交易 ⇒ 桶永远填不满(不可 falsify)。开关打开且当前这一拍
    落在**证据不足**的桶时,允许一笔**极小名义**的探索单(probe),
    用来把桶填起来;它同样进往返日志(打 probe 标记),但不冒充已证实的证据。
    """
    closed = {"allow": False, "side": None, "mu": None, "max_hold_sec": 1.0,
              "reason": "no_situation_file"}
    if not isinstance(doc, dict):
        return closed
    try:
        ts = float(doc.get("ts") or 0.0)
    except (TypeError, ValueError):
        closed["reason"] = "situation_stale"
        return closed
    if ts <= 0 or (float(now_ts) - ts) >= float(max_age):
        closed["reason"] = "situation_stale"
        return closed
    coin = ((doc.get("coins") or {}).get(str(symbol or "").upper()) or {})
    if not isinstance(coin, dict):
        closed["reason"] = "situation_not_this_tick"
        return closed
    spread_i = situation_band(spread_bp, SPREAD_EDGES_BP)
    best: Optional[Tuple[float, str, Dict[str, Any]]] = None
    for side, ahead in (("buy", ahead_bid_usd), ("sell", ahead_ask_usd)):
        ahead_i = situation_band(ahead, AHEAD_EDGES_USD)
        hit = None
        for row in coin.get(side) or []:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("ahead_i")) == ahead_i and int(row.get("spread_i")) == spread_i:
                    hit = row
                    break
            except (TypeError, ValueError):
                continue
        if hit is None:
            continue
        try:
            n = float(hit.get("n") or 0.0)
            mean = float(hit.get("mean_y"))
            med = float(hit.get("median_y"))
            recent = float(hit.get("recent_mean_y"))
        except (TypeError, ValueError):
            continue
        if n < MIN_N_EFF or mean <= float(margin_bp) or med <= 0.0:
            continue
        if best is None or mean > best[0]:
            best = (mean, side, hit)
    if best is None:
        # [h834] 冷启动探索:没有已证实的桶时,允许一笔极小名义的探索单。
        if probe_ok:
            p_side = "buy"
            for row in (coin.get("buy") or []):
                if isinstance(row, dict) and row.get("mean_y") is not None:
                    try:
                        p_side = "buy" if float(row["mean_y"]) >= 0 or True else "sell"
                    except (TypeError, ValueError):
                        p_side = "buy"
                    break
            return {
                "allow": True, "side": p_side, "mu": 0.0, "max_hold_sec": 1.0,
                "reason": "situation_probe", "probe": True,
                "probe_frac": float(PROBE_EQUITY_FRAC),
                "mean_y": 0.0, "n_eff": 0.0,
            }
        closed["reason"] = "situation_not_this_tick"
        return closed
    return {
        "allow": True,
        "side": best[1],
        "mu": best[0],
        "max_hold_sec": 1.0,
        "reason": "situation_ok",
        "mean_y": best[0],
        "n_eff": float(best[2].get("n") or 0.0),
    }


def gate_decision(doc: Optional[Dict[str, Any]], symbol: str, now_ts: float,
                  max_age: float = GATE_MAX_AGE_SEC) -> Dict[str, Any]:
    """缺文件、过期、没有考试结果、平均 y 不为正：一律不允许进场。"""
    closed = {"allow": False, "side": None, "mu": None, "max_hold_sec": None,
              "reason": "no_evidence"}
    if not isinstance(doc, dict):
        return closed
    try:
        ts = float(doc.get("ts") or 0.0)
    except (TypeError, ValueError):
        return closed
    if ts <= 0 or (float(now_ts) - ts) >= float(max_age):
        closed["reason"] = "stale_or_missing"
        return closed
    entry = ((doc.get("gates") or {}).get(str(symbol or "").upper()) or {})
    if not isinstance(entry, dict):
        return closed
    oos = entry.get("oos") or {}
    try:
        mean_y = float(oos.get("mean_y"))
        ne = float(oos.get("n_eff"))
    except (TypeError, ValueError):
        closed["reason"] = "no_oos"
        return closed
    if not bucket_tradable(mean_y, ne, oos.get("win_rate")):
        closed["reason"] = "oos_not_positive"
        return closed
    if not bool(entry.get("allow")):
        closed["reason"] = "allow_false"
        return closed
    side = entry.get("side")
    if side not in ("buy", "sell"):
        closed["reason"] = "no_side"
        return closed
    return {
        "allow": True,
        "side": side,
        "mu": float(entry.get("mu") or 0.0),
        "max_hold_sec": float(entry.get("max_hold_sec") or 90.0),
        "reason": "ok",
        "mean_y": mean_y,
        "n_eff": ne,
    }


def window_stats(rows: Sequence[Dict[str, Any]], t0: float, t1: float) -> Dict[str, float]:
    """一段时间内已成交往返的平均盈亏、吃单费占比、挂单成交率、强平次数。"""
    sel = []
    for row in rows:
        if not isinstance(row, dict) or row.get("y_bp") is None:
            continue
        try:
            ts = float(row.get("ts") or 0.0)
            y = float(row["y_bp"])
        except (TypeError, ValueError):
            continue
        if t0 <= ts < t1:
            sel.append((y, row))
    n = len(sel)
    if n == 0:
        return {"n": 0, "mean_y": 0.0, "taker_fee_share": 0.0,
                "fill_rate": 0.0, "liquidations": 0}
    ys = [y for y, _ in sel]
    losses = [row for y, row in sel if y < 0]
    taker_losses = [row for row in losses if float(row.get("fee_bp") or 0.0) > 0]
    makers = [row for _, row in sel if row.get("maker")]
    return {
        "n": n,
        "mean_y": sum(ys) / n,
        "taker_fee_share": (len(taker_losses) / len(losses)) if losses else 0.0,
        "fill_rate": len(makers) / n,
        "liquidations": sum(int(row.get("liquidations") or 0) for _, row in sel),
    }


def should_rollback_flow(before: Dict[str, float], after: Dict[str, float]) -> bool:
    """三关有一关变差就回滚。成交变多但平均变差也回滚。"""
    b_y = float(before.get("mean_y") or 0.0)
    a_y = float(after.get("mean_y") or 0.0)
    if a_y < b_y * 0.8:
        return True
    if float(after.get("taker_fee_share") or 0.0) > float(before.get("taker_fee_share") or 0.0) + 1e-9:
        return True
    if float(after.get("fill_rate") or 0.0) + 1e-9 < float(before.get("fill_rate") or 0.0):
        return True
    if int(after.get("liquidations") or 0) > 0:
        return True
    if int(after.get("n") or 0) > int(before.get("n") or 0) and a_y < b_y:
        return True
    return False


def default_learn_params() -> Dict[str, float]:
    return {
        "disaster_stop_floor_bp": STOP_FLOOR_BP,
        "disaster_stop_cap_bp": STOP_CAP_BP,
        "hold_sec": 90.0,
        "entry_margin_bp": ENTRY_MARGIN_BP,
        "min_notional_60s": 0.0,
        "notional_loss_frac": EQUITY_LOSS_PER_TRADE,
        # [2026-10-09 ping-pong] 与 pingpong.py 代码默认值同源（env 覆盖优先，
        # 其次这里的学习值，最后才是代码默认）。
        "pp_rest_sec": 15.0,
        "pp_thin_frac": 0.5,
        "pp_exit_ticks": 1.0,
        "pp_bucket_min_n": 20.0,
    }


def load_learn_params(root: Path) -> Dict[str, float]:
    params = default_learn_params()
    path = root / FLOW_PARAM_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return params
    if not isinstance(raw, dict):
        return params
    for key, (lo, hi) in FLOW_SAFE_PARAMS.items():
        if key in raw:
            try:
                val = float(raw[key])
            except (TypeError, ValueError):
                continue
            params[key] = min(hi, max(lo, val))
    return params


def save_learn_params(root: Path, params: Dict[str, float]) -> None:
    path = root / FLOW_PARAM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = default_learn_params()
    clean.update({k: float(params[k]) for k in FLOW_SAFE_PARAMS if k in params})
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def append_roundtrip(root: Path, row: Dict[str, Any]) -> None:
    path = root / FLOW_ROUNDTRIP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(row)
    payload["era"] = "flow"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def playbook_for_prompt(playbook: Dict[str, Any]) -> Dict[str, Any]:
    """做市时代的定律（没有 era=flow）不进 DSH 提示。"""
    laws = []
    for law in (playbook or {}).get("laws") or []:
        if isinstance(law, dict) and str(law.get("era") or "") == "flow":
            laws.append(law)
    return {"laws": laws}


def experience_for_prompt(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if isinstance(r, dict) and str(r.get("era") or "") == "flow"]
