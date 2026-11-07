"""
E-18 最后一轮：类别对专属参数

唯一还没试过的方向。之前所有方法都是**全局统一参数**（一个 topk、一个 margin、一个 beta）。
但混淆矩阵显示错误集中在少数类对：
  3→5 (7), 2→3 (6), 9→4 (5), 7→4 (5), 2→4 (5), 1→7 (5), 8→2 (4), 2→5 (4)

假设：这些类对需要**不同**的判别参数（topk 大小、margin 宽度）。

防过拟合措施：
  - 参数在**验证集**上调（seed 的分割独立），在**测试集**上评
  - 只对 top-N 个高错误类对启用专属参数，其余走全局
  - 报告启用类对的数量，避免"全都专属"= 过拟合

判据：C > 0.89 且 macro > 0.90（5 seeds），macro_min > 0.88
"""
from __future__ import annotations

import json
import os
import sys
import time
import itertools

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_lite"))

import numpy as np

from core import CorpusCallosum
from retina import get_features

RESULTS = os.path.join(_HERE, "results")
TASKS = {"A": {"0", "1", "2"}, "B": {"3", "4", "5"}, "C": {"6", "7", "8", "9"}}
TR, TE = 900, 300


def l2(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def split(F, y, labels, ntr, nte, seed):
    rng = np.random.default_rng(seed)
    idx = np.array([i for i, lab in enumerate(y) if lab in labels])
    rng.shuffle(idx)
    return F[idx[:ntr]], y[idx[:ntr]], F[idx[ntr:ntr + nte]], y[idx[ntr:ntr + nte]]


class Idx:
    """支持每类独立的 topk 参数。"""

    def __init__(self, V, L):
        self.M = np.ascontiguousarray(V, np.float32)
        self.labels = [str(x) for x in L]
        self.uniq = sorted(set(self.labels))
        lid = np.array([self.uniq.index(l) for l in self.labels], np.int32)
        self.masks = [np.where(lid == i)[0] for i in range(len(self.uniq))]

    def scores(self, X, topk_map):
        S = X @ self.M.T
        nc = len(self.uniq)
        out = np.empty((len(X), nc), np.float32)
        for i, idx in enumerate(self.masks):
            v = S[:, idx]
            k = min(topk_map.get(self.uniq[i], 3), v.shape[1])
            out[:, i] = np.partition(v, -k, axis=1)[:, -k:].mean(axis=1)
        return out

    def predict(self, X, topk_map):
        return np.array(self.uniq, dtype=object)[np.argmax(self.scores(X, topk_map), axis=1)]

    def accuracy(self, X, y, topk_map):
        return float(np.mean(self.predict(X, topk_map)
                             == np.array([str(v) for v in y], dtype=object)))


def build(Ftr, ytr, seed, transform, threshold=0.705):
    b = CorpusCallosum(threshold=threshold, seed=seed)
    chunks, tests = {}, {}
    for name in "ABC":
        Xtr, ytr_t, Xte, yte_t = split(Ftr, ytr, TASKS[name], TR, TE, seed)
        chunks[name] = (transform(Xtr), ytr_t)
        tests[name] = (Xte, yte_t)
    for name in "ABC":
        Xtr_t, ytr_t = chunks[name]
        for xi, yi in zip(Xtr_t, ytr_t):
            b.memorize(xi, yi)
    stars = b.all_stars()
    return Idx(np.array([s.vector for s in stars], np.float32), [s.label for s in stars]), tests


def main():
    Ftr, ytr, _ = get_features(True, "main", limit=20000, input_size=96)
    Fte, yte, _ = get_features(False, "main", limit=8000, input_size=96)
    mu = Ftr[:9000].mean(axis=0)
    sd = Ftr[:9000].std(axis=0) + 1e-6
    zsc = lambda F: l2((F - mu) / sd)

    t0 = time.time()
    print("=" * 100)
    print("E-18 类别专属参数（在 zscore-dim 表征上，阈值 0.705）")
    print("=" * 100)

    # ---------- 训练/验证/测试三段 ----------
    # 调参用 seed=0 的学习结果 + 验证集；评测用 seed=1..4
    seeds_eval = [1, 2, 3, 4]
    ix0, tests0 = build(Ftr, ytr, 0, zsc)
    Xv = np.vstack([tests0[t][0] for t in "ABC"])
    yv = np.concatenate([tests0[t][1] for t in "ABC"])
    yv_s = np.array([str(v) for v in yv], dtype=object)

    base_map = {u: 3 for u in ix0.uniq}
    base_acc = ix0.accuracy(Xv, yv, base_map)
    print(f"验证集基线（全 topk=3）: acc={base_acc:.4f}")

    # 找混淆最重的类对
    pred0 = ix0.predict(Xv, base_map)
    pairs = {}
    for t, p in zip(yv_s, pred0):
        if t != p:
            pairs[(t, p)] = pairs.get((t, p), 0) + 1
    top_pairs = sorted(pairs.items(), key=lambda kv: -kv[1])[:8]
    print(f"验证集上最重错误类对: {[(a, b, n) for (a, b), n in top_pairs]}")

    # ---------- 调参：给错误类对的目标类单独设 topk ----------
    # 逻辑：若 a→b 常错，调高 b 的 topk（b 需要更多证据才被判出），或调低
    cands = [1, 2, 3, 5, 8]
    involved = set()
    for (a, b), n in top_pairs:
        involved.add(a)
        involved.add(b)
    involved = sorted(involved)
    print(f"参与调参的类别: {involved}")

    best_map = dict(base_map)
    best_acc, best_desc = base_acc, "baseline"
    tried = 0
    for target in involved:
        for k in cands:
            m = dict(best_map)
            m[target] = k
            acc = ix0.accuracy(Xv, yv, m)
            tried += 1
            if acc > best_acc:
                best_acc, best_map, best_desc = acc, m, f"topk[{target}]={k}"
    print(f"单类调参: tried={tried}, 最好={best_desc}, 验证acc={best_acc:.4f} (+{best_acc-base_acc:+.4f})")

    # 贪心二轮
    for _ in range(3):
        improved = False
        for target in involved:
            for k in cands:
                m = dict(best_map)
                m[target] = k
                if m == best_map:
                    continue
                acc = ix0.accuracy(Xv, yv, m)
                tried += 1
                if acc > best_acc + 1e-6:
                    best_acc, best_map, best_desc = acc, m, best_desc + f" + topk[{target}]={k}"
                    improved = True
        if not improved:
            break
    changed = {u: k for u, k in best_map.items() if k != 3}
    print(f"最终调参: 验证acc={best_acc:.4f} (+{best_acc-base_acc:+.4f}), "
          f"改动类别={changed}")
    print(f"  总试参次数={tried}, 改动 {len(changed)}/{len(ix0.uniq)} 类（过拟合风险指标）")

    # ---------- 评测：在独立 seeds 上 ----------
    print("\n" + "=" * 100)
    print("评测（独立 seeds=1..4，与调参用的 seed=0 不重叠）")
    print("=" * 100)
    print(f"{'配置':<16}{'A':<10}{'B':<10}{'C':<10}{'macro':<10}")
    print("-" * 100)
    rows = []
    for label, mp in [("baseline topk3", base_map), ("tuned per-class", best_map)]:
        accs = {"A": [], "B": [], "C": []}
        for seed in seeds_eval:
            ix, tests = build(Ftr, ytr, seed, zsc)
            # 该 seed 的 uniq 可能与 seed0 相同（都是 0-9），直接复用映射
            for t in "ABC":
                Xte, yte_t = tests[t]
                accs[t].append(ix.accuracy(Xte, yte_t, mp))
        A = np.array(accs["A"]); B = np.array(accs["B"]); C = np.array(accs["C"])
        M = (A + B + C) / 3
        print(f"{label:<16}{A.mean():<10.4f}{B.mean():<10.4f}{C.mean():<10.4f}{M.mean():<10.4f}")
        rows.append({"config": label, "A": float(A.mean()), "B": float(B.mean()),
                     "C": float(C.mean()), "macro": float(M.mean()),
                     "macro_min": float(M.min()), "A_min": float(A.min()),
                     "n_seeds": len(seeds_eval)})

    print("-" * 100)
    base_r, tuned_r = rows
    print(f"\n调参后 gains: A {tuned_r['A']-base_r['A']:+.4f}, B {tuned_r['B']-base_r['B']:+.4f}, "
          f"C {tuned_r['C']-base_r['C']:+.4f}, macro {tuned_r['macro']-base_r['macro']:+.4f}")

    print("\n判据（C>0.89 且 macro>0.90 且 macro_min>0.88）:")
    for r in rows:
        print(f"  {r['config']:<16} C={r['C']:.4f} {'✓' if r['C']>0.89 else '✗'}  "
              f"macro={r['macro']:.4f} {'✓' if r['macro']>0.90 else '✗'}  "
              f"min={r['macro_min']:.4f} {'✓' if r['macro_min']>0.88 else '✗'}")

    # 过拟合检查：验证集增益 vs 测试集增益
    print(f"\n过拟合检查:")
    print(f"  验证集增益: {best_acc-base_acc:+.4f}")
    print(f"  测试集增益: {tuned_r['macro']-base_r['macro']:+.4f}")
    if (tuned_r["macro"] - base_r["macro"]) < (best_acc - base_acc) * 0.5:
        print("  ⚠ 测试集增益显著小于验证集 → 存在过拟合")
    else:
        print("  ✓ 测试集增益与验证集同量级 → 未明显过拟合")

    out = {"experiment": "E-18", "transform": "zscore-dim", "threshold": 0.705,
           "tuning_seed": 0, "eval_seeds": seeds_eval,
           "validation": {"baseline_acc": round(float(base_acc), 4),
                          "tuned_acc": round(float(best_acc), 4),
                          "gain": round(float(best_acc - base_acc), 4),
                          "n_trials": tried, "changed_classes": changed,
                          "best_desc": best_desc},
           "top_error_pairs": [{"from": a, "to": b, "n": n} for (a, b), n in top_pairs],
           "evaluation": rows,
           "elapsed_sec": round(time.time() - t0, 1)}
    with open(os.path.join(RESULTS, "e18_per_class.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e18_per_class.json')}")


if __name__ == "__main__":
    main()
