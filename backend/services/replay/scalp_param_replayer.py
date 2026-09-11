# -*- coding: utf-8 -*-
"""短线参数离线回放器（2026-09-02 P1.4）。

定位
====
把"改一个参数值多少 bp"从争论变成可计算的问题。回放不下任何真单、不花一分手续
费，输入是已落库的历史信号（`scalp_signal_log`）+ 5m OHLC，输出是**含成本的净
收益 bp**。

为什么必须有它
--------------
此前每次调参都只能事后看盘面（改完等几天看盈亏），而盘面同时被行情、仓位、其他
参数变化污染，根本分不清是哪一项的效果。实测教训：2026-09-01 一次性放宽了扫描
间隔/冷却/门槛/每 tick 开仓数，日成交量从 3 笔跳到 105 笔、胜率从 42% 掉到 25%
—— 事后才发现是配置漂移，损失已经发生。

口径
----
- 出场判定复用 `scalp_signal_logger.triple_barrier_outcome`，与线上 TB 标签**同一
  份代码**，避免"回放赚钱、上线亏钱"的口径分裂。
- 成本按往返 `cost`（默认取 SCALP_META_COST=8bp）一次性扣除。
- TP/SL 可被参数覆盖：这正是回答"止盈设多远"的关键 —— 实测计划 TP 2.27% 而实际
  MFE 仅 0.57%（MFE/TP=0.25），止盈目标比市场能给的高 3 倍。

局限（务必知道）
----------------
1. **只回放"已经产生的信号"**。放宽入场门槛会产生历史上不存在的新信号，回放无法
   凭空造出它们；收紧门槛（子集筛选）才是无偏的。
2. 5m 粒度下同根 K 线内 TP/SL 先后不可知，一律判 SL（保守，见 TB 文档）。
3. 不建模滑点与部分成交，也不建模仓位大小 —— 输出是"每笔平均净收益 bp"，
   不是账户曲线。
4. 信号落库需带 pwin/tp/sl 才能参与相应筛选，早期样本可能缺失。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ReplayParams:
    """一组待评估的参数。None 表示"不施加该约束"。"""
    name: str = "baseline"
    direction: Optional[str] = None        # long / short / None(两者)
    min_pwin: Optional[float] = None
    max_pwin: Optional[float] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    tp_pct: Optional[float] = None          # 覆盖信号自带 TP 距离
    sl_pct: Optional[float] = None
    tp_mult: Optional[float] = None         # 或按倍数缩放原 TP
    sl_mult: Optional[float] = None
    max_hold_sec: Optional[int] = None
    symbols: Optional[Tuple[str, ...]] = None
    exclude_symbols: Optional[Tuple[str, ...]] = None
    # [2026-09-02] 时间窗切分：参数寻优极易拟合样本期的行情方向。首轮实测就
    # 撞上这个坑 —— "TP 越远越赚、持仓越久越赚"（7200s→+43bp、14400s→+51bp）
    # 是上涨段做多的 beta，不是策略 alpha。任何寻优结论都必须先过分段稳健性
    # 检验（见 robustness_check）才允许上线。
    since_ts: Optional[int] = None
    until_ts: Optional[int] = None


@dataclass
class ReplayResult:
    name: str
    n: int = 0
    win_rate: float = 0.0
    net_bp: float = 0.0            # 每笔平均净收益（bp）
    total_bp: float = 0.0          # 总贡献（bp），衡量规模
    gross_bp: float = 0.0          # 未扣成本
    tp_rate: float = 0.0
    sl_rate: float = 0.0
    timeout_rate: float = 0.0
    avg_hold_min: float = 0.0
    median_hold_min: float = 0.0
    by_kind: Dict[str, Dict[str, float]] = field(default_factory=dict)
    skipped: int = 0

    def summary(self) -> str:
        return (
            f"{self.name:<30} n={self.n:>6} 胜率={self.win_rate:>5.1f}% "
            f"净={self.net_bp:>+8.2f}bp 总={self.total_bp:>+10.0f}bp "
            f"tp={self.tp_rate:>4.1f}% sl={self.sl_rate:>4.1f}% "
            f"to={self.timeout_rate:>4.1f}% 持仓中位={self.median_hold_min:>5.1f}min"
        )


@dataclass
class _Sample:
    """一条历史信号的回放输入（已与 K 线解耦，可反复扫参数）。"""
    symbol: str
    signal_ts: int
    direction: str
    entry: float
    tp_pct: float
    sl_pct: float
    pwin: Optional[float]
    score: Optional[float]


def _default_cost() -> float:
    try:
        return float(os.getenv("SCALP_META_COST", "0.0008") or 0.0008)
    except (TypeError, ValueError):
        return 0.0008


class ScalpParamReplayer:
    """加载一次数据，反复回放多组参数。

    典型用法::

        rp = ScalpParamReplayer(days=14)
        rp.load()
        base = rp.replay(ReplayParams(name="现状"))
        alt  = rp.replay(ReplayParams(name="TP=0.8%", tp_pct=0.008))
        print(alt.net_bp - base.net_bp)   # 这个改动值多少 bp
    """

    def __init__(
        self,
        *,
        days: int = 14,
        limit: int = 200_000,
        cost: Optional[float] = None,
        kline_lookback: int = 6000,
    ):
        self.days = int(days)
        self.limit = int(limit)
        self.cost = float(cost) if cost is not None else _default_cost()
        self.kline_lookback = int(kline_lookback)
        self._samples: List[_Sample] = []
        self._ohlc: Dict[str, List[Tuple[int, float, float, float]]] = {}
        self._loaded = False

    # ── 数据装载 ────────────────────────────────────────────────
    def load(self) -> int:
        """从库里取信号 + 每个 symbol 取一次 OHLC。返回样本数。"""
        from sqlalchemy import text

        from backend.database.connection import SessionLocal

        since = int(time.time()) - self.days * 86400
        rows: List[Any] = []
        with SessionLocal() as db:
            rows = db.execute(text("""
                SELECT symbol, signal_ts, direction, entry_price,
                       tb_tp_pct, tb_sl_pct, factor_score, features_json
                FROM scalp_signal_log
                WHERE signal_ts >= :since
                  AND direction IN ('long','short')
                  AND entry_price > 0
                ORDER BY signal_ts DESC
                LIMIT :lim
            """), {"since": since, "lim": self.limit}).fetchall()

        from backend.services.scalp_signal_logger import _tb_backfill_tpsl
        bf_tp, bf_sl = _tb_backfill_tpsl()

        self._samples = []
        for r in rows:
            pwin = None
            fj = r.features_json or ""
            if "meta_p_win" in fj:
                try:
                    pwin = json.loads(fj).get("meta_p_win")
                    pwin = float(pwin) if pwin is not None else None
                except (ValueError, TypeError):
                    pwin = None
            self._samples.append(_Sample(
                symbol=str(r.symbol or "").upper(),
                signal_ts=int(r.signal_ts or 0),
                direction=str(r.direction or ""),
                entry=float(r.entry_price or 0),
                tp_pct=float(r.tb_tp_pct or bf_tp),
                sl_pct=float(r.tb_sl_pct or bf_sl),
                pwin=pwin,
                score=(float(r.factor_score) if r.factor_score is not None else None),
            ))

        # OHLC 按 symbol 各取一次（回放期间复用，扫参数不再查库）
        _prev = os.environ.get("SCALP_SETTLE_KLINE_LOOKBACK")
        os.environ["SCALP_SETTLE_KLINE_LOOKBACK"] = str(self.kline_lookback)
        try:
            from backend.services.scalp_signal_logger import _load_ohlc
            for sym in {s.symbol for s in self._samples}:
                _load_ohlc(sym, self._ohlc)
        finally:
            if _prev is None:
                os.environ.pop("SCALP_SETTLE_KLINE_LOOKBACK", None)
            else:
                os.environ["SCALP_SETTLE_KLINE_LOOKBACK"] = _prev

        self._loaded = True
        _with_k = sum(1 for s in self._samples if self._ohlc.get(s.symbol))
        logger.info(
            "[Replay] 装载 %d 条信号 / %d 个币种，其中 %d 条有 K 线",
            len(self._samples), len(self._ohlc), _with_k,
        )
        return len(self._samples)

    # ── 单组回放 ────────────────────────────────────────────────
    def replay(self, params: ReplayParams) -> ReplayResult:
        if not self._loaded:
            self.load()
        from backend.services.scalp_signal_logger import triple_barrier_outcome

        now = int(time.time())
        res = ReplayResult(name=params.name)
        nets: List[float] = []
        holds: List[int] = []
        kinds: Dict[str, List[float]] = {}

        for s in self._samples:
            if not self._passes(s, params):
                continue
            ohlc = self._ohlc.get(s.symbol)
            if not ohlc:
                res.skipped += 1
                continue

            tp = self._resolve_dist(s.tp_pct, params.tp_pct, params.tp_mult)
            sl = self._resolve_dist(s.sl_pct, params.sl_pct, params.sl_mult)
            max_hold = int(params.max_hold_sec or self._default_max_hold_sec())

            out = triple_barrier_outcome(
                ohlc, start_ts=s.signal_ts, entry=s.entry,
                direction=s.direction, tp_pct=tp, sl_pct=sl,
                max_hold_sec=max_hold, now_ts=now,
            )
            if out is None:
                res.skipped += 1
                continue
            net = float(out["fwd_ret"]) - self.cost
            nets.append(net)
            holds.append(int(out["hold_sec"]))
            kinds.setdefault(str(out["kind"]), []).append(net)

        return self._finalize(res, nets, holds, kinds)

    def grid(self, base: ReplayParams, **axes: Iterable) -> List[ReplayResult]:
        """沿一或多个轴扫参数。

        例：``rp.grid(ReplayParams(direction="long"), tp_pct=[0.006,0.008,0.01])``
        多个轴时取笛卡尔积。
        """
        import itertools

        keys = list(axes.keys())
        out: List[ReplayResult] = []
        for combo in itertools.product(*(list(axes[k]) for k in keys)):
            kw = asdict(base)
            label_bits = []
            for k, v in zip(keys, combo):
                kw[k] = v
                label_bits.append(f"{k}={v}")
            kw["name"] = f"{base.name}|" + ",".join(label_bits)
            out.append(self.replay(ReplayParams(**kw)))
        return out

    # ── 内部 ────────────────────────────────────────────────────
    @staticmethod
    def _resolve_dist(
        original: float, override: Optional[float], mult: Optional[float],
    ) -> float:
        """绝对值覆盖优先于倍数缩放；都没给就用信号原值。"""
        if override is not None and float(override) > 0:
            return float(override)
        if mult is not None and float(mult) > 0:
            return max(1e-5, float(original) * float(mult))
        return float(original)

    @staticmethod
    def _default_max_hold_sec() -> int:
        """参数未指定 max_hold 时的默认值：与线上执行/标签结算同一来源。

        [2026-09-02 审查修正] 原写死 7200，而线上 TIER_PROTECTION_PARAMS['short']
        ['max_hold_sec'] 已在 P2.2 调到 5400，标签结算(_tb_max_hold_sec)也已动态跟随。
        回放器若仍用 7200，"baseline"这一档评估的就不是线上正在跑的策略——参数
        寻优的基准本身就偏了。改为复用结算侧同一函数，三处口径（执行/标签/回放）
        只有一个真源。
        """
        try:
            from backend.services.scalp_signal_logger import _tb_max_hold_sec
            return int(_tb_max_hold_sec())
        except Exception:  # pragma: no cover - 兜底不改变旧行为
            return 7200

    @staticmethod
    def _passes(s: _Sample, p: ReplayParams) -> bool:
        if p.direction and s.direction != p.direction:
            return False
        if p.since_ts is not None and s.signal_ts < p.since_ts:
            return False
        if p.until_ts is not None and s.signal_ts >= p.until_ts:
            return False
        if p.min_pwin is not None:
            if s.pwin is None or s.pwin < p.min_pwin:
                return False
        if p.max_pwin is not None:
            if s.pwin is None or s.pwin >= p.max_pwin:
                return False
        if p.min_score is not None:
            if s.score is None or s.score < p.min_score:
                return False
        if p.max_score is not None:
            if s.score is None or s.score >= p.max_score:
                return False
        if p.symbols and s.symbol not in p.symbols:
            return False
        if p.exclude_symbols and s.symbol in p.exclude_symbols:
            return False
        return True

    @staticmethod
    def _finalize(
        res: ReplayResult,
        nets: List[float],
        holds: List[int],
        kinds: Dict[str, List[float]],
    ) -> ReplayResult:
        res.n = len(nets)
        if not res.n:
            return res
        res.win_rate = sum(1 for x in nets if x > 0) / res.n * 100
        res.net_bp = sum(nets) / res.n * 10000
        res.total_bp = sum(nets) * 10000
        res.gross_bp = res.net_bp  # gross 由调用方按 cost 反推，避免重复口径
        res.avg_hold_min = sum(holds) / len(holds) / 60
        _sorted = sorted(holds)
        res.median_hold_min = _sorted[len(_sorted) // 2] / 60
        for k in ("tp", "sl", "timeout"):
            v = kinds.get(k, [])
            rate = len(v) / res.n * 100
            setattr(res, f"{k}_rate", rate)
        for k, v in kinds.items():
            res.by_kind[k] = {
                "n": len(v),
                "share_pct": round(len(v) / res.n * 100, 2),
                "net_bp": round(sum(v) / len(v) * 10000, 2) if v else 0.0,
            }
        return res


def robustness_check(
    replayer: "ScalpParamReplayer",
    params: ReplayParams,
    *,
    folds: int = 3,
) -> Dict[str, Any]:
    """把样本期切成连续时间段，逐段回放同一组参数。

    为什么这是上线前的必过关卡：参数寻优天然会拟合样本期的行情方向。首轮实测
    "TP 越远越赚、持仓越久越赚"（7200s→+43bp、14400s→+51bp）看着诱人，但它本质
    是上涨段里做多拿得久的 beta —— 换成下跌段会全额反向。

    判定口径（`verdict`）：
    - ``robust``   : 所有**有效**分段净收益为正 —— 参数在不同行情下都成立。
    - ``fragile``  : 有效分段有正有负 —— 结论依赖特定行情，不可直接上线。
    - ``negative`` : 有效分段全为负 —— 明确劣于基线。
    - ``thin``     : 有效分段不足 2 个，统计上说不了话。

    "有效分段"指样本 >= `min_fold_n` 的段。之所以要区分：实测 pwin>=0.55 做多的
    四段样本是 93/2292/44/113，若让 44 条的那段与 2292 条的那段等权投票，几百条
    样本就能翻转整体结论。因此同时返回 `weighted_net_bp`（按样本加权），它才是
    "这组参数在全样本上到底赚不赚"的答案；`verdict` 只回答"跨行情是否一致"。
    """
    if not replayer._loaded:
        replayer.load()
    min_fold_n = max(1, int(os.getenv("REPLAY_MIN_FOLD_N", "50") or 50))
    ts_all = sorted(s.signal_ts for s in replayer._samples if s.signal_ts > 0)
    if len(ts_all) < folds * 10:
        # 返回与正常路径同构的字典：调用方（含面板）不该为 early return 写分支
        return {
            "verdict": "thin", "folds": [], "reason": "总样本不足",
            "valid_folds": 0, "min_fold_n": min_fold_n,
            "net_bp_range": [0, 0], "net_bp_mean": 0.0,
            "weighted_net_bp": 0.0, "total_n": len(ts_all),
        }

    lo, hi = ts_all[0], ts_all[-1] + 1
    edges = [lo + (hi - lo) * i // folds for i in range(folds + 1)]
    out: List[Dict[str, Any]] = []
    for i in range(folds):
        kw = asdict(params)
        kw["since_ts"], kw["until_ts"] = edges[i], edges[i + 1]
        kw["name"] = f"{params.name}|fold{i + 1}"
        r = replayer.replay(ReplayParams(**kw))
        out.append({
            "fold": i + 1,
            "from": time.strftime("%m-%d %H:%M", time.localtime(edges[i])),
            "to": time.strftime("%m-%d %H:%M", time.localtime(edges[i + 1])),
            "n": r.n, "win_rate": round(r.win_rate, 1),
            "net_bp": round(r.net_bp, 2),
            "tp_rate": round(r.tp_rate, 1),
            "timeout_rate": round(r.timeout_rate, 1),
        })

    valid = [f for f in out if f["n"] >= min_fold_n]
    nets = [f["net_bp"] for f in out]
    v_nets = [f["net_bp"] for f in valid]
    if len(valid) < 2:
        verdict = "thin"
    elif all(x > 0 for x in v_nets):
        verdict = "robust"
    elif all(x <= 0 for x in v_nets):
        verdict = "negative"
    else:
        verdict = "fragile"

    # 样本加权净收益：小样本段不得以等权方式翻转全样本结论
    _tot_n = sum(f["n"] for f in out)
    _weighted = (
        round(sum(f["net_bp"] * f["n"] for f in out) / _tot_n, 2)
        if _tot_n else 0.0
    )
    return {
        "verdict": verdict, "folds": out,
        "valid_folds": len(valid), "min_fold_n": min_fold_n,
        "net_bp_range": [min(nets), max(nets)] if nets else [0, 0],
        "net_bp_mean": round(sum(nets) / len(nets), 2) if nets else 0.0,
        "weighted_net_bp": _weighted,
        "total_n": _tot_n,
    }


def compare(results: List[ReplayResult], *, baseline: int = 0) -> str:
    """把多组结果排成一张对比表，附相对基线的 bp 差值。"""
    if not results:
        return "(无结果)"
    base = results[baseline]
    lines = [f"基线: {base.summary()}", "-" * 118]
    for i, r in enumerate(results):
        if i == baseline:
            continue
        delta = r.net_bp - base.net_bp
        lines.append(f"{r.summary()}  Δ={delta:>+8.2f}bp")
    return "\n".join(lines)
