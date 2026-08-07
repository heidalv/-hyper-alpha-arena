"""断点续训 checkpoint 管理器（v6 10.1 老卡可靠性预案：长训断点续训）。

2016 老卡（GTX 1070）长训任务随时可能被温度/功耗保护打断，训练循环必须
"checkpoint 每 N 步落盘"、中断后可从最近断点恢复。本组件是通用状态快照：

- state 为任意 JSON/pickle 可序列化 dict（含 numpy 数组，训练器可放入
  model.state_dict() / optimizer.state_dict() / 随机数状态 / 步数等）。
- 原子写：先写临时文件再 rename，避免中断写坏 checkpoint。
- 保留最近 keep_last 个断点（防"最后一步恰好在被打断时写坏"场景）。

用法:
    ckpt = CheckpointManager("D:/models/ppo", every_n_steps=10, keep_last=3)
    ckpt.maybe_save(step, state)          # 每步调用，内部按 every_n_steps 落盘
    step, state = ckpt.load_latest()      # 恢复；无断点返回 (0, None)
    ckpt.latest_step()                    # 查询最近已保存步数（0 表示无）
"""
from __future__ import annotations

import datetime
import io
import json
import os
import pickle
import threading
from typing import Any, Dict, Optional, Tuple

import numpy as np

# 每个断点的元数据文件名（与状态文件同目录）
_META_NAME = "meta.json"


class CheckpointManager:
    """通用断点续训管理器（线程安全，原子写，保留最近 N 份）。"""

    def __init__(
        self,
        checkpoint_dir: str,
        every_n_steps: int = 10,
        keep_last: int = 3,
        prefix: str = "ckpt",
    ):
        if every_n_steps < 1:
            raise ValueError("every_n_steps must be >= 1")
        if keep_last < 1:
            raise ValueError("keep_last must be >= 1")
        self.checkpoint_dir = os.path.abspath(checkpoint_dir)
        self.every_n_steps = int(every_n_steps)
        self.keep_last = int(keep_last)
        self.prefix = prefix
        self._lock = threading.Lock()
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    # ─────────────────────────── 落盘 ───────────────────────────

    def maybe_save(self, step: int, state: Dict[str, Any]) -> Optional[str]:
        """按步数节奏落盘：step % every_n_steps == 0 时保存并返回路径，否则 None。"""
        if step % self.every_n_steps != 0:
            return None
        return self.save(step, state)

    def save(self, step: int, state: Dict[str, Any]) -> str:
        """强制保存（原子写：tmp 文件 + rename）。返回 checkpoint 路径。"""
        with self._lock:
            path = self._path_for(step)
            tmp = path + ".tmp"
            payload = {
                "step": int(step),
                "state": state,
            }
            with open(tmp, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)  # 原子替换，杜绝半写
            self._write_meta(step, path, list(state.keys()))
            self._prune()
            return path

    # ─────────────────────────── 恢复 ───────────────────────────

    def load_latest(self) -> Tuple[int, Optional[Dict[str, Any]]]:
        """加载最近断点，返回 (step, state)；无断点返回 (0, None)。"""
        path = self.latest_path()
        if path is None:
            return 0, None
        try:
            with open(path, "rb") as f:
                payload = pickle.load(f)
            return int(payload["step"]), payload["state"]
        except Exception:
            # 单个断点损坏时尝试更早的（keep_last 兜底）
            for earlier in self._paths()[1:]:
                try:
                    with open(earlier, "rb") as f:
                        payload = pickle.load(f)
                    return int(payload["step"]), payload["state"]
                except Exception:
                    continue
            return 0, None

    def latest_step(self) -> int:
        """最近已保存步数（0 表示无断点）。"""
        return self.load_latest()[0]

    def latest_path(self) -> Optional[str]:
        """最近断点文件路径（按步数最大者，非按 mtime）。"""
        paths = self._paths()
        return paths[0] if paths else None

    # ─────────────────────────── 内部 ───────────────────────────

    def _path_for(self, step: int) -> str:
        return os.path.join(self.checkpoint_dir, f"{self.prefix}_step{step:08d}.pkl")

    def _paths(self) -> list:
        files = []
        for name in os.listdir(self.checkpoint_dir):
            if not name.startswith(self.prefix + "_step") or not name.endswith(".pkl"):
                continue
            try:
                step = int(name.split("_step")[1][:8])
            except Exception:
                continue
            files.append((step, os.path.join(self.checkpoint_dir, name)))
        files.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in files]

    def _write_meta(self, step: int, path: str, state_keys: list) -> None:
        meta = {
            "step": step,
            "path": os.path.basename(path),
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "state_keys": state_keys,
        }
        meta_tmp = os.path.join(self.checkpoint_dir, _META_NAME + ".tmp")
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(meta_tmp, os.path.join(self.checkpoint_dir, _META_NAME))

    def _prune(self) -> None:
        """只保留最近 keep_last 份断点。"""
        for path in self._paths()[self.keep_last:]:
            try:
                os.remove(path)
            except OSError:
                pass


def dump_numpy(state: Dict[str, Any]) -> bytes:
    """把含 numpy 数组的 state 打包为字节（np.savez 流式，供跨环境传输）。"""
    buffer = io.BytesIO()
    np.savez(buffer, **state)
    return buffer.getvalue()


def load_numpy(data: bytes) -> Dict[str, Any]:
    """从 dump_numpy 字节恢复 state（numpy 版本间兼容）。"""
    buffer = io.BytesIO(data)
    with np.load(buffer, allow_pickle=True) as npz:
        return {k: npz[k] for k in npz.files}


def demo() -> None:
    """命令行演示：模拟 25 步训练，每 10 步落盘，中断后从断点恢复。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ckpt = CheckpointManager(tmp, every_n_steps=10, keep_last=3)
        rng = np.random.default_rng(0)
        weights = rng.normal(0, 1, 8)
        for step in range(1, 26):
            weights += rng.normal(0, 0.01, 8)  # 模拟权重更新
            ckpt.maybe_save(step, {"weights": weights, "seed": 0})
        last_step, state = ckpt.load_latest()
        print(f"demo: 训练到 step=25，断点恢复于 step={last_step}（weights 形状 {state['weights'].shape}）")
        assert last_step == 20, "every_n_steps=10 应最后落盘 step=20"


if __name__ == "__main__":
    demo()
