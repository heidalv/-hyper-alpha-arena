"""账本层（v3 Phase 0 地基）。

- edge_ledger：按车道（tier × side × nature）统计 N / 毛 / 费 / 净 / PF / 95% CI，
  是"哪条车道真的有边际"的唯一口径来源，看板与资本分配器都从这里读数。
- fee_budget：账户级日手续费预算门，只拦新开/加仓，不拦平仓。
- trade_facts_reconcile：trade_facts 与 paper_positions 的日对账回填，保证学习样本 100% 覆盖。
"""
