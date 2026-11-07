"""
迭代实验用快速分类器：只改推理规则，分配规则保持原样。

科学问题（P0-1）：
  原实现 infer = 全局 argmax(cos)。星多的类天然占优（max over more candidates 偏高）。
  若只改推理规则、不动学习，能否把任务 A 的保留率拉回 90%？

推理变体：
  max       基线：全局 argmax(cos)
  topk      类内 top-k 均值（标准 kNN 聚合，削弱"多候选优势"）
  density   类内 max 减去 alpha*log(类内星数)（显式惩罚密度）
  centroid  与类质心余弦（对类大小不敏感）
  zscore    类内标准化分数 (max - mean)/std（衡量"相对突出程度"）
"""
from __future__ import annotations

import numpy as np


class VGalaxy:
    """向量化星库。分配规则与 core.py 一致（全局 max + 标签匹配 + 阈值）。"""

    def __init__(self, dim: int, cap: int = 4096):
        self.dim = dim
        self.M = np.zeros((cap, dim), dtype=np.float32)
        self.lab_id = np.zeros(cap, dtype=np.int32)
        self.n = 0
        self.name_to_id: dict[str, int] = {}
        self.id_to_name: list[str] = []
        self.idx_by_id: dict[int, list[int]] = {}
        self._masks: dict[int, np.ndarray] = {}
        self._centroids: dict[int, np.ndarray] = {}
        self._dirty: set[int] = set()

    # --- 基础 ---
    def _lid(self, label: str) -> int:
        if label not in self.name_to_id:
            new_id = len(self.id_to_name)
            self.name_to_id[label] = new_id
            self.id_to_name.append(label)
            self.idx_by_id[new_id] = []
        return self.name_to_id[label]

    def add(self, vec: np.ndarray, label: str):
        if self.n >= len(self.lab_id):
            self.M = np.vstack([self.M, np.zeros((len(self.lab_id), self.dim), np.float32)])
            self.lab_id = np.concatenate([self.lab_id, np.zeros(len(self.lab_id), np.int32)])
        lid = self._lid(label)
        self.M[self.n] = vec
        self.lab_id[self.n] = lid
        self.idx_by_id[lid].append(self.n)
        self._dirty.add(lid)
        self.n += 1

    def _ensure(self, lid: int):
        if lid in self._dirty:
            idx = np.array(self.idx_by_id[lid])
            self._masks[lid] = idx
            self._centroids[lid] = self.M[idx].mean(axis=0)
            self._dirty.discard(lid)

    def sizes(self) -> dict[int, int]:
        return {lid: len(v) for lid, v in self.idx_by_id.items() if v}

    # --- 打分 ---
    def score_label(self, x: np.ndarray, lid: int, mode: str, topk: int, alpha: float) -> float:
        self._ensure(lid)
        idx = self._masks[lid]
        s = self.M[idx] @ x
        n = len(idx)
        if mode == "max":
            return float(s.max())
        if mode == "topk":
            k = min(topk, n)
            return float(np.mean(np.partition(s, -k)[-k:]))
        if mode == "density":
            return float(s.max()) - alpha * np.log1p(n)
        if mode == "centroid":
            c = self._centroids[lid]
            nc = np.linalg.norm(c)
            return float((c @ x) / nc) if nc > 0 else 0.0
        if mode == "zscore":
            mu, sd = float(s.mean()), float(s.std())
            return float((s.max() - mu) / (sd + 1e-6))
        raise ValueError(mode)

    def predict(self, x: np.ndarray, mode: str = "max", topk: int = 3,
                alpha: float = 1.0, exclude: set[int] | None = None) -> tuple[str | None, float]:
        if self.n == 0:
            return None, 0.0
        best_lid, best = -1, -2.0
        for lid in self.idx_by_id:
            if not self.idx_by_id[lid]:
                continue
            if exclude and lid in exclude:
                continue
            sc = self.score_label(x, lid, mode, topk, alpha)
            if sc > best:
                best, best_lid = sc, lid
        if best_lid < 0:
            return None, 0.0
        return self.id_to_name[best_lid], float(best)

    def best_star_global(self, x: np.ndarray) -> tuple[int, str | None, float]:
        """分配用：全局最大余弦（与原实现一致）。返回 (index, label, sim)"""
        if self.n == 0:
            return -1, None, 0.0
        s = self.M[:self.n] @ x
        i = int(np.argmax(s))
        return i, self.id_to_name[self.lab_id[i]], float(s[i])


class VBrain:
    """
    分配规则严格沿用 core.py：
      - 全局 argmax 找最近星
      - 标签相同且 cos > threshold → 命中，合并向量（**不新建**）
      - 否则 → 新建
      - 命中的星 mass > mitosis_threshold → 分裂（同标签另存一份）
    推理规则可变（mode）。
    """

    def __init__(self, dim: int, threshold: float, mode: str = "max",
                 topk: int = 3, alpha: float = 1.0, mitosis_threshold: int = 5,
                 merge_rate: str = "mass"):
        self.g = VGalaxy(dim)
        self.threshold = threshold
        self.mode = mode
        self.topk = topk
        self.alpha = alpha
        self.mitosis_threshold = mitosis_threshold
        self.merge_rate = merge_rate
        self.mass = np.zeros(4096, dtype=np.int32)
        self.n_created = 0
        self.n_reinforced = 0
        self.n_mitosis = 0
        self._cap = 4096

    def _sync_cap(self):
        if self.g.n > len(self.mass):
            new_cap = max(len(self.mass) * 2, self.g.n + 1024)
            m = np.zeros(new_cap, dtype=np.int32)
            m[:len(self.mass)] = self.mass
            self.mass = m

    def memorize(self, x: np.ndarray, y: str):
        i, lab, sim = self.g.best_star_global(x)
        if i >= 0 and lab == y and sim > self.threshold:
            m = int(self.mass[i])
            if m > self.mitosis_threshold:
                # 分裂：同标签另存一份（与原实现 mitosis 语义一致）
                self.g.add(x, y)
                self._sync_cap()
                self.mass[self.g.n - 1] = 1
                self.n_mitosis += 1
                return
            rate = 1.0 / (m + 1) if self.merge_rate == "mass" else 0.1
            nv = self.g.M[i] * (1 - rate) + x * rate
            nn = np.linalg.norm(nv)
            if nn > 0:
                nv = nv / nn
            self.g.M[i] = nv
            self.mass[i] = m + 1
            self.g._dirty.add(int(self.g.lab_id[i]))
            self.n_reinforced += 1
        else:
            self.g.add(x, y)
            self._sync_cap()
            self.mass[self.g.n - 1] = 1
            self.n_created += 1

    def perceive(self, x: np.ndarray) -> tuple[str | None, float]:
        return self.g.predict(x, mode=self.mode, topk=self.topk, alpha=self.alpha)

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> float:
        if len(X) == 0:
            return 0.0
        correct = 0
        for xi, yi in zip(X, y):
            p, _ = self.perceive(xi)
            correct += int(p == yi)
        return correct / len(X)
