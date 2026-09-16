# -*- coding: utf-8 -*-
"""[F305 2026-09-16] LLM 假设闭环：假设 → 编译成谓词 → walk-forward 门禁 → 保留/丢弃。

━━━ 为什么这么设计（尊重平台既有分工）━━━
平台**已经有** LLM 提案链（`factor_engine/llm_proposal.py`：LLM 出 numpy 公式因子）
与**四层 AST 白名单沙箱**（`factor_engine/code_safety.py`）。所以本模块不重造这两样：

  · 沙箱复用 `code_safety.ast_whitelist_check` + `forbidden_literal_scan`。
    这两个函数的设计前提是"裸名调用必须在白名单内"，因此我把 LLM 谓词**包成函数定义**
    `def _pred(**FEATURES): return <expr>` —— 特征名成为**局部形参绑定**，
    正好落进它的 L2/L4 规则（既放行合法特征引用，又挡住外来名走私）。
  · LLM 调用复用 `llm_config_service.get_llm_config_for_usage` + `call_llm_api_sync`。
    无配置时**显式降级**并如实返回 `llm_unavailable`，绝不产生假候选
    （平台既有教训：占位实现会造出"假候选"，比没有更糟）。

━━━ 门禁口径（本模块自己的判断，不依赖他人回放）━━━
LLM 只能填**阈值与布尔组合**，特征本身在 `compute_features()` 里用真实 tape 因果计算。
这样做的理由：让 LLM 写特征计算会引入 look-ahead 风险，且 AST 面会膨胀。
谓词输出 True 表示"此刻做多"（False/0 = 不动作；-1 表示做空）。

收益口径与车道一致：**被动进场 + 固定持有后按中间价出场**（Aster maker 0bp），
所以 `net_bp = 方向 × 前向中间价变化(bp)`。walk-forward：6 折，每折在
**前 70% 训练窗**上确认符号与方向一致，再在**后续 30% 测试窗**上取样本外收益。

保留条件（全部满足才 keep）：
  1. 每折 OOS 净期望 > 0（不是平均 > 0）
  2. 每折 OOS 的 t > 2
  3. 每折 OOS 事件数 ≥ min_oos_events
  4. 实际符号与 LLM 声明的 `direction` 一致（防止"符号反了但碰巧为正"）
  5. 样本外合计事件数 ≥ min_total_events
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 回滚开关 ───────────────────────────────────────────────────────────
# 环境变量 LLM_HYPOTHESIS_ENABLED=0 立即停用（不需要改库、不需要重启代码）。
ENV_ENABLED = "LLM_HYPOTHESIS_ENABLED"

# 特征词汇表：LLM 只能用这些名字。全部在 compute_features() 里**因果**计算
# （只用第 i 根及之前的数据），因此谓词本身无法引入未来数据。
FEATURES: Tuple[str, ...] = (
    "ofi",            # 订单流失衡 ∈[-1,1]，买量−卖量 / 总量
    "ofi_ewma",       # ofi 的指数均值（平滑后的主动方向）
    "trade_intensity",# 本桶成交量 / 近 20 桶均量（>1 = 放量）
    "ret_1",          # 上一桶中间价收益（bp）
    "ret_5",          # 近 5 桶累计收益（bp）
    "vol_bp",         # 近 20 桶中间价收益标准差（bp，实现波动）
    "spread_bp",      # 当前相对价差（bp）
    "depth_ratio",    # 买量/卖量（盘口不平衡不在快照里 ⇒ 用成交侧代替，见下）
    "range_bp",       # 本桶 (high−low)/mid（bp）
    "hour_sin",       # 时刻的周期编码
    "hour_cos",
    "bars_since_shock",  # 距上次 |ret_1| > 3×vol 的桶数
)

BAR_MS_DEFAULT = 15_000
LOOKBACK_DEFAULT = 20


# ────────────────────────── 数据类型 ──────────────────────────

@dataclass
class Hypothesis:
    """LLM 产出的一条假设。"""

    hypothesis_id: str
    symbol: str
    statement: str            # 自然语言假设（经济逻辑，供人审）
    predicate: str            # 可编译的布尔表达式
    direction: int            # +1 做多 / -1 做空（LLM 声明的预期方向）
    expected_edge_bp: float = 0.0
    source: str = "llm"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FoldResult:
    fold: int
    train_n: int
    oos_n: int
    oos_net_bp: float
    oos_t: float
    train_net_bp: float
    sign_ok: bool


@dataclass
class GateVerdict:
    hypothesis_id: str
    symbol: str
    kept: bool
    reason: str
    folds: List[FoldResult] = field(default_factory=list)
    oos_total_n: int = 0
    oos_total_net_bp: float = 0.0
    oos_total_t: float = 0.0
    compile_ok: bool = False
    compile_reason: str = ""
    statement: str = ""
    predicate: str = ""
    direction: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["folds"] = [asdict(f) for f in self.folds]
        return d

    def edge_json(self) -> Dict[str, Any]:
        """折算成 `lane_registry.edge_json` 口径（供晋升判定复用）。"""
        return {
            "source": "paper_shadow",
            "n": int(self.oos_total_n),
            "gross_bp": round(self.oos_total_net_bp, 4),
            "cost_bp": 0.0,          # 被动进出，Aster maker 0bp
            "net_bp": round(self.oos_total_net_bp, 4),
            "folds": [{"fold": f.fold, "n": f.oos_n,
                       "net_bp": round(f.oos_net_bp, 4),
                       "t": round(f.oos_t, 3)} for f in self.folds],
            "max_dd_pct": None,
            "fill_rate_ratio": None,
            "as_of": None,
            "note": ("LLM 假设闭环 OOS 口径：被动进场 + 固定持有按中间价出场；"
                     "每折均为样本外"),
        }


@dataclass
class GateParams:
    """门禁参数（回滚/收紧都改这里）。"""

    folds: int = 6
    train_frac: float = 0.70
    min_oos_events: int = 20
    min_fold_t: float = 2.0
    min_total_events: int = 200
    require_all_folds_positive: bool = True
    require_direction_match: bool = True
    bar_ms: int = BAR_MS_DEFAULT
    lookback: int = LOOKBACK_DEFAULT
    horizon_bars: int = 4          # 持有 4 桶 ≈ 60s（15s 桶）
    hold_ms: int = 0               # 0 = 用 horizon_bars × bar_ms


def enabled() -> bool:
    """总开关（回滚开关）。默认**开启**（这是研究闭环，不下单）。"""
    return str(os.getenv(ENV_ENABLED, "1")).strip().lower() not in (
        "0", "false", "no", "off")


# ────────────────────────── 特征（因果计算）──────────────────────────

def _bar_last(values: np.ndarray, bar: np.ndarray, nb: int) -> np.ndarray:
    """按桶取最后一个值，并对**空桶做前向填充**。

    为什么必须填充：盘口快照是稀疏的（实测 SOL 在 15s 网格上平均只有 0.24 个观测/桶，
    132015/173201 个桶完全没有观测）。早先不填充时空桶的桶价是 0，于是
    "有价桶 → 空桶"被算成 −100% 的假收益，整条链的净收益虚报成 −1000bp 量级
    （实测踩过：随机信号的期望本该是 0）。
    前向填充在语义上也正确：没有新观测时，最后已知价就是当前价。
    """
    out = np.zeros(nb)
    last = values[0] if len(values) else 0.0
    pos = -1
    order = np.argsort(bar, kind="stable")
    for idx in order:
        b = int(bar[idx])
        if b < 0 or b >= nb:
            continue
        while pos < b:
            out[pos if pos >= 0 else 0] = last
            pos += 1
            if pos < nb:
                out[pos] = last
        pos = b
        out[b] = values[idx]
        last = values[idx]
    # 尾部补齐
    for k in range(pos + 1, nb):
        out[k] = last
    return out


def compute_features(ots: np.ndarray, bb: np.ndarray, ba: np.ndarray,
                     tts: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                     sv: np.ndarray, bv: np.ndarray, tmk: Optional[np.ndarray],
                     bar_ms: int = BAR_MS_DEFAULT,
                     lookback: int = LOOKBACK_DEFAULT) -> Dict[str, np.ndarray]:
    """按 `bar_ms` 分桶，计算**因果**特征（只用第 i 桶及之前的数据）。

    成交桶（`market_trades_aggregated`）是**按落库时刻分桶且空桶不落行**（F107），
    所以成交类特征必须按 `tmk`（落库时刻）对齐到盘口时刻，否则回放会用到
    "当时其实还看不到"的数据 ⇒ 系统性偏乐观。这里凡是成交来源的特征都用
    `tmk` 做可见性门限。
    """
    n = len(ots)
    if n == 0:
        return {k: np.zeros(0) for k in FEATURES}
    mid = (bb + ba) / 2.0
    spread_bp = (ba - bb) / mid * 1e4

    # 盘口采样天然落在 ~固定节奏上；用桶编号做时间索引
    bar = ((ots - ots[0]) // bar_ms).astype(np.int64)

    # 成交可见性：可见时刻 = tmk（落库时刻）；无 tmk 时退化为标签时刻
    vis = tmk if tmk is not None else tts

    feats: Dict[str, np.ndarray] = {}
    nb = int(bar[-1]) + 1 if len(bar) else 0
    idx = np.arange(n)

    # 每个盘口观测对应的"当时可见的成交桶"下标：最后一个 vis <= ots[i]
    vis_idx = np.searchsorted(vis, ots, side="right") - 1

    ofi = np.zeros(n)
    intensity = np.ones(n)
    rng_bp = np.zeros(n)
    for i in range(n):
        vi = vis_idx[i]
        if vi < 0:
            ofi[i] = 0.0
            intensity[i] = 1.0
            continue
        # 看最近 4 个可见成交桶（活跃度足够且仍是"当时可见"）
        j0 = max(0, vi - 3)
        s = float(sv[j0:vi + 1].sum())
        b = float(bv[j0:vi + 1].sum())
        tot = s + b
        ofi[i] = (b - s) / tot if tot > 0 else 0.0
        hi_ = float(hi[j0:vi + 1].max()) if vi >= j0 else 0.0
        lo_ = float(lo[j0:vi + 1].min()) if vi >= j0 else 0.0
        if hi_ > 0 and lo_ > 0 and mid[i] > 0:
            rng_bp[i] = (hi_ - lo_) / mid[i] * 1e4

    # 成交量强度：本桶可见成交量 / 近 lookback 桶均量
    vol_series = sv + bv
    if len(vol_series) >= lookback:
        csum = np.concatenate([[0.0], np.cumsum(vol_series)])
        for i in range(n):
            vi = vis_idx[i]
            if vi < lookback:
                intensity[i] = 1.0
                continue
            base = (csum[vi] - csum[vi - lookback]) / lookback
            cur = vol_series[vi]
            intensity[i] = (cur / base) if base > 0 else 1.0

    feats["ofi"] = ofi
    feats["trade_intensity"] = intensity
    feats["range_bp"] = rng_bp
    feats["spread_bp"] = spread_bp

    # OFI 平滑（因果：只用过去）
    alpha = 2.0 / (lookback / 2.0 + 1.0)
    ew = np.zeros(n)
    acc = 0.0
    for i in range(n):
        acc = alpha * ofi[i] + (1 - alpha) * acc
        ew[i] = acc
    feats["ofi_ewma"] = ew

    # 中间价收益（bp）：按桶取每桶最后一个 mid，**空桶前向填充**
    bar_last = _bar_last(mid, bar, nb)
    prev = np.concatenate([[bar_last[0]], bar_last[:-1]])
    ret_bp = np.where(prev > 0, (bar_last - prev) / np.maximum(prev, 1e-12) * 1e4, 0.0)
    feats["ret_1"] = ret_bp[bar]
    # 近 5 桶累计
    r5 = np.zeros(nb)
    for k in range(nb):
        r5[k] = ret_bp[max(0, k - 4):k + 1].sum()
    feats["ret_5"] = r5[bar]
    # 实现波动（近 lookback 桶收益标准差）——因果
    vol = np.zeros(nb)
    for k in range(nb):
        seg = ret_bp[max(0, k - lookback + 1):k + 1]
        vol[k] = float(np.std(seg, ddof=1)) if len(seg) > 1 else 0.0
    feats["vol_bp"] = vol[bar]

    feats["depth_ratio"] = np.where(spread_bp > 0, 1.0 / np.maximum(spread_bp, 1e-9), 0.0)

    # 时刻周期编码（向量化：逐行 datetime 在 20 万行上会拖慢整条链）
    secs = (ots // 1000).astype(np.int64)
    hours = ((secs % 86400) / 3600.0).astype(np.float64)
    feats["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    feats["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)

    # 距上次冲击的桶数
    bss = np.zeros(nb)
    last_shock = -1
    for k in range(nb):
        v = vol[k]
        if v > 0 and abs(ret_bp[k]) > 3.0 * v:
            last_shock = k
        bss[k] = (k - last_shock) if last_shock >= 0 else 1e6
    feats["bars_since_shock"] = bss[bar]

    # 前向收益（**只用于评估，绝不进特征**）：持有 horizon 后的中间价变化
    mid_after = mid.copy()
    feats["_mid"] = mid
    feats["_bar"] = bar.astype(np.float64)
    return feats


def forward_return_bp(feats: Dict[str, np.ndarray], hold_ms: int,
                      bar_ms: int) -> np.ndarray:
    """每桶的前向中间价变化（bp）。评估用，不进特征。

    按**桶**推进（不是按观测），且桶价经前向填充 —— 否则稀疏桶会被算成
    "价格归零"的假收益（实测把净收益虚报成 −1000bp 量级）。
    """
    mid = feats["_mid"]
    bar = feats["_bar"].astype(np.int64)
    nb = int(bar[-1]) + 1
    bar_mid = _bar_last(mid, bar, nb)
    hb = max(1, int(round(hold_ms / float(bar_ms))))
    fwd = np.zeros(nb)
    for k in range(nb):
        j = min(nb - 1, k + hb)
        fwd[k] = ((bar_mid[j] - bar_mid[k]) / bar_mid[k] * 1e4
                  if bar_mid[k] > 0 else 0.0)
    return fwd[bar]


def _rewrite_bool_ops(expr: str) -> Tuple[Optional[str], str]:
    """`and`/`or`/`not` → `&`/`|`/`~`（AST 变换，纯位运算版）。

    为什么必须改写：谓词作用在 **numpy 数组**上，而 Python 的 `and`/`or` 是短路
    求值、无法返回逐元素结果 ⇒ `x > 0 and y > 0` 在数组上必然抛
    "truth value of an array is ambiguous"。实测 LLM 极爱写 `and`，若不改写，
    这条链会因为一个语法习惯而整体失效。

    用 AST 变换而不是正则：正则搞不定嵌套与优先级，AST 变换天然正确。
    不引入 `.astype(...)`（那会撞严格白名单的属性访问禁令）——位运算作用在
    比较结果（布尔数组）上，语义本就是逻辑与/或，无需转换。
    """
    import ast as _ast

    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return None, f"语法错误: {e}"

    class _T(_ast.NodeTransformer):
        def visit_BoolOp(self, node):
            self.generic_visit(node)
            op = _ast.BitAnd() if isinstance(node.op, _ast.And) else _ast.BitOr()
            out = node.values[0]
            for v in node.values[1:]:
                out = _ast.BinOp(left=out, op=op, right=v)
            return out

        def visit_UnaryOp(self, node):
            self.generic_visit(node)
            if isinstance(node.op, _ast.Not):
                return _ast.UnaryOp(op=_ast.Invert(), operand=node.operand)
            return node

        def visit_Call(self, node):
            self.generic_visit(node)
            # `where(...)` → `np.where(...)`：平台的 _SAFE_BUILTINS 不含 where，
            # 但 `np` 在其 _SAFE_ATTR_ROOTS 里 ⇒ 改写成属性调用即可通过，
            # 无需改动共享的 code_safety（避免影响其它消费者的安全姿态）。
            if isinstance(node.func, _ast.Name) and node.func.id == "where":
                return _ast.Call(
                    func=_ast.Attribute(value=_ast.Name(id="np", ctx=_ast.Load()),
                                        attr="where", ctx=_ast.Load()),
                    args=node.args, keywords=node.keywords)
            return node

    try:
        new = _T().visit(tree)
        _ast.fix_missing_locations(new)
        return _ast.unparse(new), ""
    except Exception as e:
        return None, f"布尔改写失败: {type(e).__name__}: {e}"


# ────────────────────────── 编译（复用平台沙箱 + 本模块收紧）──────────────────────────

# 本模块自己的 AST 节点白名单（在平台四层规则之外**额外**加的一层）。
#
# 为什么需要：平台 `code_safety.ast_whitelist_check` 为了让 LLM 能写中间变量，
# 有意放行 lambda / 推导式 / 海象（`collect_local_bindings` 会把它们的绑定名
# 收进 local_names，于是"本地绑定"这条就为攻击者开了口子）。实测
# `(lambda: 1)()` **通过**了平台校验——对公式因子那是可接受的权衡，
# 对"只允许一个比较+布尔组合"的谓词完全没必要冒这个险。
#
# 谓词只需要：数字、特征名、四则运算、比较、布尔运算、以及少量算术函数调用。
# 其余一律拒。
#
# 注意 `Attribute` **不在此列**：它只允许出现在"被调用的 np.<函数>"这一种形态里，
# 由下面的 Call 分支精确把关；把 Attribute 放进节点白名单会让 `os.system(...)`
# 这类写法整条通过。`astype` 之类的真实属性访问因此被彻底挡在门外。
_ALLOWED_NODES = frozenset({
    "Expression", "Module", "FunctionDef", "arguments", "arg", "Return",
    "BinOp", "UnaryOp", "BoolOp", "Compare", "IfExp", "Call",
    "Name", "Load", "Constant", "Tuple", "List",
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
    "USub", "UAdd", "Not", "And", "Or",
    "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE",
    "BitAnd", "BitOr", "Invert", "LShift", "RShift",
})

# 允许被调用的**函数名**白名单（比平台 _SAFE_BUILTINS 更窄：去掉 list/dict/
# tuple/enumerate/zip/range 这些与数值谓词无关的构造器）。
# `where` = np.where，是表达三值信号（+1/-1/0）的**向量化**写法。
_ALLOWED_CALLS = frozenset({"min", "max", "abs", "round", "int", "float",
                            "bool", "where"})

# `np.<attr>` 允许的属性（谓词只需向量化的 where）。
_ALLOWED_NP_ATTRS = frozenset({"where"})


def _ast_strict_check(expr: str) -> Tuple[bool, str]:
    """本模块的节点白名单校验（在平台规则之前/之外独立把关）。"""
    import ast as _ast

    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return False, f"语法错误: {e}"

    # 预先挑出"被允许的 np 属性节点"：`Attribute` 整体不在节点白名单里，但
    # `np.where(...)` 这种**被调用的**形态要放行。必须先按 id 收集再豁免——
    # 否则节点白名单会在走到 Call 分支之前就把它拒掉（实测踩过：
    # 白名单里删掉 Attribute 后仍然报"禁止的语法节点: Attribute"）。
    allowed_attr_ids = set()
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                and isinstance(node.func.value, _ast.Name)
                and node.func.value.id == "np"
                and node.func.attr in _ALLOWED_NP_ATTRS):
            allowed_attr_ids.add(id(node.func))

    for node in _ast.walk(tree):
        name = type(node).__name__
        if name not in _ALLOWED_NODES and id(node) not in allowed_attr_ids:
            return False, f"禁止的语法节点: {name}"
        if isinstance(node, _ast.Call):
            fn = node.func
            if isinstance(fn, _ast.Name):
                if fn.id not in _ALLOWED_CALLS:
                    return False, f"禁止调用 {fn.id}()"
            elif isinstance(fn, _ast.Attribute):
                # 只放行 `np.<白名单函数>`。这**不是**安全边界：平台的 L1 链段
                # 黑名单已覆盖 `pd.io.common.os.system` 这类中段走私，且 `os`
                # 等根名本身就在黑名单里。本规则只是把谓词形态收窄。
                if not (isinstance(fn.value, _ast.Name) and fn.value.id == "np"
                        and fn.attr in _ALLOWED_NP_ATTRS):
                    return False, "只允许直接调用函数名或 np.<白名单函数>"
            else:
                return False, "只允许直接调用函数名或 np.<白名单函数>"
            if node.keywords:
                return False, "函数调用不接受关键字参数"
        if isinstance(node, _ast.Attribute) and id(node) not in allowed_attr_ids:
            # 独立的属性访问（非 `np.<白名单>` 调用形式）一律禁。
            return False, "谓词中禁止属性访问"
    return True, ""
    return True, ""


def compile_predicate(predicate: str) -> Tuple[Optional[Any], str]:
    """把谓词编译成可调用对象；失败返回 (None, 原因)。

    三道防线，任一不过即拒：
      1. 本模块 `_ast_strict_check`（节点白名单 + 调用白名单，堵 lambda/推导式/
         海象/属性访问/方法调用）；
      2. 平台 `forbidden_literal_scan`（字面量黑名单，廉价前置）；
      3. 平台 `ast_whitelist_check`（四层 AST，复用既有纵深）。
    谓词被包进 `def _pred(<FEATURES>): return (<expr>)`，使特征名成为**形参局部
    绑定** —— 既让合法引用通过平台的 L2/L4，又让任何外来名被拒。
    """
    expr = (predicate or "").strip()
    if not expr:
        return None, "空谓词"
    if "\n" in expr or "\r" in expr:
        return None, "谓词必须是单行表达式（不接受多行/语句）"

    from backend.services.factor_engine.code_safety import (
        ast_whitelist_check,
        forbidden_literal_scan,
    )

    # 字面量黑名单先过（在**原始**文本上查，改写后可能掩盖危险字面量的原貌）
    ok, why = forbidden_literal_scan(expr)
    if not ok:
        return None, f"字面量黑名单: {why}"

    # `and`/`or`/`not` 改写成位运算（否则在 numpy 数组上必然报真值歧义）
    rewritten, why = _rewrite_bool_ops(expr)
    if rewritten is None:
        return None, why

    ok, why = _ast_strict_check(rewritten)
    if not ok:
        return None, f"严格节点白名单: {why}"
    src = "def _pred(%s):\n    return (%s)\n" % (", ".join(FEATURES), rewritten)
    ok, why = ast_whitelist_check(src)
    if not ok:
        return None, f"AST 白名单: {why}"

    # 命名空间必须**同一个 dict 同时作 globals 与 locals**：函数体在定义时求值，
    # 其全局查找走 globals；若把 max/abs 只放进 locals，函数体里就找不到它们
    # （实测 NameError: name 'max' is not defined —— 我踩过这个坑）。
    ns: Dict[str, Any] = {
        "__builtins__": {},
        "np": np,
        # 必须是 **numpy 版本**：谓词作用在数组上，Python 内建的 max/abs 遇到
        # 数组会抛真值歧义，而 np.maximum 是逐元素的、语义正确。
        "min": np.minimum, "max": np.maximum, "abs": np.abs,
        "round": np.round, "int": int, "float": float, "bool": bool,
        # 向量化的三元选择：Python 的 `a if c else b` 在数组上会抛真值歧义
        # （`if` 需要标量条件），所以三值信号必须用 where 写。
        "where": np.where,
    }
    try:
        exec(compile(src, "<llm_predicate>", "exec"), ns, ns)
    except Exception as e:
        return None, f"编译失败: {type(e).__name__}: {e}"
    fn = ns.get("_pred")
    if not callable(fn):
        return None, "编译后未得到可调用对象"

    # 试算：用**非退化的随机数组**，否则合法谓词会被误判（实测踩过）
    try:
        rng = np.random.default_rng(0)
        probe = {k: rng.normal(0, 1, 64) for k in FEATURES}
        out = np.asarray(fn(**probe))
        if out.shape not in ((64,), ()):
            return None, f"输出形状异常: {out.shape}（应为标量或与输入同长）"
    except Exception as e:
        return None, f"试算失败: {type(e).__name__}: {e}"
    return fn, ""


def evaluate_predicate(fn, feats: Dict[str, np.ndarray]) -> np.ndarray:
    """在特征上求值，返回 float 数组（NaN/异常按 0 = 不动作处理）。"""
    args = {k: feats[k] for k in FEATURES}
    try:
        out = np.asarray(fn(**args), dtype=float)
    except Exception as e:
        logger.warning("[F305] 谓词求值失败: %s", e)
        return np.zeros(len(feats.get("ofi", [])), dtype=float)
    if out.ndim == 0:
        out = np.full(len(args["ofi"]), float(out))
    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    return out


# ────────────────────────── walk-forward 门禁 ──────────────────────────

def _tstat(xs: np.ndarray) -> float:
    if len(xs) < 2:
        return 0.0
    sd = float(np.std(xs, ddof=1))
    if sd <= 0:
        return 0.0
    return float(np.mean(xs) / (sd / np.sqrt(len(xs))))


def walk_forward_gate(signal: np.ndarray, fwd_bp: np.ndarray,
                      params: Optional[GateParams] = None,
                      *, direction: int = 1) -> Tuple[List[FoldResult], str]:
    """多折 walk-forward：每折在训练窗确认符号，在**后续测试窗**取样本外收益。

    返回 (folds, fail_reason)。fail_reason 为空表示全折通过。
    """
    p = params or GateParams()
    n = len(signal)
    if n < 100:
        return [], "样本不足（<100 观测）"

    # 触发点：signal > 0 视为"做多"，< 0 视为"做空"，= 0 不动作
    act = signal > 0
    short = signal < 0
    if not act.any() and not short.any():
        return [], "谓词从未触发（恒为 0/False）"

    folds: List[FoldResult] = []
    k = p.folds
    # 折窗：把可用的 OOS 区段均分成 k 段；每段前面的都是它的训练窗
    oos_start = int(n * 0.4)
    seg = max(1, (n - oos_start) // k)
    for f in range(k):
        a = oos_start + f * seg
        b = min(n, a + seg) if f < k - 1 else n
        if b - a < 2:
            continue
        tr_a, tr_b = 0, max(1, int(a * p.train_frac) if a > 0 else 1)
        # 训练窗：确认该谓词在训练段上的符号（做多与做空哪个为正）
        tr_long = fwd_bp[tr_a:tr_b][act[tr_a:tr_b]]
        tr_short = -fwd_bp[tr_a:tr_b][short[tr_a:tr_b]]
        tr_all = np.concatenate([tr_long, tr_short]) if (
            len(tr_long) or len(tr_short)) else np.zeros(0)
        tr_net = float(np.mean(tr_all)) if len(tr_all) else 0.0

        # 测试窗：样本外
        oos_long = fwd_bp[a:b][act[a:b]]
        oos_short = -fwd_bp[a:b][short[a:b]]
        oos = np.concatenate([oos_long, oos_short]) if (
            len(oos_long) or len(oos_short)) else np.zeros(0)
        oos_net = float(np.mean(oos)) if len(oos) else 0.0
        oos_t = _tstat(oos)
        # 方向一致性：LLM 声明的方向必须与实际主导方向一致
        sign_ok = True
        if p.require_direction_match and len(oos) >= 2:
            realized = 1 if oos_net >= 0 else -1
            sign_ok = (realized == (1 if direction >= 0 else -1))

        folds.append(FoldResult(
            fold=f, train_n=int(len(tr_all)), oos_n=int(len(oos)),
            oos_net_bp=round(oos_net, 4), oos_t=round(oos_t, 3),
            train_net_bp=round(tr_net, 4), sign_ok=bool(sign_ok),
        ))

    if not folds:
        return [], "无法构造折窗"

    reasons = []
    thin = [f for f in folds if f.oos_n < p.min_oos_events]
    if thin:
        reasons.append("OOS 事件不足(折%s)" % ",".join(str(f.fold) for f in thin))
    if p.require_all_folds_positive:
        neg = [f for f in folds if f.oos_net_bp <= 0]
        if neg:
            reasons.append("有折 OOS 非正(折%s)" % ",".join(str(f.fold) for f in neg))
    weak = [f for f in folds if f.oos_t <= p.min_fold_t]
    if weak:
        reasons.append("OOS t 不足(折%s)" % ",".join(str(f.fold) for f in weak))
    if p.require_direction_match:
        bad = [f for f in folds if not f.sign_ok]
        if bad:
            reasons.append("方向不一致(折%s)" % ",".join(str(f.fold) for f in bad))
    total = sum(f.oos_n for f in folds)
    if total < p.min_total_events:
        reasons.append("OOS 总事件不足(%d<%d)" % (total, p.min_total_events))
    return folds, "；".join(reasons)


def gate(hyp: Hypothesis, feats: Dict[str, np.ndarray],
         params: Optional[GateParams] = None) -> GateVerdict:
    """完整闭环的判定端：编译 → 求值 → walk-forward → 保留/丢弃。"""
    p = params or GateParams()
    v = GateVerdict(hypothesis_id=hyp.hypothesis_id, symbol=hyp.symbol,
                    kept=False, reason="", statement=hyp.statement,
                    predicate=hyp.predicate, direction=int(hyp.direction))
    if not enabled():
        v.reason = "disabled_by_env(%s=0)" % ENV_ENABLED
        return v

    fn, why = compile_predicate(hyp.predicate)
    v.compile_ok = fn is not None
    v.compile_reason = why
    if fn is None:
        v.reason = f"compile_failed: {why}"
        return v

    sig = evaluate_predicate(fn, feats)
    hold = p.hold_ms or (p.horizon_bars * p.bar_ms)
    fwd = forward_return_bp(feats, hold, p.bar_ms)
    folds, fail = walk_forward_gate(sig, fwd, p, direction=int(hyp.direction))
    v.folds = folds
    v.oos_total_n = sum(f.oos_n for f in folds)
    if folds:
        allx = [f.oos_net_bp for f in folds]
        v.oos_total_net_bp = round(float(np.mean(allx)), 4)
        v.oos_total_t = round(_tstat(np.array(allx, dtype=float)), 3)
    if fail:
        v.kept = False
        v.reason = fail
        return v
    v.kept = True
    v.reason = "kept"
    return v


# ────────────────────────── LLM 接入（如实降级）──────────────────────────

def build_prompt(symbol: str, market_summary: str = "",
                 prior_rejections: Optional[List[str]] = None) -> str:
    """构造提案 prompt。特征词汇表与输出契约都写死在 prompt 里。

    注意：**不要**用 `%` 或 `.format()` 拼这段文本——里面的 JSON 契约有大括号，
    会被当成占位符（实测直接 ValueError）。用拼接最省事也最不易错。
    """
    parts = [
        "你是加密货币微观结构研究员。为 " + str(symbol) + " 的永续合约提出 **1 条可验证假设**。",
        "",
        "可用特征（只能用这些名字，全部已按 15 秒桶因果计算）:",
        "  ofi ∈[-1,1] 订单流失衡(买-卖)/总; ofi_ewma 其平滑值;",
        "  trade_intensity 本桶量/近20桶均量; ret_1 上一桶收益(bp); ret_5 近5桶累计(bp);",
        "  vol_bp 近20桶收益标准差(bp); spread_bp 相对价差(bp); depth_ratio = 1/spread_bp;",
        "  range_bp 本桶(high-low)/mid(bp); hour_sin/hour_cos 时刻周期;",
        "  bars_since_shock 距上次冲击的桶数。",
        "",
        "要求:",
        "  1) 谓词是**单个 Python 表达式**，只能用上面的特征名、数字、四则运算、",
        "     比较、and/or/not、以及 min/max/abs/round/where；",
        "     禁止属性访问、import、下标、lambda、推导式；",
        "  2) 输出 >0 表示做多，<0 表示做空，0 表示不动作。",
        "     三值信号请用 where(条件, 1, where(另一条件, -1, 0)) 写，",
        "     不要用 `1 if 条件 else 0`（那在向量上不合法）；",
        "  3) 必须有经济逻辑（≤60字），不要只堆条件；",
        "  4) expected_edge_bp 是预期每笔净收益（bp），要现实（不要写 50）。",
    ]
    if market_summary:
        parts.append("市场背景: " + str(market_summary))
    if prior_rejections:
        parts.append("已被否决的假设（避免重复）: " + "; ".join(prior_rejections[:8]))
    parts.append("")
    parts.append('只输出 JSON，形如: {"statement":"...","predicate":"...",'
                 '"direction":1,"expected_edge_bp":3.0}')
    return "\n".join(parts)


def parse_hypotheses(raw: str, symbol: str) -> List[Hypothesis]:
    """解析 LLM 输出（容忍 ```json 围栏与前后缀文本）。"""
    if not raw:
        return []
    text = str(raw).strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines()
                         if not l.strip().startswith("```")).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}")
        if 0 <= s < e:
            try:
                data = json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("hypotheses") or [data]
    else:
        return []
    out = []
    for i, d in enumerate(items):
        if not isinstance(d, dict):
            continue
        pred = str(d.get("predicate") or "").strip()
        if not pred:
            continue
        try:
            direction = 1 if float(d.get("direction", 1)) >= 0 else -1
        except Exception:
            direction = 1
        try:
            edge = float(d.get("expected_edge_bp") or 0.0)
        except Exception:
            edge = 0.0
        out.append(Hypothesis(
            hypothesis_id="%s_llm_%d" % (symbol.lower(), i),
            symbol=symbol,
            statement=str(d.get("statement") or "")[:300],
            predicate=pred,
            direction=direction,
            expected_edge_bp=edge,
        ))
    return out


def propose(symbol: str, market_summary: str = "",
            prior_rejections: Optional[List[str]] = None,
            usage: str = "factor_mining") -> Dict[str, Any]:
    """调 LLM 出假设。无配置 ⇒ 如实返回 llm_unavailable（**绝不造假候选**）。"""
    if not enabled():
        return {"ok": False, "error": "disabled_by_env", "hypotheses": []}
    try:
        from backend.services.llm_config_service import (
            call_llm_api_sync,
            get_llm_config_for_usage,
        )
    except Exception as e:
        return {"ok": False, "error": "llm_service_unavailable: %s" % e,
                "hypotheses": []}

    config = None
    for kwargs in ({"tenant_id": None}, {}):
        try:
            config = get_llm_config_for_usage(usage, **kwargs)
        except TypeError:
            continue
        except Exception:
            config = None
        if config is not None:
            break
    if config is None or not getattr(config, "api_key", None):
        return {"ok": False, "error": "llm_config_unavailable",
                "hypotheses": [],
                "hint": "需要为用途 %r 配置 LLM key" % usage}

    prompt = build_prompt(symbol, market_summary, prior_rejections)
    try:
        resp = call_llm_api_sync(
            config,
            messages=[
                {"role": "system", "content":
                 "你是量化微观结构研究员，只输出 JSON。谓词里禁止 import、"
                 "属性访问、文件/网络操作，只能用给定特征名与算术/布尔运算。"},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=1200, temperature=0.6, caller="f305_hypothesis",
        )
    except Exception as e:
        return {"ok": False, "error": "llm_error: %s" % e, "hypotheses": []}

    content = ""
    if resp:
        ch = resp.get("choices") or []
        if ch:
            content = (ch[0].get("message") or {}).get("content") or ""
    hyps = parse_hypotheses(content, symbol)
    return {"ok": bool(hyps), "hypotheses": hyps,
            "raw_len": len(content),
            "error": "" if hyps else "no_parseable_hypothesis"}


# ────────────────────────── 取数（与回放同一张表同一口径）──────────────────────────

def load_tape(symbol: str, venue: str = "asterdex",
              start_ts: Optional[int] = None,
              max_span_hours: float = 48.0) -> Optional[Dict[str, np.ndarray]]:
    """读盘口快照 + 区间成交，口径与 `market_maker.replay._load_series` 一致。

    成交侧带 `created_at`（落库时刻）并交给 `compute_features` 做可见性过滤：
    asterdex 的成交桶是**按落库时刻分桶、空桶不落行**，不看 `tmk` 就会用到
    "当时其实还看不到"的数据（F107 已记录该偏差是系统性偏乐观）。

    `max_span_hours`：只保留**最近**这段窗口。为什么需要：盘口表里混着很久以前的
    散点（实测 SOL 跨度 721 小时、最大观测间隔 19 亿毫秒 = 22 天），那些孤点会把
    桶编号撑到 17 万并把整条统计稀释成噪声；而本研究只关心当前微观结构。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    with system_identity():
        with MarketSessionLocal() as db:
            ob = db.execute(text(
                "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                " ORDER BY timestamp ASC"
            ), {"e": venue, "s": symbol}).mappings().all()
            tr = db.execute(text(
                "SELECT timestamp, low_price, high_price, taker_sell_volume,"
                " taker_buy_volume, created_at"
                " FROM market_trades_aggregated WHERE exchange=:e AND symbol=:s"
                " ORDER BY timestamp ASC"
            ), {"e": venue, "s": symbol}).mappings().all()

    if len(ob) < 200 or len(tr) < 20:
        return None
    ots = np.array([int(r["timestamp"]) for r in ob], dtype=np.int64)
    bb = np.array([float(r["best_bid"]) for r in ob])
    ba = np.array([float(r["best_ask"]) for r in ob])

    # 只保留最近 max_span_hours：从**最后一个观测**往前推
    if max_span_hours and max_span_hours > 0:
        cut = int(ots[-1] - max_span_hours * 3_600_000)
        m = ots >= cut
        if int(m.sum()) >= 200:
            ots, bb, ba = ots[m], bb[m], ba[m]

    t0_ms = int(ots[0])
    tr = [r for r in tr if int(r["timestamp"]) >= t0_ms - 60_000]
    if len(tr) < 20:
        return None
    tts = np.array([int(r["timestamp"]) for r in tr], dtype=np.int64)
    lo = np.array([float(r["low_price"] or 0) for r in tr])
    hi = np.array([float(r["high_price"] or 0) for r in tr])
    sv = np.array([float(r["taker_sell_volume"] or 0) for r in tr])
    bv = np.array([float(r["taker_buy_volume"] or 0) for r in tr])

    # created_at 是 naive 本地墙钟（见 replay.py F107 的详细说明）：直接
    # .timestamp() 会被当 UTC ⇒ 整体偏 8 小时，必须做偏移修正。
    _off_ms = int((datetime.now().astimezone().utcoffset()
                   or timedelta(0)).total_seconds() * 1000)
    tmk = []
    for r in tr:
        lab = int(r["timestamp"])
        ca = r["created_at"]
        if ca is None:
            tmk.append(lab + 15000)
            continue
        try:
            v = int(ca.timestamp() * 1000)
        except Exception:
            tmk.append(lab + 15000)
            continue
        if v - lab > 3_600_000:
            v -= _off_ms
        tmk.append(v)
    tmk = np.array(tmk, dtype=np.int64)

    if start_ts:
        m = ots >= int(start_ts)
        ots, bb, ba = ots[m], bb[m], ba[m]
    return {"ots": ots, "bb": bb, "ba": ba, "tts": tts, "lo": lo, "hi": hi,
            "sv": sv, "bv": bv, "tmk": tmk}


def features_for(symbol: str, venue: str = "asterdex",
                 params: Optional[GateParams] = None,
                 start_ts: Optional[int] = None) -> Optional[Dict[str, np.ndarray]]:
    """取数 + 算特征。失败返回 None。"""
    p = params or GateParams()
    tape = load_tape(symbol, venue, start_ts)
    if not tape:
        return None
    return compute_features(tape["ots"], tape["bb"], tape["ba"], tape["tts"],
                            tape["lo"], tape["hi"], tape["sv"], tape["bv"],
                            tape["tmk"], bar_ms=p.bar_ms, lookback=p.lookback)


# ────────────────────────── 端到端闭环 ──────────────────────────

def make_hypothesis(symbol: str, predicate: str, direction: int = 1,
                    statement: str = "", source: str = "manual") -> Hypothesis:
    """手工/测试构造假设（走进与 LLM 假设完全相同的门禁路径）。"""
    return Hypothesis(
        hypothesis_id="%s_%s" % (symbol.lower(), source),
        symbol=symbol, statement=statement, predicate=predicate,
        direction=1 if int(direction) >= 0 else -1, source=source,
    )


def run_closed_loop(symbol: str, *, venue: str = "asterdex",
                    params: Optional[GateParams] = None,
                    hyps: Optional[List[Hypothesis]] = None,
                    market_summary: str = "",
                    prior_rejections: Optional[List[str]] = None
                    ) -> Dict[str, Any]:
    """端到端：取数 → 特征 → （若无 hyps 则调 LLM 出假设）→ 编译 → walk-forward → 保留/丢弃。

    返回结构可直接写给 `lane_registry.edge_json`（对被保留的假设）。
    """
    p = params or GateParams()
    if not enabled():
        return {"ok": False, "error": "disabled_by_env(%s)" % ENV_ENABLED}

    feats = features_for(symbol, venue, p)
    if feats is None:
        return {"ok": False, "error": "no_tape", "symbol": symbol,
                "hint": ("market_orderbook_snapshots / market_trades_aggregated"
                         " 缺数据")}

    llm_info: Dict[str, Any] = {}
    if hyps is None:
        res = propose(symbol, market_summary, prior_rejections)
        llm_info = {"ok": res.get("ok"), "error": res.get("error") or ""}
        hyps = res.get("hypotheses") or []
        if not hyps:
            return {"ok": False, "error": res.get("error") or "no_hypotheses",
                    "symbol": symbol, "llm": llm_info,
                    "hint": "LLM 不可用或未产出可解析假设（不造假候选）"}

    n_obs = int(len(feats.get("ofi", [])))
    verdicts = [gate(h, feats, p) for h in hyps]
    kept = [v for v in verdicts if v.kept]
    return {
        "ok": True,
        "symbol": symbol,
        "venue": venue,
        "observations": n_obs,
        "bars": int(feats["_bar"][-1]) + 1 if n_obs else 0,
        "hypotheses": [v.to_dict() for v in verdicts],
        "kept": [v.to_dict() for v in kept],
        "kept_n": len(kept),
        "dropped_n": len(verdicts) - len(kept),
        "llm": llm_info,
        "params": asdict(p),
        # 被保留的假设折算成 edge 口径（供晋升判定/落库）
        "edge": kept[0].edge_json() if kept else None,
    }


if __name__ == "__main__":      # pragma: no cover - 手工诊断
    import sys
    print(json.dumps({"enabled": enabled(), "features": list(FEATURES)},
                     ensure_ascii=False, indent=2))
    if len(sys.argv) > 1:
        fn, why = compile_predicate(sys.argv[1])
        print("compile:", "ok" if fn else why)
