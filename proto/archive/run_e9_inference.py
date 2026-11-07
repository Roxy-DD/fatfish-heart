"""
E-9 修正版：在 core.py 的层级学习器上只换推理（与 E-1 完全可比）

修正 E-8 的三个错误：
  1) E-8 用平坦结构学习器，与 core.py 层级结构不同 → 基线不可比。本实验直接用 core.py。
  2) density 的 log1p(n)≈4.9，与 cos 差异 0.1 量级不可比 → 改为中心化 + 归一化，alpha 小量级。
  3) zscore 对大类趋于 0、小类可到 3+ → 先候选筛选（max 前 N 类），再在候选内比。

推理变体（学习器固定 core.py，阈值 0.705）：
  max        基线：全局 argmax(cos)
  topk       k=3,5,10 类内 top-k 均值
  prior_bayes max_cos + beta*log(先验)   即减去密度偏差
  prior_norm  同上，beta 扫描
  zscore_k    候选 top-N 内比 zscore
  cbs         class-balanced softmax 温度 T
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum, normalize
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def split(F, y, labels, ntr, nte, seed=0):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


class Infer:
    """
    在 core.CorpusCallosum 之上做可替换推理。
    星池 = all_stars()（含子星），按 label 分组。
    """

    def __init__(self, brain: CorpusCallosum, mode: str = "max",
                 topk: int = 5, beta: float = 0.0, cand: int = 3, edge_w: float = 0.5):
        self.brain = brain
        self.mode = mode
        self.topk = topk
        self.beta = beta
        self.cand = cand
        self.edge_w = edge_w
        self._reindex()

    def _reindex(self):
        stars = self.brain.all_stars()
        # 根星权重更重（它代表概念本体），子星权重略低
        root_ids = {id(s) for s in self.brain.right.galaxy}
        by_label: dict[str, list[np.ndarray]] = {}
        w_by_label: dict[str, list[float]] = {}
        for s in stars:
            by_label.setdefault(s.label, []).append(s.vector)
            w_by_label.setdefault(s.label, []).append(1.0 if id(s) in root_ids else self.edge_w)
        self.labels = list(by_label.keys())
        self.mats = {k: np.array(v, dtype=np.float32) for k, v in by_label.items()}
        self.weights = {k: np.array(v, dtype=np.float32) for k, v in w_by_label.items()}
        self.sizes = {k: len(v) for k, v in self.mats.items()}
        self.log_prior = {k: np.log(self.sizes[k]) for k in self.labels}
        self.mean_log_prior = float(np.mean(list(self.log_prior.values()))) if self.labels else 0.0

    def scores(self, x: np.ndarray) -> dict[str, float]:
        raw = {k: self.mats[k] @ x for k in self.labels}
        if self.mode == "max":
            return {k: float(v.max()) for k, v in raw.items()}

        if self.mode == "topk":
            out = {}
            for k in self.labels:
                v = raw[k]
                kk = min(self.topk, len(v))
                out[k] = float(np.mean(np.partition(v, -kk)[-kk:]))
            return out

        if self.mode == "prior_norm":
            # 中心化 log 先验：平均惩罚 0，alpha 可直接与 cos 同量级比较
            out = {}
            for k in self.labels:
                pen = self.log_prior[k] - self.mean_log_prior
                out[k] = float(raw[k].max()) - self.beta * pen
            return out

        if self.mode == "wtopk":
            # 根星/子星加权 top-k
            out = {}
            for k in self.labels:
                v, w = raw[k], self.weights[k]
                kk = min(self.topk * 2, len(v))
                idx = np.argpartition(v, -kk)[-kk:]
                order = idx[np.argsort(-v[idx])]
                tot_w, acc = 0.0, 0.0
                for i in order:
                    if tot_w + w[i] > self.topk * 0.5 and tot_w > 0:
                        break
                    acc += float(v[i]) * float(w[i])
                    tot_w += float(w[i])
                out[k] = acc / max(tot_w, 1e-9)
            return out

        if self.mode == "cbs":
            # class-balanced softmax：对类内 top-k 做温度聚合，再减 log 先验
            out = {}
            for k in self.labels:
                v = raw[k]
                kk = min(3, len(v))
                top = np.partition(v, -kk)[-kk:]
                m = float(top.max())
                lse = m + np.log(float(np.mean(np.exp((top - m) * 10.0))) + 1e-12)
                out[k] = float(lse) - self.beta * self.log_prior[k]
            return out

        raise ValueError(self.mode)

    def predict(self, x: np.ndarray) -> tuple[str | None, float]:
        if not self.labels:
            return None, 0.0

        if self.mode == "zscore_k":
            # 候选筛选：先按 max 取前 cand 类，再在候选内比 zscore
            base = {k: float((self.mats[k] @ x).max()) for k in self.labels}
            order = sorted(self.labels, key=lambda k: -base[k])[:self.cand]
            best_k, best_z = None, -1e9
            for k in order:
                v = self.mats[k] @ x
                z = (float(v.max()) - float(v.mean())) / (float(v.std()) + 1e-6)
                if z > best_z:
                    best_z, best_k = z, k
            return best_k, float(best_z)

        sc = self.scores(x)
        best_k = max(sc, key=lambda k: sc[k])
        return best_k, float(sc[best_k])

    def evaluate(self, X, y):
        if len(X) == 0:
            return 0.0
        c = 0
        for xi, yi in zip(X, y):
            p, _ = self.predict(xi)
            c += int(p == yi)
        return c / len(X)


def build(Ftr, ytr, threshold=0.705):
    b = CorpusCallosum(threshold=threshold, seed=0)
    tests = {}
    for name in ["A", "B", "C"]:
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE)
        tests[name] = (Xte, yte_t)
        for xi, yi in zip(Xtr, ytr_t):
            b.memorize(xi, yi)
    return b, tests


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)

    brain, tests = build(Ftr, ytr)
    print("=" * 100)
    print("E-9 层级学习器（core.py，与 E-1 同源）+ 只换推理")
    print("=" * 100)
    print(f"学习后: root={len(brain.right.galaxy)} 全部星={len(brain.all_stars())}")

    # 先确认基线复现 E-1
    base = Infer(brain, mode="max")
    bA = base.evaluate(*tests["A"])
    bB = base.evaluate(*tests["B"])
    bC = base.evaluate(*tests["C"])
    print(f"基线 max: A={bA:.4f} B={bB:.4f} C={bC:.4f}")
    print(f"（E-1 记录: A=0.6767 B=0.8700 C=0.8600 → 应一致）\n")

    configs = [("max", {})]
    for k in (3, 5, 10, 20):
        configs.append((f"topk{k}", {"mode": "topk", "topk": k}))
    for beta in (0.02, 0.05, 0.1, 0.2, 0.4):
        configs.append((f"prior{beta}", {"mode": "prior_norm", "beta": beta}))
    for k in (3, 5):
        configs.append((f"wtopk{k}", {"mode": "wtopk", "topk": k}))
    configs.append(("zscore_k3", {"mode": "zscore_k", "cand": 3}))
    configs.append(("zscore_k5", {"mode": "zscore_k", "cand": 5}))

    print(f"{'变体':<12}{'A':<9}{'B':<9}{'C':<9}{'macro':<9}{'ΔA':<9}{'Δmacro':<9}")
    print("-" * 100)
    rows = []
    for name, kw in configs:
        inf = Infer(brain, **kw)
        A, B, C = inf.evaluate(*tests["A"]), inf.evaluate(*tests["B"]), inf.evaluate(*tests["C"])
        macro = (A + B + C) / 3
        print(f"{name:<12}{A:<9.4f}{B:<9.4f}{C:<9.4f}{macro:<9.4f}{A-bA:<+9.4f}{macro-(bA+bB+bC)/3:<+9.4f}")
        rows.append({"variant": name, "kwargs": kw, "A": round(A, 4), "B": round(B, 4),
                     "C": round(C, 4), "macro": round(macro, 4),
                     "dA": round(A - bA, 4),
                     "dmacro": round(macro - (bA + bB + bC) / 3, 4)})

    print("-" * 100)
    best = max(rows, key=lambda r: r["A"])
    print(f"\n★ A 最高: {best['variant']}  A={best['A']:.4f} (基线 {bA:.4f}, {best['dA']:+.4f})")
    print(f"  判据 A>0.90: {'达成' if best['A'] > 0.90 else '未达成'}")
    bm = max(rows, key=lambda r: r["macro"])
    print(f"★ macro 最高: {bm['variant']}  macro={bm['macro']:.4f} (A={bm['A']:.4f})")

    out = {"experiment": "E-9", "learner": "core.py CorpusCallosum (hierarchical)",
           "threshold": 0.705,
           "n_root": len(brain.right.galaxy), "n_all": len(brain.all_stars()),
           "baseline": {"A": round(bA, 4), "B": round(bB, 4), "C": round(bC, 4)},
           "variants": rows, "best_by_A": best, "best_by_macro": bm}
    with open(os.path.join(RESULTS, "e9_inference_hier.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e9_inference_hier.json')}")


if __name__ == "__main__":
    main()
