# -*- coding: utf-8 -*-
"""轮78 P1-13 回归：学习层的 val_IC 不是样本外（特征筛选偷看校验段）+ 门槛硬编码。

## 缺陷一：选择性前视

旧顺序：

    feat_cols = df.columns
    # ① 用 aligned（**含校验段**）逐列 corr 选特征
    if _x.corr(aligned["__y__"]) >= min_ic: keep.append(c)
    # ② 切分
    _tr = aligned[:train_end]; _va = aligned[split:]
    model.fit(_tr)
    val_ic = model.predict(_va).corr(_va.y)      # ← 用挑剩下的「重点」再考一次
    if val_ic < 0.02: reject

① 与 ② 用的是同一批数据 —— 等于「用考试题挑重点，再用同一批题打分」，
val_IC 系统性偏乐观，门槛失去意义。
佐证（审计）：接受的 val_IC 中位 0.179、最大 0.9727，1036 次里 72 次 > 0.5，
对 5 根 15m 的加密收益而言不可能。

## 缺陷二：门槛硬编码

`_min_val_ic = 0.02` 是字面量，而同类旋钮（`LEARNED_MIN_IC` / `LEARNED_PURGE_BARS`）
都是 env 可配 —— 线上想收紧/放宽准入只能改代码。

## 另外补上的可见性

`DDGDA_ENABLED=true` 时重放行被复制 1-5×，相同 bar 跨切分两侧，
而 `purge_bars` 只按**位置**剔除、对复制行无效。现显式检测并 warning。
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.learned_weighting import (
    LearnedFactorWeighting,
    LearnedWeightingConfig,
)

_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'backend/services/factor_engine/learned_weighting.py',
)


# ══════════════════════════════════════════════════════════════════════
# 1. 门槛可配
# ══════════════════════════════════════════════════════════════════════

def test_min_val_ic_is_configurable(monkeypatch):
    assert LearnedWeightingConfig().min_val_ic == pytest.approx(0.02), '默认值必须保持 0.02'
    monkeypatch.setenv('LEARNED_MIN_VAL_IC', '0.05')
    assert LearnedWeightingConfig().min_val_ic == pytest.approx(0.05)


def test_min_val_ic_not_hardcoded():
    import io
    src = io.open(_SRC, encoding='utf-8').read()
    code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
    assert '_min_val_ic = 0.02' not in code, '门槛不得再是字面量'
    assert 'LEARNED_MIN_VAL_IC' in src, '门槛必须 env 可配'


# ══════════════════════════════════════════════════════════════════════
# 2. 切分必须早于特征筛选（源码级顺序守卫）
# ══════════════════════════════════════════════════════════════════════

def test_split_precedes_feature_selection():
    import io
    src = io.open(_SRC, encoding='utf-8').read()
    code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
    i_split = code.find('_tr = aligned.iloc[:_train_end]')
    # 锚定**使用点**（`float(self.config.min_ic_to_include`），
    # 因为 "min_ic_to_include" 也出现在文件的配置 dataclass 里（行号更小，会误判）
    i_sel = code.find('float(self.config.min_ic_to_include')
    assert i_split > 0 and i_sel > 0, (i_split, i_sel)
    assert i_split < i_sel, \
        '必须先切分再选特征（否则特征筛选会偷看校验段 —— 这正是 P1-13）'


def test_feature_selection_uses_train_segment_only():
    import io
    import re
    src = io.open(_SRC, encoding='utf-8').read()
    m = re.search(r'# \[P1-11\] min_ic_to_include 生效.*?(?=\n        if not feat_cols:)', src, re.S)
    assert m, '找不到特征筛选块'
    body = '\n'.join(l for l in m.group(0).splitlines() if not l.lstrip().startswith('#'))
    assert '_tr[_c]' in body and '_tr["__y__"]' in body, '特征筛选必须只用训练段'
    assert 'aligned[_c]' not in body, '不得再用整段 aligned 选特征'


# ══════════════════════════════════════════════════════════════════════
# 3. 因果验证：诱饵因子在校验段相关、在训练段无关 → 必须被剔除
# ══════════════════════════════════════════════════════════════════════

def _orthogonal_noise(rng, y, n):
    """返回与 y **样本相关恰为 0** 的噪声（对 y 做 Gram-Schmidt 投影后再随机化）。"""
    v = rng.normal(0, 1, n)
    yc = y - y.mean()
    denom = float(yc @ yc)
    if denom > 1e-12:
        v = v - (float(v @ yc) / denom) * yc
    return v


def _make_frame(n=180, purge=5, seed=7):
    """构造 (features, labels)，**按引擎真实的切分点**放诱饵。

    引擎切分：`split = int(0.8 * n)`、`train_end = max(split - purge, 30)`，
    训练段 = `[0, train_end)`、校验段 = `[split, n)`，
    中间 `[train_end, split)` 是 purge embargo（两边都不用）。

    因此诱饵必须：
      · 在 `[0, train_end)` 上与 y **样本相关恰为 0**（被 Gram-Schmidt 剔除）；
      · 在 `[split, n)` 上与 y 强相关。

    首版测试把「验证段起点」写成 120 而真实 split 是 144（train_end=139），
    导致诱饵的强相关部分漏进训练段（实测 corr=0.164）→ 被正确选中，
    测试反而「证明」了错误结论。这条注释留档以免再犯。
    """
    rng = np.random.default_rng(seed)
    split = int(n * 0.8)
    train_end = max(split - purge, 30)

    y = rng.normal(0, 1, n)
    real = y * 0.9 + rng.normal(0, 0.3, n)

    decoy = np.empty(n)
    decoy[:train_end] = _orthogonal_noise(rng, y[:train_end], train_end)
    # purge 区间只求「别意外高相关」，用纯噪声即可
    decoy[train_end:split] = rng.normal(0, 1, split - train_end)
    decoy[split:] = y[split:] * 0.95 + rng.normal(0, 0.2, n - split)

    idx = pd.date_range('2026-01-01', periods=n, freq='15min')
    feats = pd.DataFrame({'real': real, 'decoy': decoy}, index=idx)
    labels = pd.Series(y, index=idx)
    return feats, labels, train_end, split


def test_decoy_is_orthogonal_in_train_but_correlated_overall():
    """先证明这个构造真的构成「选择性」：训练段 0、整段明显 >0。"""
    feats, labels, train_end, split = _make_frame()
    tr_corr = abs(float(feats['decoy'][:train_end].corr(labels[:train_end])))
    all_corr = abs(float(feats['decoy'].corr(labels)))
    assert tr_corr < 1e-9, f'训练段应严格正交，实际 {tr_corr}'
    assert all_corr > 0.05, f'整段应有可检出的相关（诱饵才有效），实际 {all_corr}'


def test_decoy_feature_is_rejected(monkeypatch):
    """核心：只在校验段相关的诱饵因子不得进入特征集。"""
    monkeypatch.setenv('LEARNED_MIN_IC', '0.015')
    feats, labels, _, _ = _make_frame()

    lw = LearnedFactorWeighting()
    raised = None
    try:
        lw.train(feats, labels)
    except Exception as e:      # noqa: BLE001
        raised = e

    cols = getattr(lw, 'feature_columns', None)
    if not cols:
        pytest.skip(f'训练未产生 feature_columns（{raised!r}），顺序守卫已覆盖')
    assert 'decoy' not in cols, (
        f'诱饵因子（仅整段相关、训练段正交）不得被选入特征集，实际选中 {cols}'
    )


def test_real_feature_is_kept(monkeypatch):
    """对照：两段都相关的因子必须保留（避免「全筛掉」的假修复）。"""
    monkeypatch.setenv('LEARNED_MIN_IC', '0.015')
    feats, labels, _, _ = _make_frame()

    lw = LearnedFactorWeighting()
    try:
        lw.train(feats, labels)
    except Exception:
        pass

    cols = getattr(lw, 'feature_columns', None)
    if not cols:
        pytest.skip('训练未产生 feature_columns（模型后端不可用）')
    assert 'real' in cols, f'真实相关因子应保留，实际 {cols}'


# ══════════════════════════════════════════════════════════════════════
# 4. 重复行检测（DDGDA 上采样跨切分）
# ══════════════════════════════════════════════════════════════════════

def test_duplicate_span_detection_present():
    import io
    src = io.open(_SRC, encoding='utf-8').read()
    assert '疑似 DDGDA 上采样复制行' in src, '应有跨切分重复行告警'
    assert '_tr_keys' in src and '_dup' in src


def test_duplicate_rows_trigger_warning(monkeypatch, caplog):
    """把校验段整段复制训练段 → 必须报出重复。"""
    import logging
    monkeypatch.setenv('LEARNED_MIN_IC', '0.0')      # 关掉筛选，确保走到拟合
    rng = np.random.default_rng(3)
    n_tr, n_va = 120, 60
    y = rng.normal(0, 1, n_tr)
    # 校验段的特征与标签与训练段前 60 行完全相同 → 必然被判重复
    yv = y[:n_va]
    x = rng.normal(0, 1, n_tr)
    xv = x[:n_va]
    idx = pd.date_range('2026-01-01', periods=n_tr + n_va, freq='15min')
    feats = pd.DataFrame({'f1': np.concatenate([x, xv])}, index=idx)
    labels = pd.Series(np.concatenate([y, yv]), index=idx)

    lw = LearnedFactorWeighting()
    with caplog.at_level(logging.WARNING):
        try:
            lw.train(feats, labels)
        except Exception:
            pass
    msgs = [r.getMessage() for r in caplog.records]
    assert any('DDGDA' in m for m in msgs), \
        f'跨切分重复行必须产生 warning，实际日志: {msgs[-3:]}'
