"""
cosmos_lite —— 按 Cosmos-Net 的**真实设计意图**重写的试验原型。

设计意图来源：docs/01 设计意图.md（从代码结构反推的七步链条）
缺陷修复对照：docs/03 缺陷与证据.md（A–F）
验收判据：docs/04 试验设计.md（E-1 ~ E-6）

与原实现的关键差异（每条都对应一个缺陷编号）：
  A 加噪     原：合并前加噪，阻断合并 → 本：阈值随机化（annealing on threshold）
  B 修剪     原：mass > 1 一刀切     → 本：新鲜度 + 质量双阈值
  C CD 分母  原：簇心欧氏距离        → 本：角度距离 (1 - cos)
  D 左脑维度 原：len == 784          → 本：784/1280 双支持
  E 合并范围 原：同标签              → 本：可配置跨标签
  F 递归     原：不递归 children     → 本：递归
  新 存储    原：pickle 整个对象     → 本：JSON + npy，带 schema 版本（支撑 E-4）
"""

from __future__ import annotations

import json
import os
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

SCHEMA_VERSION = "cosmos-lite/1"


# ----------------------------------------------------------------------------
# 0. 基础
# ----------------------------------------------------------------------------

def normalize(w: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(w)
    return w / n if n > 0 else w


def cos(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度。原实现用 np.dot（已归一化，等价）——此处显式归一化，防未归一向量的静默错误。"""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def angle_dist(a: np.ndarray, b: np.ndarray) -> float:
    """角度距离 1 - cos，修缺陷 C：原实现用欧氏距离，在归一化空间接近常数。"""
    return 1.0 - cos(a, b)


# ----------------------------------------------------------------------------
# 1. 记忆单元
# ----------------------------------------------------------------------------

@dataclass
class Star:
    """一颗记忆恒星 = 一个原型向量 + 它的统计。"""
    vector: np.ndarray
    label: str
    mass: int = 1
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    children: list = field(default_factory=list)

    # 左脑统计（均值/m2，Welford），修缺陷 D 后 1280 维也能用
    stats: dict = field(default_factory=dict)
    feat_dim: int = 0

    def touch(self, now: Optional[float] = None):
        self.mass += 1
        self.last_seen = now if now is not None else time.time()

    def is_category(self) -> bool:
        return len(self.children) > 0

    # --- 序列化（不 pickle，支撑 E-4 迁移）---
    def to_meta(self) -> dict:
        return {
            "label": self.label,
            "mass": self.mass,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "stats": self.stats,
            "feat_dim": self.feat_dim,
            "n_children": len(self.children),
        }

    @staticmethod
    def from_meta(meta: dict, vector: np.ndarray) -> "Star":
        s = Star(vector=vector, label=meta["label"], mass=meta["mass"],
                 created_at=meta["created_at"], last_seen=meta["last_seen"],
                 stats=meta.get("stats", {}), feat_dim=meta.get("feat_dim", len(vector)))
        return s


# ----------------------------------------------------------------------------
# 2. 右脑：几何 / 原型（快、联想）
# ----------------------------------------------------------------------------

class RightHemisphere:
    def __init__(self, threshold: float = 0.85, mitosis_threshold: int = 5,
                 cross_label_merge: bool = False):
        self.galaxy: list[Star] = []
        self.threshold = threshold
        self.mitosis_threshold = mitosis_threshold
        self.cross_label_merge = cross_label_merge  # 修缺陷 E
        self.n_created = 0
        self.n_reinforced = 0
        self.n_mitosis = 0

    # --- 感知 ---
    def perceive(self, x: np.ndarray, pool: Optional[list] = None) -> tuple[Optional[Star], float]:
        pool = self.galaxy if pool is None else pool
        if not pool:
            return None, 0.0
        sims = [cos(x, s.vector) for s in pool]
        idx = int(np.argmax(sims))
        best, sim = pool[idx], float(sims[idx])
        # 递归进子池（修缺陷 F 的感知侧）
        if sim > self.threshold and best.is_category():
            child, csim = self.perceive(x, pool=best.children)
            if child is not None and csim > sim:
                return child, csim
        return best, sim

    # --- 学习 ---
    def memorize(self, x: np.ndarray, y: str, now: Optional[float] = None) -> str:
        x = normalize(x)
        best, sim = self.perceive(x)

        if best is not None and sim > self.threshold and (self.cross_label_merge or best.label == y):
            # 命中原型：更新（质量加权）
            if best.mass > self.mitosis_threshold:
                # 分裂：升级为类目，开子池
                child = Star(vector=x.copy(), label=best.label, feat_dim=len(x))
                best.children.append(child)
                self.n_mitosis += 1
                return "mitosis"
            lr = 1.0 / (best.mass + 1)
            best.vector = normalize(best.vector * (1 - lr) + x * lr)
            best.touch(now)
            self.n_reinforced += 1
            return "reinforce"

        # 新颖：分配新单元（不覆盖任何已有参数）
        self.galaxy.append(Star(vector=x.copy(), label=y, feat_dim=len(x)))
        self.n_created += 1
        return "create"

    # --- 睡眠：质量加权合并 + 递归（修缺陷 F）---
    def dream(self, threshold: Optional[float] = None, noise_on_threshold: float = 0.0,
              rng: Optional[np.random.Generator] = None, recursive: bool = True) -> dict:
        """
        修缺陷 A：噪声不再作用于向量，而是作用于**阈值**（模拟退火）。
        原实现先加噪再合并，σ=0.05 就把余弦打到 0.34，合并彻底不发生。
        """
        rng = rng or np.random.default_rng(0)
        thr = self.threshold if threshold is None else threshold
        if noise_on_threshold > 0:
            thr = float(np.clip(thr + rng.normal(0, noise_on_threshold), 0.0, 1.0))

        before = len(self.galaxy)
        # 质量加权排序：重的先留下
        self.galaxy.sort(key=lambda s: s.mass, reverse=True)
        kept: list[Star] = []
        merged = 0
        for s in self.galaxy:
            hit = False
            for k in kept:
                same = (k.label == s.label) or self.cross_label_merge
                if same and angle_dist(s.vector, k.vector) < (1.0 - thr):
                    total = k.mass + s.mass
                    rate = s.mass / total
                    k.vector = normalize(k.vector * (1 - rate) + s.vector * rate)
                    k.mass = total
                    k.last_seen = max(k.last_seen, s.last_seen)
                    k.children.extend(s.children)
                    merged += 1
                    hit = True
                    break
            if not hit:
                kept.append(s)
        self.galaxy = kept

        child_merged = 0
        if recursive:
            for s in self.galaxy:
                if s.children:
                    before_c = len(s.children)
                    sub = RightHemisphere(threshold=thr, cross_label_merge=self.cross_label_merge)
                    sub.galaxy = s.children
                    sub.dream(threshold=thr, recursive=True)
                    s.children = sub.galaxy
                    child_merged += before_c - len(s.children)

        return {"stars_before": before, "stars_after": len(self.galaxy),
                "merged": merged, "child_merged": child_merged, "threshold_used": thr}

    def all_stars(self) -> list[Star]:
        out = []
        for s in self.galaxy:
            out.append(s)
            if s.children:
                out.extend(self._flatten(s.children))
        return out

    def _flatten(self, pool: list[Star]) -> list[Star]:
        out = []
        for s in pool:
            out.append(s)
            if s.children:
                out.extend(self._flatten(s.children))
        return out

    # --- 修剪（修缺陷 B：新鲜度 + 质量双阈值）---
    def prune(self, min_mass: int = 1, min_age_s: float = 0.0,
              now: Optional[float] = None, protect_labels: Optional[set] = None) -> dict:
        """
        原实现：if start_count > 50: keep mass > 1
                → mass==1 是"刚学的"，被成批删除（实测任务 A 剩 0/3）
        本实现：
          - 只有「质量低」**且**「已经很久没见过」的才剪
          - 提供 protect_labels 保护任务关键记忆
        """
        now = now if now is not None else time.time()
        protect_labels = protect_labels or set()
        before = len(self.galaxy)
        out = []
        for s in self.galaxy:
            if s.label in protect_labels:
                out.append(s); continue
            stale = (now - s.last_seen) >= min_age_s
            weak = s.mass < min_mass
            if weak and stale:
                continue
            out.append(s)
        self.galaxy = out
        return {"stars_before": before, "stars_after": len(self.galaxy), "pruned": before - len(self.galaxy)}


# ----------------------------------------------------------------------------
# 3. 左脑：统计 / 分布（慢、解析）—— 修缺陷 D
# ----------------------------------------------------------------------------

class LeftHemisphere:
    """
    原实现 _extract_features 里 is_pixel_data = (len(vector) == 784)
    而 Retina 输出 1280 维 → 左脑永久返回 (None, 0.0)，双脑机制在默认路径下等于没有。

    本实现：784 走像素几何；其他维度走「可学习投影后的几何特征」。
    """

    def __init__(self, proj_dim: int = 32, seed: int = 0, z_threshold: float = 2.0):
        self.proj_dim = proj_dim
        self.z_threshold = z_threshold
        self._proj: Optional[np.ndarray] = None
        self._proj_dim_in: Optional[int] = None
        self._rng = np.random.default_rng(seed)
        self.knowledge: dict[str, dict[str, list[float]]] = {}

    def _ensure_proj(self, dim: int):
        if self._proj is None or self._proj_dim_in != dim:
            # 固定种子 → 跨进程一致（E-4 迁移必需）
            rng = np.random.default_rng(1000 + dim)
            self._proj = rng.normal(0, 1.0 / np.sqrt(dim), (dim, self.proj_dim))
            self._proj_dim_in = dim

    def features(self, v: np.ndarray) -> dict[str, float]:
        self._ensure_proj(len(v))
        p = v @ self._proj                      # 投影到 32 维
        f = {
            "norm_l1": float(np.sum(np.abs(v))),        # 墨迹量（≈sqrt(非零数)）
            "proj_mean": float(np.mean(p)),
            "proj_std": float(np.std(p)),
            "proj_max": float(np.max(p)),
            "proj_min": float(np.min(p)),
        }
        if len(v) == 784:                        # 像素路径额外几何特征
            img = v.reshape(28, 28)
            ys, xs = np.nonzero(img > 0.1)
            if len(ys) > 0:
                f["aspect"] = float((ys.max() - ys.min() + 1) / max(1, (xs.max() - xs.min() + 1)))
                cy, cx = ys.mean(), xs.mean()
                f["centroid_y"] = float(cy / 28.0)
                f["centroid_x"] = float(cx / 28.0)
            else:
                f["aspect"] = 1.0
                f["centroid_y"] = 0.0
                f["centroid_x"] = 0.0
        return f

    def memorize(self, v: np.ndarray, y: str):
        fs = self.features(v)
        bucket = self.knowledge.setdefault(y, {})
        for k, val in fs.items():
            n, mean, m2 = bucket.get(k, [0, 0.0, 0.0])
            n += 1
            delta = val - mean
            mean += delta / n
            delta2 = val - mean
            m2 += delta * delta2
            bucket[k] = [n, mean, m2]

    def perceive(self, v: np.ndarray) -> tuple[Optional[str], float]:
        if not self.knowledge:
            return None, 0.0
        fs = self.features(v)
        best_label, best_z = None, float("inf")
        for label, stats in self.knowledge.items():
            zs, cnt = 0.0, 0
            for k, val in fs.items():
                if k not in stats:
                    continue
                n, mean, m2 = stats[k]
                if n < 2:
                    continue
                var = m2 / (n - 1)
                sd = np.sqrt(max(var, 1e-12))
                zs += abs(val - mean) / sd
                cnt += 1
            if cnt:
                avg = zs / cnt
                if avg < best_z:
                    best_z, best_label = avg, label
        if best_z < 1.0:
            return best_label, 1.0
        if best_z < self.z_threshold:
            return best_label, 0.5
        return None, 0.0


# ----------------------------------------------------------------------------
# 4. 胼胝体：仲裁 + 共同学习（修缺陷 D 后半）
# ----------------------------------------------------------------------------

class CorpusCallosum:
    """
    设计意图第 6 条：Inhibit Expression, Not Learning.
    仲裁决定"谁说话"，但不阻止"谁学习"。
    """

    def __init__(self, threshold: float = 0.85, mitosis_threshold: int = 5,
                 cross_label_merge: bool = False, lr: float = 0.02, seed: int = 0):
        self.right = RightHemisphere(threshold, mitosis_threshold, cross_label_merge)
        self.left = LeftHemisphere(seed=seed)
        self.dominance = 0.5          # 1.0 = 纯直觉，0.0 = 纯逻辑
        self.lr = lr
        self.history: list[tuple[float, float, int]] = []   # (dominance, loss, step)
        self.n_steps = 0

    # --- 感知 ---
    def perceive(self, x: np.ndarray) -> tuple[Optional[str], float, dict]:
        star, r_sim = self.right.perceive(x)
        l_label, l_conf = self.left.perceive(x)

        r_score = r_sim * self.dominance
        l_score = l_conf * (1.0 - self.dominance)

        info = {"right_label": star.label if star else None, "right_sim": r_sim,
                "left_label": l_label, "left_conf": l_conf,
                "dominance": self.dominance, "winner": None}

        if star is not None and r_score >= l_score:
            info["winner"] = "right"
            return star.label, r_sim, info
        if l_label is not None:
            info["winner"] = "left"
            return l_label, l_conf, info
        if star is not None:
            info["winner"] = "right"
            return star.label, r_sim, info
        return None, 0.0, info

    # --- 学习：仲裁 != 学习 ---
    def memorize(self, x: np.ndarray, y: str, now: Optional[float] = None) -> dict:
        star, r_sim = self.right.perceive(x)
        l_label, l_conf = self.left.perceive(x)

        r_correct = star is not None and star.label == y
        l_correct = l_label is not None and l_label == y

        if r_correct and not l_correct:
            self.dominance = min(1.0, self.dominance + self.lr)
        elif l_correct and not r_correct:
            self.dominance = max(0.0, self.dominance - self.lr)

        # 抑制表达，不抑制学习：
        r_msg = self.right.memorize(x, y, now=now)
        l_conf_pre = l_conf
        self.left.memorize(x, y)
        l_conf_post = self.left.perceive(x)[1]

        self.n_steps += 1
        self.history.append((self.dominance, float(l_conf_post - l_conf_pre), self.n_steps))

        return {"right": r_msg, "dominance": self.dominance,
                "left_conf_delta": l_conf_post - l_conf_pre,
                "l_correct": l_correct, "r_correct": r_correct}

    # --- 睡眠 ---
    def dream(self, threshold: Optional[float] = None, noise_on_threshold: float = 0.0,
              rng: Optional[np.random.Generator] = None) -> dict:
        return self.right.dream(threshold=threshold, noise_on_threshold=noise_on_threshold, rng=rng)

    def prune(self, **kw) -> dict:
        return self.right.prune(**kw)

    def all_stars(self) -> list[Star]:
        return self.right.all_stars()

    # --- 序列化（支撑 E-4）---
    def save(self, path: str, extra: Optional[dict] = None):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        base = os.path.splitext(path)[0]
        meta = {
            "schema": SCHEMA_VERSION,
            "saved_at": time.time(),
            "dominance": self.dominance,
            "lr": self.lr,
            "n_steps": self.n_steps,
            "config": {
                "threshold": self.right.threshold,
                "mitosis_threshold": self.right.mitosis_threshold,
                "cross_label_merge": self.right.cross_label_merge,
                "proj_dim": self.left.proj_dim,
                "z_threshold": self.left.z_threshold,
            },
            "counters": {
                "n_created": self.right.n_created,
                "n_reinforced": self.right.n_reinforced,
                "n_mitosis": self.right.n_mitosis,
            },
            "galaxy": [s.to_meta() for s in self.right.galaxy],
            "left_knowledge": self.left.knowledge,
            "extra": extra or {},
        }
        with open(base + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        # 向量单独存 npy，避免 JSON 精度与体积问题
        vecs = [s.vector.astype(np.float32) for s in self.right.galaxy]
        np.savez_compressed(base + ".vectors.npz",
                            **{f"v{i}": v for i, v in enumerate(vecs)})
        # 子星向量
        child_vecs = {}

        def collect_children(pool, prefix, path_prefix=()):
            for i, s in enumerate(pool):
                if s.children:
                    key = f"{prefix}h{'_'.join(map(str, path_prefix))}_{i}"
                    child_vecs[key] = np.stack([c.vector for c in s.children]).astype(np.float32)
                    collect_children(s.children, prefix, path_prefix + (i,))

        collect_children(self.right.galaxy, "c")
        if child_vecs:
            np.savez_compressed(base + ".children.npz", **child_vecs)

        return {"meta": base + ".meta.json", "vectors": base + ".vectors.npz",
                "n_stars": len(self.right.galaxy),
                "sha256_meta": hashlib.sha256(
                    open(base + ".meta.json", "rb").read()).hexdigest()[:16]}

    @staticmethod
    def load(path: str) -> tuple["CorpusCallosum", dict]:
        base = os.path.splitext(path)[0]
        with open(base + ".meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["schema"] == SCHEMA_VERSION, f"schema 不符: {meta['schema']}"

        cfg = meta["config"]
        brain = CorpusCallosum(threshold=cfg["threshold"],
                               mitosis_threshold=cfg["mitosis_threshold"],
                               cross_label_merge=cfg["cross_label_merge"],
                               lr=meta["lr"])
        brain.left.proj_dim = cfg["proj_dim"]
        brain.left.z_threshold = cfg["z_threshold"]
        brain.dominance = meta["dominance"]
        brain.n_steps = meta["n_steps"]
        brain.left.knowledge = meta["left_knowledge"]

        npz = np.load(base + ".vectors.npz")
        for i, m in enumerate(meta["galaxy"]):
            v = npz[f"v{i}"]
            brain.right.galaxy.append(Star.from_meta(m, v))

        # 恢复子星
        cpath = base + ".children.npz"
        if os.path.exists(cpath):
            cn = np.load(cpath)
            for key in cn.files:
                # key 形如 "ch0_1" / "ch2" / "ch0h1_3"：h 后是父路径，_ 后是本层索引
                body = key[1:]                       # 去掉前导 'c'
                if "h" in body:
                    head, last = body.rsplit("_", 1)
                    path = tuple(int(x) for x in head.lstrip("h").split("_") if x != "") if head.strip("h") else ()
                else:
                    last = body
                    path = ()
                last_idx = int(last)
                pool = brain.right.galaxy
                for idx in path:
                    pool = pool[idx].children
                parent = pool[last_idx]
                for v in cn[key]:
                    parent.children.append(Star(vector=v, label=parent.label,
                                                created_at=parent.created_at,
                                                last_seen=parent.last_seen,
                                                feat_dim=len(v)))
        return brain, meta


# ----------------------------------------------------------------------------
# 5. 指标（修缺陷 C：角度距离）
# ----------------------------------------------------------------------------

def system_entropy(galaxy: list[Star]) -> float:
    """SE = -Σ p ln p，p 为质量占比。碎片化 = 熵高。"""
    total = sum(s.mass for s in galaxy)
    if not total or len(galaxy) < 2:
        return 0.0
    return float(-sum((s.mass / total) * np.log(s.mass / total) for s in galaxy if s.mass > 0))


def conflict_degree(galaxy: list[Star]) -> float:
    """
    CD = 类内散度 / 类间距离。
    修缺陷 C：分母改用**角度距离**。原实现用欧氏距离，在归一化向量上接近常数，
    把 CD 压到 0.15 量级（注释却写 ≈15）。
    """
    groups: dict[str, list[np.ndarray]] = {}
    for s in galaxy:
        groups.setdefault(s.label, []).append(s.vector)
    if len(groups) < 2:
        return 0.0

    intra, centroids = [], []
    for vecs in groups.values():
        arr = np.array(vecs)
        cen = normalize(arr.mean(axis=0))
        centroids.append(cen)
        if len(arr) > 1:
            intra.append(float(np.mean([angle_dist(v, cen) for v in arr])))
        else:
            intra.append(0.0)
    mean_intra = float(np.mean(intra)) if intra else 0.0

    inter = [angle_dist(centroids[i], centroids[j])
             for i in range(len(centroids)) for j in range(i + 1, len(centroids))]
    mean_inter = float(np.mean(inter)) if inter else 1.0
    if mean_inter == 0:
        return float("inf")
    return mean_intra / mean_inter


def evaluate(brain: CorpusCallosum, X: np.ndarray, y: np.ndarray,
             labels: Optional[set] = None) -> dict:
    """逐样本预测，返回总体与分组准确率。"""
    correct, total = 0, 0
    per_label: dict[str, list[int]] = {}
    for xi, yi in zip(X, y):
        pred, sim, _ = brain.perceive(xi)
        ok = (pred == yi)
        if labels is not None and yi not in labels:
            continue
        total += 1
        correct += int(ok)
        pl = per_label.setdefault(yi, [0, 0])
        pl[0] += int(ok)
        pl[1] += 1
    acc = correct / total if total else 0.0
    return {"acc": acc, "correct": correct, "total": total,
            "per_label": {k: v[0] / v[1] for k, v in per_label.items() if v[1]}}


# ----------------------------------------------------------------------------
# 6. 自检
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    rng = np.random.default_rng(0)
    D = 128
    b = CorpusCallosum(threshold=0.85, seed=0)

    # 造 3 个概念
    centers = {str(k): normalize(rng.normal(size=D)) for k in range(3)}
    for step in range(300):
        k = str(step % 3)
        x = normalize(centers[k] + rng.normal(0, 0.15, D))
        b.memorize(x, k)

    st = b.all_stars()
    print(f"星数: {len(st)}  SE={system_entropy(b.right.galaxy):.4f}  "
          f"CD={conflict_degree(b.right.galaxy):.4f}  dominance={b.dominance:.3f}")
    print(f"创建/强化/分裂: {b.right.n_created}/{b.right.n_reinforced}/{b.right.n_mitosis}")

    # 迁移自检（用固定目录，避免 tempfile 清理被沙箱拒绝）
    td = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest")
    os.makedirs(td, exist_ok=True)
    p = os.path.join(td, "brain.json")
    info = b.save(p)
    b2, meta = CorpusCallosum.load(p)
    same = all(cos(a.vector, c.vector) > 0.9999
               for a, c in zip(b.right.galaxy, b2.right.galaxy))
    print(f"迁移: 保存 {info['n_stars']} 星 → 重载 {len(b2.right.galaxy)} 星，"
          f"向量一致={same}, dominance={b2.dominance:.3f}")
    print(f"schema={meta['schema']}")
