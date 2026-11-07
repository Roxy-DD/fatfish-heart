"""
向量化推理内核：把 900 个测试样本 × 1400 颗星压成一次矩阵乘。

用途：多轮迭代实验需要反复评估，逐样本 Python 循环太慢。
正确性：与 core.Infer 的逐样本版结果一致（E-12 内置一致性校验）。
"""
from __future__ import annotations

import numpy as np


class FlatIndex:
    """把星按 label 分组成矩阵，支持向量化打分。"""

    def __init__(self, vectors: np.ndarray, labels: list[str]):
        self.M = np.ascontiguousarray(vectors, dtype=np.float32)   # [N, D]
        self.labels = list(labels)
        self.uniq = sorted(set(labels))
        self.label_id = np.array([self.uniq.index(l) for l in labels], dtype=np.int32)
        self.masks = {i: np.where(self.label_id == i)[0] for i in range(len(self.uniq))}
        self.sizes = np.array([len(self.masks[i]) for i in range(len(self.uniq))], dtype=np.float32)
        self.n_class = len(self.uniq)

    def scores(self, X: np.ndarray, mode: str = "max", topk: int = 3,
               beta: float = 0.0, evt_beta: float = 0.0,
               mix: float = 0.0) -> np.ndarray:
        """
        X: [m, D] 已归一化  →  返回 [m, n_class]
        mode: max | topk
        mix:  当 mode=topk 时，与 max 的混合权重  score = (1-mix)*topk + mix*max
        beta: 减去 beta * (log n_k - mean log n)    （log 先验校正）
        evt_beta: 减去 evt_beta * sqrt(2 log n_k)   （极值偏差校正，更讲理）
        """
        S = X @ self.M.T                       # [m, N]
        out = np.empty((len(X), self.n_class), dtype=np.float32)

        for i in range(self.n_class):
            idx = self.masks[i]
            v = S[:, idx]                      # [m, n_i]
            if mode == "max" or (mix > 0 and mode == "max"):
                out[:, i] = v.max(axis=1)
            elif mode == "topk":
                k = min(topk, v.shape[1])
                part = np.partition(v, -k, axis=1)[:, -k:]
                tk = part.mean(axis=1)
                out[:, i] = tk if mix <= 0 else (1 - mix) * tk + mix * v.max(axis=1)
            else:
                raise ValueError(mode)

        n = self.sizes.astype(np.float64)
        if beta:
            pen = np.log(n) - np.log(n).mean()
            out -= (beta * pen).astype(np.float32)
        if evt_beta:
            pen = np.sqrt(2.0 * np.log(n))
            pen = pen - pen.mean()
            out -= (evt_beta * pen).astype(np.float32)
        return out

    def predict(self, X: np.ndarray, **kw) -> np.ndarray:
        return np.array(self.uniq, dtype=object)[np.argmax(self.scores(X, **kw), axis=1)]

    def accuracy(self, X: np.ndarray, y: np.ndarray, **kw) -> float:
        pred = self.predict(X, **kw)
        return float(np.mean(pred == y))


def build_index_from_brain(brain, include_children: bool = True) -> FlatIndex:
    stars = brain.all_stars() if include_children else brain.right.galaxy
    V = np.array([s.vector for s in stars], dtype=np.float32)
    L = [str(s.label) for s in stars]
    return FlatIndex(V, L)


def accuracy_by_task(idx: FlatIndex, tests: dict, labelsets: dict, **kw) -> dict:
    out = {}
    for k, (X, y) in tests.items():
        out[k] = idx.accuracy(X, y, **kw)
    out["macro"] = float(np.mean([out[k] for k in labelsets]))
    return out
