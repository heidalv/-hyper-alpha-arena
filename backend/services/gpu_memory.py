"""GPU 显存释放工具（2026-08-21）。

PyTorch 的 caching allocator 分配过的显存**不会自动归还系统**——
后端启动后只要任何代码路径触发过 CUDA 初始化（torch.cuda.is_available()、
GpuEvalContext、冷池批量 IC 等），那块显存就被 python 进程握着直到退出。

夜间挖矿窗结束后如果不释放，白天 LLM 常驻就没有足够显存（实测被吃 3-4GB）。

用法：在 GPU 计算阶段结束的 finally 中调用 release_gpu_memory()。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def release_gpu_memory() -> int:
    """释放 PyTorch 缓存的 GPU 显存，返回释放后的已分配量（MB）。0 = 无 CUDA/失败。

    - torch.cuda.empty_cache()：归还 caching allocator 的空闲块给驱动
    - 前置 synchronize()：确保所有异步 kernel 完成后再释放
    - 对桌面/GUI 进程占的显存无效（那是 dwm/explorer 级别的，不归我们管）
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return 0
        torch.cuda.synchronize()
        before = torch.cuda.memory_reserved(0) // 1024 // 1024
        torch.cuda.empty_cache()
        after = torch.cuda.memory_reserved(0) // 1024 // 1024
        if before != after:
            logger.info(
                "[GpuMemRelease] PyTorch 缓存显存释放：%dMB → %dMB（归还 %dMB）",
                before, after, before - after,
            )
        return int(after)
    except ImportError:
        return 0
    except Exception as e:
        logger.debug("[GpuMemRelease] 释放失败（无害）: %s", e)
        return 0
