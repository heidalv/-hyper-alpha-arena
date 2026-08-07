"""
语义缓存（整改#13）—— 市场状态 prompt 高度可缓存，对标 GPTCache。

嵌入 prompt，相似查询（cosine ≥ threshold）且未过期则直接返回缓存响应，
显著降低 LLM 调用成本。自带零依赖 hashing embedder（确定性、可测），
也可注入 qaa 的 Neural/Hash embedder。

零风险：默认不接入任何实盘 LLM 调用路径，由 CachedLLMClient（llm_factory）在
LLM_SEMANTIC_CACHE_ENABLED=true 时显式启用。
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


class HashingEmbedder:
    """零依赖 hashing 词袋嵌入（char/词级），L2 归一化。确定性、可离线。

    对"仅少量数字/措辞不同"的市场状态 prompt 能给出高 cosine 相似度。
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _tokens(self, text: str) -> List[str]:
        text = (text or "").lower()
        toks = _TOKEN_RE.findall(text)
        # 加入 2-gram 提升语序敏感度
        bigrams = [f"{toks[i]}_{toks[i+1]}" for i in range(len(toks) - 1)]
        return toks + bigrams

    def embed(self, text: str) -> List[float]:
        vec = np.zeros(self.dim, dtype="float32")
        for tok in self._tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()


def _to_vec(embedder: Any, text: str) -> np.ndarray:
    if hasattr(embedder, "embed"):
        v = embedder.embed(text)
    elif callable(embedder):
        v = embedder(text)
    else:
        raise TypeError("embedder 需可调用或具备 .embed() 方法")
    arr = np.asarray(v, dtype="float32").ravel()
    n = np.linalg.norm(arr)
    return arr / n if n > 0 else arr


class SemanticCache:
    """嵌入 prompt，相似查询返回缓存响应。线程安全。"""

    def __init__(self, embedder: Any = None, similarity_threshold: float = 0.95,
                 ttl_seconds: float = 60.0, max_entries: int = 512):
        self.embedder = embedder or HashingEmbedder()
        self.threshold = float(similarity_threshold)
        self.ttl = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._store: List[Tuple[np.ndarray, str, float]] = []   # (vec, response, ts)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _evict_expired(self, now: float) -> None:
        if self.ttl <= 0:
            return
        self._store = [e for e in self._store if (now - e[2]) < self.ttl]

    def get(self, prompt: str) -> Optional[str]:
        vec = _to_vec(self.embedder, prompt)
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            best_sim, best_resp = -1.0, None
            for v, resp, ts in self._store:
                sim = float(np.dot(vec, v))   # 双方均已归一化 → 点积=cosine
                if sim > best_sim:
                    best_sim, best_resp = sim, resp
            if best_resp is not None and best_sim >= self.threshold:
                self.hits += 1
                return best_resp
            self.misses += 1
            return None

    def set(self, prompt: str, response: str) -> None:
        vec = _to_vec(self.embedder, prompt)
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            self._store.append((vec, response, now))
            if len(self._store) > self.max_entries:
                # 淘汰最旧
                self._store.sort(key=lambda e: e[2])
                self._store = self._store[-self.max_entries:]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }
