# -*- coding: utf-8 -*-
"""[h893c 2026-10-07] M5 三层互联 · 模拟仓直连桥(**纯标准库,零依赖**)。

为什么单独一个文件:模拟仓高频 worker 跑在 `.runtime\\Python312` 解释器里,
**没有 numpy** —— 而 unified_learner.py 模块级 import numpy,worker 一 import
就 ModuleNotFoundError,被桥的 except 吞掉 ⇒ 阈值永远静默回退基线(桥死在现场)。
所以 worker 直连的函数必须住在**无第三方依赖**的模块里。

口径唯一来源:runner.py(模拟仓 worker 每拍)与 h892_train_layers.py(每 2h
训练驱动)都从这里 import —— 改一处,两处同改,不会漂移。
"""
from __future__ import annotations

from typing import Optional


def m5_veto_bp(long_regime_bp: Optional[float], mid_prior_bp: Optional[float],
               base_bp: float = 4.0, long_dir_bp: float = 20.0,
               mid_flat_bp: float = 10.0) -> float:
    """从 regime 读数算高频 10 分钟趋势否决阈值(bp)。

    · 长线有方向(|long_regime_bp| ≥ long_dir_bp,即 12h 趋势 ±20bp)⇒ 逆侧更严(×0.7)。
      双向对称:偏空严做多、偏多严做空 —— 否决本身由「逆 10min 趋势」触发,
      方向已在那里自适配,这里只需判断"有没有大方向";
    · 中线无方向(|mid_prior_bp| < mid_flat_bp,即 75min 动量 <10bp)⇒ 放宽(×1.3);
    · 输出天然夹在 [1, 8]bp;读数缺失 ⇒ 基线 base_bp(fail-closed 回现状)。

    替换旧桥(h892)的错误口径:此前拿 long/mid 层的 train_ep_mean 当方向读数
    —— 那是「训练 reward 均值 = 策略赚不赚钱」,不是市场方向,语义全错。
    """
    v = float(base_bp)
    if long_regime_bp is not None and abs(float(long_regime_bp)) >= float(long_dir_bp):
        v = max(1.0, v * 0.7)
    if mid_prior_bp is not None and abs(float(mid_prior_bp)) < float(mid_flat_bp):
        v = min(8.0, v * 1.3)
    return v
