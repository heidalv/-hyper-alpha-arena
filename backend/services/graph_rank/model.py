# -*- coding: utf-8 -*-
"""graph_rank —— RTGNN 迁移 P1/P4：RTGNN-lite 模型（纯 torch，无 torch_geometric）。

管线（对应论文四模块的加密版）：
1. 时序编码：多头因果时序注意力 + GRU 压缩 → 币嵌入 z ∈ R^{N×d}
2. 自适应非对称图：S(嵌入余弦相似) + β·(Δm_i − Δm_j)（动量加速度差打破对称性）
   + 可选多关系（领先-滞后关系 / 板块关系，MDGNN 式关系注意力）
   + top-k 稀疏化 + EMA 平滑
3. 图注意力聚合：掩码多头 GAT，边权作注意力偏置（GATv2 风格）
4. 市场门控（可选，MASTER 式）：BTC 行情状态 gate 调制币嵌入
5. 预测头：MLP → 截面分数；损失 = Huber 回归 + λ·成对排序损失（margin）

输入约定：forward(x, mask, a_lead=None, a_sector=None)，
x: (N, L, F) 已标准化的回看窗口（最新 bar 在最后一维）；mask: (N, L) 或 None。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.services.graph_rank.dataset import _RET_IDX


@dataclass
class RTGNNConfig:
    d_model: int = 32
    n_heads: int = 4          # 时序注意力头数
    gru_hidden: int = 32
    gat_heads: int = 4        # 图注意力头数
    n_gat_layers: int = 1
    dropout: float = 0.1
    lambda_rank: float = 0.5  # 排序损失权重（STHAN-SR 经验区间 0.1~1.0）
    rank_margin: float = 0.05
    # 图生成
    graph_enabled: bool = True
    momentum_horizons: Tuple[int, ...] = (3, 6, 12)   # Δm 用的多周期特征下标（ret 列）
    edge_beta: float = 1.0    # 动量差打破对称性的强度
    edge_threshold: float = 0.0
    top_k: int = 6            # 每节点最大出边数
    ema_alpha: float = 0.3    # 图 EMA 平滑
    edge_gamma: float = 0.1   # 边权注意力偏置系数
    # P4
    relations_enabled: bool = False   # 多关系图（lead-lag + 板块）
    market_gate: bool = False         # BTC 市场门控
    use_huber: bool = True


def _build_relations(
    n_assets: int,
    a_raw: torch.Tensor,
    a_lead: Optional[torch.Tensor],
    a_sector: Optional[torch.Tensor],
    rel_w: nn.Parameter,
) -> torch.Tensor:
    """关系注意力组合：A = Σ_r softmax(w)_r · A_r；A_raw 恒为关系 0。"""
    mats = [a_raw]
    if a_lead is not None:
        mats.append(a_lead.to(a_raw.dtype).to(a_raw.device))
    if a_sector is not None:
        mats.append(a_sector.to(a_raw.dtype).to(a_raw.device))
    w = F.softmax(rel_w[: len(mats)], dim=0)
    out = torch.zeros_like(a_raw)
    for wk, m in zip(w, mats):
        out = out + wk * m
    return out


class RTGNNModel(nn.Module):
    def __init__(self, n_features: int, n_assets: int, config: Optional[RTGNNConfig] = None):
        super().__init__()
        self.config = config or RTGNNConfig()
        c = self.config
        self.n_features = n_features
        self.n_assets = n_assets
        self.btci: Optional[int] = None  # 由 service 注入（BTC 在截面中的下标）

        # 1) 时序编码
        self.proj = nn.Linear(n_features, c.d_model)
        self.t_attn = nn.MultiheadAttention(c.d_model, c.n_heads, dropout=c.dropout, batch_first=True)
        self.gru = nn.GRU(c.d_model, c.gru_hidden, num_layers=1, batch_first=True)
        d = c.gru_hidden
        self.ln = nn.LayerNorm(d)

        # 2) 图生成（动量差参数、EMA 状态）
        self.mom_weights = nn.Parameter(torch.ones(len(c.momentum_horizons)) / len(c.momentum_horizons))
        self.register_buffer("ema_A", torch.zeros(n_assets, n_assets), persistent=False)

        # 3) GAT（多头 + 边权偏置；mask 掉零边）
        self.gat = nn.Linear(d, c.d_model)
        self.attn_a = nn.Parameter(torch.empty(c.gat_heads, 2 * c.d_model))
        nn.init.xavier_uniform_(self.attn_a)
        self.gat_out = nn.Linear(c.gat_heads * c.d_model, d)

        # P4 关系权重（relations_enabled 时才用）
        self.rel_w = nn.Parameter(torch.zeros(3))  # raw / lead / sector

        # 4) 市场门控（P4）
        if c.market_gate:
            self.gate = nn.Linear(n_features + 6, d)  # BTC 特征 + regime one-hot(6)

        # 5) 预测头
        self.head = nn.Sequential(
            nn.Linear(d, d),
            nn.ReLU(),
            nn.Dropout(c.dropout),
            nn.Linear(d, 1),
        )

    # ── 图生成 ──────────────────────────────────────────────
    def _graph(self, z: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        """z: (N,d) 币嵌入；x: (N,L,F) 回看窗口（最新 bar 在 x[:,-1]）。

        Δm = Σ_h w_h·(ret_h(t) − ret_h(t−1))，用特征列的 ret3/ret6/ret12 差近似动量加速度。
        """
        c = self.config
        N = z.shape[0]
        S = F.normalize(z, dim=1) @ F.normalize(z, dim=1).T  # (N,N) 余弦相似
        dm = torch.zeros(N, device=z.device)
        for k, h in enumerate(c.momentum_horizons):
            feat_idx = _RET_IDX.get(f"ret{h}", 0)
            if feat_idx >= x.shape[2]:
                continue
            now = x[:, -1, feat_idx]
            prev = x[:, -2, feat_idx] if x.shape[1] > 1 else now
            dm = dm + self.mom_weights[k] * (now - prev)
        A = S + c.edge_beta * (dm[:, None] - dm[None, :])  # 打破对称性
        return A

    @staticmethod
    def _sparsify(A: torch.Tensor, top_k: int, threshold: float) -> torch.Tensor:
        """每行保留 top-k 最大出边 + 阈值，其余置 0；对角线自环保留。"""
        N = A.shape[0]
        k = max(1, min(top_k, N))
        diag = torch.diag_embed(torch.ones(N, device=A.device))
        B = A * (1.0 - torch.eye(N, device=A.device))
        topk, _ = torch.topk(B, k, dim=1)
        row_min = topk[:, -1:]  # 第 k 大
        keep = (B >= row_min) & (B > threshold)
        return torch.where(keep, A, torch.zeros_like(A)) + diag

    # ── 前向 ────────────────────────────────────────────────
    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
        a_lead: Optional[torch.Tensor] = None,
        a_sector: Optional[torch.Tensor] = None,
        regime: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, Dict[str, Any]]:
        """x: (N,L,F) → (scores (N,), aux{adjacency})。"""
        c = self.config
        N, L, Fd = x.shape
        device = x.device
        if mask is not None:
            x = torch.where(mask.unsqueeze(-1), x, torch.zeros_like(x))
        # 1) 时序编码
        h = self.proj(x)  # (N,L,d)
        causal = torch.triu(torch.ones(L, L, device=device, dtype=torch.bool), diagonal=1)
        h_attn, _ = self.t_attn(h, h, h, attn_mask=causal, need_weights=False)
        out, _ = self.gru(h_attn)
        z = self.ln(out[:, -1, :])  # (N,d)

        # 2) 图：原始图 + EMA 平滑（状态缓冲；N 变化时跳过平滑）
        if c.graph_enabled:
            A_raw = self._graph(z, x)
            if c.ema_alpha > 0 and A_raw.shape == self.ema_A.shape:
                A = (1.0 - c.ema_alpha) * self.ema_A + c.ema_alpha * A_raw
                self.ema_A = A.detach()
            else:
                A = A_raw
            A = self._sparsify(A, c.top_k, c.edge_threshold)
        else:
            A = torch.eye(N, device=device)
        if c.relations_enabled and (a_lead is not None or a_sector is not None):
            A = _build_relations(N, A, a_lead, a_sector, self.rel_w)

        # 3) GAT：每头独立注意力向量 a_m^T[Wh_i‖Wh_j] + 边权偏置，零边置 -inf
        Wh = self.gat(z)  # (N, d_model)
        neg = torch.finfo(Wh.dtype).min
        a_lhs = self.attn_a[:, : c.d_model]  # (H, d)
        a_rhs = self.attn_a[:, c.d_model:]  # (H, d)
        lhs = Wh @ a_lhs.T  # (N, H)
        rhs = Wh @ a_rhs.T  # (N, H)
        logits = F.leaky_relu(lhs[:, None, :] + rhs[None, :, :] + c.edge_gamma * A.unsqueeze(-1), 0.2)  # (N,N,H)
        logits = torch.where((A > 0).unsqueeze(-1), logits, torch.full_like(logits, neg))
        alpha = F.softmax(logits, dim=1)  # (N,N,H)
        z_agg = torch.zeros(N, c.gat_heads * c.d_model, device=device)
        for m in range(c.gat_heads):
            z_agg[:, m * c.d_model:(m + 1) * c.d_model] = alpha[:, :, m] @ Wh
        z2 = self.gat_out(z_agg) + z  # 残差

        # 4) 市场门控（P4，MASTER 式）
        if c.market_gate and self.btci is not None and 0 <= self.btci < N:
            mkt = x[self.btci, -1, :]  # BTC 最新 bar 特征
            reg = regime.to(device) if regime is not None else torch.zeros(6, device=device)
            g = torch.sigmoid(self.gate(torch.cat([mkt, reg], dim=0)))  # (d,)
            z2 = z2 * g.unsqueeze(0) + z2

        # 5) 预测头
        scores = self.head(z2).squeeze(-1)  # (N,)
        return scores, {"adjacency": A.detach(), "embedding": z2.detach()}


# ─────────────────────────────────────────────────────────────
# 损失与评估
# ─────────────────────────────────────────────────────────────
def rtgnn_loss(
    scores: torch.Tensor,
    labels: torch.Tensor,
    valid: torch.Tensor,
    lambda_rank: float,
    margin: float = 0.05,
    use_huber: bool = True,
) -> torch.Tensor:
    """Huber 回归 + λ·成对排序（margin ranking）。valid 为布尔掩码。"""
    if not bool(valid.any()):
        return torch.zeros((), device=scores.device)
    s = scores[valid]
    y = labels[valid]
    reg = F.smooth_l1_loss(s, y) if use_huber else F.mse_loss(s, y)
    if lambda_rank <= 0 or len(s) < 2:
        return reg
    diff = s[:, None] - s[None, :]
    ydiff = y[:, None] - y[None, :]
    pair = (ydiff.abs() > 1e-6).float()
    rank = (F.relu(margin - torch.sign(ydiff) * diff) * pair).sum() / max(1.0, float(pair.sum()))
    return reg + lambda_rank * rank


def _rank(x: torch.Tensor) -> torch.Tensor:
    """平均排名（并列取均值）。"""
    order = torch.argsort(x)
    ranks = torch.empty_like(order, dtype=torch.float32)
    ranks[order] = torch.arange(len(x), device=x.device, dtype=torch.float32)
    return ranks


def spearman(a: torch.Tensor, b: torch.Tensor) -> float:
    """纯 torch Spearman（与 factor_selector._rank_ic 同口径）。"""
    if len(a) < 2:
        return 0.0
    ok = torch.isfinite(a) & torch.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 2:
        return 0.0
    ra, rb = _rank(a), _rank(b)
    if float(torch.std(ra)) < 1e-12 or float(torch.std(rb)) < 1e-12:
        return 0.0
    return float(torch.corrcoef(torch.stack([ra, rb]))[0, 1].item())


def rank_ic_series(
    scores: torch.Tensor,  # (T,N)
    labels: torch.Tensor,  # (T,N)
    mask: torch.Tensor,    # (T,N)
) -> Tuple[float, float, List[float]]:
    """逐时间戳截面 Rank IC → (mean_ic, icir, per_t_ics)。"""
    ics: List[float] = []
    for t in range(scores.shape[0]):
        m = mask[t]
        if m.sum() < 3:
            continue
        ic = spearman(scores[t][m], labels[t][m])
        ics.append(ic)
    if not ics:
        return 0.0, 0.0, []
    arr = np.asarray(ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std())
    return mean, (mean / std if std > 1e-12 else 0.0), ics
