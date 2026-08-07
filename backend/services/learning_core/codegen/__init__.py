"""opencode 治理 codegen（方案需求 7，双模式）

  - 开发期助手：assist() 直接调用 opencode LLM 通道，辅助代码实现/重构/测试/文档。
  - 产品内 codegen：propose() 生成"因子/策略 .py"，仅写入隔离沙箱目录
    （data/codegen_shadow/，不在 backend 包内，不会被自动导入执行）；
    必须经 review + paper 验证 + Governor 审批（approve）后，才允许人工合入主干。

安全：解除 .py 生成硬禁令**仅限本受控管道**，且默认 OPENCODE_CODEGEN_SHADOW_ONLY=True，
approve 不做自动合并，只标记可合入并暴露沙箱路径供人工/worktree review。
"""

from .codegen_service import governed_codegen, GovernedCodegenService

__all__ = ["governed_codegen", "GovernedCodegenService"]
