"""
local_llm — 本地化 LLM 自训练（门控参数优化器）模块

设计目标：训练/部署一个专属本地模型（运行在内网 GPU 算力机上），
学习「在什么市场状态下、用什么门控参数最优」，通过 RuntimeGovernor
接入现有调参闭环，完全不触碰实时交易决策链。

详见 docs/LOCAL_LLM_SELF_TRAINING_DESIGN.md。

模块组成：
- gate_optimizer_service: 调 GPU 机推理 API 拿参数建议，经 Governor 仲裁写入
- dataset_builder:        读 DecisionSnapshot + 回测引擎，生成 SFT 训练数据集
- connectivity_check:     MVP 连通性测试（验证交易机→GPU 机链路）
"""

from . import gate_optimizer_service  # noqa: F401
from . import dataset_builder  # noqa: F401
